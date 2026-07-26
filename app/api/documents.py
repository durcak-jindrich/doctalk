"""Document workspace routes: upload, list, inspect, delete.

Handlers are sync `def`, so FastAPI runs them in its threadpool. Parsing,
embedding and psycopg are all blocking; `async def` here would block the event
loop instead of yielding it. See `app/llm/base.py` for the same reasoning on
the LLM side.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from psycopg import Connection

from app.config import settings
from app.storage import (
    WorkspaceFullError,
    delete_document,
    get_chunks,
    get_document,
    ingest_document,
)

from .deps import db, workspace_state
from .schemas import (
    ChunkOut,
    DocumentDetailOut,
    UploadResponseOut,
    UploadResultOut,
    WorkspaceOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _ingest_one(upload: UploadFile) -> UploadResultOut:
    """Ingest one file, converting every expected failure into a result row.

    Only *expected* failures are caught here — a bad file must not abort the
    rest of the batch, but an unexpected error still propagates to the 500
    handler rather than being reported as a tidy rejection.
    """
    filename = upload.filename or "unnamed"
    content = upload.file.read()

    if not content:
        return UploadResultOut(filename=filename, status="rejected", error="The file is empty.")
    if len(content) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes / 1024 / 1024
        return UploadResultOut(
            filename=filename,
            status="rejected",
            error=f"File is larger than the {limit_mb:.0f} MB limit.",
        )

    try:
        result = ingest_document(filename, content)
    except WorkspaceFullError as exc:
        return UploadResultOut(filename=filename, status="rejected", error=str(exc))
    except (ValueError, UnicodeDecodeError) as exc:
        # Unsupported extension, no text layer, or undecodable bytes.
        logger.info("rejected upload %r: %s", filename, exc)
        return UploadResultOut(filename=filename, status="rejected", error=str(exc))

    return UploadResultOut(
        filename=result.filename,
        status="duplicate" if result.deduplicated else "ingested",
        document_id=result.document_id,
        chunk_count=result.chunk_count,
    )


@router.post("", response_model=UploadResponseOut)
def upload_documents(
    files: Annotated[list[UploadFile], File()],
    conn: Annotated[Connection, Depends(db)],
) -> UploadResponseOut:
    """Ingest one or more documents, reporting the outcome of each."""
    results = [_ingest_one(upload) for upload in files]
    ingested = sum(1 for r in results if r.status == "ingested")
    logger.info("upload: %d file(s), %d ingested", len(results), ingested)
    return UploadResponseOut(results=results, workspace=workspace_state(conn))


@router.get("", response_model=WorkspaceOut)
def list_workspace(conn: Annotated[Connection, Depends(db)]) -> WorkspaceOut:
    return workspace_state(conn)


@router.get("/{document_id}", response_model=DocumentDetailOut)
def read_document(document_id: str, conn: Annotated[Connection, Depends(db)]) -> DocumentDetailOut:
    document = get_document(conn, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No document {document_id!r}.")
    chunks = get_chunks(conn, document_id)
    return DocumentDetailOut(
        **document,
        chunk_count=len(chunks),
        chunks=[ChunkOut(**row) for row in chunks],
    )


@router.delete("/{document_id}", response_model=WorkspaceOut)
def remove_document(document_id: str, conn: Annotated[Connection, Depends(db)]) -> WorkspaceOut:
    """Delete a document and free its slot. Chunks cascade with it."""
    if not delete_document(conn, document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No document {document_id!r}.")
    logger.info("deleted document %s", document_id)
    return workspace_state(conn)
