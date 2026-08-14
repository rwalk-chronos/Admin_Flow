"""Add lineage for derived document extractions."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260814_0005"
down_revision: str | Sequence[str] | None = "20260814_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_extractions",
        sa.Column(
            "source_extraction_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_document_extractions_source_extraction",
        "document_extractions",
        "document_extractions",
        ["source_extraction_id"],
        ["id"],
    )
    op.create_index(
        "ix_document_extractions_source_extraction_id",
        "document_extractions",
        ["source_extraction_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_extractions_source_extraction_id",
        table_name="document_extractions",
    )
    op.drop_constraint(
        "fk_document_extractions_source_extraction",
        "document_extractions",
        type_="foreignkey",
    )
    op.drop_column("document_extractions", "source_extraction_id")
