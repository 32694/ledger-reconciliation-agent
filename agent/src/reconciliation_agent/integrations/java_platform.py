"""Read-only adapter for the Java ledger platform evidence API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from reconciliation_agent.domain.models import (
    AuditEvent,
    CaseStatus,
    LedgerEntry,
    Payment,
    ReconciliationCase,
    ResultType,
)
from reconciliation_agent.domain.repository import CaseNotFoundError
from reconciliation_agent.settings import DatabaseSettings


class JavaPlatformUnavailableError(RuntimeError):
    """Raised when the Java evidence API cannot provide a response."""


class JavaPlatformRepository:
    """Map Java API DTOs into the Agent's stable read-only domain models.

    The adapter deliberately knows nothing about Java JPA entities or SQL. It only
    translates the documented JSON contract, so the Agent's workflow and semantic
    evidence checks remain identical for local and Java-backed data.
    """

    def __init__(self, client: httpx.Client, settings: DatabaseSettings) -> None:
        self._client = client
        self._settings = settings
        self._evidence_cache: dict[str, Mapping[str, Any]] = {}

    def _result_id(self, case_id: str) -> str:
        mapping = self._settings.java_case_id_map.get(case_id)
        result_id = mapping.get("result_id") if mapping else None
        if not result_id:
            raise CaseNotFoundError(
                f"Java resultId mapping is missing for Agent case {case_id}"
            )
        return result_id

    def _get_evidence(self, case_id: str) -> Mapping[str, Any]:
        cached = self._evidence_cache.get(case_id)
        if cached is not None:
            return cached
        result_id = self._result_id(case_id)
        try:
            response = self._client.get(
                f"/api/v1/reconciliation/results/{result_id}/evidence"
            )
            if response.status_code == 404:
                raise CaseNotFoundError(case_id)
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as error:
            raise JavaPlatformUnavailableError("Java evidence API is unavailable") from error
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise JavaPlatformUnavailableError("Java evidence API returned invalid JSON")
        self._evidence_cache[case_id] = payload
        return payload

    @staticmethod
    def _instant(value: Any) -> datetime:
        if not isinstance(value, str):
            raise JavaPlatformUnavailableError("Java evidence timestamp is invalid")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def get_case(self, case_id: str) -> ReconciliationCase:
        evidence = self._get_evidence(case_id)
        view = evidence.get("caseView")
        if not isinstance(view, Mapping):
            raise JavaPlatformUnavailableError("Java evidence is missing caseView")
        try:
            result_type = ResultType(str(view["resultType"]))
            status = str(view.get("resolutionStatus", "OPEN"))
            case_status = CaseStatus.RESOLVED if status == "RESOLVED" else CaseStatus.OPEN
            return ReconciliationCase(
                id=0,
                case_id=case_id,
                channel=str(self._settings.java_case_id_map[case_id].get("channel", "UNKNOWN")),
                result_type=result_type,
                status=case_status,
                rule_version=str(
                    self._settings.java_case_id_map[case_id].get(
                        "rule_version", "JAVA_RULE_VERSION_UNAVAILABLE"
                    )
                ),
                channel_transaction_id=view.get("channelTransactionId"),
                channel_amount_cents=view.get("channelAmountCents"),
                occurred_at=self._instant(view["occurredAt"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise JavaPlatformUnavailableError("Java evidence caseView is invalid") from error

    def find_payments(self, case: ReconciliationCase) -> list[Payment]:
        payment = self._get_evidence(case.case_id).get("payment")
        if not isinstance(payment, Mapping):
            return []
        return [
            Payment(
                id=0,
                payment_id=str(payment["id"]),
                channel_transaction_id=payment.get("channelReference"),
                amount_cents=int(payment["amountCents"]),
                status=str(payment["status"]),
                occurred_at=self._instant(payment["occurredAt"]),
            )
        ]

    def find_ledger_entries(self, case: ReconciliationCase) -> list[LedgerEntry]:
        transaction = self._get_evidence(case.case_id).get("ledgerTransaction")
        if not isinstance(transaction, Mapping):
            return []
        entries = transaction.get("entries", [])
        if not isinstance(entries, list):
            raise JavaPlatformUnavailableError("Java ledger entries are invalid")
        result: list[LedgerEntry] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise JavaPlatformUnavailableError("Java ledger entry is invalid")
            result.append(
                LedgerEntry(
                    id=0,
                    business_reference=str(transaction.get("businessReference", "")),
                    account_code=str(entry.get("accountReference", "UNKNOWN")),
                    direction=str(entry["side"]),
                    amount_cents=int(entry["amountCents"]),
                    posted_at=self._instant(entry["createdAt"]),
                )
            )
        return result

    def get_audit_timeline(self, case_id: str) -> list[AuditEvent]:
        events = self._get_evidence(case_id).get("auditEvents", [])
        if not isinstance(events, list):
            raise JavaPlatformUnavailableError("Java audit events are invalid")
        return [
            AuditEvent(
                id=0,
                actor=str(event["actor"]),
                action=str(event["action"]),
                result=str(event["outcome"]),
                business_reference=case_id,
                occurred_at=self._instant(event["occurredAt"]),
            )
            for event in events
            if isinstance(event, Mapping)
        ]


def java_repository_provider(settings: DatabaseSettings):
    """Create a repository provider that opens one short-lived HTTP client."""

    from contextlib import contextmanager

    @contextmanager
    def provider():
        headers = {}
        api_key = settings.java_platform_read_api_key.get_secret_value().strip()
        if api_key:
            headers["X-Read-Api-Key"] = api_key
        with httpx.Client(
            base_url=settings.java_platform_base_url.rstrip("/"),
            headers=headers,
            timeout=10.0,
        ) as client:
            yield JavaPlatformRepository(client, settings)

    return provider
