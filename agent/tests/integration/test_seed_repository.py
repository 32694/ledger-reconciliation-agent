from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import (
    AuditEvent,
    CaseResolution,
    CaseStatus,
    LedgerEntry,
    Payment,
    ReconciliationCase,
    ResolutionCode,
    ResultType,
)
from reconciliation_agent.domain.repository import (
    CaseAlreadyResolvedError,
    CaseNotFoundError,
    ReconciliationRepository,
)
from reconciliation_agent.domain.schemas import ResolveCaseCommand
from reconciliation_agent.domain.seed import seed_demo_data


def test_seed_is_repeatable(session: Session) -> None:
    seed_demo_data(session)
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    assert [case.case_id for case in repository.list_cases()] == [
        "CASE-1001",
        "CASE-1002",
        "CASE-1003",
        "CASE-1004",
    ]


def test_case_1001_has_amount_mismatch_evidence(session: Session) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    case = repository.get_case("CASE-1001")
    payments = repository.find_payments(case)
    assert case.channel_amount_cents == 10_000
    assert [payment.amount_cents for payment in payments] == [9_900]


def test_get_missing_case_raises(session: Session) -> None:
    repository = ReconciliationRepository(session)

    with pytest.raises(CaseNotFoundError, match="CASE-404"):
        repository.get_case("CASE-404")


@pytest.mark.parametrize("correlation_id", [None, ""])
def test_empty_correlation_does_not_match_unrelated_evidence(
    session: Session, correlation_id: str | None
) -> None:
    suffix = "NONE" if correlation_id is None else "EMPTY"
    unrelated_payment = Payment(
        payment_id=f"PAY-{suffix}-UNRELATED",
        channel_transaction_id=correlation_id,
        amount_cents=1_234,
        status="SUCCESS",
        occurred_at=datetime(2026, 8, 2, 8, 0, tzinfo=UTC),
    )
    session.add(unrelated_payment)
    session.flush()
    session.add(
        LedgerEntry(
            business_reference=unrelated_payment.payment_id,
            account_code="UNRELATED",
            direction="DEBIT",
            amount_cents=1_234,
            posted_at=datetime(2026, 8, 2, 8, 1, tzinfo=UTC),
        )
    )
    session.flush()
    case = ReconciliationCase(
        case_id="CASE-NULL-CORRELATION",
        channel="ALIPAY",
        result_type=ResultType.CHANNEL_ONLY,
        status=CaseStatus.OPEN,
        rule_version="RULE-NULL-V1",
        channel_transaction_id=correlation_id,
        channel_amount_cents=1_234,
        occurred_at=datetime(2026, 8, 2, 8, 0, tzinfo=UTC),
    )
    repository = ReconciliationRepository(session)

    assert repository.find_payments(case) == []
    assert repository.find_ledger_entries(case) == []


def test_seeded_case_evidence_covers_all_discrepancy_types(session: Session) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)

    case_1002 = repository.get_case("CASE-1002")
    case_1003 = repository.get_case("CASE-1003")
    case_1004 = repository.get_case("CASE-1004")

    assert repository.find_payments(case_1002) == []
    assert case_1003.channel_amount_cents is None
    assert [
        payment.amount_cents for payment in repository.find_payments(case_1003)
    ] == [20_000]
    assert case_1004.channel_amount_cents == 8_000
    assert [
        payment.amount_cents for payment in repository.find_payments(case_1004)
    ] == [7_900]
    assert case_1004.rule_version == "RULE-MISSING-V9"


def test_evidence_and_timeline_queries_have_stable_ordering(session: Session) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    case = repository.get_case("CASE-1001")
    session.add(
        Payment(
            payment_id="PAY-1001-Z",
            channel_transaction_id=case.channel_transaction_id,
            amount_cents=9_800,
            status="SUCCESS",
            occurred_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        )
    )
    session.flush()

    assert [case.case_id for case in repository.list_cases()] == [
        "CASE-1001",
        "CASE-1002",
        "CASE-1003",
        "CASE-1004",
    ]
    assert [payment.payment_id for payment in repository.find_payments(case)] == [
        "PAY-1001",
        "PAY-1001-Z",
    ]
    assert [entry.account_code for entry in repository.find_ledger_entries(case)] == [
        "ALIPAY_CLEARING",
        "CUSTOMER_FUNDS",
    ]
    assert [event.action for event in repository.get_audit_timeline(case.case_id)] == [
        "EVIDENCE_CAPTURED",
        "CASE_OPENED",
    ]


@pytest.mark.parametrize("case_id", ["CASE-1001", "CASE-1003", "CASE-1004"])
def test_internal_payment_ledger_entries_are_balanced(
    session: Session, case_id: str
) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)

    entries = repository.find_ledger_entries(repository.get_case(case_id))
    debits = sum(entry.amount_cents for entry in entries if entry.direction == "DEBIT")
    credits = sum(
        entry.amount_cents for entry in entries if entry.direction == "CREDIT"
    )

    assert entries
    assert debits == credits


