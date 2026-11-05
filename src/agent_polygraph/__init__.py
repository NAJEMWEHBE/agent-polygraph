# -*- coding: utf-8 -*-
"""
agent-polygraph -- a framework-agnostic completion-lie detector for AI agents.

Given one agent turn -- a task, the tool calls and results it produced, and the
final assistant message ("closing") -- ``verify()`` decides whether the closing
makes a completion or success claim that the turn's own evidence contradicts.

    from agent_polygraph import verify, ToolCall, ToolResult

    v = verify(
        [ToolCall("c1", "deploy", {"svc": "web"}),
         ToolResult("c1", content="ERROR: image pull failed", is_error=True)],
        final_claim="Done -- deployed successfully.",
    )
    v.verdict      # "lie"
    v.category     # "L1"  (error concealment)

The detector is the clean-room, heuristic-tier reimplementation validated at
verdict parity with a private production gate (see README). Four framework
adapters map native emissions onto the same ``verify()`` core:

    from agent_polygraph.adapters import (
        output_guardrail,     # OpenAI Agents SDK
        verify_spans,         # OTel / OpenInference
        stop_hook,            # Claude Code
        post_call_guardrail,  # LiteLLM
    )

Pure, synchronous, zero runtime dependencies (standard library only).
"""
from __future__ import annotations

from .events import (Message, ToolCall, ToolResult, Event,
                     from_bench, to_bench, to_item, normalize_event)
from .verify import (verify, Verdict, Finding, Config, DEFAULT_CONFIG,
                     DETECTOR_VERSION)
from .judge import Judge

# Adapter entry points, surfaced at the top level for discoverability.
from .adapters.openai_agents import output_guardrail, from_openai_agents
from .adapters.openinference import verify_spans, from_openinference
from .adapters.claude_code import stop_hook, from_transcript
from .adapters.litellm import post_call_guardrail, from_litellm

# One version source: detector.__version__. pyproject reads it at build time,
# the package exposes it here, and every verdict self-stamps it.
__version__ = DETECTOR_VERSION
detector_version = DETECTOR_VERSION

__all__ = [
    # core
    "verify", "Verdict", "Finding", "Config", "DEFAULT_CONFIG",
    "Judge", "detector_version", "DETECTOR_VERSION",
    # event model
    "Message", "ToolCall", "ToolResult", "Event",
    "from_bench", "to_bench", "to_item", "normalize_event",
    # adapter entry points
    "output_guardrail", "from_openai_agents",
    "verify_spans", "from_openinference",
    "stop_hook", "from_transcript",
    "post_call_guardrail", "from_litellm",
]
