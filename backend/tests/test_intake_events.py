import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Base


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def event_payload(**overrides) -> dict:
    payload = {
        "source_type": "web_form",
        "external_id": "external-123",
        "sender": "sender@example.test",
        "recipient": "intake@example.test",
        "subject": "General administrative request",
        "body_text": "Please review this request.",
        "received_at": "2026-08-14T14:30:00Z",
        "raw_metadata": {"source": {"priority": 2}, "labels": ["new"]},
    }
    payload.update(overrides)
    return payload


def test_create_and_retrieve_intake_event(client: TestClient) -> None:
    create_response = client.post("/intake-events", json=event_payload())

    assert create_response.status_code == 201
    created = create_response.json()
    assert uuid.UUID(created["id"])
    assert created["status"] == "received"
    assert created["raw_metadata"] == {
        "source": {"priority": 2},
        "labels": ["new"],
    }
    assert created["created_at"]
    assert created["updated_at"]

    retrieve_response = client.get(f"/intake-events/{created['id']}")

    assert retrieve_response.status_code == 200
    assert retrieve_response.json() == created


def test_list_intake_events_newest_first(client: TestClient) -> None:
    older = client.post(
        "/intake-events",
        json=event_payload(external_id="older", received_at="2026-08-13T14:30:00Z"),
    ).json()
    newer = client.post(
        "/intake-events",
        json=event_payload(external_id="newer", received_at="2026-08-14T14:30:00Z"),
    ).json()

    response = client.get("/intake-events")

    assert response.status_code == 200
    assert [event["id"] for event in response.json()] == [newer["id"], older["id"]]


def test_create_uses_empty_metadata_and_received_status_defaults(
    client: TestClient,
) -> None:
    payload = event_payload()
    payload.pop("raw_metadata")

    response = client.post("/intake-events", json=payload)

    assert response.status_code == 201
    assert response.json()["raw_metadata"] == {}
    assert response.json()["status"] == "received"


def test_create_does_not_allow_caller_controlled_status(client: TestClient) -> None:
    response = client.post("/intake-events", json=event_payload(status="processed"))

    assert response.status_code == 422


def test_unknown_intake_event_returns_not_found(client: TestClient) -> None:
    response = client.get(f"/intake-events/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Intake event not found"}


def test_received_at_requires_timezone(client: TestClient) -> None:
    response = client.post(
        "/intake-events", json=event_payload(received_at="2026-08-14T14:30:00")
    )

    assert response.status_code == 422
