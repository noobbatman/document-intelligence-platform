"""Add page evidence to review_tasks, CorrectionRecord table, validation_results to extraction_results,
   tenant_id to audit_logs, UTC-aware timestamps.

Revision ID: 0002
Revises: 0001
Create Date: 2025-02-01 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # review_tasks — page-level evidence columns
    with op.batch_alter_table("review_tasks") as batch:
        batch.add_column(sa.Column("page_number", sa.Integer, nullable=True))
        batch.add_column(sa.Column("bbox", sa.JSON, nullable=True))
        batch.add_column(sa.Column("validation_reason", sa.Text, nullable=True))

    # extraction_results — store validation results
    with op.batch_alter_table("extraction_results") as batch:
        batch.add_column(sa.Column("validation_results", sa.JSON, nullable=False, server_default="[]"))

    # audit_logs — tenant scoping
    with op.batch_alter_table("audit_logs") as batch:
        batch.add_column(sa.Column("tenant_id", sa.String(80), nullable=True))
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])

    # correction_records — active learning table
    op.create_table(
        "correction_records",
        sa.Column("id",               sa.String(36),  primary_key=True),
        sa.Column("document_id",      sa.String(36),  sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("document_type",    sa.String(80),  nullable=False),
        sa.Column("field_name",       sa.String(255), nullable=False),
        sa.Column("original_value",   sa.Text,        nullable=True),
        sa.Column("corrected_value",  sa.Text,        nullable=True),
        sa.Column("ocr_snippet",      sa.Text,        nullable=True),
        sa.Column("reviewer_name",    sa.String(255), nullable=False),
        sa.Column("pipeline_version", sa.String(40),  nullable=False),
        sa.Column("created_at",       sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_corrections_document_type", "correction_records", ["document_type"])


def downgrade() -> None:
    op.drop_index("ix_corrections_document_type", "correction_records")
    op.drop_table("correction_records")
    op.drop_index("ix_audit_logs_tenant_id", "audit_logs")
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_column("tenant_id")
    with op.batch_alter_table("extraction_results") as batch:
        batch.drop_column("validation_results")
    with op.batch_alter_table("review_tasks") as batch:
        batch.drop_column("validation_reason")
        batch.drop_column("bbox")
        batch.drop_column("page_number")
