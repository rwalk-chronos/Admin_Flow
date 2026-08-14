import uuid
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.models import DocumentExtractionStatus, IntakeEventStatus


class IntakeEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(min_length=1, max_length=100)
    external_id: str | None = Field(default=None, max_length=255)
    sender: str | None = Field(default=None, max_length=255)
    recipient: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=500)
    body_text: str | None = None
    received_at: AwareDatetime
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class IntakeEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: str
    external_id: str | None
    sender: str | None
    recipient: str | None
    subject: str | None
    body_text: str | None
    received_at: datetime
    status: IntakeEventStatus
    raw_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class IntakeArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    intake_event_id: uuid.UUID
    original_filename: str | None
    content_type: str | None
    byte_size: int
    sha256: str
    created_at: datetime


class DocumentPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int
    text: str
    character_count: int
    needs_ocr: bool


class OcrDocumentPageResult(DocumentPageResult):
    text_source: str


class DocumentExtractionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    intake_artifact_id: uuid.UUID
    source_extraction_id: uuid.UUID | None
    extraction_method: str
    status: DocumentExtractionStatus
    page_count: int
    character_count: int
    text_content: str | None
    page_results: list[DocumentPageResult | OcrDocumentPageResult]
    error_message: str | None
    created_at: datetime
