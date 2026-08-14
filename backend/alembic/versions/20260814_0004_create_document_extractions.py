"""Create deterministic document extraction records."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260814_0004"
down_revision: str | Sequence[str] | None = "20260814_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "intake_artifact_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "extraction_method",
            sa.String(length=50),
            server_default="pdf_text",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column(
            "page_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('extracted', 'partial', 'needs_ocr', "
            "'password_required', 'failed')",
            name="ck_document_extractions_status",
        ),
        sa.CheckConstraint(
            "page_count >= 0", name="ck_document_extractions_page_count"
        ),
        sa.CheckConstraint(
            "character_count >= 0",
            name="ck_document_extractions_character_count",
        ),
        sa.ForeignKeyConstraint(
            ["intake_artifact_id"],
            ["intake_artifacts.id"],
            name="fk_document_extractions_intake_artifact_id_intake_artifacts",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_extractions_intake_artifact_id",
        "document_extractions",
        ["intake_artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_extractions_intake_artifact_id",
        table_name="document_extractions",
    )
    op.drop_table("document_extractions")
