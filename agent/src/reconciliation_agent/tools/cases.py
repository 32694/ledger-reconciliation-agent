"""Read-only reconciliation case tool."""

from datetime import datetime

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict

from reconciliation_agent.domain.models import (
    CaseStatus,
    ReconciliationCase,
    ResultType,
)
from reconciliation_agent.tools.common import (
    CaseIdInput,
    RepositoryProvider,
    configure_validation_errors,
    invoke_read_tool,
    production_repository_provider,
)


class CaseEvidence(BaseModel):
    """Structured reconciliation case evidence."""

    model_config = ConfigDict(from_attributes=True)

    case_id: str
    channel: str
    result_type: ResultType
    status: CaseStatus
    rule_version: str
    channel_transaction_id: str | None
    channel_amount_cents: int | None
    occurred_at: datetime


def _case_evidence(case: ReconciliationCase) -> CaseEvidence:
    return CaseEvidence.model_validate(case)


def build_case_tool(provider: RepositoryProvider) -> BaseTool:
    """Build the case tool with an injected repository provider."""

    @tool(args_schema=CaseIdInput)
    def get_reconciliation_case(case_id: str) -> str:
        """Read structured case evidence by case ID without writing data."""
        return invoke_read_tool(
            provider,
            case_id,
            lambda _repository, case: _case_evidence(case),
        )

    return configure_validation_errors(get_reconciliation_case)


get_reconciliation_case = build_case_tool(production_repository_provider)
