import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.document_structured_extractor import (
    DocumentStructuredExtractor,
    OpenAIDocumentStructuredExtractor,
    StructuredExtractionProviderError,
    validate_extracted_data,
)
from app.models import (
    DocumentClassification,
    DocumentExtraction,
    DocumentStructuredExtraction,
)
from app.schemas import (
    DocumentStructuredExtractionCreate,
    DocumentStructuredExtractionResponse,
)

router = APIRouter(tags=["document-structured-extractions"])
SessionDependency = Annotated[Session, Depends(get_session)]


@lru_cache
def get_document_structured_extractor() -> DocumentStructuredExtractor:
    settings = get_settings()
    api_key = (
        settings.openai_api_key.get_secret_value().strip()
        if settings.openai_api_key is not None
        else ""
    )
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI structured extraction is not configured",
        )
    return OpenAIDocumentStructuredExtractor(
        api_key=api_key,
        model=settings.ai_structured_extraction_model,
    )


ExtractorDependency = Annotated[
    DocumentStructuredExtractor, Depends(get_document_structured_extractor)
]


@router.post(
    "/document-extractions/{extraction_id}/structured-extractions",
    response_model=DocumentStructuredExtractionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_document_structured_extraction(
    extraction_id: uuid.UUID,
    request: DocumentStructuredExtractionCreate,
    session: SessionDependency,
    extractor: ExtractorDependency,
) -> DocumentStructuredExtraction:
    extraction = session.get(DocumentExtraction, extraction_id)
    if extraction is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    if extraction.text_content is None or not extraction.text_content.strip():
        raise HTTPException(
            status_code=409,
            detail="Document extraction has no readable text to extract",
        )

    classification = _load_classification(session, extraction, request)
    classification_context = (
        {"label": classification.label, "rationale": classification.rationale}
        if classification is not None
        else None
    )
    try:
        provider_result = extractor.extract(
            text=extraction.text_content,
            fields=request.fields,
            classification_context=classification_context,
        )
        if not hasattr(provider_result, "data"):
            raise StructuredExtractionProviderError(
                "AI structured extractor returned invalid structured data"
            )
        extracted_data = validate_extracted_data(request.fields, provider_result.data)
    except StructuredExtractionProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    record = DocumentStructuredExtraction(
        document_extraction_id=extraction.id,
        document_classification_id=(classification.id if classification else None),
        field_schema=[field.model_dump(mode="json") for field in request.fields],
        extracted_data=extracted_data,
        provider_name=extractor.provider_name,
        model_name=extractor.model_name,
        prompt_version=extractor.prompt_version,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _load_classification(
    session: Session,
    extraction: DocumentExtraction,
    request: DocumentStructuredExtractionCreate,
) -> DocumentClassification | None:
    if request.document_classification_id is None:
        return None
    classification = session.get(
        DocumentClassification, request.document_classification_id
    )
    if classification is None:
        raise HTTPException(status_code=404, detail="Document classification not found")
    if classification.document_extraction_id != extraction.id:
        raise HTTPException(
            status_code=409,
            detail="Document classification belongs to a different extraction",
        )
    return classification


@router.get(
    "/document-extractions/{extraction_id}/structured-extractions",
    response_model=list[DocumentStructuredExtractionResponse],
)
def list_document_structured_extractions(
    extraction_id: uuid.UUID, session: SessionDependency
) -> list[DocumentStructuredExtraction]:
    if session.get(DocumentExtraction, extraction_id) is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    statement = (
        select(DocumentStructuredExtraction)
        .where(DocumentStructuredExtraction.document_extraction_id == extraction_id)
        .order_by(
            DocumentStructuredExtraction.created_at.desc(),
            DocumentStructuredExtraction.id.desc(),
        )
    )
    return list(session.scalars(statement))


@router.get(
    "/document-structured-extractions/{structured_extraction_id}",
    response_model=DocumentStructuredExtractionResponse,
)
def get_document_structured_extraction(
    structured_extraction_id: uuid.UUID, session: SessionDependency
) -> DocumentStructuredExtraction:
    record = session.get(DocumentStructuredExtraction, structured_extraction_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail="Document structured extraction not found"
        )
    return record