def test_resolve_case_persists_resolution_and_matching_audit(
    session: Session,
) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    timeline_before = repository.get_audit_timeline("CASE-1001")
    command = ResolveCaseCommand(
        case_id="CASE-1001",
        resolution_code=ResolutionCode.INTERNAL_CONFIRMED,
        note="Internal payment verified",
        confirmed_by="operator@example.test",
        agent_run_id="run-1001",
    )

    resolution = repository.resolve_case(command)

    case = repository.get_case(command.case_id)
    timeline_after = repository.get_audit_timeline(command.case_id)
    assert case.status is CaseStatus.RESOLVED
    assert (
        session.scalar(
            select(func.count())
            .select_from(CaseResolution)
            .where(CaseResolution.case_id == command.case_id)
        )
        == 1
    )
    assert len(timeline_after) == len(timeline_before) + 1
    assert timeline_after[-1].action == "CASE_RESOLVED"
    assert timeline_after[-1].occurred_at == resolution.confirmed_at
    assert resolution.agent_run_id == "run-1001"

    with pytest.raises(CaseAlreadyResolvedError, match=command.case_id):
        repository.resolve_case(command)


def test_resolve_missing_case_writes_no_resolution_or_audit(session: Session) -> None:
    repository = ReconciliationRepository(session)
    command = ResolveCaseCommand(
        case_id="CASE-NOT-FOUND",
        resolution_code=ResolutionCode.OTHER,
        note="No matching reconciliation case",
        confirmed_by="operator@example.test",
        agent_run_id="run-not-found",
    )

    with pytest.raises(CaseNotFoundError, match=command.case_id):
        repository.resolve_case(command)

    assert (
        session.scalar(
            select(func.count())
            .select_from(CaseResolution)
            .where(CaseResolution.case_id == command.case_id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.business_reference == command.case_id)
        )
        == 0
    )


def test_reseed_preserves_resolved_case_lifecycle(session: Session) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    command = ResolveCaseCommand(
        case_id="CASE-1001",
        resolution_code=ResolutionCode.INTERNAL_CONFIRMED,
        note="Internal payment verified",
        confirmed_by="operator@example.test",
        agent_run_id="run-reseed",
    )
    resolution = repository.resolve_case(command)
    resolution_id = resolution.id
    resolved_event = repository.get_audit_timeline(command.case_id)[-1]

    seed_demo_data(session)

    assert repository.get_case(command.case_id).status is CaseStatus.RESOLVED
    assert (
        session.scalar(
            select(func.count())
            .select_from(CaseResolution)
            .where(CaseResolution.id == resolution_id)
        )
        == 1
    )
    timeline = repository.get_audit_timeline(command.case_id)
    assert any(event.id == resolved_event.id for event in timeline)
    with pytest.raises(CaseAlreadyResolvedError, match=command.case_id):
        repository.resolve_case(command)


def test_seed_starts_new_demo_case_open(session: Session) -> None:
    session.execute(delete(CaseResolution).where(CaseResolution.case_id == "CASE-1004"))
    session.execute(
        delete(ReconciliationCase).where(ReconciliationCase.case_id == "CASE-1004")
    )

    seed_demo_data(session)

    assert (
        ReconciliationRepository(session).get_case("CASE-1004").status
        is CaseStatus.OPEN
    )


def test_seed_uses_ledger_business_identity_without_overwriting_ids(
    session: Session,
) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    case = repository.get_case("CASE-1001")
    demo_entry = repository.find_ledger_entries(case)[0]
    planned_id = demo_entry.id
    demo_entry.id = 900_101_200
    demo_entry.amount_cents = 1
    session.flush()
    unrelated_entry = LedgerEntry(
        id=planned_id,
        business_reference="PAY-UNRELATED",
        account_code="UNRELATED",
        direction="DEBIT",
        amount_cents=321,
        posted_at=datetime(2026, 8, 3, 8, 0, tzinfo=UTC),
    )
    session.add(unrelated_entry)
    session.flush()

    seed_demo_data(session)

    preserved_entry = session.get(LedgerEntry, planned_id)
    assert preserved_entry is not None
    assert preserved_entry.business_reference == "PAY-UNRELATED"
    converged_entry = session.get(LedgerEntry, 900_101_200)
    assert converged_entry is not None
    assert converged_entry.amount_cents == 9_900
    assert (
        session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(
                LedgerEntry.business_reference == "PAY-1001",
                LedgerEntry.account_code == "ALIPAY_CLEARING",
                LedgerEntry.direction == "DEBIT",
                LedgerEntry.posted_at == datetime(2026, 8, 1, 8, 2, tzinfo=UTC),
            )
        )
        == 1
    )


