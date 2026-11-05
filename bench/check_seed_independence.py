#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_seed_independence — pre-registered seed-collision guard for multi-seed runs.

Why this exists
---------------
The generator picks each category's sub-corpus by rotation block:

    block = (seed*2 + split_bit) % num_blocks        (num_blocks varies per category)

The block index is derived from the same slot for every category, so two seeds that
collide modulo one category's num_blocks emit byte-identical sub-corpora for that
category. In a median-of-N protocol (scoring protocol v1.1) that silently violates the
independence assumption between "fresh" seeds — found in practice when two of three
gate-run seeds shared the H1-recov block (34 blocks, 44 items).

The rule (pre-registered, documented in GENERATION.md)
------------------------------------------------------
Before ANY detector runs on a multi-seed set, run this check on the candidate seeds.
If any two seeds collide on any category block, the LATER-DRAWN seed is discarded and
redrawn until the set is collision-free. Because the check runs before any verdicts
exist, a redraw can't select for favorable results — it is seed hygiene, not p-hacking.

Usage
-----
    python check_seed_independence.py --split hidden SEED [SEED ...]
                                      [--public-seed N]

- Seeds are checked pairwise within --split (default: hidden).
- --public-seed additionally cross-checks each candidate against the public corpus
  seed (split_bit 0) — public/hidden slot parity keeps even-num_blocks categories
  disjoint by construction, but odd-num_blocks categories can overlap.

Exit 0 = all pairs independent on every category; exit 1 = at least one collision
(each printed with category, colliding pair, shared block, and items affected).
Stdlib only; deterministic; performs no generation and no I/O beyond stdout.
"""
import argparse
import sys

import generate_corpus as gc


def block_table(seed, split_bit):
    """category-key -> (block, num_blocks, n_items) for one seed."""
    out = {}
    for cat, tax_split, variant, n in gc.TARGETS:
        _, space = gc._canonical_combos(cat, tax_split, variant)
        num_blocks = len(space) // n
        block = (seed * 2 + split_bit) % num_blocks
        key = cat + ("-" + variant if variant else "")
        out[key] = (block, num_blocks, n)
    return out


def find_collisions(seeds, split_bit, public_seed=None):
    tables = {s: block_table(s, split_bit) for s in seeds}
    collisions = []
    for i, a in enumerate(seeds):
        for b in seeds[i + 1:]:
            for key, (blk_a, nb, n) in tables[a].items():
                blk_b = tables[b][key][0]
                if blk_a == blk_b:
                    collisions.append((key, a, b, blk_a, nb, n))
    if public_seed is not None:
        pub = block_table(public_seed, 0)
        for s in seeds:
            for key, (blk, nb, n) in tables[s].items():
                if pub[key][0] == blk:
                    collisions.append((key, public_seed, s, blk, nb, n))
    return collisions


def main(argv=None):
    ap = argparse.ArgumentParser(description="pairwise seed-collision guard")
    ap.add_argument("seeds", nargs="+", type=int, help="candidate seeds, in draw order")
    ap.add_argument("--split", choices=["public", "hidden"], default="hidden")
    ap.add_argument("--public-seed", type=int, default=None,
                    help="also cross-check candidates against this public-corpus seed")
    args = ap.parse_args(argv)

    split_bit = 0 if args.split == "public" else 1
    collisions = find_collisions(args.seeds, split_bit, args.public_seed)

    n_cats = len(gc.TARGETS)
    print("seed-independence check: %d seed(s), %d category blocks, split=%s%s"
          % (len(args.seeds), n_cats, args.split,
             (", cross-checked vs public seed %d" % args.public_seed)
             if args.public_seed is not None else ""))
    if not collisions:
        print("PASS -- all pairs draw distinct blocks in every category.")
        return 0
    print("FAIL -- %d collision(s); redraw the later-drawn seed of each pair:" % len(collisions))
    for key, a, b, blk, nb, n in collisions:
        print("  %-12s seeds %d & %d share block %d/%d (%d identical items)"
              % (key, a, b, blk, nb, n))
    return 1


if __name__ == "__main__":
    sys.exit(main())
