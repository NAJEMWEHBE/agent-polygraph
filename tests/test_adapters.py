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


LITELLM_DATA = {"messages": [
    {"role": "user", "content": "deploy web"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "x1", "type": "function",
         "function": {"name": "deploy", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "x1", "content": "ERROR: permission denied"}]}
CLAIM = "Done -- deployed successfully."


class _Obj:
    """Attribute bag standing in for litellm's pydantic response types."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestLiteLLMResponseShapes(unittest.TestCase):
    """The claim is read from every response shape LiteLLM hands a hook."""

    def test_response_shapes(self):
        shapes = {
            "dict": {"choices": [{"message": {"content": CLAIM}}]},
            "ModelResponse-like object": _Obj(choices=[_Obj(message=_Obj(content=CLAIM))]),
            "object with dict message": _Obj(choices=[_Obj(message={"content": CLAIM})]),
            "model_dump only": _Obj(model_dump=lambda: {"choices": [{"message": {"content": CLAIM}}]}),
            "content blocks": {"choices": [{"message": {"content": [
                {"type": "text", "text": CLAIM}]}}]},
            "message object": _Obj(role="assistant", content=CLAIM),
        }
        for name, resp in shapes.items():
            with self.subTest(shape=name):
                _traj, claim, _task = LL.from_litellm(LITELLM_DATA["messages"], resp)
                self.assertEqual(claim, CLAIM)
                self.assertEqual(LL.post_call_guardrail(LITELLM_DATA, resp).verdict, "lie")

    def test_tool_call_objects_are_read(self):
        fn = _Obj(name="deploy", arguments="{}")
        msgs = [{"role": "user", "content": "deploy web"},
                {"role": "assistant", "content": None,
                 "tool_calls": [_Obj(id="x1", type="function", function=fn)]},
                {"role": "tool", "tool_call_id": "x1", "content": "ERROR: permission denied"}]
        traj, _claim, _task = LL.from_litellm(msgs, CLAIM)
        self.assertEqual([type(e).__name__ for e in traj], ["ToolCall", "ToolResult"])
        self.assertEqual(traj[0].name, "deploy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
