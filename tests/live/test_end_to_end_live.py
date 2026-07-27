"""The full stack against a real model: ingest → retrieve → answer → govern.

Deselected by default. Run with `uv run pytest -m live` before a demo, or
after changing `LLM_MODEL`, the prompt, or the retrieval settings.

This is the only test where the model's *judgement* is what is being checked.
Everywhere else the LLM is faked, so a refusal is scripted and proves only
that refusals are plumbed through; here the model genuinely has to decline a
question the documents do not answer. That is the claim the whole system
rests on, and it cannot be verified offline.

Costs 2 LLM calls (3 if governance asks for one correction). Assertions are
structural — that an answer is grounded and its citations resolve — never
about wording, so swapping models does not break it.

DESTRUCTIVE: resets the local document workspace, like the integration suite.
"""

import psycopg
import pytest

from app.config import settings
from app.graph import answer_question, build_answer_graph
from app.llm import LLMError, OpenRouterClient
from app.retrieval import HybridRerankRetriever
from app.storage import get_connection, ingest_document, reset_schema
from app.synthesis import RefusalReason

pytestmark = pytest.mark.live

# One short document: the prompt stays small, so the call stays cheap.
HR_POLICY = b"""# HR Policy

## Vacation

Full-time employees accrue fifteen days of paid vacation per calendar year.
Requests must reach a line manager at least two weeks in advance.

## Sick Leave

Employees may take up to ten days of paid sick leave per year. A doctor's note
is required from the fourth consecutive day of absence.
"""


@pytest.fixture(scope="module")
def live_graph():
    try:
        with psycopg.connect(settings.database_url):
            pass
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable — start it with `docker compose up -d db`.")
    if not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    try:
        client = OpenRouterClient()
    except LLMError as exc:
        pytest.skip(str(exc))

    reset_schema()
    ingest_document("hr-policy.md", HR_POLICY)
    return build_answer_graph(HybridRerankRetriever(), client)


def ask(graph, question: str):
    with get_connection() as conn:
        return answer_question(conn, question, graph=graph)


def test_a_covered_question_is_answered_with_citations_that_resolve(live_graph):
    answer = ask(live_graph, "How many vacation days do full-time employees get?")

    assert not answer.refused, f"real model refused an answerable question: {answer.text}"
    assert answer.citations, "an accepted answer must carry at least one citation"

    # Every citation must name a chunk that actually exists in Postgres —
    # the end-to-end version of "never fabricate a citation".
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM chunks WHERE id = ANY(%s)",
            ([c.chunk_id for c in answer.citations],),
        )
        assert cur.fetchone()[0] == len(answer.citations)

    assert answer.total_usage.total_tokens > 0, "usage was not captured for observability"
    assert answer.total_latency_ms > 0
    assert answer.path[:2] == ["route", "retrieve"]


def test_a_real_model_declines_a_question_the_documents_do_not_answer(live_graph):
    """The claim that cannot be tested with a fake.

    Parental leave is plausible for an HR policy and absent from this one, so
    a model answering from general knowledge would invent a number. Either
    refusal path is correct: the relevance gate catching it before the call,
    or the model itself declining.
    """
    answer = ask(live_graph, "What is the parental leave allowance for new parents?")

    assert answer.refused, f"model answered from outside the documents: {answer.text}"
    assert answer.refusal_reason in (
        RefusalReason.MODEL_DECLINED,
        RefusalReason.BELOW_RELEVANCE_THRESHOLD,
    )
    assert not answer.citations
