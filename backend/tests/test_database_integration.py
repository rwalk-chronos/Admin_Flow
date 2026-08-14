import os
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.artifact_storage import LocalArtifactStorage
from app.db import database_is_ready, get_engine
from app.intake_artifacts import get_artifact_storage
from app.main import app
from app.models import DocumentExtraction, IntakeArtifact, IntakeEvent
from tests.pdf_samples import build_pdf


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_integration_database() -> None:
    if os.getenv("ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS") != "1":
        pytest.skip(
            "set ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 to run PostgreSQL integration tests"
        )


@pytest.fixture
def clean_intake_events() -> Generator[None, None, None]:
    with Session(get_engine()) as session:
        session.execute(delete(DocumentExtraction))
        session.execute(delete(IntakeArtifact))
        session.execute(delete(IntakeEvent))
        session.commit()
    yield
    with Session(get_engine()) as session:
        session.execute(delete(DocumentExtraction))
        session.execute(delete(IntakeArtifact))
        session.execute(delete(IntakeEvent))
        session.commit()


def test_postgresql_connection() -> None:
    assert database_is_ready() is True


def test_intake_event_persists_with_json_and_status_defaults(
    clean_intake_events: None,
) -> None:
    payload = {
        "source_type": "api",
        "external_id": "origin-456",
        "sender": "external-system",
        "recipient": "adminflow",
        "subject": "Administrative request",
        "body_text": "Persist this source material.",
        "received_at": "2026-08-14T15:00:00-04:00",
        "raw_metadata": {"delivery": {"attempt": 1}, "tags": ["external"]},
    }

    with TestClient(app) as client:
        response = client.post("/intake-events", json=payload)

    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "received"
    assert created["raw_metadata"] == payload["raw_metadata"]

    with Session(get_engine()) as session:
        persisted = session.scalar(
            select(IntakeEvent).where(IntakeEvent.id == uuid.UUID(created["id"]))
        )

    assert persisted is not None
    assert persisted.external_id == "origin-456"
    assert persisted.status == "received"
    assert persisted.raw_metadata == payload["raw_metadata"]
    assert persisted.received_at.tzinfo is not None


def test_intake_event_listing_retrieval_and_unknown_id(
    clean_intake_events: None,
) -> None:
    with TestClient(app) as client:
        older = client.post(
            "/intake-events",
            json={
                "source_type": "manual_upload",
                "received_at": "2026-08-13T12:00:00Z",
            },
        ).json()
        newer = client.post(
            "/intake-events",
            json={
                "source_type": "scanner",
                "received_at": "2026-08-14T12:00:00Z",
                "raw_metadata": {"device": "scanner-1"},
            },
        ).json()

        list_response = client.get("/intake-events")
        retrieve_response = client.get(f"/intake-events/{newer['id']}")
        missing_response = client.get(f"/intake-events/{uuid.uuid4()}")

    assert list_response.status_code == 200
    assert [event["id"] for event in list_response.json()] == [
        newer["id"],
        older["id"],
    ]
    assert retrieve_response.status_code == 200
    assert retrieve_response.json()["raw_metadata"] == {"device": "scanner-1"}
    assert missing_response.status_code == 404


def test_intake_artifact_persists_in_postgresql_and_local_storage(
    clean_intake_events: None, tmp_path: Path
) -> None:
    storage = LocalArtifactStorage(tmp_path)
    app.dependency_overrides[get_artifact_storage] = lambda: storage
    content = b"postgresql-backed original artifact"

    try:
        with TestClient(app) as client:
            event = client.post(
                "/intake-events",
                json={
                    "source_type": "manual_upload",
                    "received_at": "2026-08-14T17:00:00Z",
                },
            ).json()
            response = client.post(
                f"/intake-events/{event['id']}/artifacts",
                files={"file": ("source.bin", content, "application/octet-stream")},
            )
    finally:
        app.dependency_overrides.pop(get_artifact_storage, None)

    assert response.status_code == 201
    created = response.json()

    with Session(get_engine()) as session:
        artifact = session.scalar(
            select(IntakeArtifact).where(
                IntakeArtifact.id == uuid.UUID(created["id"])
            )
        )

    assert artifact is not None
    assert artifact.intake_event_id == uuid.UUID(event["id"])
    assert artifact.original_filename == "source.bin"
    assert artifact.content_type == "application/octet-stream"
    assert artifact.byte_size == len(content)
    assert artifact.sha256 == created["sha256"]
    assert artifact.created_at.tzinfo is not None
    with storage.open(artifact.storage_key) as stored_file:
        assert stored_file.read() == content


