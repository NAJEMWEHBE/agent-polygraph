# -*- coding: utf-8 -*-
"""
Adapter 2 -- OTel / OpenInference span consumer (AUDIT-ONLY).

OpenInference convention first (ps-semconv-strategy: it captures tool
args/results by default; official OTel gen_ai.* is opt-in-dark). This is a
read-only trace consumer; there is no run to halt, so ``mode="block"`` raises
loud rather than silently degrading.

Real-emission deltas honoured (CAPTURE-NOTES):

  * Span kind is the attribute ``openinference.span.kind`` (values CHAIN / LLM /
    TOOL / AGENT), NOT a top-level ``span_kind``. A single turn emits ~6 CHAIN
    spans -- we do NOT treat every CHAIN as task/final (the prototype's bug).
  * Attributes are FLAT indexed scalars, not nested lists: messages live at
    ``llm.output_messages.<i>.message.content`` etc. We reconstruct from the
    flat keys; ``attrs.get("llm.output_messages")`` returns nothing.
  * The error bit is the OTel span status (``status_code == "ERROR"`` with a
    ``status_description``); it propagates to parent chain spans. OTLP-JSON
    exports spell it ``{"status": {"code": 2}}`` or ``"STATUS_CODE_ERROR"``;
    every spelling is read.
  * ERROR vs dangling COLLIDE: a failed TOOL span has NO ``output.value`` at all.
    We check status FIRST -- an ERROR tool span becomes a failed ToolResult, only
    a genuinely resultless non-error TOOL span becomes a dangling call.
  * Success ``output.value`` on a TOOL span is a serialized LangChain ToolMessage
    JSON ``{"type":"tool","data":{"content":...}}`` -- content is extracted.
  * ``status_description`` / exception stacktraces leak host paths -> redacted.

Capability probe (ps-semconv-strategy): if the trace looks like vanilla official
OTel gen_ai.* with content capture off (tool spans present but no args/results),
we surface that as a diagnostic rather than silently returning honest.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..events import (Message, ToolCall, ToolResult, looks_like_error,
                      matches_truncation, redact)
from ..verify import Config, DEFAULT_CONFIG, Verdict, _adapter_config, verify

# OTel/OpenInference has NO truncation signal (CAPTURE-NOTES). No default marker;
# supply one via Config.extra_truncation_markers. Any such mark is INFERRED.
_DEFAULT_TRUNC_MARKERS: Tuple[Any, ...] = ()


def _kind(sp: Dict[str, Any]) -> Optional[str]:
    attrs = sp.get("attributes") or {}
    return attrs.get("openinference.span.kind") or sp.get("span_kind")


def _status_code(sp: Dict[str, Any]) -> Any:
    # capture serialized status_code at top level; also tolerate a nested status
    if sp.get("status_code") is not None:
        return sp["status_code"]
    st = sp.get("status")
    if isinstance(st, dict):
        code = st.get("status_code")
        return code if code is not None else st.get("code")
    if isinstance(st, (str, int)):
        return st
    return None


def _is_error_status(sp: Dict[str, Any]) -> bool:
    """True iff the span's OTel status is ERROR, in any serialised form: the
    name ("ERROR"), the OTLP-JSON enum ("STATUS_CODE_ERROR") or number (2),
    or a stringified Python enum ("StatusCode.ERROR")."""
    raw = _status_code(sp)
    if raw is None or isinstance(raw, bool):
        return False
    code = str(raw).strip().upper()
    for prefix in ("STATUSCODE.", "STATUS_CODE_"):
        if code.startswith(prefix):
            code = code[len(prefix):]
    return code in ("ERROR", "2")


def _status_description(sp: Dict[str, Any]) -> str:
    if sp.get("status_description"):
        return sp["status_description"]
    st = sp.get("status")
    if isinstance(st, dict):
        return (st.get("description") or st.get("status_description")
                or st.get("message") or "")
    return ""


def _reconstruct_output_messages(attrs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rebuild ``llm.output_messages.<i>.message.*`` flat keys into message dicts."""
    msgs: Dict[int, Dict[str, Any]] = {}
    prefix = "llm.output_messages."
    for k, v in attrs.items():
        if not k.startswith(prefix):
            continue
        rest = k[len(prefix):]
        idx_str, _, tail = rest.partition(".")
        try:
            i = int(idx_str)
        except ValueError:
            continue
        m = msgs.setdefault(i, {})
        if tail == "message.role":
            m["role"] = v
        elif tail == "message.content":
            m["content"] = v
    return [msgs[i] for i in sorted(msgs)]


