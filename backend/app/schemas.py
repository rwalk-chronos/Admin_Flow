import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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


class ClassificationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class DocumentClassificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_labels: list[ClassificationCandidate] = Field(
        min_length=2, max_length=50
    )

    @model_validator(mode="after")
    def candidate_names_are_unique(self) -> "DocumentClassificationCreate":
        normalized_names = [
            candidate.name.strip().casefold() for candidate in self.candidate_labels
        ]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("candidate label names must be unique")
        return self


class DocumentClassificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_extraction_id: uuid.UUID
    candidate_labels: list[ClassificationCandidate]
    provider_name: str
    model_name: str
    prompt_version: str
    label: str
    confidence: float
    rationale: str
    created_at: datetime


StructuredFieldType = Literal[
    "string", "integer", "number", "boolean", "date", "array_string"
]


class StructuredFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    type: StructuredFieldType
    required: bool

    @model_validator(mode="after")
    def name_must_not_be_blank(self) -> "StructuredFieldDefinition":
        if not self.name.strip():
            raise ValueError("field name must not be blank")
        return self


class DocumentStructuredExtractionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_classification_id: uuid.UUID | None = None
    fields: list[StructuredFieldDefinition] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def field_names_are_unique(self) -> "DocumentStructuredExtractionCreate":
        normalized_names = [field.name.strip().casefold() for field in self.fields]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("field names must be unique")
        return self


class DocumentStructuredExtractionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_extraction_id: uuid.UUID
    document_classification_id: uuid.UUID | None
    field_schema: list[StructuredFieldDefinition]
    extracted_data: dict[str, Any]
    summary: str | None
    provider_name: str
    model_name: str
    prompt_version: str
    created_at: datetime

STATE_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


class WorkflowStateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=STATE_IDENTIFIER_PATTERN)
    description: str | None = Field(default=None, max_length=500)
    terminal: bool = False
    review_required: bool = Field(default=False, exclude_if=lambda value: not value)


class WorkflowTransitionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_state: str = Field(pattern=STATE_IDENTIFIER_PATTERN)
    to_state: str = Field(pattern=STATE_IDENTIFIER_PATTERN)
    review_decision: Literal["approve", "reject", "handle_manually"] | None = Field(default=None, exclude_if=lambda value: value is None)


class WorkflowDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    description: str | None = Field(default=None, max_length=1000)
    states: list[WorkflowStateDefinition] = Field(min_length=1, max_length=50)
    initial_state: str = Field(pattern=STATE_IDENTIFIER_PATTERN)
    transitions: list[WorkflowTransitionDefinition] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def definition_is_valid(self) -> "WorkflowDefinitionCreate":
        from app.workflow_engine import validate_workflow_graph
        if not self.name.strip():
            raise ValueError("workflow name must not be blank")
        validate_workflow_graph(self.states, self.initial_state, self.transitions)
        return self


class WorkflowDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    version: int
    description: str | None
    states: list[WorkflowStateDefinition]
    initial_state: str
    transitions: list[WorkflowTransitionDefinition]
    created_at: datetime


class WorkItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_definition_id: uuid.UUID
    intake_event_id: uuid.UUID
    document_structured_extraction_id: uuid.UUID | None = None
    work_type: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def source_data_is_unambiguous(self) -> "WorkItemCreate":
        if not self.work_type.strip() or not self.title.strip():
            raise ValueError("work_type and title must not be blank")
        if self.document_structured_extraction_id is not None and self.data is not None:
            raise ValueError("data cannot override a document structured extraction")
        return self


class WorkItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workflow_definition_id: uuid.UUID
    intake_event_id: uuid.UUID
    document_structured_extraction_id: uuid.UUID | None
    work_type: str
    title: str
    data: dict[str, Any]
    current_state: str
    version: int
    created_at: datetime
    updated_at: datetime


class WorkItemTransitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_state: str = Field(pattern=STATE_IDENTIFIER_PATTERN)
    expected_version: int = Field(ge=1)
    to_state: str = Field(pattern=STATE_IDENTIFIER_PATTERN)
    reason: str | None = Field(default=None, max_length=500)


class WorkItemTransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    work_item_id: uuid.UUID
    version: int
    from_state: str | None
    to_state: str
    reason: str | None
    created_at: datetime

ReviewStatus = Literal["pending", "approved", "rejected"]
ReviewDecision = Literal["approve", "reject"]


class WorkItemReviewResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject", "handle_manually"]
    expected_work_item_state: str = Field(pattern=STATE_IDENTIFIER_PATTERN)
    expected_work_item_version: int = Field(ge=1)
    reviewer: str = Field(min_length=1, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)
    reviewed_data: dict[str, Any] | None = None
    action_plan_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def reviewer_must_not_be_blank(self) -> "WorkItemReviewResolve":
        if not self.reviewer.strip():
            raise ValueError("reviewer must not be blank")
        return self


class WorkItemReviewResponse(BaseModel):
    id: uuid.UUID
    work_item_id: uuid.UUID
    work_item_version: int
    state: str
    status: ReviewStatus
    reviewer: str | None
    notes: str | None
    reviewed_data: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None
    work_type: str
    title: str
    current_state: str
    current_version: int
    work_item_data: dict[str, Any]
    authorized_action_plan_id: uuid.UUID | None = None


class ActionPlanRevise(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_work_item_state: str = Field(pattern=STATE_IDENTIFIER_PATTERN)
    expected_work_item_version: int = Field(ge=1)
    reviewed_data: dict[str, Any]


class ActionPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    work_item_id: uuid.UUID
    work_item_state: str
    work_item_version: int
    workflow_definition_id: uuid.UUID
    workflow_definition_version: int
    intake_event_id: uuid.UUID
    revision: int
    action_type: Literal["create_internal_task"]
    facts_snapshot: dict[str, Any]
    destination: dict[str, Any]
    payload: dict[str, Any]
    source_artifact_ids: list[str]
    action_title: str
    action_description: str
    approval_label: str
    external_effect: str
    superseded_at: datetime | None
    superseded_reason: str | None
    created_at: datetime


class ActionExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action_plan_id: uuid.UUID
    idempotency_key: str
    status: Literal["succeeded", "failed"]
    result: dict[str, Any]
    error_message: str | None
    attempted_at: datetime
    completed_at: datetime


class InternalTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action_execution_id: uuid.UUID
    work_item_id: uuid.UUID
    title: str
    queue: str
    owner_role: str | None
    due_at: datetime | None
    facts_snapshot: dict[str, Any]
    source_artifact_ids: list[str]
    status: Literal["open", "completed"]
    created_at: datetime


class DecisionPacketFact(BaseModel):
    key: str
    label: str
    value: Any | None
    display_value: str
    missing: bool


class DecisionPacketAttention(BaseModel):
    title: str
    guidance: str
    blocking: bool = False


class DecisionPacketArtifact(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str | None
    content_type: str | None
    byte_size: int


class DecisionPacketReview(BaseModel):
    id: uuid.UUID
    status: ReviewStatus
    reviewer: str | None
    notes: str | None
    created_at: datetime
    resolved_at: datetime | None


class DecisionPacketActionResult(BaseModel):
    status: Literal["succeeded", "failed"]
    completed_at: datetime
    message: str
    task_id: uuid.UUID | None = None
    task_title: str | None = None
    queue: str | None = None
    owner_role: str | None = None
    task_created_at: datetime | None = None


class DecisionPacketResponse(BaseModel):
    review: DecisionPacketReview | None
    work_item_id: uuid.UUID
    title: str
    status_label: str
    document_type: str
    confidence: float | None
    confidence_band: Literal["High confidence", "Moderate confidence", "Low confidence"] | None
    summary: str
    summary_source: Literal["ai", "deterministic_fallback"]
    key_information: list[DecisionPacketFact]
    attention_items: list[DecisionPacketAttention]
    artifacts: list[DecisionPacketArtifact]
    action_plan: ActionPlanResponse | None
    action_result: DecisionPacketActionResult | None
    correction_schema: list[StructuredFieldDefinition]
    correction_data: dict[str, Any]
    technical: dict[str, Any]

class DocumentProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: Literal["generic_office"] = "generic_office"


class DocumentProcessingProfileResponse(BaseModel):
    id: str
    display_name: str


class DocumentProcessingConfigResponse(BaseModel):
    provider: Literal["stub", "openai"]
    provider_display_name: str
    uses_external_service: bool
    configured: bool
    profiles: list[DocumentProcessingProfileResponse]


class DocumentProcessResponse(BaseModel):
    profile_id: str
    provider_name: str
    reused: bool
    classification: DocumentClassificationResponse
    structured_extraction: DocumentStructuredExtractionResponse
    work_item: WorkItemResponse
    review_id: uuid.UUID
    action_plan_id: uuid.UUID | None = None
