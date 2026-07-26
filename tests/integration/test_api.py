"""The document API against a real Postgres, and `/ask` end to end over it.

Real parsing, chunking, embedding and SQL; the only stand-in is the LLM, so
no quota is spent. Skipped when Postgres is unreachable (see `conftest.py`).
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import answer_graph
from app.config import settings
from app.graph import build_answer_graph
from app.main import app
from app.retrieval import HybridRerankRetriever
from tests.fakes import FakeLLMClient

HR_POLICY = b"""# HR Policy

## Vacation

Full-time employees accrue fifteen days of vacation per year.

## Sick Leave

A doctor's note is required from the fourth consecutive day of absence.
"""

TRAVEL = b"""# Travel

## Booking

Flights must be booked at least fourteen days before departure.
"""


@pytest.fixture
def client(clean_schema):
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def with_fake_llm(replies: list[str]) -> None:
    app.dependency_overrides[answer_graph] = lambda: build_answer_graph(
        HybridRerankRetriever(), FakeLLMClient(replies)
    )


def upload(client: TestClient, *files: tuple[str, bytes]):
    return client.post(
        "/api/documents",
        files=[("files", (name, content, "application/octet-stream")) for name, content in files],
    )


def test_upload_ingests_and_reports_remaining_capacity(client):
    response = upload(client, ("hr-policy.md", HR_POLICY))

    assert response.status_code == 200
    body = response.json()
    (result,) = body["results"]
    assert result["status"] == "ingested"
    assert result["chunk_count"] > 0

    workspace = body["workspace"]
    assert workspace["used"] == 1
    assert workspace["capacity"] == settings.max_documents
    assert workspace["remaining"] == settings.max_documents - 1
    assert workspace["documents"][0]["filename"] == "hr-policy.md"
    assert workspace["documents"][0]["chunk_count"] == result["chunk_count"]


def test_one_bad_file_does_not_sink_the_rest_of_the_batch(client):
    response = upload(
        client,
        ("hr-policy.md", HR_POLICY),
        ("notes.txt", b"unsupported extension"),
        ("travel.md", TRAVEL),
    )

    statuses = {r["filename"]: r["status"] for r in response.json()["results"]}
    assert statuses == {
        "hr-policy.md": "ingested",
        "notes.txt": "rejected",
        "travel.md": "ingested",
    }
    assert response.json()["workspace"]["used"] == 2

    rejected = next(r for r in response.json()["results"] if r["status"] == "rejected")
    assert "Supported: PDF, DOCX, MD" in rejected["error"]


def test_re_uploading_identical_bytes_is_a_no_op(client):
    upload(client, ("hr-policy.md", HR_POLICY))
    response = upload(client, ("hr-policy.md", HR_POLICY))

    assert response.json()["results"][0]["status"] == "duplicate"
    assert response.json()["workspace"]["used"] == 1


def test_an_empty_file_is_rejected_with_a_readable_reason(client):
    response = upload(client, ("empty.md", b""))

    (result,) = response.json()["results"]
    assert result["status"] == "rejected"
    assert "empty" in result["error"].lower()


def test_a_file_over_the_size_limit_is_rejected_before_it_is_parsed(client, monkeypatch):
    """The ceiling is a memory bound — parsing loads the whole file at once."""
    monkeypatch.setattr(settings, "max_upload_bytes", 512)

    response = upload(client, ("big.md", b"# Big\n\n" + b"x" * 600))

    (result,) = response.json()["results"]
    assert result["status"] == "rejected"
    assert "limit" in result["error"].lower()
    assert response.json()["workspace"]["used"] == 0


def test_a_file_within_the_size_limit_is_still_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 512)

    response = upload(client, ("small.md", b"# Small\n\nWell under the limit."))

    assert response.json()["results"][0]["status"] == "ingested"


def test_the_workspace_cap_is_enforced_at_the_api(client):
    files = [(f"doc-{i}.md", f"# Doc {i}\n\nContent number {i}.".encode()) for i in range(6)]
    response = upload(client, *files)

    results = response.json()["results"]
    assert sum(r["status"] == "ingested" for r in results) == settings.max_documents
    rejected = [r for r in results if r["status"] == "rejected"]
    assert len(rejected) == 1
    assert "full" in rejected[0]["error"].lower()
    assert response.json()["workspace"]["remaining"] == 0


def test_listing_reading_and_deleting_a_document(client):
    document_id = upload(client, ("hr-policy.md", HR_POLICY)).json()["results"][0]["document_id"]

    listed = client.get("/api/documents").json()
    assert [d["id"] for d in listed["documents"]] == [document_id]

    detail = client.get(f"/api/documents/{document_id}").json()
    assert detail["filename"] == "hr-policy.md"
    assert len(detail["chunks"]) == detail["chunk_count"]
    assert "fifteen days of vacation" in " ".join(c["text"] for c in detail["chunks"])

    deleted = client.delete(f"/api/documents/{document_id}")
    assert deleted.status_code == 200
    assert deleted.json()["used"] == 0
    # Chunks cascade, so the freed slot is genuinely free.
    assert client.get("/api/documents").json()["documents"] == []


def test_unknown_document_ids_are_404s(client):
    assert client.get("/api/documents/nope").status_code == 404
    assert client.delete("/api/documents/nope").status_code == 404


def test_ask_answers_from_a_real_uploaded_document(client):
    upload(client, ("hr-policy.md", HR_POLICY))
    with_fake_llm(["Full-time employees accrue fifteen days [1]."])

    body = client.post("/api/ask", json={"question": "How many vacation days?"}).json()

    assert body["refused"] is False
    (citation,) = body["citations"]
    # The citation must resolve to a chunk that really exists in Postgres.
    document_id = citation["chunk_id"].split("#")[0]
    chunk_ids = {c["id"] for c in client.get(f"/api/documents/{document_id}").json()["chunks"]}
    assert citation["chunk_id"] in chunk_ids
    # Both legs, not `or`: either one alone would pass that and hide a
    # retriever that had quietly stopped being hybrid.
    assert citation["dense_rank"] is not None
    assert citation["lexical_rank"] is not None


def test_ask_refuses_when_the_workspace_is_empty(client):
    with_fake_llm([])

    body = client.post("/api/ask", json={"question": "How many vacation days?"}).json()

    assert body["refused"] is True
    assert body["citations"] == []
    assert body["observability"]["attempts"] == 0


def test_a_summary_request_routes_to_the_summarize_tool(client):
    upload(client, ("hr-policy.md", HR_POLICY), ("travel.md", TRAVEL))
    with_fake_llm(["HR covers leave [1]. Travel covers booking [3]."])

    body = client.post("/api/ask", json={"question": "Summarize the documents"}).json()

    assert body["observability"]["route"] == "summarize"
    assert "retrieve" not in body["observability"]["steps"]
    assert {c["document_id"] for c in body["citations"]} == {
        d["id"] for d in client.get("/api/documents").json()["documents"]
    }
