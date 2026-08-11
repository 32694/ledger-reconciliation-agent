import json
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import OperationalError

from reconciliation_agent.rag.retriever import RuleCitation
from reconciliation_agent.tools.rules import build_rule_search_tool


class FakeRetriever:
    def __init__(self, results=None, error: Exception | None = None) -> None:
        self.results = results if results is not None else []
        self.error = error
        self.calls: list[tuple[str, str, str, int]] = []

    def search(self, channel, rule_version, query, limit):
        self.calls.append((channel, rule_version, query, limit))
        if self.error is not None:
            raise self.error
        return self.results


def provider_for(retriever):
    @contextmanager
    def provider():
        yield retriever

    return provider


def test_rule_tool_returns_versioned_citations() -> None:
    citation = RuleCitation(
        chunk_id="CHUNK-1",
        channel="ALIPAY",
        source="alipay-v1.md",
        section="金额差异",
        rule_version="RULE-ALIPAY-V1",
        content="金额必须完全一致。",
        distance=0.01,
    )
    retriever = FakeRetriever([citation])
    tool = build_rule_search_tool(provider_for(retriever))

    result = json.loads(
        tool.invoke(
            {
                "channel": "ALIPAY",
                "rule_version": "RULE-ALIPAY-V1",
                "query": "金额差异",
            }
        )
    )

    assert result["citations"][0]["chunk_id"] == "CHUNK-1"
    assert retriever.calls == [("ALIPAY", "RULE-ALIPAY-V1", "金额差异", 3)]


def test_rule_tool_never_falls_back_when_version_is_missing() -> None:
    tool = build_rule_search_tool(provider_for(FakeRetriever()))
    assert json.loads(
        tool.invoke(
            {
                "channel": "ALIPAY",
                "rule_version": "RULE-MISSING-V9",
                "query": "金额差异",
            }
        )
    ) == {
        "error": "RULE_VERSION_NOT_FOUND",
        "message": "未找到指定版本的对账规则: RULE-MISSING-V9",
    }


def test_rule_tool_rejects_unknown_fields_exactly() -> None:
    tool = build_rule_search_tool(provider_for(FakeRetriever()))
    assert json.loads(
        tool.invoke(
            {
                "channel": "ALIPAY",
                "rule_version": "RULE-ALIPAY-V1",
                "query": "金额",
                "sql": "DROP TABLE rule_chunk",
            }
        )
    ) == {"error": "INVALID_ARGUMENT", "message": "不支持的参数: sql"}


@pytest.mark.parametrize(
    "arguments",
    [
        {"channel": "ALIPAY", "rule_version": "RULE-ALIPAY-V1"},
        {
            "channel": "ALIPAY",
            "rule_version": "RULE-ALIPAY-V1",
            "query": "金额",
            "sql": "DROP TABLE rule_chunk",
        },
        {
            "channel": "ALIPAY",
            "rule_version": "bad-version",
            "query": "金额",
        },
    ],
)
def test_rule_tool_returns_stable_invalid_argument(arguments) -> None:
    tool = build_rule_search_tool(provider_for(FakeRetriever()))
    result = json.loads(tool.invoke(arguments))
    assert result["error"] == "INVALID_ARGUMENT"
    assert "sql" not in result.get("details", "")


def test_rule_tool_hides_database_error_details() -> None:
    error = OperationalError("SELECT secret", {}, Exception("db-password"))
    tool = build_rule_search_tool(provider_for(FakeRetriever(error=error)))
    output = tool.invoke(
        {
            "channel": "ALIPAY",
            "rule_version": "RULE-ALIPAY-V1",
            "query": "金额",
        }
    )
    assert json.loads(output) == {
        "error": "DEPENDENCY_UNAVAILABLE",
        "message": "规则检索暂时不可用，请稍后重试",
    }
    assert "secret" not in output
    assert "password" not in output


def test_rule_tool_does_not_mask_programmer_errors() -> None:
    tool = build_rule_search_tool(
        provider_for(FakeRetriever(error=RuntimeError("programmer bug")))
    )
    with pytest.raises(RuntimeError, match="programmer bug"):
        tool.invoke(
            {
                "channel": "ALIPAY",
                "rule_version": "RULE-ALIPAY-V1",
                "query": "金额",
            }
        )
