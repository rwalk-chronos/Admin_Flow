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
from app.models import Base, DocumentClassification, DocumentExtraction, DocumentStructuredExtraction, IntakeArtifact, IntakeEvent, WorkItem, WorkItemReview, WorkItemTransition, WorkflowDefinition
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
