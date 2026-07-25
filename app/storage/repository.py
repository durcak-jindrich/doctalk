from dataclasses import dataclass

from psycopg import Connection
from psycopg.rows import dict_row

from app.chunking import Chunk


class WorkspaceFullError(Exception):
    """Raised when an upload would exceed the max_documents cap."""


@dataclass
class DocumentRecord:
    id: str
    filename: str
    file_type: str
    char_count: int


def count_documents(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM documents")
        return cur.fetchone()[0]


def count_chunks(conn: Connection, document_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (document_id,))
        return cur.fetchone()[0]


def document_exists(conn: Connection, document_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM documents WHERE id = %s", (document_id,))
        return cur.fetchone() is not None


def insert_document(conn: Connection, record: DocumentRecord) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (id, filename, file_type, char_count) VALUES (%s, %s, %s, %s)",
            (record.id, record.filename, record.file_type, record.char_count),
        )


def insert_chunks(
    conn: Connection,
    document_id: str,
    chunks: list[Chunk],
    chunk_ids: list[str],
    embeddings: list[list[float]],
) -> None:
    rows = [
        (
            chunk_id,
            document_id,
            chunk.chunk_index,
            chunk.section_path,
            chunk.page_number,
            chunk.char_start,
            chunk.char_end,
            chunk.text,
            embedding,
        )
        for chunk_id, chunk, embedding in zip(chunk_ids, chunks, embeddings, strict=True)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks
                (id, document_id, chunk_index, section_path, page_number,
                 char_start, char_end, text, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def list_documents(conn: Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, filename, file_type, char_count, uploaded_at "
            "FROM documents ORDER BY uploaded_at"
        )
        return cur.fetchall()


def get_document(conn: Connection, document_id: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, filename, file_type, char_count, uploaded_at FROM documents WHERE id = %s",
            (document_id,),
        )
        return cur.fetchone()


def get_chunks(conn: Connection, document_id: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, chunk_index, section_path, page_number, char_start, char_end, text "
            "FROM chunks WHERE document_id = %s ORDER BY chunk_index",
            (document_id,),
        )
        return cur.fetchall()


def delete_document(conn: Connection, document_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        return cur.rowcount > 0
