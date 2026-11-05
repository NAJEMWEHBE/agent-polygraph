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
from ..verify import Config, DEFAULT_CONFIG, Verdict, _adapter_config, verify

# LiteLLM proxy has NO truncation signal (CAPTURE-NOTES). No default marker;
# supply one via Config.extra_truncation_markers. Any such mark is INFERRED.
_DEFAULT_TRUNC_MARKERS: Tuple[Any, ...] = ()
_FUNC_REPR = re.compile(r"name=(['\"])(?P<name>.*?)\1")
_FUNC_ARGS = re.compile(r"arguments=(['\"])(?P<args>.*?)\1")


def _get(obj: Any, key: str) -> Any:
    """Read ``key`` from a dict or an attribute object (LiteLLM hands hooks
    pydantic ``ModelResponse`` / ``Message`` / ``ChatCompletionMessageToolCall``
    objects, and some deployments plain dicts)."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _content_text(content: Any) -> str:
    """Message content as text: a string, or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif _get(b, "type") in (None, "text"):
                parts.append(str(_get(b, "text") or ""))
        return "\n".join(p for p in parts if p)
    return ""


def _parse_function(fn: Any) -> Tuple[str, Any]:
    """(name, arguments) from a tool_call.function that may be a dict, an
    attribute object, or a ``Function(arguments=..., name=...)`` repr string."""
    if isinstance(fn, str):
        name_m = _FUNC_REPR.search(fn)
        args_m = _FUNC_ARGS.search(fn)
        return (name_m.group("name") if name_m else "?",
                args_m.group("args") if args_m else "")
    if fn is None:
        return "?", ""
    args = _get(fn, "arguments")
    return _get(fn, "name") or "?", "" if args is None else args


def _final_claim(response: Any) -> str:
    """Assistant text from a response: a string, a dict, a ``ModelResponse``-
    style object (``.choices[0].message.content``), a bare message
    (``.content``), or anything with ``model_dump()``."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    content = _get(response, "content")
    if isinstance(content, (str, list, tuple)):
        return _content_text(content)
    choices = _get(response, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        msg = _get(choices[0], "message")
        if msg is not None:
            return _content_text(_get(msg, "content"))
    dump = getattr(response, "model_dump", None)
    if not isinstance(response, dict) and callable(dump):
        try:
            dumped = dump()
        except Exception:  # a foreign object's serializer must not break the audit
            return ""
        if isinstance(dumped, dict):
            return _final_claim(dumped)
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
        if m is None or isinstance(m, (str, bytes)):
            continue
        role = _get(m, "role")
        content = _get(m, "content")

        if role == "user":
            if not task and isinstance(content, str) and content.strip():
                task = content
        elif role == "assistant":
            if isinstance(content, str) and content.strip():
                traj.append(Message(redact(content) if config.redact_paths else content))
            for tc in _get(m, "tool_calls") or []:
                if tc is None or isinstance(tc, (str, bytes)):
                    continue
                cid = _get(tc, "id") or ""
                name, args = _parse_function(_get(tc, "function"))
                traj.append(ToolCall(cid, name=name, arguments=args))
        elif role == "tool":
            cid = _get(m, "tool_call_id") or ""
            body = (_content_text(content) if isinstance(content, (str, list, tuple))
                    else _stringify(content))
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
    return verify(traj, claim, task, config=_adapter_config(config))
