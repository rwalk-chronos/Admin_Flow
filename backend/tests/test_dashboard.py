from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_root_redirects_to_dashboard() -> None:
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/")

    assert response.status_code == 307
    assert response.headers["location"] == "/app/"


def test_dashboard_and_local_assets_are_served() -> None:
    with TestClient(app) as client:
        page = client.get("/app/")
        script = client.get("/app/app.js")
        manual_intake = client.get("/app/manual-intake.js")
        stylesheet = client.get("/app/styles.css")

    assert page.status_code == 200
    assert "AdminFlow" in page.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert manual_intake.status_code == 200
    assert manual_intake.headers["content-type"].startswith("text/javascript")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")


def test_dashboard_mount_preserves_health_docs_and_api_routes() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        docs = client.get("/docs")
        openapi = client.get("/openapi.json")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert docs.status_code == 200
    assert openapi.status_code == 200
    assert "/work-items" in openapi.json()["paths"]
    assert "/intake-events" in openapi.json()["paths"]


def test_frontend_has_no_external_or_unsafe_rendering_dependencies() -> None:
    static_directory = Path(__file__).parents[1] / "app" / "static"
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in static_directory.iterdir()
    )

    assert "http://" not in sources
    assert "https://" not in sources
    assert ".innerHTML" not in sources
    assert "console." not in sources
    assert "localStorage" not in (static_directory / "index.html").read_text()
    assert "localStorage" not in (static_directory / "styles.css").read_text()
    assert sources.count("localStorage") == 2


def test_manual_intake_wires_existing_processing_apis_deterministically() -> None:
    source = (
        Path(__file__).parents[1] / "app" / "static" / "manual-intake.js"
    ).read_text(encoding="utf-8")

    assert 'source_type: "manual_upload"' in source
    assert "new FormData()" in source
    assert 'body.append("file", file, file.name)' in source
    assert 'request(`/intake-events/${eventId}/artifacts`' in source
    assert 'request(`/intake-artifacts/${artifact.id}/extract`' in source
    assert 'request(`/document-extractions/${extraction.id}/ocr`' in source
    assert 'status === "partial" || status === "needs_ocr"' in source
    assert 'extraction.status === "partial"' in source
    assert 'typeof extraction.text_content === "string"' in source
    assert "extraction.text_content.trim().length > 0" in source
    assert "if (hasReadableText(ocr))" in source
    assert "document-extractions/${extraction.id}/classifications" not in source
    assert "structured-extractions" not in source
    assert "OpenAI" not in source


def test_manual_intake_uses_native_multiple_file_input_and_accessible_status() -> None:
    source = (
        Path(__file__).parents[1] / "app" / "static" / "manual-intake.js"
    ).read_text(encoding="utf-8")

    assert 'type: "file"' in source
    assert 'multiple: ""' in source
    assert '"aria-live": "polite"' in source
    assert 'event.dataTransfer.files' in source
    assert 'Select at least one document.' in source


def test_manual_intake_uses_server_owned_processing_profile_and_review_routing() -> None:
    source = (Path(__file__).parents[1] / "app" / "static" / "manual-intake.js").read_text(encoding="utf-8")
    assert 'request("/document-processing/config")' in source
    assert '`/document-extractions/${extractionId}/process`' in source
    assert 'profile_id: "generic_office"' in source
    assert '"review/" + reviewIds[0]' in source
    assert "candidate_labels" not in source
    assert "field_schema" not in source
    assert "workflow_definition" not in source
    assert "provider-select" not in source


def test_review_ui_explains_and_authorizes_exact_action_plan() -> None:
    source = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "Summary" in source
    assert "Key information" in source
    assert "Needs your attention" in source
    assert "What will happen next" in source
    assert "Correct Information" in source
    assert "Handle Manually" in source
    assert "action_plan_id: packet.action_plan?.id" in source
    assert "View Original Document" in source
    assert "Review Changes" in source
    assert "Information updated. Review the revised action before approving." in source
    assert "Technical details" in source
    assert "AI-generated summary" in source
    assert "Basic summary" in source
    assert "Send to" in source
    assert "Responsible role" in source
    assert "Approval performs this internal handoff automatically" in source
    assert "Ready for review" not in source  # Status wording is server-owned.
    assert "Nothing needs your attention" not in source
    assert "...(attention ? [attention] : [])" in source
    assert "function summaryParagraphs(summary)" in source
    assert '.split(/\\n\\s*\\n/)' in source
    assert '...summaryParagraphs(packet.summary)' in source


def test_review_defaults_to_decision_packet_not_technical_editor() -> None:
    source = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    read_mode = source[source.index("const drawReadMode"):source.index("const drawCorrectionMode")]
    correction_mode = source[source.index("const drawCorrectionMode"):source.index("drawReadMode();")]
    assert "structuredEditor(" not in read_mode
    assert "genericEditor(" not in read_mode
    assert "structuredEditor(" in correction_mode
    assert "work_type" not in read_mode
    assert "technical.state" not in read_mode
    assert "definition.type" not in read_mode
    assert "Set optional value" not in source
    assert "form_review" not in read_mode
    assert "needs_review" not in read_mode
    render_line = next(line for line in read_mode.splitlines() if "packetHost.append" in line)
    assert render_line.index('section("Key information"') < render_line.index("...(attention")


def test_completed_action_work_item_prioritizes_outcome_over_technical_details() -> None:
    source = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    completed = source[source.index("if (plans.length)"):source.index('content.append(pageHeader("Work item"')]
    assert completed.index("What happened") < completed.index("Technical details")
    assert completed.index("Key information") < completed.index("Technical details")
    assert completed.index("View Original Document") < completed.index("Technical details")
    assert "Awaiting task completion" not in completed  # Status wording is server-owned.
    assert "The follow-up task still needs to be completed." in completed
    assert "Responsible role" in completed
    assert "Task status" in completed
    assert "View Task" in completed
    assert "Task completion" in completed


def test_tasks_ui_is_cognitive_and_completes_through_supported_endpoint() -> None:
    source = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    markup = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'href="#tasks"' in markup
    assert "nav-task-count" in markup
    assert 'api("/internal-tasks?status=open")' in source
    assert 'async function tasks(' in source
    assert 'async function taskDetail(' in source
    assert "Responsible role" in source
    assert "Unassigned — available to the responsible queue" in source
    assert "View Original Document" in source
    assert "View Source Work Item" in source
    assert "Mark Task Complete" in source
    assert "Completion note (optional)" in source
    assert "completed_by" in source
    assert "Task completed" in source
    assert "Technical details" in source
