import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.document_structured_extractor import StructuredExtractionProviderError, validate_extracted_data
from app.models import ActionExecution, ActionPlan, IntakeArtifact, InternalTask, WorkItem, WorkItemReview
from app.schemas import ActionExecutionResponse, ActionPlanResponse, ActionPlanRevise, InternalTaskResponse, StructuredFieldDefinition

router = APIRouter(tags=["actions"])
SessionDependency = Annotated[Session, Depends(get_session)]


def build_internal_task_plan(session: Session, item: WorkItem, facts: dict, *, revision: int) -> ActionPlan:
    artifacts = list(session.scalars(select(IntakeArtifact.id).where(IntakeArtifact.intake_event_id == item.intake_event_id).order_by(IntakeArtifact.created_at, IntakeArtifact.id)))
    queue = "accounts_payable" if item.work_type == "invoice_review" else "office_review"
    queue_label = "Accounts Payable" if queue == "accounts_payable" else "Office Review"
    due_at = facts.get("due_date")
    task_title = f"Review {item.title}"
    due_text = f", due {due_at}" if due_at else ""
    return ActionPlan(
        work_item_id=item.id, work_item_state=item.current_state, work_item_version=item.version,
        workflow_definition_id=item.workflow_definition_id, workflow_definition_version=item.workflow_definition.version,
        intake_event_id=item.intake_event_id, revision=revision, action_type="create_internal_task",
        facts_snapshot=dict(facts), destination={"queue": queue, "role": "office_manager"},
        payload={"task": {"title": task_title, "due_at": due_at}, "facts": dict(facts), "attachments": ["original_source_artifact"]},
        source_artifact_ids=[str(value) for value in artifacts], action_title=f"Create {queue_label} task",
        action_description=f"Create an internal {queue_label} task for the Office Manager{due_text}, with the reviewed information and original document attached.",
        approval_label="Approve & Create Task", external_effect="No external message will be sent.",
    )


def execute_internal_task(session: Session, plan: ActionPlan) -> ActionExecution:
    existing = session.scalar(select(ActionExecution).where(ActionExecution.action_plan_id == plan.id))
    if existing is not None:
        return existing
    now = datetime.now(timezone.utc)
    execution = ActionExecution(action_plan_id=plan.id, idempotency_key=f"action-plan:{plan.id}", status="succeeded", result={}, completed_at=now)
    session.add(execution); session.flush()
    task_data = plan.payload["task"]
    due_at = datetime.fromisoformat(task_data["due_at"]) if task_data.get("due_at") else None
    task = InternalTask(action_execution_id=execution.id, work_item_id=plan.work_item_id, title=task_data["title"], queue=plan.destination["queue"], owner_role=plan.destination.get("role"), due_at=due_at, facts_snapshot=dict(plan.facts_snapshot), source_artifact_ids=list(plan.source_artifact_ids))
    session.add(task); session.flush()
    execution.result = {"internal_task_id": str(task.id), "status": "created"}
    return execution


@router.get("/work-items/{work_item_id}/action-plans", response_model=list[ActionPlanResponse])
def list_action_plans(work_item_id: uuid.UUID, session: SessionDependency):
    if session.get(WorkItem, work_item_id) is None: raise HTTPException(404, "WorkItem not found")
    return list(session.scalars(select(ActionPlan).where(ActionPlan.work_item_id == work_item_id).order_by(ActionPlan.revision)))


@router.get("/action-plans/{plan_id}", response_model=ActionPlanResponse)
def get_action_plan(plan_id: uuid.UUID, session: SessionDependency):
    plan = session.get(ActionPlan, plan_id)
    if plan is None: raise HTTPException(404, "Action Plan not found")
    return plan


@router.post("/work-item-reviews/{review_id}/action-plan", response_model=ActionPlanResponse)
def revise_action_plan(review_id: uuid.UUID, request: ActionPlanRevise, session: SessionDependency):
    review = session.get(WorkItemReview, review_id)
    if review is None: raise HTTPException(404, "WorkItem review not found")
    item = session.scalar(select(WorkItem).where(WorkItem.id == review.work_item_id).with_for_update())
    if review.status != "pending" or item.current_state != request.expected_work_item_state or item.version != request.expected_work_item_version:
        session.rollback(); raise HTTPException(409, "WorkItem review is stale")
    data = request.reviewed_data
    if item.document_structured_extraction is not None:
        fields = [StructuredFieldDefinition.model_validate(value) for value in item.document_structured_extraction.field_schema]
        try: data = validate_extracted_data(fields, data)
        except StructuredExtractionProviderError as exc: raise HTTPException(422, "reviewed_data does not match the structured extraction field schema") from exc
    current = session.scalar(select(ActionPlan).where(ActionPlan.work_item_id == item.id, ActionPlan.superseded_at.is_(None)).order_by(ActionPlan.revision.desc()))
    if current and current.facts_snapshot == data: return current
    revision = 1 if current is None else current.revision + 1
    if current:
        current.superseded_at = datetime.now(timezone.utc); current.superseded_reason = "Reviewed facts changed"
    plan = build_internal_task_plan(session, item, data, revision=revision)
    session.add(plan); session.commit(); session.refresh(plan); return plan


@router.get("/action-plans/{plan_id}/executions", response_model=list[ActionExecutionResponse])
def list_executions(plan_id: uuid.UUID, session: SessionDependency):
    if session.get(ActionPlan, plan_id) is None: raise HTTPException(404, "Action Plan not found")
    return list(session.scalars(select(ActionExecution).where(ActionExecution.action_plan_id == plan_id)))


@router.get("/internal-tasks", response_model=list[InternalTaskResponse])
def list_internal_tasks(session: SessionDependency):
    return list(session.scalars(select(InternalTask).order_by(InternalTask.created_at.desc(), InternalTask.id.desc())))


@router.get("/internal-tasks/{task_id}", response_model=InternalTaskResponse)
def get_internal_task(task_id: uuid.UUID, session: SessionDependency):
    task = session.get(InternalTask, task_id)
    if task is None: raise HTTPException(404, "Internal task not found")
    return task
