import uuid
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
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
    WorkItemReview,
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


def review_workflow(name="review_work", initial_state="new"):
    if initial_state == "human_review":
        return {
            "name": name, "version": 1,
            "states": [
                {"name": "human_review", "terminal": False, "review_required": True},
                {"name": "completed", "terminal": True},
                {"name": "rejected", "terminal": True},
            ],
            "initial_state": "human_review",
            "transitions": [
                {"from_state": "human_review", "to_state": "completed", "review_decision": "approve"},
                {"from_state": "human_review", "to_state": "rejected", "review_decision": "reject"},
            ],
        }
    return {
        "name": name,
        "version": 1,
        "states": [
            {"name": "new", "terminal": False},
            {"name": "human_review", "terminal": False, "review_required": True},
            {"name": "completed", "terminal": True},
            {"name": "rejected", "terminal": True},
        ],
        "initial_state": initial_state,
        "transitions": [
            {"from_state": "new", "to_state": "human_review"},
            {"from_state": "human_review", "to_state": "completed", "review_decision": "approve"},
            {"from_state": "human_review", "to_state": "rejected", "review_decision": "reject"},
        ],
    }


def create_workflow(client, payload=None):
    response = client.post("/workflow-definitions", json=payload or review_workflow())
    assert response.status_code == 201
    return response.json()


def create_event(client):
    response = client.post(
        "/intake-events",
        json={"source_type": "api", "received_at": "2026-08-15T12:00:00Z"},
    )
    assert response.status_code == 201
    return response.json()


def create_item(client, workflow_id, event_id, **overrides):
    payload = {
        "workflow_definition_id": workflow_id,
        "intake_event_id": event_id,
        "work_type": "generic_review",
        "title": "Review this work",
        "data": {"title": "Original", "count": 1},
    }
    payload.update(overrides)
    response = client.post("/work-items", json=payload)
    assert response.status_code == 201
    return response.json()


def enter_review(client, item):
    response = client.post(
        f"/work-items/{item['id']}/transitions",
        json={"expected_state": "new", "expected_version": 1, "to_state": "human_review"},
    )
    assert response.status_code == 201
    return client.get(f"/work-items/{item['id']}").json()


def pending_review(client):
    response = client.get("/work-item-reviews")
    assert response.status_code == 200
    assert len(response.json()) == 1
    return response.json()[0]


def resolve(client, review, decision="approve", **overrides):
    payload = {
        "decision": decision,
        "expected_work_item_state": review["state"],
        "expected_work_item_version": review["work_item_version"],
        "reviewer": "reviewer-1",
        "notes": "Human decision",
    }
    payload.update(overrides)
    return client.post(f"/work-item-reviews/{review['id']}/resolve", json=payload)


def create_structured_source(engine, event_id):
    field_schema = [
        {"name": "title", "description": "Title", "type": "string", "required": True},
        {"name": "count", "description": "Count", "type": "integer", "required": True},
        {"name": "due_date", "description": "Date", "type": "date", "required": False},
    ]
    original = {"title": "Original", "count": 1, "due_date": None}
    with Session(engine) as session:
        artifact = IntakeArtifact(
            intake_event_id=uuid.UUID(event_id), byte_size=1, sha256="c" * 64,
            storage_key=f"test/{uuid.uuid4()}",
        )
        extraction = DocumentExtraction(
            intake_artifact=artifact, extraction_method="pdf_text", status="extracted",
            page_count=1, character_count=8, text_content="readable", page_results=[],
        )
        structured = DocumentStructuredExtraction(
            document_extraction=extraction, field_schema=field_schema,
            extracted_data=original, provider_name="stub", model_name="stub",
            prompt_version="test-v1",
        )
        session.add(structured)
        session.commit()
        return str(structured.id), original


def test_existing_workflow_defaults_remain_backward_compatible(client):
    payload = {
        "name": "legacy", "version": 1,
        "states": [{"name": "new", "terminal": False}, {"name": "done", "terminal": True}],
        "initial_state": "new",
        "transitions": [{"from_state": "new", "to_state": "done"}],
    }
    created = create_workflow(client, payload)
    assert all("review_required" not in state for state in created["states"])
    assert all("review_decision" not in edge for edge in created["transitions"])
    assert created["initial_state"] == "new"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["states"].__setitem__(2, {"name": "completed", "terminal": True, "review_required": True}), "terminal states cannot require review"),
        (lambda p: p["transitions"].__setitem__(1, {"from_state": "human_review", "to_state": "completed"}), "require review_decision"),
        (lambda p: p["transitions"].__setitem__(0, {"from_state": "new", "to_state": "human_review", "review_decision": "approve"}), "normal state cannot"),
        (lambda p: p["transitions"].__setitem__(1, {"from_state": "human_review", "to_state": "completed", "review_decision": "reject"}), "review decisions from a state must be unique"),
        (lambda p: p["transitions"].append({"from_state": "human_review", "to_state": "new", "review_decision": "approve"}), "review decisions from a state must be unique"),
        (lambda p: p["transitions"].append({"from_state": "human_review", "to_state": "new", "review_decision": "reject"}), "review decisions from a state must be unique"),
    ],
)
def test_invalid_review_graphs_are_rejected(client, mutate, message):
    payload = review_workflow()
    mutate(payload)
    response = client.post("/workflow-definitions", json=payload)
    assert response.status_code == 422
    assert message in response.text


