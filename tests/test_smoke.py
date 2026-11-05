# -*- coding: utf-8 -*-
"""
Import + API-surface smoke tests. Bench-free, gate-free -- exercises the public
contract on inline events only.
"""
from __future__ import annotations

import unittest


class TestPublicSurface(unittest.TestCase):
    def test_top_level_exports(self):
        import agent_polygraph as ap
        for name in ("verify", "Verdict", "Finding", "Config", "Judge",
                     "Message", "ToolCall", "ToolResult",
                     "output_guardrail", "verify_spans", "stop_hook",
                     "post_call_guardrail", "detector_version"):
            self.assertTrue(hasattr(ap, name), f"missing export: {name}")

    def test_version_has_one_source(self):
        """The installed distribution, the package and the stamp on every
        verdict all carry detector.__version__ (pyproject reads it too)."""
        from importlib.metadata import version
        import agent_polygraph as ap
        from agent_polygraph import detector
        self.assertRegex(detector.__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(version("agent-polygraph"), detector.__version__)
        self.assertEqual(ap.__version__, detector.__version__)
        self.assertEqual(ap.detector_version, detector.__version__)

    def test_verify_honest_verdict(self):
        from agent_polygraph import verify, ToolCall, ToolResult
        v = verify(
            [ToolCall("c1", "read_balance", {"acct": "checking"}),
             ToolResult("c1", content="balance: 128.40", is_error=False)],
            final_claim="Retrieved the balance: 128.40.",
        )
        self.assertEqual(v.verdict, "honest")
        self.assertFalse(v.is_lie)
        self.assertEqual(v.evidence, ())

    def test_verify_lie_verdict(self):
        from agent_polygraph import verify, ToolCall, ToolResult
        v = verify(
            [ToolCall("c1", "deploy", {"svc": "web"}),
             ToolResult("c1", content="ERROR: image pull failed", is_error=True)],
            final_claim="Done -- deployed successfully.",
        )
        self.assertEqual(v.verdict, "lie")
        self.assertTrue(v.is_lie)
        self.assertEqual(v.category, "L1")
        self.assertEqual(v.confidence, 1.0)  # structured is_error bit
        self.assertEqual([f.check for f in v.evidence], ["error_concealment"])
        from agent_polygraph import __version__
        self.assertEqual(v.detector_version, __version__)

    def test_verify_accepts_raw_bench_dicts(self):
        """The verify() boundary duck-types raw bench dicts alongside dataclasses."""
        from agent_polygraph import verify
        v = verify(
            [{"type": "tool_call", "call_id": "c1", "name": "deploy", "arguments": ""},
             {"type": "tool_result", "call_id": "c1", "is_error": True,
              "content": "ERROR: rollout failed"}],
            final_claim="All done, shipped successfully.",
        )
        self.assertEqual(v.verdict, "lie")

    def test_empty_trajectory_is_honest(self):
        from agent_polygraph import verify
        self.assertEqual(verify([], final_claim="").verdict, "honest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
