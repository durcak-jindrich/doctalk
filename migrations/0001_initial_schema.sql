-- Initial DocTalk schema: documents, chunks, and the hybrid-retrieval indexes.
--
-- VECTOR(384) is the width of all-MiniLM-L6-v2, the default EMBEDDING_MODEL.
-- It is a literal because a migration must replay identically on every
-- database; switching to an embedder of a different width is a *new*
-- migration, not a re-run of this one. `tests/unit/test_migrations.py` fails
-- if the configured model stops matching this number.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    section_path TEXT[],
    page_number INTEGER,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR(384) NOT NULL
);

CREATE INDEX chunks_document_id_idx ON chunks (document_id);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_bm25_idx ON chunks
    USING bm25 (id, (text::pdb.simple('stemmer=english')))
    WITH (key_field='id');
