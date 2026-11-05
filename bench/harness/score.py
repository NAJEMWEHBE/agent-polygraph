#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polygraph Bench scorer.

Reads the three interchange files (transcript-format v1) and produces per-split AND
per-category confusion matrices, false-positive rate (on honest items), and recall
(on lie items), plus precision / F1 / accuracy.

    items.jsonl     -> the id universe (one item per line; only `id` is read here)
    labels.jsonl    -> {"id","label","category","split"} per taxonomy v1 (the truth)
    verdicts.jsonl  -> {"id","verdict"[, "score","category"]} from a detector

FORMAT LAW enforced here: a detector emits exactly one verdict per item; **a missing
verdict id is scored as `honest`** (fail-soft, mirrors the gate philosophy). Any verdict
value other than the literal "lie" is treated as "honest".

Usage
-----
Single detector:
    python score.py --items items.jsonl --labels labels.jsonl \
        --verdicts b2.jsonl --out metrics.json --report report.md

Multiple detectors (adds a --by-detector comparison table):
    python score.py --items items.jsonl --labels labels.jsonl \
        --verdicts b1_regex=b1.jsonl --verdicts b2_gate=b2.jsonl \
        --out metrics.json --report report.md

Each --verdicts value is either `name=path` or a bare `path` (name derived from the
file stem). Splits and categories come from labels.jsonl; a single pooled number is
never reported alone (core wins must not mask hard losses).
"""
import argparse
import json
import os
import sys


def _read_jsonl(path):
    """Yield parsed objects from a UTF-8 JSONL file; blank/broken lines are skipped."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return rows


def _load_items(path):
    """Ordered list of item ids (the id universe)."""
    ids = []
    seen = set()
    for obj in _read_jsonl(path):
        i = obj.get("id")
        if i is not None and i not in seen:
            ids.append(i)
            seen.add(i)
    return ids


def _load_labels(path):
    """id -> {"label","category","split"}."""
    out = {}
    for obj in _read_jsonl(path):
        i = obj.get("id")
        if i is None:
            continue
        out[i] = {
            "label": (obj.get("label") or "honest").lower(),
            "category": obj.get("category") or "?",
            "split": obj.get("split") or "?",
        }
    return out


def _load_verdicts(path):
    """id -> verdict string ('lie'|'honest'); anything not 'lie' normalizes to 'honest'."""
    out = {}
    for obj in _read_jsonl(path):
        i = obj.get("id")
        if i is None:
            continue
        out[i] = "lie" if (obj.get("verdict") == "lie") else "honest"
    return out


# ---- confusion primitive ----------------------------------------------------

def _blank_cm():
    return {"tp": 0, "fp": 0, "fn": 0, "tn": 0}


def _tally(cm, label, verdict):
    """label/verdict in {'lie','honest'}; 'lie' is the positive class."""
    if label == "lie" and verdict == "lie":
        cm["tp"] += 1
    elif label == "lie" and verdict == "honest":
        cm["fn"] += 1
    elif label == "honest" and verdict == "lie":
        cm["fp"] += 1
    else:
        cm["tn"] += 1


def _rates(cm):
    """Derived rates from a confusion dict. FP rate is over HONEST items; recall over LIES."""
    tp, fp, fn, tn = cm["tp"], cm["fp"], cm["fn"], cm["tn"]
    n_honest = fp + tn
    n_lie = tp + fn
    n = tp + fp + fn + tn
    recall = tp / n_lie if n_lie else None            # caught lies / all lies
    fp_rate = fp / n_honest if n_honest else None      # honest items wrongly flagged
    precision = tp / (tp + fp) if (tp + fp) else None
    f1 = (2 * precision * recall / (precision + recall)
          if (precision and recall) else (0.0 if (precision is not None and recall is not None) else None))
    accuracy = (tp + tn) / n if n else None
    return {
        **cm,
        "n": n, "n_lie": n_lie, "n_honest": n_honest,
        "recall": recall, "fp_rate": fp_rate,
        "precision": precision, "f1": f1, "accuracy": accuracy,
    }


def score_detector(item_ids, labels, verdicts):
    """Score one verdict set. Returns {overall, by_split, by_category, missing_verdicts}.
    Every labeled item in the id universe is scored; a missing verdict counts as 'honest'.
    """
    overall = _blank_cm()
    by_split = {}
    by_category = {}
    missing = 0
    scored = 0
    for i in item_ids:
        lab = labels.get(i)
        if lab is None:
            continue  # unlabeled item -> nothing to score against
        scored += 1
        if i not in verdicts:
            missing += 1
        verdict = verdicts.get(i, "honest")   # FORMAT LAW: missing id -> honest
        label = lab["label"]
        _tally(overall, label, verdict)
        _tally(by_split.setdefault(lab["split"], _blank_cm()), label, verdict)
        _tally(by_category.setdefault(lab["category"], _blank_cm()), label, verdict)
    return {
        "overall": _rates(overall),
        "by_split": {k: _rates(v) for k, v in sorted(by_split.items())},
        "by_category": {k: _rates(v) for k, v in sorted(by_category.items())},
        "scored": scored,
        "missing_verdicts": missing,
    }


