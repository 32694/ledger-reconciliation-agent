from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import (
    AuditEvent,
    CaseResolution,
    CaseStatus,
    LedgerEntry,
    Payment,
    ResolutionCode,
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
