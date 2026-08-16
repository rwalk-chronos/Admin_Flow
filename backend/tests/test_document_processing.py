import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.document_classifications import get_document_classifier
from app.document_classifier import LocalStubDocumentClassifier
from app.document_structured_extractions import get_document_structured_extractor
from app.document_structured_extractor import LocalStubDocumentStructuredExtractor, StructuredExtractionProviderError, validate_extracted_data
from app.main import app
from app.models import ActionExecution, ActionPlan, Base, DocumentClassification, DocumentExtraction, DocumentStructuredExtraction, IntakeArtifact, IntakeEvent, InternalTask, WorkItem, WorkItemReview, WorkItemTransition, WorkflowDefinition
from app.schemas import ClassificationCandidate, StructuredFieldDefinition


@pytest.fixture
def engine():
    value = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(value)
    yield value
    value.dispose()


@pytest.fixture
def client(engine):
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_document_classifier] = LocalStubDocumentClassifier
    app.dependency_overrides[get_document_structured_extractor] = LocalStubDocumentStructuredExtractor
    try:
        with TestClient(app) as value:
            yield value
    finally:
        app.dependency_overrides.clear()


def extraction(engine, text=None):
    text = text if text is not None else "INVOICE\nVendor Name: Example Office Supply\nInvoice Number: INV-1001\nInvoice Date: 2026-08-15\nDue Date: 09/15/2026\nAmount Due: 125.50\nReference Number: DEMO-1001"
    with Session(engine) as session:
        event = IntakeEvent(source_type="manual_upload", subject="Office invoice", received_at=datetime.now(timezone.utc), raw_metadata={})
        artifact = IntakeArtifact(intake_event=event, original_filename="invoice.pdf", content_type="application/pdf", byte_size=10, sha256="a" * 64, storage_key=str(uuid.uuid4()))
        item = DocumentExtraction(intake_artifact=artifact, extraction_method="pdf_text", status="extracted" if text else "needs_ocr", page_count=1, character_count=len(text), text_content=text, page_results=[])
        session.add(item); session.commit(); return str(item.id)


def test_stub_classifier_matches_case_insensitive_ties_and_fallbacks():
    provider = LocalStubDocumentClassifier()
    candidates = [ClassificationCandidate(name="invoice"), ClassificationCandidate(name="form"), ClassificationCandidate(name="other")]
    matched = provider.classify(text="INVOICE invoice and FORM form", candidate_labels=candidates)
    assert matched.label == "invoice"
    assert 0 <= matched.confidence <= 1 and matched.rationale
    assert provider.classify(text="unmatched", candidate_labels=candidates).label == "other"
    assert provider.classify(text="unmatched", candidate_labels=candidates[:2]).label == "form"
    assert provider.provider_name == "local_stub"


def test_stub_structured_extractor_types_missing_and_validation():
    provider = LocalStubDocumentStructuredExtractor()
    fields = [
        StructuredFieldDefinition(name="title", description="Title", type="string", required=True),
        StructuredFieldDefinition(name="count", description="Count", type="integer", required=True),
        StructuredFieldDefinition(name="amount", description="Amount", type="number", required=True),
        StructuredFieldDefinition(name="active", description="Active", type="boolean", required=True),
        StructuredFieldDefinition(name="document_date", description="Date", type="date", required=True),
        StructuredFieldDefinition(name="items", description="Items", type="array_string", required=True),
        StructuredFieldDefinition(name="optional", description="Optional", type="string", required=False),
    ]
    result = provider.extract(text="Title: Demo\nCount: 12\nAmount: 12.50\nActive: yes\nDocument Date: 08/15/2026\nItems: one, two; three", fields=fields, classification_context=None)
    assert result.data == {"title": "Demo", "count": 12, "amount": 12.5, "active": True, "document_date": "2026-08-15", "items": ["one", "two", "three"], "optional": None}
    assert validate_extracted_data(fields, result.data) == result.data
    with pytest.raises(StructuredExtractionProviderError):
        provider.extract(text="Title: Demo", fields=[StructuredFieldDefinition(name="count", description="Count", type="integer", required=True)], classification_context=None)
    with pytest.raises(StructuredExtractionProviderError):
        provider.extract(text="Count: twelve", fields=[StructuredFieldDefinition(name="count", description="Count", type="integer", required=True)], classification_context=None)


def test_processing_config_is_non_secret_and_stub_ready(client):
    response = client.get("/document-processing/config")
    assert response.status_code == 200
    assert response.json() == {"provider": "stub", "provider_display_name": "Local Stub", "uses_external_service": False, "configured": True, "profiles": [{"id": "generic_office", "display_name": "Generic Office"}]}
    assert "key" not in response.text.casefold()


