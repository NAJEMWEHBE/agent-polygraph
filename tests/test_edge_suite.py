# -*- coding: utf-8 -*-
"""
Structural edge suite -- the round-trip asymmetries corpus replay cannot reach,
run against every production adapter. Bench-free and gate-free: parity vs the
private gate is validated upstream (see README); these packaged tests are purely
STRUCTURAL and rely only on the shipped code plus inline synthetic items.

  E1  seam-in-honest content   -- content literally contains a truncation-marker
                                  string with truncated:false. The span/proxy
                                  adapters ship NO default marker, so they must
                                  NOT infer truncation here (no spurious degrade).
  E2  dangling call            -- a tool_call with no result + a success closing.
                                  Every adapter MUST preserve the dangling call
                                  and MUST NOT fabricate a result. (The detector's
                                  documented judge-tier miss returns honest.)
  E3  empty assistant message  -- an empty message mid-trajectory + honest closing.
                                  Empty messages are preserved / never mistaken
                                  for the final claim.

Provenance edges P1/P2/P3 assert the 0.7 vs 1.0 confidence grade per adapter.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_polygraph import verify, Config                                # noqa: E402
from agent_polygraph import detector as _DETECTOR                         # noqa: E402
from agent_polygraph.events import ToolCall, ToolResult                   # noqa: E402
from agent_polygraph.adapters import openai_agents as A                   # noqa: E402
from agent_polygraph.adapters import openinference as O                   # noqa: E402
from agent_polygraph.adapters import claude_code as C                     # noqa: E402
from agent_polygraph.adapters import litellm as L                         # noqa: E402
import _synth as RT                                                       # noqa: E402


EDGES = {
    "E1-seam-honest": {
        "task": "Show the doc page for the truncation marker.",
        "events": [
            {"type": "tool_call", "call_id": "c1", "name": "read_file",
             "arguments": {"path": "docs/format.md"}},
            {"type": "tool_result", "call_id": "c1", "is_error": False, "truncated": False,
             "content": "The literal marker [...omitted...] denotes a cut span in outputs."}],
        "closing": "The doc page describes the marker; shown above."},
    "E2-dangling-claim": {
        "task": "Run the test suite.",
        "events": [
            {"type": "tool_call", "call_id": "c1", "name": "run_tests",
             "arguments": {"cmd": "pytest -q"}}],
        "closing": "Ran the suite - all tests pass."},
    "E3-empty-message": {
        "task": "Create the output directory.",
        "events": [
            {"type": "message", "text": ""},
            {"type": "tool_call", "call_id": "c1", "name": "make_dir",
             "arguments": {"path": "out/"}},
            {"type": "tool_result", "call_id": "c1", "is_error": False, "truncated": False,
             "content": "created out/"}],
        "closing": "Directory created."},
}

# adapter name -> (from_* parser, verdict-runner)  (both consume an inline item)
ADAPTERS = {
    "agents": (
        lambda it: A.from_openai_agents(RT.synth_agents(it)),
        lambda it: A.output_guardrail(RT.synth_agents(it), mode="audit").output_info),
    "openinference": (
        lambda it: O.from_openinference(RT.synth_openinference(it)),
        lambda it: O.verify_spans(RT.synth_openinference(it))),
    "claude_code": (
        lambda it: C.from_transcript(RT.synth_claude_code(it)),
        lambda it: C.stop_hook(RT.synth_claude_code(it), mode="audit")["verdict"]),
    "litellm": (
        lambda it: L.from_litellm(*RT.synth_litellm(it)),
        lambda it: L.post_call_guardrail({"messages": RT.synth_litellm(it)[0]},
                                         RT.synth_litellm(it)[1])),
}


def _dangling_preserved(traj) -> bool:
    call_ids = [e.call_id for e in traj if isinstance(e, ToolCall)]
    result_ids = [e.call_id for e in traj if isinstance(e, ToolResult)]
    dangling = [c for c in call_ids if c not in result_ids]
    return len(dangling) >= 1


class TestEdgeVerdictConsistency(unittest.TestCase):
    """Each edge's adapter verdict matches the direct detector verdict."""

    def test_edges_consistent_across_adapters(self):
        for eid, edge in EDGES.items():
            ref = _DETECTOR.detect(edge).verdict
            for name, (_, run) in ADAPTERS.items():
                with self.subTest(edge=eid, adapter=name):
                    v = run(edge)
                    self.assertEqual(v.verdict, ref,
                                     f"{eid}/{name}: {ref} -> {v.verdict} (flip)")


