# Claude Code stop-hook adapter

Runs the polygraph check as a [Claude Code](https://docs.claude.com/en/docs/claude-code)
`Stop` hook, over the session transcript the hook is pointed at. A stop hook **can veto the
turn**, so `mode="block"` is supported. This is the strongest surface — Claude Code
carries a structured error bit, so no payload sniffing is needed.

## Quickstart: the ready-made hook

Install the package where Claude Code can run it, then add a `Stop` hook to
`.claude/settings.json` (project) or `~/.claude/settings.json` (user):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command",
            "command": "python -m agent_polygraph.claude_code_hook --block" }
        ]
      }
    ]
  }
}
```

The hook reads the Stop payload from stdin, loads the session transcript from its
`transcript_path` (JSONL), and judges **only the turn that just ended** (everything
after the last message you typed). On a lie it prints
`{"decision": "block", "reason": ...}`: Claude Code keeps the agent working and shows
it the reason. On an honest turn it prints nothing.

- Leave out `--block` to run in **audit** mode (never blocks). Add
  `--log verdicts.jsonl` to append every verdict as one JSON line; start there and
  measure the false-positive rate on your own traffic before you block.
- `stop_hook_active` is honoured: when Claude Code is already continuing because of
  a Stop hook, the hook lets the turn end, so a block can never loop.
- The hook never wedges the agent: a missing transcript or any internal error is
  reported on stderr and the turn ends normally.
- The same command is installed as `agent-polygraph-claude-hook`.

## Entry points (library use)

```python
from agent_polygraph.adapters import stop_hook, from_transcript
```

- `from_transcript(entries, *, config=DEFAULT_CONFIG) -> (trajectory, final_claim, task)`
- `stop_hook(entries, *, mode="audit", config=DEFAULT_CONFIG, stop_hook_active=False) -> dict`

`entries` are the parsed lines of the transcript JSONL. Only the current turn (the
entries after the last human message; meta and sidechain entries skipped) is judged.
`stop_hook` returns `{"decision": None | "block", "verdict": Verdict, "reason": ...}`.
`verdict` is a `Verdict` object; use `verdict.as_dict()` when you need JSON. In
`mode="audit"` (default) `decision` is always `None`.

Your own edit or test-runner tools can be declared through the config:
`Config(edit_tools=("my_patch_tool",), run_tools=("run_tests",))`. Claude Code's
`Edit`, `MultiEdit`, `NotebookEdit` and `Write` are edits and `Bash` is a run out of
the box; an edit of a prose file (`.md`, `.rst`, `.txt`, `.adoc`) is not a code change.

## Framework specifics

- **Structured error bit:** `tool_result.is_error` (absent/None on success), with
  `tool_use_id` linking result to call. Confidence 1.0 on caught lies.
- **Hook-block channel:** `hook_blocking_error` / `hook_non_blocking_error`
  attachments record a *PreToolUse* hook that blocked a tool — the tool never ran.
  By design these are **not** counted as tool failures (a blocked call is a dangling
  call, not a returned error).
- **Polymorphic content:** `tool_result.content` is a string (Bash) or a list of
  `{"type":"text","text":...}` blocks (Agent/Task) — both handled.
- **Truncation:** the inline marker `[N lines truncated]` is *inferred* (confidence
  0.7); the `truncatedByTokenCap` attachment flag is *structured* (1.0). See the A1
  limitation in the [README](../README.md#known-limitations): a marker riding inside
  a skipped file-read attachment may not set the truncation bit — verdict-safe, but
  a possible missed confidence downgrade.
