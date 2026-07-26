"""The graph's nodes: one responsibility each, dependencies injected.

Nodes are built by `make_nodes()` as closures over the retriever and LLM
client, so the graph is compiled once and tests can drive it with a fake
client and no database.

Refusals are written by nodes rather than by the edges between them: an edge
function cannot write state, and the reason for a refusal is state the caller
needs. So a node that decides to stop sets `answer`, and the edge that follows
it only has to ask whether `answer` is set.
"""

import logging
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from psycopg import Connection

from app.config import settings
from app.llm import LLMClient, Message
from app.retrieval import RetrievedChunk
from app.retrieval.retriever import HybridRerankRetriever
from app.storage import count_documents, get_leading_chunks
from app.synthesis import (
    Answer,
    RefusalReason,
    build_correction_message,
    build_messages,
    build_summary_messages,
    describe_problem,
    is_refusal,
    normalize_markers,
    refuse,
    validate_citations,
)

from .routing import classify
from .state import AskState

logger = logging.getLogger(__name__)

NodeMap = dict[str, Callable[..., dict[str, Any]]]


def _setting(config: RunnableConfig, key: str, default: Any) -> Any:
    """Read a per-run override, falling back to the deployed default."""
    value = config.get("configurable", {}).get(key)
    return default if value is None else value


def _connection(config: RunnableConfig) -> Connection:
    """The request's database handle.

    Passed per-run through `configurable` rather than held in graph state: a
    live connection is not serializable, and state is what a checkpointer
    would persist.
    """
    conn = config.get("configurable", {}).get("conn")
    if conn is None:
        raise ValueError("no database connection supplied to the graph run")
    return conn


def _run_state(state: AskState, step: str) -> dict[str, Any]:
    """Observability carried onto whatever `Answer` a node produces.

    `step` is the node writing the answer. It has to be appended by hand
    because state reducers only run *after* a node returns, so `state["steps"]`
    still ends at the previous node while this one is executing.
    """
    return {
        "route": state.get("route", "qa"),
        "steps": [*state.get("steps", []), step],
        "attempts": state.get("attempts", 0),
        "usages": state.get("usages", []),
        "model": state.get("model"),
        "llm_latency_ms": state.get("llm_latency_ms", 0.0),
    }


def make_nodes(retriever: HybridRerankRetriever, client: LLMClient) -> NodeMap:
    def route(state: AskState) -> dict[str, Any]:
        """Pick the tool for this question — see `routing.classify`."""
        chosen = classify(state["question"])
        logger.info("routing question to %s", chosen)
        return {"route": chosen, "steps": ["route"]}

    def retrieve(state: AskState, config: RunnableConfig) -> dict[str, Any]:
        """Hybrid retrieval, plus the two gates that refuse before spending an
        LLM call: nothing found, and nothing found that is relevant enough."""
        threshold = _setting(config, "min_rerank_score", settings.min_rerank_score)
        chunks = retriever.retrieve(_connection(config), state["question"])
        step = {"steps": ["retrieve"]}

        if not chunks:
            logger.info("refusing: retrieval returned no chunks")
            return {
                **step,
                "chunks": [],
                "answer": refuse(RefusalReason.NO_RETRIEVAL, **_run_state(state, "retrieve")),
            }

        best_score = max(chunk.rerank_score for chunk in chunks)
        if best_score < threshold:
            logger.info(
                "refusing: best rerank score %.2f is below threshold %.2f", best_score, threshold
            )
            return {
                **step,
                "chunks": chunks,
                "answer": refuse(
                    RefusalReason.BELOW_RELEVANCE_THRESHOLD, **_run_state(state, "retrieve")
                ),
            }

        return {**step, "chunks": chunks, "messages": build_messages(state["question"], chunks)}

    def gather_summary_sources(state: AskState, config: RunnableConfig) -> dict[str, Any]:
        """The summarize tool's source selection.

        Takes each document's opening chunks instead of relevance-ranked ones,
        sharing a fixed budget across the workspace so no document is left out
        and the prompt stays bounded.
        """
        conn = _connection(config)
        budget = _setting(config, "summary_max_chunks", settings.summary_max_chunks)
        step = {"steps": ["gather_summary_sources"]}

        document_count = count_documents(conn)
        if document_count == 0:
            logger.info("refusing: summary requested with an empty workspace")
            return {
                **step,
                "chunks": [],
                "answer": refuse(
                    RefusalReason.NO_RETRIEVAL, **_run_state(state, "gather_summary_sources")
                ),
            }

        rows = get_leading_chunks(conn, max(1, budget // document_count))
        # Not ranked against a query, so `rerank_score` is not meaningful here
        # and the rank fields stay unset — the UI reads that as "not scored".
        chunks = [
            RetrievedChunk(
                chunk_id=row["id"],
                document_id=row["document_id"],
                filename=row["filename"],
                text=row["text"],
                section_path=row["section_path"],
                page_number=row["page_number"],
                rerank_score=0.0,
                dense_rank=None,
                lexical_rank=None,
            )
            for row in rows
        ]
        if not chunks:
            logger.info("refusing: documents exist but hold no chunks")
            return {
                **step,
                "chunks": [],
                "answer": refuse(
                    RefusalReason.NO_RETRIEVAL, **_run_state(state, "gather_summary_sources")
                ),
            }

        return {
            **step,
            "chunks": chunks,
            "messages": build_summary_messages(state["question"], chunks),
        }

    def draft(state: AskState) -> dict[str, Any]:
        """One LLM call. The retry loop lives in the edges, not in here."""
        response = client.complete(state["messages"])
        return {
            "draft": response.text.strip(),
            "attempts": state.get("attempts", 0) + 1,
            "usages": [response.usage],
            "model": response.model,
            "llm_latency_ms": state.get("llm_latency_ms", 0.0) + response.latency_ms,
            "steps": ["draft"],
        }

    def govern(state: AskState, config: RunnableConfig) -> dict[str, Any]:
        """Deterministic citation governance: accept, correct, or refuse.

        The only node that can put an answer in front of a user, so every
        outcome is decided in one place.
        """
        max_attempts = _setting(config, "max_attempts", settings.synthesis_max_attempts)
        chunks = state["chunks"]
        raw = state["draft"]
        step = {"steps": ["govern"]}
        run_state = _run_state(state, "govern")

        if is_refusal(raw):
            logger.info("refusing: model declined for lack of grounding")
            return {**step, "answer": refuse(RefusalReason.MODEL_DECLINED, **run_state)}

        text = normalize_markers(raw, chunks)
        report = validate_citations(text, chunks)
        problem = describe_problem(report)
        if problem is None:
            return {**step, "answer": Answer(text=text, citations=report.citations, **run_state)}

        attempts = state.get("attempts", 0)
        logger.warning("citation validation failed on attempt %d: %s", attempts, problem)
        if attempts >= max_attempts:
            logger.warning("refusing: citations still invalid after %d attempts", attempts)
            return {**step, "answer": refuse(RefusalReason.UNGROUNDED, **run_state)}

        correction: list[Message] = [
            *state["messages"],
            Message(role="assistant", content=raw),
            build_correction_message(problem, len(chunks)),
        ]
        return {**step, "messages": correction}

    return {
        "route": route,
        "retrieve": retrieve,
        "gather_summary_sources": gather_summary_sources,
        "draft": draft,
        "govern": govern,
    }
