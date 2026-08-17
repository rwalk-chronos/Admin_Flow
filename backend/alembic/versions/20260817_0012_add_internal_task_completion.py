"""Add internal task completion audit metadata.

Revision ID: 20260817_0012
Revises: 20260816_0011
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260817_0012"
down_revision: str | None = "20260816_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "internal_tasks",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "internal_tasks",
        sa.Column("completed_by", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "internal_tasks",
        sa.Column("completion_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("internal_tasks", "completion_note")
    op.drop_column("internal_tasks", "completed_by")
    op.drop_column("internal_tasks", "completed_at")
