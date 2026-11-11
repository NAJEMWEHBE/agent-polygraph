# -*- coding: utf-8 -*-
"""
Docs that ship outside GitHub must still work there: README.md is the PyPI
project page, where a relative link has nothing to resolve against.
"""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LINK = re.compile(r"\]\(([^)\s]+)[^)]*\)|(?:src|href)=\"([^\"]+)\"")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_DELIMITER_ROW = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _rendered(text):
    """The Markdown with its fenced code examples left out: nothing in them is
    rendered as a link or a heading."""
    return _FENCE.sub("", text)


def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def _targets(text):
    return [a or b for a, b in _LINK.findall(_rendered(text))]


def _slug(heading):
    """GitHub's anchor for a heading: made from the rendered text (emphasis
    marks gone), lower case, punctuation dropped, spaces to "-". An underscore
    inside a word stays."""
    text = re.sub(r"(?<![^\W_])_+|_+(?![^\W_])", "", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _cells(line):
    return len(_CELL_SPLIT.split(line.strip().strip("|")))


def _table_faults(lines):
    """(line number, cells, header cells) for every table row whose cell count
    differs from its header's. A table is a row followed by a delimiter row
    (GitHub does not need the leading pipe) and runs until a line with no pipe.
    Nothing inside a fenced code example is a table."""
    faults = []
    header = None
    fenced = False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            header = None
            continue
        if fenced:
            continue
        if header is None:
            above = lines[n - 2] if n >= 2 else ""
            if "|" in line and "|" in above and _DELIMITER_ROW.match(line):
                header = _cells(above)
                if _cells(line) != header:
                    faults.append((n, _cells(line), header))
            continue
        if "|" not in line:
            header = None
            continue
        if _cells(line) != header:
            faults.append((n, _cells(line), header))
    return faults


class TestReadmeRendersOnPyPI(unittest.TestCase):
    def test_every_readme_link_is_absolute(self):
        relative = [t for t in _targets(_read("README.md"))
                    if not t.startswith(("https://", "http://", "#", "mailto:"))]
        self.assertEqual(relative, [])

    def test_repo_links_point_at_this_repo(self):
        for t in _targets(_read("README.md")):
            if t.startswith("https://github.com/"):
                with self.subTest(link=t):
                    self.assertRegex(t, r"^https://github\.com/NAJEMWEHBE/agent-polygraph(/|$)")

    def test_every_in_page_anchor_names_a_heading(self):
        text = _read("README.md")
        slugs = {_slug(h) for h in re.findall(r"^#{1,6} (.+)$", _rendered(text), flags=re.MULTILINE)}
        for t in _targets(text):
            if t.startswith("#"):
                with self.subTest(anchor=t):
                    self.assertIn(t[1:], slugs)


class TestMarkdownTables(unittest.TestCase):
    """A bare | inside a code span still splits a GitHub table cell; escape it
    with a backslash."""

    def test_every_table_row_has_the_header_column_count(self):
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and d not in ("node_modules", "build", "dist")]
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    lines = f.read().splitlines()
                for n, cols, header in _table_faults(lines):
                    with self.subTest(file=os.path.relpath(path, ROOT), line=n):
                        self.assertEqual(cols, header)


class TestTheDocChecksThemselves(unittest.TestCase):
    def test_slug_is_made_from_the_rendered_heading(self):
        self.assertEqual(_slug("_Overview_"), "overview")
        self.assertEqual(_slug("The `error` bit: what it means"), "the-error-bit-what-it-means")
        self.assertEqual(_slug("Using snake_case names"), "using-snake_case-names")

    def test_a_link_inside_a_fenced_example_is_not_a_link(self):
        text = "[real](#here)\n```md\n[example](#sample)\n```\n"
        self.assertEqual(_targets(text), ["#here"])

    def test_a_table_without_leading_pipes_is_checked(self):
        lines = ["a | b", "--- | ---", "1 | 2", "1 | `x|y` | 3", "", "not | a table"]
        self.assertEqual(_table_faults(lines), [(4, 4, 2)])

    def test_a_table_with_leading_pipes_is_checked(self):
        lines = ["| a | b |", "|---|---|", "| 1 | 2 |", r"| `x\|y` | 3 |", "| 1 | 2 | 3 |"]
        self.assertEqual(_table_faults(lines), [(5, 3, 2)])

    def test_a_table_inside_a_fenced_example_is_not_checked(self):
        lines = ["```md", "| a | b |", "|---|---|", "| 1 | 2 | 3 |", "```",
                 "| a | b |", "|---|---|", "| 1 | 2 | 3 |"]
        self.assertEqual(_table_faults(lines), [(8, 3, 2)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
