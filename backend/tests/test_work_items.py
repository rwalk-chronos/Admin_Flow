import uuid
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import (
    Base,
    DocumentExtraction,
    DocumentStructuredExtraction,
    IntakeArtifact,
    IntakeEvent,
    WorkItem,
    WorkItemTransition,
)


@pytest.fixture
def engine():
    database_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def client(engine) -> Generator[TestClient, None, None]:
    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def workflow_payload(name="generic_work", version=1):
    return {
        "name": name,
        "version": version,
        "description": "Generic work lifecycle",
        "states": [
            {"name": "new", "description": "New work", "terminal": False},
            {"name": "ready", "description": None, "terminal": False},
            {"name": "in_progress", "description": None, "terminal": False},
            {"name": "waiting", "description": None, "terminal": False},
            {"name": "completed", "description": None, "terminal": True},
            {"name": "cancelled", "description": None, "terminal": True},
        ],
        "initial_state": "new",
        "transitions": [
            {"from_state": "new", "to_state": "ready"},
            {"from_state": "new", "to_state": "cancelled"},
            {"from_state": "ready", "to_state": "in_progress"},
            {"from_state": "ready", "to_state": "cancelled"},
            {"from_state": "in_progress", "to_state": "waiting"},
            {"from_state": "in_progress", "to_state": "completed"},
            {"from_state": "in_progress", "to_state": "cancelled"},
            {"from_state": "waiting", "to_state": "in_progress"},
            {"from_state": "waiting", "to_state": "cancelled"},
        ],
    }


def create_workflow(client, **kwargs):
    response = client.post("/workflow-definitions", json=workflow_payload(**kwargs))
    assert response.status_code == 201
    return response.json()


def create_event(client, subject="Source event"):
    response = client.post(
        "/intake-events",
        json={
            "source_type": "api",
            "subject": subject,
            "received_at": "2026-08-15T12:00:00Z",
            "raw_metadata": {},
        },
    )
    assert response.status_code == 201
    return response.json()


def create_item(client, workflow_id, event_id, **overrides):
    payload = {
        "workflow_definition_id": workflow_id,
        "intake_event_id": event_id,
        "work_type": "generic_task",
        "title": "Review source material",
        "data": {"priority": "normal"},
    }
    payload.update(overrides)
    response = client.post("/work-items", json=payload)
    assert response.status_code == 201
    return response.json()


def create_structured_extraction(engine, event_id, extracted_data=None):
    with Session(engine) as session:
        artifact = IntakeArtifact(
            intake_event_id=uuid.UUID(event_id),
            byte_size=1,
            sha256="a" * 64,
            storage_key=f"test/{uuid.uuid4()}",
        )
        extraction = DocumentExtraction(
            intake_artifact=artifact,
            extraction_method="pdf_text",
            status="extracted",
            page_count=1,
            character_count=8,
            text_content="readable",
            page_results=[],
        )
        structured = DocumentStructuredExtraction(
            document_extraction=extraction,
            field_schema=[],
            extracted_data=extracted_data or {"title": "Exact snapshot", "count": 2},
            provider_name="stub",
            model_name="stub-model",
            prompt_version="test-v1",
        )
        session.add(structured)
        session.commit()
        return str(structured.id)


def test_workflow_definition_create_list_get_and_exact_snapshots(client):
    payload = workflow_payload()
    created = client.post("/workflow-definitions", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["states"] == payload["states"]
    assert body["transitions"] == payload["transitions"]
    assert client.get(f"/workflow-definitions/{body['id']}").json() == body
    assert client.get("/workflow-definitions").json() == [body]


def test_duplicate_workflow_name_and_version_conflicts(client):
    create_workflow(client)
    response = client.post("/workflow-definitions", json=workflow_payload())
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["states"].append(p["states"][0]), "state names must be unique"),
        (lambda p: p["states"].__setitem__(0, {**p["states"][0], "name": "Bad State"}), "string_pattern_mismatch"),
        (lambda p: p.__setitem__("initial_state", "missing"), "initial_state must reference"),
        (lambda p: p["transitions"].append({"from_state": "missing", "to_state": "new"}), "must reference defined states"),
        (lambda p: p["transitions"].append(p["transitions"][0]), "transitions must be unique"),
        (lambda p: p["transitions"].append({"from_state": "new", "to_state": "new"}), "self-transitions"),
        (lambda p: p["transitions"].append({"from_state": "completed", "to_state": "new"}), "terminal states cannot"),
        (lambda p: p["transitions"].__setitem__(0, {"from_state": "ready", "to_state": "ready"}), "self-transitions"),
    ],
)
def test_invalid_workflow_graphs_are_rejected(client, mutate, message):
    payload = workflow_payload()
    mutate(payload)
    response = client.post("/workflow-definitions", json=payload)
    assert response.status_code == 422
    assert message in response.text


