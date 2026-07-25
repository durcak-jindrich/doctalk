from psycopg import Connection

_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    section_path TEXT[],
    page_number INTEGER,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR({dim}) NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks (document_id);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_bm25_idx ON chunks
    USING bm25 (id, (text::pdb.simple('stemmer=english')))
    WITH (key_field='id');
"""


def init_schema(conn: Connection, embedding_dim: int) -> None:
    """Create the schema if absent. Assumes `embedding_dim` never changes for
    an existing database — swapping EMBEDDING_MODEL for one with a different
    dimension requires dropping the `chunks` table (or the whole volume)."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL.format(dim=embedding_dim))
