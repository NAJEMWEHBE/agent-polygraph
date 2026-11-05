#!/usr/bin/env python3
"""Polygraph Bench leakage / contamination audit.

Scans a generated corpus directory and fails (exit non-zero) on any of:
  1. Contamination deny-list hits (internal project markers that must never
     reach a public corpus).
  2. Generic secret patterns (cloud keys, tokens, PEM headers, JWTs, UUIDs,
     high-entropy blobs).
  3. Schema violations against transcript interchange format v1.0.
  4. Label / item integrity: 1:1 pairing, every taxonomy category populated,
     binding twin-pairing satisfied.

Usage:
    python leakage_audit.py <corpus_dir>

Exit 0 = clean; exit 1 = one or more violations (details printed).

The deny-list markers are assembled at runtime from fragments so that this
auditor's own source contains none of the forbidden literals verbatim.
"""

import json
import math
import os
import re
import sys

# --------------------------------------------------------------------------- #
# 1. Contamination deny-list  (assembled from fragments; never verbatim here)  #
# --------------------------------------------------------------------------- #

_DENY_LITERALS = [
    "NIN" + "OH",
    "F:\\" + "ai",
    "F:/" + "ai",
    "claude" + "-work",
    "way" + "finder",
    "claude" + "-tools",
    "stop-" + "honesty-" + "gate",
]

# regex markers: internal turn-ids (t00 + digits) and machine tags (PC + digits + _)
_DENY_REGEXES = [
    ("internal-turn-id", re.compile(r"t00\d{2,}")),
    ("machine-tag",      re.compile(r"PC\d+_")),
]

# --------------------------------------------------------------------------- #
# 2. Generic secret patterns                                                  #
# --------------------------------------------------------------------------- #

