"""Fixed synthetic data for local reconciliation demonstrations."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import (
    AuditEvent,
    CaseStatus,
    LedgerEntry,
    Payment,
    ReconciliationCase,
    ResultType,
    RuleChunk,
)

DEMO_CASES = (
    ("CASE-1001", "ALIPAY", "AMOUNT_MISMATCH", "RULE-ALIPAY-V1", 10_000),
    ("CASE-1002", "WECHAT", "CHANNEL_ONLY", "RULE-WECHAT-V1", 5_000),
    ("CASE-1003", "UNIONPAY", "INTERNAL_ONLY", "RULE-UNIONPAY-V1", 20_000),
    ("CASE-1004", "ALIPAY", "AMOUNT_MISMATCH", "RULE-MISSING-V9", 8_000),
)

_BASE_TIME = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
_TRANSACTION_IDS = {
    "CASE-1001": "ALIPAY-TXN-1001",
    "CASE-1002": "WECHAT-TXN-1002",
    "CASE-1003": "UNIONPAY-TXN-1003",
    "CASE-1004": "ALIPAY-TXN-1004",
}


def _upsert_case(
    session: Session,
    case_id: str,
    channel: str,
    result_type: str,
    rule_version: str,
    amount_cents: int,
    occurred_at: datetime,
) -> None:
    case = session.scalar(
        select(ReconciliationCase).where(ReconciliationCase.case_id == case_id)
    )
    channel_amount = None if result_type == "INTERNAL_ONLY" else amount_cents
    values = {
        "channel": channel,
        "result_type": ResultType(result_type),
        "rule_version": rule_version,
        "channel_transaction_id": _TRANSACTION_IDS[case_id],
        "channel_amount_cents": channel_amount,
        "occurred_at": occurred_at,
        "created_at": occurred_at,
    }
    if case is None:
        session.add(
            ReconciliationCase(case_id=case_id, status=CaseStatus.OPEN, **values)
        )
        return
    for field, value in values.items():
        setattr(case, field, value)


def _upsert_payment(session: Session, primary_key: int, values: dict[str, Any]) -> None:
    payment = session.scalar(
        select(Payment).where(Payment.payment_id == values["payment_id"])
    )
    if payment is None:
        session.add(Payment(id=primary_key, **values))
        return
    for field, value in values.items():
        setattr(payment, field, value)


def _upsert_rule_chunk(
    session: Session, primary_key: int, values: dict[str, Any]
) -> None:
    rule_chunk = session.scalar(
        select(RuleChunk).where(RuleChunk.chunk_id == values["chunk_id"])
    )
    if rule_chunk is None:
        session.add(RuleChunk(id=primary_key, **values))
        return
    for field, value in values.items():
        setattr(rule_chunk, field, value)


def _upsert_ledger_entry(session: Session, values: dict[str, Any]) -> None:
    ledger_entry = session.scalar(
        select(LedgerEntry).where(
            LedgerEntry.business_reference == values["business_reference"],
            LedgerEntry.account_code == values["account_code"],
            LedgerEntry.direction == values["direction"],
            LedgerEntry.posted_at == values["posted_at"],
        )
    )
    if ledger_entry is None:
        session.add(LedgerEntry(**values))
        return
    for field, value in values.items():
        setattr(ledger_entry, field, value)


def _upsert_audit_event(session: Session, values: dict[str, Any]) -> None:
    audit_event = session.scalar(
        select(AuditEvent).where(
            AuditEvent.business_reference == values["business_reference"],
            AuditEvent.action == values["action"],
            AuditEvent.occurred_at == values["occurred_at"],
        )
    )
    if audit_event is None:
        session.add(AuditEvent(**values))
        return
    for field, value in values.items():
        setattr(audit_event, field, value)


def seed_demo_data(session: Session) -> None:
    """Upsert the deterministic synthetic reconciliation data set."""
    for offset, demo_case in enumerate(DEMO_CASES):
        case_id, channel, result_type, rule_version, amount_cents = demo_case
        _upsert_case(
            session,
            case_id,
            channel,
            result_type,
            rule_version,
            amount_cents,
            _BASE_TIME + timedelta(hours=offset),
        )

    payment_rows = (
        (-101, "PAY-1001", "CASE-1001", 9_900),
        (-103, "PAY-1003", "CASE-1003", 20_000),
        (-104, "PAY-1004", "CASE-1004", 7_900),
    )
    for offset, (row_id, payment_id, case_id, amount_cents) in enumerate(payment_rows):
        _upsert_payment(
            session,
            row_id,
            {
                "payment_id": payment_id,
                "channel_transaction_id": _TRANSACTION_IDS[case_id],
                "amount_cents": amount_cents,
                "status": "SUCCESS",
                "occurred_at": _BASE_TIME + timedelta(hours=offset, minutes=1),
            },
        )

    ledger_rows = (
        ("PAY-1001", "ALIPAY_CLEARING", "DEBIT", 9_900, 0),
        ("PAY-1001", "CUSTOMER_FUNDS", "CREDIT", 9_900, 1),
        ("PAY-1003", "UNIONPAY_CLEARING", "DEBIT", 20_000, 120),
        ("PAY-1003", "CUSTOMER_FUNDS", "CREDIT", 20_000, 121),
        ("PAY-1004", "ALIPAY_CLEARING", "DEBIT", 7_900, 180),
        ("PAY-1004", "CUSTOMER_FUNDS", "CREDIT", 7_900, 181),
    )
    for reference, account, direction, amount_cents, minute in ledger_rows:
        _upsert_ledger_entry(
            session,
            {
                "business_reference": reference,
                "account_code": account,
                "direction": direction,
                "amount_cents": amount_cents,
                "posted_at": _BASE_TIME + timedelta(minutes=minute + 2),
            },
        )

    for offset, (case_id, _, _, _, _) in enumerate(DEMO_CASES):
        case_time = _BASE_TIME + timedelta(hours=offset)
        _upsert_audit_event(
            session,
            {
                "actor": "synthetic-seed",
                "action": "EVIDENCE_CAPTURED",
                "result": "RECORDED",
                "business_reference": case_id,
                "occurred_at": case_time - timedelta(minutes=2),
            },
        )
        _upsert_audit_event(
            session,
            {
                "actor": "reconciliation-engine",
                "action": "CASE_OPENED",
                "result": "OPEN",
                "business_reference": case_id,
                "occurred_at": case_time + timedelta(minutes=5),
            },
        )

    rule_rows = (
        (-301, "RULE-ALIPAY-V1", "ALIPAY"),
        (-302, "RULE-WECHAT-V1", "WECHAT"),
        (-303, "RULE-UNIONPAY-V1", "UNIONPAY"),
    )
    for row_id, rule_version, channel in rule_rows:
        _upsert_rule_chunk(
            session,
            row_id,
            {
                "chunk_id": f"{rule_version}-CHUNK-1",
                "channel": channel,
                "rule_version": rule_version,
                "effective_at": _BASE_TIME - timedelta(days=30),
                "source": "synthetic://reconciliation-rules",
                "section": "amount-and-presence-checks",
                "content": f"Synthetic reconciliation rule for {channel}.",
                "embedding": [0.0] * 512,
            },
        )

    session.flush()
