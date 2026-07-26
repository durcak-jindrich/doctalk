import psycopg
import pytest

from app.config import settings
from app.storage import reset_schema


@pytest.fixture(autouse=True)
def _require_db():
    # A plain connect, not get_connection(): the latter registers the pgvector
    # adapter, which a database that has never been migrated does not have yet.
    try:
        with psycopg.connect(settings.database_url):
            pass
    except psycopg.OperationalError:
        pytest.skip(
            "Postgres not reachable at DATABASE_URL — start it with `docker compose up -d db`."
        )


@pytest.fixture
def clean_schema():
    reset_schema()
    yield
