"""Create the domain-neutral intake artifacts table."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260814_0003"
down_revision: str | Sequence[str] | None = "20260814_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intake_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "intake_event_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("original_filename", sa.String(length=500), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "byte_size >= 0", name="ck_intake_artifacts_byte_size"
        ),
        sa.CheckConstraint(
            "length(sha256) = 64", name="ck_intake_artifacts_sha256_length"
        ),
        sa.ForeignKeyConstraint(
            ["intake_event_id"],
            ["intake_events.id"],
            name="fk_intake_artifacts_intake_event_id_intake_events",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "storage_key", name="uq_intake_artifacts_storage_key"
        ),
    )
    op.create_index(
        "ix_intake_artifacts_intake_event_id",
        "intake_artifacts",
        ["intake_event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_intake_artifacts_intake_event_id", table_name="intake_artifacts"
    )
    op.drop_table("intake_artifacts")
