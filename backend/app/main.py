from fastapi import FastAPI, Response, status

from app.config import get_settings
from app.db import database_is_ready

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/database")
def database_health(response: Response) -> dict[str, str]:
    if database_is_ready():
        return {"status": "ok", "database": "postgresql"}

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable", "database": "postgresql"}