def test_seed_uses_audit_business_identity_without_overwriting_ids(
    session: Session,
) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    demo_event = repository.get_audit_timeline("CASE-1001")[0]
    planned_id = demo_event.id
    demo_event.id = 900_200_000
    demo_event.actor = "corrupted"
    demo_event.result = "CORRUPTED"
    session.flush()
    unrelated_event = AuditEvent(
        id=planned_id,
        actor="unrelated-actor",
        action="UNRELATED_ACTION",
        result="UNCHANGED",
        business_reference="CASE-UNRELATED",
        occurred_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
    )
    session.add(unrelated_event)
    session.flush()

    seed_demo_data(session)

    preserved_event = session.get(AuditEvent, planned_id)
    assert preserved_event is not None
    assert preserved_event.action == "UNRELATED_ACTION"
    converged_event = session.get(AuditEvent, 900_200_000)
    assert converged_event is not None
    assert converged_event.actor == "synthetic-seed"
    assert converged_event.result == "RECORDED"
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.business_reference == "CASE-1001",
                AuditEvent.action == "EVIDENCE_CAPTURED",
                AuditEvent.occurred_at == datetime(2026, 8, 1, 7, 58, tzinfo=UTC),
            )
        )
        == 1
    )


def test_concurrent_resolutions_produce_one_resolution(database_url: str) -> None:
    engine = create_engine(database_url)
    case_id = "CASE-CONCURRENT-RESOLUTION"
    command = ResolveCaseCommand(
        case_id=case_id,
        resolution_code=ResolutionCode.OTHER,
        note="Concurrent resolution",
        confirmed_by="concurrency-test",
    )
    barrier = Barrier(2, timeout=5)

    with Session(engine) as setup_session:
        setup_session.execute(
            delete(AuditEvent).where(AuditEvent.business_reference == case_id)
        )
        setup_session.execute(
            delete(CaseResolution).where(CaseResolution.case_id == case_id)
        )
        setup_session.execute(
            delete(ReconciliationCase).where(ReconciliationCase.case_id == case_id)
        )
        setup_session.add(
            ReconciliationCase(
                case_id=case_id,
                channel="ALIPAY",
                result_type=ResultType.AMOUNT_MISMATCH,
                status=CaseStatus.OPEN,
                rule_version="RULE-CONCURRENT-V1",
                channel_transaction_id="ALIPAY-TXN-CONCURRENT",
                channel_amount_cents=10_000,
                occurred_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
            )
        )
        setup_session.commit()

    def resolve() -> str:
        with Session(engine) as worker_session:
            worker_session.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait()
            try:
                ReconciliationRepository(worker_session).resolve_case(command)
                worker_session.commit()
                return "resolved"
            except CaseAlreadyResolvedError:
                worker_session.rollback()
                return "already-resolved"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=10)
                for future in [
                    executor.submit(resolve),
                    executor.submit(resolve),
                ]
            ]

        assert sorted(results) == ["already-resolved", "resolved"]
        with Session(engine) as verification_session:
            assert (
                verification_session.scalar(
                    select(func.count())
                    .select_from(CaseResolution)
                    .where(CaseResolution.case_id == case_id)
                )
                == 1
            )
            assert (
                verification_session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.business_reference == case_id,
                        AuditEvent.action == "CASE_RESOLVED",
                    )
                )
                == 1
            )
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(
                delete(AuditEvent).where(AuditEvent.business_reference == case_id)
            )
            cleanup_session.execute(
                delete(CaseResolution).where(CaseResolution.case_id == case_id)
            )
            cleanup_session.execute(
                delete(ReconciliationCase).where(ReconciliationCase.case_id == case_id)
            )
            cleanup_session.commit()
        engine.dispose()


def test_seed_converges_existing_synthetic_rows_without_duplicates(
    session: Session,
) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    case = repository.get_case("CASE-1001")
    payment = repository.find_payments(case)[0]
    ledger_entry = repository.find_ledger_entries(case)[0]
    audit_event = repository.get_audit_timeline(case.case_id)[0]
    case.channel = "CORRUPTED"
    payment.id = 100_001
    payment.amount_cents = 1
    ledger_entry.amount_cents = 2
    audit_event.result = "CORRUPTED"
    session.flush()

    seed_demo_data(session)

    case = repository.get_case("CASE-1001")
    assert case.channel == "ALIPAY"
    assert repository.find_payments(case)[0].amount_cents == 9_900
    assert repository.find_ledger_entries(case)[0].amount_cents == 9_900
    assert repository.get_audit_timeline(case.case_id)[0].result == "RECORDED"
    assert session.scalar(select(func.count()).select_from(Payment)) == 3
    assert session.scalar(select(func.count()).select_from(LedgerEntry)) == 6
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == 8
