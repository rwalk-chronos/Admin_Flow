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
