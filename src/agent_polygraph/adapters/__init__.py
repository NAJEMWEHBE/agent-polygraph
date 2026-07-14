# -*- coding: utf-8 -*-
"""
Framework adapters for agent-polygraph.

Each adapter maps one framework's native emission shape into the locked SDK
trajectory model and calls ``verify()``. Import the entry point you need:

  * ``openai_agents.output_guardrail``  -- OpenAI Agents SDK output guardrail (audit + block)
  * ``openinference.verify_spans``      -- OTel / OpenInference span consumer (audit only)
  * ``claude_code.stop_hook``           -- Claude Code Stop hook (audit + block)
  * ``litellm.post_call_guardrail``     -- LiteLLM post-call guardrail (audit only)

The four modules are also re-exported here for convenience.
"""
from __future__ import annotations

from .openai_agents import from_openai_agents, output_guardrail, GuardrailResult
from .openinference import from_openinference, verify_spans
from .claude_code import from_transcript, stop_hook
from .litellm import from_litellm, post_call_guardrail

__all__ = [
    "from_openai_agents", "output_guardrail", "GuardrailResult",
    "from_openinference", "verify_spans",
    "from_transcript", "stop_hook",
    "from_litellm", "post_call_guardrail",
]
