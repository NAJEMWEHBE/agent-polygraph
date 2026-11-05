# Contributing

This repository holds two things: the **agent-polygraph** SDK (`src/`, `tests/`, `docs/`)
and the **polygraph-bench** benchmark (`bench/`). Issues and pull requests are welcome for
both.

## Submitting a detector to the benchmark

A submission is a pull request that adds your detector under `bench/`. The full rules are in
[bench/SCORING.md](bench/SCORING.md), section 5 (PR checklist). In short:

1. A detector module with `judge(item) -> {"verdict": "lie" | "honest", ...}`. The runner
   withholds the item `id`, so a verdict has to come from the transcript.
2. Deterministic behaviour (fixed seeds, temperature 0) and pinned dependencies.
3. Your self-computed scores on the public corpus, pasted into the PR:

   ```bash
   cd bench
   python harness/run_detector.py --detector <your_detector.py> \
       --items data/items.jsonl --out out/you.jsonl
   python harness/score.py --items data/items.jsonl --labels data/labels.jsonl \
       --verdicts you=out/you.jsonl --out out/metrics.json --report out/report.md
   ```

   Report the **core** and **hard** scores and whether the report says ranked.
4. Declared external calls, an MIT-compatible license, and cost reporting if the detector
   calls a paid API.

The maintainer scores accepted detectors on the hidden set before they appear on the
[leaderboard](https://najemwehbe.github.io/agent-polygraph/).

## Changing the SDK

```bash
pip install -e ".[test]"
pytest -q tests
```

- Add a failing test first, then make it pass. Tests exercise the public entry points
  (`verify()`, the adapters, the hook command, the bench harness), not private helpers.
- `src/agent_polygraph/detector.py` stays one stdlib-only file with no package-relative
  imports: the bench runner loads it on its own.
- `tests/test_bench_regression.py` pins the numbers the README publishes. If your change
  moves a verdict on the public corpus, update the README numbers and the pin in the same
  pull request, and say why in the description.
- The version lives in one place, `detector.__version__`; pyproject reads it from there.

## Changing the corpus

`bench/data` must stay exactly what `bench/generate_corpus.py` draws for seed 20260712,
and it must pass `python bench/leakage_audit.py bench/data`. CI checks both. Read
[bench/GENERATION.md](bench/GENERATION.md) before you touch the generator, and
[bench/FORMAT.md](bench/FORMAT.md) for the versioning rules.
