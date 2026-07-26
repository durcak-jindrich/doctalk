from app.synthesis import REFUSAL_TOKEN, RefusalReason, synthesize
from app.synthesis.prompt import build_messages
from tests.fakes import FakeLLMClient, make_chunk

CHUNKS = [
    make_chunk("handbook-abc123#c0001", text="25 days of annual leave.", rerank_score=8.0),
    make_chunk("handbook-abc123#c0007", text="A doctor's note is needed after 3 days."),
]


def test_answers_and_resolves_citations_when_grounded():
    client = FakeLLMClient(["Employees get 25 days of annual leave [1]."])

    answer = synthesize("How much leave?", CHUNKS, client)

    assert not answer.refused
    assert answer.text == "Employees get 25 days of annual leave [1]."
    assert [c.chunk_id for c in answer.citations] == ["handbook-abc123#c0001"]
    assert answer.attempts == 1
    assert client.call_count == 1


def test_refuses_without_calling_the_llm_when_retrieval_is_empty():
    client = FakeLLMClient([])

    answer = synthesize("Anything?", [], client)

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.NO_RETRIEVAL
    assert answer.citations == []
    assert client.call_count == 0


def test_refuses_without_calling_the_llm_when_nothing_scores_as_relevant():
    off_topic = [make_chunk(rerank_score=-11.0), make_chunk(rerank_score=-9.4)]
    client = FakeLLMClient([])

    answer = synthesize("Who won the 1998 World Cup?", off_topic, client)

    assert answer.refusal_reason is RefusalReason.BELOW_RELEVANCE_THRESHOLD
    assert client.call_count == 0


def test_one_relevant_chunk_is_enough_to_reach_the_llm():
    mixed = [make_chunk(rerank_score=-11.0), make_chunk(rerank_score=2.0)]
    client = FakeLLMClient(["Yes [2]."])

    answer = synthesize("Question?", mixed, client)

    assert not answer.refused
    assert client.call_count == 1


def test_refuses_when_the_model_declines_for_lack_of_grounding():
    client = FakeLLMClient([REFUSAL_TOKEN])

    answer = synthesize("What is the CEO's salary?", CHUNKS, client)

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.MODEL_DECLINED
    assert REFUSAL_TOKEN not in answer.text
    assert answer.attempts == 1


def test_declining_is_detected_even_when_the_model_wraps_the_token():
    client = FakeLLMClient([f"I'm sorry — {REFUSAL_TOKEN}."])

    assert synthesize("q", CHUNKS, client).refusal_reason is RefusalReason.MODEL_DECLINED


def test_invalid_citation_triggers_one_corrective_retry_then_succeeds():
    client = FakeLLMClient(["Leave is 25 days [7].", "Leave is 25 days [1]."])

    answer = synthesize("How much leave?", CHUNKS, client)

    assert not answer.refused
    assert answer.attempts == 2
    assert [c.marker for c in answer.citations] == [1]
    assert "[7]" in client.last_prompt  # the correction names the bad marker


def test_uncited_answer_triggers_a_retry():
    client = FakeLLMClient(["Employees get 25 days of leave.", "Employees get 25 days [1]."])

    answer = synthesize("How much leave?", CHUNKS, client)

    assert not answer.refused
    assert answer.attempts == 2


def test_refuses_rather_than_passing_through_a_persistently_ungrounded_answer():
    client = FakeLLMClient(["Leave is 40 days [7].", "Leave is 40 days [8]."])

    answer = synthesize("How much leave?", CHUNKS, client)

    assert answer.refused
    assert answer.refusal_reason is RefusalReason.UNGROUNDED
    assert "40 days" not in answer.text
    assert answer.attempts == 2
    assert client.call_count == 2


def test_retry_budget_is_configurable_and_bounded():
    client = FakeLLMClient(["[7]"] * 5)

    answer = synthesize("q", CHUNKS, client, max_attempts=3)

    assert client.call_count == 3
    assert answer.refused


def test_usage_is_accumulated_across_attempts_for_observability():
    client = FakeLLMClient(["Bad [7].", "Good [1]."])

    answer = synthesize("q", CHUNKS, client)

    assert len(answer.usages) == 2
    assert answer.total_usage.total_tokens == 240
    assert answer.total_usage.cost_usd == 0.0002
    assert answer.llm_latency_ms == 25.0
    assert answer.model == "fake/model"


def test_prompt_numbers_every_source_and_carries_the_question():
    messages = build_messages("How much leave?", CHUNKS)
    prompt = messages[-1].content

    assert messages[0].role == "system"
    assert "[1] handbook.md" in prompt
    assert "[2] handbook.md" in prompt
    assert "25 days of annual leave." in prompt
    assert "How much leave?" in prompt


def test_prompt_forbids_outside_knowledge_and_names_the_refusal_token():
    system = build_messages("q", CHUNKS)[0].content

    assert REFUSAL_TOKEN in system
    assert "Answer only from the SOURCES" in system
