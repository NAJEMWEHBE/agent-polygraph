#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polygraph Bench detector runner.

Runs a detector over an items.jsonl file and writes a verdicts.jsonl file.

    python run_detector.py --detector <module-or-path> --items items.jsonl --out verdicts.jsonl

Detector contract
-----------------
A detector is a Python module exposing:

    def judge(item: dict) -> dict

`item` is one interchange item (transcript-format v1). The returned dict MUST carry
`verdict` in {"lie","honest"} and MAY carry `score` (lie-probability in [0,1]) and a
predicted `category` (diagnostics only). The runner copies `verdict`, and `score` /
`category` when present, into the output line alongside the item's `id`.

`--detector` accepts either a filesystem path to a `.py` file (e.g.
`../baselines/b2_gate.py`) or an importable dotted module name (resolved after adding
the baselines dir + cwd to sys.path).

Fail-soft: if a detector's judge() raises on an item, the runner records `honest`
(mirrors the format's missing-verdict law) and counts it — a broken detector degrades
to the honest floor rather than crashing the run.
"""
import argparse
import importlib
import importlib.util
import json
import os
import sys


def _read_items(path):
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except json.JSONDecodeError:
                continue


def _load_detector(spec):
    """Load a detector module from a file path or a dotted module name."""
    looks_like_path = spec.endswith(".py") or os.path.sep in spec or (os.altsep and os.altsep in spec)
    if looks_like_path or os.path.exists(spec):
        path = os.path.abspath(spec)
        # let a file-loaded baseline import its siblings (e.g. shared helpers)
        sys.path.insert(0, os.path.dirname(path))
        modspec = importlib.util.spec_from_file_location(
            "pb_detector_" + os.path.splitext(os.path.basename(path))[0], path)
        mod = importlib.util.module_from_spec(modspec)
        modspec.loader.exec_module(mod)
        return mod
    # dotted module name: make the baselines dir importable
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "baselines"))
    sys.path.insert(0, os.getcwd())
    return importlib.import_module(spec)


def run(detector_spec, items_path, out_path):
    det = _load_detector(detector_spec)
    if not hasattr(det, "judge"):
        raise SystemExit(f"detector '{detector_spec}' exposes no judge(item) function")
    n = 0
    n_lie = 0
    n_fail = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for item in _read_items(items_path):
            iid = item.get("id")
            try:
                res = det.judge(item) or {}
                verdict = "lie" if res.get("verdict") == "lie" else "honest"
            except Exception as e:  # fail-soft to the honest floor
                verdict = "honest"
                n_fail += 1
                res = {}
                print(f"  [warn] judge() raised on {iid}: {e}", file=sys.stderr)
            rec = {"id": iid, "verdict": verdict}
            if isinstance(res, dict):
                if isinstance(res.get("score"), (int, float)):
                    rec["score"] = res["score"]
                if res.get("category"):
                    rec["category"] = res["category"]
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if verdict == "lie":
                n_lie += 1
    print(f"wrote {out_path}: {n} verdicts ({n_lie} lie / {n - n_lie} honest"
          + (f", {n_fail} fail-soft->honest" if n_fail else "") + ")")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a detector over items.jsonl")
    ap.add_argument("--detector", required=True, help="path to a .py detector or a dotted module name")
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    run(args.detector, args.items, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
