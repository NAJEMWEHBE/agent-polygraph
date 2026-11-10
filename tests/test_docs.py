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


def _targets(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        text = f.read()
    return [a or b for a, b in _LINK.findall(text)]


class TestReadmeRendersOnPyPI(unittest.TestCase):
    def test_every_readme_link_is_absolute(self):
        relative = [t for t in _targets("README.md")
                    if not t.startswith(("https://", "http://", "#", "mailto:"))]
        self.assertEqual(relative, [])

    def test_repo_links_point_at_this_repo(self):
        for t in _targets("README.md"):
            if t.startswith("https://github.com/"):
                with self.subTest(link=t):
                    self.assertRegex(t, r"^https://github\.com/NAJEMWEHBE/agent-polygraph(/|$)")

    def test_every_in_page_anchor_names_a_heading(self):
        # GitHub's heading slug: lower case, punctuation dropped, spaces to "-".
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            text = re.sub(r"```.*?```", "", f.read(), flags=re.DOTALL)
        slugs = {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
                 for h in re.findall(r"^#{1,6} (.+)$", text, flags=re.MULTILINE)}
        for t in _targets("README.md"):
            if t.startswith("#"):
                with self.subTest(anchor=t):
                    self.assertIn(t[1:], slugs)


class TestMarkdownTables(unittest.TestCase):
    """A bare | inside a code span still splits a GitHub table cell; escape it
    with a backslash."""

    def test_every_table_row_has_the_header_column_count(self):
        cell_split = re.compile(r"(?<!\\)\|")
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and d not in ("node_modules", "build", "dist")]
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    lines = f.read().splitlines()
                header = None
                for n, line in enumerate(lines, 1):
                    if not line.startswith("|"):
                        header = None
                        continue
                    cols = len(cell_split.split(line.strip().strip("|")))
                    if header is None:
                        header = cols
                        continue
                    with self.subTest(file=os.path.relpath(path, ROOT), line=n):
                        self.assertEqual(cols, header)


if __name__ == "__main__":
    unittest.main(verbosity=2)
