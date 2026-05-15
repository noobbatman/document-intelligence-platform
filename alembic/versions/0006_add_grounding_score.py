"""Add draft grounding score.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "draft_outputs",
        sa.Column("overall_grounding_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("draft_outputs", "overall_grounding_score")
