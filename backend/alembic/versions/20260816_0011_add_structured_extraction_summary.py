"""Add immutable structured extraction summary.

Revision ID: 20260816_0011
Revises: 20260816_0010
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260816_0011"
down_revision: str | None = "20260816_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_structured_extractions",
        sa.Column("summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_structured_extractions", "summary")
