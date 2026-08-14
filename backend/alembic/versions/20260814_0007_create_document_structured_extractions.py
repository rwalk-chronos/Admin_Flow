"""Create immutable structured document extraction records."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0007"
down_revision: str | Sequence[str] | None = "20260814_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_structured_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_extraction_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "document_classification_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "field_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "extracted_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("provider_name", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_extraction_id"],
            ["document_extractions.id"],
            name="fk_structured_extractions_extraction",
        ),
        sa.ForeignKeyConstraint(
            ["document_classification_id"],
            ["document_classifications.id"],
            name="fk_structured_extractions_classification",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_structured_extractions_extraction_id",
        "document_structured_extractions",
        ["document_extraction_id"],
        unique=False,
    )
    op.create_index(
        "ix_structured_extractions_classification_id",
        "document_structured_extractions",
        ["document_classification_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_structured_extractions_classification_id",
        table_name="document_structured_extractions",
    )
    op.drop_index(
        "ix_structured_extractions_extraction_id",
        table_name="document_structured_extractions",
    )
    op.drop_table("document_structured_extractions")
