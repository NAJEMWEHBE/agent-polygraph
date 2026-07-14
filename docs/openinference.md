# OpenInference / OpenTelemetry adapter

Consumes captured OTel spans emitted under the
[OpenInference](https://github.com/Arize-ai/openinference) convention. This is a
**read-only trace consumer** — there is no run to halt, so it is **audit-only** and
`mode="block"` raises loudly (never a silent downgrade).

OpenInference is targeted first because it captures tool arguments and results **by
default**; vanilla OTel GenAI semconv marks tool content opt-in-dark, so only
`error.type` is free there. Point this adapter at an OpenInference-instrumented
framework (LangChain, LlamaIndex, CrewAI, AutoGen, and others).

## Entry points

```python
from agent_polygraph.adapters import verify_spans, from_openinference
```

- `from_openinference(spans, *, config=DEFAULT_CONFIG) -> (trajectory, final_claim, task)`
- `verify_spans(spans, final_claim=None, task=None, *, mode="audit", config=DEFAULT_CONFIG) -> Verdict`

## Input

A list of serialized OTel/OpenInference spans (dicts). Attributes are the flat,
indexed scalar keys OpenInference emits (e.g.
`llm.output_messages.0.message.content`).

## Quickstart

```python
from agent_polygraph.adapters import verify_spans

# `spans` = your captured OpenInference spans as dicts.
v = verify_spans(spans)                 # mode defaults to "audit"
if v.is_lie:
    log.warning("polygraph: %s (conf %.1f)", v.category, v.confidence)

verify_spans(spans, mode="block")       # raises ValueError -- consumer can't halt
```

## Framework specifics

- **Span kind** is the attribute `openinference.span.kind` (`CHAIN` / `LLM` /
  `TOOL` / `AGENT`), not a top-level field. A single turn emits several `CHAIN`
  spans; only `TOOL` and `LLM` spans are consumed.
- **Attributes are flat indexed scalars** — messages are reconstructed from
  `llm.output_messages.<i>.message.*` keys.
- **Error bit = OTel span status** (`status_code == "ERROR"` + `status_description`),
  checked **before** dangling inference: a failed `TOOL` span has no `output.value`
  at all, so status must be read first or a failed tool would be mislabelled a
  dangling call. Structured error → confidence 1.0.
- Success `output.value` on a `TOOL` span is a serialized LangChain `ToolMessage`
  JSON; its `data.content` is extracted.
- **Redaction:** OpenInference exception stacktraces can leak absolute host paths;
  re-emitted content is scrubbed (`redact_paths` on by default).
- **Multi-agent nesting:** the adapter is order-driven (first user input = task,
  last `LLM` content = claim) and does not use parent/child structure. On concurrent
  or handoff sub-agent traces the "last LLM span" heuristic may pick the wrong turn.
