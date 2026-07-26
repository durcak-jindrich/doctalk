"""One-shot DB bootstrap: creates extensions, tables, and indexes.

Run once before the backend starts - see docker-compose.yml's `migrate`
service (runs this automatically), or for local dev:
    uv run python -m scripts.init_db

Idempotent: safe to re-run against an already-initialized database.
"""

from app.config import settings
from app.storage import get_connection, init_schema


def main() -> None:
    with get_connection() as conn:
        init_schema(conn, settings.embedding_dim)
    print(f"Schema ready (embedding_dim={settings.embedding_dim}).")


if __name__ == "__main__":
    main()