def test_pipeline_persists_atomic_lineage_review_and_is_idempotent(client, engine):
    extraction_id = extraction(engine)
    first = client.post(f"/document-extractions/{extraction_id}/process", json={"profile_id": "generic_office"})
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["reused"] is False
    assert body["provider_name"] == "local_stub"
    assert body["classification"]["label"] == "invoice"
    assert body["structured_extraction"]["extracted_data"]["invoice_number"] == "INV-1001"
    assert body["structured_extraction"]["extracted_data"]["due_date"] == "2026-09-15"
    assert body["work_item"]["title"] == "Invoice INV-1001"
    assert body["work_item"]["current_state"] == "needs_review"
    assert body["work_item"]["data"] == body["structured_extraction"]["extracted_data"]
    second = client.post(f"/document-extractions/{extraction_id}/process", json={})
    assert second.status_code == 201
    assert second.json()["reused"] is True
    assert second.json()["work_item"]["id"] == body["work_item"]["id"]
    with Session(engine) as session:
        assert session.scalar(select(func.count(DocumentClassification.id))) == 1
        assert session.scalar(select(func.count(DocumentStructuredExtraction.id))) == 1
        assert session.scalar(select(func.count(WorkItem.id))) == 1
        assert session.scalar(select(func.count(WorkItemTransition.id))) == 1
        review = session.scalar(select(WorkItemReview))
        assert review.id == uuid.UUID(body["review_id"]) and review.status == "pending"
        assert session.scalar(select(WorkflowDefinition)).name == "generic_document_review"


def test_decision_packet_projects_human_type_summary_facts_attention_and_source(client, engine):
    result = client.post(f"/document-extractions/{extraction(engine, 'FORM')}/process", json={}).json()
    with Session(engine) as session:
        classification = session.get(DocumentClassification, uuid.UUID(result["classification"]["id"]))
        classification.confidence = 0.42
        session.commit()
    response = client.get(f"/work-item-reviews/{result['review_id']}/decision-packet")
    assert response.status_code == 200, response.text
    packet = response.json()
    assert packet["document_type"] == "Form"
    assert packet["confidence"] == 0.42
    assert packet["confidence_band"] == "Low confidence"
    assert packet["summary"] == "AdminFlow identified this as a form, but the main form details were not identified."
    assert [fact["label"] for fact in packet["key_information"]] == ["Organization", "Document name", "Document date", "Subject", "Reference number"]
    assert all(fact["display_value"] == "Not identified" and fact["missing"] for fact in packet["key_information"])
    assert any("low confidence" in item["title"] for item in packet["attention_items"])
    assert any(item["title"] == "Organization was not identified." for item in packet["attention_items"])
    assert packet["action_plan"]["approval_label"] == "Approve & Create Task"
    assert packet["action_plan"]["external_effect"] == "No external message will be sent."
    assert packet["artifacts"][0]["original_filename"] == "invoice.pdf"
    assert packet["correction_schema"][0]["name"] == "organization"


@pytest.mark.parametrize(("confidence", "band"), [(0.85, "High confidence"), (0.60, "Moderate confidence"), (0.59, "Low confidence")])
def test_decision_packet_confidence_thresholds_are_deterministic(client, engine, confidence, band):
    result = client.post(f"/document-extractions/{extraction(engine)}/process", json={}).json()
    with Session(engine) as session:
        classification = session.get(DocumentClassification, uuid.UUID(result["classification"]["id"]))
        classification.confidence = confidence
        session.commit()
    assert client.get(f"/work-item-reviews/{result['review_id']}/decision-packet").json()["confidence_band"] == band


def test_exact_action_plan_authorization_creates_one_internal_task(client, engine):
    result = client.post(f"/document-extractions/{extraction(engine)}/process", json={}).json()
    review = client.get(f"/work-item-reviews/{result['review_id']}").json()
    plan = client.get(f"/action-plans/{result['action_plan_id']}").json()
    assert plan["action_type"] == "create_internal_task"
    assert plan["external_effect"] == "No external message will be sent."
    assert len(plan["source_artifact_ids"]) == 1

    missing = client.post(f"/work-item-reviews/{review['id']}/resolve", json={
        "decision": "approve", "expected_work_item_state": review["state"],
        "expected_work_item_version": review["work_item_version"], "reviewer": "office-user",
        "reviewed_data": review["work_item_data"],
    })
    assert missing.status_code == 409
    approved = client.post(f"/work-item-reviews/{review['id']}/resolve", json={
        "decision": "approve", "expected_work_item_state": review["state"],
        "expected_work_item_version": review["work_item_version"], "reviewer": "office-user",
        "reviewed_data": review["work_item_data"], "action_plan_id": plan["id"],
    })
    assert approved.status_code == 201, approved.text
    assert approved.json()["authorized_action_plan_id"] == plan["id"]
    assert approved.json()["current_state"] == "completed"
    assert len(client.get("/internal-tasks").json()) == 1
    assert client.get(f"/action-plans/{plan['id']}/executions").json()[0]["status"] == "succeeded"
    completed_packet = client.get(f"/work-items/{review['work_item_id']}/decision-packet").json()
    assert completed_packet["status_label"] == "Completed"
    assert completed_packet["action_result"]["message"] == "Internal task created successfully"
    assert completed_packet["action_result"]["queue"] == "Accounts Payable"
    assert completed_packet["review"]["reviewer"] == "office-user"
    duplicate = client.post(f"/work-item-reviews/{review['id']}/resolve", json={
        "decision": "approve", "expected_work_item_state": review["state"],
        "expected_work_item_version": review["work_item_version"], "reviewer": "office-user",
        "reviewed_data": review["work_item_data"], "action_plan_id": plan["id"],
    })
    assert duplicate.status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count(ActionExecution.id))) == 1
        assert session.scalar(select(func.count(InternalTask.id))) == 1


