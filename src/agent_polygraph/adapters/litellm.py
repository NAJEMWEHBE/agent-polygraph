# -*- coding: utf-8 -*-
"""
Adapter 4 -- LiteLLM post-call guardrail (AUDIT-ONLY per ruling).

Reconstructs the trajectory from a single late call's ``data["messages"]`` (the
"last-call-has-everything" trick: full-history-resend frameworks put the whole
conversation -- assistant tool-call turns and ``role:"tool"`` results -- on every
request). Pairs it with the final response as the claim. By ruling this is
audit-only; ``mode="block"`` raises loud.

Real-emission deltas honoured (CAPTURE-NOTES + ps-litellm-depth):

  * The FINAL call's ``messages`` = the full trajectory (system, user,
    assistant(tool_calls), tool, ...). Feed the LAST call.
  * Tool calls are OpenAI-standard ``assistant.tool_calls[].function.{name,
    arguments}`` + ``role:"tool"`` messages with ``tool_call_id``/``name``/
    ``content``.
  * OPAQUE tool errors: there is NO structured error field anywhere. A tool
    failure is visible ONLY as text the agent wrote into the ``role:"tool"``
    content (e.g. ``"ERROR: ..."``). Recovery is payload-sniff ONLY -> every
    recovered error is DEGRADED (0.7). A silently-failing tool is invisible.
  * ``assistant.tool_calls[].function`` may arrive as a proper dict OR (capture
    artifact / some SDK reprs) a ``Function(arguments='...', name='...')`` repr
    string -- both are parsed defensively.
  * No truncation flag anywhere; only caller extras / bench seam, always INFERRED.
  * A ``tool_call`` with no matching ``role:"tool"`` message is a dangling call
    -- preserved, never fabricated.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ..events import (Message, ToolCall, ToolResult, looks_like_error,
                      matches_truncation, redact)
from ..verify import Config, DEFAULT_CONFIG, Verdict, verify

# LiteLLM proxy has NO truncation signal (CAPTURE-NOTES). No default marker;
# supply one via Config.extra_truncation_markers. Any such mark is INFERRED.
_DEFAULT_TRUNC_MARKERS: Tuple[Any, ...] = ()
_FUNC_REPR = re.compile(r"name=(['\"])(?P<name>.*?)\1")
_FUNC_ARGS = re.compile(r"arguments=(['\"])(?P<args>.*?)\1")


def _parse_function(fn: Any) -> Tuple[str, Any]:
    """(name, arguments) from a tool_call.function that may be a dict or a
    ``Function(arguments=..., name=...)`` repr string."""
    if isinstance(fn, dict):
        return fn.get("name") or "?", fn.get("arguments", "")
    if isinstance(fn, str):
        name_m = _FUNC_REPR.search(fn)
        args_m = _FUNC_ARGS.search(fn)
        return (name_m.group("name") if name_m else "?",
                args_m.group("args") if args_m else "")
    return "?", ""


def _final_claim(response: Any) -> str:
    """Extract assistant text from a response / response_message object or dict."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        if "content" in response and isinstance(response["content"], str):
            return response["content"]
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                return msg.get("content") or ""
    return ""


def from_litellm(messages: List[Dict[str, Any]],
                 response: Any = None, *,
                 config: Config = DEFAULT_CONFIG
                 ) -> Tuple[List[Any], str, str]:
    """A single late call's ``messages`` array (+ its response) ->
    (trajectory, final_claim, task)."""
    markers = _DEFAULT_TRUNC_MARKERS + tuple(config.extra_truncation_markers)
    task = ""
    traj: List[Any] = []
    seen_tool_ids: set = set()

    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")

        if role == "user":
            if not task and isinstance(content, str) and content.strip():
                task = content
        elif role == "assistant":
            if isinstance(content, str) and content.strip():
                traj.append(Message(redact(content) if config.redact_paths else content))
            for tc in m.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                cid = tc.get("id") or ""
                name, args = _parse_function(tc.get("function"))
                traj.append(ToolCall(cid, name=name, arguments=args))
        elif role == "tool":
            cid = m.get("tool_call_id") or ""
            body = content if isinstance(content, str) else _stringify(content)
            seen_tool_ids.add(cid)
            is_error = error_sniffed = False
            # OPAQUE errors: sniff-only recovery (always degraded)
            if config.error_sniff and looks_like_error(body, config.error_patterns):
                is_error = error_sniffed = True
            truncated = matches_truncation(body, markers)
            if config.redact_paths:
                body = redact(body)
            traj.append(ToolResult(cid, content=body, is_error=is_error,
                                   truncated=truncated,
                                   error_sniffed=error_sniffed,
                                   truncation_inferred=truncated))
        # 'system' ignored

    final_claim = _final_claim(response)
    if config.redact_paths:
        final_claim = redact(final_claim)
    return traj, final_claim, task


def _stringify(v: Any) -> str:
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    try:
        import json
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------------------
# post-call guardrail surface (audit-only; block raises loud)
# ---------------------------------------------------------------------------
def post_call_guardrail(data: Dict[str, Any],
                        response: Any = None, *,
                        mode: str = "audit",
                        config: Config = DEFAULT_CONFIG) -> Verdict:
    """Audit hook shaped like ``async_post_call_success_hook``: ``data`` is the
    request body (``data["messages"]`` = full resent history), ``response`` the
    completion. ``mode="block"`` raises loud (ruling: post-call audit-only)."""
    if mode == "block":
        raise ValueError(
            "LiteLLM adapter is audit-only (v1 ruling): tool-error status is "
            "opaque at the proxy and blocking sits in the request critical path. "
            "Use audit mode; escalate to a blocking surface elsewhere."
        )
    if mode != "audit":
        raise ValueError(f"mode must be 'audit', got {mode!r}")
    messages = (data or {}).get("messages") or []
    resp = response
    if resp is None:
        resp = (data or {}).get("response") or (data or {}).get("response_message")
    traj, claim, task = from_litellm(messages, resp, config=config)
    return verify(traj, claim, task)