def test_intake_artifact_migration_schema() -> None:
    inspector = inspect(get_engine())

    columns = {column["name"] for column in inspector.get_columns("intake_artifacts")}
    foreign_keys = inspector.get_foreign_keys("intake_artifacts")
    indexes = inspector.get_indexes("intake_artifacts")
    checks = inspector.get_check_constraints("intake_artifacts")

    assert columns == {
        "id",
        "intake_event_id",
        "original_filename",
        "content_type",
        "byte_size",
        "sha256",
        "storage_key",
        "created_at",
    }
    assert any(
        key["referred_table"] == "intake_events"
        and key["constrained_columns"] == ["intake_event_id"]
        for key in foreign_keys
    )
    assert any(
        index["column_names"] == ["intake_event_id"] for index in indexes
    )
    assert {check["name"] for check in checks} >= {
        "ck_intake_artifacts_byte_size",
        "ck_intake_artifacts_sha256_length",
    }


def test_document_extraction_persists_jsonb_and_artifact_relationship(
    clean_intake_events: None, tmp_path: Path
) -> None:
    storage = LocalArtifactStorage(tmp_path)
    app.dependency_overrides[get_artifact_storage] = lambda: storage

    try:
        with TestClient(app) as client:
            event = client.post(
                "/intake-events",
                json={
                    "source_type": "manual_upload",
                    "received_at": "2026-08-14T19:00:00Z",
                },
            ).json()
            artifact = client.post(
                f"/intake-events/{event['id']}/artifacts",
                files={
                    "file": (
                        "native.pdf",
                        build_pdf(["PostgreSQL page one", "PostgreSQL page two"]),
                        "application/pdf",
                    )
                },
            ).json()
            response = client.post(
                f"/intake-artifacts/{artifact['id']}/extract"
            )
    finally:
        app.dependency_overrides.pop(get_artifact_storage, None)

    assert response.status_code == 201
    created = response.json()

    with Session(get_engine()) as session:
        extraction = session.scalar(
            select(DocumentExtraction).where(
                DocumentExtraction.id == uuid.UUID(created["id"])
            )
        )
        assert extraction is not None
        assert extraction.intake_artifact.id == uuid.UUID(artifact["id"])
        assert extraction.status == "extracted"
        assert extraction.page_count == 2
        assert extraction.page_results == created["page_results"]
        assert extraction.page_results[0]["page_number"] == 1
        assert extraction.page_results[1]["page_number"] == 2
        assert extraction.created_at.tzinfo is not None


def test_document_extraction_migration_schema() -> None:
    inspector = inspect(get_engine())

    columns = {
        column["name"] for column in inspector.get_columns("document_extractions")
    }
    foreign_keys = inspector.get_foreign_keys("document_extractions")
    indexes = inspector.get_indexes("document_extractions")
    checks = inspector.get_check_constraints("document_extractions")

    assert columns == {
        "id",
        "intake_artifact_id",
        "extraction_method",
        "status",
        "page_count",
        "character_count",
        "text_content",
        "page_results",
        "error_message",
        "created_at",
    }
    assert any(
        key["referred_table"] == "intake_artifacts"
        and key["constrained_columns"] == ["intake_artifact_id"]
        for key in foreign_keys
    )
    assert any(
        index["column_names"] == ["intake_artifact_id"] for index in indexes
    )
    assert {check["name"] for check in checks} >= {
        "ck_document_extractions_status",
        "ck_document_extractions_page_count",
        "ck_document_extractions_character_count",
    }
