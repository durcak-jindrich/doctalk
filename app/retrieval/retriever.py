from dataclasses import dataclass

from pgvector import Vector
from psycopg import Connection
from psycopg.rows import dict_row

from .embedder import embed_texts
from .reranker import rerank as cross_encoder_rerank


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    text: str
    section_path: list[str] | None
    page_number: int | None
    rerank_score: float
    dense_rank: int | None
    lexical_rank: int | None


def _dense_search(conn: Connection, query_vector: list[float], limit: int) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM chunks ORDER BY embedding <=> %s LIMIT %s",
            (Vector(query_vector), limit),
        )
        return [row[0] for row in cur.fetchall()]


def _lexical_search(conn: Connection, query_text: str, limit: int) -> list[str]:
    """BM25 search over `chunks.text`.

    Deliberately uses `paradedb.match` rather than the `text @@@ '<query>'`
    string form: the string form feeds raw input to ParadeDB's query-string
    parser, so ordinary punctuation in a user's question (`:`, `-`, `"`, `(`)
    is read as query syntax and raises a parse error. `paradedb.match` takes
    the input as plain terms, tokenized by the field's indexed analyzer.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM chunks WHERE id @@@ paradedb.match('text', %s) "
            "ORDER BY paradedb.score(id) DESC LIMIT %s",
            (query_text, limit),
        )
        return [row[0] for row in cur.fetchall()]


def _rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda cid: scores[cid], reverse=True)


def _fetch_chunks(conn: Connection, chunk_ids: list[str]) -> dict[str, dict]:
    if not chunk_ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT c.id, c.document_id, c.text, c.section_path, c.page_number, d.filename "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE c.id = ANY(%s)",
            (chunk_ids,),
        )
        return {row["id"]: row for row in cur.fetchall()}


class HybridRerankRetriever:
    """Dense (pgvector) + lexical (pg_search/BM25) search, RRF-fused, then
    cross-encoder reranked. The one production `Retriever` — see
    docs/technical-decisions.md for why hybrid, not a swappable tier."""

    def __init__(self, leg_top_k: int = 20, fused_top_k: int = 20):
        self.leg_top_k = leg_top_k
        self.fused_top_k = fused_top_k

    def retrieve(self, conn: Connection, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        query_vector = embed_texts([query])[0]

        dense_ids = _dense_search(conn, query_vector, self.leg_top_k)
        lexical_ids = _lexical_search(conn, query, self.leg_top_k)
        dense_rank = {cid: i + 1 for i, cid in enumerate(dense_ids)}
        lexical_rank = {cid: i + 1 for i, cid in enumerate(lexical_ids)}

        fused_ids = _rrf_fuse([dense_ids, lexical_ids])[: self.fused_top_k]
        if not fused_ids:
            return []

        rows = _fetch_chunks(conn, fused_ids)
        candidates = [rows[cid] for cid in fused_ids if cid in rows]
        if not candidates:
            return []

        rerank_scores = cross_encoder_rerank(query, [c["text"] for c in candidates])
        ranked = sorted(
            zip(candidates, rerank_scores, strict=True), key=lambda p: p[1], reverse=True
        )

        return [
            RetrievedChunk(
                chunk_id=row["id"],
                document_id=row["document_id"],
                filename=row["filename"],
                text=row["text"],
                section_path=row["section_path"],
                page_number=row["page_number"],
                rerank_score=score,
                dense_rank=dense_rank.get(row["id"]),
                lexical_rank=lexical_rank.get(row["id"]),
            )
            for row, score in ranked[:top_k]
        ]
