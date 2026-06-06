"""Add correlation_id to audit_logs; add failed_webhook_events dead-letter table.

Revision ID: 0004
Revises: 0003
Create Date: 2025-04-01 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.add_column(sa.Column("correlation_id", sa.String(64), nullable=True))
    op.create_index("ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"])

    op.create_table(
        "failed_webhook_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("webhook_id", sa.String(36), nullable=True),
        sa.Column("webhook_url", sa.String(2048), nullable=False),
        sa.Column("event", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("replayed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_failed_webhook_event", "failed_webhook_events", ["event"])
    op.create_index("ix_failed_webhook_created", "failed_webhook_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_failed_webhook_created", "failed_webhook_events")
    op.drop_index("ix_failed_webhook_event", "failed_webhook_events")
    op.drop_table("failed_webhook_events")
    op.drop_index("ix_audit_logs_correlation_id", "audit_logs")
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_column("correlation_id")
