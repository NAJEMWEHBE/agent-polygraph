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


if __name__ == "__main__":
    unittest.main(verbosity=2)
