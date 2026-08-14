import uuid
from functools import lru_cache
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifact_storage import ArtifactStorage
from app.db import get_session
from app.document_reader import DocumentReader, PdfTextReader
from app.intake_artifacts import get_artifact_storage
from app.models import DocumentExtraction, IntakeArtifact
from app.schemas import DocumentExtractionResponse


router = APIRouter(tags=["document-extractions"])
SessionDependency = Annotated[Session, Depends(get_session)]
StorageDependency = Annotated[ArtifactStorage, Depends(get_artifact_storage)]


@lru_cache
def get_pdf_document_reader() -> DocumentReader:
    return PdfTextReader()


ReaderDependency = Annotated[DocumentReader, Depends(get_pdf_document_reader)]


@router.post(
    "/intake-artifacts/{artifact_id}/extract",
    response_model=DocumentExtractionResponse,
    status_code=status.HTTP_201_CREATED,
)
def extract_intake_artifact(
    artifact_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
    reader: ReaderDependency,
) -> DocumentExtraction:
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

    with stored_file:
        if not _is_pdf_candidate(artifact, stored_file):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Intake artifact is not a supported PDF",
            )
        result = reader.read(stored_file)

    extraction = DocumentExtraction(
        intake_artifact_id=artifact.id,
        extraction_method=reader.extraction_method,
        status=result.status,
        page_count=result.page_count,
        character_count=result.character_count,
        text_content=result.text_content,
        page_results=result.page_results,
        error_message=result.error_message,
    )
    session.add(extraction)
    session.commit()
    session.refresh(extraction)
    return extraction


@router.get(
    "/intake-artifacts/{artifact_id}/extractions",
    response_model=list[DocumentExtractionResponse],
)
def list_document_extractions(
    artifact_id: uuid.UUID, session: SessionDependency
) -> list[DocumentExtraction]:
    if session.get(IntakeArtifact, artifact_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intake artifact not found"
        )

    statement = (
        select(DocumentExtraction)
        .where(DocumentExtraction.intake_artifact_id == artifact_id)
        .order_by(DocumentExtraction.created_at.desc(), DocumentExtraction.id.desc())
    )
    return list(session.scalars(statement))


@router.get(
    "/document-extractions/{extraction_id}",
    response_model=DocumentExtractionResponse,
)
def get_document_extraction(
    extraction_id: uuid.UUID, session: SessionDependency
) -> DocumentExtraction:
    extraction = session.get(DocumentExtraction, extraction_id)
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document extraction not found",
        )
    return extraction


def _is_pdf_candidate(artifact: IntakeArtifact, source: BinaryIO) -> bool:
    content_type_is_pdf = (
        artifact.content_type or ""
    ).partition(";")[0].strip().lower() == "application/pdf"
    filename_is_pdf = (artifact.original_filename or "").lower().endswith(".pdf")
    signature_is_pdf = b"%PDF-" in source.read(1024)
    source.seek(0)
    return content_type_is_pdf or filename_is_pdf or signature_is_pdf
