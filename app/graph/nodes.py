"""The graph's nodes: one responsibility each, dependencies injected.

Nodes are built by `make_nodes()` as closures over the retriever and LLM
client, so the graph is compiled once and tests can drive it with a fake
client and no database.

Refusals are written by nodes rather than by the edges between them: an edge
function cannot write state, and the reason for a refusal is state the caller
needs. So a node that decides to stop sets `answer`, and the edge that follows
it only has to ask whether `answer` is set.

Timing and logging are not written into the nodes. `_instrument` wraps each
one, so every node is measured and logged the same way and a node body stays
about its decision. A node reports what it decided by returning `_detail`,
which the wrapper moves onto the step record.
"""

import inspect
import logging
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from psycopg import Connection

from app.config import settings
from app.llm import LLMClient, Message
from app.observability import NodeStep, timed
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


def _run_state(state: AskState) -> dict[str, Any]:
    """Observability carried onto whatever `Answer` a node produces.

    `steps` is deliberately absent: the wrapper has not recorded the current
    node yet, so the authoritative list is attached by `answer_question` once
    the run is over.
    """
    return {
        "route": state.get("route", "qa"),
        "attempts": state.get("attempts", 0),
        "usages": state.get("usages", []),
        "model": state.get("model"),
        "llm_latency_ms": state.get("llm_latency_ms", 0.0),
    }


def _instrument(name: str, fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Time a node, log it as one structured event, and record its step."""
    # LangGraph always hands the wrapper a config; only some nodes want one.
    wants_config = "config" in inspect.signature(fn).parameters

    def wrapper(state: AskState, config: RunnableConfig) -> dict[str, Any]:
        with timed() as elapsed:
            update = fn(state, config) if wants_config else fn(state)

        detail = update.pop("_detail", {})
        logger.info(
            "node %s finished in %.0fms",
            name,
            elapsed.ms,
            extra={
                "event": "graph.node",
                "node": name,
                "duration_ms": round(elapsed.ms, 3),
                **detail,
            },
        )
        return {
            **update,
            "steps": [NodeStep(node=name, duration_ms=round(elapsed.ms, 1), detail=detail)],
        }

    # LangGraph inspects the signature to decide whether to pass `config`.
    wrapper.__name__ = name
    return wrapper


def make_nodes(retriever: HybridRerankRetriever, client: LLMClient) -> NodeMap:
    def route(state: AskState) -> dict[str, Any]:
        """Pick the tool for this question — see `routing.classify`."""
        chosen = classify(state["question"])
        return {"route": chosen, "_detail": {"route": chosen}}

    def retrieve(state: AskState, config: RunnableConfig) -> dict[str, Any]:
        """Hybrid retrieval, plus the two gates that refuse before spending an
        LLM call: nothing found, and nothing found that is relevant enough."""
        threshold = _setting(config, "min_rerank_score", settings.min_rerank_score)
        chunks = retriever.retrieve(_connection(config), state["question"])

        if not chunks:
            return {
                "chunks": [],
                "answer": refuse(RefusalReason.NO_RETRIEVAL, **_run_state(state)),
                "_detail": {"chunks": 0, "verdict": "refused_no_retrieval"},
            }

        best_score = max(chunk.rerank_score for chunk in chunks)
        detail = {
            "chunks": len(chunks),
            "best_score": round(best_score, 3),
            "threshold": threshold,
        }
        if best_score < threshold:
            return {
                "chunks": chunks,
                "answer": refuse(RefusalReason.BELOW_RELEVANCE_THRESHOLD, **_run_state(state)),
                "_detail": {**detail, "verdict": "refused_below_threshold"},
            }

        return {
            "chunks": chunks,
            "messages": build_messages(state["question"], chunks),
            "_detail": {**detail, "verdict": "proceed"},
        }

    def gather_summary_sources(state: AskState, config: RunnableConfig) -> dict[str, Any]:
        """The summarize tool's source selection.

        Takes each document's opening chunks instead of relevance-ranked ones,
        sharing a fixed budget across the workspace so no document is left out
        and the prompt stays bounded.
        """
        conn = _connection(config)
        budget = _setting(config, "summary_max_chunks", settings.summary_max_chunks)

        document_count = count_documents(conn)
        if document_count == 0:
            return {
                "chunks": [],
                "answer": refuse(RefusalReason.NO_RETRIEVAL, **_run_state(state)),
                "_detail": {"documents": 0, "chunks": 0, "verdict": "refused_empty_workspace"},
            }

        per_document = max(1, budget // document_count)
        rows = get_leading_chunks(conn, per_document)
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
        detail = {
            "documents": document_count,
            "chunks": len(chunks),
            "per_document": per_document,
        }
        if not chunks:
            return {
                "chunks": [],
                "answer": refuse(RefusalReason.NO_RETRIEVAL, **_run_state(state)),
                "_detail": {**detail, "verdict": "refused_no_chunks"},
            }

        return {
            "chunks": chunks,
            "messages": build_summary_messages(state["question"], chunks),
            "_detail": {**detail, "verdict": "proceed"},
        }

    def draft(state: AskState) -> dict[str, Any]:
        """One LLM call. The retry loop lives in the edges, not in here."""
        attempt = state.get("attempts", 0) + 1
        response = client.complete(state["messages"])
        return {
            "draft": response.text.strip(),
            "attempts": attempt,
            "usages": [response.usage],
            "model": response.model,
            "llm_latency_ms": state.get("llm_latency_ms", 0.0) + response.latency_ms,
            "_detail": {
                "attempt": attempt,
                "model": response.model,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cost_usd": response.usage.cost_usd,
                "llm_latency_ms": round(response.latency_ms, 1),
            },
        }

    def govern(state: AskState, config: RunnableConfig) -> dict[str, Any]:
        """Deterministic citation governance: accept, correct, or refuse.

        The only node that can put an answer in front of a user, so every
        outcome is decided in one place.
        """
        max_attempts = _setting(config, "max_attempts", settings.synthesis_max_attempts)
        chunks = state["chunks"]
        raw = state["draft"]
        run_state = _run_state(state)
        attempts = state.get("attempts", 0)

        if is_refusal(raw):
            return {
                "answer": refuse(RefusalReason.MODEL_DECLINED, **run_state),
                "_detail": {"verdict": "refused_model_declined", "attempt": attempts},
            }

        text = normalize_markers(raw, chunks)
        report = validate_citations(text, chunks)
        problem = describe_problem(report)
        if problem is None:
            return {
                "answer": Answer(text=text, citations=report.citations, **run_state),
                "_detail": {
                    "verdict": "accepted",
                    "attempt": attempts,
                    "citations": len(report.citations),
                },
            }

        if attempts >= max_attempts:
            return {
                "answer": refuse(RefusalReason.UNGROUNDED, **run_state),
                "_detail": {
                    "verdict": "refused_ungrounded",
                    "attempt": attempts,
                    "problem": problem,
                },
            }

        correction: list[Message] = [
            *state["messages"],
            Message(role="assistant", content=raw),
            build_correction_message(problem, len(chunks)),
        ]
        return {
            "messages": correction,
            "_detail": {"verdict": "correction_requested", "attempt": attempts, "problem": problem},
        }

    return {
        name: _instrument(name, fn)
        for name, fn in [
            ("route", route),
            ("retrieve", retrieve),
            ("gather_summary_sources", gather_summary_sources),
            ("draft", draft),
            ("govern", govern),
        ]
    }
