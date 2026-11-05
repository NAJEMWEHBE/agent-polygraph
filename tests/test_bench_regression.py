# -*- coding: utf-8 -*-
"""
Pins the numbers the README publishes: the SDK detector's verdicts on the
396-item public corpus, the adapter round trip, and the b1 smoke fixtures.
A change to any of them must update the README in the same commit.
"""
from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BENCH = os.path.join(ROOT, "bench")
sys.path.insert(0, HERE)

from agent_polygraph.detector import detect                              # noqa: E402
from agent_polygraph.adapters import (openai_agents as OA, openinference as OI,  # noqa: E402
                                      claude_code as CC, litellm as LL)
import _synth as RT                                                        # noqa: E402


def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


ITEMS = _read_jsonl(os.path.join(BENCH, "data", "items.jsonl"))
LABELS = {lab["id"]: lab for lab in _read_jsonl(os.path.join(BENCH, "data", "labels.jsonl"))}

# sha256 of the sorted, newline-joined ids the detector calls "lie"
SDK_LIE_SET_SHA256 = "0217f15ac231003ea5b4dd597377e4372ef7d9017208d9043ec0ac5ad8ba6d93"


def _tally(verdicts):
    out = collections.Counter()
    for iid, v in verdicts.items():
        if v == "lie":
            lab = LABELS[iid]
            out[(lab["split"], "tp" if lab["label"] == "lie" else "fp")] += 1
    return out


class TestSdkDetectorOnThePublicCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verdicts = {it["id"]: detect(it).verdict for it in ITEMS}

    def test_published_counts(self):
        # README "Validation": 85 flagged = 81 true positives + 4 false blocks;
        # core 61 TP / 4 FP, hard 20 TP / 0 FP.
        self.assertEqual(_tally(self.verdicts), {("core", "tp"): 61, ("core", "fp"): 4,
                                                 ("hard", "tp"): 20})

    def test_exact_verdict_set(self):
        lies = sorted(i for i, v in self.verdicts.items() if v == "lie")
        digest = hashlib.sha256("\n".join(lies).encode("utf-8")).hexdigest()
        self.assertEqual(digest, SDK_LIE_SET_SHA256)


class TestAdapterRoundTrip(unittest.TestCase):
    """README round-trip table: the structured adapters agree with direct
    detection on every item; LiteLLM loses exactly 41 lies, never adds one."""

    @classmethod
    def setUpClass(cls):
        cls.direct = {it["id"]: detect(it).verdict for it in ITEMS}

    def _flips(self, fn):
        return collections.Counter((self.direct[it["id"]], fn(it)) for it in ITEMS
                                   if fn(it) != self.direct[it["id"]])

    def test_structured_adapters_never_flip(self):
        for name, fn in {
            "agents": lambda it: OA.output_guardrail(RT.synth_agents(it)).output_info.verdict,
            "openinference": lambda it: OI.verify_spans(RT.synth_openinference(it)).verdict,
            "claude_code": lambda it: CC.stop_hook(RT.synth_claude_code(it))["verdict"].verdict,
        }.items():
            with self.subTest(adapter=name):
                self.assertEqual(self._flips(fn), {})

    def test_litellm_opaque_error_floor(self):
        def ll(it):
            msgs, resp = RT.synth_litellm(it)
            return LL.post_call_guardrail({"messages": msgs}, resp).verdict
        self.assertEqual(self._flips(ll), {("lie", "honest"): 41})


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestB1SmokeFixtures(unittest.TestCase):
    def test_b1_matches_its_expected_verdicts(self):
        runner = _load("pb_run_detector_reg", os.path.join(BENCH, "harness", "run_detector.py"))
        fx = os.path.join(BENCH, "fixtures")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "b1.jsonl")
            runner.run(os.path.join(BENCH, "baselines", "b1_regex.py"),
                       os.path.join(fx, "items.jsonl"), out)
            got = {r["id"]: r["verdict"] for r in _read_jsonl(out)}
        want = {r["id"]: r["verdict"]
                for r in _read_jsonl(os.path.join(fx, "expected", "b1.jsonl"))}
        self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main(verbosity=2)
