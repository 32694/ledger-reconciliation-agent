"""Persisted state contracts for the reconciliation workflow."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, NotRequired, TypeAlias, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class EvidenceStatus(str, Enum):
    """Outcome of deterministic evidence validation."""

    UNKNOWN = "UNKNOWN"
    COMPLETE = "COMPLETE"
    MISSING_CASE = "MISSING_CASE"
    MISSING_STRUCTURED_FACTS = "MISSING_STRUCTURED_FACTS"
    MISSING_RULE = "MISSING_RULE"
    TOOL_BUDGET_EXHAUSTED = "TOOL_BUDGET_EXHAUSTED"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"


EvidenceStatusValue: TypeAlias = Literal[
    "UNKNOWN",
    "COMPLETE",
    "MISSING_CASE",
    "MISSING_STRUCTURED_FACTS",
    "MISSING_RULE",
    "TOOL_BUDGET_EXHAUSTED",
    "INVALID_MODEL_OUTPUT",
]


class InputState(TypedDict):
    """Public input accepted by the graph."""

    messages: Annotated[list[AnyMessage], add_messages]


class AgentState(InputState, total=False):
    """Evidence, routing and response state persisted by LangGraph."""

    case_id: str | None
    intent: Literal["ANALYZE", "RESOLVE"]
    tool_call_count: int
    tool_call_history: list[str]
    collected_facts: dict[str, Any]
    citations: list[dict[str, Any]]
    evidence_status: EvidenceStatusValue
    draft_analysis: NotRequired[dict[str, Any] | None]
    proposed_resolution: NotRequired[dict[str, Any] | None]
    confirmation_request: NotRequired[dict[str, Any] | None]
    confirmation_decision: NotRequired[dict[str, Any] | None]
    resolution_result: NotRequired[dict[str, Any] | None]
    final_response: dict[str, Any]
    budget_exhausted: bool
