"""Wiring the nodes into the `/ask` pipeline.

                      ┌─ retrieve ─────────────┐
    START → route ────┤                        ├─→ draft → govern ─→ END
                      └─ gather_summary_sources┘        ↑         │
                                                        └─ retry ─┘

Why the corrective retry is an edge rather than a `for` loop: it is a
governance decision, and as an edge it shows up in the run's node path. An
answer that needed a correction is visibly different from one that did not,
which is what the observability panel and the evaluation both report on.

Two edges guard against runaway cost. Both `retrieve` and
`gather_summary_sources` can end the run before any LLM call, and `govern`
enforces the attempt budget, so the retry edge cannot cycle indefinitely.
"""

from functools import lru_cache

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from psycopg import Connection

from app.config import settings
from app.llm import LLMClient
from app.llm.openrouter import OpenRouterClient
from app.retrieval.retriever import HybridRerankRetriever
from app.synthesis import Answer

from .nodes import make_nodes
from .state import AskState


def _after_sources(state: AskState) -> str:
    """Stop if a source node already refused; otherwise draft an answer."""
    return END if state.get("answer") is not None else "draft"


def _after_govern(state: AskState) -> str:
    """Stop once governance has produced an answer; otherwise redraft."""
    return END if state.get("answer") is not None else "draft"


def build_answer_graph(retriever: HybridRerankRetriever, client: LLMClient):
    """Compile the `/ask` graph against an injected retriever and LLM client."""
    nodes = make_nodes(retriever, client)
    graph = StateGraph(AskState)
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        lambda state: state["route"],
        {"qa": "retrieve", "summarize": "gather_summary_sources"},
    )
    graph.add_conditional_edges("retrieve", _after_sources, {"draft": "draft", END: END})
    graph.add_conditional_edges(
        "gather_summary_sources", _after_sources, {"draft": "draft", END: END}
    )
    graph.add_edge("draft", "govern")
    graph.add_conditional_edges("govern", _after_govern, {"draft": "draft", END: END})

    return graph.compile()


@lru_cache(maxsize=1)
def default_graph():
    """The production graph, built once per process.

    Cached because constructing it loads the embedding and cross-encoder
    models — a per-request cost the API must not pay.
    """
    return build_answer_graph(HybridRerankRetriever(), OpenRouterClient())


def answer_question(
    conn: Connection,
    question: str,
    *,
    graph=None,
    max_attempts: int | None = None,
    min_rerank_score: float | None = None,
    summary_max_chunks: int | None = None,
) -> Answer:
    """Answer `question` from the uploaded documents, or refuse and say why.

    The single entry point behind `/ask`. Per-run tunables go through the
    graph's config rather than a rebuild, so one compiled graph serves every
    request; `None` means "use the deployed setting".
    """
    runner = graph if graph is not None else default_graph()
    attempts = max_attempts if max_attempts is not None else settings.synthesis_max_attempts
    config: RunnableConfig = {
        "configurable": {
            "conn": conn,
            "max_attempts": max_attempts,
            "min_rerank_score": min_rerank_score,
            "summary_max_chunks": summary_max_chunks,
        },
        # A run visits `route`, one source node, then `draft` + `govern` per
        # attempt. Derived rather than fixed so raising the retry budget can't
        # turn a refusal into a recursion error; the margin means this only
        # ever fires if the graph itself fails to terminate.
        "recursion_limit": 2 * attempts + 6,
    }
    final = runner.invoke({"question": question}, config=config)
    return final["answer"]
