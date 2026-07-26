"""The result of an `/ask` run, and the refusal policy behind it.

DocTalk refuses in four situations, and each one names itself so the UI and
the eval script can tell them apart:

    no chunks retrieved             → NO_RETRIEVAL, no LLM call
    nothing scored as relevant      → BELOW_RELEVANCE_THRESHOLD, no LLM call
    model said INSUFFICIENT_CONTEXT → MODEL_DECLINED
    citations never validated       → UNGROUNDED, after the retry budget

The control flow that reaches these outcomes lives in `app/graph/` — this
module holds only the vocabulary, so there is exactly one definition of what
a refusal is. There is no path that returns an uncited, unvalidated answer.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from app.llm import TokenUsage

from .citations import Citation

#: Which pipeline answered: relevance-ranked retrieval, or the summarize tool.
Route = Literal["qa", "summarize"]


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
    route: Route = "qa"
    # Graph nodes visited, in order — the audit trail of how this answer was
    # reached, including any corrective retry. Timings land here in Phase 6.
    steps: list[str] = field(default_factory=list)
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


def refuse(reason: RefusalReason, **state) -> Answer:
    """Build the refusal for `reason`, carrying whatever run state exists."""
    return Answer(
        text=REFUSAL_MESSAGES[reason],
        refused=True,
        refusal_reason=reason,
        **state,
    )
