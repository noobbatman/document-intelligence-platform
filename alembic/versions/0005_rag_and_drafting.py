"""Add RAG chunks, draft outputs, edit capture, and learned preferences.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-13 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def _embedding_type(dialect_name: str):
    if dialect_name == "postgresql":
        from pgvector.sqlalchemy import Vector

        return Vector(768)
    return sa.JSON()


def _text_list_type(dialect_name: str):
    if dialect_name == "postgresql":
        return sa.ARRAY(sa.Text())
    return sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    embedding_type = _embedding_type(dialect_name)
    text_list_type = _text_list_type(dialect_name)

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("section_header", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index(
        "ix_document_chunks_document_chunk",
        "document_chunks",
        ["document_id", "chunk_index"],
    )

    op.create_table(
        "draft_outputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("tenant_id", sa.String(80), nullable=True),
        sa.Column("draft_type", sa.String(60), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="generating"),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_chunk_ids", text_list_type, nullable=False),
        sa.Column("generation_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_id", sa.String(120), nullable=True),
        sa.Column("preferences_applied", text_list_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_draft_outputs_document_id", "draft_outputs", ["document_id"])
    op.create_index("ix_draft_outputs_tenant_id", "draft_outputs", ["tenant_id"])
    op.create_index("ix_draft_outputs_status", "draft_outputs", ["status"])

    op.create_table(
        "draft_edits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("draft_outputs.id"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("tenant_id", sa.String(80), nullable=True),
        sa.Column("section_key", sa.String(120), nullable=False),
        sa.Column("original_content", sa.Text(), nullable=False),
        sa.Column("edited_content", sa.Text(), nullable=False),
        sa.Column("reviewer_name", sa.String(255), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_draft_edits_draft_id", "draft_edits", ["draft_id"])
    op.create_index("ix_draft_edits_document_id", "draft_edits", ["document_id"])
    op.create_index("ix_draft_edits_processed", "draft_edits", ["processed"])

    op.create_table(
        "draft_preferences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=True),
        sa.Column("document_type", sa.String(80), nullable=False),
        sa.Column("preference_text", sa.Text(), nullable=False),
        sa.Column("source_edit_id", sa.String(36), sa.ForeignKey("draft_edits.id"), nullable=True),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("application_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effectiveness_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_draft_preferences_tenant_type",
        "draft_preferences",
        ["tenant_id", "document_type"],
    )
    op.create_index(
        "ix_draft_preferences_source_edit",
        "draft_preferences",
        ["source_edit_id"],
    )

    if dialect_name == "postgresql":
        op.execute(
            "CREATE INDEX ix_document_chunks_embedding_ivfflat "
            "ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )
        op.execute(
            "CREATE INDEX ix_draft_preferences_embedding_ivfflat "
            "ON draft_preferences USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_draft_preferences_embedding_ivfflat")
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_ivfflat")

    op.drop_index("ix_draft_preferences_source_edit", "draft_preferences")
    op.drop_index("ix_draft_preferences_tenant_type", "draft_preferences")
    op.drop_table("draft_preferences")
    op.drop_index("ix_draft_edits_processed", "draft_edits")
    op.drop_index("ix_draft_edits_document_id", "draft_edits")
    op.drop_index("ix_draft_edits_draft_id", "draft_edits")
    op.drop_table("draft_edits")
    op.drop_index("ix_draft_outputs_status", "draft_outputs")
    op.drop_index("ix_draft_outputs_tenant_id", "draft_outputs")
    op.drop_index("ix_draft_outputs_document_id", "draft_outputs")
    op.drop_table("draft_outputs")
    op.drop_index("ix_document_chunks_document_chunk", "document_chunks")
    op.drop_index("ix_document_chunks_document_id", "document_chunks")
    op.drop_table("document_chunks")
