from dataclasses import dataclass
from pathlib import Path

from app.chunking import chunk_blocks, make_chunk_id, make_document_id
from app.config import settings
from app.parsers import parse_document
from app.retrieval.embedder import embed_texts

from .db import get_connection
from .repository import (
    DocumentRecord,
    WorkspaceFullError,
    count_chunks,
    count_documents,
    document_exists,
    get_document,
    insert_chunks,
    insert_document,
)


@dataclass
class IngestResult:
    document_id: str
    filename: str
    chunk_count: int
    deduplicated: bool


def ingest_document(filename: str, content: bytes) -> IngestResult:
    """Parse, chunk, embed, and persist one document.

    Re-uploading a file with identical bytes is a no-op that returns the
    existing record (content-addressed `document_id`) rather than raising or
    duplicating rows. New documents past `max_documents` are rejected.
    """
    document_id = make_document_id(filename, content)

    with get_connection() as conn:
        if document_exists(conn, document_id):
            existing = get_document(conn, document_id)
            return IngestResult(
                document_id=document_id,
                filename=existing["filename"],
                chunk_count=count_chunks(conn, document_id),
                deduplicated=True,
            )

        if count_documents(conn) >= settings.max_documents:
            raise WorkspaceFullError(
                f"Workspace is full ({settings.max_documents}/{settings.max_documents} "
                "documents). Remove a document before uploading a new one."
            )

        parsed = parse_document(filename, content)
        chunks = chunk_blocks(parsed.blocks)
        if not chunks:
            raise ValueError(f"No extractable text found in {filename!r}.")

        chunk_ids = [make_chunk_id(document_id, c.chunk_index) for c in chunks]
        embeddings = embed_texts([c.text for c in chunks])

        insert_document(
            conn,
            DocumentRecord(
                id=document_id,
                filename=filename,
                file_type=Path(filename).suffix.lower().lstrip("."),
                char_count=sum(len(b.text) for b in parsed.blocks),
            ),
        )
        insert_chunks(conn, document_id, chunks, chunk_ids, embeddings)

    return IngestResult(
        document_id=document_id,
        filename=filename,
        chunk_count=len(chunks),
        deduplicated=False,
    )
