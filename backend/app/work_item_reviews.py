import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.document_structured_extractor import (
    StructuredExtractionProviderError,
    validate_extracted_data,
)
from app.models import (
    ActionPlan,
    WorkItem,
    WorkItemReview,
    WorkItemTransition,
    WorkflowDefinition,
)
from app.action_plans import execute_internal_task
from app.schemas import (
    StructuredFieldDefinition,
    WorkItemReviewResolve,
    WorkItemReviewResponse,
)
from app.workflow_engine import WorkflowTransitionConflict, apply_transition

router = APIRouter(tags=["work-item-reviews"])
SessionDependency = Annotated[Session, Depends(get_session)]


def state_requires_review(workflow: WorkflowDefinition, state: str) -> bool:
    return bool(
        next(item for item in workflow.states if item["name"] == state).get(
            "review_required", False
        )
    )


def create_pending_review_if_required(
    session: Session, work_item: WorkItem, workflow: WorkflowDefinition
) -> WorkItemReview | None:
    if not state_requires_review(workflow, work_item.current_state):
        return None
    review = WorkItemReview(
        work_item_id=work_item.id,
        work_item_version=work_item.version,
        state=work_item.current_state,
        status="pending",
    )
    session.add(review)
    return review


def _response(review: WorkItemReview) -> dict:
    item = review.work_item
    return {
        "id": review.id,
        "work_item_id": review.work_item_id,
        "work_item_version": review.work_item_version,
        "state": review.state,
        "status": review.status,
        "reviewer": review.reviewer,
        "notes": review.notes,
        "reviewed_data": review.reviewed_data,
        "created_at": review.created_at,
        "resolved_at": review.resolved_at,
        "work_type": item.work_type,
        "title": item.title,
        "current_state": item.current_state,
        "current_version": item.version,
        "work_item_data": item.data,
        "authorized_action_plan_id": review.authorized_action_plan_id,
    }


@router.get("/work-item-reviews", response_model=list[WorkItemReviewResponse])
def list_reviews(
    session: SessionDependency,
    review_status: Literal["pending", "approved", "rejected"] = Query(
        default="pending", alias="status"
    ),
) -> list[dict]:
    statement = (
        select(WorkItemReview)
        .where(WorkItemReview.status == review_status)
        .order_by(WorkItemReview.created_at.asc(), WorkItemReview.id.asc())
    )
    return [_response(review) for review in session.scalars(statement)]


@router.get(
    "/work-item-reviews/{review_id}", response_model=WorkItemReviewResponse
)
def get_review(review_id: uuid.UUID, session: SessionDependency) -> dict:
    review = session.get(WorkItemReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="WorkItem review not found")
    return _response(review)


@router.get(
    "/work-items/{work_item_id}/reviews",
    response_model=list[WorkItemReviewResponse],
)
def list_work_item_reviews(
    work_item_id: uuid.UUID, session: SessionDependency
) -> list[dict]:
    if session.get(WorkItem, work_item_id) is None:
        raise HTTPException(status_code=404, detail="WorkItem not found")
    statement = (
        select(WorkItemReview)
        .where(WorkItemReview.work_item_id == work_item_id)
        .order_by(WorkItemReview.created_at.asc(), WorkItemReview.id.asc())
    )
    return [_response(review) for review in session.scalars(statement)]


