import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.document_classifications import get_document_classifier
from app.document_classifier import ClassificationProviderError, DocumentClassifier
from app.document_structured_extractions import get_document_structured_extractor
from app.document_structured_extractor import DocumentStructuredExtractor, StructuredExtractionProviderError, validate_extracted_data
from app.models import DocumentClassification, DocumentExtraction, DocumentStructuredExtraction, WorkItem, WorkItemReview, WorkItemTransition, WorkflowDefinition
from app.schemas import ClassificationCandidate, DocumentProcessRequest, DocumentProcessResponse, DocumentProcessingConfigResponse, StructuredFieldDefinition, WorkflowDefinitionCreate
from app.work_item_reviews import create_pending_review_if_required

router = APIRouter(tags=["document-processing"])

@dataclass(frozen=True)
class ProcessingProfile:
    id: str
    display_name: str
    candidates: list[ClassificationCandidate]
    fields_by_label: dict[str, list[StructuredFieldDefinition]]
    work_types: dict[str, str]


def _fields(*definitions: tuple[str, str, str]) -> list[StructuredFieldDefinition]:
    return [StructuredFieldDefinition(name=name, description=description, type=field_type, required=False) for name, description, field_type in definitions]

GENERIC_OFFICE = ProcessingProfile(
    id="generic_office",
    display_name="Generic Office",
    candidates=[
        ClassificationCandidate(name="invoice", description="A request for payment for goods or services"),
        ClassificationCandidate(name="correspondence", description="Administrative correspondence between parties"),
        ClassificationCandidate(name="form", description="An administrative form with labeled fields"),
        ClassificationCandidate(name="other", description="Another general office document"),
    ],
    fields_by_label={
        "invoice": _fields(("vendor_name", "Vendor or supplier name", "string"), ("invoice_number", "Invoice identifier", "string"), ("invoice_date", "Invoice date", "date"), ("due_date", "Payment due date", "date"), ("amount_due", "Amount due", "number"), ("reference_number", "Reference identifier", "string")),
        "correspondence": _fields(("sender", "Document sender", "string"), ("recipient", "Document recipient", "string"), ("document_date", "Document date", "date"), ("subject", "Correspondence subject", "string"), ("reference_number", "Reference identifier", "string")),
        "form": _fields(("organization", "Organization name", "string"), ("form_name", "Form name", "string"), ("document_date", "Document date", "date"), ("subject", "Form subject", "string"), ("reference_number", "Reference identifier", "string")),
        "other": _fields(("document_title", "Document title", "string"), ("organization", "Organization name", "string"), ("document_date", "Document date", "date"), ("reference_number", "Reference identifier", "string")),
    },
    work_types={"invoice": "invoice_review", "correspondence": "correspondence_review", "form": "form_review", "other": "document_review"},
)

WORKFLOW_PAYLOAD = WorkflowDefinitionCreate.model_validate({
    "name": "generic_document_review", "version": 1, "description": "Generic deterministic document review workflow",
    "states": [{"name": "needs_review", "terminal": False, "review_required": True}, {"name": "completed", "terminal": True}, {"name": "rejected", "terminal": True}],
    "initial_state": "needs_review",
    "transitions": [{"from_state": "needs_review", "to_state": "completed", "review_decision": "approve"}, {"from_state": "needs_review", "to_state": "rejected", "review_decision": "reject"}],
})


def ensure_generic_review_workflow(session: Session) -> WorkflowDefinition:
    workflow = session.scalar(select(WorkflowDefinition).where(WorkflowDefinition.name == WORKFLOW_PAYLOAD.name, WorkflowDefinition.version == WORKFLOW_PAYLOAD.version))
    states = [item.model_dump(mode="json") for item in WORKFLOW_PAYLOAD.states]
    transitions = [item.model_dump(mode="json") for item in WORKFLOW_PAYLOAD.transitions]
    if workflow:
        if workflow.initial_state != WORKFLOW_PAYLOAD.initial_state or workflow.states != states or workflow.transitions != transitions:
            raise HTTPException(status_code=409, detail="Reserved generic review workflow is incompatible")
        return workflow
    workflow = WorkflowDefinition(name=WORKFLOW_PAYLOAD.name, version=1, description=WORKFLOW_PAYLOAD.description, states=states, initial_state=WORKFLOW_PAYLOAD.initial_state, transitions=transitions)
    session.add(workflow)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        workflow = session.scalar(select(WorkflowDefinition).where(WorkflowDefinition.name == WORKFLOW_PAYLOAD.name, WorkflowDefinition.version == 1))
        if workflow is None:
            raise
        if workflow.states != states or workflow.transitions != transitions:
            raise HTTPException(status_code=409, detail="Reserved generic review workflow is incompatible")
    return workflow


