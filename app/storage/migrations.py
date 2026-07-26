"""Replaying `migrations/*.sql` from Python, for local dev and the test suite.

The deployed path is `migrations/apply.sh` (incremental, psql, advisory-locked).
This module covers the other case — "give me a clean database" — without
requiring psql on the host. Both read the same `.sql` files, so there is one
source of truth for the schema itself.
"""

from hashlib import sha256
from pathlib import Path

import psycopg

from app.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def reset_schema() -> list[str]:
    """Drop DocTalk's tables and replay every migration from scratch.

    Destructive — it discards the workspace. Returns the versions applied.
    """
    versions = []
    # A plain connection rather than `get_connection()`: registering the
    # pgvector adapter needs the `vector` extension, which 0001 is what creates.
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS chunks, documents, schema_migrations")
            cur.execute(_TRACKING_TABLE)
            for path in migration_files():
                body = path.read_bytes()
                cur.execute(body.decode())
                cur.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (path.name, sha256(body).hexdigest()),
                )
                versions.append(path.name)
        conn.commit()
    return versions
