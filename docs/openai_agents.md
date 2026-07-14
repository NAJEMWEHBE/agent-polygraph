# OpenAI Agents SDK adapter

Runs the polygraph check as an [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
output guardrail. This surface **can halt a run**, so `mode="block"` is supported.

## Entry points

```python
from agent_polygraph.adapters import output_guardrail, from_openai_agents
```

- `from_openai_agents(trace_export, *, config=DEFAULT_CONFIG) -> (trajectory, final_claim, task)`
  — parse a span-export stream into an SDK trajectory.
- `output_guardrail(trace_export, final_claim=None, task=None, *, mode="audit", config=DEFAULT_CONFIG) -> GuardrailResult`
  — parse + `verify()` in one call.

## Input

A list of span-export dicts — each span's native `Span.export()`, as a
`TracingProcessor` receives them. Envelope lines (`{"object": "trace"}`, `_meta`)
are tolerated and skipped.

## Quickstart

```python
from agents import Agent, Runner  # your existing agent
from agent_polygraph.adapters import output_guardrail

# Collect span exports from a TracingProcessor into `trace_export`
# (a list of dicts, one per span.export()).

result = output_guardrail(trace_export, mode="audit")
if result.output_info.is_lie:
    log.warning("polygraph: %s", result.output_info.category)

# Block mode: the tripwire halts the run on a lie.
result = output_guardrail(trace_export, mode="block")
if result.tripwire_triggered:
    ...  # the Agents SDK stops here
```

`GuardrailResult` mirrors the SDK's `GuardrailFunctionOutput`
(`.tripwire_triggered`, `.output_info` — the `Verdict`; `.verdict` alias).

## Framework specifics

- **`type` is nested** at `span_data.type`, not top-level.
- The **agent span carries no task/output**. Task is taken from the first
  `generation` span's user input; the final claim from the **last** `generation`
  span's non-empty assistant `output[0].content` (tool-call turns have
  `content=""` and are skipped).
- A **tool failure is on the span wrapper** `error = SpanError{message, data}`, and
  the function `output` is replaced with a generic retry string — the real error
  text is read from `error.data.error` (structured, confidence 1.0). Payload sniff
  is only a fallback.
- **Responses backend caveat:** with the OpenAI Responses backend,
  `ResponseSpanData.export()` drops the response content, so the final claim is not
  in the span. Pass an explicit `final_claim=` to `output_guardrail` in that case.
- **Truncation:** the Agents SDK emits no truncation signal. Register one via
  `Config(extra_truncation_markers=(...))` if your tools truncate; any such marker
  is *inferred* (confidence 0.7). Truncation never flips a verdict.
