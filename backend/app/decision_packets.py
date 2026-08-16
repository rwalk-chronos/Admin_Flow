import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    ActionExecution,
    ActionPlan,
    IntakeArtifact,
    InternalTask,
    WorkItem,
    WorkItemReview,
)
from app.schemas import DecisionPacketResponse

router = APIRouter(tags=["decision-packets"])
SessionDependency = Annotated[Session, Depends(get_session)]

FIELD_LABELS = {
    "vendor_name": "Vendor",
    "invoice_number": "Invoice number",
    "invoice_date": "Invoice date",
    "due_date": "Due date",
    "amount_due": "Amount due",
    "reference_number": "Reference number",
    "sender": "Sender",
    "recipient": "Recipient",
    "document_date": "Document date",
    "subject": "Subject",
    "organization": "Organization",
    "form_name": "Document name",
    "document_title": "Document title",
}


def confidence_band(value: float) -> str:
    if value >= 0.85:
        return "High confidence"
    if value >= 0.60:
        return "Moderate confidence"
    return "Low confidence"


def _label(name: str, description: str) -> str:
    return FIELD_LABELS.get(name) or description.strip().rstrip(".") or name.replace("_", " ").title()


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _display(value: Any, field_type: str, field_name: str | None = None) -> str:
    if _missing(value):
        return "Not identified"
    if field_type == "date" and isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            return parsed.strftime("%B %d, %Y").replace(" 0", " ")
        except ValueError:
            return value
    if field_type == "number" and isinstance(value, (int, float)):
        return f"${value:,.2f}" if field_name == "amount_due" else f"{value:,.2f}"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _summary(label: str, title: str, data: dict[str, Any], event) -> str:
    if label == "invoice":
        vendor = data.get("vendor_name")
        amount = data.get("amount_due")
        due = _display(data.get("due_date"), "date") if data.get("due_date") else None
        if vendor or amount is not None or due:
            parts = [f"Invoice from {vendor}" if vendor else "Invoice"]
            if amount is not None:
                parts.append(f"for ${amount:,.2f}")
            if due:
                parts.append(f"due {due}")
            return " ".join(parts) + "."
        return "AdminFlow identified this as an invoice, but the main invoice details were not identified."
    if label == "correspondence":
        sender = data.get("sender") or event.sender
        subject = data.get("subject") or event.subject
        if sender and subject:
            return f"Correspondence from {sender} regarding {subject}."
        if sender:
            return f"Correspondence from {sender} received for review."
        if subject:
            return f"Correspondence regarding {subject}."
        return "AdminFlow identified this as correspondence, but the sender and subject were not identified."
    if label == "form":
        organization = data.get("organization")
        topic = data.get("form_name") or data.get("subject")
        if organization and topic:
            return f"Administrative form from {organization} regarding {topic}."
        if organization:
            return f"Administrative form from {organization}."
        if topic:
            return f"Administrative form regarding {topic}."
        return "AdminFlow identified this as a form, but the main form details were not identified."
    if title and title.strip():
        return f"General office document titled {title.strip()} received for review."
    return "General office document received for review."


