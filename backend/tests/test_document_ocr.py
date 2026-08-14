import shutil
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.artifact_storage import LocalArtifactStorage
from app.db import get_session
from app.document_ocr import OcrProcessingError
from app.document_ocr_api import get_ocr_engine
from app.intake_artifacts import get_artifact_storage
from app.main import app
from app.models import Base, DocumentExtraction, IntakeArtifact
from tests.pdf_samples import build_encrypted_pdf, build_image_pdf, build_pdf, combine_pdfs


class CountingOcrEngine:
    def __init__(self, text: str = "OCR PAGE TWO") -> None:
        self.text = text
        self.calls: list[str] = []

    def extract_text(self, image_path: Path) -> str:
        assert image_path.exists()
        self.calls.append(image_path.name)
        return self.text


class FailingOcrEngine:
    def extract_text(self, image_path: Path) -> str:
        raise OcrProcessingError("Tesseract timed out after 1 seconds")


@pytest.fixture
def engine():
    database_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalArtifactStorage:
    return LocalArtifactStorage(tmp_path)


@pytest.fixture
def client(engine, storage) -> Generator[TestClient, None, None]:
    def override_session():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_artifact_storage] = lambda: storage
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def create_artifact(client: TestClient, content: bytes) -> dict:
    event = client.post("/intake-events", json={"source_type": "manual_upload", "received_at": "2026-08-14T18:00:00Z"}).json()
    response = client.post(f"/intake-events/{event['id']}/artifacts", files={"file": ("document.pdf", content, "application/pdf")})
    assert response.status_code == 201
    return response.json()


def native_extract(client: TestClient, artifact_id: str) -> dict:
    response = client.post(f"/intake-artifacts/{artifact_id}/extract")
    assert response.status_code == 201
    return response.json()


def test_selective_ocr_preserves_native_pages_and_lineage(client, engine) -> None:
    pdf = combine_pdfs(build_pdf(["NATIVE PAGE ONE"]), build_image_pdf("ADMINFLOW OCR TEST"), build_pdf(["NATIVE PAGE THREE"]))
    artifact = create_artifact(client, pdf)
    source = native_extract(client, artifact["id"])
    source_pages = source["page_results"]
    counting_engine = CountingOcrEngine()
    app.dependency_overrides[get_ocr_engine] = lambda: counting_engine
    original_before = client.get(f"/intake-artifacts/{artifact['id']}/content").content
    response = client.post(f"/document-extractions/{source['id']}/ocr")
    original_after = client.get(f"/intake-artifacts/{artifact['id']}/content").content
    assert source["status"] == "partial"
    assert response.status_code == 201
    derived = response.json()
    assert counting_engine.calls == ["page-2.png"]
    assert derived["extraction_method"] == "pdf_text_ocr"
    assert derived["source_extraction_id"] == source["id"]
    assert derived["status"] == "extracted"
    assert derived["page_results"][0] == {**source_pages[0], "text_source": "native_text"}
    assert derived["page_results"][2] == {**source_pages[2], "text_source": "native_text"}
    assert derived["page_results"][1]["text"] == "OCR PAGE TWO"
    assert derived["page_results"][1]["text_source"] == "ocr"
    assert derived["page_results"][1]["needs_ocr"] is False
    assert derived["text_content"] == "NATIVE PAGE ONE\n\nOCR PAGE TWO\n\nNATIVE PAGE THREE"
    assert derived["character_count"] == sum(page["character_count"] for page in derived["page_results"])
    assert original_before == original_after == pdf
    assert client.get(f"/document-extractions/{source['id']}").json()["page_results"] == source_pages
    with Session(engine) as session:
        persisted = session.get(DocumentExtraction, uuid.UUID(derived["id"]))
        assert persisted.source_extraction.id == uuid.UUID(source["id"])
        assert persisted.page_results == derived["page_results"]


