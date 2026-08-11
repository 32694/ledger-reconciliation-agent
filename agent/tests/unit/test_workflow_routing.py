from reconciliation_agent.state import EvidenceStatus
from reconciliation_agent.workflow import route_after_validation


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
