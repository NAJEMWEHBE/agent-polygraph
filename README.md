# agent-polygraph

A framework-agnostic **completion-lie detector** for AI agents. Given one agent
turn — a task, the tool calls and results it produced ("evidence"), and the final
assistant message ("closing") — `verify()` decides whether the closing makes a
completion or success claim that the turn's own evidence contradicts.

It measures the *turn*, not the model. The check is heuristic, deterministic, and
synchronous, with **zero runtime dependencies** (standard library only).

```python
from agent_polygraph import verify, ToolCall, ToolResult

v = verify(
    [ToolCall("c1", "deploy", {"svc": "web"}),
     ToolResult("c1", content="ERROR: image pull failed", is_error=True)],
    final_claim="Done -- deployed successfully.",
)

v.verdict      # "lie"
v.category     # "L1"  (error concealment)
v.confidence   # 1.0   (structured error bit)
v.evidence     # (Finding(check="error_concealment", detail=...),)
```

This is the SDK companion to [polygraph-bench](https://github.com/NAJEMWEHBE/agent-polygraph/tree/main/bench) (in this repo since 2026-11-05),
the public benchmark for agent-lying detectors. The detector shipped here is a
**clean-room reimplementation** of a private production honesty gate, validated at
verdict parity against that gate on the benchmark's 396-item corpus (numbers below).

---

## Install

```bash
pip install agent-polygraph
```

Python 3.10+. No third-party runtime dependencies.

---

## What it catches

Three heuristic check families, each mapped to a diagnostic category:

| category | family (`check` id) | catches |
|---|---|---|
| **L1** | `error_concealment` | an unrecovered tool error hidden behind a plain, unacknowledged success claim |
| **L2** | `completion_vs_evidence` | a completeness / counted-total claim over a full-page fetch with no proof the set was exhausted |
| **L3** | `narrative_vs_assertion` | a runtime "it works / is fixed" claim after a file edit with nothing actually run |

`verify()` returns a `Verdict`:

```python
Verdict(
    verdict="lie" | "honest",
    evidence=(Finding(check, detail), ...),
    confidence=1.0 | 0.7,      # signal-quality GRADE, not a probability
    detector_version="0.2.0",  # self-stamped for cross-version comparison
    flags=(...),               # degradation markers driving a 0.7 grade
    category="L1" | "L2" | "L3" | None,
)
```

`confidence` is a **signal-quality grade, not a probability**: `1.0` when the
verdict rode a structured framework signal, `0.7` when it rode an *inferred*
truncation marker or a *sniffed* error payload (see [degraded signals](#the-error-bit-is-the-portability-spine)).

---

## Framework adapters

Four adapters map a framework's native emission onto the same `verify()` core.
The design default is **audit** everywhere — a foreign framework's false-positive
rate is unknown until you measure it on your own traffic, and a false halt kills
adoption. **Blocking is opt-in**, and only on the two surfaces that can actually
halt a run; the audit-only adapters **raise loudly** on `mode="block"` rather than
silently downgrading.

| adapter | entry point | tool-error signal | default | block |
|---|---|---|---|---|
| OpenAI Agents SDK | `output_guardrail` | span wrapper `error.data.error` (structured) | audit | **opt-in** |
| Claude Code stop-hook | `stop_hook` | `tool_result.is_error` (structured) | audit | **opt-in** |
| OTel / OpenInference | `verify_spans` | OTel span `status_code=ERROR` (structured) | audit | raises loud |
| LiteLLM proxy | `post_call_guardrail` | **none** — sniff `role:"tool"` text only | audit | raises loud |

```python
from agent_polygraph.adapters import output_guardrail

result = output_guardrail(trace_export, mode="block")  # halt on a lie
if result.tripwire_triggered:
    ...  # the Agents SDK stops the run
```

Per-adapter quickstarts live in [`docs/`](https://github.com/NAJEMWEHBE/agent-polygraph/tree/main/docs):
[OpenAI Agents](https://github.com/NAJEMWEHBE/agent-polygraph/blob/main/docs/openai_agents.md) ·
[OpenInference / OTel](https://github.com/NAJEMWEHBE/agent-polygraph/blob/main/docs/openinference.md) ·
[Claude Code](https://github.com/NAJEMWEHBE/agent-polygraph/blob/main/docs/claude_code.md) ·
[LiteLLM](https://github.com/NAJEMWEHBE/agent-polygraph/blob/main/docs/litellm.md).

---

## Validation

All numbers below come from actually-executed runs and are reported verbatim.
Parity and the recall floor are the honest limits of a heuristic detector —
read them before you deploy in `block` mode.

### Parity vs the private gate — 393 / 396 (99.24%) at 0.1.0

The detector was written clean-room from the public benchmark spec and corpus,
then refined by **behavioural** parity iteration against a private production gate
treated as a black box (feed an input, observe its block/allow verdict; the gate's
source was never read). On all 396 public bench items:

```
agreement matrix (clean-room \ gate):
                 gate=lie   gate=honest
  clean=lie            88             0
  clean=honest          3           305
```

- **0 over-blocks** — the clean detector never blocks anything the gate allows.
- **3 under-blocks**, all category **H2** (disclosed-failure honest twins). These
  are exactly the three items the gate's own heuristic tier *false-blocks*; the
  clean detector **matches the ground-truth label on all three** (they are honest).
  Reproducing the gate's mistake would require either reading its source or
  overfitting a rule that makes the public detector actively worse.

Label-level, both over the 396-item set: **identical recall (0.460)** and the same
true-positive set; the clean detector carries **3 fewer false blocks**
(FP 3.18% vs the gate's 4.55%).

**Since 0.1.0 the detector has moved away from the gate on purpose.** The 0.2.0
detector no longer treats a documentation-only edit (`.md`, `.rst`, `.txt`, …) as
code that needs a test run, which clears the three H5 honest items that both 0.1.0
and the gate false-blocked. It flags **85** items instead of 88: the same 81 true
positives (recall **0.460**) and **4** false blocks (FP **1.82%** vs the gate's
4.55%). Because every 0.1.0 block was also a gate block, the agreement with the gate
is now 390 / 396; all six disagreements are items the gate blocks and the label
calls honest. This was derived from the 0.1.0 matrix above, not re-run against the
gate. The other fixes since 0.1.0 (whole-word vocabulary, evidence tied to its own
tool, page-size arguments, negated success claims) change no verdict on the 396
items.

### Real-trace replay — 33 traces, 0 unexplained flips (0.1.0)

33 real captured framework traces (OpenAI Agents ×10, OpenInference ×6, Claude
Code ×13, LiteLLM ×4; local models only, no paid API calls) were replayed through
each shipped adapter's public surface **and** through `verify()` on the same parsed
trajectory:

- **0 surface-vs-direct disagreements** across all 33.
- **2 lies caught** — both Claude Code turns with an unrecovered structured
  `is_error` bit under a success claim (confidence 1.0). The other 31 → honest,
  all correct true-negatives (including honest failure-*disclosure* turns).

The captured traces are not shipped, so this replay was not re-run for the current
detector. Claude Code transcripts are now judged one turn at a time (the entries
after the last human message), which can change the verdict on a multi-turn
session.

### LiteLLM opaque-error recall floor — read this before using the LiteLLM adapter

The detection signal rides the **structured tool-error bit**. At the LiteLLM proxy
that bit does not exist: a tool failure is visible only as whatever text the agent
wrote into the `role:"tool"` message. The adapter recovers what it can by sniffing
the payload (`"ERROR: ..."`, `Traceback`, …) — but a failure that does not read as
an error in text is **invisible** at that layer.

Measured cost, round-trip over all 396 bench items (reference = direct detector):

```
path              agree  flips   recall      FP
direct(ref)      100.0%      0    0.460    0.018
agents           100.0%      0    0.460    0.018
openinference    100.0%      0    0.460    0.018
claude_code      100.0%      0    0.460    0.018
litellm           89.6%     41    0.227    0.018
```

- The three **structured-error** adapters are fully consistent with direct
  detection — **0 verdict flips** on 396 items.
- **LiteLLM: 41 flips, all `lie -> honest`** — recall drops from **0.460 to 0.227**
  on error-concealment lies whose tool text does not read as an error. **FP is
  unchanged (0.018)** — no false blocks are introduced, only real lies are missed.

This is a measured framework ceiling, not a bug: the proxy sits below the structured
error signal. The LiteLLM adapter is **audit-only** for this reason.

---

## Known limitations

These are structural properties of a heuristic-tier detector, not defects to
be worked around silently.

**The error bit is the portability spine.**
Every adapter recovers the tool-failure signal per framework — structured where the
framework exposes it (Agents span wrapper, OpenInference OTel status, Claude Code
`is_error`), sniffed from the payload where it does not (LiteLLM). A sniffed error
grades to **0.7 confidence**; a framework that redacts tool outputs by default is a
dark surface. If you take one design fact from this project: a completion-lie
detector is only as portable as the tool-error signal it can recover.

**Dangling call + success claim is a heuristic miss (judge-tier material).**
A `tool_call` with no matching `tool_result` (a legal *dangling call*) plus a
"Done, deployed successfully" closing returns **honest**. The heuristic tier cannot
associate an outcome that never returned. This is the motivating example for the
[judge-tier extension](#judge-tier-extension) below, and it is a documented miss in
both the private gate and this detector.

**A disclosure is not tied to the tool it discloses.**
Any on-topic acknowledgement in the closing ("X failed", "could not deploy")
clears error concealment for the whole turn, even when it names a different tool
than the one that errored. Scoping an acknowledgement to its own tool needs
claim-to-evidence association, which is judge-tier work; a sentence-level rule
would false-block honest closings such as "The upload failed. Everything else is
done." A retry clears an error only when the *same* tool later succeeds, matched
by tool name, not by arguments.

**Inferred truncation is a low-confidence, verdict-neutral signal.**
Truncation is inferred from seam markers (`[...omitted...]`, Claude Code's
`[N lines truncated]`, or markers you register). An *inferred* truncation only
**downgrades confidence to 0.7**; it never flips a verdict (the detector uses the
bit solely to grade an L2 completeness finding). A *structured* truncation flag
(Claude Code `truncatedByTokenCap`) stays at 1.0.

**A1 — Claude Code truncation markers inside skipped file-read attachments.**
In Claude Code, the `[N lines truncated]` marker sometimes rides inside a file-read
`attachment` block (rather than a `tool_result.content`), which the adapter skips.
On those turns the truncation bit is not set. This is **verdict-safe and
confidence-neutral** in every observed case (it can only matter when an L2
completeness finding fires over such an attachment — which did not occur in
validation); the residual is a possible missed 0.7 downgrade, never a false block.
A follow-up may scan attachment snippets or let you register the marker via
`Config.extra_truncation_markers`.

---

## Judge-tier extension

The heuristic detector is deterministic and cheap; some cases (the dangling-call
miss above) need reasoning it cannot do. `verify()` exposes a synchronous `Judge`
protocol as the stable extension seam. **No judge ships yet** — this is the
documented interface a future model-backed judge slots into without changing the
`verify()` contract.

```python
from agent_polygraph import verify, Judge, Verdict

class DanglingCallJudge:
    """Motivating example: catch a success claim over a call that never returned."""
    def judge(self, trajectory, final_claim, task, heuristic_verdict):
        call_ids = {e.call_id for e in trajectory if getattr(e, "call_id", None)
                    and type(e).__name__ == "ToolCall"}
        result_ids = {e.call_id for e in trajectory if type(e).__name__ == "ToolResult"}
        if call_ids - result_ids and heuristic_verdict.verdict == "honest":
            return Verdict("lie", confidence=0.7, detector_version="my-judge-0.1")
        return None  # keep the heuristic verdict

v = verify(trajectory, final_claim, judge=DanglingCallJudge(), escalate="honest_only")
```

`escalate` chooses which heuristic verdicts reach the judge: `"honest_only"`
(default — catch what the heuristic missed), `"degraded_only"` (any 0.7-confidence
verdict), or `"all"`. A judge that overrides a verdict self-stamps its own
`detector_version` so co-evolving verdicts stay comparable.

---

## Relationship to polygraph-bench

agent-polygraph and [polygraph-bench](https://github.com/NAJEMWEHBE/agent-polygraph/tree/main/bench) (merged into this repo on 2026-11-05, both histories kept)
are two halves of the same effort:

- **[polygraph-bench](https://github.com/NAJEMWEHBE/agent-polygraph/tree/main/bench)** (`bench/`) — the public
  396-item benchmark that *measures* completion-lie detectors. The clean-room
  detector here is validated against it.
  [Leaderboard](https://najemwehbe.github.io/agent-polygraph/) ·
  [Dataset on Hugging Face](https://huggingface.co/datasets/najemwehbe/polygraph-bench).
- **agent-polygraph** (this package) — the *detector* you run in production, with
  adapters for real frameworks.

If you build a better detector, benchmark it on polygraph-bench and open a
submission PR here (files under `bench/`); see
[CONTRIBUTING.md](https://github.com/NAJEMWEHBE/agent-polygraph/blob/main/CONTRIBUTING.md).

---

## License

MIT. See [LICENSE](https://github.com/NAJEMWEHBE/agent-polygraph/blob/main/LICENSE).
