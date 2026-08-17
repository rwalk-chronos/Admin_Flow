import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_engine
from app.document_classifications import get_document_classifier
from app.document_classifier import ClassificationResult, LocalStubDocumentClassifier
from app.document_structured_extractions import get_document_structured_extractor
from app.document_structured_extractor import LocalStubDocumentStructuredExtractor
from app.main import app
from app.models import ActionExecution, ActionPlan, DocumentClassification, DocumentExtraction, DocumentStructuredExtraction, IntakeArtifact, IntakeEvent, InternalTask, WorkItem, WorkItemReview, WorkItemTransition, WorkflowDefinition

pytestmark = pytest.mark.integration


class ConcurrentClassifier(LocalStubDocumentClassifier):
    barrier = threading.Barrier(2)

    def classify(self, *, text, candidate_labels):
        self.barrier.wait(timeout=10)
        return ClassificationResult(
            label="invoice",
            confidence=1.0,
            rationale="Synchronized deterministic test classification",
        )


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
            review = client.get(f"/work-item-reviews/{first.json()['review_id']}").json()
            approved = client.post(f"/work-item-reviews/{review['id']}/resolve", json={
                "decision": "approve", "expected_work_item_state": review["state"],
                "expected_work_item_version": review["work_item_version"], "reviewer": "integration-reviewer",
                "reviewed_data": review["work_item_data"], "action_plan_id": first.json()["action_plan_id"],
            })
            task = client.get("/internal-tasks?status=open").json()[0]
            completed = client.post(
                f"/internal-tasks/{task['id']}/complete",
                json={"completed_by": "integration-worker", "completion_note": "PostgreSQL completion"},
            )
            repeated = client.post(
                f"/internal-tasks/{task['id']}/complete",
                json={"completed_by": "different-worker", "completion_note": "Must not overwrite"},
            )
    finally:
        app.dependency_overrides.clear()
    assert first.status_code == second.status_code == 201
    assert approved.status_code == 201
    assert approved.json()["current_state"] == "awaiting_task_completion"
    assert completed.status_code == repeated.status_code == 200
    assert completed.json() == repeated.json()
    assert completed.json()["completed_by"] == "integration-worker"
    assert first.json()["reused"] is False and second.json()["reused"] is True
    with Session(get_engine()) as session:
        item = session.get(WorkItem, first.json()["work_item"]["id"])
        assert item.data["invoice_number"] == "PG-1001"
        assert item.document_structured_extraction.document_extraction_id == extraction_id
        assert (item.current_state, item.version) == ("completed", 4)
        transitions = list(session.scalars(select(WorkItemTransition).where(WorkItemTransition.work_item_id == item.id).order_by(WorkItemTransition.version)))
        assert [(row.version, row.to_state) for row in transitions] == [
            (1, "needs_review"),
            (2, "approved_for_action"),
            (3, "awaiting_task_completion"),
            (4, "completed"),
        ]
        review = session.scalar(select(WorkItemReview).where(WorkItemReview.work_item_id == item.id))
        assert review.status == "approved" and review.state == "needs_review"
        plan = session.scalar(select(ActionPlan).where(ActionPlan.work_item_id == item.id))
        assert plan.action_type == "create_internal_task"
        task_row = session.scalar(select(InternalTask).where(InternalTask.work_item_id == item.id))
        assert (task_row.status, task_row.completed_by, task_row.completion_note) == (
            "completed", "integration-worker", "PostgreSQL completion",
        )
        session.execute(delete(InternalTask).where(InternalTask.work_item_id == item.id))
        session.execute(delete(ActionExecution).where(ActionExecution.action_plan_id == plan.id))
        session.execute(delete(WorkItemReview).where(WorkItemReview.work_item_id == item.id))
        session.execute(delete(ActionPlan).where(ActionPlan.work_item_id == item.id))
        session.execute(delete(WorkItemTransition).where(WorkItemTransition.work_item_id == item.id))
        session.execute(delete(WorkItem).where(WorkItem.id == item.id))
        session.execute(delete(DocumentStructuredExtraction).where(DocumentStructuredExtraction.document_extraction_id == extraction_id))
        session.execute(delete(DocumentClassification).where(DocumentClassification.document_extraction_id == extraction_id))
        session.execute(delete(DocumentExtraction).where(DocumentExtraction.id == extraction_id))
        session.execute(delete(IntakeArtifact).where(IntakeArtifact.storage_key == "integration/dual-mode-pipeline"))
        session.execute(delete(IntakeEvent).where(IntakeEvent.subject == "PostgreSQL pipeline"))
        session.execute(delete(WorkflowDefinition).where(WorkflowDefinition.name == "generic_document_review"))
        session.commit()


