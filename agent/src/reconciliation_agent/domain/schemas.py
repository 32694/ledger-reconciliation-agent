"""Validated commands for reconciliation operations."""

from pydantic import BaseModel

from reconciliation_agent.domain.models import ResolutionCode


class ResolveCaseCommand(BaseModel):
    """Inputs required to resolve a reconciliation case."""

    case_id: str
    resolution_code: ResolutionCode
    note: str
    confirmed_by: str
    agent_run_id: str | None = None
