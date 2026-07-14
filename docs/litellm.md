# LiteLLM proxy adapter

Runs the polygraph check as a [LiteLLM](https://github.com/BerriAI/litellm) post-call
guardrail. **Audit-only** — tool-error status is opaque at the proxy and blocking
sits in the request critical path, so `mode="block"` raises loudly.

> **Read the recall floor first.** LiteLLM has **no structured tool-error signal**.
> A tool failure is recoverable only from text the agent wrote into the `role:"tool"`
> content, so recall on error-concealment lies drops from **0.460 to 0.227** on the
> benchmark (41 of 396 items flip `lie -> honest`). False positives are unchanged —
> no false blocks are introduced, real lies are missed. Every recovered error grades
> to **0.7 confidence** (sniffed). See the
> [README recall-floor section](../README.md#litellm-opaque-error-recall-floor--read-this-before-using-the-litellm-adapter).

## Entry points

```python
from agent_polygraph.adapters import post_call_guardrail, from_litellm
```

- `from_litellm(messages, response=None, *, config=DEFAULT_CONFIG) -> (trajectory, final_claim, task)`
- `post_call_guardrail(data, response=None, *, mode="audit", config=DEFAULT_CONFIG) -> Verdict`

## Input

Shaped like LiteLLM's `async_post_call_success_hook`: `data` is the request body,
where `data["messages"]` on the **final** call carries the full resent history
(system, user, `assistant(tool_calls)`, `role:"tool"` results); `response` is the
completion.

## Quickstart

```python
from agent_polygraph.adapters import post_call_guardrail

# inside your async_post_call_success_hook(data, response, ...):
v = post_call_guardrail(data, response, mode="audit")
if v.is_lie:
    log.warning("polygraph: %s (conf %.1f, flags=%s)", v.category, v.confidence, v.flags)
```

## Framework specifics

- **Full-history-resend only.** The adapter relies on the last call carrying the
  whole conversation in `messages` (the standard stateless tool-calling pattern).
  Stateful / threaded API patterns that do not resend history starve `verify()` —
  the trajectory would be incomplete.
- **Opaque errors, sniff-only recovery.** No structured error field exists; a
  failure is detected only if its `role:"tool"` text reads as an error
  (`ERROR:`, `Traceback`, …). Always degraded (0.7). A silently-failing tool is
  invisible at this layer.
- **`Function(...)` repr strings.** Some captures serialize
  `tool_calls[].function` as a `Function(arguments=..., name=...)` repr string
  rather than a dict — both are parsed.
- **No truncation signal.** Register markers via
  `Config(extra_truncation_markers=(...))` if needed; always inferred.
