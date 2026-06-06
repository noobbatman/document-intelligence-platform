"""Initial schema — documents, extraction_results, review tasks/decisions, audit_logs, webhooks.

Revision ID: 0001
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("stored_path", sa.String(1000), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="uploaded"),
        sa.Column("document_type", sa.String(80), nullable=True),
        sa.Column("pipeline_version", sa.String(40), nullable=False),
        sa.Column("classifier_confidence", sa.Float, nullable=True),
        sa.Column("document_confidence", sa.Float, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("tenant_id", sa.String(80), nullable=True),
        sa.Column("tags", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_document_type", "documents", ["document_type"])
    op.create_index("ix_documents_created_at", "documents", ["created_at"])
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_deleted_at", "documents", ["deleted_at"])

    op.create_table(
        "extraction_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), unique=True, nullable=False),
        sa.Column("ocr_text", sa.Text, nullable=False, server_default=""),
        sa.Column("raw_payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("normalized_payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("export_payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("ocr_metadata", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("extraction_metadata", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "review_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("proposed_value", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("original_value", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("source_snippet", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_review_tasks_status", "review_tasks", ["status"])

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_task_id", sa.String(36), sa.ForeignKey("review_tasks.id"), nullable=False),
        sa.Column("reviewer_name", sa.String(255), nullable=False),
        sa.Column("corrected_value", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False, server_default="system"),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])

    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("event", sa.String(80), nullable=False),
        sa.Column("secret", sa.String(255), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="active"),
        sa.Column("failure_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_triggered_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("url", "event", name="uq_webhook_url_event"),
    )
    op.create_index("ix_webhooks_status", "webhooks", ["status"])


def downgrade() -> None:
    op.drop_table("webhooks")
    op.drop_table("audit_logs")
    op.drop_table("review_decisions")
    op.drop_table("review_tasks")
    op.drop_table("extraction_results")
    op.drop_table("documents")
