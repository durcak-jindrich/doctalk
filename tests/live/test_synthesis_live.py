"""The one test that spends real OpenRouter quota.

Deselected by default (`addopts = -m 'not live'` in pyproject.toml). Run it
with `uv run pytest -m live` when the OpenRouter integration itself is what
you want to check — after changing LLM_MODEL, the adapter, or the grounding
prompt, and once before a demo. Not part of the normal dev loop.

Deliberately one call with structural assertions only: it proves the adapter,
prompt, and citation validator work against a real model, not that a given
model phrases an answer a given way, so a model swap doesn't break it. The
failure paths a real provider won't produce on demand live offline in
tests/unit/test_openrouter_client.py. No database needed — the retrieved
chunks are constructed directly.
"""

import pytest

from app.config import settings
from app.graph import answer_question, build_answer_graph
from app.llm import LLMError, OpenRouterClient
from tests.fakes import FAKE_CONN, FakeRetriever, make_chunk

pytestmark = pytest.mark.live

CHUNKS = [
    make_chunk(
        "hr-policy-abc123#c0000",
        text="Full-time employees accrue fifteen days of vacation per year.",
        filename="hr-policy.md",
        section_path=["HR Policy", "Vacation"],
        rerank_score=8.0,
    ),
    make_chunk(
        "hr-policy-abc123#c0001",
        text="A doctor's note is required from the fourth consecutive day of absence.",
        filename="hr-policy.md",
        section_path=["HR Policy", "Sick Leave"],
        rerank_score=1.2,
    ),
]


def test_real_model_answers_from_sources_with_a_valid_citation():
    if not settings.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    try:
        client = OpenRouterClient()
    except LLMError as exc:
        pytest.skip(str(exc))

    # The real graph, with retrieval stubbed out — this test is about the
    # provider, prompt, and validator, not about the database.
    graph = build_answer_graph(FakeRetriever(CHUNKS), client)
    answer = answer_question(
        FAKE_CONN, "How many vacation days do full-time employees get?", graph=graph
    )

    assert not answer.refused, f"real model refused an answerable question: {answer.text}"
    assert answer.citations, "an accepted answer must carry at least one citation"
    retrieved_ids = {chunk.chunk_id for chunk in CHUNKS}
    assert all(citation.chunk_id in retrieved_ids for citation in answer.citations)
    assert answer.total_usage.total_tokens > 0, "usage was not captured for observability"
