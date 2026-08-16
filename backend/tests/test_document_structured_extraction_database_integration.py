import os
import uuid
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.db import get_engine
from app.document_structured_extractions import get_document_structured_extractor
from app.document_structured_extractor import StructuredExtractionResult
from app.main import app
from app.models import (
    ActionExecution,
    ActionPlan,
    DocumentClassification,
    DocumentExtraction,
    DocumentStructuredExtraction,
    IntakeArtifact,
    IntakeEvent,
    InternalTask,
    WorkItem,
    WorkItemReview,
    WorkItemTransition,
)

pytestmark = pytest.mark.integration


class IntegrationExtractor:
    provider_name = "integration-stub"
    model_name = "integration-structured-model"
    prompt_version = "integration-v1"

    def extract(self, *, text, fields, classification_context):
        assert text == "PostgreSQL structured extraction document"
        assert classification_context == {
            "label": "procedure",
            "rationale": "Contains steps.",
        }
        return StructuredExtractionResult(
            data={"title": "Procedure", "steps": ["First", "Second"]},
            summary="A two-step administrative procedure.",
        )


@pytest.fixture(autouse=True)
def require_integration_database() -> None:
    if os.getenv("ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS") != "1":
        pytest.skip(
            "set ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 to run PostgreSQL integration tests"
        )


@pytest.fixture
def clean_structured_extraction_data() -> Generator[None, None, None]:
    _clean_database()
    yield
    _clean_database()


def _clean_database() -> None:
    with Session(get_engine()) as session:
        session.execute(delete(InternalTask)); session.execute(delete(ActionExecution)); session.execute(delete(ActionPlan))
        session.execute(delete(WorkItemReview)); session.execute(delete(WorkItemTransition)); session.execute(delete(WorkItem))
        session.execute(delete(DocumentStructuredExtraction))
        session.execute(delete(DocumentClassification))
        session.execute(delete(DocumentExtraction))
        session.execute(delete(IntakeArtifact))
        session.execute(delete(IntakeEvent))
        session.commit()


def test_document_structured_extraction_migration_schema() -> None:
    inspector = inspect(get_engine())
    table = "document_structured_extractions"
    column_details = {column["name"]: column for column in inspector.get_columns(table)}
    columns = set(column_details)
    foreign_keys = inspector.get_foreign_keys(table)
    indexes = inspector.get_indexes(table)
    assert columns == {
        "id",
        "document_extraction_id",
        "document_classification_id",
        "field_schema",
        "extracted_data",
        "summary",
        "provider_name",
        "model_name",
        "prompt_version",
        "created_at",
    }
    assert any(
        key["referred_table"] == "document_extractions"
        and key["constrained_columns"] == ["document_extraction_id"]
        for key in foreign_keys
    )
    assert any(
        key["referred_table"] == "document_classifications"
        and key["constrained_columns"] == ["document_classification_id"]
        for key in foreign_keys
    )
    assert any(index["column_names"] == ["document_extraction_id"] for index in indexes)
    assert any(
        index["column_names"] == ["document_classification_id"] for index in indexes
    )
    assert column_details["summary"]["nullable"] is True


def test_structured_extraction_jsonb_and_lineage_round_trip(
    clean_structured_extraction_data: None,
) -> None:
    with Session(get_engine()) as session:
        event = IntakeEvent(
            source_type="manual_upload",
            received_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
            raw_metadata={},
        )
        artifact = IntakeArtifact(
            intake_event=event,
            byte_size=1,
            sha256="a" * 64,
            storage_key=f"integration/{uuid.uuid4()}",
        )
        extraction = DocumentExtraction(
            intake_artifact=artifact,
            extraction_method="pdf_text",
            status="extracted",
            page_count=1,
            character_count=41,
            text_content="PostgreSQL structured extraction document",
            page_results=[],
        )
        classification = DocumentClassification(
            document_extraction=extraction,
            candidate_labels=[{"name": "procedure", "description": None}],
            provider_name="stub",
            model_name="stub-model",
            prompt_version="stub-v1",
            label="procedure",
            confidence=0.9,
            rationale="Contains steps.",
        )
        session.add(classification)
        session.commit()
        extraction_id, classification_id = extraction.id, classification.id

    app.dependency_overrides[get_document_structured_extractor] = IntegrationExtractor
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/document-extractions/{extraction_id}/structured-extractions",
                json={
                    "document_classification_id": str(classification_id),
                    "fields": [
                        {
                            "name": "title",
                            "description": "Title",
                            "type": "string",
                            "required": True,
                        },
                        {
                            "name": "steps",
                            "description": "Steps",
                            "type": "array_string",
                            "required": False,
                        },
                    ],
                },
            )
    finally:
        app.dependency_overrides.pop(get_document_structured_extractor, None)

    assert response.status_code == 201
    created = response.json()
    with Session(get_engine()) as session:
        persisted = session.scalar(
            select(DocumentStructuredExtraction).where(
                DocumentStructuredExtraction.id == uuid.UUID(created["id"])
            )
        )
        assert persisted is not None
        assert persisted.document_extraction.id == extraction_id
        assert persisted.document_classification.id == classification_id
        assert persisted.field_schema == created["field_schema"]
        assert persisted.extracted_data == {
            "title": "Procedure",
            "steps": ["First", "Second"],
        }
        assert persisted.summary == "A two-step administrative procedure."
        assert persisted.created_at.tzinfo is not None
