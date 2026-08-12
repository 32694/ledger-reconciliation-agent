"""Validated commands for reconciliation operations."""

from pydantic import BaseModel, ConfigDict, Field

from reconciliation_agent.domain.models import ResolutionCode


class ResolutionProposal(BaseModel):
    """Exact write action bound to one human confirmation."""

    # 禁止额外字段，防止模型或前端偷偷把未经审核的写入参数带入确认流程。
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

    # Proposal 是待确认建议；Command 额外携带真正执行写操作的确认人。
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
