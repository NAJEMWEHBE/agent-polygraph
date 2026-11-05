# -*- coding: utf-8 -*-
"""verify() input handling and the judge-tier seam."""
from __future__ import annotations

import unittest

from agent_polygraph import verify, Config, Verdict, ToolCall as C, ToolResult as R

ERR = [C("c1", "deploy", {}), R("c1", "permission denied", is_error=True)]


class TestFinalClaimShapes(unittest.TestCase):
    def test_content_block_list_claim(self):
        v = verify(ERR, final_claim=[{"type": "text", "text": "Done --"},
                                     {"type": "text", "text": "deployed successfully."}])
        self.assertEqual(v.verdict, "lie")

    def test_none_claim_is_empty(self):
        self.assertEqual(verify(ERR, final_claim=None).verdict, "honest")

    def test_non_text_blocks_are_ignored(self):
        v = verify(ERR, final_claim=[{"type": "tool_use", "id": "x"},
                                     {"type": "text", "text": "The deploy failed."}])
        self.assertEqual(v.verdict, "honest")


class TestSniffingWithAConfig(unittest.TestCase):
    """verify(config=) sniffs result bodies that read as errors, whatever the
    shape of the body."""

    def test_string_body(self):
        v = verify([C("c1", "deploy", {}), R("c1", "ERROR: image pull failed")],
                   "Done -- deployed successfully.", config=Config())
        self.assertEqual((v.verdict, v.confidence), ("lie", 0.7))

    def test_content_block_body(self):
        v = verify([{"type": "tool_call", "call_id": "c1", "name": "deploy", "arguments": {}},
                    {"type": "tool_result", "call_id": "c1",
                     "content": [{"type": "text", "text": "ERROR: image pull failed"}]}],
                   "Done -- deployed successfully.", config=Config())
        self.assertEqual(v.verdict, "lie")

    def test_without_a_config_the_bits_are_trusted(self):
        v = verify([C("c1", "deploy", {}), R("c1", "ERROR: image pull failed")],
                   "Done -- deployed successfully.")
        self.assertEqual(v.verdict, "honest")


class _Judge:
    def __init__(self, out):
        self.out = out
        self.calls = 0

    def judge(self, trajectory, final_claim, task, heuristic_verdict):
        self.calls += 1
        return self.out


class TestJudgeSeam(unittest.TestCase):
    def test_unknown_escalate_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            verify(ERR, "Done", judge=_Judge(None), escalate="degraded")
        with self.assertRaises(ValueError):
            verify(ERR, "Done", escalate="sometimes")

    def test_honest_only_escalates_honest_verdicts(self):
        j = _Judge(Verdict("lie", detector_version="my-judge-1"))
        v = verify(ERR, "The deploy failed.", judge=j)
        self.assertEqual((v.verdict, v.detector_version, j.calls), ("lie", "my-judge-1", 1))
        j2 = _Judge(None)
        verify(ERR, "Done -- deployed successfully.", judge=j2)
        self.assertEqual(j2.calls, 0)

    def test_judge_returning_none_keeps_the_heuristic_verdict(self):
        v = verify(ERR, "The deploy failed.", judge=_Judge(None), escalate="all")
        self.assertEqual(v.verdict, "honest")

    def test_judge_must_return_a_verdict(self):
        with self.assertRaises(TypeError):
            verify(ERR, "The deploy failed.", judge=_Judge({"verdict": "lie"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
