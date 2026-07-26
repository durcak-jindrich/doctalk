"""The grounding contract: what the model is actually told.

The pipeline that uses these prompts is tested in `test_graph.py`.
"""

from app.synthesis import REFUSAL_TOKEN, build_messages, build_summary_messages
from tests.fakes import make_chunk

CHUNKS = [
    make_chunk("handbook-abc123#c0001", text="25 days of annual leave.", rerank_score=8.0),
    make_chunk("handbook-abc123#c0007", text="A doctor's note is needed after 3 days."),
]


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


def test_summary_prompt_reuses_the_one_grounding_contract():
    """Both routes share a system prompt, so the citation rules cannot drift."""
    assert build_summary_messages("Summarize", CHUNKS)[0] == build_messages("q", CHUNKS)[0]


def test_summary_prompt_numbers_sources_and_asks_for_citations():
    prompt = build_summary_messages("Summarize the documents", CHUNKS)[-1].content

    assert "[1] handbook.md" in prompt
    assert "[2] handbook.md" in prompt
    assert "[n] markers" in prompt


def test_summary_prompt_states_that_the_sources_are_partial():
    """The tool sees document openings only — the summary must not imply more."""
    prompt = build_summary_messages("Summarize", CHUNKS)[-1].content

    assert "not the whole of them" in prompt
