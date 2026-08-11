import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import OperationalError

from reconciliation_agent.domain.models import (
    AuditEvent,
    CaseStatus,
    LedgerEntry,
    Payment,
    ReconciliationCase,
    ResultType,
)
from reconciliation_agent.domain.repository import CaseNotFoundError
from reconciliation_agent.tools import (
    READ_TOOLS,
    find_ledger_evidence,
    find_payment_evidence,
    get_audit_timeline,
    get_reconciliation_case,
)
from reconciliation_agent.tools.audit import build_audit_timeline_tool
from reconciliation_agent.tools.cases import build_case_tool
from reconciliation_agent.tools.ledger import build_ledger_tool
from reconciliation_agent.tools.payments import build_payment_tool

NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.case = ReconciliationCase(
            id=1,
            case_id="CASE-1001",
            channel="ALIPAY",
            result_type=ResultType.AMOUNT_MISMATCH,
            status=CaseStatus.OPEN,
            rule_version="RULE-ALIPAY-V1",
            channel_transaction_id="ALIPAY-TXN-1001",
            channel_amount_cents=10_000,
            occurred_at=NOW,
        )
        self.payments = [
            Payment(
                id=2,
                payment_id="PAY-1001-B",
                channel_transaction_id="ALIPAY-TXN-1001",
                amount_cents=9_800,
                status="SUCCESS",
                occurred_at=NOW.replace(minute=2),
            ),
            Payment(
                id=1,
                payment_id="PAY-1001-A",
                channel_transaction_id="ALIPAY-TXN-1001",
                amount_cents=9_900,
                status="SUCCESS",
                occurred_at=NOW.replace(minute=1),
            ),
        ]
        self.entries = [
            LedgerEntry(
                id=22,
                business_reference="PAY-1001-B",
                account_code="CUSTOMER_FUNDS",
                direction="CREDIT",
                amount_cents=9_800,
                posted_at=NOW.replace(minute=4),
            ),
            LedgerEntry(
                id=21,
                business_reference="PAY-1001-B",
                account_code="ALIPAY_CLEARING",
                direction="DEBIT",
                amount_cents=9_800,
                posted_at=NOW.replace(minute=3),
            ),
        ]
        self.events = [
            AuditEvent(
                id=32,
                actor="reconciliation-job",
                action="CASE_OPENED",
                result="OPEN",
                business_reference="CASE-1001",
                occurred_at=NOW.replace(minute=6),
            ),
            AuditEvent(
                id=31,
                actor="evidence-job",
                action="EVIDENCE_CAPTURED",
                result="RECORDED",
                business_reference="CASE-1001",
                occurred_at=NOW.replace(minute=5),
            ),
        ]

    def get_case(self, case_id: str) -> ReconciliationCase:
        if self.error is not None:
            raise self.error
        if case_id != self.case.case_id:
            raise CaseNotFoundError(case_id)
        return self.case

    def find_payments(self, case: ReconciliationCase) -> list[Payment]:
        return self.payments

    def find_ledger_entries(self, case: ReconciliationCase) -> list[LedgerEntry]:
        return self.entries

    def get_audit_timeline(self, case_id: str) -> list[AuditEvent]:
        return self.events


class FakeProvider:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.open_count = 0
        self.close_count = 0

    @contextmanager
    def __call__(self):
        self.open_count += 1
        try:
            yield self.repository
        finally:
            self.close_count += 1


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(FakeRepository())


@pytest.fixture
def case_tool(provider: FakeProvider):
    return build_case_tool(provider)


@pytest.fixture
def payment_tool(provider: FakeProvider):
    return build_payment_tool(provider)


@pytest.fixture
def ledger_tool(provider: FakeProvider):
    return build_ledger_tool(provider)


@pytest.fixture
def audit_tool(provider: FakeProvider):
    return build_audit_timeline_tool(provider)


def test_case_tool_returns_structured_evidence(case_tool) -> None:
    result = json.loads(case_tool.invoke({"case_id": "CASE-1001"}))
    assert result["case_id"] == "CASE-1001"
    assert result["rule_version"] == "RULE-ALIPAY-V1"


def test_payment_tool_does_not_accept_unknown_fields(payment_tool) -> None:
    result = json.loads(
        payment_tool.invoke({"case_id": "CASE-1001", "sql": "DROP TABLE payment"})
    )
    assert result == {
        "error": "INVALID_ARGUMENT",
        "message": "不支持的参数: sql",
    }


def test_read_tool_names_and_order_are_stable() -> None:
    assert [tool.name for tool in READ_TOOLS] == [
        "get_reconciliation_case",
        "find_payment_evidence",
        "find_ledger_evidence",
        "get_audit_timeline",
    ]
    assert READ_TOOLS == [
        get_reconciliation_case,
        find_payment_evidence,
        find_ledger_evidence,
        get_audit_timeline,
    ]


