import uuid
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.artifact_storage import LocalArtifactStorage
from app.db import get_session
from app.intake_artifacts import get_artifact_storage
from app.main import app
from app.models import Base, DocumentExtraction
from tests.pdf_samples import build_encrypted_pdf, build_pdf


@pytest.fixture
def engine():
    database_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalArtifactStorage:
    return LocalArtifactStorage(tmp_path)


@pytest.fixture
def client(engine, storage: LocalArtifactStorage) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_artifact_storage] = lambda: storage
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def create_artifact(
    client: TestClient,
    content: bytes,
    *,
    filename: str = "document.pdf",
    content_type: str = "application/pdf",
) -> dict:
    event_response = client.post(
        "/intake-events",
        json={
            "source_type": "manual_upload",
            "received_at": "2026-08-14T18:00:00Z",
        },
    )
    assert event_response.status_code == 201
    event_id = event_response.json()["id"]
    artifact_response = client.post(
        f"/intake-events/{event_id}/artifacts",
        files={"file": (filename, content, content_type)},
    )
    assert artifact_response.status_code == 201
    return artifact_response.json()


def extract(client: TestClient, artifact_id: str):
    return client.post(f"/intake-artifacts/{artifact_id}/extract")


def test_extracts_native_text_and_uses_actual_pdf_signature(client: TestClient) -> None:
    pdf_bytes = build_pdf(["Native document text"])
    artifact = create_artifact(
        client,
        pdf_bytes,
        filename="uploaded.bin",
        content_type="application/octet-stream",
    )

    original_before = client.get(f"/intake-artifacts/{artifact['id']}/content").content
    response = extract(client, artifact["id"])
    original_after = client.get(f"/intake-artifacts/{artifact['id']}/content").content

    assert response.status_code == 201
    assert original_before == original_after == pdf_bytes
    extraction = response.json()
    assert extraction["intake_artifact_id"] == artifact["id"]
    assert extraction["extraction_method"] == "pdf_text"
    assert extraction["status"] == "extracted"
    assert extraction["page_count"] == 1
    assert extraction["character_count"] == len("Native document text")
    assert extraction["text_content"] == "Native document text"
    assert extraction["page_results"] == [
        {
            "page_number": 1,
            "text": "Native document text",
            "character_count": len("Native document text"),
            "needs_ocr": False,
        }
    ]
    assert extraction["error_message"] is None
    assert "storage_key" not in extraction


def test_multipage_text_preserves_page_boundaries(client: TestClient) -> None:
    page_texts = ["First page", "Second page"]
    artifact = create_artifact(client, build_pdf(page_texts))

    extraction = extract(client, artifact["id"]).json()

    assert extraction["status"] == "extracted"
    assert extraction["page_count"] == 2
    assert extraction["character_count"] == sum(map(len, page_texts))
    assert extraction["text_content"] == "First page\n\nSecond page"
    assert [page["page_number"] for page in extraction["page_results"]] == [1, 2]
    assert [page["text"] for page in extraction["page_results"]] == page_texts
    assert [
        page["character_count"] for page in extraction["page_results"]
    ] == list(map(len, page_texts))
    assert all(not page["needs_ocr"] for page in extraction["page_results"])


def test_graphics_only_pdf_needs_ocr(client: TestClient) -> None:
    artifact = create_artifact(client, build_pdf([None, None]))

    extraction = extract(client, artifact["id"]).json()

    assert extraction["status"] == "needs_ocr"
    assert extraction["page_count"] == 2
    assert extraction["character_count"] == 0
    assert extraction["text_content"] is None
    assert all(page["needs_ocr"] for page in extraction["page_results"])
    assert [page["character_count"] for page in extraction["page_results"]] == [0, 0]


def test_mixed_native_and_graphics_pages_are_partial(client: TestClient) -> None:
    artifact = create_artifact(client, build_pdf(["Readable page", None]))

    extraction = extract(client, artifact["id"]).json()

    assert extraction["status"] == "partial"
    assert extraction["page_count"] == 2
    assert extraction["character_count"] == len("Readable page")
    assert [page["needs_ocr"] for page in extraction["page_results"]] == [False, True]


def test_password_protected_pdf_records_password_required(client: TestClient) -> None:
    artifact = create_artifact(client, build_encrypted_pdf("Protected text"))

    response = extract(client, artifact["id"])

    assert response.status_code == 201
    extraction = response.json()
    assert extraction["status"] == "password_required"
    assert extraction["page_count"] == 0
    assert extraction["character_count"] == 0
    assert extraction["text_content"] is None
    assert extraction["page_results"] == []
    assert extraction["error_message"] is None


def test_corrupt_pdf_records_sanitized_failure(client: TestClient) -> None:
    artifact = create_artifact(client, b"%PDF-1.4\ncorrupt data")

    response = extract(client, artifact["id"])

    assert response.status_code == 201
    extraction = response.json()
    assert extraction["status"] == "failed"
    assert extraction["page_count"] == 0
    assert extraction["page_results"] == []
    assert extraction["error_message"].startswith("PDF extraction failed:")
    assert "Traceback" not in extraction["error_message"]


def test_unsupported_non_pdf_is_rejected_without_extraction(client: TestClient) -> None:
    artifact = create_artifact(
        client,
        b"plain text, not a PDF",
        filename="notes.txt",
        content_type="text/plain",
    )

    response = extract(client, artifact["id"])
    list_response = client.get(f"/intake-artifacts/{artifact['id']}/extractions")

    assert response.status_code == 415
    assert response.json() == {"detail": "Intake artifact is not a supported PDF"}
    assert list_response.status_code == 200
    assert list_response.json() == []


def test_unknown_artifact_and_extraction_return_not_found(client: TestClient) -> None:
    artifact_id = uuid.uuid4()
    extraction_id = uuid.uuid4()

    assert extract(client, str(artifact_id)).status_code == 404
    assert client.get(f"/intake-artifacts/{artifact_id}/extractions").status_code == 404
    assert client.get(f"/document-extractions/{extraction_id}").status_code == 404


def test_multiple_extractions_persist_and_list_newest_first(
    client: TestClient, engine
) -> None:
    artifact = create_artifact(client, build_pdf(["Repeatable text"]))
    first = extract(client, artifact["id"]).json()

    with Session(engine) as session:
        session.execute(
            update(DocumentExtraction)
            .where(DocumentExtraction.id == uuid.UUID(first["id"]))
            .values(created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        )
        session.commit()

    second = extract(client, artifact["id"]).json()
    list_response = client.get(f"/intake-artifacts/{artifact['id']}/extractions")
    retrieval_response = client.get(f"/document-extractions/{first['id']}")

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [
        second["id"],
        first["id"],
    ]
    assert retrieval_response.status_code == 200
    assert retrieval_response.json()["id"] == first["id"]