def test_review_state_without_approve_edge_is_rejected(client):
    payload = {
        "name": "no_approve", "version": 1,
        "states": [
            {"name": "review", "terminal": False, "review_required": True},
            {"name": "done", "terminal": True},
        ],
        "initial_state": "review",
        "transitions": [
            {"from_state": "review", "to_state": "done", "review_decision": "reject"}
        ],
    }
    response = client.post("/workflow-definitions", json=payload)
    assert response.status_code == 422
    assert "exactly one approve" in response.text


def test_initial_review_state_creates_review_transition_and_item_atomically(client, engine):
    workflow = create_workflow(client, review_workflow(initial_state="human_review"))
    event = create_event(client)
    item = create_item(client, workflow["id"], event["id"])
    review = pending_review(client)
    assert (item["current_state"], item["version"]) == ("human_review", 1)
    assert (review["state"], review["work_item_version"], review["status"]) == ("human_review", 1, "pending")
    with Session(engine) as session:
        assert session.query(WorkItem).count() == 1
        assert session.query(WorkItemTransition).count() == 1
        assert session.query(WorkItemReview).count() == 1


def test_transition_into_review_creates_pending_and_generic_transition_cannot_bypass(client, engine):
    workflow = create_workflow(client)
    event = create_event(client)
    item = create_item(client, workflow["id"], event["id"])
    current = enter_review(client, item)
    review = pending_review(client)
    blocked = client.post(
        f"/work-items/{item['id']}/transitions",
        json={"expected_state": "human_review", "expected_version": 2, "to_state": "completed"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Human review is required for the current state"
    assert current["version"] == review["work_item_version"] == 2
    with Session(engine) as session:
        assert session.query(WorkItemTransition).count() == 2
        assert session.query(WorkItemReview).count() == 1


def test_queue_context_order_filter_get_and_work_item_history(client, engine):
    workflow = create_workflow(client)
    event = create_event(client)
    first_item = enter_review(client, create_item(client, workflow["id"], event["id"], title="First"))
    first_review = client.get("/work-item-reviews").json()[0]
    with Session(engine) as session:
        session.execute(update(WorkItemReview).where(WorkItemReview.id == uuid.UUID(first_review["id"])).values(created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        session.commit()
    second_item = enter_review(client, create_item(client, workflow["id"], event["id"], title="Second"))
    queue = client.get("/work-item-reviews").json()
    assert [row["title"] for row in queue] == ["First", "Second"]
    assert queue[0]["work_item_data"] == {"title": "Original", "count": 1}
    assert client.get(f"/work-item-reviews/{queue[0]['id']}").json()["work_item_id"] == first_item["id"]
    assert [row["id"] for row in client.get(f"/work-items/{first_item['id']}/reviews").json()] == [queue[0]["id"]]
    assert client.get("/work-item-reviews?status=approved").json() == []
    assert client.get(f"/work-items/{uuid.uuid4()}/reviews").status_code == 404
    assert client.get(f"/work-item-reviews/{uuid.uuid4()}").status_code == 404


def test_approve_uses_deterministic_edge_and_persists_once(client, engine):
    workflow = create_workflow(client)
    event = create_event(client)
    item = enter_review(client, create_item(client, workflow["id"], event["id"]))
    review = pending_review(client)
    response = resolve(client, review, "approve")
    assert response.status_code == 201
    resolved = response.json()
    assert (resolved["status"], resolved["reviewer"], resolved["reviewed_data"]) == ("approved", "reviewer-1", item["data"])
    assert (resolved["current_state"], resolved["current_version"]) == ("completed", 3)
    history = client.get(f"/work-items/{item['id']}/transitions").json()
    assert [(row["version"], row["to_state"]) for row in history] == [(1, "new"), (2, "human_review"), (3, "completed")]
    assert len(client.get("/work-item-reviews?status=approved").json()) == 1
    with Session(engine) as session:
        assert session.query(WorkItemTransition).count() == 3


def test_reject_uses_reject_edge_and_does_not_change_data(client):
    workflow = create_workflow(client)
    event = create_event(client)
    item = enter_review(client, create_item(client, workflow["id"], event["id"]))
    review = pending_review(client)
    response = resolve(client, review, "reject", reviewed_data={"ignored": True})
    assert response.status_code == 201
    resolved = response.json()
    assert resolved["status"] == "rejected"
    assert resolved["reviewed_data"] is None
    assert resolved["work_item_data"] == item["data"]
    assert resolved["current_state"] == "rejected"


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_work_item_state": "new"},
        {"expected_work_item_version": 1},
    ],
)
def test_stale_resolution_rejected_without_persistence(client, engine, overrides):
    workflow = create_workflow(client)
    event = create_event(client)
    item = enter_review(client, create_item(client, workflow["id"], event["id"]))
    review = pending_review(client)
    response = resolve(client, review, **overrides)
    assert response.status_code == 409
    with Session(engine) as session:
        persisted = session.get(WorkItem, uuid.UUID(item["id"]))
        persisted_review = session.get(WorkItemReview, uuid.UUID(review["id"]))
        assert (persisted.current_state, persisted.version) == ("human_review", 2)
        assert persisted_review.status == "pending"
        assert session.query(WorkItemTransition).count() == 2


def test_already_resolved_invalid_decision_and_reviewer_validation(client):
    workflow = create_workflow(client)
    event = create_event(client)
    enter_review(client, create_item(client, workflow["id"], event["id"]))
    review = pending_review(client)
    assert resolve(client, review).status_code == 201
    assert resolve(client, review).status_code == 409
    bad_decision = {"decision": "maybe", "expected_work_item_state": "human_review", "expected_work_item_version": 2, "reviewer": "person"}
    assert client.post(f"/work-item-reviews/{review['id']}/resolve", json=bad_decision).status_code == 422
    missing = pending = client.get("/work-item-reviews").json()
    assert pending == []
    assert resolve(client, {**review, "id": str(uuid.uuid4())}).status_code == 404


def test_blank_reviewer_rejected(client):
    workflow = create_workflow(client)
    event = create_event(client)
    enter_review(client, create_item(client, workflow["id"], event["id"]))
    review = pending_review(client)
    assert resolve(client, review, reviewer="   ").status_code == 422


def test_structured_correction_validated_and_source_remains_immutable(client, engine):
    workflow = create_workflow(client)
    event = create_event(client)
    structured_id, original = create_structured_source(engine, event["id"])
    item = create_item(client, workflow["id"], event["id"], data=None, document_structured_extraction_id=structured_id)
    enter_review(client, item)
    review = pending_review(client)
    corrected = {"title": "Corrected", "count": 2, "due_date": "2026-08-15"}
    response = resolve(client, review, reviewed_data=corrected)
    assert response.status_code == 201
    assert response.json()["reviewed_data"] == corrected
    assert response.json()["work_item_data"] == corrected
    with Session(engine) as session:
        source = session.get(DocumentStructuredExtraction, uuid.UUID(structured_id))
        assert source.extracted_data == original


@pytest.mark.parametrize(
    "invalid",
    [
        {"title": "Missing fields"},
        {"title": "Bad count", "count": "2", "due_date": None},
        {"title": "Bad date", "count": 2, "due_date": "2026-02-31"},
        {"title": "Extra", "count": 2, "due_date": None, "extra": True},
    ],
)
def test_invalid_structured_correction_returns_422_and_persists_nothing(client, engine, invalid):
    workflow = create_workflow(client)
    event = create_event(client)
    structured_id, original = create_structured_source(engine, event["id"])
    item = create_item(client, workflow["id"], event["id"], data=None, document_structured_extraction_id=structured_id)
    enter_review(client, item)
    review = pending_review(client)
    response = resolve(client, review, reviewed_data=invalid)
    assert response.status_code == 422
    with Session(engine) as session:
        persisted = session.get(WorkItem, uuid.UUID(item["id"]))
        persisted_review = session.get(WorkItemReview, uuid.UUID(review["id"]))
        assert persisted.data == original
        assert persisted_review.status == "pending"
        assert session.query(WorkItemTransition).count() == 2


def test_unstructured_correction_accepts_any_json_object(client):
    workflow = create_workflow(client)
    event = create_event(client)
    item = enter_review(client, create_item(client, workflow["id"], event["id"]))
    review = pending_review(client)
    corrected = {"freeform": {"nested": [1, True, None]}}
    response = resolve(client, review, reviewed_data=corrected)
    assert response.status_code == 201
    assert response.json()["work_item_data"] == corrected
