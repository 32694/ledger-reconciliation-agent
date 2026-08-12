"""One-time, action-bound confirmation tokens."""

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import ConfirmationToken
from reconciliation_agent.domain.schemas import ResolutionProposal

Clock = Callable[[], datetime]


class ConfirmationRejected(Exception):
    """Raised when a confirmation cannot authorize the requested action."""


def _system_clock() -> datetime:
    return datetime.now(UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _proposal_fingerprint(proposal: ResolutionProposal) -> bytes:
    # 稳定 JSON 序列化使字段顺序不影响比较，同时把令牌绑定到完整业务参数。
    return json.dumps(
        proposal.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _stored_fingerprint(token: ConfirmationToken) -> bytes:
    return _proposal_fingerprint(
        ResolutionProposal(
            run_id=token.run_id,
            case_id=token.case_id,
            resolution_code=token.resolution_code,
            note=token.note,
        )
    )


class ConfirmationService:
    """Issue and consume one-time tokens inside a caller-owned transaction."""

    def __init__(
        self,
        session: Session,
        *,
        ttl_seconds: int = 600,
        clock: Clock = _system_clock,
    ) -> None:
        """Bind confirmation operations to a database session and clock."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._session = session
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    def issue(self, proposal: ResolutionProposal) -> str:
        """Persist only a token hash and return the one-time clear token."""
        # 明文令牌只出现一次；泄露数据库也无法直接恢复可用令牌。
        token = secrets.token_urlsafe(32)
        issued_at = self._clock()
        self._session.add(
            ConfirmationToken(
                token_hash=_token_hash(token),
                run_id=proposal.run_id,
                case_id=proposal.case_id,
                resolution_code=proposal.resolution_code,
                note=proposal.note,
                issued_at=issued_at,
                expires_at=issued_at + self._ttl,
                consumed_at=None,
            )
        )
        self._session.flush()
        return token

    def consume(self, token: str, proposal: ResolutionProposal) -> None:
        """Consume a matching, unexpired token exactly once under a row lock."""
        # 行锁串行化同一令牌的并发请求，阻止双击或重放造成重复写入。
        stored = self._session.scalar(
            select(ConfirmationToken)
            .where(ConfirmationToken.token_hash == _token_hash(token))
            .with_for_update()
        )
        if stored is None:
            raise ConfirmationRejected("确认无效")
        now = self._clock()
        if stored.consumed_at is not None:
            raise ConfirmationRejected("确认已使用")
        if now >= stored.expires_at:
            raise ConfirmationRejected("确认已过期")
        if not hmac.compare_digest(
            _stored_fingerprint(stored), _proposal_fingerprint(proposal)
        ):
            raise ConfirmationRejected("确认参数已变化")
        stored.consumed_at = now
        self._session.flush()
