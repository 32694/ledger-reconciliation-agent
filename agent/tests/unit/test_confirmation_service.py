from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import ConfirmationToken, ResolutionCode
from reconciliation_agent.domain.schemas import ResolutionProposal
from reconciliation_agent.safety.confirmations import (
    ConfirmationRejected,
    ConfirmationService,
)


class FrozenClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 11, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def _proposal(**changes: str) -> ResolutionProposal:
    values = {
        "run_id": "RUN-1",
        "case_id": "CASE-1001",
        "resolution_code": "CHANNEL_CONFIRMED",
        "note": "渠道金额为准",
        **changes,
    }
    return ResolutionProposal.model_validate(values)


def test_token_is_bound_to_exact_action(session: Session) -> None:
    service = ConfirmationService(session, clock=FrozenClock())
    proposal = _proposal()
    token = service.issue(proposal)
    changed = proposal.model_copy(
        update={"resolution_code": ResolutionCode.INTERNAL_CONFIRMED}
    )

    with pytest.raises(ConfirmationRejected, match="确认参数已变化"):
        service.consume(token, changed)


def test_token_cannot_be_replayed(session: Session) -> None:
    service = ConfirmationService(session, clock=FrozenClock())
    proposal = _proposal(run_id="RUN-2")
    token = service.issue(proposal)

    service.consume(token, proposal)

    with pytest.raises(ConfirmationRejected, match="确认已使用"):
        service.consume(token, proposal)


def test_expired_confirmation_is_rejected(session: Session) -> None:
    clock = FrozenClock()
    service = ConfirmationService(session, clock=clock)
    proposal = _proposal(run_id="RUN-EXPIRED")
    token = service.issue(proposal)
    clock.advance(timedelta(minutes=11))

    with pytest.raises(ConfirmationRejected, match="确认已过期"):
        service.consume(token, proposal)


def test_changed_note_invalidates_confirmation(session: Session) -> None:
    service = ConfirmationService(session, clock=FrozenClock())
    proposal = _proposal()
    token = service.issue(proposal)
    changed = proposal.model_copy(update={"note": "内部流水为准"})

    with pytest.raises(ConfirmationRejected, match="确认参数已变化"):
        service.consume(token, changed)


def test_clear_token_is_never_persisted(session: Session) -> None:
    service = ConfirmationService(session, clock=FrozenClock())
    token = service.issue(_proposal())
    stored_hash = session.scalar(select(ConfirmationToken.token_hash))

    assert stored_hash is not None
    assert token not in stored_hash
    assert token not in repr(session.scalar(select(ConfirmationToken)))