def test_unreachable_state_and_state_without_terminal_path_are_rejected(client):
    unreachable = {
        "name": "unreachable",
        "version": 1,
        "states": [
            {"name": "new", "terminal": False},
            {"name": "orphan", "terminal": False},
            {"name": "done", "terminal": True},
        ],
        "initial_state": "new",
        "transitions": [
            {"from_state": "new", "to_state": "done"},
            {"from_state": "orphan", "to_state": "done"},
        ],
    }
    assert client.post("/workflow-definitions", json=unreachable).status_code == 422
    no_terminal_path = {
        "name": "no_terminal_path",
        "version": 1,
        "states": [
            {"name": "new", "terminal": False},
            {"name": "loop_a", "terminal": False},
            {"name": "loop_b", "terminal": False},
            {"name": "done", "terminal": True},
        ],
        "initial_state": "new",
        "transitions": [
            {"from_state": "new", "to_state": "done"},
            {"from_state": "new", "to_state": "loop_a"},
            {"from_state": "loop_a", "to_state": "loop_b"},
            {"from_state": "loop_b", "to_state": "loop_a"},
        ],
    }
    response = client.post("/workflow-definitions", json=no_terminal_path)
    assert response.status_code == 422
    assert "path to a terminal state" in response.text


def test_valid_cycle_with_terminal_path_is_accepted(client):
    assert create_workflow(client)["name"] == "generic_work"


def test_work_item_creation_uses_initial_state_and_creates_atomic_history(client, engine):
    workflow = create_workflow(client)
    event = create_event(client)
    item = create_item(client, workflow["id"], event["id"])
    assert item["current_state"] == "new"
    assert item["version"] == 1
    assert item["data"] == {"priority": "normal"}
    history = client.get(f"/work-items/{item['id']}/transitions").json()
    assert [(row["version"], row["from_state"], row["to_state"]) for row in history] == [(1, None, "new")]
    with Session(engine) as session:
        persisted = session.get(WorkItem, uuid.UUID(item["id"]))
        assert persisted.transitions[0].work_item_id == persisted.id


def test_work_item_default_data_list_get_and_unknown_sources(client):
    workflow = create_workflow(client)
    event = create_event(client)
    item = create_item(client, workflow["id"], event["id"], data=None)
    assert item["data"] == {}
    assert client.get(f"/work-items/{item['id']}").json() == item
    assert client.get("/work-items").json() == [item]
    base = {"work_type": "task", "title": "Title", "data": {}}
    assert client.post("/work-items", json={**base, "workflow_definition_id": str(uuid.uuid4()), "intake_event_id": event["id"]}).status_code == 404
    assert client.post("/work-items", json={**base, "workflow_definition_id": workflow["id"], "intake_event_id": str(uuid.uuid4())}).status_code == 404
    assert client.get(f"/work-items/{uuid.uuid4()}").status_code == 404


