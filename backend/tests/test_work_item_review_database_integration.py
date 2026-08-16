import os
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.db import get_engine
from app.main import app
from app.models import ActionExecution, ActionPlan, InternalTask, WorkItem, WorkItemReview, WorkItemTransition, WorkflowDefinition

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_integration_database() -> None:
    if os.getenv("ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS") != "1":
        pytest.skip("set ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 to run PostgreSQL integration tests")


@pytest.fixture
def clean_reviews() -> Generator[None, None, None]:
    with Session(get_engine()) as session:
        session.execute(delete(InternalTask)); session.execute(delete(ActionExecution)); session.execute(delete(ActionPlan))
        session.execute(delete(WorkItemReview))
        session.execute(delete(WorkItemTransition))
        session.execute(delete(WorkItem))
        session.execute(delete(WorkflowDefinition))
        session.commit()
    yield
    with Session(get_engine()) as session:
        session.execute(delete(InternalTask)); session.execute(delete(ActionExecution)); session.execute(delete(ActionPlan))
        session.execute(delete(WorkItemReview))
        session.execute(delete(WorkItemTransition))
        session.execute(delete(WorkItem))
        session.execute(delete(WorkflowDefinition))
        session.commit()


def workflow_payload(initial="new"):
    states = [
        {"name": "new", "terminal": False},
        {"name": "review", "terminal": False, "review_required": True},
        {"name": "done", "terminal": True},
        {"name": "rejected", "terminal": True},
    ]
    transitions = [
        {"from_state": "new", "to_state": "review"},
        {"from_state": "review", "to_state": "done", "review_decision": "approve"},
        {"from_state": "review", "to_state": "rejected", "review_decision": "reject"},
    ]
    if initial == "review":
        states = states[1:]
        transitions = transitions[1:]
    return {"name": f"postgres_review_{initial}", "version": 1, "states": states, "initial_state": initial, "transitions": transitions}


def create_event(client):
    return client.post("/intake-events", json={"source_type": "api", "received_at": "2026-08-15T12:00:00Z"}).json()


def create_item(client, workflow, event):
    response = client.post("/work-items", json={"workflow_definition_id": workflow["id"], "intake_event_id": event["id"], "work_type": "review_test", "title": "PostgreSQL review", "data": {"value": 1}})
    assert response.status_code == 201
    return response.json()


def test_migration_0009_schema_constraints_and_indexes() -> None:
    inspector = inspect(get_engine())
    columns = {column["name"]: column for column in inspector.get_columns("work_item_reviews")}
    assert set(columns) == {"id", "work_item_id", "work_item_version", "state", "status", "reviewer", "notes", "reviewed_data", "authorized_action_plan_id", "created_at", "resolved_at"}
    assert isinstance(columns["reviewed_data"]["type"], JSONB)
    assert any(fk["constrained_columns"] == ["work_item_id"] and fk["referred_table"] == "work_items" for fk in inspector.get_foreign_keys("work_item_reviews"))
    indexes = {tuple(index["column_names"]) for index in inspector.get_indexes("work_item_reviews")}
    assert {("work_item_id",), ("status", "created_at")} <= indexes
    assert {constraint["name"] for constraint in inspector.get_unique_constraints("work_item_reviews")} >= {"uq_work_item_reviews_item_version"}
    assert {constraint["name"] for constraint in inspector.get_check_constraints("work_item_reviews")} >= {"ck_work_item_reviews_status", "ck_work_item_reviews_version", "ck_work_item_reviews_resolution", "ck_work_item_reviews_rejected_data"}


def test_migration_0010_action_schema_constraints_and_lineage() -> None:
    inspector = inspect(get_engine())
    assert {"action_plans", "action_executions", "internal_tasks"} <= set(inspector.get_table_names())
    plan_columns = {column["name"] for column in inspector.get_columns("action_plans")}
    assert {"work_item_id", "workflow_definition_id", "intake_event_id", "facts_snapshot", "payload", "source_artifact_ids", "superseded_at"} <= plan_columns
    assert {constraint["name"] for constraint in inspector.get_unique_constraints("action_plans")} >= {"uq_action_plans_item_revision"}
    assert {constraint["name"] for constraint in inspector.get_unique_constraints("action_executions")} >= {"uq_action_executions_plan", "uq_action_executions_idempotency"}
    assert {constraint["name"] for constraint in inspector.get_unique_constraints("internal_tasks")} >= {"uq_internal_tasks_execution"}
    review_fks = inspector.get_foreign_keys("work_item_reviews")
    assert any(fk["constrained_columns"] == ["authorized_action_plan_id"] and fk["referred_table"] == "action_plans" for fk in review_fks)


def test_postgresql_initial_review_and_resolution_are_atomic_with_row_lock_path(clean_reviews) -> None:
    with TestClient(app) as client:
        workflow = client.post("/workflow-definitions", json=workflow_payload("review")).json()
        item = create_item(client, workflow, create_event(client))
        review = client.get("/work-item-reviews").json()[0]
        response = client.post(f"/work-item-reviews/{review['id']}/resolve", json={"decision": "approve", "expected_work_item_state": "review", "expected_work_item_version": 1, "reviewer": "integration-reviewer", "reviewed_data": {"value": 2}})
    assert response.status_code == 201
    with Session(get_engine()) as session:
        persisted_item = session.get(WorkItem, uuid.UUID(item["id"]))
        persisted_review = session.get(WorkItemReview, uuid.UUID(review["id"]))
        transitions = list(session.scalars(select(WorkItemTransition).where(WorkItemTransition.work_item_id == persisted_item.id).order_by(WorkItemTransition.version)))
        assert (persisted_item.current_state, persisted_item.version, persisted_item.data) == ("done", 2, {"value": 2})
        assert (persisted_review.status, persisted_review.reviewer, persisted_review.reviewed_data) == ("approved", "integration-reviewer", {"value": 2})
        assert [(row.version, row.to_state) for row in transitions] == [(1, "review"), (2, "done")]


def test_postgresql_transition_creates_review_and_stale_resolution_changes_nothing(clean_reviews) -> None:
    with TestClient(app) as client:
        workflow = client.post("/workflow-definitions", json=workflow_payload()).json()
        item = create_item(client, workflow, create_event(client))
        entered = client.post(f"/work-items/{item['id']}/transitions", json={"expected_state": "new", "expected_version": 1, "to_state": "review"})
        review = client.get("/work-item-reviews").json()[0]
        stale = client.post(f"/work-item-reviews/{review['id']}/resolve", json={"decision": "reject", "expected_work_item_state": "review", "expected_work_item_version": 1, "reviewer": "integration-reviewer"})
    assert entered.status_code == 201
    assert stale.status_code == 409
    with Session(get_engine()) as session:
        persisted_item = session.get(WorkItem, uuid.UUID(item["id"]))
        persisted_review = session.get(WorkItemReview, uuid.UUID(review["id"]))
        assert (persisted_item.current_state, persisted_item.version) == ("review", 2)
        assert persisted_review.status == "pending"
        assert session.query(WorkItemTransition).filter_by(work_item_id=persisted_item.id).count() == 2
