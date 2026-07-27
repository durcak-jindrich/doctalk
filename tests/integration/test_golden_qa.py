"""The golden set driven through the real pipeline, with only the LLM faked.

Real parsing, chunking, embedding, hybrid retrieval, reranking, gating and
citation governance — so what is under test is whether a question reaches the
right document and whether an unanswerable one is refused.

The fake model is deliberately *obedient*: it cites whatever it was given.
That is the point — it removes the model as a variable, so a failure here is a
retrieval or governance failure and nothing else.
"""

import re

import pytest

from app.graph import answer_question, build_answer_graph
from app.llm import LLMClient, LLMResponse, TokenUsage
from app.retrieval import HybridRerankRetriever
from app.storage import get_connection, ingest_document
from app.synthesis import REFUSAL_TOKEN, RefusalReason
from tests.golden import DOCUMENTS, GOLDEN, GoldenCase


class ObedientClient(LLMClient):
    """Always answers, always citing source [1] — or declines when told to.

    Stands in for a perfectly-behaved model so retrieval and governance are
    the only things that can fail a case.
    """

    model = "fake/obedient"

    def __init__(self, *, decline: bool = False):
        self.decline = decline
        self.call_count = 0

    def complete(self, messages, *, temperature=None, max_tokens=None) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            text=REFUSAL_TOKEN if self.decline else "According to the sources, yes [1].",
            model=self.model,
            usage=TokenUsage(prompt_tokens=500, completion_tokens=25, cost_usd=0.00004),
            latency_ms=400.0,
        )


@pytest.fixture(scope="module")
def workspace(request):
    """Ingest the golden documents once for the whole module."""
    from app.storage import reset_schema

    reset_schema()
    for filename, content in DOCUMENTS.items():
        ingest_document(filename, content)


def ask(case: GoldenCase, client: ObedientClient):
    graph = build_answer_graph(HybridRerankRetriever(), client)
    with get_connection() as conn:
        return answer_question(conn, case.question, graph=graph)


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c.question)
def test_golden_case_routes_and_resolves_as_expected(case, workspace):
    # An obedient model answers unless the case is one where a real model
    # would have to decline for lack of grounding.
    client = ObedientClient(decline=case.outcome == "refused")
    answer = ask(case, client)

    assert answer.route == case.route, case.why

    if case.outcome == "answered":
        assert not answer.refused, f"{case.why}: refused {answer.refusal_reason}"
        assert answer.citations, "an accepted answer must carry at least one citation"
    else:
        assert answer.refused, f"{case.why}: answered instead of refusing"


@pytest.mark.parametrize("case", [c for c in GOLDEN if c.expect_document], ids=lambda c: c.question)
def test_retrieval_puts_the_right_document_first(case, workspace):
    """The real signal in this suite: did hybrid retrieval find the source?

    The fake cites [1], so the top citation is whatever retrieval ranked
    first — this asserts retrieval quality, not model behaviour.
    """
    answer = ask(case, ObedientClient())

    top = answer.citations[0]
    assert top.document_id.startswith(case.expect_document), (
        f"{case.why}: top citation came from {top.document_id!r}"
    )


@pytest.mark.parametrize(
    "case", [c for c in GOLDEN if c.refused_before_llm], ids=lambda c: c.question
)
def test_off_topic_questions_cost_nothing(case, workspace):
    """Not circular: the gate's verdict is real, and a call that never
    happened cannot have been scripted."""
    client = ObedientClient()
    answer = ask(case, client)

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.BELOW_RELEVANCE_THRESHOLD
    assert client.call_count == 0, "an off-topic question reached the model"
    assert answer.attempts == 0


def test_every_citation_in_the_set_resolves_to_a_real_stored_chunk(workspace):
    """Across the whole set, no answer may cite something that isn't there."""
    cited: set[str] = set()
    for case in GOLDEN:
        answer = ask(case, ObedientClient(decline=case.outcome == "refused"))
        cited.update(c.chunk_id for c in answer.citations)

    assert cited, "the golden set produced no citations at all"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE id = ANY(%s)", (sorted(cited),))
        resolvable = cur.fetchone()[0]

    assert resolvable == len(cited), "a citation points at a chunk_id not in the database"


def test_a_summary_represents_more_than_one_document(workspace):
    """The summarize tool splits its budget across the workspace, so its
    sources must not all come from whichever document was uploaded first."""

    class CitesEverySourceClient(ObedientClient):
        """Cites every source it was actually given.

        Markers are read off the prompt rather than hard-coded: the number of
        summary sources depends on the budget split across the workspace, and
        a fabricated marker would (correctly) be rejected by governance,
        failing this test for the wrong reason.
        """

        def complete(self, messages, *, temperature=None, max_tokens=None):
            response = super().complete(messages)
            markers = re.findall(r"^\[(\d+)\] ", messages[-1].content, re.MULTILINE)
            assert markers, "the summary prompt carried no numbered sources"
            return LLMResponse(
                text="The documents cover " + "".join(f"[{m}]" for m in markers) + ".",
                model=response.model,
                usage=response.usage,
                latency_ms=response.latency_ms,
            )

    answer = ask(
        GoldenCase("Summarize the documents", "summarize", "answered", None, False, ""),
        CitesEverySourceClient(),
    )

    assert answer.route == "summarize"
    assert not answer.refused
    assert {c.document_id.split("-")[0] for c in answer.citations} == {"hr", "it", "product"}, (
        "the summary budget did not reach every uploaded document"
    )
