from langchain_core.messages import HumanMessage

from reconciliation_agent.state import EvidenceStatus
from reconciliation_agent.workflow import parse_request, route_after_validation


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
