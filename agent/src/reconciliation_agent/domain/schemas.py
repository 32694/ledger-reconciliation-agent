"""Validated commands for reconciliation operations."""

from pydantic import BaseModel, ConfigDict, Field

from reconciliation_agent.domain.models import ResolutionCode


class ResolutionProposal(BaseModel):
    """Exact write action bound to one human confirmation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^CASE-[A-Z0-9-]+$",
    )
    resolution_code: ResolutionCode
    note: str = Field(min_length=1, max_length=2000)


class ResolveCaseCommand(BaseModel):
    """Inputs required to resolve a reconciliation case."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    case_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^CASE-[A-Z0-9-]+$",
    )
    resolution_code: ResolutionCode
    note: str = Field(min_length=1, max_length=2000)
    confirmed_by: str = Field(min_length=1, max_length=128)
    agent_run_id: str | None = Field(default=None, min_length=1, max_length=64)
