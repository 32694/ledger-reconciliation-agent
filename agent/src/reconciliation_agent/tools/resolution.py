"""Protected write operation executed only after human confirmation."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from reconciliation_agent.domain.repository import ReconciliationRepository
from reconciliation_agent.domain.schemas import ResolutionProposal, ResolveCaseCommand
from reconciliation_agent.safety.confirmations import ConfirmationService
from reconciliation_agent.tools.common import get_tool_engine


class ResolutionResult(BaseModel):
    """Public result of one confirmed case resolution."""

    model_config = ConfigDict(from_attributes=True)

    case_id: str
    resolution_code: str
    confirmed_by: str
    agent_run_id: str | None
    audit_event_id: int


ResolutionActionScope = tuple[ConfirmationService, ReconciliationRepository]
ResolutionActionProvider = Callable[[], AbstractContextManager[ResolutionActionScope]]


@contextmanager
def production_resolution_action_provider() -> Iterator[ResolutionActionScope]:
    """Commit token consumption and case resolution in one transaction."""
    # token 消费、案件更新和审计落库共享同一事务，任何一步失败都会整体回滚。
    with Session(get_tool_engine()) as session, session.begin():
        yield ConfirmationService(session), ReconciliationRepository(session)


def execute_confirmed_resolution(
    provider: ResolutionActionProvider,
    *,
    confirmation_token: str,
    proposal: ResolutionProposal,
    confirmed_by: str,
) -> ResolutionResult:
    """Consume a bound token and resolve its exact proposal atomically."""
    # 写工具不暴露给模型的普通 ToolNode，只能由人工确认后的固定图节点调用。
    with provider() as (confirmation_service, repository):
        confirmation_service.consume(confirmation_token, proposal)
        resolution, audit_event = repository.resolve_case_with_audit(
            ResolveCaseCommand(
                case_id=proposal.case_id,
                resolution_code=proposal.resolution_code,
                note=proposal.note,
                confirmed_by=confirmed_by,
                agent_run_id=proposal.run_id,
            )
        )
        return ResolutionResult(
            case_id=resolution.case_id,
            resolution_code=resolution.resolution_code,
            confirmed_by=resolution.confirmed_by,
            agent_run_id=resolution.agent_run_id,
            audit_event_id=audit_event.id,
        )