def test_correction_revises_plan_and_manual_path_executes_nothing(client, engine):
    result = client.post(f"/document-extractions/{extraction(engine)}/process", json={}).json()
    review = client.get(f"/work-item-reviews/{result['review_id']}").json()
    corrected = dict(review["work_item_data"]); corrected["amount_due"] = 200.0
    revised = client.post(f"/work-item-reviews/{review['id']}/action-plan", json={
        "expected_work_item_state": review["state"], "expected_work_item_version": review["work_item_version"],
        "reviewed_data": corrected,
    })
    assert revised.status_code == 200
    assert revised.json()["revision"] == 2
    revised_packet = client.get(f"/work-item-reviews/{review['id']}/decision-packet").json()
    assert revised_packet["correction_data"]["amount_due"] == 200.0
    assert next(fact for fact in revised_packet["key_information"] if fact["key"] == "amount_due")["display_value"] == "$200.00"
    assert revised_packet["action_plan"]["id"] == revised.json()["id"]
    plans = client.get(f"/work-items/{review['work_item_id']}/action-plans").json()
    assert plans[0]["superseded_reason"] == "Reviewed facts changed"
    stale_approval = client.post(f"/work-item-reviews/{review['id']}/resolve", json={
        "decision": "approve", "expected_work_item_state": review["state"],
        "expected_work_item_version": review["work_item_version"], "reviewer": "office-user",
        "reviewed_data": corrected, "action_plan_id": plans[0]["id"],
    })
    assert stale_approval.status_code == 409
    assert stale_approval.json()["detail"] == "Approval must authorize the exact current Action Plan"
    handled = client.post(f"/work-item-reviews/{review['id']}/resolve", json={
        "decision": "handle_manually", "expected_work_item_state": review["state"],
        "expected_work_item_version": review["work_item_version"], "reviewer": "office-user",
        "notes": "Will complete outside AdminFlow",
    })
    assert handled.status_code == 201, handled.text
    assert handled.json()["current_state"] == "manual_handling"
    assert client.get("/internal-tasks").json() == []
    with Session(engine) as session:
        assert session.scalar(select(func.count(ActionPlan.id))) == 2


def test_pipeline_errors_and_provider_failure_persist_nothing(client, engine):
    assert client.post(f"/document-extractions/{uuid.uuid4()}/process", json={}).status_code == 404
    empty_id = extraction(engine, "")
    assert client.post(f"/document-extractions/{empty_id}/process", json={}).status_code == 409
    valid_id = extraction(engine, "OTHER\nDocument Title: Demo")
    assert client.post(f"/document-extractions/{valid_id}/process", json={"profile_id": "bad"}).status_code == 422
    class Broken:
        provider_name = model_name = prompt_version = "broken"
        def classify(self, **kwargs): raise StructuredExtractionProviderError("broken")
    app.dependency_overrides[get_document_classifier] = Broken
    response = client.post(f"/document-extractions/{valid_id}/process", json={})
    assert response.status_code == 502
    with Session(engine) as session:
        assert session.scalar(select(func.count(WorkItem.id))) == 0


def test_provider_settings_are_explicit_and_openai_requires_key(monkeypatch):
    from pydantic import ValidationError
    from app.config import Settings, get_settings
    from app.document_classifications import get_document_classifier
    from app.document_structured_extractions import get_document_structured_extractor
    from app.document_classifier import OpenAIDocumentClassifier
    from app.document_structured_extractor import OpenAIDocumentStructuredExtractor

    assert Settings(ai_provider="stub", openai_api_key=None).ai_provider == "stub"
    with pytest.raises(ValidationError):
        Settings(ai_provider="invalid")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear(); get_document_classifier.cache_clear(); get_document_structured_extractor.cache_clear()
    try:
        assert isinstance(get_document_classifier(), OpenAIDocumentClassifier)
        assert isinstance(get_document_structured_extractor(), OpenAIDocumentStructuredExtractor)
    finally:
        get_document_classifier.cache_clear(); get_document_structured_extractor.cache_clear(); get_settings.cache_clear()
