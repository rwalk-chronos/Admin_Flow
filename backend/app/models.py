import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IntakeEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class IntakeEvent(Base):
    __tablename__ = "intake_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'processing', 'processed', 'failed')",
            name="ck_intake_events_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    sender: Mapped[str | None] = mapped_column(String(255))
    recipient: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(500))
    body_text: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=IntakeEventStatus.RECEIVED.value,
        server_default=IntakeEventStatus.RECEIVED.value,
    )
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    artifacts: Mapped[list["IntakeArtifact"]] = relationship(
        back_populates="intake_event"
    )


class IntakeArtifact(Base):
    __tablename__ = "intake_artifacts"
    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_intake_artifacts_byte_size"),
        CheckConstraint(
            "length(sha256) = 64", name="ck_intake_artifacts_sha256_length"
        ),
        UniqueConstraint("storage_key", name="uq_intake_artifacts_storage_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intake_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intake_events.id"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(255))
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    intake_event: Mapped[IntakeEvent] = relationship(back_populates="artifacts")
    extractions: Mapped[list["DocumentExtraction"]] = relationship(
        back_populates="intake_artifact"
    )


class DocumentExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    PARTIAL = "partial"
    NEEDS_OCR = "needs_ocr"
    PASSWORD_REQUIRED = "password_required"
    FAILED = "failed"


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('extracted', 'partial', 'needs_ocr', "
            "'password_required', 'failed')",
            name="ck_document_extractions_status",
        ),
        CheckConstraint(
            "page_count >= 0", name="ck_document_extractions_page_count"
        ),
        CheckConstraint(
            "character_count >= 0",
            name="ck_document_extractions_character_count",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intake_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("intake_artifacts.id"),
        nullable=False,
        index=True,
    )
    source_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_extractions.id"),
        nullable=True,
        index=True,
    )
    extraction_method: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pdf_text", server_default="pdf_text"
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    page_count: Mapped[int] = mapped_column(nullable=False)
    character_count: Mapped[int] = mapped_column(nullable=False)
    text_content: Mapped[str | None] = mapped_column(Text)
    page_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    intake_artifact: Mapped[IntakeArtifact] = relationship(
        back_populates="extractions"
    )
    source_extraction: Mapped["DocumentExtraction | None"] = relationship(
        remote_side=[id],
        back_populates="derived_extractions",
        foreign_keys=[source_extraction_id],
    )
    derived_extractions: Mapped[list["DocumentExtraction"]] = relationship(
        back_populates="source_extraction",
        foreign_keys=[source_extraction_id],
    )
    classifications: Mapped[list["DocumentClassification"]] = relationship(
        back_populates="document_extraction"
    )


class DocumentClassification(Base):
    __tablename__ = "document_classifications"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_document_classifications_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_extractions.id"),
        nullable=False,
        index=True,
    )
    candidate_labels: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    document_extraction: Mapped[DocumentExtraction] = relationship(
        back_populates="classifications"
    )
