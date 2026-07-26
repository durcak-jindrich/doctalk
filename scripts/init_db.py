"""One-shot DB bootstrap: creates extensions, tables, and indexes.

Run once before the backend starts - see docker-compose.yml's `migrate`
service (runs this automatically), or for local dev:
    uv run python -m scripts.init_db

Idempotent: safe to re-run against an already-initialized database.
"""

from app.config import settings
from app.retrieval import embedding_dim
from app.storage import get_connection, init_schema


def main() -> None:
    # Loads the embedding model to read its vector width — the schema's
    # VECTOR(N) is derived from the model, never configured separately.
    dim = embedding_dim()
    with get_connection() as conn:
        init_schema(conn, dim)
    print(f"Schema ready (embedding_dim={dim}, model={settings.embedding_model}).")


if __name__ == "__main__":
    main()
