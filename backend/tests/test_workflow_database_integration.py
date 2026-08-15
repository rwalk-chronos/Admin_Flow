import os
import uuid
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.db import get_engine
from app.main import app
from app.models import (
    DocumentExtraction,
    DocumentStructuredExtraction,
    IntakeArtifact,
    IntakeEvent,
    WorkItem,
    WorkItemTransition,
    WorkflowDefinition,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_integration_database() -> None:
    if os.getenv("ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS") != "1":
        pytest.skip("set ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 to run PostgreSQL integration tests")


@pytest.fixture
def clean_workflows() -> Generator[None, None, None]:
    with Session(get_engine()) as session:
        session.execute(delete(WorkItemTransition))
        session.execute(delete(WorkItem))
        session.execute(delete(WorkflowDefinition))
        session.commit()
    yield
    with Session(get_engine()) as session:
        session.execute(delete(WorkItemTransition))
        session.execute(delete(WorkItem))
        session.execute(delete(WorkflowDefinition))
        session.commit()


def workflow_payload():
    return {
        "name": "postgresql_generic_work",
        "version": 1,
        "states": [
            {"name": "new", "description": None, "terminal": False},
            {"name": "ready", "description": None, "terminal": False},
            {"name": "completed", "description": None, "terminal": True},
        ],
        "initial_state": "new",
        "transitions": [
            {"from_state": "new", "to_state": "ready"},
            {"from_state": "ready", "to_state": "completed"},
        ],
    }


def test_migration_0008_schema_constraints_foreign_keys_and_indexes() -> None:
    inspector = inspect(get_engine())
    assert {"workflow_definitions", "work_items", "work_item_transitions"} <= set(inspector.get_table_names())

    workflow_columns = {column["name"]: column for column in inspector.get_columns("workflow_definitions")}
    item_columns = {column["name"]: column for column in inspector.get_columns("work_items")}
    transition_columns = {column["name"] for column in inspector.get_columns("work_item_transitions")}
    assert set(workflow_columns) == {"id", "name", "version", "description", "states", "initial_state", "transitions", "created_at"}
    assert set(item_columns) == {"id", "workflow_definition_id", "intake_event_id", "document_structured_extraction_id", "work_type", "title", "data", "current_state", "version", "created_at", "updated_at"}
    assert transition_columns == {"id", "work_item_id", "version", "from_state", "to_state", "reason", "created_at"}
    assert isinstance(workflow_columns["states"]["type"], JSONB)
    assert isinstance(workflow_columns["transitions"]["type"], JSONB)
    assert isinstance(item_columns["data"]["type"], JSONB)

    item_fks = {(fk["constrained_columns"][0], fk["referred_table"]) for fk in inspector.get_foreign_keys("work_items")}
    assert item_fks == {
        ("workflow_definition_id", "workflow_definitions"),
        ("intake_event_id", "intake_events"),
        ("document_structured_extraction_id", "document_structured_extractions"),
    }
    transition_fks = inspector.get_foreign_keys("work_item_transitions")
    assert any(fk["constrained_columns"] == ["work_item_id"] and fk["referred_table"] == "work_items" for fk in transition_fks)
    item_indexes = {tuple(index["column_names"]) for index in inspector.get_indexes("work_items")}
    assert {("workflow_definition_id",), ("intake_event_id",), ("document_structured_extraction_id",), ("current_state",)} <= item_indexes
    assert any(index["column_names"] == ["work_item_id"] for index in inspector.get_indexes("work_item_transitions"))
    assert {constraint["name"] for constraint in inspector.get_unique_constraints("workflow_definitions")} >= {"uq_workflow_definitions_name_version"}
    assert {constraint["name"] for constraint in inspector.get_unique_constraints("work_item_transitions")} >= {"uq_work_item_transitions_item_version"}


def test_postgresql_jsonb_initial_history_transition_and_stale_guard(clean_workflows) -> None:
    with TestClient(app) as client:
        workflow_response = client.post("/workflow-definitions", json=workflow_payload())
        assert workflow_response.status_code == 201
        workflow = workflow_response.json()
        event = client.post(
            "/intake-events",
            json={"source_type": "api", "received_at": "2026-08-15T12:00:00Z", "raw_metadata": {}},
        ).json()
        item_response = client.post(
            "/work-items",
            json={
                "workflow_definition_id": workflow["id"],
                "intake_event_id": event["id"],
                "work_type": "generic_task",
                "title": "PostgreSQL transition test",
                "data": {"nested": {"flag": True}, "values": [1, 2]},
            },
        )
        assert item_response.status_code == 201
        item = item_response.json()
        transitioned = client.post(
            f"/work-items/{item['id']}/transitions",
            json={"expected_state": "new", "expected_version": 1, "to_state": "ready", "reason": "Prepared"},
        )
        stale = client.post(
            f"/work-items/{item['id']}/transitions",
            json={"expected_state": "new", "expected_version": 1, "to_state": "ready"},
        )
    assert transitioned.status_code == 201
    assert stale.status_code == 409

    with Session(get_engine()) as session:
        persisted_workflow = session.get(WorkflowDefinition, uuid.UUID(workflow["id"]))
        persisted_item = session.get(WorkItem, uuid.UUID(item["id"]))
        history = list(session.scalars(select(WorkItemTransition).where(WorkItemTransition.work_item_id == persisted_item.id).order_by(WorkItemTransition.version)))
        assert persisted_workflow.states == workflow_payload()["states"]
        assert persisted_workflow.transitions == workflow_payload()["transitions"]
        assert persisted_item.data == {"nested": {"flag": True}, "values": [1, 2]}
        assert (persisted_item.current_state, persisted_item.version) == ("ready", 2)
        assert [(row.version, row.from_state, row.to_state) for row in history] == [(1, None, "new"), (2, "new", "ready")]


def test_postgresql_structured_source_lineage_and_jsonb_snapshot(clean_workflows) -> None:
    with Session(get_engine()) as session:
        event = IntakeEvent(source_type="api", received_at=datetime(2026, 8, 15, tzinfo=timezone.utc), raw_metadata={})
        artifact = IntakeArtifact(intake_event=event, byte_size=1, sha256="b" * 64, storage_key=f"test/{uuid.uuid4()}")
        extraction = DocumentExtraction(intake_artifact=artifact, extraction_method="pdf_text", status="extracted", page_count=1, character_count=8, text_content="readable", page_results=[])
        structured = DocumentStructuredExtraction(document_extraction=extraction, field_schema=[], extracted_data={"source": "exact", "count": 7}, provider_name="stub", model_name="stub", prompt_version="test-v1")
        session.add(structured)
        session.commit()
        event_id, artifact_id, extraction_id, structured_id = (
            str(event.id), str(artifact.id), str(extraction.id), str(structured.id)
        )
    with TestClient(app) as client:
        workflow = client.post("/workflow-definitions", json=workflow_payload()).json()
        response = client.post("/work-items", json={"workflow_definition_id": workflow["id"], "intake_event_id": event_id, "document_structured_extraction_id": structured_id, "work_type": "generic_task", "title": "Lineage test"})
    assert response.status_code == 201
    assert response.json()["data"] == {"source": "exact", "count": 7}
    with Session(get_engine()) as session:
        item = session.get(WorkItem, uuid.UUID(response.json()["id"]))
        assert item.document_structured_extraction.id == uuid.UUID(structured_id)
        assert item.intake_event.id == uuid.UUID(event_id)
        session.execute(delete(WorkItemTransition).where(WorkItemTransition.work_item_id == item.id))
        session.delete(item)
        session.delete(session.get(DocumentStructuredExtraction, uuid.UUID(structured_id)))
        session.delete(session.get(DocumentExtraction, uuid.UUID(extraction_id)))
        session.delete(session.get(IntakeArtifact, uuid.UUID(artifact_id)))
        session.delete(session.get(IntakeEvent, uuid.UUID(event_id)))
        session.commit()


def test_postgresql_unique_workflow_and_transition_constraints(clean_workflows) -> None:
    with TestClient(app) as client:
        first = client.post("/workflow-definitions", json=workflow_payload())
        duplicate = client.post("/workflow-definitions", json=workflow_payload())
    assert first.status_code == 201
    assert duplicate.status_code == 409
