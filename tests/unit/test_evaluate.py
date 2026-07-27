"""Pure scoring functions behind the Phase 9 evaluation, in isolation.

`scripts/evaluate.py` drives the real graph to build `CaseResult`s; none of
that is needed here — every function under test only reads fields off
constructed `Answer`/`GoldenCase` objects, so these run with no database and
no LLM, like the rest of the fast suite.
"""

from app.llm import TokenUsage
from app.observability import NodeStep
from app.synthesis import Answer, Citation
from scripts.evaluate import (
    CaseResult,
    cost_summary,
    faithfulness_rate,
    first_attempt_rate,
    latency_by_node,
    leg_contribution,
    outcome_accuracy,
    retrieval_targeting_accuracy,
    routing_accuracy,
    threshold_sensitivity,
)
from tests.golden import GoldenCase


def _case(route="qa", refused_before_llm=False, **kwargs) -> GoldenCase:
    return GoldenCase(
        question="q",
        route=route,
        outcome="answered",
        expect_document=None,
        refused_before_llm=refused_before_llm,
        why="",
        **kwargs,
    )


def _citation(*, dense_rank=None, lexical_rank=None) -> Citation:
    return Citation(
        marker=1,
        chunk_id="doc#c0001",
        document_id="doc",
        filename="doc.md",
        label="doc.md",
        text="text",
        section_path=None,
        page_number=None,
        rerank_score=1.0,
        dense_rank=dense_rank,
        lexical_rank=lexical_rank,
    )


def _result(
    *,
    route="qa",
    route_correct=True,
    outcome_correct=True,
    retrieval_correct=None,
    faithful=None,
    best_score=None,
    attempts=1,
    citations=(),
    steps=(),
    usages=(),
) -> CaseResult:
    answer = Answer(
        text="answer",
        citations=list(citations),
        route=route,
        attempts=attempts,
        steps=list(steps),
        usages=list(usages),
    )
    return CaseResult(
        case=_case(route=route),
        answer=answer,
        route_correct=route_correct,
        outcome_correct=outcome_correct,
        retrieval_correct=retrieval_correct,
        faithful=faithful,
        best_score=best_score,
    )


def test_routing_and_outcome_accuracy_average_the_flags():
    results = [
        _result(route_correct=True, outcome_correct=True),
        _result(route_correct=True, outcome_correct=False),
        _result(route_correct=False, outcome_correct=True),
        _result(route_correct=True, outcome_correct=True),
    ]
    assert routing_accuracy(results) == 0.75
    assert outcome_accuracy(results) == 0.75


def test_routing_accuracy_is_none_for_an_empty_run():
    assert routing_accuracy([]) is None


def test_retrieval_targeting_accuracy_ignores_not_applicable_cases():
    results = [
        _result(retrieval_correct=True),
        _result(retrieval_correct=False),
        _result(retrieval_correct=None),  # a refusal or the summary case
    ]
    assert retrieval_targeting_accuracy(results) == 0.5


def test_first_attempt_rate_excludes_gate_refusals_that_never_drafted():
    results = [
        _result(attempts=1),  # drafted once, accepted
        _result(attempts=2),  # needed a corrective retry
        _result(attempts=0),  # refused by the relevance gate, no draft at all
    ]
    assert first_attempt_rate(results) == 0.5


def test_first_attempt_rate_is_none_when_nothing_ever_drafted():
    assert first_attempt_rate([_result(attempts=0)]) is None


def test_faithfulness_rate_only_counts_scored_cases():
    results = [_result(faithful=True), _result(faithful=False), _result(faithful=None)]
    assert faithfulness_rate(results) == 0.5


def test_leg_contribution_categorizes_by_which_leg_ranked_the_chunk():
    results = [
        _result(citations=[_citation(dense_rank=1, lexical_rank=None)]),
        _result(citations=[_citation(dense_rank=None, lexical_rank=2)]),
        _result(citations=[_citation(dense_rank=1, lexical_rank=1)]),
        _result(citations=[_citation(dense_rank=3, lexical_rank=None)]),
    ]
    contribution = leg_contribution(results)
    assert contribution.dense_only == 2
    assert contribution.lexical_only == 1
    assert contribution.both == 1
    assert contribution.total == 4


def test_leg_contribution_ignores_summarize_route_citations():
    """The summarize tool selects sources structurally, never through either
    leg — its citations would only dilute the retrieval signal."""
    results = [
        _result(route="summarize", citations=[_citation(dense_rank=None, lexical_rank=None)]),
        _result(route="qa", citations=[_citation(dense_rank=1, lexical_rank=1)]),
    ]
    contribution = leg_contribution(results)
    assert contribution.total == 1
    assert contribution.both == 1


def test_latency_by_node_groups_durations_across_cases():
    results = [
        _result(steps=[NodeStep(node="retrieve", duration_ms=10.0)]),
        _result(
            steps=[
                NodeStep(node="retrieve", duration_ms=20.0),
                NodeStep(node="draft", duration_ms=5.0),
            ]
        ),
    ]
    by_node = latency_by_node(results)
    assert by_node["retrieve"] == [10.0, 20.0]
    assert by_node["draft"] == [5.0]


def test_cost_summary_sums_usage_across_every_call_including_retries():
    results = [
        _result(
            usages=[
                TokenUsage(prompt_tokens=100, completion_tokens=20, cost_usd=0.001),
                TokenUsage(prompt_tokens=100, completion_tokens=20, cost_usd=0.001),
            ]
        ),
        _result(usages=[TokenUsage(prompt_tokens=50, completion_tokens=10, cost_usd=None)]),
    ]
    summary = cost_summary(results)
    assert summary.calls == 3
    assert summary.prompt_tokens == 250
    assert summary.completion_tokens == 50
    assert summary.total_usd == 0.002


def test_cost_summary_reports_none_when_no_usage_carries_a_cost():
    results = [_result(usages=[TokenUsage(prompt_tokens=10, completion_tokens=5, cost_usd=None)])]
    assert cost_summary(results).total_usd is None


def test_threshold_sensitivity_scores_the_gate_decision_not_the_outcome():
    """A case where `refused_before_llm=True` wants best_score < threshold;
    `refused_before_llm=False` wants best_score >= threshold."""
    off_topic = CaseResult(
        case=_case(refused_before_llm=True),
        answer=Answer(text=""),
        route_correct=True,
        outcome_correct=True,
        retrieval_correct=None,
        faithful=None,
        best_score=-11.0,
    )
    in_domain = CaseResult(
        case=_case(refused_before_llm=False),
        answer=Answer(text=""),
        route_correct=True,
        outcome_correct=True,
        retrieval_correct=None,
        faithful=None,
        best_score=-10.0,
    )
    results = [off_topic, in_domain]

    rows = threshold_sensitivity(results, thresholds=[-12.0, -10.5, -5.0])
    by_threshold = {row.threshold: row.correct for row in rows}

    # -12.0: both pass the gate -> off_topic wrong, in_domain right -> 1/2
    assert by_threshold[-12.0] == 1
    # -10.5: off_topic refused, in_domain passes -> both right -> 2/2
    assert by_threshold[-10.5] == 2
    # -5.0: both refused -> off_topic right, in_domain wrong -> 1/2
    assert by_threshold[-5.0] == 1


def test_threshold_sensitivity_skips_cases_with_no_retrieval_score():
    """The summarize route never sets best_score — it must not be counted."""
    results = [_result(best_score=None)]
    rows = threshold_sensitivity(results, thresholds=[-5.0])
    assert rows[0].total == 0
    assert rows[0].correct == 0
