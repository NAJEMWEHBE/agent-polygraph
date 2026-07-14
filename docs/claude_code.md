# Claude Code stop-hook adapter

Runs the polygraph check as a [Claude Code](https://docs.claude.com/en/docs/claude-code)
`Stop` hook, over the transcript tail the hook receives. A stop hook **can veto the
turn**, so `mode="block"` is supported. This is the strongest surface — Claude Code
carries a structured error bit, so no payload sniffing is needed.

## Entry points

```python
from agent_polygraph.adapters import stop_hook, from_transcript
```

- `from_transcript(entries, *, config=DEFAULT_CONFIG) -> (trajectory, final_claim, task)`
- `stop_hook(entries, *, mode="audit", config=DEFAULT_CONFIG) -> dict`

## Input

The transcript-tail entries a `Stop` hook sees: `human -> assistant (text +
tool_use) -> user (tool_result) -> ... -> final assistant text`.

## Quickstart

Wire it into a `Stop` hook that reads the transcript from stdin:

```python
import json, sys
from agent_polygraph.adapters import stop_hook

payload = json.load(sys.stdin)
entries = payload["transcript"]          # the tail entries

decision = stop_hook(entries, mode="block")
if decision["decision"] == "block":
    print(json.dumps({"decision": "block", "reason": decision["reason"]}))
    sys.exit(0)
```

`stop_hook` returns `{"decision": None | "block", "verdict": Verdict, "reason": ...}`.
In `mode="audit"` (default) `decision` is always `None` — log the `verdict` and let
the turn proceed.

## Framework specifics

- **Structured error bit:** `tool_result.is_error` (absent/None on success), with
  `tool_use_id` linking result to call. Confidence 1.0 on caught lies.
- **Hook-block channel:** `hook_blocking_error` / `hook_non_blocking_error`
  attachments record a *PreToolUse* hook that blocked a tool — the tool never ran.
  By ruling these are **not** counted as tool failures (a blocked call is a dangling
  call, not a returned error).
- **Polymorphic content:** `tool_result.content` is a string (Bash) or a list of
  `{"type":"text","text":...}` blocks (Agent/Task) — both handled.
- **Truncation:** the inline marker `[N lines truncated]` is *inferred* (confidence
  0.7); the `truncatedByTokenCap` attachment flag is *structured* (1.0). See the A1
  limitation in the [README](../README.md#known-limitations): a marker riding inside
  a skipped file-read attachment may not set the truncation bit — verdict-safe, but
  a possible missed confidence downgrade.
