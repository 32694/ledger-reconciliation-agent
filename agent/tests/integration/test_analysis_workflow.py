import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Self

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field
from sqlalchemy.orm import Session

from reconciliation_agent.domain.repository import ReconciliationRepository
from reconciliation_agent.domain.seed import seed_demo_data
from reconciliation_agent.rag.retriever import RuleCitation
from reconciliation_agent.runs import RunService, RunServiceProvider
from reconciliation_agent.state import EvidenceStatus
from reconciliation_agent.tools.cases import build_case_tool
from reconciliation_agent.tools.ledger import build_ledger_tool
from reconciliation_agent.tools.payments import build_payment_tool
from reconciliation_agent.tools.rules import build_rule_search_tool
from reconciliation_agent.workflow import build_graph


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    cursor: int = 0
    bound_tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-reconciliation-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Self:
        del tool_choice, kwargs
        self.bound_tool_names = [
            tool.name if isinstance(tool, BaseTool) else str(tool) for tool in tools
        ]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        response = self.responses[self.cursor]
        self.cursor += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


class StubRuleRetriever:
    def search(
        self,
        channel: str,
        rule_version: str,
        query: str,
        limit: int,
    ) -> list[RuleCitation]:
        del query, limit
        if rule_version == "RULE-MISSING-V9":
            return []
        return [
            RuleCitation(
                chunk_id=f"chunk-{channel.lower()}-amount",
                source=f"{channel.lower()}-v1.md",
                section="金额差异",
                rule_version=rule_version,
                content="渠道金额与内部支付金额不一致时，需要人工确认差异来源。",
                distance=0.1,
            )
        ]