def _existing_result(session: Session, extraction_id: uuid.UUID):
    statement = select(WorkItem).join(DocumentStructuredExtraction).join(WorkflowDefinition).where(DocumentStructuredExtraction.document_extraction_id == extraction_id, WorkflowDefinition.name == WORKFLOW_PAYLOAD.name, WorkflowDefinition.version == 1).order_by(WorkItem.created_at.asc())
    item = session.scalar(statement)
    if not item: return None
    structured = item.document_structured_extraction
    classification = structured.document_classification
    review = session.scalar(select(WorkItemReview).where(WorkItemReview.work_item_id == item.id).order_by(WorkItemReview.created_at.asc()))
    return classification, structured, item, review


def _title(data: dict, extraction: DocumentExtraction, label: str) -> str:
    for key in ("subject", "document_title", "form_name"):
        if isinstance(data.get(key), str) and data[key].strip(): return data[key].strip()
    if isinstance(data.get("invoice_number"), str) and data["invoice_number"].strip(): return f"Invoice {data['invoice_number'].strip()}"
    event = extraction.intake_artifact.intake_event
    if event.subject and event.subject.strip(): return event.subject.strip()
    if extraction.intake_artifact.original_filename: return extraction.intake_artifact.original_filename
    return f"{label.replace('_', ' ').title()} document"


@router.get("/document-processing/config", response_model=DocumentProcessingConfigResponse)
def processing_config():
    settings = get_settings()
    configured = settings.ai_provider == "stub" or bool(settings.openai_api_key and settings.openai_api_key.get_secret_value().strip())
    return {"provider": settings.ai_provider, "provider_display_name": "Local Stub" if settings.ai_provider == "stub" else "OpenAI", "uses_external_service": settings.ai_provider == "openai", "configured": configured, "profiles": [{"id": GENERIC_OFFICE.id, "display_name": GENERIC_OFFICE.display_name}]}


@router.post("/document-extractions/{extraction_id}/process", response_model=DocumentProcessResponse, status_code=status.HTTP_201_CREATED)
def process_document(extraction_id: uuid.UUID, request: DocumentProcessRequest, session: Session = Depends(get_session), classifier: DocumentClassifier = Depends(get_document_classifier), extractor: DocumentStructuredExtractor = Depends(get_document_structured_extractor)):
    extraction = session.get(DocumentExtraction, extraction_id)
    if extraction is None: raise HTTPException(status_code=404, detail="Document extraction not found")
    if not extraction.text_content or not extraction.text_content.strip(): raise HTTPException(status_code=409, detail="Document extraction has no readable text to process")
    if request.profile_id != GENERIC_OFFICE.id: raise HTTPException(status_code=404, detail="Document processing profile not found")
    existing = _existing_result(session, extraction.id)
    if existing:
        classification, structured, item, review = existing
        return {"profile_id": request.profile_id, "provider_name": classification.provider_name, "reused": True, "classification": classification, "structured_extraction": structured, "work_item": item, "review_id": review.id}
    try:
        classified = classifier.classify(text=extraction.text_content, candidate_labels=GENERIC_OFFICE.candidates)
        labels = {item.name for item in GENERIC_OFFICE.candidates}
        if classified.label not in labels: raise ClassificationProviderError("Classifier returned a label outside the processing profile")
        fields = GENERIC_OFFICE.fields_by_label[classified.label]
        extracted = extractor.extract(text=extraction.text_content, fields=fields, classification_context={"label": classified.label, "rationale": classified.rationale})
        data = validate_extracted_data(fields, extracted.data)
    except (ClassificationProviderError, StructuredExtractionProviderError) as exc:
        session.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    workflow = ensure_generic_review_workflow(session)
    classification = DocumentClassification(document_extraction_id=extraction.id, candidate_labels=[item.model_dump(mode="json") for item in GENERIC_OFFICE.candidates], provider_name=classifier.provider_name, model_name=classifier.model_name, prompt_version=classifier.prompt_version, label=classified.label, confidence=classified.confidence, rationale=classified.rationale)
    session.add(classification); session.flush()
    structured = DocumentStructuredExtraction(document_extraction_id=extraction.id, document_classification_id=classification.id, field_schema=[item.model_dump(mode="json") for item in fields], extracted_data=data, provider_name=extractor.provider_name, model_name=extractor.model_name, prompt_version=extractor.prompt_version)
    session.add(structured); session.flush()
    item = WorkItem(workflow_definition_id=workflow.id, intake_event_id=extraction.intake_artifact.intake_event_id, document_structured_extraction_id=structured.id, work_type=GENERIC_OFFICE.work_types[classified.label], title=_title(data, extraction, classified.label), data=dict(data), current_state=workflow.initial_state, version=1)
    session.add(item); session.flush()
    session.add(WorkItemTransition(work_item_id=item.id, version=1, from_state=None, to_state=workflow.initial_state, reason=None))
    review = create_pending_review_if_required(session, item, workflow)
    if review is None: session.rollback(); raise HTTPException(status_code=500, detail="Processing workflow did not create a review")
    session.commit(); session.refresh(classification); session.refresh(structured); session.refresh(item); session.refresh(review)
    return {"profile_id": request.profile_id, "provider_name": classifier.provider_name, "reused": False, "classification": classification, "structured_extraction": structured, "work_item": item, "review_id": review.id}
