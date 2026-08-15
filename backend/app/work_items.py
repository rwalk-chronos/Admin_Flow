import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    DocumentStructuredExtraction,
    IntakeEvent,
    WorkItem,
    WorkItemTransition,
    WorkflowDefinition,
)
from app.schemas import (
    WorkItemCreate,
    WorkItemResponse,
    WorkItemTransitionCreate,
    WorkItemTransitionResponse,
    WorkflowDefinitionCreate,
    WorkflowDefinitionResponse,
)
from app.workflow_engine import WorkflowTransitionConflict, apply_transition


router = APIRouter()
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "/workflow-definitions",
    response_model=WorkflowDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["workflow-definitions"],
)
def create_workflow_definition(
    request: WorkflowDefinitionCreate, session: SessionDependency
) -> WorkflowDefinition:
    workflow = WorkflowDefinition(
        name=request.name,
        version=request.version,
        description=request.description,
        states=[state.model_dump(mode="json") for state in request.states],
        initial_state=request.initial_state,
        transitions=[edge.model_dump(mode="json") for edge in request.transitions],
    )
    session.add(workflow)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Workflow definition name and version already exist",
        ) from None
    session.refresh(workflow)
    return workflow


@router.get(
    "/workflow-definitions",
    response_model=list[WorkflowDefinitionResponse],
    tags=["workflow-definitions"],
)
def list_workflow_definitions(session: SessionDependency) -> list[WorkflowDefinition]:
    statement = select(WorkflowDefinition).order_by(
        WorkflowDefinition.created_at.desc(), WorkflowDefinition.id.desc()
    )
    return list(session.scalars(statement))


@router.get(
    "/workflow-definitions/{workflow_definition_id}",
    response_model=WorkflowDefinitionResponse,
    tags=["workflow-definitions"],
)
def get_workflow_definition(
    workflow_definition_id: uuid.UUID, session: SessionDependency
) -> WorkflowDefinition:
    workflow = session.get(WorkflowDefinition, workflow_definition_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow definition not found")
    return workflow


@router.post(
    "/work-items",
    response_model=WorkItemResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["work-items"],
)
def create_work_item(
    request: WorkItemCreate, session: SessionDependency
) -> WorkItem:
    workflow = session.get(WorkflowDefinition, request.workflow_definition_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow definition not found")
    if session.get(IntakeEvent, request.intake_event_id) is None:
        raise HTTPException(status_code=404, detail="Intake event not found")

    data = request.data or {}
    if request.document_structured_extraction_id is not None:
        if request.data is not None:
            raise HTTPException(
                status_code=422,
                detail="data cannot override a document structured extraction",
            )
        structured = session.get(
            DocumentStructuredExtraction,
            request.document_structured_extraction_id,
        )
        if structured is None:
            raise HTTPException(
                status_code=404, detail="Document structured extraction not found"
            )
        source_event_id = (
            structured.document_extraction.intake_artifact.intake_event_id
        )
        if source_event_id != request.intake_event_id:
            raise HTTPException(
                status_code=409,
                detail="Document structured extraction belongs to another IntakeEvent",
            )
        data = dict(structured.extracted_data)

    item = WorkItem(
        workflow_definition_id=workflow.id,
        intake_event_id=request.intake_event_id,
        document_structured_extraction_id=request.document_structured_extraction_id,
        work_type=request.work_type,
        title=request.title,
        data=data,
        current_state=workflow.initial_state,
        version=1,
    )
    session.add(item)
    session.flush()
    session.add(
        WorkItemTransition(
            work_item_id=item.id,
            version=1,
            from_state=None,
            to_state=workflow.initial_state,
            reason=None,
        )
    )
    session.commit()
    session.refresh(item)
    return item


@router.get("/work-items", response_model=list[WorkItemResponse], tags=["work-items"])
def list_work_items(session: SessionDependency) -> list[WorkItem]:
    statement = select(WorkItem).order_by(
        WorkItem.created_at.desc(), WorkItem.id.desc()
    )
    return list(session.scalars(statement))


@router.get(
    "/work-items/{work_item_id}", response_model=WorkItemResponse, tags=["work-items"]
)
def get_work_item(work_item_id: uuid.UUID, session: SessionDependency) -> WorkItem:
    item = session.get(WorkItem, work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="WorkItem not found")
    return item


@router.post(
    "/work-items/{work_item_id}/transitions",
    response_model=WorkItemTransitionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["work-item-transitions"],
)
def transition_work_item(
    work_item_id: uuid.UUID,
    request: WorkItemTransitionCreate,
    session: SessionDependency,
) -> WorkItemTransition:
    statement = select(WorkItem).where(WorkItem.id == work_item_id).with_for_update()
    item = session.scalar(statement)
    if item is None:
        raise HTTPException(status_code=404, detail="WorkItem not found")
    workflow = session.get(WorkflowDefinition, item.workflow_definition_id)
    try:
        result = apply_transition(
            item,
            workflow,
            expected_state=request.expected_state,
            expected_version=request.expected_version,
            to_state=request.to_state,
        )
    except WorkflowTransitionConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    transition = WorkItemTransition(
        work_item_id=item.id,
        version=result.version,
        from_state=result.from_state,
        to_state=result.to_state,
        reason=request.reason,
    )
    session.add(transition)
    session.commit()
    session.refresh(transition)
    return transition


@router.get(
    "/work-items/{work_item_id}/transitions",
    response_model=list[WorkItemTransitionResponse],
    tags=["work-item-transitions"],
)
def list_work_item_transitions(
    work_item_id: uuid.UUID, session: SessionDependency
) -> list[WorkItemTransition]:
    if session.get(WorkItem, work_item_id) is None:
        raise HTTPException(status_code=404, detail="WorkItem not found")
    statement = (
        select(WorkItemTransition)
        .where(WorkItemTransition.work_item_id == work_item_id)
        .order_by(WorkItemTransition.version.asc())
    )
    return list(session.scalars(statement))
