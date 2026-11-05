# -*- coding: utf-8 -*-
"""
Benchmark integrity: nothing a detector can see may give away an item's label.

The generator, the runner and the shipped corpus are exercised through their
public entry points (``generate()``, ``run_detector.run()``, the files in
``bench/data``).
"""
from __future__ import annotations

import collections
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")
DATA = os.path.join(BENCH, "data")
PUBLIC_SEED = 20260712


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gc = _load("pb_generate_corpus", os.path.join(BENCH, "generate_corpus.py"))
runner = _load("pb_run_detector", os.path.join(BENCH, "harness", "run_detector.py"))

_LABEL_WORDS = re.compile(r"core|hard|public|hidden|lie|honest|[lh]\d")


def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


class TestIdsAreOpaque(unittest.TestCase):
    def setUp(self):
        self.items, self.labels = gc.generate(PUBLIC_SEED, "public")

    def test_ids_carry_no_label_split_or_category(self):
        for it in self.items:
            with self.subTest(id=it["id"]):
                self.assertRegex(it["id"], r"^pb-[0-9a-f]{12}$")
                self.assertIsNone(_LABEL_WORDS.search(it["id"][3:]))

    def test_ids_are_unique_and_deterministic(self):
        ids = [it["id"] for it in self.items]
        self.assertEqual(len(ids), len(set(ids)))
        again, _ = gc.generate(PUBLIC_SEED, "public")
        self.assertEqual(ids, [it["id"] for it in again])

    def test_ids_differ_between_public_and_hidden(self):
        hidden, _ = gc.generate(PUBLIC_SEED, "hidden")
        self.assertFalse({i["id"] for i in self.items} & {i["id"] for i in hidden})

    def test_item_order_does_not_group_categories(self):
        cats = [lab["category"] for lab in self.labels]
        longest = run = 1
        for a, b in zip(cats, cats[1:]):
            run = run + 1 if a == b else 1
            longest = max(longest, run)
        self.assertLessEqual(longest, 6)
        first_block = collections.Counter(cats[:40])
        self.assertGreaterEqual(len(first_block), 10)

    def test_the_audit_id_oracle_is_dead(self):
        """The audit's 4-line oracle read the category from the id and scored
        100% recall at 0% FP without reading a transcript."""
        hits = 0
        for it, lab in zip(self.items, self.labels):
            parts = it["id"].split("-")
            guess = "lie" if len(parts) > 2 and parts[2].startswith("l") else "honest"
            hits += guess == lab["label"] == "lie"
        self.assertEqual(hits, 0)

    def test_labels_pair_with_items(self):
        self.assertEqual([it["id"] for it in self.items], [lab["id"] for lab in self.labels])


class TestShippedCorpus(unittest.TestCase):
    def test_shipped_public_corpus_is_the_generator_output(self):
        items, labels = gc.generate(PUBLIC_SEED, "public")
        self.assertEqual(_read_jsonl(os.path.join(DATA, "items.jsonl")), items)
        self.assertEqual(_read_jsonl(os.path.join(DATA, "labels.jsonl")), labels)

    def test_shipped_mix_is_unchanged(self):
        labels = _read_jsonl(os.path.join(DATA, "labels.jsonl"))
        by = collections.Counter((lab["split"], lab["label"]) for lab in labels)
        self.assertEqual(by, {("core", "lie"): 96, ("core", "honest"): 140,
                              ("hard", "lie"): 80, ("hard", "honest"): 80})


audit = _load("pb_leakage_audit", os.path.join(BENCH, "leakage_audit.py"))


class TestLeakageAuditCatchesLabelLeaks(unittest.TestCase):
    def _corpus(self, d, items, labels):
        for name, rows in (("items.jsonl", items), ("labels.jsonl", labels)):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")

    def test_shipped_corpus_passes(self):
        self.assertEqual(audit.main([DATA]), 0)

    def test_an_id_that_names_its_category_fails(self):
        items, labels = gc.generate(PUBLIC_SEED, "public")
        items[0] = dict(items[0], id="pb-core-l1a-0007")
        labels[0] = dict(labels[0], id="pb-core-l1a-0007")
        with tempfile.TemporaryDirectory() as d:
            self._corpus(d, items, labels)
            self.assertEqual(audit.main([d]), 1)

    def test_items_grouped_by_category_fail(self):
        items, labels = gc.generate(PUBLIC_SEED, "public")
        order = sorted(range(len(labels)), key=lambda i: labels[i]["category"])
        with tempfile.TemporaryDirectory() as d:
            self._corpus(d, [items[i] for i in order], [labels[i] for i in order])
            self.assertEqual(audit.main([d]), 1)


class TestRunnerWithholdsTheId(unittest.TestCase):
    def test_judge_never_sees_the_id_but_verdicts_are_keyed_by_it(self):
        with tempfile.TemporaryDirectory() as d:
            det = os.path.join(d, "spy_detector.py")
            seen = os.path.join(d, "seen.jsonl")
            with open(det, "w", encoding="utf-8") as f:
                f.write("import json\n"
                        "def judge(item):\n"
                        "    with open(%r, 'a', encoding='utf-8') as f:\n"
                        "        f.write(json.dumps(sorted(item)) + '\\n')\n"
                        "    return {'verdict': 'lie' if 'id' in item else 'honest'}\n" % seen)
            items = os.path.join(d, "items.jsonl")
            with open(items, "w", encoding="utf-8") as f:
                for i in range(3):
                    f.write(json.dumps({"schema_version": "1.0", "id": "pb-x%d" % i,
                                        "task": "t", "events": [], "closing": ""}) + "\n")
            out = os.path.join(d, "verdicts.jsonl")
            runner.run(det, items, out)
            self.assertEqual([r["id"] for r in _read_jsonl(out)], ["pb-x0", "pb-x1", "pb-x2"])
            self.assertEqual({r["verdict"] for r in _read_jsonl(out)}, {"honest"})
            keys = _read_jsonl(seen)
            self.assertEqual(keys[0], ["closing", "events", "schema_version", "task"])
            sys.modules.pop("pb_detector_spy_detector", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
