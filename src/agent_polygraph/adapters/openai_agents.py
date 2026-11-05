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
