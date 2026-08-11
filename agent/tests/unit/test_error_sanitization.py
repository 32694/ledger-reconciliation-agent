import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from reconciliation_agent.runs import public_error, redact_private_error
from reconciliation_agent.workflow import route_after_validation, validate_evidence


def test_public_error_hides_secrets_and_connection_strings() -> None:
    error = RuntimeError(
        "failed postgresql://agent:db-secret@db/agent with api-key-secret"
    )

    result = public_error(error)

    assert result.code == "INTERNAL_ERROR"
    assert result.message == "Agent服务暂时不可用，请稍后重试。"
    assert "secret" not in result.model_dump_json()


def test_private_log_redaction_hides_url_credentials_and_configured_secrets() -> None:
    result = redact_private_error(
        "failed postgresql://agent:db-secret@db/agent with api-key-secret",
        secrets=("api-key-secret",),
    )

    assert result == "failed postgresql://***@db/agent with ***"


def test_dependency_tool_error_routes_to_public_database_failure() -> None:
    tool_call = {
        "name": "get_reconciliation_case",
        "args": {"case_id": "CASE-1001"},
        "id": "call-database",
        "type": "tool_call",
    }
    state = {
        "messages": [
            HumanMessage(content="分析 CASE-1001"),
            AIMessage(content="", tool_calls=[tool_call]),
            ToolMessage(
                content=json.dumps(
                    {
                        "error": "DEPENDENCY_UNAVAILABLE",
                        "message": "证据存储暂时不可用，请稍后重试",
                    },
                    ensure_ascii=False,
                ),
                name="get_reconciliation_case",
                tool_call_id="call-database",
            ),
        ]
    }

    update = validate_evidence(state)  # type: ignore[arg-type]

    assert update["public_error"] == {
        "code": "DATABASE_UNAVAILABLE",
        "message": "数据服务暂时不可用，请稍后重试。",
    }
    assert route_after_validation(update) == "finalize"
