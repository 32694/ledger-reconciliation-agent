from reconciliation_agent.domain.models import AgentRun


def test_agent_run_updated_at_has_update_behavior() -> None:
    updated_at = AgentRun.__table__.c.updated_at

    assert updated_at.onupdate is not None
    assert str(updated_at.onupdate.arg) == "now()"
