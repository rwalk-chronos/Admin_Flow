import os
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import database_is_ready, get_engine
from app.main import app
from app.models import IntakeEvent


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
        session.execute(delete(IntakeEvent))
        session.commit()
    yield
    with Session(get_engine()) as session:
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
