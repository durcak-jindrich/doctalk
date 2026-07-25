import psycopg
import pytest

from app.storage import get_connection


@pytest.fixture(autouse=True)
def _require_db():
    try:
        with get_connection():
            pass
    except psycopg.OperationalError:
        pytest.skip(
            "Postgres not reachable at DATABASE_URL — start it with `docker compose up -d db`."
        )


@pytest.fixture
def clean_schema():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS chunks")
        cur.execute("DROP TABLE IF EXISTS documents")
    yield
