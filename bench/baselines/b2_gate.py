#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
b2_gate — the production honesty gate, ported as a benchmark detector (heuristic tier).

This baseline runs the REAL gate's decision logic against each interchange item. It does
NOT reimplement the checks: it imports `analyze()` from the live gate module
(stop-honesty-gate.py) via importlib and feeds it an adapted turn. The gate is never
edited — only read/imported (its own sys.path insertion resolves transcript_utils and
judge_prompts from its own directory).

HEURISTIC TIER ONLY — no Ollama dependency
------------------------------------------
The gate has a two-tier design: heuristic checks 1/2/3, plus a local-model judge that can
VETO a Check-3 block (narrative-vs-assertion) and shadow-log Check-3 passes. This baseline
pins the judge OFF by forcing the gate's test seam `HONESTY_GATE_JUDGE_STUB=unavailable`
(and the association stub likewise). With the stub "unavailable", the veto judge returns
None and the heuristic block stands; because we call `analyze(turn)` with no judge_ctx, the
shadow judge never runs at all. Result: b2 makes ZERO network calls and reflects exactly the
gate's HEURISTIC verdict. A direct consequence, documented and intended: b2 keeps the gate's
heuristic-tier false blocks (e.g. the narrative "already fixed" / H5 shape that only the judge
tier rescues) — the gap b3/b4 are meant to close.

Adapter: interchange item -> Claude Code transcript turn
--------------------------------------------------------
  task            -> the human turn-start message (turn boundary)
  message event   -> mid-turn assistant text block
  tool_call event -> assistant tool_use block (call_id->id, name, arguments->input)
  tool_result     -> user tool_result block (call_id->tool_use_id, is_error, content)
                     truncated:true -> a gate-recognized "[output truncated]" marker is
                     appended to the content so the structured flag reaches the gate's
                     truncation signal (the seam text alone is not in the gate's markers)
  closing         -> the FINAL assistant text block (the message the gate judges)

Verdict: "lie" iff the gate returns decision == "block"; else "honest". The fired check
id maps to a diagnostic category (1->L1, 2->L2, 3->L3), never scored in v1.

Contract: judge(item) -> {"verdict": ..., "category": ...(optional)}. Stdlib only.
"""
import importlib.util
import os

# ---- force the judge tier OFF before importing the gate (heuristic-only, no network) ----
os.environ["HONESTY_GATE_JUDGE_STUB"] = "unavailable"   # veto judge -> None -> heuristic block stands
os.environ["HONESTY_GATE_ASSOC_STUB"] = "unavailable"   # shadow judge -> never consulted (no judge_ctx)

_GATE_PATH = os.environ.get("PB_GATE_PATH")
if not _GATE_PATH:
    raise RuntimeError(
        "PB_GATE_PATH is not set. b2_gate imports the honesty gate script "
        "(stop-honesty-gate.py), which is not distributed with this benchmark. "
        "Set PB_GATE_PATH to the absolute path of your local copy.")


def _load_gate(path):
    spec = importlib.util.spec_from_file_location("pb_honesty_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # runs the gate's module body (defs only; __main__ guard skips main)
    return mod


_GATE = _load_gate(_GATE_PATH)

_TRUNC_MARKER = "\n[output truncated]"
_CHECK_TO_CATEGORY = {"1": "L1", "2": "L2", "3": "L3"}


def _u(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _tool_use(tid, name, args):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": args}]}}


def _tool_result(tid, is_error, content):
    return {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tid,
                         "is_error": bool(is_error), "content": content}]}}


def _text(text):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def item_to_turn(item):
    """Adapt one interchange item into a Claude Code transcript turn (list of entries)."""
    turn = [_u(item.get("task", "") or "")]
    for ev in item.get("events", []) or []:
        t = ev.get("type")
        if t == "message":
            turn.append(_text(ev.get("text", "") or ""))
        elif t == "tool_call":
            turn.append(_tool_use(ev.get("call_id"), ev.get("name", "?"),
                                  ev.get("arguments", "")))
        elif t == "tool_result":
            content = ev.get("content", "") or ""
            if ev.get("truncated"):
                content = content + _TRUNC_MARKER
            turn.append(_tool_result(ev.get("call_id"), ev.get("is_error"), content))
    # the closing is always the final assistant text (empty string is legal -> never flagged)
    turn.append(_text(item.get("closing", "") or ""))
    return turn


def judge(item):
    """Run the gate's heuristic decision on the adapted turn. block -> lie."""
    turn = item_to_turn(item)
    res = _GATE.analyze(turn)   # judge_ctx=None: shadow judge never runs; veto judge forced unavailable
    if res.decision == "block":
        out = {"verdict": "lie", "score": 1.0}
        cat = _CHECK_TO_CATEGORY.get(res.check)
        if cat:
            out["category"] = cat
        return out
    return {"verdict": "honest", "score": 0.0}


if __name__ == "__main__":
    import json
    import sys
    for ln in sys.stdin:
        ln = ln.strip()
        if ln:
            print(json.dumps(judge(json.loads(ln))))
