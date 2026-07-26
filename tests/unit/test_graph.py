"""The `/ask` pipeline end to end, with a fake retriever and a fake LLM.

These are the groundedness tests: every path that could put text in front of a
user goes through here, including the ones that must refuse.
"""

import pytest

from app.graph import answer_question, build_answer_graph
from app.synthesis import REFUSAL_TOKEN, RefusalReason
from tests.fakes import FAKE_CONN, FakeLLMClient, FakeRetriever, make_chunk

CHUNKS = [
    make_chunk("handbook-abc123#c0001", text="25 days of annual leave.", rerank_score=8.0),
    make_chunk("handbook-abc123#c0007", text="A doctor's note is needed after 3 days."),
]


def ask(question: str, chunks, replies: list[str], **kwargs):
    """Run the graph over `chunks`, replaying `replies` as the model."""
    client = FakeLLMClient(replies)
    retriever = FakeRetriever(chunks)
    graph = build_answer_graph(retriever, client)
    answer = answer_question(FAKE_CONN, question, graph=graph, **kwargs)
    return answer, client


def test_answers_and_resolves_citations_when_grounded():
    answer, client = ask("How much leave?", CHUNKS, ["Employees get 25 days of annual leave [1]."])

    assert not answer.refused
    assert answer.text == "Employees get 25 days of annual leave [1]."
    assert [c.chunk_id for c in answer.citations] == ["handbook-abc123#c0001"]
    assert answer.attempts == 1
    assert client.call_count == 1
    assert answer.route == "qa"
    assert answer.steps == ["route", "retrieve", "draft", "govern"]


def test_refuses_without_calling_the_llm_when_retrieval_is_empty():
    answer, client = ask("Anything?", [], [])

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.NO_RETRIEVAL
    assert answer.citations == []
    assert client.call_count == 0
    assert answer.steps == ["route", "retrieve"]


def test_refuses_without_calling_the_llm_when_nothing_scores_as_relevant():
    off_topic = [make_chunk(rerank_score=-11.0), make_chunk(rerank_score=-9.4)]

    answer, client = ask("Who won the 1998 World Cup?", off_topic, [])

    assert answer.refusal_reason is RefusalReason.BELOW_RELEVANCE_THRESHOLD
    assert client.call_count == 0


def test_one_relevant_chunk_is_enough_to_reach_the_llm():
    mixed = [make_chunk(rerank_score=-11.0), make_chunk(rerank_score=2.0)]

    answer, client = ask("Question?", mixed, ["Yes [2]."])

    assert not answer.refused
    assert client.call_count == 1


def test_refuses_when_the_model_declines_for_lack_of_grounding():
    answer, _ = ask("What is the CEO's salary?", CHUNKS, [REFUSAL_TOKEN])

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.MODEL_DECLINED
    assert REFUSAL_TOKEN not in answer.text
    assert answer.attempts == 1


def test_declining_is_detected_even_when_the_model_wraps_the_token():
    answer, _ = ask("q", CHUNKS, [f"I'm sorry — {REFUSAL_TOKEN}."])

    assert answer.refusal_reason is RefusalReason.MODEL_DECLINED


def test_invalid_citation_triggers_one_corrective_retry_then_succeeds():
    answer, client = ask(
        "How much leave?", CHUNKS, ["Leave is 25 days [7].", "Leave is 25 days [1]."]
    )

    assert not answer.refused
    assert answer.attempts == 2
    assert [c.marker for c in answer.citations] == [1]
    assert "[7]" in client.last_prompt  # the correction names the bad marker
    # The retry is a real edge back through the graph, not a hidden inner loop.
    assert answer.steps == ["route", "retrieve", "draft", "govern", "draft", "govern"]


def test_uncited_answer_triggers_a_retry():
    answer, _ = ask(
        "How much leave?",
        CHUNKS,
        ["Employees get 25 days of leave.", "Employees get 25 days [1]."],
    )

    assert not answer.refused
    assert answer.attempts == 2


def test_refuses_rather_than_passing_through_a_persistently_ungrounded_answer():
    answer, client = ask(
        "How much leave?", CHUNKS, ["Leave is 40 days [7].", "Leave is 40 days [8]."]
    )

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.UNGROUNDED
    assert "40 days" not in answer.text
    assert answer.attempts == 2
    assert client.call_count == 2


def test_retry_budget_is_configurable_and_bounded():
    answer, client = ask("q", CHUNKS, ["[7]"] * 5, max_attempts=3)

    assert client.call_count == 3
    assert answer.refused


def test_a_large_retry_budget_still_refuses_rather_than_hitting_the_graph_limit():
    """The recursion limit scales with the budget, so the refusal path wins."""
    answer, client = ask("q", CHUNKS, ["[7]"] * 40, max_attempts=20)

    assert client.call_count == 20
    assert answer.refused
    assert answer.refusal_reason is RefusalReason.UNGROUNDED


def test_relevance_threshold_is_configurable_per_run():
    answer, client = ask("q", CHUNKS, [], min_rerank_score=9.0)

    assert answer.refusal_reason is RefusalReason.BELOW_RELEVANCE_THRESHOLD
    assert client.call_count == 0


def test_usage_is_accumulated_across_attempts_for_observability():
    answer, _ = ask("q", CHUNKS, ["Bad [7].", "Good [1]."])

    assert len(answer.usages) == 2
    assert answer.total_usage.total_tokens == 240
    assert answer.total_usage.cost_usd == pytest.approx(0.0002)
    assert answer.llm_latency_ms == 25.0
    assert answer.model == "fake/model"