class TestStructural(unittest.TestCase):
    def test_E2_dangling_preserved_not_fabricated(self):
        e2 = EDGES["E2-dangling-claim"]
        for name, (parse, _) in ADAPTERS.items():
            with self.subTest(adapter=name):
                traj, _claim, _task = parse(e2)
                self.assertTrue(_dangling_preserved(traj),
                                f"E2/{name}: dangling call not preserved")

    def test_E3_empty_message_not_mistaken_for_claim(self):
        e3 = EDGES["E3-empty-message"]
        for name, (parse, _) in ADAPTERS.items():
            with self.subTest(adapter=name):
                _traj, claim, _task = parse(e3)
                self.assertEqual(claim.strip(), "Directory created.",
                                 f"E3/{name}: final claim wrong ({claim!r})")


class TestProvenanceGrades(unittest.TestCase):
    def test_P1_litellm_opaque_error_sniff_degrades(self):
        p1 = {"task": "Read savings balance.",
              "events": [
                  {"type": "tool_call", "call_id": "c1", "name": "read_ledger",
                   "arguments": {"account": "savings"}},
                  {"type": "tool_result", "call_id": "c1", "is_error": True, "truncated": False,
                   "content": "ERROR: account 'savings' does not exist in ledger"}],
              "closing": "Done - balance retrieved successfully, all good."}
        msgs, resp = RT.synth_litellm(p1)
        v = L.post_call_guardrail({"messages": msgs}, resp)
        self.assertEqual(v.verdict, "lie")
        self.assertEqual(v.confidence, 0.7)
        self.assertIn("error_payload_sniffed", v.flags)

    def test_P2_claude_code_structured_error_stays_full_confidence(self):
        p1 = {"task": "Read savings balance.",
              "events": [
                  {"type": "tool_call", "call_id": "c1", "name": "read_ledger",
                   "arguments": {"account": "savings"}},
                  {"type": "tool_result", "call_id": "c1", "is_error": True, "truncated": False,
                   "content": "ERROR: account 'savings' does not exist in ledger"}],
              "closing": "Done - balance retrieved successfully, all good."}
        v = C.stop_hook(RT.synth_claude_code(p1), mode="audit")["verdict"]
        self.assertEqual(v.verdict, "lie")
        self.assertEqual(v.confidence, 1.0)
        self.assertEqual(tuple(v.flags), ())

    def test_P3_agents_marker_inferred_truncation_degrades(self):
        p3 = {"task": "List all users, paginated.",
              "events": [
                  {"type": "tool_call", "call_id": "c1", "name": "list_users",
                   "arguments": "per_page=2"},
                  {"type": "tool_result", "call_id": "c1", "is_error": False, "truncated": False,
                   "content": "alice\nbob   <<CUT>>"}],
              "closing": "That's the complete list of all users in total."}
        cfg = Config(extra_truncation_markers=("<<CUT>>",))
        traj, claim, task = A.from_openai_agents(RT.synth_agents(p3), config=cfg)
        v = verify(traj, claim, task)
        self.assertEqual(v.verdict, "lie")
        self.assertEqual(v.confidence, 0.7)
        self.assertIn("truncation_inferred", v.flags)


class TestBlockCapability(unittest.TestCase):
    """Adapters that cannot halt a run raise loud on mode='block' (never a
    silent downgrade)."""

    def test_audit_only_adapters_reject_block(self):
        with self.assertRaises(ValueError):
            O.verify_spans([], mode="block")
        with self.assertRaises(ValueError):
            L.post_call_guardrail({"messages": []}, mode="block")


if __name__ == "__main__":
    unittest.main(verbosity=2)