def _tool_call(name: str, call_id: str, **args: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _analysis_responses(case_id: str, rule_version: str) -> list[AIMessage]:
    return [
        _tool_call("get_reconciliation_case", "call-case", case_id=case_id),
        _tool_call("find_payment_evidence", "call-payment", case_id=case_id),
        _tool_call("find_ledger_evidence", "call-ledger", case_id=case_id),
        _tool_call(
            "search_reconciliation_rules",
            "call-rule",
            channel="ALIPAY",
            rule_version=rule_version,
            query="金额差异如何处理",
            limit=3,
        ),
        AIMessage(
            content=json.dumps(
                {
                    "status": "READY",
                    "summary": "渠道金额与内部支付金额相差 100 分。",
                    "resolution_recommendation": {
                        "resolution_code": "INTERNAL_CONFIRMED",
                        "note": "内部支付与账本金额一致，建议以内部金额为准。",
                    },
                },
                ensure_ascii=False,
            )
        ),
    ]


def _build_test_tools(session: Session) -> list[BaseTool]:
    @contextmanager
    def repository_provider() -> Iterator[ReconciliationRepository]:
        yield ReconciliationRepository(session)

    @contextmanager
    def rule_provider() -> Iterator[StubRuleRetriever]:
        yield StubRuleRetriever()

    return [
        build_case_tool(repository_provider),
        build_payment_tool(repository_provider),
        build_ledger_tool(repository_provider),
        build_rule_search_tool(rule_provider),
    ]


def _build_test_run_provider(session: Session) -> RunServiceProvider:
    @contextmanager
    def provider() -> Iterator[RunService]:
        with session.begin_nested():
            yield RunService(session)

    return provider


@pytest.mark.anyio
async def test_complete_evidence_produces_cited_analysis_and_checkpoint(
    session: Session,
) -> None:
    seed_demo_data(session)
    saver = InMemorySaver()
    model = ScriptedChatModel(
        responses=_analysis_responses("CASE-1001", "RULE-ALIPAY-V1")
    )
    tools = _build_test_tools(session)
    run_provider = _build_test_run_provider(session)
    graph = build_graph(
        model=model,
        tools=tools,
        checkpointer=saver,
        run_service_provider=run_provider,
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": "THREAD-CASE-1001"}
    }

    result = await graph.ainvoke(
        {"messages": [("user", "分析 CASE-1001 的差异原因")]}, config=config
    )

    assert model.bound_tool_names == [tool.name for tool in tools]
    assert result["tool_call_history"] == [
        "get_reconciliation_case",
        "find_payment_evidence",
        "find_ledger_evidence",
        "search_reconciliation_rules",
    ]
    assert result["tool_call_count"] == 4
    assert result["evidence_status"] == EvidenceStatus.COMPLETE
    assert {item["rule_version"] for item in result["citations"]} == {
        "RULE-ALIPAY-V1"
    }
    assert result["final_response"]["status"] == "READY"

    fresh_graph = build_graph(
        model=model,
        tools=tools,
        checkpointer=saver,
        run_service_provider=run_provider,
    )
    restored = await fresh_graph.aget_state(config)
    assert restored.values["final_response"] == result["final_response"]


@pytest.mark.anyio
async def test_missing_locked_rule_returns_deterministic_refusal(
    session: Session,
) -> None:
    seed_demo_data(session)
    graph = build_graph(
        model=ScriptedChatModel(
            responses=_analysis_responses("CASE-1004", "RULE-MISSING-V9")
        ),
        tools=_build_test_tools(session),
        checkpointer=InMemorySaver(),
        run_service_provider=_build_test_run_provider(session),
    )

    result = await graph.ainvoke(
        {"messages": [("user", "分析 CASE-1004 的差异原因")]},
        config={"configurable": {"thread_id": "THREAD-CASE-1004"}},
    )

    assert result["evidence_status"] == EvidenceStatus.MISSING_RULE
    assert result["final_response"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["final_response"]["resolution_recommendation"] is None


@pytest.mark.anyio
async def test_malformed_case_id_returns_invalid_request_without_model(
    session: Session,
) -> None:
    model = ScriptedChatModel(responses=[])
    graph = build_graph(
        model=model,
        tools=_build_test_tools(session),
        checkpointer=InMemorySaver(),
        run_service_provider=_build_test_run_provider(session),
    )

    result = await graph.ainvoke(
        {"messages": [("user", "分析格式错误的 CASE-ABCD")]},
        config={"configurable": {"thread_id": "THREAD-INVALID-CASE"}},
    )

    assert model.cursor == 0
    assert result["final_response"]["status"] == "INVALID_REQUEST"
    assert result["final_response"]["case_id"] is None


@pytest.mark.anyio
async def test_new_turn_cannot_reuse_previous_turn_evidence(session: Session) -> None:
    seed_demo_data(session)
    first_turn = _analysis_responses("CASE-1001", "RULE-ALIPAY-V1")
    model = ScriptedChatModel(
        responses=[
            *first_turn,
            AIMessage(
                content=json.dumps(
                    {
                        "status": "READY",
                        "summary": "没有重新查询证据的结论。",
                        "resolution_recommendation": {
                            "resolution_code": "INTERNAL_CONFIRMED",
                            "note": "不应被接受。",
                        },
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    graph = build_graph(
        model=model,
        tools=_build_test_tools(session),
        checkpointer=InMemorySaver(),
        run_service_provider=_build_test_run_provider(session),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "THREAD-TWO-TURNS"}}
    await graph.ainvoke(
        {"messages": [("user", "分析 CASE-1001 的差异原因")]}, config=config
    )

    result = await graph.ainvoke(
        {"messages": [("user", "重新分析 CASE-1001")]}, config=config
    )

    assert result["tool_call_count"] == 0
    assert result["tool_call_history"] == []
    assert result["evidence_status"] == EvidenceStatus.MISSING_CASE
    assert result["final_response"]["status"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.anyio
async def test_tool_budget_stops_after_eight_calls(session: Session) -> None:
    seed_demo_data(session)
    calls = [
        {
            "name": "get_reconciliation_case",
            "args": {"case_id": "CASE-1001"},
            "id": f"call-{index}",
            "type": "tool_call",
        }
        for index in range(9)
    ]
    graph = build_graph(
        model=ScriptedChatModel(responses=[AIMessage(content="", tool_calls=calls)]),
        tools=_build_test_tools(session),
        checkpointer=InMemorySaver(),
        run_service_provider=_build_test_run_provider(session),
    )

    result = await graph.ainvoke(
        {"messages": [("user", "分析 CASE-1001")]},
        config={"configurable": {"thread_id": "THREAD-BUDGET"}},
    )

    assert result["tool_call_count"] == 8
    assert len(result["tool_call_history"]) == 8
    assert result["evidence_status"] == EvidenceStatus.TOOL_BUDGET_EXHAUSTED
    budget_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and "TOOL_BUDGET_EXHAUSTED" in str(message.content)
    ]
    assert len(budget_messages) == 1
