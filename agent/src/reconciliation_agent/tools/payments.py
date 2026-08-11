"""Read-only payment evidence tool."""

from datetime import datetime

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict

from reconciliation_agent.domain.models import ReconciliationCase
from reconciliation_agent.tools.common import (
    CaseIdInput,
    ReadRepository,
    RepositoryProvider,
    configure_validation_errors,
    invoke_read_tool,
    production_repository_provider,
)


class PaymentEvidence(BaseModel):
    """One structured payment evidence record."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: str
    channel_transaction_id: str | None
    amount_cents: int
    status: str
    occurred_at: datetime


class PaymentEvidenceResult(BaseModel):
    """Payment evidence associated with one case."""

    case_id: str
    payments: list[PaymentEvidence]


def _payment_evidence(
    repository: ReadRepository, case_id: str, case: ReconciliationCase
) -> PaymentEvidenceResult:
    return PaymentEvidenceResult(
        case_id=case_id,
        payments=[
            PaymentEvidence.model_validate(payment)
            for payment in repository.find_payments(case)
        ],
    )


def build_payment_tool(provider: RepositoryProvider) -> BaseTool:
    """Build the payment tool with an injected repository provider."""

    @tool(args_schema=CaseIdInput)
    def find_payment_evidence(case_id: str) -> str:
        """Read ordered structured payment evidence by case ID without SQL."""
        return invoke_read_tool(
            provider,
            case_id,
            lambda repository, case: _payment_evidence(repository, case_id, case),
        )

    return configure_validation_errors(find_payment_evidence)


find_payment_evidence = build_payment_tool(production_repository_provider)
