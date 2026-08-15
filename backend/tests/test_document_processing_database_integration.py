import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_engine
from app.document_classifications import get_document_classifier
from app.document_classifier import LocalStubDocumentClassifier
from app.document_structured_extractions import get_document_structured_extractor
from app.document_structured_extractor import LocalStubDocumentStructuredExtractor
from app.main import app
from app.models import DocumentExtraction, IntakeArtifact, IntakeEvent, WorkItem, WorkItemReview, WorkItemTransition

pytestmark = pytest.mark.integration


def test_postgresql_complete_stub_pipeline_atomic_lineage_and_idempotency():
    if os.getenv("ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS") != "1":
        pytest.skip("set ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 to run PostgreSQL integration tests")
    with Session(get_engine()) as session:
        event = IntakeEvent(source_type="manual_upload", subject="PostgreSQL pipeline", received_at=datetime.now(timezone.utc), raw_metadata={})
        artifact = IntakeArtifact(intake_event=event, original_filename="invoice.pdf", content_type="application/pdf", byte_size=5, sha256="b" * 64, storage_key="integration/dual-mode-pipeline")
        text = "INVOICE\nVendor Name: PostgreSQL Supply\nInvoice Number: PG-1001\nAmount Due: 75.25"
        extraction = DocumentExtraction(intake_artifact=artifact, extraction_method="pdf_text", status="extracted", page_count=1, character_count=len(text), text_content=text, page_results=[])
        session.add(extraction); session.commit(); extraction_id = extraction.id
    app.dependency_overrides[get_document_classifier] = LocalStubDocumentClassifier
    app.dependency_overrides[get_document_structured_extractor] = LocalStubDocumentStructuredExtractor
    try:
        with TestClient(app) as client:
            first = client.post(f"/document-extractions/{extraction_id}/process", json={})
            second = client.post(f"/document-extractions/{extraction_id}/process", json={})
    finally:
        app.dependency_overrides.clear()
    assert first.status_code == second.status_code == 201
    assert first.json()["reused"] is False and second.json()["reused"] is True
    with Session(get_engine()) as session:
        item = session.get(WorkItem, first.json()["work_item"]["id"])
        assert item.data["invoice_number"] == "PG-1001"
        assert item.document_structured_extraction.document_extraction_id == extraction_id
        assert len(list(session.scalars(select(WorkItemTransition).where(WorkItemTransition.work_item_id == item.id)))) == 1
        review = session.scalar(select(WorkItemReview).where(WorkItemReview.work_item_id == item.id))
        assert review.status == "pending" and review.state == "needs_review"
