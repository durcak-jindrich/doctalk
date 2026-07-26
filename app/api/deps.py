"""Shared request dependencies."""

from collections.abc import Iterator

from psycopg import Connection

from app.config import settings
from app.graph import default_graph
from app.storage import count_documents, get_connection, list_documents

from .schemas import DocumentOut, WorkspaceOut


def db() -> Iterator[Connection]:
    """One connection per request, committed or rolled back on the way out."""
    with get_connection() as conn:
        yield conn


def answer_graph():
    """The compiled `/ask` graph.

    A dependency rather than a direct call so tests can swap in a graph backed
    by a fake LLM — the API contract is then exercised without spending quota.
    """
    return default_graph()


def workspace_state(conn: Connection) -> WorkspaceOut:
    """Documents and remaining capacity, as returned alongside every mutation.

    Returned by upload and delete rather than left for the client to re-fetch:
    the slot indicator must never disagree with what the server just did.
    """
    documents = [DocumentOut(**row) for row in list_documents(conn)]
    used = count_documents(conn)
    return WorkspaceOut(
        documents=documents,
        used=used,
        capacity=settings.max_documents,
        remaining=max(0, settings.max_documents - used),
    )
