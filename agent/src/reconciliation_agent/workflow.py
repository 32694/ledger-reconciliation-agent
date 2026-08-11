"""Evidence-constrained LangGraph workflow for reconciliation analysis."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from reconciliation_agent.context import AgentContext
from reconciliation_agent.domain.models import ResolutionCode, ResultType
from reconciliation_agent.model import build_chat_model, invoke_model_with_retry
from reconciliation_agent.prompts import SYSTEM_PROMPT
from reconciliation_agent.settings import get_settings
from reconciliation_agent.state import (
    AgentState,
    EvidenceStatus,
    EvidenceStatusValue,
    InputState,
)
from reconciliation_agent.tools import READ_TOOLS
from reconciliation_agent.tools.rules import search_reconciliation_rules

_CASE_ID_PATTERN = re.compile(r"\bCASE-[0-9]{4}\b")


class ResolutionRecommendation(BaseModel):
    """Model-proposed action that still requires later human confirmation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolution_code: ResolutionCode
    note: str = Field(min_length=1, max_length=2000)


class DraftAnalysis(BaseModel):
    """Strict draft emitted by the model after it stops using tools."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["READY"]
    summary: str = Field(min_length=1, max_length=4000)
    resolution_recommendation: ResolutionRecommendation


def _message_text(message: AnyMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        item if isinstance(item, str) else str(item.get("text", ""))
        for item in content
    )


def parse_request(state: AgentState) -> dict[str, Any]:
    """Extract the fixed-format case identifier and requested intent."""
    question = _message_text(state["messages"][-1]) if state["messages"] else ""
    match = _CASE_ID_PATTERN.search(question.upper())
    case_id = match.group(0) if match else None
    intent: Literal["ANALYZE", "RESOLVE"] = (
        "RESOLVE" if any(word in question for word in ("解决", "处理", "结案")) else "ANALYZE"
    )
    return {
        "case_id": case_id,
        "intent": intent,
        "tool_call_count": 0,
        "tool_call_history": [],
        "collected_facts": {},
        "citations": [],
        "evidence_status": (
            EvidenceStatus.UNKNOWN.value if case_id else EvidenceStatus.MISSING_CASE.value
        ),
        "budget_exhausted": False,
    }


def _route_after_parse(
    state: AgentState,
) -> Literal["collect_case_facts", "refuse_analysis"]:
    return "collect_case_facts" if state.get("case_id") else "refuse_analysis"


def _route_after_model(state: AgentState) -> Literal["tools", "validate_evidence"]:
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return "validate_evidence"


def _route_after_tools(
    state: AgentState,
) -> Literal["collect_case_facts", "validate_evidence"]:
    if state.get("budget_exhausted"):
        return "validate_evidence"
    return "collect_case_facts"


def _current_turn_messages(messages: Sequence[AnyMessage]) -> Sequence[AnyMessage]:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index:]
    return messages


def _tool_calls_by_id(messages: Sequence[AnyMessage]) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                calls[str(call["id"])] = cast(dict[str, Any], call)
    return calls


def _decode_tool_message(message: ToolMessage) -> dict[str, Any] | None:
    try:
        value = json.loads(_message_text(message))
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _collect_tool_evidence(
    messages: Sequence[AnyMessage],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    facts: dict[str, Any] = {}
    citations: list[dict[str, Any]] = []
    calls = _tool_calls_by_id(messages)
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        value = _decode_tool_message(message)
        if value is None or "error" in value:
            continue
        if message.name == "get_reconciliation_case":
            facts["case"] = value
        elif message.name == "find_payment_evidence":
            facts["payments"] = value
        elif message.name == "find_ledger_evidence":
            facts["ledger"] = value
        elif message.name == "get_audit_timeline":
            facts["audit"] = value
        elif message.name == "search_reconciliation_rules":
            call = calls.get(message.tool_call_id, {})
            args = call.get("args", {})
            facts["rule_search"] = {
                "channel": args.get("channel"),
                "rule_version": args.get("rule_version"),
            }
            raw_citations = value.get("citations", [])
            if isinstance(raw_citations, list):
                citations = [item for item in raw_citations if isinstance(item, dict)]
    return facts, citations


def _has_required_structured_facts(
    case: dict[str, Any], facts: dict[str, Any]
) -> bool:
    result_type = case.get("result_type")
    payments = facts.get("payments")
    ledger = facts.get("ledger")
    if not isinstance(payments, dict) or not isinstance(payments.get("payments"), list):
        return False
    if result_type == ResultType.CHANNEL_ONLY.value:
        return True
    if not payments["payments"]:
        return False
    return (
        isinstance(ledger, dict)
        and isinstance(ledger.get("entries"), list)
        and bool(ledger["entries"])
    )


def validate_evidence(state: AgentState) -> dict[str, Any]:
    """Accept only matching structured facts and exact-version rule citations."""
    facts, citations = _collect_tool_evidence(_current_turn_messages(state["messages"]))
    case = facts.get("case")
    if state.get("budget_exhausted"):
        status: EvidenceStatusValue = EvidenceStatus.TOOL_BUDGET_EXHAUSTED.value
    elif not isinstance(case, dict):
        status = EvidenceStatus.MISSING_CASE.value
    elif not _has_required_structured_facts(case, facts):
        status = EvidenceStatus.MISSING_STRUCTURED_FACTS.value
    else:
        rule_search = facts.get("rule_search")
        matching_rule = (
            isinstance(rule_search, dict)
            and rule_search.get("channel") == case.get("channel")
            and rule_search.get("rule_version") == case.get("rule_version")
            and bool(citations)
            and all(
                citation.get("rule_version") == case.get("rule_version")
                for citation in citations
            )
        )
        status = (
            EvidenceStatus.COMPLETE.value
            if matching_rule
            else EvidenceStatus.MISSING_RULE.value
        )
    return {
        "collected_facts": facts,
        "citations": citations if status == EvidenceStatus.COMPLETE else [],
        "evidence_status": status,
    }


def route_after_validation(
    state: dict[str, Any],
) -> Literal["draft_analysis", "refuse_analysis"]:
    """Route complete evidence to drafting and every gap to deterministic refusal."""
    if state.get("evidence_status") == EvidenceStatus.COMPLETE:
        return "draft_analysis"
    return "refuse_analysis"


def draft_analysis(state: AgentState) -> dict[str, Any]:
    """Validate the model draft without accepting model-supplied citations."""
    last_message = state["messages"][-1]
    try:
        draft = DraftAnalysis.model_validate_json(_message_text(last_message))
    except ValidationError:
        return {
            "draft_analysis": None,
            "proposed_resolution": None,
            "evidence_status": EvidenceStatus.INVALID_MODEL_OUTPUT.value,
        }
    return {
        "draft_analysis": draft.model_dump(mode="json"),
        "proposed_resolution": draft.resolution_recommendation.model_dump(mode="json"),
    }


def refuse_analysis(state: AgentState) -> dict[str, Any]:
    """Clear any proposed write when evidence is incomplete."""
    del state
    return {"draft_analysis": None, "proposed_resolution": None}


def finalize(state: AgentState) -> dict[str, Any]:
    """Build the stable public response from validated state only."""
    draft = state.get("draft_analysis")
    if state.get("evidence_status") == EvidenceStatus.COMPLETE and isinstance(draft, dict):
        response = {
            "status": "READY",
            "case_id": state.get("case_id"),
            "summary": draft["summary"],
            "citations": state.get("citations", []),
            "resolution_recommendation": state.get("proposed_resolution"),
        }
    else:
        evidence_status = state.get("evidence_status", EvidenceStatus.UNKNOWN.value)
        response = {
            "status": "INSUFFICIENT_EVIDENCE",
            "case_id": state.get("case_id"),
            "summary": "证据不完整，无法生成可靠的对账结论。",
            "evidence_status": evidence_status,
            "citations": [],
            "resolution_recommendation": None,
        }
    return {"final_response": response}


def build_graph(
    *,
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    max_tool_calls: int = 8,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile a graph with injectable model, tools and test checkpointer."""
    tool_list = list(tools or [*READ_TOOLS, search_reconciliation_rules])
    tool_node = ToolNode(tool_list)
    resolved_model = model

    async def collect_case_facts(state: AgentState) -> dict[str, list[AIMessage]]:
        nonlocal resolved_model
        if resolved_model is None:
            resolved_model = build_chat_model(get_settings())
        bound_model = resolved_model.bind_tools(tool_list)
        prompt = SYSTEM_PROMPT.format(
            case_id=state.get("case_id") or "UNKNOWN",
            max_tool_calls=max_tool_calls,
        )
        response = await invoke_model_with_retry(
            bound_model,
            [SystemMessage(content=prompt), *_current_turn_messages(state["messages"])],
        )
        return {"messages": [response]}

    async def execute_tools(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, Any]:
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage):
            return {}
        remaining = max(0, max_tool_calls - state.get("tool_call_count", 0))
        allowed = last_message.tool_calls[:remaining]
        overflow = last_message.tool_calls[remaining:]
        messages: list[ToolMessage] = []
        if allowed:
            bounded_request = AIMessage(content="", tool_calls=allowed)
            tool_result = await tool_node.ainvoke(
                {"messages": [bounded_request]}, config=config
            )
            messages.extend(cast(list[ToolMessage], tool_result["messages"]))
        messages.extend(
            ToolMessage(
                content=json.dumps(
                    {
                        "error": "TOOL_BUDGET_EXHAUSTED",
                        "message": f"工具调用次数不能超过 {max_tool_calls} 次",
                    },
                    ensure_ascii=False,
                ),
                name=str(call["name"]),
                tool_call_id=str(call["id"]),
                status="error",
            )
            for call in overflow
        )
        return {
            "messages": messages,
            "tool_call_count": state.get("tool_call_count", 0) + len(allowed),
            "tool_call_history": [
                *state.get("tool_call_history", []),
                *(str(call["name"]) for call in allowed),
            ],
            "budget_exhausted": bool(overflow),
        }

    builder = StateGraph(
        AgentState,
        input_schema=InputState,
        context_schema=AgentContext,
    )
    builder.add_node("parse_request", parse_request)
    builder.add_node("collect_case_facts", collect_case_facts)
    builder.add_node("tools", execute_tools)
    builder.add_node("validate_evidence", validate_evidence)
    builder.add_node("draft_analysis", draft_analysis)
    builder.add_node("refuse_analysis", refuse_analysis)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "parse_request")
    builder.add_conditional_edges("parse_request", _route_after_parse)
    builder.add_conditional_edges("collect_case_facts", _route_after_model)
    builder.add_conditional_edges("tools", _route_after_tools)
    builder.add_conditional_edges("validate_evidence", route_after_validation)
    builder.add_edge("draft_analysis", "finalize")
    builder.add_edge("refuse_analysis", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer, name="Reconciliation Agent")


# LangGraph API injects its own persistent checkpointer into this exported graph.
graph = build_graph()
