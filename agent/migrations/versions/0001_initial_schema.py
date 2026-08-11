"""Create the reconciliation agent schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create pgvector support and reconciliation tables."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "reconciliation_case",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column(
            "result_type",
            sa.Enum(
                "AMOUNT_MISMATCH",
                "CHANNEL_ONLY",
                "INTERNAL_ONLY",
                name="result_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "RESOLVED",
                name="case_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("channel_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("channel_amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "result_type IN ('AMOUNT_MISMATCH', 'CHANNEL_ONLY', 'INTERNAL_ONLY')",
            name="result_type",
        ),
        sa.CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="case_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )
    op.create_index(
        "ix_reconciliation_case_channel_transaction_id",
        "reconciliation_case",
        ["channel_transaction_id"],
    )

    op.create_table(
        "payment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.String(length=64), nullable=False),
        sa.Column("channel_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id"),
    )
    op.create_index(
        "ix_payment_channel_transaction_id",
        "payment",
        ["channel_transaction_id"],
    )

    op.create_table(
        "ledger_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_reference", sa.String(length=128), nullable=False),
        sa.Column("account_code", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ledger_entry_business_reference",
        "ledger_entry",
        ["business_reference"],
    )

    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("result", sa.String(length=128), nullable=False),
        sa.Column("business_reference", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_event_business_reference",
        "audit_event",
        ["business_reference"],
    )

    op.create_table(
        "rule_chunk",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.String(length=128), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("section", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id"),
    )
    op.create_index(
        "ix_rule_chunk_channel_rule_version",
        "rule_chunk",
        ["channel", "rule_version"],
    )

    op.create_table(
        "agent_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )

    op.create_table(
        "case_resolution",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column(
            "resolution_code",
            sa.Enum(
                "INTERNAL_CONFIRMED",
                "CHANNEL_CONFIRMED",
                "IGNORED_TEST_DATA",
                "OTHER",
                name="resolution_code",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=128), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_run_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "resolution_code IN ('INTERNAL_CONFIRMED', 'CHANNEL_CONFIRMED', "
            "'IGNORED_TEST_DATA', 'OTHER')",
            name="resolution_code",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )

    op.create_table(
        "confirmation_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column(
            "resolution_code",
            sa.Enum(
                "INTERNAL_CONFIRMED",
                "CHANNEL_CONFIRMED",
                "IGNORED_TEST_DATA",
                "OTHER",
                name="resolution_code",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "resolution_code IN ('INTERNAL_CONFIRMED', 'CHANNEL_CONFIRMED', "
            "'IGNORED_TEST_DATA', 'OTHER')",
            name="resolution_code",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )


def downgrade() -> None:
    """Drop reconciliation tables while retaining the vector extension."""
    op.drop_table("confirmation_token")
    op.drop_table("case_resolution")
    op.drop_table("agent_run")
    op.drop_table("rule_chunk")
    op.drop_table("audit_event")
    op.drop_table("ledger_entry")
    op.drop_table("payment")
    op.drop_table("reconciliation_case")
