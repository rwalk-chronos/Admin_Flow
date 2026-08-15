from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import database_is_ready
from app.document_classifications import router as document_classifications_router
from app.document_extractions import router as document_extractions_router
from app.document_structured_extractions import (
    router as document_structured_extractions_router,
)
from app.document_ocr_api import router as document_ocr_router
from app.intake_artifacts import router as intake_artifacts_router
from app.intake_events import router as intake_events_router
from app.work_items import router as work_items_router
from app.work_item_reviews import router as work_item_reviews_router

settings = get_settings()
app = FastAPI(title=settings.app_name)
app.include_router(document_classifications_router)
app.include_router(document_extractions_router)
app.include_router(document_structured_extractions_router)
app.include_router(document_ocr_router)
app.include_router(intake_artifacts_router)
app.include_router(intake_events_router)
app.include_router(work_items_router)
app.include_router(work_item_reviews_router)

static_directory = Path(__file__).parent / "static"
app.mount("/app", StaticFiles(directory=static_directory, html=True), name="dashboard")


@app.get("/", include_in_schema=False)
def dashboard_redirect() -> RedirectResponse:
    return RedirectResponse(url="/app/")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/database")
def database_health(response: Response) -> dict[str, str]:
    if database_is_ready():
        return {"status": "ok", "database": "postgresql"}

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable", "database": "postgresql"}
