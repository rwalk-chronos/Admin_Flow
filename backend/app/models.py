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
    work_items: Mapped[list["WorkItem"]] = relationship(back_populates="intake_event")


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
    structured_extractions: Mapped[list["DocumentStructuredExtraction"]] = relationship(
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
    structured_extractions: Mapped[list["DocumentStructuredExtraction"]] = relationship(
        back_populates="document_classification"
    )


class DocumentStructuredExtraction(Base):
    __tablename__ = "document_structured_extractions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_extractions.id"),
        nullable=False,
        index=True,
    )
    document_classification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_classifications.id"),
        nullable=True,
        index=True,
    )
    field_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    extracted_data: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    document_extraction: Mapped[DocumentExtraction] = relationship(
        back_populates="structured_extractions"
    )
    document_classification: Mapped[DocumentClassification | None] = relationship(
        back_populates="structured_extractions"
    )
    work_items: Mapped[list["WorkItem"]] = relationship(
        back_populates="document_structured_extraction"
    )


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_workflow_definitions_version"),
        UniqueConstraint("name", "version", name="uq_workflow_definitions_name_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    states: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    initial_state: Mapped[str] = mapped_column(String(64), nullable=False)
    transitions: Mapped[list[dict[str, str]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    work_items: Mapped[list["WorkItem"]] = relationship(back_populates="workflow_definition")


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_work_items_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False, index=True)
    intake_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("intake_events.id"), nullable=False, index=True)
    document_structured_extraction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("document_structured_extractions.id"), nullable=True, index=True)
    work_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)
    current_state: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    workflow_definition: Mapped[WorkflowDefinition] = relationship(back_populates="work_items")
    intake_event: Mapped[IntakeEvent] = relationship(back_populates="work_items")
    document_structured_extraction: Mapped[DocumentStructuredExtraction | None] = relationship(back_populates="work_items")
    transitions: Mapped[list["WorkItemTransition"]] = relationship(back_populates="work_item", order_by="WorkItemTransition.version")
    reviews: Mapped[list["WorkItemReview"]] = relationship(
        back_populates="work_item", order_by="WorkItemReview.created_at"
    )
    action_plans: Mapped[list["ActionPlan"]] = relationship(
        back_populates="work_item", order_by="ActionPlan.revision"
    )
    internal_tasks: Mapped[list["InternalTask"]] = relationship(back_populates="work_item")


class WorkItemTransition(Base):
    __tablename__ = "work_item_transitions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_work_item_transitions_version"),
        UniqueConstraint("work_item_id", "version", name="uq_work_item_transitions_item_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_items.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(64))
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    work_item: Mapped[WorkItem] = relationship(back_populates="transitions")


class WorkItemReview(Base):
    __tablename__ = "work_item_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_work_item_reviews_status",
        ),
        CheckConstraint(
            "work_item_version >= 1", name="ck_work_item_reviews_version"
        ),
        CheckConstraint(
            "(status = 'pending' AND reviewer IS NULL AND resolved_at IS NULL "
            "AND reviewed_data IS NULL) OR "
            "(status IN ('approved', 'rejected') AND reviewer IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_work_item_reviews_resolution",
        ),
        CheckConstraint(
            "status != 'rejected' OR reviewed_data IS NULL",
            name="ck_work_item_reviews_rejected_data",
        ),
        UniqueConstraint(
            "work_item_id",
            "work_item_version",
            name="uq_work_item_reviews_item_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_items.id"), nullable=False, index=True
    )
    work_item_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    reviewer: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    reviewed_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    authorized_action_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("action_plans.id", use_alter=True), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_item: Mapped[WorkItem] = relationship(back_populates="reviews")
    authorized_action_plan: Mapped["ActionPlan | None"] = relationship(
        foreign_keys=[authorized_action_plan_id]
    )


class ActionPlan(Base):
    __tablename__ = "action_plans"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_action_plans_revision"),
        CheckConstraint("action_type IN ('create_internal_task')", name="ck_action_plans_type"),
        UniqueConstraint("work_item_id", "revision", name="uq_action_plans_item_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_items.id"), nullable=False, index=True)
    work_item_state: Mapped[str] = mapped_column(String(64), nullable=False)
    work_item_version: Mapped[int] = mapped_column(nullable=False)
    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False)
    workflow_definition_version: Mapped[int] = mapped_column(nullable=False)
    intake_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("intake_events.id"), nullable=False)
    revision: Mapped[int] = mapped_column(nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    facts_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    destination: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    source_artifact_ids: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    action_title: Mapped[str] = mapped_column(String(255), nullable=False)
    action_description: Mapped[str] = mapped_column(Text, nullable=False)
    approval_label: Mapped[str] = mapped_column(String(100), nullable=False)
    external_effect: Mapped[str] = mapped_column(String(255), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    work_item: Mapped[WorkItem] = relationship(back_populates="action_plans")
    executions: Mapped[list["ActionExecution"]] = relationship(back_populates="action_plan")


class ActionExecution(Base):
    __tablename__ = "action_executions"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed')", name="ck_action_executions_status"),
        UniqueConstraint("action_plan_id", name="uq_action_executions_plan"),
        UniqueConstraint("idempotency_key", name="uq_action_executions_idempotency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("action_plans.id"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action_plan: Mapped[ActionPlan] = relationship(back_populates="executions")
    internal_task: Mapped["InternalTask | None"] = relationship(back_populates="action_execution", uselist=False)


class InternalTask(Base):
    __tablename__ = "internal_tasks"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'completed')", name="ck_internal_tasks_status"),
        UniqueConstraint("action_execution_id", name="uq_internal_tasks_execution"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("action_executions.id"), nullable=False, index=True)
    work_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_items.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    queue: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_role: Mapped[str | None] = mapped_column(String(100))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    facts_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    source_artifact_ids: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    action_execution: Mapped[ActionExecution] = relationship(back_populates="internal_task")
    work_item: Mapped[WorkItem] = relationship(back_populates="internal_tasks")
