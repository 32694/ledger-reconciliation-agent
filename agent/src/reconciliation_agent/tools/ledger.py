"""Read-only ledger evidence tool."""

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


class LedgerEvidence(BaseModel):
    """One structured ledger evidence record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    business_reference: str
    account_code: str
    direction: str
    amount_cents: int
    posted_at: datetime


class LedgerEvidenceResult(BaseModel):
    """Ledger evidence associated with one case."""

    case_id: str
    entries: list[LedgerEvidence]


def _ledger_evidence(
    repository: ReadRepository,
    case_id: str,
    case: ReconciliationCase,
) -> LedgerEvidenceResult:
    return LedgerEvidenceResult(
        case_id=case_id,
        entries=[
            LedgerEvidence.model_validate(entry)
            for entry in repository.find_ledger_entries(case)
        ],
    )


def build_ledger_tool(provider: RepositoryProvider) -> BaseTool:
    """Build the ledger tool with an injected repository provider."""

    @tool(args_schema=CaseIdInput)
    def find_ledger_evidence(case_id: str) -> str:
        """Read ordered structured ledger evidence by case ID without SQL."""
        return invoke_read_tool(
            provider,
            case_id,
            lambda repository, case: _ledger_evidence(repository, case_id, case),
        )

    return configure_validation_errors(find_ledger_evidence)


find_ledger_evidence = build_ledger_tool(production_repository_provider)
