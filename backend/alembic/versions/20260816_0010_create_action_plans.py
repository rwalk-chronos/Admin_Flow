"""create action plans and internal task execution

Revision ID: 20260816_0010
Revises: 20260815_0009
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0010"
down_revision: str | None = "20260815_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table("action_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("work_items.id"), nullable=False),
        sa.Column("work_item_state", sa.String(64), nullable=False), sa.Column("work_item_version", sa.Integer(), nullable=False),
        sa.Column("workflow_definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workflow_definitions.id"), nullable=False),
        sa.Column("workflow_definition_version", sa.Integer(), nullable=False),
        sa.Column("intake_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intake_events.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("facts_snapshot", postgresql.JSONB(), nullable=False), sa.Column("destination", postgresql.JSONB(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("source_artifact_ids", postgresql.JSONB(), nullable=False),
        sa.Column("action_title", sa.String(255), nullable=False), sa.Column("action_description", sa.Text(), nullable=False),
        sa.Column("approval_label", sa.String(100), nullable=False), sa.Column("external_effect", sa.String(255), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True)), sa.Column("superseded_reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("revision >= 1", name="ck_action_plans_revision"),
        sa.CheckConstraint("action_type IN ('create_internal_task')", name="ck_action_plans_type"),
        sa.UniqueConstraint("work_item_id", "revision", name="uq_action_plans_item_revision"))
    op.create_index("ix_action_plans_work_item_id", "action_plans", ["work_item_id"])
    op.add_column("work_item_reviews", sa.Column("authorized_action_plan_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key("fk_reviews_authorized_action_plan", "work_item_reviews", "action_plans", ["authorized_action_plan_id"], ["id"])
    op.create_index("ix_work_item_reviews_authorized_action_plan_id", "work_item_reviews", ["authorized_action_plan_id"])
    op.create_table("action_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("action_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("action_plans.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False), sa.Column("error_message", sa.String(500)),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_action_executions_status"), sa.UniqueConstraint("action_plan_id", name="uq_action_executions_plan"), sa.UniqueConstraint("idempotency_key", name="uq_action_executions_idempotency"))
    op.create_index("ix_action_executions_action_plan_id", "action_executions", ["action_plan_id"])
    op.create_table("internal_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("action_execution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("action_executions.id"), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("work_items.id"), nullable=False), sa.Column("title", sa.String(500), nullable=False),
        sa.Column("queue", sa.String(100), nullable=False), sa.Column("owner_role", sa.String(100)), sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("facts_snapshot", postgresql.JSONB(), nullable=False), sa.Column("source_artifact_ids", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('open', 'completed')", name="ck_internal_tasks_status"), sa.UniqueConstraint("action_execution_id", name="uq_internal_tasks_execution"))
    op.create_index("ix_internal_tasks_action_execution_id", "internal_tasks", ["action_execution_id"])
    op.create_index("ix_internal_tasks_work_item_id", "internal_tasks", ["work_item_id"])

def downgrade() -> None:
    op.drop_table("internal_tasks"); op.drop_table("action_executions")
    op.drop_index("ix_work_item_reviews_authorized_action_plan_id", table_name="work_item_reviews")
    op.drop_constraint("fk_reviews_authorized_action_plan", "work_item_reviews", type_="foreignkey")
    op.drop_column("work_item_reviews", "authorized_action_plan_id"); op.drop_table("action_plans")