def test_postgresql_concurrent_processing_is_idempotent():
    if os.getenv("ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS") != "1":
        pytest.skip("set ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 to run PostgreSQL integration tests")
    storage_key = "integration/concurrent-dual-mode-pipeline"
    subject = "PostgreSQL concurrent pipeline"
    text = "INVOICE\nVendor Name: Concurrent Supply\nInvoice Number: RACE-1001\nAmount Due: 25.50"
    with Session(get_engine()) as session:
        event = IntakeEvent(source_type="manual_upload", subject=subject, received_at=datetime.now(timezone.utc), raw_metadata={})
        artifact = IntakeArtifact(intake_event=event, original_filename="race.pdf", content_type="application/pdf", byte_size=5, sha256="c" * 64, storage_key=storage_key)
        extraction = DocumentExtraction(intake_artifact=artifact, extraction_method="pdf_text", status="extracted", page_count=1, character_count=len(text), text_content=text, page_results=[])
        session.add(extraction)
        session.commit()
        extraction_id = extraction.id
        artifact_id = artifact.id
        source_snapshot = (extraction.status, extraction.text_content, extraction.character_count)
        artifact_snapshot = (artifact.sha256, artifact.storage_key, artifact.byte_size)

    ConcurrentClassifier.barrier = threading.Barrier(2)
    app.dependency_overrides[get_document_classifier] = ConcurrentClassifier
    app.dependency_overrides[get_document_structured_extractor] = LocalStubDocumentStructuredExtractor

    def process():
        with TestClient(app) as client:
            return client.post(f"/document-extractions/{extraction_id}/process", json={})

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: process(), range(2)))
    finally:
        app.dependency_overrides.clear()

    assert [response.status_code for response in responses] == [201, 201]
    payloads = [response.json() for response in responses]
    assert sorted(payload["reused"] for payload in payloads) == [False, True]
    assert len({payload["work_item"]["id"] for payload in payloads}) == 1
    assert len({payload["review_id"] for payload in payloads}) == 1

    with Session(get_engine()) as session:
        classifications = list(session.scalars(select(DocumentClassification).where(DocumentClassification.document_extraction_id == extraction_id)))
        structured = list(session.scalars(select(DocumentStructuredExtraction).where(DocumentStructuredExtraction.document_extraction_id == extraction_id)))
        items = list(session.scalars(select(WorkItem).where(WorkItem.document_structured_extraction_id.in_([row.id for row in structured]))))
        transitions = list(session.scalars(select(WorkItemTransition).where(WorkItemTransition.work_item_id.in_([row.id for row in items]))))
        reviews = list(session.scalars(select(WorkItemReview).where(WorkItemReview.work_item_id.in_([row.id for row in items]))))
        assert len(classifications) == len(structured) == len(items) == len(transitions) == len(reviews) == 1
        assert payloads[0]["work_item"]["id"] == str(items[0].id)
        assert payloads[0]["review_id"] == str(reviews[0].id)
        source = session.get(DocumentExtraction, extraction_id)
        source_artifact = session.get(IntakeArtifact, artifact_id)
        assert (source.status, source.text_content, source.character_count) == source_snapshot
        assert (source_artifact.sha256, source_artifact.storage_key, source_artifact.byte_size) == artifact_snapshot

        plan_ids = select(ActionPlan.id).where(ActionPlan.work_item_id == items[0].id)
        session.execute(delete(InternalTask).where(InternalTask.work_item_id == items[0].id))
        session.execute(delete(ActionExecution).where(ActionExecution.action_plan_id.in_(plan_ids)))
        session.execute(delete(WorkItemReview).where(WorkItemReview.work_item_id == items[0].id))
        session.execute(delete(ActionPlan).where(ActionPlan.work_item_id == items[0].id))
        session.execute(delete(WorkItemTransition).where(WorkItemTransition.work_item_id == items[0].id))
        session.execute(delete(WorkItem).where(WorkItem.id == items[0].id))
        session.execute(delete(DocumentStructuredExtraction).where(DocumentStructuredExtraction.document_extraction_id == extraction_id))
        session.execute(delete(DocumentClassification).where(DocumentClassification.document_extraction_id == extraction_id))
        session.execute(delete(DocumentExtraction).where(DocumentExtraction.id == extraction_id))
        session.execute(delete(IntakeArtifact).where(IntakeArtifact.id == artifact_id))
        session.execute(delete(IntakeEvent).where(IntakeEvent.subject == subject))
        session.execute(delete(WorkflowDefinition).where(WorkflowDefinition.name == "generic_document_review"))
        session.commit()
