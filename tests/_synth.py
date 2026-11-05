# -*- coding: utf-8 -*-
"""
Framework-native emission synthesizers for the packaged tests.

Each ``synth_*`` builds the shape a framework REALLY emits (per the deltas
observed in captured real traces) from a plain interchange item dict
``{"task", "events", "closing"}``. This is test scaffolding only: it operates on
inline items handed in by the test, needs NO bench corpus and NO private gate,
and lets the edge suite drive every adapter through its real parsing path.

(Ported from the upstream ``roundtrip_396.py`` synth builders; the corpus-driven
parity harness itself stays in the dev tree and is NOT shipped.)
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


def _as_text(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _results_index(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {e["call_id"]: e for e in item.get("events", [])
            if e.get("type") == "tool_result"}


def synth_agents(item: Dict[str, Any], drop_error_bit: bool = False) -> List[Dict[str, Any]]:
    """OpenAI Agents SDK span-export stream: tool error on span WRAPPER, function
    output replaced with a generic string (the real behaviour)."""
    results = _results_index(item)
    spans: List[Dict[str, Any]] = [
        {"span_data": {"type": "generation",
                       "input": [{"role": "user", "content": item.get("task", "")}],
                       "output": []}, "error": None}
    ]
    for ev in item.get("events", []):
        t = ev.get("type")
        if t == "message":
            spans.append({"span_data": {"type": "generation",
                          "output": [{"role": "assistant", "content": ev.get("text", "")}]},
                          "error": None})
        elif t == "tool_call":
            res = results.get(ev.get("call_id"))
            wrapper_err = None
            output = None  # dangling until a result arrives
            if res is not None:
                content = res.get("content", "") or ""
                output = content
                if res.get("is_error") and not drop_error_bit:
                    wrapper_err = {"message": "Error running tool (non-fatal)",
                                   "data": {"tool_name": ev.get("name"), "error": content}}
                    output = "An error occurred while running the tool. Please try again."
            spans.append({"span_data": {"type": "function", "name": ev.get("name", "?"),
                          "input": ev.get("arguments", ""), "output": output},
                          "error": wrapper_err})
    spans.append({"span_data": {"type": "generation",
                  "output": [{"role": "assistant", "content": item.get("closing", "")}]},
                  "error": None})
    return spans


def synth_openinference(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """OpenInference/OTel span stream: flat indexed attrs, OTel status error bit,
    failed TOOL span carries NO output.value."""
    results = _results_index(item)
    spans: List[Dict[str, Any]] = [
        {"name": "Prompt", "status_code": "OK",
         "attributes": {"openinference.span.kind": "CHAIN",
                        "llm.input_messages.0.message.role": "user",
                        "llm.input_messages.0.message.content": item.get("task", "")}}
    ]
    for ev in item.get("events", []):
        t = ev.get("type")
        if t == "message":
            spans.append({"name": "ChatOpenAI", "status_code": "OK",
                          "attributes": {"openinference.span.kind": "LLM",
                                         "llm.output_messages.0.message.role": "assistant",
                                         "llm.output_messages.0.message.content": ev.get("text", "")}})
        elif t == "tool_call":
            res = results.get(ev.get("call_id"))
            attrs = {"openinference.span.kind": "TOOL",
                     "tool_call.id": ev.get("call_id"),
                     "tool_call.function.name": ev.get("name", "?"),
                     "input.value": _as_text(ev.get("arguments", ""))}
            status = "OK"
            if res is None:
                pass  # dangling: no output.value, status OK
            elif res.get("is_error"):
                status = "ERROR"
                spans.append({"name": ev.get("name", "?"), "status_code": "ERROR",
                              "status_description": res.get("content", "") or "",
                              "attributes": attrs})
                continue
            else:
                attrs["output.value"] = json.dumps(
                    {"type": "tool", "data": {"content": res.get("content", "")}})
            spans.append({"name": ev.get("name", "?"), "status_code": status,
                          "attributes": attrs})
    spans.append({"name": "ChatOpenAI", "status_code": "OK",
                  "attributes": {"openinference.span.kind": "LLM",
                                 "llm.output_messages.0.message.role": "assistant",
                                 "llm.output_messages.0.message.content": item.get("closing", "")}})
    return spans


def synth_claude_code(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Claude Code transcript tail: structured is_error, real [N lines truncated]."""
    results = _results_index(item)
    entries: List[Dict[str, Any]] = [
        {"type": "user", "message": {"role": "user", "content": item.get("task", "")}}
    ]
    for ev in item.get("events", []):
        t = ev.get("type")
        if t == "message":
            entries.append({"type": "assistant", "message": {"role": "assistant",
                            "content": [{"type": "text", "text": ev.get("text", "")}]}})
        elif t == "tool_call":
            entries.append({"type": "assistant", "message": {"role": "assistant",
                            "content": [{"type": "tool_use", "id": ev.get("call_id"),
                                         "name": ev.get("name", "?"),
                                         "input": ev.get("arguments", "")}]}})
        elif t == "tool_result":
            content = ev.get("content", "") or ""
            if ev.get("truncated") and "truncated" not in content:
                content = content + "\n... [12 lines truncated] ..."  # CC real marker
            block = {"type": "tool_result", "tool_use_id": ev.get("call_id"),
                     "content": content}
            if ev.get("is_error"):
                block["is_error"] = True
            entries.append({"type": "user", "message": {"role": "user", "content": [block]}})
    entries.append({"type": "assistant", "message": {"role": "assistant",
                    "content": [{"type": "text", "text": item.get("closing", "")}]}})
    return entries


def synth_litellm(item: Dict[str, Any]):
    """LiteLLM final-call messages (full resent history) + response. NO structured
    error bit -- a failed tool's error is visible only as text in the tool content."""
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": item.get("task", "")}
    ]
    for ev in item.get("events", []):
        t = ev.get("type")
        if t == "message":
            messages.append({"role": "assistant", "content": ev.get("text", "")})
        elif t == "tool_call":
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"id": ev.get("call_id"), "type": "function",
                                             "function": {"name": ev.get("name", "?"),
                                                          "arguments": _as_text(ev.get("arguments", ""))}}]})
        elif t == "tool_result":
            messages.append({"role": "tool", "tool_call_id": ev.get("call_id"),
                             "name": "", "content": ev.get("content", "") or ""})
    response = {"content": item.get("closing", ""), "role": "assistant"}
    return messages, response
