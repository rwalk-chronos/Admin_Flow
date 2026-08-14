import hashlib
import io
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.artifact_storage import LocalArtifactStorage
from app.db import get_session
from app.intake_artifacts import get_artifact_storage
from app.main import app
from app.models import Base


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    storage = LocalArtifactStorage(tmp_path)

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
        engine.dispose()


def create_event(client: TestClient) -> dict:
    response = client.post(
        "/intake-events",
        json={
            "source_type": "manual_upload",
            "received_at": "2026-08-14T16:00:00Z",
        },
    )
    assert response.status_code == 201
    return response.json()


def upload_artifact(
    client: TestClient,
    event_id: str,
    *,
    filename: str = "original document.txt",
    content: bytes = b"original artifact bytes",
    content_type: str = "text/plain",
):
    return client.post(
        f"/intake-events/{event_id}/artifacts",
        files={"file": (filename, content, content_type)},
    )


def test_upload_persists_metadata_and_original_content(
    client: TestClient, tmp_path: Path
) -> None:
    event = create_event(client)
    content = b"unaltered original bytes\x00\xff"

    response = upload_artifact(client, event["id"], content=content)

    assert response.status_code == 201
    artifact = response.json()
    assert artifact["intake_event_id"] == event["id"]
    assert artifact["original_filename"] == "original document.txt"
    assert artifact["content_type"] == "text/plain"
    assert artifact["byte_size"] == len(content)
    assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
    assert artifact["created_at"]
    assert "storage_key" not in artifact

    stored_files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(stored_files) == 1
    assert stored_files[0].name == uuid.UUID(artifact["id"]).hex
    assert stored_files[0].parent.name == uuid.UUID(event["id"]).hex
    assert stored_files[0].read_bytes() == content

    metadata_response = client.get(f"/intake-artifacts/{artifact['id']}")
    content_response = client.get(f"/intake-artifacts/{artifact['id']}/content")

    assert metadata_response.status_code == 200
    assert metadata_response.json() == artifact
    assert content_response.status_code == 200
    assert content_response.content == content
    assert content_response.headers["content-length"] == str(len(content))
    assert content_response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "original%20document.txt" in content_response.headers["content-disposition"]


def test_multiple_artifacts_can_be_listed_for_one_event(client: TestClient) -> None:
    event = create_event(client)
    first = upload_artifact(
        client, event["id"], filename="first.bin", content=b"first"
    ).json()
    second = upload_artifact(
        client, event["id"], filename="second.bin", content=b"second"
    ).json()

    response = client.get(f"/intake-events/{event['id']}/artifacts")

    assert response.status_code == 200
    assert {artifact["id"] for artifact in response.json()} == {
        first["id"],
        second["id"],
    }
    assert all(
        artifact["intake_event_id"] == event["id"] for artifact in response.json()
    )


def test_unknown_event_rejects_upload_and_listing(
    client: TestClient, tmp_path: Path
) -> None:
    unknown_event_id = uuid.uuid4()

    upload_response = upload_artifact(client, str(unknown_event_id))
    list_response = client.get(f"/intake-events/{unknown_event_id}/artifacts")

    assert upload_response.status_code == 404
    assert list_response.status_code == 404
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_unknown_artifact_returns_not_found(client: TestClient) -> None:
    artifact_id = uuid.uuid4()

    metadata_response = client.get(f"/intake-artifacts/{artifact_id}")
    content_response = client.get(f"/intake-artifacts/{artifact_id}/content")

    assert metadata_response.status_code == 404
    assert content_response.status_code == 404


def test_artifact_storage_removes_partial_file_on_failure(tmp_path: Path) -> None:
    class FailingStream(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"partial")
            self.read_count = 0

        def read(self, size: int = -1) -> bytes:
            self.read_count += 1
            if self.read_count > 1:
                raise OSError("source read failed")
            return super().read(size)

    storage = LocalArtifactStorage(tmp_path)

    with pytest.raises(OSError, match="source read failed"):
        storage.store("event/artifact", FailingStream())

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_artifacts_have_no_update_or_delete_endpoints(client: TestClient) -> None:
    event = create_event(client)
    artifact = upload_artifact(client, event["id"]).json()

    assert client.patch(f"/intake-artifacts/{artifact['id']}", json={}).status_code == 405
    assert client.delete(f"/intake-artifacts/{artifact['id']}").status_code == 405
