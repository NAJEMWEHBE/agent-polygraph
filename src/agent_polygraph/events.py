# -*- coding: utf-8 -*-
"""
Stable SDK event model for agent-polygraph.

Three dataclasses -- ``Message`` / ``ToolCall`` / ``ToolResult`` -- mirror the
polygraph-bench interchange FORMAT v1.0 event shapes 1:1, so an SDK trajectory
serialises straight back to a bench item and a bench item deserialises straight
into a trajectory (``from_bench`` / ``to_bench``). A ``verify()`` trajectory may
mix dataclasses and raw bench dicts freely: ``normalize_event`` is the
dict-accepting boundary that folds either into a dataclass.

Bench FORMAT v1.0 (the only fields that cross the bench boundary):

  * message      ``{"type": "message", "text": str}``
  * tool_call    ``{"type": "tool_call", "call_id": str, "name": str, "arguments": Any}``
  * tool_result  ``{"type": "tool_result", "call_id": str, "is_error": bool,
                    "truncated": bool, "content": str}``

``ToolResult`` additionally carries two *SDK-internal provenance* flags,
``error_sniffed`` and ``truncation_inferred``. These record *how* an adapter
recovered a degraded signal (payload sniff / marker inference vs. a structured
framework bit). They are NEVER emitted to bench (``to_bench`` drops them) and are
absent from ``from_bench`` output; they exist only so ``verify()`` can grade
confidence 1.0 (structured) vs 0.7 (degraded).

Pure, standard-library only, no I/O, no globals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Union


def content_text(value: Any) -> str:
    """Text content as a string: a string, None, or a list of content blocks
    (``{"type": "text", "text": ...}`` dicts or plain strings, as chat APIs and
    MCP return them); non-text blocks are dropped. Used for claims, tasks and
    tool-result bodies, so the detector only ever sees strings."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = []
        for b in value:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict) and b.get("type", "text") == "text":
                parts.append(str(b.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return str(value)


# ---------------------------------------------------------------------------
# event dataclasses (bench FORMAT v1.0, 1:1)
# ---------------------------------------------------------------------------
@dataclass
class Message:
    """An assistant/model text turn. Empty text is preserved, never dropped."""
    text: str = ""

    def to_bench(self) -> Dict[str, Any]:
        return {"type": "message", "text": self.text or ""}


@dataclass
class ToolCall:
    """A tool invocation. A ToolCall with no following ToolResult is a legal
    *dangling call* -- adapters MUST preserve it and MUST NOT fabricate a
    result for it."""
    call_id: str
    name: str = ""
    arguments: Any = ""

    def to_bench(self) -> Dict[str, Any]:
        return {
            "type": "tool_call",
            "call_id": self.call_id,
            "name": self.name or "",
            "arguments": self.arguments if self.arguments is not None else "",
        }


@dataclass
class ToolResult:
    """A tool result.

    ``is_error`` / ``truncated`` are the bench-visible bits. ``error_sniffed``
    and ``truncation_inferred`` are SDK-internal provenance: True means the bit
    was recovered from an unstructured payload sniff / marker inference (a
    DEGRADED signal -> 0.7 confidence), False means it came from a structured
    framework signal (or was never set).
    """
    call_id: str
    content: str = ""
    is_error: bool = False
    truncated: bool = False
    # provenance -- not part of bench FORMAT, dropped by to_bench()
    error_sniffed: bool = False
    truncation_inferred: bool = False

    def to_bench(self) -> Dict[str, Any]:
        return {
            "type": "tool_result",
            "call_id": self.call_id,
            "is_error": bool(self.is_error),
            "truncated": bool(self.truncated),
            "content": content_text(self.content),
        }


Event = Union[Message, ToolCall, ToolResult]


# ---------------------------------------------------------------------------
# bench <-> dataclass boundary
# ---------------------------------------------------------------------------
def from_bench(d: Dict[str, Any]) -> Event:
    """One bench event dict -> the matching dataclass. Unknown/None types raise."""
    if not isinstance(d, dict):
        raise TypeError(f"bench event must be a dict, got {type(d).__name__}")
    t = d.get("type")
    if t == "message":
        return Message(text=d.get("text") or "")
    if t == "tool_call":
        return ToolCall(
            call_id=d.get("call_id"),
            name=d.get("name") or "",
            arguments=d.get("arguments", ""),
        )
    if t == "tool_result":
        return ToolResult(
            call_id=d.get("call_id"),
            content=content_text(d.get("content")),
            is_error=bool(d.get("is_error")),
            truncated=bool(d.get("truncated")),
        )
    raise ValueError(f"unknown bench event type: {t!r}")


def normalize_event(x: Union[Event, Dict[str, Any]]) -> Event:
    """Dict-accepting boundary: pass a dataclass through, fold a dict via
    ``from_bench``. This is what lets ``verify()`` accept a mixed trajectory."""
    if isinstance(x, (Message, ToolCall, ToolResult)):
        return x
    return from_bench(x)


def to_bench(trajectory: List[Union[Event, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Whole trajectory -> a list of bench event dicts (provenance stripped)."""
    return [normalize_event(e).to_bench() for e in (trajectory or [])]


def to_item(trajectory: List[Union[Event, Dict[str, Any]]],
            final_claim: str = "", task: str = "") -> Dict[str, Any]:
    """Build the interchange ITEM the clean-room detector consumes:
    ``{"task", "events", "closing"}``."""
    return {
        "task": task or "",
        "events": to_bench(trajectory),
        "closing": final_claim or "",
    }


# ---------------------------------------------------------------------------
# shared adapter helpers: host-path redaction, error-payload sniffing,
# truncation-marker matching. Kept here so every adapter shares one
# implementation and one default-marker registry.
# ---------------------------------------------------------------------------
import re  # noqa: E402  (kept adjacent to the helpers that use it)

# Host-path shapes to scrub from ANYTHING an adapter re-emits (real OpenInference
# tracebacks were found leaking absolute host paths).
# We scrub the PATHS only, never the words "Error"/"Traceback", so error-sniffing
# still works on redacted content.
_PATH_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"[A-Za-z]:\\(?:[^\s\"'<>|]+\\?)+"),          # Windows  C:\a\b\c
    re.compile(r"(?<![\w.])/(?:home|Users|root|mnt|opt|var|tmp)/[^\s\"'<>|:]+"),  # POSIX
    re.compile(r'File "[^"]*"'),                              # traceback frame path
)
_REDACTED = "<path>"


def redact(text: Any) -> Any:
    """Scrub absolute host paths / traceback frame paths from a string. Non-str
    passes through untouched (so structured arguments survive)."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pat in _PATH_PATTERNS:
        out = pat.sub(_REDACTED, out)
    return out


# --- error-payload sniffing (recovery for the error-bit collapse) ------------
# Conservative built-in patterns; a tool result whose body matches is treated as
# a failure ONLY when the framework gave no structured error bit. Overridable via
# Config.error_patterns.
DEFAULT_ERROR_PATTERNS: Tuple[str, ...] = (
    r"^\s*error\b[:\s]",
    r"^\s*exception\b[:\s]",
    r"traceback \(most recent call last\)",
    r"^\s*fatal\b[:\s]",
)


def looks_like_error(content: str, patterns: Tuple[str, ...] = DEFAULT_ERROR_PATTERNS) -> bool:
    """True iff a tool-result body reads as a failure payload. Every pattern,
    built-in or caller-supplied, is matched case-insensitively."""
    if not content:
        return False
    return any(re.search(p, content, re.IGNORECASE) for p in patterns)


# --- truncation-marker matching ---------------------------------------------
# Per-framework default markers live in each adapter; this matches a content
# string against a set of literal-or-regex markers plus caller-supplied extras.
# The bench universal seam is included so seam-bearing bench content round-trips.
BENCH_SEAM = "[...omitted...]"


def matches_truncation(content: str, markers: Tuple[Any, ...] = ()) -> bool:
    """True iff content bears any truncation marker. ``markers`` items may be
    plain substrings, ``re.Pattern`` objects, or regex strings starting with the
    sentinel ``re:``."""
    if not content:
        return False
    for m in markers:
        if isinstance(m, re.Pattern):
            if m.search(content):
                return True
        elif isinstance(m, str) and m.startswith("re:"):
            if re.search(m[3:], content):
                return True
        elif isinstance(m, str):
            if m in content:
                return True
    return False