_SECRET_REGEXES = [
    ("aws-access-key",   re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws-secret-decl",  re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+]{20,}")),
    ("gcp-api-key",      re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("github-token",     re.compile(r"gh[posru]_[0-9A-Za-z]{36,}")),
    ("slack-token",      re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("pem-private-key",  re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt",              re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer-token",     re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{24,}")),
    ("uuid",             re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("secret-assign",    re.compile(
        r"(?i)(api[_\-]?key|access[_\-]?key|secret[_\-]?key|client[_\-]?secret|password|passwd)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9/+_=\-]{16,}")),
]

# High-entropy backstop for random credential blobs. The token charset excludes
# the path separator '/' so filesystem paths (not secrets) split at separators
# instead of reading as one long token; named credential formats with '/' (AWS
# secret, PEM, etc.) are caught by the dedicated regexes above.
_ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9+=_\-]{32,}")


def _shannon(s):
    if not s:
        return 0.0
    freq = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


# --------------------------------------------------------------------------- #
# 3 / 4. Taxonomy expectations                                                #
# --------------------------------------------------------------------------- #

LIE_CATEGORIES = {"L1a", "L1b", "L1c", "L2a", "L2b", "L3", "L4", "L5", "L6", "L7"}
HONEST_CATEGORIES = {"H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9"}
ALL_CATEGORIES = LIE_CATEGORIES | HONEST_CATEGORIES
VALID_SPLITS = {"core", "hard"}

# Canonical category -> split assignment (taxonomy contents table, authoritative):
#   core = L1a/b/c, L2a/b, L3 + H1, H2, H3, H4
#   hard = L4, L5, L6, L7 + H5, H6, H7, H8, H9
CANONICAL_SPLIT = {
    "L1a": "core", "L1b": "core", "L1c": "core",
    "L2a": "core", "L2b": "core", "L3": "core",
    "H1": "core", "H2": "core", "H3": "core", "H4": "core",
    "L4": "hard", "L5": "hard", "L6": "hard", "L7": "hard",
    "H5": "hard", "H6": "hard", "H7": "hard", "H8": "hard", "H9": "hard",
}

# Twin-pairing: (lie coarse-family, split) -> (twin honest category, twin split).
# Twin split follows the contents table, so hard lies whose taxonomy twin is a
# core category (L6->H2-generic, L7->H1-synonym) pair with vocabulary twins in
# core; the rest pair within their own split.
TWIN_MAP = {
    ("L1", "core"): ("H2", "core"),
    ("L2", "core"): ("H1", "core"),
    ("L3", "core"): ("H1", "core"),
    ("L4", "hard"): ("H8", "hard"),
    ("L5", "hard"): ("H6", "hard"),
    ("L6", "hard"): ("H2", "core"),
    ("L7", "hard"): ("H1", "core"),
}


def _family(category):
    m = re.match(r"([LH]\d+)", category)
    return m.group(1) if m else category


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _strings(obj):
    """Recursively yield every string value in a JSON object (keys + values)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            rows.append((lineno, json.loads(line)))
    return rows


# --------------------------------------------------------------------------- #
# Scans                                                                        #
# --------------------------------------------------------------------------- #

def scan_contamination(text, where, viol):
    low = text.lower()
    for needle in _DENY_LITERALS:
        if needle.lower() in low:
            viol.append("%s: deny-list marker present (%d chars)" % (where, len(needle)))
    for name, rx in _DENY_REGEXES:
        if rx.search(text):
            viol.append("%s: deny-list pattern '%s'" % (where, name))


def scan_secrets(text, where, viol):
    for name, rx in _SECRET_REGEXES:
        if rx.search(text):
            viol.append("%s: secret pattern '%s'" % (where, name))
    for tok in _ENTROPY_TOKEN.findall(text):
        if _shannon(tok) >= 4.0:
            viol.append("%s: high-entropy token (len=%d, H=%.2f)" % (where, len(tok), _shannon(tok)))


def validate_item(item, where, viol):
    if item.get("schema_version") != "1.0":
        viol.append("%s: schema_version != '1.0'" % where)
    iid = item.get("id")
    if not isinstance(iid, str) or not iid:
        viol.append("%s: missing/invalid id" % where)
    if not isinstance(item.get("task"), str):
        viol.append("%s: task not a string" % where)
    if not isinstance(item.get("closing"), str):
        viol.append("%s: closing not a string" % where)
    events = item.get("events")
    if not isinstance(events, list):
        viol.append("%s: events not a list" % where)
        return
    call_ids = set()
    result_ids = set()
    for j, ev in enumerate(events):
        eloc = "%s ev[%d]" % (where, j)
        if not isinstance(ev, dict):
            viol.append("%s: event not an object" % eloc)
            continue
        et = ev.get("type")
        if et == "message":
            if not isinstance(ev.get("text"), str):
                viol.append("%s: message.text not a string" % eloc)
        elif et == "tool_call":
            if not isinstance(ev.get("call_id"), str):
                viol.append("%s: tool_call.call_id not a string" % eloc)
            else:
                call_ids.add(ev["call_id"])
            if not isinstance(ev.get("name"), str):
                viol.append("%s: tool_call.name not a string" % eloc)
            if "arguments" not in ev:
                viol.append("%s: tool_call missing arguments" % eloc)
        elif et == "tool_result":
            cid = ev.get("call_id")
            if not isinstance(cid, str):
                viol.append("%s: tool_result.call_id not a string" % eloc)
            else:
                result_ids.add(cid)
            if not isinstance(ev.get("is_error"), bool):
                viol.append("%s: tool_result.is_error not a bool" % eloc)
            if not isinstance(ev.get("truncated"), bool):
                viol.append("%s: tool_result.truncated not a bool" % eloc)
            content = ev.get("content")
            if not isinstance(content, str):
                viol.append("%s: tool_result.content not a string" % eloc)
            elif ev.get("truncated") is True and "[...omitted...]" not in content:
                viol.append("%s: truncated result missing '[...omitted...]' seam" % eloc)
        else:
            viol.append("%s: unknown event type %r" % (eloc, et))
    # a tool_result with no matching tool_call is malformation (dangling CALL is legal)
    for rid in result_ids - call_ids:
        viol.append("%s: tool_result call_id %r has no matching tool_call" % (where, rid))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: leakage_audit.py <corpus_dir>\n")
        return 2
    corpus = argv[0]
    items_path = os.path.join(corpus, "items.jsonl")
    labels_path = os.path.join(corpus, "labels.jsonl")

    viol = []

    if not os.path.exists(items_path):
        sys.stderr.write("FAIL: no items.jsonl in %s\n" % corpus)
        return 1

    items = _read_jsonl(items_path)
    item_ids = []
    for lineno, item in items:
        where = "items:%d" % lineno
        validate_item(item, where, viol)
        if isinstance(item.get("id"), str):
            item_ids.append(item["id"])
        blob = "\n".join(_strings(item))
        scan_contamination(blob, where, viol)
        scan_secrets(blob, where, viol)

    # duplicate id check
    if len(item_ids) != len(set(item_ids)):
        seen, dupes = set(), set()
        for i in item_ids:
            if i in seen:
                dupes.add(i)
            seen.add(i)
        viol.append("items: duplicate ids: %s" % ", ".join(sorted(dupes)[:10]))

    have_labels = os.path.exists(labels_path)
    if have_labels:
        labels = _read_jsonl(labels_path)
        label_by_id = {}
        present = {}          # category -> set(splits)
        family_splits = {}    # (family, split) present among lies
        for lineno, lab in labels:
            where = "labels:%d" % lineno
            blob = "\n".join(_strings(lab))
            scan_contamination(blob, where, viol)
            scan_secrets(blob, where, viol)
            lid = lab.get("id")
            llabel = lab.get("label")
            lcat = lab.get("category")
            lsplit = lab.get("split")
            if llabel not in ("lie", "honest"):
                viol.append("%s: label not lie|honest (%r)" % (where, llabel))
            if lcat not in ALL_CATEGORIES:
                viol.append("%s: unknown category %r" % (where, lcat))
            if lsplit not in VALID_SPLITS:
                viol.append("%s: unknown split %r" % (where, lsplit))
            # label/category consistency
            if lcat in LIE_CATEGORIES and llabel != "lie":
                viol.append("%s: lie-category %s labelled %r" % (where, lcat, llabel))
            if lcat in HONEST_CATEGORIES and llabel != "honest":
                viol.append("%s: honest-category %s labelled %r" % (where, lcat, llabel))
            # split assignment must follow the contents table
            base = _family(lcat) if lcat in HONEST_CATEGORIES else lcat
            canon = CANONICAL_SPLIT.get(lcat) or CANONICAL_SPLIT.get(base)
            if canon and lsplit in VALID_SPLITS and lsplit != canon:
                viol.append("%s: category %s must be in split %s, found %s"
                            % (where, lcat, canon, lsplit))
            label_by_id[lid] = lab
            if lcat in ALL_CATEGORIES and lsplit in VALID_SPLITS:
                present.setdefault(lcat, set()).add(lsplit)
                if lcat in LIE_CATEGORIES:
                    family_splits[(_family(lcat), lsplit)] = True

        # 1:1 pairing
        item_id_set = set(item_ids)
        label_id_set = set(label_by_id)
        for miss in sorted(item_id_set - label_id_set):
            viol.append("integrity: item %s has no label" % miss)
        for miss in sorted(label_id_set - item_id_set):
            viol.append("integrity: label %s has no item" % miss)

        # every taxonomy category populated
        for cat in sorted(ALL_CATEGORIES):
            if cat not in present:
                viol.append("coverage: category %s not populated" % cat)

        # binding twin-pairing satisfied (twin category populated in the split
        # the contents table assigns it to)
        for (fam, split) in sorted(family_splits):
            twin = TWIN_MAP.get((fam, split))
            if twin is None:
                viol.append("twin-pairing: no twin defined for lie family %s in %s" % (fam, split))
            else:
                tcat, tsplit = twin
                if tsplit not in present.get(tcat, set()):
                    viol.append("twin-pairing: lie %s(%s) lacks twin %s in split %s"
                                % (fam, split, tcat, tsplit))
    else:
        sys.stderr.write("note: no labels.jsonl (items-only holdout); "
                         "skipping label/coverage/twin checks\n")

    # ------------------------------------------------------------------- #
    n_items = len(items)
    if viol:
        sys.stderr.write("LEAKAGE AUDIT: FAIL (%d violation(s), %d items)\n"
                         % (len(viol), n_items))
        for v in viol[:200]:
            sys.stderr.write("  - %s\n" % v)
        if len(viol) > 200:
            sys.stderr.write("  ... and %d more\n" % (len(viol) - 200))
        return 1
    sys.stderr.write("LEAKAGE AUDIT: PASS  (%d items, labels=%s)\n"
                     % (n_items, "yes" if have_labels else "no"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
