"""The `/api/ask` HTTP contract, with the graph and database swapped out.

No LLM and no Postgres: the fake graph never reads the connection, so this
covers the route's own behaviour — response shape, status mapping, validation.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import answer_graph, db
from app.graph import build_answer_graph
from app.llm import LLMError
from app.main import app
from app.synthesis import RefusalReason
from tests.fakes import FAKE_CONN, FakeLLMClient, FakeRetriever, make_chunk

CHUNKS = [make_chunk("handbook-abc123#c0001", text="25 days of annual leave.", rerank_score=8.0)]


class ExplodingClient(FakeLLMClient):
    def complete(self, messages, **kwargs):
        raise LLMError("model 'x/y:free' is not available on this account.")


@pytest.fixture
def client():
    """A `TestClient` whose `/ask` runs on a fake retriever and fake LLM."""

    def build(chunks, replies, llm=None):
        app.dependency_overrides[db] = lambda: FAKE_CONN
        app.dependency_overrides[answer_graph] = lambda: build_answer_graph(
            FakeRetriever(chunks), llm or FakeLLMClient(replies)
        )
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


def test_grounded_answer_carries_citations_and_observability(client):
    response = client(CHUNKS, ["Employees get 25 days [1]."]).post(
        "/api/ask", json={"question": "How much leave?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Employees get 25 days [1]."
    assert body["refused"] is False
    assert body["refusal_reason"] is None

    (citation,) = body["citations"]
    assert citation["marker"] == 1
    assert citation["chunk_id"] == "handbook-abc123#c0001"
    assert citation["label"] == "handbook.md"

    obs = body["observability"]
    assert obs["route"] == "qa"
    assert [s["node"] for s in obs["steps"]] == ["route", "retrieve", "draft", "govern"]
    assert obs["attempts"] == 1
    assert obs["usage"]["total_tokens"] == 120
    assert obs["total_latency_ms"] >= 0
    assert obs["trace_id"]

    # Each node is separately attributable, with its own verdict.
    by_node = {s["node"]: s for s in obs["steps"]}
    assert by_node["retrieve"]["detail"]["chunks"] == 1
    assert by_node["govern"]["detail"]["verdict"] == "accepted"
    assert by_node["draft"]["detail"]["prompt_tokens"] == 100
    assert all(s["duration_ms"] >= 0 for s in obs["steps"])


def test_a_refusal_is_a_200_not_an_error(client):
    """ "The documents don't answer this" is the product working correctly."""
    response = client([], []).post("/api/ask", json={"question": "Anything?"})

    assert response.status_code == 200
    body = response.json()
    assert body["refused"] is True
    assert body["refusal_reason"] == RefusalReason.NO_RETRIEVAL.value
    assert body["citations"] == []


def test_a_corrective_retry_is_visible_in_the_response(client):
    response = client(CHUNKS, ["Leave is 25 days [7].", "Leave is 25 days [1]."]).post(
        "/api/ask", json={"question": "How much leave?"}
    )

    obs = response.json()["observability"]
    assert obs["attempts"] == 2
    verdicts = [s["detail"].get("verdict") for s in obs["steps"] if s["node"] == "govern"]
    assert verdicts == ["correction_requested", "accepted"]


def test_a_broken_provider_is_a_503(client):
    response = client(CHUNKS, [], llm=ExplodingClient([])).post(
        "/api/ask", json={"question": "How much leave?"}
    )

    assert response.status_code == 503
    assert "not available on this account" in response.json()["detail"]


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"question": ""}, "empty"),
        ({"question": "x" * 2001}, "over the length cap"),
        ({}, "missing"),
    ],
)
def test_the_question_is_validated_before_any_work_happens(client, payload, why):
    response = client(CHUNKS, []).post("/api/ask", json=payload)

    assert response.status_code == 422, why
