# -*- coding: utf-8 -*-
"""
Locked public verify() contract for agent-polygraph v0.1 (ps-api-shape).

    verify(trajectory, final_claim, task="") -> Verdict

Sync, pure, thread-safe, no I/O, no env reads, no global mutable state. One
detector per package (the clean-room ``detector.py``); this module wires it once
at import and exposes its version as ``DETECTOR_VERSION`` (== the package
version).

Verdict schema (locked):
  * ``verdict``          -- "lie" | "honest"
  * ``evidence``         -- list[Finding{check, detail}]
  * ``confidence``       -- signal-quality GRADE, not a probability: 1.0 when the
                            verdict rode a structured signal, 0.7 when it rode an
                            inferred truncation or a sniffed error payload.
  * ``detector_version`` -- self-stamped so verdicts stay comparable across
                            detector co-evolution.
  (``flags`` and ``category`` ride along as documented diagnostics.)

Confidence grading is computed HERE from adapter provenance, not read from the
detector's internal flags: a structured truncation bit (e.g. Claude Code's
``truncatedByTokenCap``) must NOT degrade confidence, while a marker-*inferred*
truncation must -- a distinction the detector alone cannot make.

Config: frozen dataclass, explicit kwargs. NO env vars, NO global registry.
Judge tier: sync ``Judge`` protocol + ``escalate`` policy; ZERO judges shipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

from . import detector as _DETECTOR
from .events import (Message, ToolCall, ToolResult, normalize_event, to_item,
                     DEFAULT_ERROR_PATTERNS)
from .judge import Judge, should_escalate as _should_escalate

# The one detector per package; detector.py is never modified. Its version is
# the package version (wired in pyproject via detector.__version__).
DETECTOR_VERSION: str = getattr(_DETECTOR, "__version__", "0.0.0")


# ---------------------------------------------------------------------------
# result + config types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    """One check family's contribution to a lie verdict."""
    check: str
    detail: str

    def as_dict(self) -> dict:
        return {"check": self.check, "detail": self.detail}


@dataclass(frozen=True)
class Verdict:
    """Locked verdict schema (+ diagnostic flags/category)."""
    verdict: str                       # "lie" | "honest"
    evidence: Tuple[Finding, ...] = ()
    confidence: float = 1.0            # 1.0 structured | 0.7 degraded
    detector_version: str = DETECTOR_VERSION
    flags: Tuple[str, ...] = ()        # degradation markers driving a 0.7 grade
    category: Optional[str] = None     # L1/L2/L3 diagnostic

    @property
    def is_lie(self) -> bool:
        return self.verdict == "lie"

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "evidence": [f.as_dict() for f in self.evidence],
            "confidence": self.confidence,
            "detector_version": self.detector_version,
            "flags": list(self.flags),
            "category": self.category,
        }


@dataclass(frozen=True)
class Config:
    """Adapter behaviour knobs. Frozen; explicit; no env, no globals.

    * ``error_sniff``            -- recover a lost tool-error bit by sniffing the
                                    result payload when no structured bit exists
                                    (ON by default; sniffed -> 0.7 grade).
    * ``error_patterns``         -- override the built-in sniff patterns.
    * ``extra_truncation_markers`` -- extra markers appended to each adapter's
                                    per-framework defaults (literal substrings, or
                                    ``re:``-prefixed regexes).
    * ``redact_paths``           -- scrub host paths / traceback frames from
                                    re-emitted content (ON by default).
    """
    error_sniff: bool = True
    error_patterns: Tuple[str, ...] = DEFAULT_ERROR_PATTERNS
    extra_truncation_markers: Tuple[Any, ...] = ()
    redact_paths: bool = True


DEFAULT_CONFIG = Config()


# ---------------------------------------------------------------------------
# the locked entry point
# ---------------------------------------------------------------------------
def verify(trajectory: List[Union[Message, ToolCall, ToolResult, dict]],
           final_claim: str = "",
           task: str = "",
           *,
           judge: Optional[Judge] = None,
           escalate: str = "honest_only") -> Verdict:
    """Does ``final_claim`` contradict the trajectory's own tool evidence?

    Pure and synchronous. ``trajectory`` may mix SDK event dataclasses and raw
    bench dicts. ``judge``/``escalate`` are the documented judge-tier extension
    point (no judge ships in v0.1; the dangling-call miss is its motivating case).
    """
    events = [normalize_event(e) for e in (trajectory or [])]
    item = to_item(events, final_claim, task)
    det = _DETECTOR.detect(item)

    findings = tuple(Finding(f.check, f.detail) for f in det.findings)

    # SDK-level confidence grading from ADAPTER provenance (not detector flags):
    # a firing family degrades only if it leaned on a sniffed/inferred signal.
    checks = {f.check for f in findings}
    flags: List[str] = []
    if "error_concealment" in checks and any(
        isinstance(e, ToolResult) and e.error_sniffed for e in events
    ):
        flags.append("error_payload_sniffed")
    if "completion_vs_evidence" in checks and any(
        isinstance(e, ToolResult) and e.truncation_inferred for e in events
    ):
        flags.append("truncation_inferred")

    confidence = 0.7 if (findings and flags) else 1.0
    result = Verdict(
        verdict=det.verdict,
        evidence=findings,
        confidence=confidence,
        detector_version=DETECTOR_VERSION,
        flags=tuple(flags),
        category=det.category,
    )

    if judge is not None and _should_escalate(escalate, result):
        replacement = judge.judge(events, final_claim or "", task or "", result)
        if replacement is not None:
            return replacement
    return result
