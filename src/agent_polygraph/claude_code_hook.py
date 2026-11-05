# -*- coding: utf-8 -*-
"""
Ready-made Claude Code ``Stop`` hook.

Claude Code runs a Stop hook with a JSON payload on stdin that names the
session transcript (``transcript_path``, a JSONL file) and says whether the
agent is already continuing because of a Stop hook (``stop_hook_active``).
This command reads that transcript, judges the turn that just ended, and on a
lie in ``--block`` mode prints ``{"decision": "block", "reason": ...}`` so Claude
Code keeps the agent working and shows it the reason.

    python -m agent_polygraph.claude_code_hook            # audit: never blocks
    python -m agent_polygraph.claude_code_hook --block    # block on a lie
    python -m agent_polygraph.claude_code_hook --log verdicts.jsonl

The hook never wedges the agent: a missing or unreadable transcript, or any
internal error, is reported on stderr and the turn is allowed to end (exit 0).
``stop_hook_active`` is honoured, so a block can never loop.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, TextIO

from .adapters.claude_code import stop_hook


def read_transcript(path: str) -> List[Dict[str, Any]]:
    """Transcript JSONL -> entries. Blank or torn lines (the file may still be
    being written) are skipped."""
    entries: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                entries.append(obj)
    return entries


def run(payload: Dict[str, Any], *, block: bool = False) -> Dict[str, Any]:
    """Judge the turn a Stop payload points at -> the ``stop_hook`` result."""
    path = payload.get("transcript_path")
    if not path:
        raise ValueError("Stop payload has no transcript_path")
    return stop_hook(read_transcript(path),
                     mode="block" if block else "audit",
                     stop_hook_active=bool(payload.get("stop_hook_active")))


def main(argv: Optional[List[str]] = None, *,
         stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m agent_polygraph.claude_code_hook",
        description="agent-polygraph as a Claude Code Stop hook")
    ap.add_argument("--block", action="store_true",
                    help="block the stop on a lie (default: audit only)")
    ap.add_argument("--log", metavar="PATH",
                    help="append each verdict as one JSON line to PATH")
    args = ap.parse_args(argv)
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    try:
        payload = json.load(stdin)
        if not isinstance(payload, dict):
            raise ValueError("Stop payload is not a JSON object")
        out = run(payload, block=args.block)
    except Exception as e:  # never wedge the agent on a hook failure
        print(f"agent-polygraph: hook skipped ({type(e).__name__}: {e}); "
              "check the Stop payload and its transcript_path", file=sys.stderr)
        return 0

    verdict = out["verdict"]
    if args.log:
        rec = dict(verdict.as_dict(), session_id=payload.get("session_id"),
                   decision=out["decision"])
        try:
            with open(args.log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"agent-polygraph: could not write log {args.log}: {e}", file=sys.stderr)

    if out["decision"] == "block":
        stdout.write(json.dumps({"decision": "block", "reason": out["reason"]}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
