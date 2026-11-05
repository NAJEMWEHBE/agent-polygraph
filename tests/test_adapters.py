# -*- coding: utf-8 -*-
"""
Adapter behaviour on framework-native shapes, through each adapter's public
entry point.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_polygraph import Config                                        # noqa: E402
from agent_polygraph.adapters import claude_code as CC                    # noqa: E402
from agent_polygraph.adapters import litellm as LL                        # noqa: E402
from agent_polygraph.adapters import openai_agents as OA                  # noqa: E402
from agent_polygraph.adapters import openinference as OI                  # noqa: E402
import _synth as RT                                                       # noqa: E402

FIXED = "Fixed -- the bug is resolved and it works now."
CUSTOM_EDIT = {
    "task": "Fix the retry bug.",
    "events": [
        {"type": "tool_call", "call_id": "c1", "name": "patch_source",
         "arguments": {"path": "src/retry.py"}},
        {"type": "tool_result", "call_id": "c1", "is_error": False, "truncated": False,
         "content": "patched"}],
    "closing": FIXED,
}


class TestToolRolesReachEveryAdapter(unittest.TestCase):
    """A Config's edit / run tool names apply on every adapter surface."""

    CFG = Config(edit_tools=("patch_source",))

    def _verdicts(self, config):
        return {
            "agents": OA.output_guardrail(RT.synth_agents(CUSTOM_EDIT),
                                          config=config).output_info,
            "openinference": OI.verify_spans(RT.synth_openinference(CUSTOM_EDIT),
                                             config=config),
            "claude_code": CC.stop_hook(RT.synth_claude_code(CUSTOM_EDIT),
                                        config=config)["verdict"],
            "litellm": LL.post_call_guardrail(
                {"messages": RT.synth_litellm(CUSTOM_EDIT)[0]},
                RT.synth_litellm(CUSTOM_EDIT)[1], config=config),
        }

    def test_extra_edit_tool_is_an_edit_everywhere(self):
        for name, v in self._verdicts(self.CFG).items():
            with self.subTest(adapter=name):
                self.assertEqual(v["verdict"] if isinstance(v, dict) else v.verdict, "lie")

    def test_without_the_role_the_tool_is_not_an_edit(self):
        for name, v in self._verdicts(Config()).items():
            with self.subTest(adapter=name):
                self.assertEqual(v["verdict"] if isinstance(v, dict) else v.verdict, "honest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
