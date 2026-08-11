from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import (
    CaseResolution,
    CaseStatus,
    ConfirmationToken,
)
from reconciliation_agent.domain.repository import (
    AgentRunRepository,
    ReconciliationRepository,
)
from reconciliation_agent.domain.seed import seed_demo_data
from reconciliation_agent.safety.confirmations import ConfirmationService
from reconciliation_agent.tools.resolution import ResolutionActionProvider
from reconciliation_agent.workflow import build_graph

from .test_analysis_workflow import (
    ScriptedChatModel,
    _analysis_responses,
    _build_test_run_provider,
    _build_test_tools,
)


def _action_provider(session: Session) -> ResolutionActionProvider:
    @contextmanager
    def provider() -> Iterator[tuple[ConfirmationService, ReconciliationRepository]]:
        with session.begin_nested():
            yield ConfirmationService(session), ReconciliationRepository(session)

    return provider


def _postgres_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture
def checkpoint_session(database_url: str) -> Generator[Session, None, None]:
    postgres_url = _postgres_url(database_url)
    with PostgresSaver.from_conn_string(postgres_url) as saver:
        saver.setup()
    engine = create_engine(database_url)
    connection = engine.connect()
    transaction = connection.begin()
    db_session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db_session
    finally:
        db_session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.anyio
async def test_reject_keeps_case_open(session: Session) -> None:
    seed_demo_data(session)
    graph = build_graph(
        model=ScriptedChatModel(
            responses=_analysis_responses("CASE-1001", "RULE-ALIPAY-V1")
        ),
        tools=_build_test_tools(session),
        checkpointer=InMemorySaver(),
        resolution_action_provider=_action_provider(session),
        run_service_provider=_build_test_run_provider(session),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "THREAD-REJECT"}}
    interrupted = await graph.ainvoke(
        {"messages": [("user", "解决 CASE-1001")]}, config=config
    )

    result = await graph.ainvoke(
        Command[Any](resume={"decisions": [{"type": "reject"}]}), config=config
    )

    assert interrupted["__interrupt__"]
    assert ReconciliationRepository(session).get_case("CASE-1001").status is CaseStatus.OPEN
    assert session.scalar(select(func.count()).select_from(CaseResolution)) == 0
    assert result["final_response"]["status"] == "REJECTED"
    assert AgentRunRepository(session).get(result["run_id"]).status == "REJECTED"


@pytest.mark.anyio
async def test_approve_resumes_with_fresh_postgres_graph_and_resolves_once(
    checkpoint_session: Session,
    database_url: str,
) -> None:
    session = checkpoint_session
    seed_demo_data(session)
    thread_id = f"THREAD-APPROVE-{uuid4()}"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    provider = _action_provider(session)
    postgres_url = _postgres_url(database_url)

    async with AsyncPostgresSaver.from_conn_string(postgres_url) as first_saver:
        first_graph = build_graph(
            model=ScriptedChatModel(
                responses=_analysis_responses("CASE-1001", "RULE-ALIPAY-V1")
            ),
            tools=_build_test_tools(session),
            checkpointer=first_saver,
            resolution_action_provider=provider,
            run_service_provider=_build_test_run_provider(session),
        )
        interrupted = await first_graph.ainvoke(
            {"messages": [("user", "解决 CASE-1001")]}, config=config
        )

    token = interrupted["__interrupt__"][0].value["action_requests"][0]["args"][
        "confirmation_token"
    ]
    stored = session.scalar(select(ConfirmationToken))
    assert stored is not None
    assert token != stored.token_hash
    assert (
        AgentRunRepository(session).get(interrupted["run_id"]).status
        == "WAITING_CONFIRMATION"
    )

    async with AsyncPostgresSaver.from_conn_string(postgres_url) as second_saver:
        fresh_graph = build_graph(
            model=ScriptedChatModel(responses=[]),
            tools=_build_test_tools(session),
            checkpointer=second_saver,
            resolution_action_provider=provider,
            run_service_provider=_build_test_run_provider(session),
        )
        result = await fresh_graph.ainvoke(
            Command[Any](resume={"decisions": [{"type": "approve"}]}), config=config
        )

    session.expire_all()
    assert ReconciliationRepository(session).get_case("CASE-1001").status is CaseStatus.RESOLVED
    assert session.scalar(select(func.count()).select_from(CaseResolution)) == 1
    assert session.scalar(select(func.count()).select_from(ConfirmationToken)) == 1
    assert result["final_response"]["status"] == "RESOLVED"
    assert token not in str(result)
    assert AgentRunRepository(session).get(result["run_id"]).status == "COMPLETED"
