"""Runtime context for reconciliation graph invocations."""

from dataclasses import dataclass

from reconciliation_agent.prompts import SYSTEM_PROMPT


@dataclass(kw_only=True)
class AgentContext:
    """Per-run prompt configuration supplied outside persisted graph state."""

    # 运行上下文不写入 checkpoint，适合存放可替换但不属于案件事实的配置。
    system_prompt: str = SYSTEM_PROMPT
