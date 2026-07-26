import psycopg
import pytest

from app.config import settings
from app.storage import get_connection, init_schema


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
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS chunks")
            cur.execute("DROP TABLE IF EXISTS documents")
        init_schema(conn, settings.embedding_dim)
    yield