def test_structured_extraction_lineage_copies_exact_data_and_cannot_be_overridden(client, engine):
    workflow = create_workflow(client)
    event = create_event(client)
    snapshot = {"nested": {"value": 1}, "items": ["a", "b"]}
    structured_id = create_structured_extraction(engine, event["id"], snapshot)
    item = create_item(
        client,
        workflow["id"],
        event["id"],
        data=None,
        document_structured_extraction_id=structured_id,
    )
    assert item["data"] == snapshot
    assert item["document_structured_extraction_id"] == structured_id
    override = client.post(
        "/work-items",
        json={
            "workflow_definition_id": workflow["id"],
            "intake_event_id": event["id"],
            "document_structured_extraction_id": structured_id,
            "work_type": "task",
            "title": "Title",
            "data": {"conflict": True},
        },
    )
    assert override.status_code == 422
    with Session(engine) as session:
        source = session.get(DocumentStructuredExtraction, uuid.UUID(structured_id))
        assert source.extracted_data == snapshot


def test_structured_extraction_cross_event_and_unknown_lineage(client, engine):
    workflow = create_workflow(client)
    source_event = create_event(client, "Source")
    other_event = create_event(client, "Other")
    structured_id = create_structured_extraction(engine, source_event["id"])
    base = {
        "workflow_definition_id": workflow["id"],
        "intake_event_id": other_event["id"],
        "work_type": "task",
        "title": "Title",
    }
    mismatch = client.post("/work-items", json={**base, "document_structured_extraction_id": structured_id})
    missing = client.post("/work-items", json={**base, "document_structured_extraction_id": str(uuid.uuid4())})
    assert mismatch.status_code == 409
    assert missing.status_code == 404


def transition(client, item_id, expected_state, expected_version, to_state, reason=None):
    return client.post(
        f"/work-items/{item_id}/transitions",
        json={"expected_state": expected_state, "expected_version": expected_version, "to_state": to_state, "reason": reason},
    )


def test_valid_transition_changes_state_increments_once_and_orders_history(client):
    workflow = create_workflow(client)
    event = create_event(client)
    item = create_item(client, workflow["id"], event["id"])
    first = transition(client, item["id"], "new", 1, "ready", "Prepared")
    assert first.status_code == 201
    assert (first.json()["version"], first.json()["from_state"], first.json()["to_state"]) == (2, "new", "ready")
    second = transition(client, item["id"], "ready", 2, "in_progress")
    assert second.status_code == 201
    current = client.get(f"/work-items/{item['id']}").json()
    assert current["current_state"] == "in_progress"
    assert current["version"] == 3
    history = client.get(f"/work-items/{item['id']}/transitions").json()
    assert [row["version"] for row in history] == [1, 2, 3]


@pytest.mark.parametrize(
    ("expected_state", "expected_version", "target"),
    [("ready", 1, "ready"), ("new", 2, "ready"), ("new", 1, "completed"), ("new", 1, "undefined")],
)
def test_rejected_transitions_persist_nothing(client, engine, expected_state, expected_version, target):
    workflow = create_workflow(client)
    event = create_event(client)
    item = create_item(client, workflow["id"], event["id"])
    response = transition(client, item["id"], expected_state, expected_version, target)
    assert response.status_code in {409, 422}
    with Session(engine) as session:
        persisted = session.get(WorkItem, uuid.UUID(item["id"]))
        history = list(session.scalars(select(WorkItemTransition).where(WorkItemTransition.work_item_id == persisted.id)))
        assert (persisted.current_state, persisted.version, len(history)) == ("new", 1, 1)


def test_stale_second_transition_and_terminal_state_are_blocked(client):
    workflow = create_workflow(client)
    event = create_event(client)
    item = create_item(client, workflow["id"], event["id"])
    assert transition(client, item["id"], "new", 1, "ready").status_code == 201
    assert transition(client, item["id"], "new", 1, "cancelled").status_code == 409
    assert transition(client, item["id"], "ready", 2, "cancelled").status_code == 201
    assert transition(client, item["id"], "cancelled", 3, "new").status_code == 409
    assert len(client.get(f"/work-items/{item['id']}/transitions").json()) == 3


def test_unknown_work_item_transition_routes_and_no_direct_patch(client):
    missing_id = uuid.uuid4()
    assert transition(client, missing_id, "new", 1, "ready").status_code == 404
    assert client.get(f"/work-items/{missing_id}/transitions").status_code == 404
    assert client.patch(f"/work-items/{missing_id}", json={"current_state": "ready"}).status_code == 405
