"""Phase 9 evaluation: scores the golden Q&A set and writes docs/evaluation.md.

Two modes:
    uv run python -m scripts.evaluate           # ObedientClient, no LLM calls
    uv run python -m scripts.evaluate --live     # real OpenRouter model, ~10 calls

Only the LLM is faked in the default mode — retrieval, reranking, governance,
and the relevance gate all run for real, so routing accuracy, retrieval
targeting, retrieval-leg contribution, latency, and the MIN_RERANK_SCORE
sensitivity table are meaningful either way. Faithfulness and cost are not:
an `ObedientClient` reply cannot demonstrate a model's judgement, and its
cost is fabricated — both are reported only for `--live`. Same distinction
`tests/golden.py`'s module docstring makes, and for the same reason: a
scripted refusal only proves the refusal is plumbed through.

Prerequisites:
    docker compose up -d db          # Postgres/ParadeDB reachable at DATABASE_URL
    OPENROUTER_API_KEY in .env       # --live only

DESTRUCTIVE: resets and re-ingests the fixture workspace (same as the
manual smoke tests).
"""

import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.graph import answer_question, build_answer_graph
from app.llm import LLMClient, LLMError, OpenRouterClient
from app.retrieval import HybridRerankRetriever
from app.storage import get_connection
from app.synthesis import Answer
from tests.golden import GOLDEN, GoldenCase, ObedientClient, ingest_golden_workspace

REPORT_PATH = Path(__file__).resolve().parent.parent / "docs" / "evaluation.md"
# Spans well below the deployed MIN_RERANK_SCORE (-5.0) as well as above it:
# the golden set's off-topic score sits near -11, so a narrower sweep would
# miss whether there is a threshold that separates it from the in-domain
# cases even better than -5.0 does.
CANDIDATE_THRESHOLDS = [-11.0, -10.5, -10.0, -9.0, -8.0, -7.0, -6.0, -5.0, -4.0, -3.0, -2.0]


@dataclass
class CaseResult:
    case: GoldenCase
    answer: Answer
    route_correct: bool
    outcome_correct: bool
    #: None when the case has no expected document (a refusal, or the summary).
    retrieval_correct: bool | None
    #: None unless run with --live and the case names an expected fact.
    faithful: bool | None
    #: The `retrieve` node's best rerank score, or None (summarize route, or
    #: a run that never reached retrieval).
    best_score: float | None


@dataclass
class LegContribution:
    dense_only: int = 0
    lexical_only: int = 0
    both: int = 0

    @property
    def total(self) -> int:
        return self.dense_only + self.lexical_only + self.both


@dataclass
class CostSummary:
    total_usd: float | None
    prompt_tokens: int
    completion_tokens: int
    calls: int


@dataclass
class ThresholdRow:
    threshold: float
    correct: int
    total: int


