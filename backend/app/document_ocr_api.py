import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.artifact_storage import ArtifactStorage
from app.config import get_settings
from app.db import get_session
from app.document_ocr import OcrEngine, PdfOcrProcessor, TesseractOcrEngine
from app.intake_artifacts import get_artifact_storage
from app.models import DocumentExtraction, IntakeArtifact
from app.schemas import DocumentExtractionResponse


router = APIRouter(tags=["document-extractions"])
SessionDependency = Annotated[Session, Depends(get_session)]
StorageDependency = Annotated[ArtifactStorage, Depends(get_artifact_storage)]


@lru_cache
def get_ocr_engine() -> OcrEngine:
    settings = get_settings()
    return TesseractOcrEngine(
        language=settings.ocr_language,
        timeout_seconds=settings.ocr_timeout_seconds,
    )


OcrEngineDependency = Annotated[OcrEngine, Depends(get_ocr_engine)]


def get_pdf_ocr_processor(engine: OcrEngineDependency) -> PdfOcrProcessor:
    return PdfOcrProcessor(engine=engine, dpi=get_settings().ocr_dpi)


ProcessorDependency = Annotated[PdfOcrProcessor, Depends(get_pdf_ocr_processor)]


@router.post(
    "/document-extractions/{extraction_id}/ocr",
    response_model=DocumentExtractionResponse,
    status_code=status.HTTP_201_CREATED,
)
def ocr_document_extraction(
    extraction_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
    processor: ProcessorDependency,
) -> DocumentExtraction:
    source_extraction = session.get(DocumentExtraction, extraction_id)
    if source_extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document extraction not found",
        )
    if source_extraction.status not in {"partial", "needs_ocr"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Document extraction status '{source_extraction.status}' "
                "is not eligible for OCR"
            ),
        )

    artifact = session.get(IntakeArtifact, source_extraction.intake_artifact_id)
    try:
        stored_file = storage.open(artifact.storage_key)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Intake artifact content not found",
        ) from None

    with stored_file:
        result = processor.process(
            source=stored_file,
            source_page_count=source_extraction.page_count,
            source_page_results=source_extraction.page_results,
        )

    derived_extraction = DocumentExtraction(
        intake_artifact_id=source_extraction.intake_artifact_id,
        source_extraction_id=source_extraction.id,
        extraction_method=processor.extraction_method,
        status=result.status,
        page_count=result.page_count,
        character_count=result.character_count,
        text_content=result.text_content,
        page_results=result.page_results,
        error_message=result.error_message,
    )
    session.add(derived_extraction)
    session.commit()
    session.refresh(derived_extraction)
    return derived_extraction
