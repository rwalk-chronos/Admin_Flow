import logging

from fastapi.testclient import TestClient

import app.main as main_module
from app import db


client = TestClient(main_module.app)


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "AdminFlow"}


def test_database_health_ready(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "database_is_ready", lambda: True)

    response = client.get("/health/database")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "postgresql"}


def test_database_health_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "database_is_ready", lambda: False)

    response = client.get("/health/database")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "postgresql"}


def test_database_readiness_logs_connection_errors(monkeypatch, caplog) -> None:
    class UnavailableEngine:
        def connect(self):
            raise ConnectionError("database is unavailable")

    monkeypatch.setattr(db, "get_engine", lambda: UnavailableEngine())

    with caplog.at_level(logging.ERROR, logger="app.db"):
        assert db.database_is_ready() is False

    assert "Database readiness check failed" in caplog.text
    assert "database is unavailable" in caplog.text
