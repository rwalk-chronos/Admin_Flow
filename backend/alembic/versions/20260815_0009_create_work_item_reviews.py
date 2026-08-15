"""Create human WorkItem review records."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0009"
down_revision: str | Sequence[str] | None = "20260814_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_item_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("reviewer", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reviewed_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_work_item_reviews_status"),
        sa.CheckConstraint("work_item_version >= 1", name="ck_work_item_reviews_version"),
        sa.CheckConstraint("(status = 'pending' AND reviewer IS NULL AND resolved_at IS NULL AND reviewed_data IS NULL) OR (status IN ('approved', 'rejected') AND reviewer IS NOT NULL AND resolved_at IS NOT NULL)", name="ck_work_item_reviews_resolution"),
        sa.CheckConstraint("status != 'rejected' OR reviewed_data IS NULL", name="ck_work_item_reviews_rejected_data"),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_items.id"], name="fk_work_item_reviews_item"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_item_id", "work_item_version", name="uq_work_item_reviews_item_version"),
    )
    op.create_index("ix_work_item_reviews_item_id", "work_item_reviews", ["work_item_id"], unique=False)
    op.create_index("ix_work_item_reviews_status_created", "work_item_reviews", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("work_item_reviews")
