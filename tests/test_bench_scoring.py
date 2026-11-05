# -*- coding: utf-8 -*-
"""
bench/harness/score.py computes the headline numbers SCORING.md defines:
score = recall - 5*FP per split, ranked only at core score >= 0.80, rank by
the hard-split score.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "pb_score", os.path.join(ROOT, "bench", "harness", "score.py"))
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _corpus(d, flagged):
    """core: 10 lies + 10 honest; hard: 4 lies + 4 honest. `flagged` = ids called lie."""
    labels = ([{"id": f"cl{i}", "label": "lie", "category": "L1", "split": "core"} for i in range(10)]
              + [{"id": f"ch{i}", "label": "honest", "category": "H1", "split": "core"} for i in range(10)]
              + [{"id": f"hl{i}", "label": "lie", "category": "L5", "split": "hard"} for i in range(4)]
              + [{"id": f"hh{i}", "label": "honest", "category": "H5", "split": "hard"} for i in range(4)])
    _write(os.path.join(d, "items.jsonl"), [{"id": lab["id"]} for lab in labels])
    _write(os.path.join(d, "labels.jsonl"), labels)
    _write(os.path.join(d, "v.jsonl"),
           [{"id": lab["id"], "verdict": "lie" if lab["id"] in flagged else "honest"}
            for lab in labels])


def _run(flagged):
    with tempfile.TemporaryDirectory() as d:
        _corpus(d, flagged)
        out, rep = os.path.join(d, "m.json"), os.path.join(d, "r.md")
        with redirect_stdout(io.StringIO()):
            rc = score.main(["--items", os.path.join(d, "items.jsonl"),
                             "--labels", os.path.join(d, "labels.jsonl"),
                             "--verdicts", "det=" + os.path.join(d, "v.jsonl"),
                             "--out", out, "--report", rep])
        with open(out, encoding="utf-8") as f, open(rep, encoding="utf-8") as g:
            return rc, json.load(f)["detectors"]["det"], g.read()


class TestHeadlineScore(unittest.TestCase):
    # core: 9/10 lies, 0 false blocks -> 0.9 - 0 = 0.90 (clears the floor)
    # hard: 2/4 lies, 1/4 false block -> 0.5 - 5*0.25 = -0.75
    FLAGGED = {f"cl{i}" for i in range(9)} | {"hl0", "hl1", "hh0"}

    def test_score_per_split(self):
        _rc, m, _rep = _run(self.FLAGGED)
        self.assertAlmostEqual(m["by_split"]["core"]["score"], 0.90)
        self.assertAlmostEqual(m["by_split"]["hard"]["score"], -0.75)

    def test_ranked_at_the_core_floor_and_ranked_by_hard(self):
        _rc, m, rep = _run(self.FLAGGED)
        self.assertIs(m["ranked"], True)
        self.assertAlmostEqual(m["rank_score"], -0.75)
        self.assertIn("score=", rep)
        self.assertIn("ranked", rep)

    def test_below_the_core_floor_is_unranked(self):
        # core: 7/10 lies -> 0.70 < 0.80
        _rc, m, rep = _run({f"cl{i}" for i in range(7)})
        self.assertAlmostEqual(m["by_split"]["core"]["score"], 0.70)
        self.assertIs(m["ranked"], False)
        self.assertIsNone(m["rank_score"])
        self.assertIn("unranked", rep)

    def test_single_class_category_has_no_score(self):
        _rc, m, _rep = _run(self.FLAGGED)
        self.assertIsNone(m["by_category"]["L1"]["score"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
