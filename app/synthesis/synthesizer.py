"""Grounded synthesis with a bounded, deterministic governance loop.

`synthesize()` is the whole Phase 3 pipeline as a plain function — Phase 4
wraps its pieces as LangGraph nodes without changing the contract:

    no chunks / nothing relevant   → refuse without calling the LLM
    model says INSUFFICIENT_CONTEXT → refuse, verbatim from the model's own call
    citations fail validation       → one corrective retry, then refuse
    citations validate              → answer + resolved citations

There is no path that returns an uncited or unvalidated answer.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from app.config import settings
from app.llm import LLMClient, Message, TokenUsage
from app.retrieval import RetrievedChunk

from .citations import Citation, describe_problem, normalize_markers, validate_citations
from .prompt import build_correction_message, build_messages, is_refusal

logger = logging.getLogger(__name__)


class RefusalReason(StrEnum):
    NO_RETRIEVAL = "no_retrieval"
    BELOW_RELEVANCE_THRESHOLD = "below_relevance_threshold"
    MODEL_DECLINED = "model_declined"
    UNGROUNDED = "ungrounded"


REFUSAL_MESSAGES: dict[RefusalReason, str] = {
    RefusalReason.NO_RETRIEVAL: (
        "There is nothing in the uploaded documents to answer from. Upload a "
        "document that covers this topic and ask again."
    ),
    RefusalReason.BELOW_RELEVANCE_THRESHOLD: (
        "I could not find anything relevant to this question in the uploaded "
        "documents, so I will not answer it. Try rephrasing, or check that the "
        "document covering it has been uploaded."
    ),
    RefusalReason.MODEL_DECLINED: (
        "The uploaded documents do not contain enough information to answer "
        "this question. I will not answer from general knowledge."
    ),
    RefusalReason.UNGROUNDED: (
        "I drafted an answer but could not verify its citations against the "
        "retrieved passages, so I am not showing it. Please rephrase the "
        "question and try again."
    ),
}


@dataclass(frozen=True)
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    refused: bool = False
    refusal_reason: RefusalReason | None = None
    attempts: int = 0
    usages: list[TokenUsage] = field(default_factory=list)
    model: str | None = None
    llm_latency_ms: float = 0.0

    @property
    def total_usage(self) -> TokenUsage:
        """Usage across every attempt — a retry is not free, so it is counted."""
        costs = [u.cost_usd for u in self.usages if u.cost_usd is not None]
        return TokenUsage(
            prompt_tokens=sum(u.prompt_tokens for u in self.usages),
            completion_tokens=sum(u.completion_tokens for u in self.usages),
            cost_usd=sum(costs) if costs else None,
        )


def _refuse(reason: RefusalReason, **state) -> Answer:
    return Answer(
        text=REFUSAL_MESSAGES[reason],
        refused=True,
        refusal_reason=reason,
        **state,
    )


def synthesize(
    question: str,
    chunks: list[RetrievedChunk],
    client: LLMClient,
    *,
    max_attempts: int | None = None,
    min_rerank_score: float | None = None,
) -> Answer:
    """Answer `question` strictly from `chunks`, or refuse and say why."""
    attempts_allowed = max_attempts if max_attempts is not None else settings.synthesis_max_attempts
    threshold = min_rerank_score if min_rerank_score is not None else settings.min_rerank_score

    if not chunks:
        logger.info("refusing: retrieval returned no chunks")
        return _refuse(RefusalReason.NO_RETRIEVAL)

    best_score = max(chunk.rerank_score for chunk in chunks)
    if best_score < threshold:
        logger.info(
            "refusing: best rerank score %.2f is below threshold %.2f", best_score, threshold
        )
        return _refuse(RefusalReason.BELOW_RELEVANCE_THRESHOLD)

    messages: list[Message] = build_messages(question, chunks)
    usages: list[TokenUsage] = []
    latency_ms = 0.0
    model: str | None = None

    for attempt in range(1, attempts_allowed + 1):
        response = client.complete(messages)
        usages.append(response.usage)
        latency_ms += response.latency_ms
        model = response.model
        state = {
            "attempts": attempt,
            "usages": usages,
            "model": model,
            "llm_latency_ms": latency_ms,
        }

        raw = response.text.strip()
        if is_refusal(raw):
            logger.info("refusing: model declined for lack of grounding")
            return _refuse(RefusalReason.MODEL_DECLINED, **state)

        text = normalize_markers(raw, chunks)
        report = validate_citations(text, chunks)
        problem = describe_problem(report)
        if problem is None:
            return Answer(text=text, citations=report.citations, **state)

        logger.warning("citation validation failed on attempt %d: %s", attempt, problem)
        if attempt < attempts_allowed:
            messages = [
                *messages,
                Message(role="assistant", content=raw),
                build_correction_message(problem, len(chunks)),
            ]

    logger.warning("refusing: citations still invalid after %d attempts", attempts_allowed)
    return _refuse(
        RefusalReason.UNGROUNDED,
        attempts=attempts_allowed,
        usages=usages,
        model=model,
        llm_latency_ms=latency_ms,
    )