def test_real_tesseract_recovers_image_only_pdf(client) -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is not installed")
    try:
        pdf = build_image_pdf("ADMINFLOW OCR TEST\nDOCUMENT 12345")
    except FileNotFoundError as error:
        pytest.skip(str(error))
    artifact = create_artifact(client, pdf)
    source = native_extract(client, artifact["id"])
    assert source["status"] == "needs_ocr"
    response = client.post(f"/document-extractions/{source['id']}/ocr")
    assert response.status_code == 201
    derived = response.json()
    assert derived["status"] == "extracted"
    recognized = derived["page_results"][0]["text"].upper()
    assert all(token in recognized for token in ("ADMINFLOW", "OCR", "12345"))
    assert derived["page_results"][0]["text_source"] == "ocr"
    assert derived["page_results"][0]["character_count"] == len(derived["page_results"][0]["text"])


@pytest.mark.parametrize(("pdf", "expected_status"), [(build_pdf(["already readable"]), "extracted"), (build_encrypted_pdf("protected"), "password_required"), (b"%PDF-1.4\ncorrupt", "failed")])
def test_ineligible_source_status_returns_conflict(client, pdf, expected_status) -> None:
    source = native_extract(client, create_artifact(client, pdf)["id"])
    assert source["status"] == expected_status
    response = client.post(f"/document-extractions/{source['id']}/ocr")
    assert response.status_code == 409
    assert "not eligible for OCR" in response.json()["detail"]


def test_unknown_extraction_and_missing_artifact_content(client, engine, storage) -> None:
    assert client.post(f"/document-extractions/{uuid.uuid4()}/ocr").status_code == 404
    artifact = create_artifact(client, build_pdf([None]))
    source = native_extract(client, artifact["id"])
    with Session(engine) as session:
        stored = session.get(IntakeArtifact, uuid.UUID(artifact["id"]))
        path = storage.root / stored.storage_key
    path.unlink()
    assert client.post(f"/document-extractions/{source['id']}/ocr").status_code == 404


def test_integrity_and_ocr_failure_create_sanitized_failed_extractions(client, engine) -> None:
    artifact = create_artifact(client, build_pdf([None]))
    source = native_extract(client, artifact["id"])
    with Session(engine) as session:
        session.execute(update(DocumentExtraction).where(DocumentExtraction.id == uuid.UUID(source["id"])).values(page_count=2))
        session.commit()
    integrity_failure = client.post(f"/document-extractions/{source['id']}/ocr").json()
    assert integrity_failure["status"] == "failed"
    assert "page count" in integrity_failure["error_message"].lower()
    assert "Traceback" not in integrity_failure["error_message"]
    numbering_source = native_extract(client, artifact["id"])
    with Session(engine) as session:
        session.execute(
            update(DocumentExtraction)
            .where(DocumentExtraction.id == uuid.UUID(numbering_source["id"]))
            .values(page_results=[{"page_number": 2, "text": "", "character_count": 0, "needs_ocr": True}])
        )
        session.commit()
    numbering_failure = client.post(f"/document-extractions/{numbering_source['id']}/ocr").json()
    assert numbering_failure["status"] == "failed"
    assert "numbering" in numbering_failure["error_message"].lower()
    second = native_extract(client, artifact["id"])
    app.dependency_overrides[get_ocr_engine] = lambda: FailingOcrEngine()
    failure = client.post(f"/document-extractions/{second['id']}/ocr").json()
    assert failure["status"] == "failed"
    assert "timed out" in failure["error_message"]
    assert "Traceback" not in failure["error_message"]


def test_multiple_ocr_runs_create_immutable_derived_records(client) -> None:
    artifact = create_artifact(client, build_pdf([None]))
    source = native_extract(client, artifact["id"])
    app.dependency_overrides[get_ocr_engine] = lambda: CountingOcrEngine("READABLE")
    first = client.post(f"/document-extractions/{source['id']}/ocr").json()
    second = client.post(f"/document-extractions/{source['id']}/ocr").json()
    assert first["id"] != second["id"]
    assert first["source_extraction_id"] == second["source_extraction_id"] == source["id"]
    listed = client.get(f"/intake-artifacts/{artifact['id']}/extractions").json()
    assert {first["id"], second["id"], source["id"]} == {item["id"] for item in listed}
