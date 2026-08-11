from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.orm import Session

from reconciliation_agent.domain.repository import AgentRunRepository
from reconciliation_agent.domain.seed import seed_demo_data
from reconciliation_agent.runs import RunService, RunServiceProvider
from reconciliation_agent.workflow import build_graph

from .test_analysis_workflow import (
    ScriptedChatModel,
    _analysis_responses,
    _build_test_tools,
)


def _run_service_provider(session: Session) -> RunServiceProvider:
    @contextmanager
    def provider() -> Iterator[RunService]:
        with session.begin_nested():
            yield RunService(session)

    return provider


@pytest.mark.anyio
async def test_completed_run_keeps_tool_summary_and_citations(
    session: Session,
) -> None:
    seed_demo_data(session)
    graph = build_graph(
        model=ScriptedChatModel(
            responses=_analysis_responses("CASE-1001", "RULE-ALIPAY-V1")
        ),
        tools=_build_test_tools(session),
        checkpointer=InMemorySaver(),
        run_service_provider=_run_service_provider(session),
    )

    result = await graph.ainvoke(
        {"messages": [("user", "分析案件 CASE-1001")]},
        config={"configurable": {"thread_id": "THREAD-RUN-1"}},
    )

    saved = AgentRunRepository(session).get(result["run_id"])
    assert saved.status == "COMPLETED"
    assert saved.tool_names == [
        "get_reconciliation_case",
        "find_payment_evidence",
        "find_ledger_evidence",
        "search_reconciliation_rules",
    ]
    assert saved.citation_ids == ["chunk-alipay-amount"]
    assert saved.error_message is None
    assert "READY" in (saved.conclusion or "")


@pytest.mark.anyio
async def test_missing_rule_run_is_persisted_as_rejected(session: Session) -> None:
    seed_demo_data(session)
    graph = build_graph(
        model=ScriptedChatModel(
            responses=_analysis_responses("CASE-1004", "RULE-MISSING-V9")
        ),
        tools=_build_test_tools(session),
        checkpointer=InMemorySaver(),
        run_service_provider=_run_service_provider(session),
    )

    result = await graph.ainvoke(
        {"messages": [("user", "分析案件 CASE-1004")]},
        config={"configurable": {"thread_id": "THREAD-RUN-REJECTED"}},
    )

    saved = AgentRunRepository(session).get(result["run_id"])
    assert saved.status == "REJECTED"
    assert saved.citation_ids == []
    assert result["final_response"]["status"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.anyio
async def test_unexpected_model_failure_is_public_and_persisted(session: Session) -> None:
    seed_demo_data(session)
    graph = build_graph(
        model=ScriptedChatModel(responses=[]),
        tools=_build_test_tools(session),
        checkpointer=InMemorySaver(),
        run_service_provider=_run_service_provider(session),
    )

    result = await graph.ainvoke(
        {"messages": [("user", "分析案件 CASE-1001")]},
        config={"configurable": {"thread_id": "THREAD-RUN-FAILED"}},
    )

    saved = AgentRunRepository(session).get(result["run_id"])
    assert saved.status == "FAILED"
    assert saved.error_message == "Agent服务暂时不可用，请稍后重试。"
    assert result["final_response"] == {
        "status": "FAILED",
        "case_id": "CASE-1001",
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "Agent服务暂时不可用，请稍后重试。",
        },
    }
