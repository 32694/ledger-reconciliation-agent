"""Runtime context for reconciliation graph invocations."""

from dataclasses import dataclass

from reconciliation_agent.prompts import SYSTEM_PROMPT


@dataclass(kw_only=True)
class AgentContext:
    """Per-run prompt configuration supplied outside persisted graph state."""

    system_prompt: str = SYSTEM_PROMPT
