import logging
import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifact_storage import (
    CHUNK_SIZE,
    ArtifactStorage,
    LocalArtifactStorage,
    build_storage_key,
)
from app.config import get_settings
from app.db import get_session
from app.models import IntakeArtifact, IntakeEvent
from app.schemas import IntakeArtifactResponse


logger = logging.getLogger(__name__)
router = APIRouter(tags=["intake-artifacts"])
SessionDependency = Annotated[Session, Depends(get_session)]


@lru_cache
def get_artifact_storage() -> ArtifactStorage:
    return LocalArtifactStorage(get_settings().artifact_storage_path)


StorageDependency = Annotated[ArtifactStorage, Depends(get_artifact_storage)]


@router.post(
    "/intake-events/{event_id}/artifacts",
    response_model=IntakeArtifactResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_intake_artifact(
    event_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
    file: Annotated[UploadFile, File()],
) -> IntakeArtifact:
    if session.get(IntakeEvent, event_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intake event not found"
        )

    artifact_id = uuid.uuid4()
    storage_key = build_storage_key(event_id, artifact_id)
    stored = storage.store(storage_key, file.file)
    artifact = IntakeArtifact(
        id=artifact_id,
        intake_event_id=event_id,
        original_filename=file.filename or None,
        content_type=file.content_type or None,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        storage_key=storage_key,
    )
    session.add(artifact)

    try:
        session.commit()
    except Exception:
        session.rollback()
        try:
            storage.delete(storage_key)
        except OSError:
            logger.exception("Failed to clean up artifact after database error")
        raise

    session.refresh(artifact)
    return artifact


@router.get(
    "/intake-events/{event_id}/artifacts",
    response_model=list[IntakeArtifactResponse],
)
def list_intake_artifacts(
    event_id: uuid.UUID, session: SessionDependency
) -> list[IntakeArtifact]:
    if session.get(IntakeEvent, event_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intake event not found"
        )

    statement = (
        select(IntakeArtifact)
        .where(IntakeArtifact.intake_event_id == event_id)
        .order_by(IntakeArtifact.created_at.asc(), IntakeArtifact.id.asc())
    )
    return list(session.scalars(statement))


@router.get("/intake-artifacts/{artifact_id}", response_model=IntakeArtifactResponse)
def get_intake_artifact(
    artifact_id: uuid.UUID, session: SessionDependency
) -> IntakeArtifact:
    artifact = session.get(IntakeArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intake artifact not found"
        )
    return artifact


@router.get("/intake-artifacts/{artifact_id}/content")
def get_intake_artifact_content(
    artifact_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
) -> StreamingResponse:
    artifact = session.get(IntakeArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intake artifact not found"
        )

    try:
        stored_file = storage.open(artifact.storage_key)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Intake artifact content not found",
        ) from None

    headers = {"Content-Length": str(artifact.byte_size)}
    if artifact.original_filename:
        encoded_filename = quote(artifact.original_filename, safe="")
        headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{encoded_filename}"
        )

    return StreamingResponse(
        _stream_file(stored_file),
        media_type=artifact.content_type or "application/octet-stream",
        headers=headers,
    )


def _stream_file(stored_file: BinaryIO) -> Iterator[bytes]:
    with stored_file:
        while chunk := stored_file.read(CHUNK_SIZE):
            yield chunk
