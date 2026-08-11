"""Database models for reconciliation data."""

import enum
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CaseStatus(str, enum.Enum):
    """Lifecycle status of a reconciliation case."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ResultType(str, enum.Enum):
    """Type of reconciliation discrepancy."""

    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    CHANNEL_ONLY = "CHANNEL_ONLY"
    INTERNAL_ONLY = "INTERNAL_ONLY"


class ResolutionCode(str, enum.Enum):
    """Confirmed disposition of a reconciliation case."""

    INTERNAL_CONFIRMED = "INTERNAL_CONFIRMED"
    CHANNEL_CONFIRMED = "CHANNEL_CONFIRMED"
    IGNORED_TEST_DATA = "IGNORED_TEST_DATA"
    OTHER = "OTHER"


class Base(DeclarativeBase):
    """Base class for reconciliation models."""


class ReconciliationCase(Base):
    """A discrepancy produced by deterministic reconciliation."""

    __tablename__ = "reconciliation_case"
    __table_args__ = (
        CheckConstraint(
            "result_type IN ('AMOUNT_MISMATCH', 'CHANNEL_ONLY', 'INTERNAL_ONLY')",
            name="result_type",
        ),
        CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="case_status"),
        Index(
            "ix_reconciliation_case_channel_transaction_id", "channel_transaction_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    result_type: Mapped[ResultType] = mapped_column(
        Enum(ResultType, name="result_type", native_enum=False),
        nullable=False,
    )
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, name="case_status", native_enum=False),
        nullable=False,
    )
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_transaction_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    channel_amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Payment(Base):
    """Internal payment record used for reconciliation."""

    __tablename__ = "payment"
    __table_args__ = (
        Index("ix_payment_channel_transaction_id", "channel_transaction_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    channel_transaction_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class LedgerEntry(Base):
    """Accounting entry associated with a payment."""

    __tablename__ = "ledger_entry"
    __table_args__ = (
        Index("ix_ledger_entry_business_reference", "business_reference"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    account_code: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    """Auditable event emitted by the ledger domain."""

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_business_reference", "business_reference"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(128), nullable=False)
    business_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RuleChunk(Base):
    """Versioned reconciliation rule text and its vector embedding."""

    __tablename__ = "rule_chunk"
    __table_args__ = (
        Index("ix_rule_chunk_channel_rule_version", "channel", "rule_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    section: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)


class CaseResolution(Base):
    """The single confirmed resolution for a reconciliation case."""

    __tablename__ = "case_resolution"
    __table_args__ = (
        CheckConstraint(
            "resolution_code IN ('INTERNAL_CONFIRMED', 'CHANNEL_CONFIRMED', "
            "'IGNORED_TEST_DATA', 'OTHER')",
            name="resolution_code",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    resolution_code: Mapped[ResolutionCode] = mapped_column(
        Enum(
            ResolutionCode,
            name="resolution_code",
            native_enum=False,
        ),
        nullable=False,
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    agent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ConfirmationToken(Base):
    """One-time confirmation token for a proposed case resolution."""

    __tablename__ = "confirmation_token"
    __table_args__ = (
        CheckConstraint(
            "resolution_code IN ('INTERNAL_CONFIRMED', 'CHANNEL_CONFIRMED', "
            "'IGNORED_TEST_DATA', 'OTHER')",
            name="resolution_code",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_code: Mapped[ResolutionCode] = mapped_column(
        Enum(
            ResolutionCode,
            name="resolution_code",
            native_enum=False,
        ),
        nullable=False,
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentRun(Base):
    """Persisted record of an agent investigation."""

    __tablename__ = "agent_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
