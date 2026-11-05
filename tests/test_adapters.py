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


def _oi_spans(status):
    tool = {"attributes": {"openinference.span.kind": "TOOL",
                           "tool_call.function.name": "deploy", "input.value": "{}"}}
    tool.update(status)
    llm = {"attributes": {"openinference.span.kind": "LLM",
                          "llm.output_messages.0.message.role": "assistant",
                          "llm.output_messages.0.message.content": CLAIM}}
    return [tool, llm]


class TestOpenInferenceStatus(unittest.TestCase):
    """Every serialised form of the OTel ERROR status marks the tool as failed."""

    def test_error_status_shapes(self):
        for status in ({"status_code": "ERROR"},
                       {"status": {"status_code": "ERROR"}},
                       {"status": {"code": 2}},
                       {"status": {"code": "2"}},
                       {"status": {"code": "STATUS_CODE_ERROR"}},
                       {"status": {"code": "StatusCode.ERROR"}},
                       {"status": "error"},
                       {"status_code": 2}):
            with self.subTest(status=status):
                v = OI.verify_spans(_oi_spans(status))
                self.assertEqual(v.verdict, "lie")
                self.assertEqual(v.confidence, 1.0)

    def test_ok_and_unset_status_shapes(self):
        for status in ({"status": {"code": 1}}, {"status": {"code": 0}},
                       {"status": {"code": "STATUS_CODE_OK"}},
                       {"status_code": "UNSET"}, {"status": {}}):
            with self.subTest(status=status):
                spans = _oi_spans(status)
                spans[0]["attributes"]["output.value"] = "rolled out"
                self.assertEqual(OI.verify_spans(spans).verdict, "honest")


class _FakeSpan:
    def __init__(self, trace_id, exported):
        self.trace_id = trace_id
        self._exported = exported

    def export(self):
        return dict(self._exported, trace_id=self.trace_id)


def _fake_agents_module(current_trace_id):
    """Stand-in for the parts of the `agents` package the wiring touches."""
    import types
    mod = types.ModuleType("agents")

    class GuardrailFunctionOutput:
        def __init__(self, output_info, tripwire_triggered):
            self.output_info = output_info
            self.tripwire_triggered = tripwire_triggered

    def output_guardrail(func=None, *, name=None):
        def wrap(f):
            f.guardrail_name = name
            return f
        return wrap(func) if func is not None else wrap

    mod.GuardrailFunctionOutput = GuardrailFunctionOutput
    mod.output_guardrail = output_guardrail
    mod.get_current_trace = lambda: types.SimpleNamespace(trace_id=current_trace_id)
    return mod


class TestAgentsSdkWiring(unittest.TestCase):
    """PolygraphTracingProcessor + make_output_guardrail: collect each trace's
    spans and judge the agent's final output against them."""

    LIE = {"task": "deploy", "events": [
        {"type": "tool_call", "call_id": "c1", "name": "deploy", "arguments": {}},
        {"type": "tool_result", "call_id": "c1", "is_error": True, "truncated": False,
         "content": "permission denied"}], "closing": ""}

    def _processor_with(self, item, trace_id):
        proc = OA.PolygraphTracingProcessor()
        for sp in RT.synth_agents(item):
            proc.on_span_start(_FakeSpan(trace_id, sp))
            proc.on_span_end(_FakeSpan(trace_id, sp))
        return proc

    def _guardrail(self, proc, trace_id, **kw):
        fake = _fake_agents_module(trace_id)
        saved = sys.modules.get("agents")
        sys.modules["agents"] = fake
        try:
            return OA.make_output_guardrail(proc, **kw)
        finally:
            if saved is None:
                sys.modules.pop("agents", None)
            else:
                sys.modules["agents"] = saved

    def test_block_mode_trips_on_a_lie_in_the_current_trace(self):
        proc = self._processor_with(self.LIE, "trace_a")
        g = self._guardrail(proc, "trace_a", mode="block")
        out = g(None, None, "Done -- deployed successfully.")
        self.assertTrue(out.tripwire_triggered)
        self.assertEqual(out.output_info["verdict"], "lie")
        self.assertEqual(g.guardrail_name, "agent_polygraph")

    def test_audit_mode_reports_but_never_trips(self):
        proc = self._processor_with(self.LIE, "trace_a")
        out = self._guardrail(proc, "trace_a")(None, None, "Done -- deployed successfully.")
        self.assertFalse(out.tripwire_triggered)
        self.assertEqual(out.output_info["verdict"], "lie")

    def test_spans_of_another_trace_are_not_used(self):
        proc = self._processor_with(self.LIE, "trace_a")
        out = self._guardrail(proc, "trace_b", mode="block")(None, None, "Done -- deployed.")
        self.assertFalse(out.tripwire_triggered)

    def test_trace_end_frees_its_spans(self):
        import types
        proc = self._processor_with(self.LIE, "trace_a")
        self.assertTrue(proc.spans("trace_a"))
        proc.on_trace_end(types.SimpleNamespace(trace_id="trace_a"))
        self.assertEqual(proc.spans("trace_a"), [])

    def test_processor_keeps_a_bounded_number_of_traces(self):
        proc = OA.PolygraphTracingProcessor(max_traces=2)
        for t in ("t1", "t2", "t3"):
            proc.on_span_end(_FakeSpan(t, {"span_data": {"type": "custom"}}))
        self.assertEqual((proc.spans("t1"), len(proc.spans("t3"))), ([], 1))

    def test_missing_sdk_raises_a_clear_error(self):
        saved = sys.modules.get("agents")
        sys.modules["agents"] = None          # makes `import agents` fail
        try:
            with self.assertRaises(ImportError) as cm:
                OA.make_output_guardrail(OA.PolygraphTracingProcessor())
            self.assertIn("openai-agents", str(cm.exception))
        finally:
            if saved is None:
                sys.modules.pop("agents", None)
            else:
                sys.modules["agents"] = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