@router.post(
    "/work-item-reviews/{review_id}/resolve",
    response_model=WorkItemReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def resolve_review(
    review_id: uuid.UUID,
    request: WorkItemReviewResolve,
    session: SessionDependency,
) -> dict:
    existing = session.get(WorkItemReview, review_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="WorkItem review not found")

    item_statement = (
        select(WorkItem)
        .where(WorkItem.id == existing.work_item_id)
        .with_for_update()
    )
    item = session.scalar(item_statement)
    review_statement = (
        select(WorkItemReview)
        .where(WorkItemReview.id == review_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    review = session.scalar(review_statement)
    if review.status != "pending":
        session.rollback()
        raise HTTPException(status_code=409, detail="WorkItem review is already resolved")
    if review.work_item_version != item.version or review.state != item.current_state:
        session.rollback()
        raise HTTPException(status_code=409, detail="WorkItem review is stale")
    if request.expected_work_item_state != item.current_state:
        session.rollback()
        raise HTTPException(status_code=409, detail="WorkItem state has changed")
    if request.expected_work_item_version != item.version:
        session.rollback()
        raise HTTPException(status_code=409, detail="WorkItem version has changed")

    workflow = session.get(WorkflowDefinition, item.workflow_definition_id)
    if not state_requires_review(workflow, item.current_state):
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Human review is not required for the current state"
        )
    edge = next(
        (
            edge
            for edge in workflow.transitions
            if edge["from_state"] == item.current_state
            and edge.get("review_decision") == request.decision
        ),
        None,
    )
    if edge is None:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Workflow does not define a {request.decision} review transition",
        )

    approved_data = None
    if request.decision == "approve":
        approved_data = dict(item.data) if request.reviewed_data is None else request.reviewed_data
        if request.reviewed_data is not None and item.document_structured_extraction is not None:
            fields = [
                StructuredFieldDefinition.model_validate(field)
                for field in item.document_structured_extraction.field_schema
            ]
            try:
                approved_data = validate_extracted_data(fields, request.reviewed_data)
            except StructuredExtractionProviderError as exc:
                session.rollback()
                raise HTTPException(
                    status_code=422,
                    detail="reviewed_data does not match the structured extraction field schema",
                ) from exc

        active_plan = session.scalar(
            select(ActionPlan).where(
                ActionPlan.work_item_id == item.id,
                ActionPlan.superseded_at.is_(None),
            ).order_by(ActionPlan.revision.desc())
        )
        if active_plan is not None:
            if request.action_plan_id != active_plan.id:
                session.rollback()
                raise HTTPException(status_code=409, detail="Approval must authorize the exact current Action Plan")
            if active_plan.work_item_state != item.current_state or active_plan.work_item_version != item.version:
                session.rollback()
                raise HTTPException(status_code=409, detail="Action Plan is stale")
            if active_plan.facts_snapshot != approved_data:
                session.rollback()
                raise HTTPException(status_code=409, detail="Reviewed facts require a revised Action Plan")
        elif request.action_plan_id is not None:
            session.rollback()
            raise HTTPException(status_code=409, detail="Action Plan does not belong to this review")

    try:
        result = apply_transition(
            item,
            workflow,
            expected_state=request.expected_work_item_state,
            expected_version=request.expected_work_item_version,
            to_state=edge["to_state"],
            allow_review=True,
        )
    except WorkflowTransitionConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if request.decision == "approve":
        item.data = approved_data
    review.status = "approved" if request.decision == "approve" else "rejected"
    review.reviewer = request.reviewer
    review.notes = request.notes
    review.reviewed_data = approved_data
    if request.decision == "approve" and active_plan is not None:
        review.authorized_action_plan_id = active_plan.id
    review.resolved_at = datetime.now(timezone.utc)
    transition = WorkItemTransition(
        work_item_id=item.id,
        version=result.version,
        from_state=result.from_state,
        to_state=result.to_state,
        reason=request.notes,
    )
    session.add(transition)
    if request.decision == "approve" and active_plan is not None:
        execution = execute_internal_task(session, active_plan)
        result_state = "completed" if execution.status == "succeeded" else "action_needs_attention"
        try:
            execution_transition = apply_transition(
                item, workflow, expected_state=item.current_state,
                expected_version=item.version, to_state=result_state,
            )
        except WorkflowTransitionConflict as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.add(WorkItemTransition(
            work_item_id=item.id, version=execution_transition.version,
            from_state=execution_transition.from_state, to_state=execution_transition.to_state,
            reason=f"Action execution {execution.status}",
        ))
    create_pending_review_if_required(session, item, workflow)
    session.commit()
    session.refresh(review)
    return _response(review)
