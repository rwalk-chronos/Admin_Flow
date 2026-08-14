import os
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.artifact_storage import LocalArtifactStorage
from app.db import get_engine
from app.document_classifications import get_document_classifier
from app.document_classifier import ClassificationResult
from app.intake_artifacts import get_artifact_storage
from app.main import app
from app.models import (
    DocumentClassification,
    DocumentExtraction,
    IntakeArtifact,
    IntakeEvent,
)
from tests.pdf_samples import build_pdf


pytestmark = pytest.mark.integration


class IntegrationClassifier:
    provider_name = "integration-stub"
    model_name = "integration-model"
    prompt_version = "integration-v1"

    def classify(self, *, text, candidate_labels):
        assert "PostgreSQL classification document" in text
        return ClassificationResult(
            label="procedure",
            confidence=0.91,
            rationale="Integration test classification.",
        )


@pytest.fixture(autouse=True)
def require_integration_database() -> None:
    if os.getenv("ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS") != "1":
        pytest.skip(
            "set ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 to run PostgreSQL integration tests"
        )


@pytest.fixture
def clean_classification_data() -> Generator[None, None, None]:
    with Session(get_engine()) as session:
        session.execute(delete(DocumentClassification))
        session.execute(delete(DocumentExtraction))
        session.execute(delete(IntakeArtifact))
        session.execute(delete(IntakeEvent))
        session.commit()
    yield
    with Session(get_engine()) as session:
        session.execute(delete(DocumentClassification))
        session.execute(delete(DocumentExtraction))
        session.execute(delete(IntakeArtifact))
        session.execute(delete(IntakeEvent))
        session.commit()


def test_document_classification_migration_schema() -> None:
    inspector = inspect(get_engine())

    columns = {
        column["name"]
        for column in inspector.get_columns("document_classifications")
    }
    foreign_keys = inspector.get_foreign_keys("document_classifications")
    indexes = inspector.get_indexes("document_classifications")
    checks = inspector.get_check_constraints("document_classifications")

    assert columns == {
        "id",
        "document_extraction_id",
        "candidate_labels",
        "provider_name",
        "model_name",
        "prompt_version",
        "label",
        "confidence",
        "rationale",
        "created_at",
    }
    assert any(
        key["referred_table"] == "document_extractions"
        and key["constrained_columns"] == ["document_extraction_id"]
        for key in foreign_keys
    )
    assert any(
        index["column_names"] == ["document_extraction_id"]
        for index in indexes
    )
    assert {check["name"] for check in checks} >= {
        "ck_document_classifications_confidence"
    }


def test_document_classification_persists_jsonb_and_lineage(
    clean_classification_data: None,
    tmp_path: Path,
) -> None:
    storage = LocalArtifactStorage(tmp_path)
    app.dependency_overrides[get_artifact_storage] = lambda: storage
    app.dependency_overrides[get_document_classifier] = IntegrationClassifier

    try:
        with TestClient(app) as client:
            event = client.post(
                "/intake-events",
                json={
                    "source_type": "manual_upload",
                    "received_at": "2026-08-14T22:00:00Z",
                },
            ).json()
            artifact = client.post(
                f"/intake-events/{event['id']}/artifacts",
                files={
                    "file": (
                        "classification.pdf",
                        build_pdf(["PostgreSQL classification document"]),
                        "application/pdf",
                    )
                },
            ).json()
            extraction = client.post(
                f"/intake-artifacts/{artifact['id']}/extract"
            ).json()
            response = client.post(
                f"/document-extractions/{extraction['id']}/classifications",
                json={
                    "candidate_labels": [
                        {"name": "procedure", "description": "Instructions"},
                        {"name": "invoice", "description": "Payment request"},
                    ]
                },
            )
    finally:
        app.dependency_overrides.pop(get_artifact_storage, None)
        app.dependency_overrides.pop(get_document_classifier, None)

    assert response.status_code == 201
    created = response.json()
    assert created["document_extraction_id"] == extraction["id"]
    assert created["label"] == "procedure"

    with Session(get_engine()) as session:
        persisted = session.scalar(
            select(DocumentClassification).where(
                DocumentClassification.id == uuid.UUID(created["id"])
            )
        )
        assert persisted is not None
        assert persisted.document_extraction.id == uuid.UUID(extraction["id"])
        assert persisted.candidate_labels == created["candidate_labels"]
        assert persisted.provider_name == "integration-stub"
        assert persisted.created_at.tzinfo is not None
