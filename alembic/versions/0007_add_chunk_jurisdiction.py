"""Add jurisdiction tags to document chunks.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-15 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("jurisdiction", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_document_chunks_document_jurisdiction",
        "document_chunks",
        ["document_id", "jurisdiction"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_document_jurisdiction", "document_chunks")
    op.drop_column("document_chunks", "jurisdiction")
