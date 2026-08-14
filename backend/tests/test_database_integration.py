import os

import pytest

from app.db import database_is_ready


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS") != "1",
    reason="set ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 to run PostgreSQL integration tests",
)
def test_postgresql_connection() -> None:
    assert database_is_ready() is True
