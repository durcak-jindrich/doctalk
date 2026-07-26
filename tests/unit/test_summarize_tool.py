"""The summarize route: structural source selection, same citation governance.

Storage is monkeypatched rather than injected — these are thin SQL helpers,
and the real queries are covered by the integration suite.
"""

import pytest

from app.graph import answer_question, build_answer_graph
from app.synthesis import RefusalReason
from tests.fakes import FAKE_CONN, FakeLLMClient, FakeRetriever


def make_row(document_id: str, index: int, filename: str) -> dict:
    return {
        "id": f"{document_id}#c{index:04d}",
        "document_id": document_id,
        "filename": filename,
        "text": f"Opening passage {index} of {filename}.",
        "section_path": None,
        "page_number": None,
    }


@pytest.fixture
def summarize(monkeypatch):
    """Run a summary request against a stubbed workspace."""

    def run(rows: list[dict], replies: list[str], *, document_count: int | None = None, **kwargs):
        seen: dict = {}
        monkeypatch.setattr(
            "app.graph.nodes.count_documents",
            lambda conn: (
                document_count
                if document_count is not None
                else len({row["document_id"] for row in rows})
            ),
        )

        def fake_leading_chunks(conn, per_document):
            seen["per_document"] = per_document
            return rows

        monkeypatch.setattr("app.graph.nodes.get_leading_chunks", fake_leading_chunks)

        client = FakeLLMClient(replies)
        graph = build_answer_graph(FakeRetriever([]), client)
        answer = answer_question(FAKE_CONN, "Summarize the documents", graph=graph, **kwargs)
        return answer, client, seen

    return run


def test_summary_answers_from_document_openings_with_citations(summarize):
    rows = [make_row("handbook-a1", 1, "handbook.md"), make_row("policy-b2", 1, "policy.pdf")]

    answer, client, _ = summarize(
        rows, ["The handbook covers leave [1]. The policy covers IT [2]."]
    )

    assert not answer.refused
    assert answer.route == "summarize"
    assert [c.chunk_id for c in answer.citations] == ["handbook-a1#c0001", "policy-b2#c0001"]
    assert answer.steps == ["route", "gather_summary_sources", "draft", "govern"]
    # Retrieval is skipped entirely — there is no query to be relevant to.
    assert "retrieve" not in answer.steps
    assert client.call_count == 1


def test_summary_budget_is_split_across_the_uploaded_documents(summarize):
    rows = [make_row("d1", 1, "a.md"), make_row("d2", 1, "b.md")]

    _, _, seen = summarize(rows, ["Both documents [1][2]."], summary_max_chunks=12)

    assert seen["per_document"] == 6


def test_summary_budget_never_drops_below_one_chunk_per_document(summarize):
    rows = [make_row(f"d{i}", 1, f"{i}.md") for i in range(5)]

    _, _, seen = summarize(rows, ["All five [1][2][3][4][5]."], summary_max_chunks=3)

    assert seen["per_document"] == 1


def test_summary_refuses_on_an_empty_workspace_without_calling_the_llm(summarize):
    answer, client, _ = summarize([], [], document_count=0)

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.NO_RETRIEVAL
    assert client.call_count == 0
    assert answer.steps == ["route", "gather_summary_sources"]


def test_summary_sources_are_not_presented_as_relevance_ranked(summarize):
    rows = [make_row("handbook-a1", 1, "handbook.md")]

    answer, _, _ = summarize(rows, ["The handbook covers leave [1]."])

    citation = answer.citations[0]
    assert citation.dense_rank is None
    assert citation.lexical_rank is None


def test_an_uncited_summary_is_governed_exactly_like_an_answer(summarize):
    rows = [make_row("handbook-a1", 1, "handbook.md")]

    answer, client, _ = summarize(
        rows, ["The handbook covers leave.", "The handbook covers leave [1]."]
    )

    assert not answer.refused
    assert answer.attempts == 2
    assert client.call_count == 2


def test_a_persistently_ungrounded_summary_is_refused(summarize):
    rows = [make_row("handbook-a1", 1, "handbook.md")]

    answer, _, _ = summarize(rows, ["Invented [9].", "Still invented [9]."])

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.UNGROUNDED
    assert "Invented" not in answer.text
