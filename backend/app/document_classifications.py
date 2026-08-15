import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.document_classifier import (
    ClassificationProviderError,
    DocumentClassifier,
    LocalStubDocumentClassifier,
    OpenAIDocumentClassifier,
)
from app.models import DocumentClassification, DocumentExtraction
from app.schemas import (
    DocumentClassificationCreate,
    DocumentClassificationResponse,
)


router = APIRouter(tags=["document-classifications"])
SessionDependency = Annotated[Session, Depends(get_session)]


@lru_cache
def get_document_classifier() -> DocumentClassifier:
    settings = get_settings()
    if settings.ai_provider == "stub":
        return LocalStubDocumentClassifier()
    api_key = (
        settings.openai_api_key.get_secret_value().strip()
        if settings.openai_api_key is not None
        else ""
    )
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI document classification is not configured",
        )
    return OpenAIDocumentClassifier(
        api_key=api_key,
        model=settings.ai_classification_model,
    )


ClassifierDependency = Annotated[DocumentClassifier, Depends(get_document_classifier)]


@router.post(
    "/document-extractions/{extraction_id}/classifications",
    response_model=DocumentClassificationResponse,
    status_code=status.HTTP_201_CREATED,
)
def classify_document_extraction(
    extraction_id: uuid.UUID,
    request: DocumentClassificationCreate,
    session: SessionDependency,
    classifier: ClassifierDependency,
) -> DocumentClassification:
    extraction = session.get(DocumentExtraction, extraction_id)
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document extraction not found",
        )
    if extraction.text_content is None or not extraction.text_content.strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document extraction has no readable text to classify",
        )

    try:
        result = classifier.classify(
            text=extraction.text_content,
            candidate_labels=request.candidate_labels,
        )
    except ClassificationProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    candidate_names = {candidate.name for candidate in request.candidate_labels}
    if result.label not in candidate_names:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI classifier returned a label outside the candidate taxonomy",
        )

    classification = DocumentClassification(
        document_extraction_id=extraction.id,
        candidate_labels=[
            candidate.model_dump(mode="json")
            for candidate in request.candidate_labels
        ],
        provider_name=classifier.provider_name,
        model_name=classifier.model_name,
        prompt_version=classifier.prompt_version,
        label=result.label,
        confidence=result.confidence,
        rationale=result.rationale,
    )
    session.add(classification)
    session.commit()
    session.refresh(classification)
    return classification


@router.get(
    "/document-extractions/{extraction_id}/classifications",
    response_model=list[DocumentClassificationResponse],
)
def list_document_classifications(
    extraction_id: uuid.UUID,
    session: SessionDependency,
) -> list[DocumentClassification]:
    if session.get(DocumentExtraction, extraction_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document extraction not found",
        )

    statement = (
        select(DocumentClassification)
        .where(DocumentClassification.document_extraction_id == extraction_id)
        .order_by(
            DocumentClassification.created_at.desc(),
            DocumentClassification.id.desc(),
        )
    )
    return list(session.scalars(statement))


@router.get(
    "/document-classifications/{classification_id}",
    response_model=DocumentClassificationResponse,
)
def get_document_classification(
    classification_id: uuid.UUID,
    session: SessionDependency,
) -> DocumentClassification:
    classification = session.get(DocumentClassification, classification_id)
    if classification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document classification not found",
        )
    return classification
