"""Create workflow definitions, WorkItems, and transition history."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0008"
down_revision: str | Sequence[str] | None = "20260814_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("states", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("initial_state", sa.String(length=64), nullable=False),
        sa.Column("transitions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_workflow_definitions_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_workflow_definitions_name_version"),
    )
    op.create_table(
        "work_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("intake_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_structured_extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("work_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_state", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_work_items_version"),
        sa.ForeignKeyConstraint(["workflow_definition_id"], ["workflow_definitions.id"], name="fk_work_items_workflow"),
        sa.ForeignKeyConstraint(["intake_event_id"], ["intake_events.id"], name="fk_work_items_intake_event"),
        sa.ForeignKeyConstraint(["document_structured_extraction_id"], ["document_structured_extractions.id"], name="fk_work_items_structured_extraction"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "work_item_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=64), nullable=True),
        sa.Column("to_state", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_work_item_transitions_version"),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_items.id"], name="fk_work_item_transitions_item"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_item_id", "version", name="uq_work_item_transitions_item_version"),
    )
    for name, table, columns in [
        ("ix_work_items_workflow_id", "work_items", ["workflow_definition_id"]),
        ("ix_work_items_intake_event_id", "work_items", ["intake_event_id"]),
        ("ix_work_items_structured_id", "work_items", ["document_structured_extraction_id"]),
        ("ix_work_items_current_state", "work_items", ["current_state"]),
        ("ix_work_item_transitions_item_id", "work_item_transitions", ["work_item_id"]),
    ]:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    op.drop_table("work_item_transitions")
    op.drop_table("work_items")
    op.drop_table("workflow_definitions")
