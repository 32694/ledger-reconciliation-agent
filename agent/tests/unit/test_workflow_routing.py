from contextlib import nullcontext
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import Tool
from pydantic import ValidationError

import reconciliation_agent.workflow as workflow
from reconciliation_agent.settings import get_settings
from reconciliation_agent.state import EvidenceStatus
from reconciliation_agent.workflow import (
    _parse_draft_analysis,
    build_graph,
    parse_request,
    route_after_validation,
)


class ScriptedChatModel:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, _tools: list[Any]) -> "ScriptedChatModel":
        return self

    async def ainvoke(self, _messages: list[Any]) -> AIMessage:
        self.calls += 1
        return AIMessage(content="No additional evidence is available.")


class InMemoryRunService:
    def start(self, _question: str) -> str:
        return "RUN-test"

    def finish(self, **_kwargs: Any) -> None:
        return None


def test_case_id_can_touch_chinese_text() -> None:
    result = parse_request(
        {"messages": [HumanMessage(content="分析CASE-1001并建议处理方式")]}
    )

    assert result["case_id"] == "CASE-1001"


def test_missing_rule_routes_to_refusal() -> None:
    assert (
        route_after_validation({"evidence_status": EvidenceStatus.MISSING_RULE})
        == "refuse_analysis"
    )


def test_complete_evidence_routes_to_draft() -> None:
    assert (
        route_after_validation({"evidence_status": EvidenceStatus.COMPLETE})
        == "draft_analysis"
    )


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"status":"READY","summary":"已核对","resolution_recommendation":{"resolution_code":"INTERNAL_CONFIRMED","note":"以内部证据为准"}}\n```',
        '模型结论如下：\n{"status":"READY","summary":"已核对","resolution_recommendation":{"resolution_code":"INTERNAL_CONFIRMED","note":"以内部证据为准"}}',
        '{"status":"READY","summary":"渠道交易 ALIPAY-TXN-1001 金额为 10000 分，而内部支付 PAY-1001（状态 SUCCESS）和账本均一致记录为 9900 分，差额 100 分。","resolution_recommendation":{"resolution_code":"INTERNAL_CONFIRMED","note":"内部证据一致，建议人工复核渠道侧差额。"}}',
    ],
)
def test_draft_parser_accepts_json_wrappers_without_relaxing_schema(text: str) -> None:
    draft = _parse_draft_analysis(text)

    assert draft.status == "READY"
    assert draft.resolution_recommendation.resolution_code == "INTERNAL_CONFIRMED"


def test_draft_parser_still_rejects_invalid_json_schema() -> None:
    with pytest.raises(ValidationError):
        _parse_draft_analysis(
            '{"status":"READY","summary":"已核对","resolution_recommendation":{}}'
        )


@pytest.mark.anyio
async def test_injected_model_does_not_require_production_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in (
        "DATABASE_URL",
        "MODEL_BASE_URL",
        "MODEL_API_KEY",
        "MODEL_NAME",
        "MODEL_TIMEOUT_SECONDS",
        "MODEL_MAX_OUTPUT_TOKENS",
        "MODEL_MAX_RETRIES",
    ):
        monkeypatch.delenv(variable, raising=False)
    get_settings.cache_clear()
    model = ScriptedChatModel()
    unused_tool = Tool.from_function(
        name="unused_tool",
        description="A tool that must not be called by this test.",
        func=lambda: "unused",
    )

    try:
        result = await build_graph(
            model=cast(Any, model),
            tools=[unused_tool],
            run_service_provider=cast(Any, lambda: nullcontext(InMemoryRunService())),
        ).ainvoke({"messages": [HumanMessage(content="请分析 CASE-1001")]})
    finally:
        get_settings.cache_clear()

    assert result.get("public_error") is None
    assert model.calls == 1


@pytest.mark.anyio
async def test_production_model_keeps_configured_retries_across_graph_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://agent:password@localhost/agent"
    )
    monkeypatch.setenv("MODEL_BASE_URL", "https://model.example.test/v1")
    monkeypatch.setenv("MODEL_API_KEY", "model-api-key")
    monkeypatch.setenv("MODEL_NAME", "ledger-model")
    monkeypatch.setenv("MODEL_MAX_RETRIES", "7")
    get_settings.cache_clear()
    model = ScriptedChatModel()
    retries: list[int] = []
    unused_tool = Tool.from_function(
        name="unused_tool",
        description="A tool that must not be called by this test.",
        func=lambda: "unused",
    )

    async def invoke_with_retry(
        _model: Any, _messages: list[Any], *, max_retries: int
    ) -> AIMessage:
        retries.append(max_retries)
        return AIMessage(content="No additional evidence is available.")

    monkeypatch.setattr(workflow, "build_chat_model", lambda _settings: model)
    monkeypatch.setattr(workflow, "invoke_model_with_retry", invoke_with_retry)

    try:
        graph = workflow.build_graph(
            tools=[unused_tool],
            run_service_provider=cast(Any, lambda: nullcontext(InMemoryRunService())),
        )
        await graph.ainvoke({"messages": [HumanMessage(content="请分析 CASE-1001")]})
        await graph.ainvoke({"messages": [HumanMessage(content="请分析 CASE-1002")]})
    finally:
        get_settings.cache_clear()

    assert retries == [7, 7]