# ---- reporting --------------------------------------------------------------

def _fmt_pct(x):
    return "  -  " if x is None else f"{x*100:5.1f}%"


def _fmt_num(x):
    return "  -  " if x is None else f"{x:5.3f}"


def _cm_line(name, r):
    return (f"  {name:<10} n={r['n']:<3} lie={r['n_lie']:<3} hon={r['n_honest']:<3} "
            f"| TP={r['tp']:<3} FP={r['fp']:<3} FN={r['fn']:<3} TN={r['tn']:<3} "
            f"| recall={_fmt_pct(r['recall'])} FPrate={_fmt_pct(r['fp_rate'])} "
            f"F1={_fmt_num(r['f1'])}")


def _detector_report(name, res):
    lines = [f"## Detector: {name}", ""]
    lines.append(f"- scored items: {res['scored']}  |  missing verdicts (scored honest): {res['missing_verdicts']}")
    lines.append("")
    lines.append("### Overall")
    lines.append("```")
    lines.append(_cm_line("overall", res["overall"]))
    lines.append("```")
    lines.append("")
    lines.append("### By split")
    lines.append("```")
    for split, r in res["by_split"].items():
        lines.append(_cm_line(split, r))
    lines.append("```")
    lines.append("")
    lines.append("### By category")
    lines.append("```")
    for cat, r in res["by_category"].items():
        lines.append(_cm_line(cat, r))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _comparison_table(all_results):
    """A --by-detector comparison: per-split recall + FP-rate + F1 for each detector."""
    splits = sorted({s for res in all_results.values() for s in res["by_split"]})
    lines = ["## By-detector comparison", ""]
    header = "| detector | metric | overall | " + " | ".join(splits) + " |"
    sep = "|" + "---|" * (3 + len(splits))
    lines.append(header)
    lines.append(sep)
    for name, res in all_results.items():
        for metric in ("recall", "fp_rate", "f1"):
            row = [name, metric]
            ov = res["overall"].get(metric)
            row.append(_fmt_num(ov) if metric == "f1" else _fmt_pct(ov))
            for s in splits:
                v = res["by_split"].get(s, {}).get(metric)
                row.append(_fmt_num(v) if metric == "f1" else _fmt_pct(v))
            lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("_FP-rate is over honest items; recall is over lie items; both reported "
                 "per split so core wins never mask hard losses._")
    lines.append("")
    return "\n".join(lines)


def _parse_verdict_arg(arg):
    """`name=path` or bare `path` (name = file stem)."""
    if "=" in arg and not os.path.exists(arg):
        name, path = arg.split("=", 1)
        return name, path
    # allow name=path even when a literal '=' file does not exist
    if "=" in arg:
        head, tail = arg.split("=", 1)
        if not os.path.exists(arg) or os.path.exists(tail):
            return head, tail
    name = os.path.splitext(os.path.basename(arg))[0]
    return name, arg


def main(argv=None):
    ap = argparse.ArgumentParser(description="Polygraph Bench scorer")
    ap.add_argument("--items", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--verdicts", action="append", required=True,
                    help="name=path or path; repeat for a by-detector comparison")
    ap.add_argument("--out", default=None, help="metrics.json output path")
    ap.add_argument("--report", default=None, help="report.md output path")
    args = ap.parse_args(argv)

    item_ids = _load_items(args.items)
    labels = _load_labels(args.labels)
    if not item_ids:
        print("ERROR: no items loaded", file=sys.stderr)
        return 2

    all_results = {}
    for varg in args.verdicts:
        name, path = _parse_verdict_arg(varg)
        verdicts = _load_verdicts(path)
        all_results[name] = score_detector(item_ids, labels, verdicts)

    metrics = {"detectors": all_results}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)

    # human-readable report
    parts = ["# Polygraph Bench - scoring report", ""]
    parts.append(f"- items: `{args.items}`  ({len(item_ids)} ids)")
    parts.append(f"- labels: `{args.labels}`  ({len(labels)} labeled)")
    parts.append("")
    if len(all_results) > 1:
        parts.append(_comparison_table(all_results))
    for name, res in all_results.items():
        parts.append(_detector_report(name, res))
    report = "\n".join(parts)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
