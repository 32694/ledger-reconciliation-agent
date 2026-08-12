"""Repository boundary for reconciliation persistence."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import (
    AgentRun,
    AuditEvent,
    CaseResolution,
    CaseStatus,
    LedgerEntry,
    Payment,
    ReconciliationCase,
)
from reconciliation_agent.domain.schemas import ResolveCaseCommand


class CaseNotFoundError(Exception):
    """Raised when a requested reconciliation case does not exist."""


class CaseAlreadyResolvedError(Exception):
    """Raised when a reconciliation case already has a resolution."""


class AgentRunNotFoundError(Exception):
    """Raised when a requested Agent run does not exist."""


class AgentRunRepository:
    """Persistence boundary for Agent investigation runs."""

    def __init__(self, session: Session) -> None:
        """Bind run persistence to one database session."""
        self._session = session

    def create(self, *, run_id: str, question: str, status: str) -> AgentRun:
        """Create a new run with empty evidence summaries."""
        # 每次问答都先创建运行记录，后续可按 run_id 追踪工具、引用和最终结论。
        run = AgentRun(
            run_id=run_id,
            question=question,
            tool_calls=[],
            citations=[],
            conclusion=None,
            status=status,
            error_message=None,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def get(self, run_id: str, *, for_update: bool = False) -> AgentRun:
        """Return one run, optionally locking it for a state transition."""
        # 生命周期更新使用 SELECT FOR UPDATE，避免并发回调相互覆盖运行状态。
        statement = select(AgentRun).where(AgentRun.run_id == run_id)
        if for_update:
            statement = statement.with_for_update()
        run = self._session.scalar(statement)
        if run is None:
            raise AgentRunNotFoundError(run_id)
        return run


class ReconciliationRepository:
    """Persistence boundary for reconciliation data."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to a SQLAlchemy session."""
        self._session = session

    def list_cases(self) -> list[ReconciliationCase]:
        """Return reconciliation cases in stable identifier order."""
        statement = select(ReconciliationCase).order_by(ReconciliationCase.case_id)
        return list(self._session.scalars(statement))

    def get_case(self, case_id: str) -> ReconciliationCase:
        """Return one reconciliation case or raise when it is absent."""
        case = self._session.scalar(
            select(ReconciliationCase).where(ReconciliationCase.case_id == case_id)
        )
        if case is None:
            raise CaseNotFoundError(case_id)
        return case

    def find_payments(self, case: ReconciliationCase) -> list[Payment]:
        """Return internal payments related to a reconciliation case."""
        # 渠道交易号是渠道案件与内部支付之间的业务关联键。
        if not case.channel_transaction_id:
            return []
        statement = (
            select(Payment)
            .where(Payment.channel_transaction_id == case.channel_transaction_id)
            .order_by(Payment.payment_id)
        )
        return list(self._session.scalars(statement))

    def find_ledger_entries(self, case: ReconciliationCase) -> list[LedgerEntry]:
        """Return ledger entries related to payments for a case."""
        # 先得到关联支付号，再通过 business_reference 找到账本借贷分录。
        if not case.channel_transaction_id:
            return []
        payment_ids = select(Payment.payment_id).where(
            Payment.channel_transaction_id == case.channel_transaction_id
        )
        statement = (
            select(LedgerEntry)
            .where(LedgerEntry.business_reference.in_(payment_ids))
            .order_by(LedgerEntry.posted_at, LedgerEntry.id)
        )
        return list(self._session.scalars(statement))

    def get_audit_timeline(self, case_id: str) -> list[AuditEvent]:
        """Return a case audit timeline in stable chronological order."""
        statement = (
            select(AuditEvent)
            .where(AuditEvent.business_reference == case_id)
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
        return list(self._session.scalars(statement))

    def resolve_case(self, command: ResolveCaseCommand) -> CaseResolution:
        """Resolve an open case and write the corresponding audit event."""
        resolution, _ = self.resolve_case_with_audit(command)
        return resolution

    def resolve_case_with_audit(
        self, command: ResolveCaseCommand
    ) -> tuple[CaseResolution, AuditEvent]:
        """Resolve an open case and return its exact audit event."""
        # 锁定案件行后检查状态，使同一案件在并发确认下也只能成功解决一次。
        case = self._session.scalar(
            select(ReconciliationCase)
            .where(ReconciliationCase.case_id == command.case_id)
            .with_for_update()
        )
        if case is None:
            raise CaseNotFoundError(command.case_id)
        if case.status is not CaseStatus.OPEN:
            raise CaseAlreadyResolvedError(command.case_id)

        now = datetime.now(UTC)
        resolution = CaseResolution.from_command(command, confirmed_at=now)
        audit_event = AuditEvent.for_resolution(command, occurred_at=now)
        case.status = CaseStatus.RESOLVED
        # 案件状态、解决记录和审计事件由外层同一数据库事务原子提交。
        self._session.add_all([resolution, audit_event])
        self._session.flush()
        return resolution, audit_event