def _rate(flags: list[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def _best_score(answer: Answer) -> float | None:
    for step in answer.steps:
        if step.node == "retrieve":
            return step.detail.get("best_score")
    return None


def run_case(
    case: GoldenCase, retriever: HybridRerankRetriever, client: LLMClient, *, live: bool
) -> CaseResult:
    """Drive one golden case through the real graph and score the outcome."""
    graph = build_answer_graph(retriever, client)
    with get_connection() as conn:
        answer = answer_question(conn, case.question, graph=graph)

    retrieval_correct = None
    if case.expect_document:
        retrieval_correct = bool(answer.citations) and answer.citations[0].document_id.startswith(
            case.expect_document
        )

    faithful = None
    if live and case.expect_answer_contains and not answer.refused:
        faithful = case.expect_answer_contains.lower() in answer.text.lower()

    return CaseResult(
        case=case,
        answer=answer,
        route_correct=answer.route == case.route,
        outcome_correct=answer.refused == (case.outcome == "refused"),
        retrieval_correct=retrieval_correct,
        faithful=faithful,
        best_score=_best_score(answer),
    )


# --- Aggregate metrics — pure functions over CaseResult, unit-tested directly
# in tests/unit/test_evaluate.py without a database or an LLM. ------------


def routing_accuracy(results: list[CaseResult]) -> float | None:
    return _rate([r.route_correct for r in results])


def outcome_accuracy(results: list[CaseResult]) -> float | None:
    return _rate([r.outcome_correct for r in results])


def retrieval_targeting_accuracy(results: list[CaseResult]) -> float | None:
    return _rate([r.retrieval_correct for r in results if r.retrieval_correct is not None])


def first_attempt_rate(results: list[CaseResult]) -> float | None:
    """Of the cases that reached the model at all, how many needed no
    corrective retry — governance's own signal for how often the model's
    first draft cited something it shouldn't have."""
    drafted = [r.answer.attempts == 1 for r in results if r.answer.attempts >= 1]
    return _rate(drafted)


def faithfulness_rate(results: list[CaseResult]) -> float | None:
    return _rate([r.faithful for r in results if r.faithful is not None])


def leg_contribution(results: list[CaseResult]) -> LegContribution:
    """How often each retrieval leg is the reason a cited chunk was found.

    Only `qa`-routed citations count: the summarize tool selects sources
    structurally (each document's opening), never through either leg, so
    its citations would only dilute this with "neither".
    """
    contribution = LegContribution()
    for r in results:
        if r.case.route != "qa":
            continue
        for citation in r.answer.citations:
            has_dense = citation.dense_rank is not None
            has_lexical = citation.lexical_rank is not None
            if has_dense and has_lexical:
                contribution.both += 1
            elif has_dense:
                contribution.dense_only += 1
            elif has_lexical:
                contribution.lexical_only += 1
    return contribution


def latency_by_node(results: list[CaseResult]) -> dict[str, list[float]]:
    by_node: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for step in r.answer.steps:
            by_node[step.node].append(step.duration_ms)
    return dict(by_node)


def cost_summary(results: list[CaseResult]) -> CostSummary:
    costs: list[float] = []
    prompt = completion = calls = 0
    for r in results:
        for usage in r.answer.usages:
            calls += 1
            prompt += usage.prompt_tokens
            completion += usage.completion_tokens
            if usage.cost_usd is not None:
                costs.append(usage.cost_usd)
    return CostSummary(
        total_usd=sum(costs) if costs else None,
        prompt_tokens=prompt,
        completion_tokens=completion,
        calls=calls,
    )


def threshold_sensitivity(
    results: list[CaseResult], thresholds: list[float] = CANDIDATE_THRESHOLDS
) -> list[ThresholdRow]:
    """For each candidate MIN_RERANK_SCORE, how many cases would the gate
    have routed correctly — "correctly" meaning it matches
    `refused_before_llm`, not `outcome`: a threshold's job is only to let a
    question through to the model or not, and a case like "parental leave"
    (in-domain, but the model must decline) is *supposed* to pass the gate.
    """
    scored = [r for r in results if r.best_score is not None]
    rows = []
    for threshold in thresholds:
        correct = sum(
            1 for r in scored if (r.best_score >= threshold) == (not r.case.refused_before_llm)
        )
        rows.append(ThresholdRow(threshold=threshold, correct=correct, total=len(scored)))
    return rows


# --- Report rendering -------------------------------------------------------


def _pct(rate: float | None) -> str:
    return "N/A" if rate is None else f"{rate * 100:.0f}%"


def _mark(flag: bool | None) -> str:
    return {True: "OK", False: "FAIL", None: "—"}[flag]


def render_report(results: list[CaseResult], *, live: bool, model: str | None) -> str:
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    mode = f"live ({model})" if live else "fake (ObedientClient, no LLM calls)"
    costs = cost_summary(results)
    legs = leg_contribution(results)
    latencies = latency_by_node(results)

    lines = [
        "# Evaluation Report",
        "",
        f"Generated {generated} by `scripts/evaluate.py`, mode: **{mode}**, "
        f"{len(results)}/{len(GOLDEN)} golden cases scored.",
        "",
        "Regenerate with `uv run python -m scripts.evaluate` (no quota spent) or "
        "`uv run python -m scripts.evaluate --live` (real answers — see caveat below). "
        "The golden set is 7 questions (`tests/golden.py`), shared with "
        "`tests/integration/test_golden_qa.py`; treat every rate below as illustrative, "
        "not statistically robust — the sample is a case study fixture, not a benchmark.",
        "",
    ]

    if not live:
        lines += [
            "> **Fake-mode run.** Retrieval, reranking, the relevance gate, and citation "
            "governance are all real; the LLM is not. Faithfulness and cost below are "
            "N/A/fabricated on purpose — `ObedientClient` always cites `[1]` and never "
            "contains a golden case's expected fact, so scoring it would only prove the "
            "script matches itself. Run with `--live` for a report that means something "
            "about the model.",
            "",
        ]

    lines += [
        "## Case results",
        "",
        "| # | Question | Route | Outcome | Attempts | Best score | Retrieval | Faithful |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, start=1):
        q = r.case.question if len(r.case.question) <= 42 else r.case.question[:39] + "..."
        best = f"{r.best_score:+.2f}" if r.best_score is not None else "—"
        lines.append(
            f"| {i} | {q} | {_mark(r.route_correct)} | {_mark(r.outcome_correct)} "
            f"| {r.answer.attempts} | {best} | {_mark(r.retrieval_correct)} "
            f"| {_mark(r.faithful)} |"
        )

    lines += [
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value | What it measures |",
        "|---|---|---|",
        f"| Routing accuracy | {_pct(routing_accuracy(results))} "
        "| qa vs. summarize picked correctly |",
        f"| Outcome accuracy | {_pct(outcome_accuracy(results))} "
        "| answered/refused matched the expected outcome |",
        f"| Retrieval targeting | {_pct(retrieval_targeting_accuracy(results))} "
        "| top citation came from the expected document |",
        f"| First-attempt grounding | {_pct(first_attempt_rate(results))} "
        "| no corrective retry needed once a draft was requested |",
        f"| Faithfulness | {_pct(faithfulness_rate(results))} "
        f"| answer text contained the expected fact ({'this run' if live else 'live only'}) |",
        "",
        "**Citation validity is not listed as a rate: it is a hard 100% by construction.** "
        "Every citation reaching an `Answer` has already been resolved in code against the "
        "chunks sent to the model (`app/synthesis/citations.py`) — an answer with an "
        "unresolvable marker is refused, never shown with the bad citation stripped. "
        '"First-attempt grounding" above is the closer question: how often the model\'s '
        "*first* draft already passed, versus needing governance's one corrective retry.",
        "",
        "## Retrieval-leg contribution",
        "",
        "Across every `qa`-route citation in this run — which leg actually found the chunk "
        "that ended up cited (RRF fusion + rerank sit on top of both):",
        "",
        "| Leg | Citations |",
        "|---|---|",
        f"| Dense only | {legs.dense_only} |",
        f"| Lexical only | {legs.lexical_only} |",
        f"| Both | {legs.both} |",
        f"| **Total** | **{legs.total}** |",
        "",
        "## Latency breakdown",
        "",
        "Average wall-clock time per graph node, across every case that visited it "
        + (
            "(includes real model latency):"
            if live
            else "(draft/govern are near-instant — the LLM is faked):"
        ),
        "",
        "| Node | Avg ms | Min ms | Max ms | Visits |",
        "|---|---|---|---|---|",
    ]
    for node in ("route", "retrieve", "gather_summary_sources", "draft", "govern"):
        durations = latencies.get(node, [])
        if not durations:
            continue
        lines.append(
            f"| {node} | {sum(durations) / len(durations):.1f} | {min(durations):.1f} "
            f"| {max(durations):.1f} | {len(durations)} |"
        )
    total_latencies = [r.answer.total_latency_ms for r in results]
    llm_latencies = [r.answer.llm_latency_ms for r in results]
    lines += [
        "",
        f"Average total per question: {sum(total_latencies) / len(total_latencies):.1f} ms "
        f"({sum(llm_latencies) / len(llm_latencies):.1f} ms of that in the LLM call — the "
        "rest is retrieval, reranking, and governance).",
        "",
        "## Cost summary",
        "",
        f"{costs.calls} LLM call(s), {costs.prompt_tokens} prompt + {costs.completion_tokens} "
        "completion tokens. Total cost: "
        + (f"${costs.total_usd:.6f}" if costs.total_usd is not None else "N/A (fake mode)")
        + ".",
        "",
        "## MIN_RERANK_SCORE sensitivity",
        "",
        f'The deployed threshold is `{settings.min_rerank_score}`. "Correct" here means the '
        "gate's own job — pass a question to the model, or refuse before spending a call — "
        "matched `refused_before_llm` in `tests/golden.py`, not the final answered/refused "
        "outcome (an in-domain-but-uncovered question is supposed to reach the model and be "
        "declined *there*, not caught by this gate).",
        "",
        "| Threshold | Correct gate decisions |",
        "|---|---|",
    ]
    rows = threshold_sensitivity(results)
    for row in rows:
        marker = " (deployed)" if row.threshold == settings.min_rerank_score else ""
        lines.append(f"| {row.threshold:+.1f}{marker} | {row.correct}/{row.total} |")

    deployed_row = next((r for r in rows if r.threshold == settings.min_rerank_score), None)
    best = max((r.correct for r in rows), default=0)
    if deployed_row is not None and rows:
        if deployed_row.correct == best == deployed_row.total:
            note = "matches the best score achievable on this fixture — no change indicated."
        elif deployed_row.correct == best:
            note = (
                "ties the best score achievable on this fixture; other thresholds reach the "
                "same rate without doing better."
            )
        else:
            better = sorted({r.threshold for r in rows if r.correct == best})
            note = (
                f"is not the best on this fixture — {better} would also reach {best}/"
                f"{deployed_row.total}. With only {deployed_row.total} scored cases this is "
                "a lead to investigate, not a reason to move the deployed value on its own."
            )
        lines.append(f"\nThe deployed threshold {note}")

    lines += [
        "",
        "## Known limitations of this report",
        "",
        "- **Seven questions is a demonstration, not a statistically powered eval.** Every "
        'rate above should be read as "the pipeline behaved as intended on this fixture", '
        "not as a generalizable accuracy figure.",
        "- **Faithfulness is a substring check** (`tests/golden.py`'s `expect_answer_contains`), "
        "not entailment. It catches a wrong number; it would not catch a right number "
        "attached to unsupported reasoning.",
        "- **Latency and cost reflect a free-tier or lightly-loaded model** — see "
        "`docs/technical-decisions.md`'s note on free OpenRouter slugs churning and "
        "throttling; a paid model changes both numbers.",
        "",
    ]
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------


def _print_case(r: CaseResult) -> None:
    verdict = f"REFUSED ({r.answer.refusal_reason})" if r.answer.refused else "ANSWERED"
    print(
        f"  [{_mark(r.outcome_correct)}] {r.case.question!r} -> {verdict}, "
        f"route={r.answer.route} ({_mark(r.route_correct)}), attempts={r.answer.attempts}"
    )


def main() -> None:
    live = "--live" in sys.argv
    retriever = HybridRerankRetriever()

    print("Resetting schema and ingesting the golden workspace...")
    ingest_golden_workspace()

    client: LLMClient | None = None
    if live:
        client = OpenRouterClient()
        print(f"Live model: {settings.llm_model}\n")
    else:
        print("Fake mode (ObedientClient) — no LLM calls, no quota spent.\n")

    results: list[CaseResult] = []
    for case in GOLDEN:
        case_client = client if live else ObedientClient(decline=case.outcome == "refused")
        try:
            result = run_case(case, retriever, case_client, live=live)
        except LLMError as exc:
            print(f"  ERROR on {case.question!r}: {exc}")
            continue
        results.append(result)
        _print_case(result)

    if not results:
        print("\nNo cases completed — nothing to report.")
        sys.exit(1)

    report = render_report(results, live=live, model=settings.llm_model if live else None)
    REPORT_PATH.write_text(report)
    print(f"\nWrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
