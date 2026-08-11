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


ResolutionActionScope = tuple[ConfirmationService, ReconciliationRepository]
ResolutionActionProvider = Callable[
    [], AbstractContextManager[ResolutionActionScope]
]


@contextmanager
def production_resolution_action_provider() -> Iterator[ResolutionActionScope]:
    """Commit token consumption and case resolution in one transaction."""
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
    with provider() as (confirmation_service, repository):
        confirmation_service.consume(confirmation_token, proposal)
        resolution = repository.resolve_case(
            ResolveCaseCommand(
                case_id=proposal.case_id,
                resolution_code=proposal.resolution_code,
                note=proposal.note,
                confirmed_by=confirmed_by,
                agent_run_id=proposal.run_id,
            )
        )
        return ResolutionResult.model_validate(resolution)
