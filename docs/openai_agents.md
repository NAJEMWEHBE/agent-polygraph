# OpenAI Agents SDK adapter

Runs the polygraph check as an [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
output guardrail. This surface **can halt a run**, so `mode="block"` is supported.

## Quickstart: live guardrail

```python
from agents import Agent, Runner, add_trace_processor
from agent_polygraph.adapters import PolygraphTracingProcessor, make_output_guardrail

spans = PolygraphTracingProcessor()          # keeps each live trace's span exports
add_trace_processor(spans)

agent = Agent(
    name="deployer",
    instructions="...",
    tools=[...],
    output_guardrails=[make_output_guardrail(spans, mode="block")],
)

result = await Runner.run(agent, "deploy web")  # raises OutputGuardrailTripwireTriggered on a lie
```

`make_output_guardrail` judges the agent's final output against the spans the
processor recorded for the current trace (`agents.get_current_trace()`). In
`mode="audit"` (the default) it never trips and the verdict is in the guardrail
result's `output_info` (a `Verdict.as_dict()`). The processor drops a trace's
spans when the trace ends and holds at most `max_traces` traces (default 256).

This wiring is unit-tested against stand-ins for the SDK's `TracingProcessor`,
`output_guardrail` and `get_current_trace`; it has not yet been run against a
live `openai-agents` release.

## Entry points

```python
from agent_polygraph.adapters import output_guardrail, from_openai_agents
```

- `PolygraphTracingProcessor(max_traces=256)`: a `TracingProcessor`; `.spans(trace_id)`
  returns the span exports recorded so far.
- `make_output_guardrail(processor, *, mode="audit", config=DEFAULT_CONFIG, name="agent_polygraph")`
  returns an SDK `OutputGuardrail` (needs `pip install openai-agents`).
- `from_openai_agents(trace_export, *, config=DEFAULT_CONFIG) -> (trajectory, final_claim, task)`
  parses a span-export stream into an SDK trajectory.
- `output_guardrail(trace_export, final_claim=None, task=None, *, mode="audit", config=DEFAULT_CONFIG) -> GuardrailResult`
  parses and runs `verify()` in one call, for offline traces.

## Input

A list of span-export dicts (each span's native `Span.export()`, as a
`TracingProcessor` receives them). Envelope lines (`{"object": "trace"}`, `_meta`)
are tolerated and skipped.

## Offline traces

```python
from agent_polygraph.adapters import output_guardrail

result = output_guardrail(trace_export, mode="audit")   # trace_export: list of span.export() dicts
if result.output_info.is_lie:
    log.warning("polygraph: %s", result.output_info.category)
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
  in the span. `make_output_guardrail` always passes the agent's real output;
  with `output_guardrail` on offline traces, pass an explicit `final_claim=`.
- **Truncation:** the Agents SDK emits no truncation signal. Register one via
  `Config(extra_truncation_markers=(...))` if your tools truncate; any such marker
  is *inferred* (confidence 0.7). Truncation never flips a verdict.
