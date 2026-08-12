"""Read-only audit timeline tool."""

from datetime import datetime

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict

from reconciliation_agent.tools.common import (
    CaseIdInput,
    ReadRepository,
    RepositoryProvider,
    configure_validation_errors,
    configured_repository_provider,
    invoke_read_tool,
    production_repository_provider,
)


class AuditEvidence(BaseModel):
    """One structured audit event."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str
    action: str
    result: str
    business_reference: str
    occurred_at: datetime


class AuditEvidenceResult(BaseModel):
    """Audit evidence associated with one case."""

    case_id: str
    events: list[AuditEvidence]


def _audit_evidence(
    repository: ReadRepository,
    case_id: str,
) -> AuditEvidenceResult:
    return AuditEvidenceResult(
        case_id=case_id,
        events=[
            AuditEvidence.model_validate(event)
            for event in repository.get_audit_timeline(case_id)
        ],
    )


def build_audit_timeline_tool(provider: RepositoryProvider) -> BaseTool:
    """Build the audit tool with an injected repository provider."""

    @tool(args_schema=CaseIdInput)
    def get_audit_timeline(case_id: str) -> str:
        """Read ordered structured audit evidence by case ID without writes."""
        return invoke_read_tool(
            provider,
            case_id,
            lambda repository, _case: _audit_evidence(repository, case_id),
        )

    return configure_validation_errors(get_audit_timeline)


get_audit_timeline = build_audit_timeline_tool(configured_repository_provider())
