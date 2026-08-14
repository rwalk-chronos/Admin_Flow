import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import IntakeEvent
from app.schemas import IntakeEventCreate, IntakeEventResponse


router = APIRouter(prefix="/intake-events", tags=["intake-events"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("", response_model=IntakeEventResponse, status_code=status.HTTP_201_CREATED)
def create_intake_event(
    intake_event: IntakeEventCreate, session: SessionDependency
) -> IntakeEvent:
    event = IntakeEvent(**intake_event.model_dump())
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


@router.get("", response_model=list[IntakeEventResponse])
def list_intake_events(session: SessionDependency) -> list[IntakeEvent]:
    statement = select(IntakeEvent).order_by(
        IntakeEvent.received_at.desc(), IntakeEvent.created_at.desc()
    )
    return list(session.scalars(statement))


@router.get("/{event_id}", response_model=IntakeEventResponse)
def get_intake_event(event_id: uuid.UUID, session: SessionDependency) -> IntakeEvent:
    event = session.get(IntakeEvent, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intake event not found"
        )
    return event
