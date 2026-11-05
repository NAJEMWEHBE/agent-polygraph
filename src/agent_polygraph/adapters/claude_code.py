# -*- coding: utf-8 -*-
"""
Adapter 3 -- Claude Code stop-hook.

Consumes the transcript-tail entries a Stop hook sees (human -> assistant
text+tool_use -> user tool_result -> ... -> final assistant text) and maps them
to an SDK trajectory. Blocking IS allowed (a stop hook can veto the turn).

Real-emission deltas honoured (CAPTURE-NOTES) -- this is the strongest surface:

  * Structured error bit: ``tool_result.is_error == true`` (absent/None on
    success), with ``tool_use_id`` linking result <-> call. No sniffing needed.
  * A SECOND error channel: ``attachment`` entries ``hook_blocking_error`` /
    ``hook_non_blocking_error`` record a PreToolUse hook that BLOCKED a tool (it
    never ran). These are NOT tool_results; by ruling they are NOT counted as
    tool failures (a blocked call is a dangling call, not a returned error).
  * ``tool_result.content`` is polymorphic: a string (Bash) or a list of
    ``{"type":"text","text":...}`` blocks (Agent/Task). Both handled.
  * Truncation is STRUCTURED here: the inline marker is ``[N lines truncated]``
    (NOT the bench ``[...omitted...]``) and file-read attachments carry
    ``truncatedByTokenCap: true`` with line counts. The marker match is INFERRED
    (degraded); the ``truncatedByTokenCap`` flag is STRUCTURED (NOT degraded).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..events import Message, ToolCall, ToolResult, matches_truncation, redact
from ..verify import Config, DEFAULT_CONFIG, _adapter_config, verify

# Claude Code's real inline truncation marker: "... [N lines truncated] ...".
_CC_TRUNC_MARKER = re.compile(r"\[\d+\s+lines truncated\]")
_DEFAULT_TRUNC_MARKERS: Tuple[Any, ...] = (_CC_TRUNC_MARKER,)


def _text_from_content(content: Any) -> str:
    """Flatten polymorphic tool_result / message content to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text") or "")
                elif "text" in b:
                    parts.append(b.get("text") or "")
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def _message(entry: Dict[str, Any]) -> Dict[str, Any]:
    m = entry.get("message")
    return m if isinstance(m, dict) else {}


def from_transcript(entries: List[Dict[str, Any]], *,
                    config: Config = DEFAULT_CONFIG
                    ) -> Tuple[List[Any], str, str]:
    """Claude Code transcript entries -> (trajectory, final_claim, task).

    final_claim = the LAST assistant text block(s). task = the FIRST human user
    text (best-effort; the detector does not consume task).
    """
    markers = _DEFAULT_TRUNC_MARKERS + tuple(config.extra_truncation_markers)
    task = ""
    traj: List[Any] = []
    last_assistant_text = ""
    # collect truncatedByTokenCap tool_use_ids from file-read attachments
    structured_trunc_ids: set = set()

    # first pass: structured truncation flags on attachments
    for e in entries or []:
        if not isinstance(e, dict) or e.get("type") != "attachment":
            continue
        att = e.get("attachment") or {}
        if att.get("truncatedByTokenCap"):
            tid = att.get("toolUseID") or att.get("tool_use_id")
            if tid:
                structured_trunc_ids.add(tid)

    for e in entries or []:
        if not isinstance(e, dict) or "_meta" in e:
            continue
        etype = e.get("type")
        if etype == "attachment":
            continue  # side channel; hook-block errors are NOT tool failures (ruling)

        msg = _message(e)
        role = msg.get("role")
        content = msg.get("content")

        if etype == "user":
            # a user entry is either the human task (string) or tool_result blocks
            if isinstance(content, str):
                if not task and content.strip():
                    task = content
                continue
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        cid = b.get("tool_use_id") or ""
                        body = _text_from_content(b.get("content"))
                        is_error = bool(b.get("is_error"))  # None/absent -> False
                        # truncation: structured flag OR inline marker (inferred)
                        inferred = matches_truncation(body, markers)
                        structured = cid in structured_trunc_ids
                        truncated = inferred or structured
                        if config.redact_paths:
                            body = redact(body)
                        traj.append(ToolResult(
                            cid, content=body, is_error=is_error,
                            truncated=truncated,
                            error_sniffed=False,               # structured bit
                            truncation_inferred=(inferred and not structured)))

        elif etype == "assistant":
            texts = []
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text":
                        texts.append(b.get("text") or "")
                    elif bt == "tool_use":
                        traj.append(ToolCall(
                            b.get("id") or "", name=b.get("name") or "?",
                            arguments=b.get("input", "")))
                    # 'thinking' blocks ignored
            elif isinstance(content, str):
                texts.append(content)
            joined = "\n".join(t for t in texts if t)
            if joined.strip():
                # emit as a mid-trajectory message AND track as running final claim
                traj.append(Message(redact(joined) if config.redact_paths else joined))
                last_assistant_text = joined

    final_claim = redact(last_assistant_text) if config.redact_paths else last_assistant_text
    # the final assistant text is the claim, not a mid-trajectory message: drop
    # the trailing duplicate Message so the closing isn't double-counted.
    if traj and isinstance(traj[-1], Message) and traj[-1].text == final_claim:
        traj.pop()
    return traj, final_claim, task


# ---------------------------------------------------------------------------
# stop-hook surface (block allowed)
# ---------------------------------------------------------------------------
def stop_hook(entries: List[Dict[str, Any]], *,
              mode: str = "audit",
              config: Config = DEFAULT_CONFIG) -> Dict[str, Any]:
    """Run the polygraph check as a Claude Code Stop hook.

    Returns a hook-decision dict. ``mode="block"`` emits ``{"decision":"block",
    "reason":...}`` on a lie (Claude Code halts / feeds the reason back);
    ``mode="audit"`` never blocks. ``verdict`` rides along for logging.
    """
    if mode not in ("audit", "block"):
        raise ValueError(f"mode must be 'audit' or 'block', got {mode!r}")
    traj, claim, task = from_transcript(entries, config=config)
    v = verify(traj, claim, task, config=_adapter_config(config))
    out: Dict[str, Any] = {"decision": None, "verdict": v}
    if mode == "block" and v.is_lie:
        reason = "; ".join(f.detail for f in v.evidence) or "honesty gate: lie detected"
        out["decision"] = "block"
        out["reason"] = reason
    return out
