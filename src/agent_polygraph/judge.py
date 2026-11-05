# -*- coding: utf-8 -*-
"""
Judge-tier extension point for agent-polygraph (a stable public seam).

The heuristic detector (``detector.py``) is deterministic and cheap, but it
cannot reach the cases that need genuine reasoning over the trajectory -- the
motivating example is the *dangling call + success claim* miss: a ``tool_call``
with no matching ``tool_result`` plus a "Done, deployed successfully" closing is
returned ``honest`` by the heuristic tier, because a result that never came back
cannot be associated with the claim. Catching that class needs a judge.

``Judge`` is the sync protocol a caller implements to plug one in; ``escalate``
decides *which* heuristic verdicts get handed to it. No judge ships yet --
this module is the stable seam a future ``AsyncJudge`` / model-backed judge slots
into without changing the ``verify()`` contract. A judge that overrides a verdict
self-stamps its own ``detector_version`` so co-evolving verdicts stay comparable.

Pure, standard-library only, no I/O, no globals.
"""
from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class Judge(Protocol):
    """A pluggable second-tier verifier.

    Implement ``judge(...)`` to inspect the trajectory the heuristic tier could
    only pattern-match, and return either a replacement ``Verdict`` (which MUST
    self-stamp its own ``detector_version``) or ``None`` to keep the heuristic
    verdict unchanged.
    """

    def judge(self, trajectory: List[Any], final_claim: str, task: str,
              heuristic_verdict: Any) -> Optional[Any]:
        ...


ESCALATE_POLICIES = ("honest_only", "degraded_only", "all")


def should_escalate(escalate: str, verdict: Any) -> bool:
    """Escalation policy: does this heuristic ``verdict`` get handed to the judge?

    * ``"honest_only"`` (default) -- escalate only heuristic *honest* verdicts
      (the judge exists to catch what the heuristic missed; blocks it already
      caught are trusted).
    * ``"degraded_only"`` -- escalate any verdict that leaned on a degraded
      signal (a non-empty ``flags`` list).
    * ``"all"`` -- escalate every verdict.
    """
    if escalate not in ESCALATE_POLICIES:
        raise ValueError(f"escalate must be one of {ESCALATE_POLICIES}, got {escalate!r}")
    if escalate == "all":
        return True
    if escalate == "degraded_only":
        return bool(getattr(verdict, "flags", ()))
    # default: honest_only
    return getattr(verdict, "verdict", None) == "honest"