def _tool_output_content(raw: Any) -> str:
    """Extract text from a TOOL span's output.value (may be a ToolMessage JSON)."""
    if not isinstance(raw, str):
        return "" if raw is None else str(raw)
    s = raw.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                data = obj.get("data")
                if isinstance(data, dict) and "content" in data:
                    return str(data.get("content") or "")
                if "content" in obj:
                    return str(obj.get("content") or "")
        except (ValueError, TypeError):
            pass
    return raw


def from_openinference(spans: List[Dict[str, Any]], *,
                       config: Config = DEFAULT_CONFIG
                       ) -> Tuple[List[Any], str, str]:
    """List of serialized OTel/OpenInference spans -> (trajectory, final_claim, task)."""
    markers = _DEFAULT_TRUNC_MARKERS + tuple(config.extra_truncation_markers)
    task, final_claim = "", ""
    traj: List[Any] = []
    call_n = 0

    for sp in spans or []:
        if not isinstance(sp, dict) or "_meta" in sp:
            continue
        attrs = sp.get("attributes") or {}
        kind = _kind(sp)

        if kind == "TOOL":
            call_n += 1
            cid = attrs.get("tool_call.id") or f"otel{call_n}"
            name = (attrs.get("tool_call.function.name")
                    or sp.get("name") or "?")
            traj.append(ToolCall(cid, name=name,
                                 arguments=attrs.get("input.value", "")))
            has_output = "output.value" in attrs
            # ERROR must be checked BEFORE dangling inference (they collide).
            if _is_error_status(sp):
                desc = _status_description(sp)
                content = desc or "tool error"
                if config.redact_paths:
                    content = redact(content)
                traj.append(ToolResult(cid, content=content, is_error=True,
                                       truncated=matches_truncation(content, markers)))
            elif has_output:
                content = _tool_output_content(attrs.get("output.value"))
                is_error = error_sniffed = False
                if config.error_sniff and looks_like_error(content, config.error_patterns):
                    is_error = error_sniffed = True
                truncated = matches_truncation(content, markers)
                if config.redact_paths:
                    content = redact(content)
                traj.append(ToolResult(cid, content=content, is_error=is_error,
                                       truncated=truncated,
                                       error_sniffed=error_sniffed,
                                       truncation_inferred=truncated))
            else:
                # non-error TOOL span with no output.value -> genuine dangling call
                pass  # preserve the ToolCall, fabricate nothing

        elif kind == "LLM":
            out_msgs = _reconstruct_output_messages(attrs)
            for m in out_msgs:
                content = m.get("content")
                if isinstance(content, str) and content.strip():
                    final_claim = content  # last non-empty assistant text wins
            # earliest user input as task
            if not task:
                role = attrs.get("llm.input_messages.0.message.role")
                content = attrs.get("llm.input_messages.0.message.content")
                if role == "user" and isinstance(content, str) and content.strip():
                    task = content

    return traj, final_claim, task


# ---------------------------------------------------------------------------
# audit-only surface (mode="block" raises loud -- cannot halt a trace consumer)
# ---------------------------------------------------------------------------
def verify_spans(spans: List[Dict[str, Any]],
                 final_claim: Optional[str] = None,
                 task: Optional[str] = None,
                 *,
                 mode: str = "audit",
                 config: Config = DEFAULT_CONFIG) -> Verdict:
    """Audit a captured OTel/OpenInference trace. ``mode="block"`` is invalid for
    a passive span consumer and raises loud (never a silent downgrade)."""
    if mode == "block":
        raise ValueError(
            "OpenInference/OTel adapter is audit-only: it consumes captured spans "
            "and cannot halt a run. Use the OpenAI Agents guardrail or Claude Code "
            "stop-hook for blocking."
        )
    if mode != "audit":
        raise ValueError(f"mode must be 'audit', got {mode!r}")
    traj, parsed_claim, parsed_task = from_openinference(spans, config=config)
    return verify(traj, final_claim if final_claim is not None else parsed_claim,
                  task if task is not None else parsed_task,
                  config=_adapter_config(config))
