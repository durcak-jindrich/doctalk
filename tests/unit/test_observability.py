"""Structured logging, trace correlation, and per-node instrumentation."""

import json
import logging

import pytest

from app.graph import answer_question, build_answer_graph
from app.observability import (
    JsonLinesFormatter,
    NodeStep,
    TextFormatter,
    current_trace_id,
    new_trace_id,
    trace,
)
from tests.fakes import FAKE_CONN, FakeLLMClient, FakeRetriever, make_chunk

CHUNKS = [make_chunk("handbook-abc123#c0001", text="25 days of annual leave.", rerank_score=8.0)]


def record(**extra) -> logging.LogRecord:
    rec = logging.LogRecord("app.test", logging.INFO, "f.py", 1, "hello %s", ("world",), None)
    rec.__dict__.update(extra)
    return rec


def test_json_formatter_emits_one_parseable_object_per_line():
    line = JsonLinesFormatter().format(record())
    payload = json.loads(line)

    assert "\n" not in line
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello world"


def test_extra_fields_are_merged_into_the_payload():
    payload = json.loads(JsonLinesFormatter().format(record(event="graph.node", chunks=5)))

    assert payload["event"] == "graph.node"
    assert payload["chunks"] == 5


def test_unserializable_values_do_not_break_a_log_line():
    """A log call must never take down the request it is describing."""
    payload = json.loads(JsonLinesFormatter().format(record(conn=object())))

    assert isinstance(payload["conn"], str)


def test_the_trace_id_lands_on_every_line_inside_the_scope():
    with trace("abc123") as trace_id:
        payload = json.loads(JsonLinesFormatter().format(record()))

    assert trace_id == "abc123"
    assert payload["trace_id"] == "abc123"
    assert json.loads(JsonLinesFormatter().format(record())).get("trace_id") is None


def test_the_trace_id_is_restored_after_a_nested_scope():
    with trace("outer"):
        with trace("inner"):
            assert current_trace_id() == "inner"
        assert current_trace_id() == "outer"
    assert current_trace_id() is None


def test_trace_ids_are_unique():
    assert new_trace_id() != new_trace_id()


def test_text_format_stays_readable_and_names_the_trace():
    with trace("abc123"):
        line = TextFormatter().format(record())

    assert "app.test" in line
    assert "hello world" in line
    assert "[abc123]" in line


@pytest.fixture
def answer():
    graph = build_answer_graph(
        FakeRetriever(CHUNKS), FakeLLMClient(["Bad [7].", "Employees get 25 days [1]."])
    )
    with trace("t-1234"):
        return answer_question(FAKE_CONN, "How much leave?", graph=graph)


def test_every_node_is_timed_and_recorded_in_order(answer):
    assert answer.path == ["route", "retrieve", "draft", "govern", "draft", "govern"]
    assert all(isinstance(step, NodeStep) for step in answer.steps)
    assert all(step.duration_ms >= 0 for step in answer.steps)


def test_each_node_reports_what_it_decided(answer):
    by_node: dict[str, list[NodeStep]] = {}
    for step in answer.steps:
        by_node.setdefault(step.node, []).append(step)

    assert by_node["route"][0].detail["route"] == "qa"
    assert by_node["retrieve"][0].detail == {
        "chunks": 1,
        "best_score": 8.0,
        "threshold": -5.0,
        "verdict": "proceed",
    }
    assert [s.detail["verdict"] for s in by_node["govern"]] == [
        "correction_requested",
        "accepted",
    ]
    assert by_node["draft"][1].detail["attempt"] == 2
    assert by_node["draft"][0].detail["prompt_tokens"] == 100


def test_the_run_carries_its_trace_id_and_total_latency(answer):
    assert answer.trace_id == "t-1234"
    # Wall clock for the whole pipeline, distinct from the summed LLM latency.
    assert answer.total_latency_ms > 0
    assert answer.llm_latency_ms == 25.0


def test_a_pre_llm_refusal_still_records_its_reason():
    graph = build_answer_graph(FakeRetriever([]), FakeLLMClient([]))
    refusal = answer_question(FAKE_CONN, "Anything?", graph=graph)

    (retrieve_step,) = [s for s in refusal.steps if s.node == "retrieve"]
    assert retrieve_step.detail["verdict"] == "refused_no_retrieval"
    assert refusal.total_latency_ms > 0
