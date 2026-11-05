# -*- coding: utf-8 -*-
"""
Adapter 1 -- OpenAI Agents SDK output guardrail.

Consumes the span-export dicts a ``TracingProcessor`` receives (each span's
native ``Span.export()``), maps them to an SDK trajectory, and exposes an
``@output_guardrail``-shaped surface. Blocking IS allowed here (the SDK guardrail
tripwire can halt a run), so ``mode="block"`` is supported.

Real-emission deltas honoured (CAPTURE-NOTES, vs. the throwaway prototype which
was WRONG about all of these):

  * ``type`` is nested at ``span_data.type`` -- NOT top-level.
  * The agent span carries NO task/output; task comes from the first
    ``generation`` span's input (the user message), final claim from the LAST
    ``generation`` span's non-empty assistant ``output[0].content``.
  * A tool failure is on the span WRAPPER ``error`` (``SpanError{message,data}``),
    NOT in ``span_data``; the function ``output`` is replaced with a generic
    retry string, so the real error text survives only in ``error.data.error``.
    We read the wrapper first; payload-sniff is the fallback.
  * ``generation`` assistant tool-call turns have ``content=""`` with the call in
    ``tool_calls`` -- those empty turns are NOT the final claim.
  * ``function`` spans always carry an ``output`` (SDK guarantees result-or-error)
    so a genuine dangling call is essentially non-inducible; we still preserve
    ``output is None`` + no wrapper error as a dangling call and never fabricate.
  * ``custom`` spans (turn/task) and the Responses-path caveat (export drops
    content) are documented; ``custom`` spans are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..events import (ToolCall, ToolResult, looks_like_error,
                      matches_truncation, redact)
from ..verify import Config, DEFAULT_CONFIG, Verdict, _adapter_config, verify

# Agents SDK has NO truncation signal at all (CAPTURE-NOTES). No default marker;
# a deployment supplies one via Config.extra_truncation_markers. Any such mark is
# INFERRED (degraded). The verdict is truncation-independent anyway (the detector
# uses the bit only to grade confidence), so losing it never flips a verdict.
_DEFAULT_TRUNC_MARKERS: Tuple[Any, ...] = ()


def _span_type(sp: Dict[str, Any]) -> Optional[str]:
    sd = sp.get("span_data")
    if isinstance(sd, dict):
        return sd.get("type")
    return None


def _generation_final_text(sp: Dict[str, Any]) -> Optional[str]:
    """Non-empty assistant text from a generation span, or None (tool-call turn)."""
    sd = sp.get("span_data") or {}
    for msg in sd.get("output") or []:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


def _generation_user_task(sp: Dict[str, Any]) -> Optional[str]:
    sd = sp.get("span_data") or {}
    for msg in sd.get("input") or []:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


def from_openai_agents(trace_export: List[Dict[str, Any]], *,
                       config: Config = DEFAULT_CONFIG
                       ) -> Tuple[List[Any], str, str]:
    """List of span-export dicts (a trace-processor stream, ``_meta``/``trace``
    envelope lines tolerated) -> (trajectory, final_claim, task)."""
    markers = _DEFAULT_TRUNC_MARKERS + tuple(config.extra_truncation_markers)
    task, final_claim = "", ""
    traj: List[Any] = []
    call_n = 0

    for sp in trace_export or []:
        if not isinstance(sp, dict):
            continue
        # tolerate the capture envelope lines (_meta / {"object":"trace"})
        if "_meta" in sp or sp.get("object") == "trace":
            continue
        k = _span_type(sp)
        sd = sp.get("span_data") or {}

        if k == "generation":
            t = _generation_user_task(sp)
            if t and not task:
                task = t
            final = _generation_final_text(sp)
            if final is not None:
                final_claim = final  # last non-empty assistant turn wins

        elif k == "function":
            call_n += 1
            cid = f"fn{call_n}"
            traj.append(ToolCall(cid, name=sd.get("name") or "?",
                                 arguments=sd.get("input", "")))
            out = sd.get("output")
            wrapper_err = sp.get("error")  # SpanError{message,data} or None
            if out is None and not wrapper_err:
                # genuine dangling call (SDK rarely produces this) -- no result
                continue
            # error text: prefer wrapper error.data.error, else the (generic) output
            is_error = False
            error_sniffed = False
            content = out if isinstance(out, str) else ("" if out is None else str(out))
            if wrapper_err:
                is_error = True
                data = wrapper_err.get("data") if isinstance(wrapper_err, dict) else None
                real = None
                if isinstance(data, dict):
                    real = data.get("error")
                content = real or (wrapper_err.get("message") if isinstance(wrapper_err, dict) else None) or content
            elif config.error_sniff and looks_like_error(content, config.error_patterns):
                # no structured wrapper error -> sniff the payload (degraded)
                is_error = True
                error_sniffed = True

            truncated = matches_truncation(content, markers)
            if config.redact_paths:
                content = redact(content)
            traj.append(ToolResult(cid, content=content, is_error=is_error,
                                   truncated=truncated,
                                   error_sniffed=error_sniffed,
                                   truncation_inferred=truncated))

        elif k == "agent":
            # agent span carries no task/output; a MaxTurnsExceeded / run error
            # lands here but is not a tool-error bit -- ignored for detection.
            continue
        # 'custom' / 'response' / other -> ignored (documented)

    return traj, final_claim, task


# ---------------------------------------------------------------------------
# @output_guardrail-shaped surface (tripwire; block allowed)
# ---------------------------------------------------------------------------
@dataclass
class GuardrailResult:
    """Mirrors the Agents SDK ``GuardrailFunctionOutput`` contract."""
    tripwire_triggered: bool
    output_info: Verdict

    @property
    def verdict(self) -> Verdict:
        return self.output_info


def output_guardrail(trace_export: List[Dict[str, Any]],
                     final_claim: Optional[str] = None,
                     task: Optional[str] = None,
                     *,
                     mode: str = "audit",
                     config: Config = DEFAULT_CONFIG) -> GuardrailResult:
    """Run the polygraph check as an Agents-SDK output guardrail.

    ``mode="audit"`` (default) always returns tripwire=False (log-only).
    ``mode="block"`` sets tripwire on a lie -> the SDK halts the run.
    Explicit ``final_claim``/``task`` override what is parsed from the spans
    (useful on the Responses path where export() drops the final content).
    """
    if mode not in ("audit", "block"):
        raise ValueError(f"mode must be 'audit' or 'block', got {mode!r}")
    traj, parsed_claim, parsed_task = from_openai_agents(trace_export, config=config)
    v = verify(traj, final_claim if final_claim is not None else parsed_claim,
               task if task is not None else parsed_task,
               config=_adapter_config(config))
    tripwire = (mode == "block" and v.is_lie)
    return GuardrailResult(tripwire_triggered=tripwire, output_info=v)


# ---------------------------------------------------------------------------
# Live wiring: a trace processor that keeps each trace's spans, and a ready
# Agents-SDK output guardrail that judges the run's final output against them.
# ---------------------------------------------------------------------------
class PolygraphTracingProcessor:
    """Duck-typed Agents-SDK ``TracingProcessor`` that keeps the
    ``span.export()`` dicts of each live trace, keyed by trace id.

    Register it with ``agents.add_trace_processor(proc)``. Spans are recorded
    when they end; a trace's spans are dropped when the trace ends, and at most
    ``max_traces`` traces are held (oldest dropped first), so a long-running
    process never grows without bound. Thread-safe.
    """

    def __init__(self, max_traces: int = 256):
        import threading
        from collections import OrderedDict
        self._max = max(1, int(max_traces))
        self._spans: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
        self._lock = threading.Lock()

    # -- TracingProcessor interface ----------------------------------------
    def on_trace_start(self, trace: Any) -> None:
        pass

    def on_trace_end(self, trace: Any) -> None:
        tid = getattr(trace, "trace_id", None)
        with self._lock:
            self._spans.pop(tid, None)

    def on_span_start(self, span: Any) -> None:
        pass

    def on_span_end(self, span: Any) -> None:
        tid = getattr(span, "trace_id", None)
        try:
            exported = span.export()
        except Exception:  # a span that cannot export is skipped, never fatal
            return
        if tid is None or not isinstance(exported, dict):
            return
        with self._lock:
            bucket = self._spans.get(tid)
            if bucket is None:
                bucket = self._spans[tid] = []
                while len(self._spans) > self._max:
                    self._spans.popitem(last=False)
            else:
                self._spans.move_to_end(tid)
            bucket.append(exported)

    def shutdown(self) -> None:
        with self._lock:
            self._spans.clear()

    def force_flush(self) -> None:
        pass

    # -- reading ------------------------------------------------------------
    def spans(self, trace_id: Any) -> List[Dict[str, Any]]:
        """A copy of the spans recorded so far for ``trace_id``."""
        with self._lock:
            return list(self._spans.get(trace_id, ()))


def _output_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    dump = getattr(output, "model_dump_json", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    return str(output)


def make_output_guardrail(processor: PolygraphTracingProcessor, *,
                          mode: str = "audit",
                          config: Config = DEFAULT_CONFIG,
                          name: str = "agent_polygraph") -> Any:
    """A ready ``@output_guardrail`` for ``Agent(output_guardrails=[...])``.

    It judges the agent's final output against the spans ``processor`` has
    recorded for the current trace (``agents.get_current_trace()``). In
    ``mode="block"`` a lie trips the wire and the SDK raises
    ``OutputGuardrailTripwireTriggered``; ``output_info`` is the verdict dict.
    Needs the ``openai-agents`` package (imported lazily here).
    """
    if mode not in ("audit", "block"):
        raise ValueError(f"mode must be 'audit' or 'block', got {mode!r}")
    try:
        import agents  # the OpenAI Agents SDK
    except ImportError as e:
        raise ImportError("make_output_guardrail needs the OpenAI Agents SDK: "
                          "pip install openai-agents") from e

    def polygraph_guardrail(ctx: Any, agent: Any, output: Any) -> Any:
        trace = agents.get_current_trace()
        spans = processor.spans(getattr(trace, "trace_id", None)) if trace else []
        res = output_guardrail(spans, final_claim=_output_text(output),
                               mode=mode, config=config)
        return agents.GuardrailFunctionOutput(
            output_info=res.output_info.as_dict(),
            tripwire_triggered=res.tripwire_triggered)

    return agents.output_guardrail(polygraph_guardrail, name=name)
