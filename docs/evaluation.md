# Evaluation Report

> **Generated file — do not edit by hand.** Change `scripts/evaluate.py` instead.

Generated 2026-07-27 11:47 UTC, mode: **live (inclusionai/ling-3.0-flash:free)**, 7/7 golden cases scored.

Regenerate with `uv run python -m scripts.evaluate` (fake LLM, no quota spent) or `uv run python -m scripts.evaluate --live` (real answers — the only mode where faithfulness and cost mean anything). The golden set is 7 cases in `tests/golden.py`, shared with `tests/integration/test_golden_qa.py`. Every rate below is illustrative, not statistically robust — a case-study fixture, not a benchmark.

## Case results

| # | Question | Route | Outcome | Attempts | Best score | Retrieval | Faithful |
|---|---|---|---|---|---|---|---|
| 1 | How many vacation days do full-time emp... | OK | OK | 1 | +8.63 | OK | OK |
| 2 | When is a doctor's note required? | OK | OK | 1 | +6.77 | OK | OK |
| 3 | What is the minimum password length? | OK | OK | 1 | +2.13 | OK | OK |
| 4 | How long do I have to return something ... | OK | OK | 1 | +2.09 | OK | OK |
| 5 | What is the parental leave allowance? | OK | OK | 0 | -9.98 | — | — |
| 6 | Who won the 1998 football World Cup? | OK | OK | 0 | -10.99 | — | — |
| 7 | Summarize the documents | OK | OK | 1 | — | — | — |

## Aggregate metrics

| Metric | Value | What it measures |
|---|---|---|
| Routing accuracy | 100% | qa vs. summarize picked correctly |
| Outcome accuracy | 100% | answered/refused matched the expected outcome |
| Retrieval targeting | 100% | top citation came from the expected document |
| First-attempt grounding | 100% | no corrective retry needed once a draft was requested |
| Faithfulness | 100% | answer text contained the expected fact (this run) |

**Citation validity is not listed as a rate: it is a hard 100% by construction.** Every citation reaching an `Answer` has already been resolved in code against the chunks sent to the model (`app/synthesis/citations.py`) — an answer with an unresolvable marker is refused, never shown with the bad citation stripped. "First-attempt grounding" above is the closer question: how often the model's *first* draft already passed, versus needing governance's one corrective retry.

## Retrieval-leg contribution

Across every `qa`-route citation in this run — which leg actually found the chunk that ended up cited (RRF fusion + rerank sit on top of both):

| Leg | Citations |
|---|---|
| Dense only | 1 |
| Lexical only | 0 |
| Both | 3 |
| **Total** | **4** |

## Latency breakdown

Average wall-clock time per graph node, across every case that visited it (includes real model latency):

| Node | Avg ms | Min ms | Max ms | Visits |
|---|---|---|---|---|
| route | 0.0 | 0.0 | 0.0 | 7 |
| retrieve | 835.0 | 39.4 | 4534.5 | 6 |
| gather_summary_sources | 5.0 | 5.0 | 5.0 | 1 |
| draft | 1282.2 | 817.4 | 1751.3 | 5 |
| govern | 0.2 | 0.1 | 0.4 | 5 |

Average total per question: 1637.7 ms (915.8 ms of that in the LLM call — the rest is retrieval, reranking, and governance).

## Cost summary

5 LLM call(s), 2814 prompt + 714 completion tokens. Total cost: $0.000000.

## MIN_RERANK_SCORE sensitivity

The deployed threshold is `-5.0`. "Correct" here means the gate's own job — pass a question to the model, or refuse before spending a call — matched `refused_before_llm` in `tests/golden.py`, not the final answered/refused outcome (an in-domain-but-uncovered question is supposed to reach the model and be declined *there*, not caught by this gate).

| Threshold | Correct gate decisions |
|---|---|
| -11.0 | 5/6 |
| -10.5 | 6/6 |
| -10.0 | 6/6 |
| -9.0 | 5/6 |
| -8.0 | 5/6 |
| -7.0 | 5/6 |
| -6.0 | 5/6 |
| -5.0 (deployed) | 5/6 |
| -4.0 | 5/6 |
| -3.0 | 5/6 |
| -2.0 | 5/6 |

The deployed threshold is not the best on this fixture — [-10.5, -10.0] would also reach 6/6. With only 6 scored cases this is a lead to investigate, not a reason to move the deployed value on its own.

## Known limitations of this report

- **Seven questions is a demonstration, not a statistically powered eval.** Every rate above should be read as "the pipeline behaved as intended on this fixture", not as a generalizable accuracy figure.
- **Faithfulness is a substring check** (`tests/golden.py`'s `expect_answer_contains`), not entailment. It catches a wrong number; it would not catch a right number attached to unsupported reasoning.
- **Latency and cost reflect a free-tier or lightly-loaded model** — see `docs/technical-decisions.md`'s note on free OpenRouter slugs churning and throttling; a paid model changes both numbers.