def build_decision_packet(session: Session, item: WorkItem, review: WorkItemReview | None) -> dict[str, Any]:
    structured = item.document_structured_extraction
    classification = structured.document_classification if structured else None
    label = classification.label if classification else "other"
    document_type = label.replace("_", " ").title()
    confidence = classification.confidence if classification else None
    schema = list(structured.field_schema) if structured else []
    if review and review.authorized_action_plan_id:
        plan = session.get(ActionPlan, review.authorized_action_plan_id)
    else:
        plan = session.scalar(select(ActionPlan).where(ActionPlan.work_item_id == item.id, ActionPlan.superseded_at.is_(None)).order_by(ActionPlan.revision.desc()))
    if review and review.reviewed_data is not None:
        data = dict(review.reviewed_data)
    elif review and review.status == "pending" and plan is not None:
        data = dict(plan.facts_snapshot)
    else:
        data = dict(item.data)
    facts = [
        {
            "key": field["name"],
            "label": _label(field["name"], field["description"]),
            "value": data.get(field["name"]),
            "display_value": _display(data.get(field["name"]), field["type"], field["name"]),
            "missing": _missing(data.get(field["name"])),
        }
        for field in schema
    ]
    artifacts = list(session.scalars(select(IntakeArtifact).where(IntakeArtifact.intake_event_id == item.intake_event_id).order_by(IntakeArtifact.created_at, IntakeArtifact.id)))
    attention = []
    if confidence is not None and confidence < 0.60:
        attention.append({"title": f"AdminFlow has low confidence this is a {document_type}.", "guidance": "Compare the original document before relying on this classification.", "blocking": False})
    missing_facts = [fact for fact in facts if fact["missing"]]
    if missing_facts:
        for fact in missing_facts:
            attention.append({"title": f"{fact['label']} was not identified.", "guidance": "Check the original document if this information is needed.", "blocking": False})
    if plan is None and review and review.status == "pending":
        attention.append({"title": "The next action is not available.", "guidance": "Handle this item manually or ask an administrator to check the workflow configuration.", "blocking": True})
    execution = session.scalar(select(ActionExecution).where(ActionExecution.action_plan_id == plan.id)) if plan else None
    task = session.scalar(select(InternalTask).where(InternalTask.action_execution_id == execution.id)) if execution else None
    action_result = None
    if execution:
        action_result = {
            "status": execution.status,
            "completed_at": execution.completed_at,
            "message": "Internal task created successfully" if execution.status == "succeeded" else "AdminFlow could not create the internal task",
            "task_id": task.id if task else None,
            "task_title": task.title if task else None,
            "queue": task.queue.replace("_", " ").title() if task else None,
            "owner_role": task.owner_role.replace("_", " ").title() if task and task.owner_role else None,
            "task_created_at": task.created_at if task else None,
        }
    status_labels = {"completed": "Completed", "manual_handling": "Manual handling", "action_needs_attention": "Action needs attention"}
    return {
        "review": {"id": review.id, "status": review.status, "reviewer": review.reviewer, "notes": review.notes, "created_at": review.created_at, "resolved_at": review.resolved_at} if review else None,
        "work_item_id": item.id,
        "title": item.title,
        "status_label": status_labels.get(item.current_state, "Needs your review"),
        "document_type": document_type,
        "confidence": confidence,
        "confidence_band": confidence_band(confidence) if confidence is not None else None,
        "summary": _summary(label, item.title, data, item.intake_event),
        "key_information": facts,
        "attention_items": attention,
        "artifacts": artifacts,
        "action_plan": plan,
        "action_result": action_result,
        "correction_schema": schema,
        "correction_data": data,
        "technical": {"work_type": item.work_type, "state": item.current_state, "version": item.version, "workflow_definition_id": str(item.workflow_definition_id), "structured_extraction_id": str(item.document_structured_extraction_id) if item.document_structured_extraction_id else None},
    }


@router.get("/work-item-reviews/{review_id}/decision-packet", response_model=DecisionPacketResponse)
def get_review_decision_packet(review_id: uuid.UUID, session: SessionDependency):
    review = session.get(WorkItemReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="WorkItem review not found")
    return build_decision_packet(session, review.work_item, review)


@router.get("/work-items/{work_item_id}/decision-packet", response_model=DecisionPacketResponse)
def get_work_item_decision_packet(work_item_id: uuid.UUID, session: SessionDependency):
    item = session.get(WorkItem, work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="WorkItem not found")
    review = session.scalar(select(WorkItemReview).where(WorkItemReview.work_item_id == item.id).order_by(WorkItemReview.created_at.desc(), WorkItemReview.id.desc()))
    return build_decision_packet(session, item, review)