def test_case_tool_serializes_exact_result(case_tool, provider: FakeProvider) -> None:
    assert json.loads(case_tool.invoke({"case_id": "CASE-1001"})) == {
        "case_id": "CASE-1001",
        "channel": "ALIPAY",
        "result_type": "AMOUNT_MISMATCH",
        "status": "OPEN",
        "rule_version": "RULE-ALIPAY-V1",
        "channel_transaction_id": "ALIPAY-TXN-1001",
        "channel_amount_cents": 10_000,
        "occurred_at": "2026-08-01T10:00:00Z",
    }
    assert (provider.open_count, provider.close_count) == (1, 1)


def test_payment_tool_preserves_repository_order(payment_tool) -> None:
    assert json.loads(payment_tool.invoke({"case_id": "CASE-1001"})) == {
        "case_id": "CASE-1001",
        "payments": [
            {
                "payment_id": "PAY-1001-B",
                "channel_transaction_id": "ALIPAY-TXN-1001",
                "amount_cents": 9_800,
                "status": "SUCCESS",
                "occurred_at": "2026-08-01T10:02:00Z",
            },
            {
                "payment_id": "PAY-1001-A",
                "channel_transaction_id": "ALIPAY-TXN-1001",
                "amount_cents": 9_900,
                "status": "SUCCESS",
                "occurred_at": "2026-08-01T10:01:00Z",
            },
        ],
    }


def test_ledger_tool_preserves_repository_order(ledger_tool) -> None:
    assert json.loads(ledger_tool.invoke({"case_id": "CASE-1001"})) == {
        "case_id": "CASE-1001",
        "entries": [
            {
                "id": 22,
                "business_reference": "PAY-1001-B",
                "account_code": "CUSTOMER_FUNDS",
                "direction": "CREDIT",
                "amount_cents": 9_800,
                "posted_at": "2026-08-01T10:04:00Z",
            },
            {
                "id": 21,
                "business_reference": "PAY-1001-B",
                "account_code": "ALIPAY_CLEARING",
                "direction": "DEBIT",
                "amount_cents": 9_800,
                "posted_at": "2026-08-01T10:03:00Z",
            },
        ],
    }


def test_audit_tool_preserves_repository_order(audit_tool) -> None:
    assert json.loads(audit_tool.invoke({"case_id": "CASE-1001"})) == {
        "case_id": "CASE-1001",
        "events": [
            {
                "id": 32,
                "actor": "reconciliation-job",
                "action": "CASE_OPENED",
                "result": "OPEN",
                "business_reference": "CASE-1001",
                "occurred_at": "2026-08-01T10:06:00Z",
            },
            {
                "id": 31,
                "actor": "evidence-job",
                "action": "EVIDENCE_CAPTURED",
                "result": "RECORDED",
                "business_reference": "CASE-1001",
                "occurred_at": "2026-08-01T10:05:00Z",
            },
        ],
    }


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "缺少必填参数: case_id"),
        ({"case_id": "CASE-12"}, "参数 case_id 必须符合 CASE-四位数字格式"),
        (
            {"case_id": "CASE-1001", "sql": "select 1", "query": "all"},
            "不支持的参数: query, sql",
        ),
    ],
)
def test_invalid_arguments_return_stable_json(case_tool, arguments, message) -> None:
    assert json.loads(case_tool.invoke(arguments)) == {
        "error": "INVALID_ARGUMENT",
        "message": message,
    }


@pytest.mark.parametrize(
    "builder",
    [build_case_tool, build_payment_tool, build_ledger_tool, build_audit_timeline_tool],
)
def test_case_not_found_is_stable_and_scope_closes(builder) -> None:
    provider = FakeProvider(FakeRepository())
    tool = builder(provider)

    assert json.loads(tool.invoke({"case_id": "CASE-9999"})) == {
        "error": "CASE_NOT_FOUND",
        "message": "未找到案件: CASE-9999",
    }
    assert (provider.open_count, provider.close_count) == (1, 1)


@pytest.mark.parametrize(
    "builder",
    [build_case_tool, build_payment_tool, build_ledger_tool, build_audit_timeline_tool],
)
def test_database_failure_is_generic_and_scope_closes(builder) -> None:
    database_error = OperationalError(
        "SELECT secret FROM private_table", {}, Exception("password=private")
    )
    provider = FakeProvider(FakeRepository(error=database_error))
    tool = builder(provider)

    output = tool.invoke({"case_id": "CASE-1001"})

    assert json.loads(output) == {
        "error": "DEPENDENCY_UNAVAILABLE",
        "message": "证据存储暂时不可用，请稍后重试",
    }
    assert "secret" not in output
    assert "password" not in output
    assert (provider.open_count, provider.close_count) == (1, 1)


def test_programmer_error_is_not_mislabeled_as_database_failure() -> None:
    provider = FakeProvider(FakeRepository(error=RuntimeError("programmer bug")))
    tool = build_case_tool(provider)

    with pytest.raises(RuntimeError, match="programmer bug"):
        tool.invoke({"case_id": "CASE-1001"})
    assert (provider.open_count, provider.close_count) == (1, 1)


def test_tools_import_without_database_settings(tmp_path) -> None:
    environment = {
        key: value for key, value in os.environ.items() if key != "DATABASE_URL"
    }

    completed = subprocess.run(
        [sys.executable, "-c", "import reconciliation_agent.tools"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
