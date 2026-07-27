"""The golden set: a fixture workspace plus questions with known outcomes.

Shared rather than inlined in one test, because the Phase 9 evaluation scores
the same cases it asserts on — one definition of "right answer" for both.

What a fake-LLM run over this set genuinely proves is retrieval and
governance: which document a question reaches, whether an off-topic question
is refused *before* a model is ever called, and whether every citation
resolves. What it cannot prove is the model's judgement — a scripted refusal
only shows the refusal is plumbed through. That judgement is what the
`live`-marked end-to-end test and the `--live` evaluation run
(`scripts/evaluate.py`) exist to check — which is also why
`expect_answer_contains` only means anything against a real model: an
`ObedientClient` answer never contains the fact, on purpose.
"""

from dataclasses import dataclass
from typing import Literal

from app.llm import LLMClient, LLMResponse, Message, TokenUsage
from app.storage import ingest_document, reset_schema
from app.synthesis import REFUSAL_TOKEN

DOCUMENTS: dict[str, bytes] = {
    "hr-policy.md": b"""# HR Policy

## Vacation

Full-time employees accrue fifteen days of paid vacation per calendar year.
Requests must reach a line manager at least two weeks in advance.

## Sick Leave

Employees may take up to ten days of paid sick leave per year. A doctor's note
is required from the fourth consecutive day of absence.
""",
    "it-security.md": b"""# IT Security Standard

## Passwords

Passwords must be at least twelve characters long and rotated every ninety
days. Reuse across internal systems is prohibited.

## Devices

Company laptops require full-disk encryption before issue. Report a lost or
stolen device to the helpdesk within twenty-four hours.
""",
    "product-faq.md": b"""# Product FAQ

## Refunds

Customers may request a refund within thirty days of purchase, provided the
product is unused and in its original packaging.

## Shipping

Standard delivery takes three to five business days.
""",
}


@dataclass(frozen=True)
class GoldenCase:
    question: str
    #: Which tool should handle it.
    route: Literal["qa", "summarize"]
    #: Whether DocTalk should answer at all.
    outcome: Literal["answered", "refused"]
    #: Document the top citation must come from, for answerable questions.
    expect_document: str | None
    #: True when the question is so far off-topic that the relevance gate
    #: should refuse it without spending an LLM call.
    refused_before_llm: bool
    why: str
    #: A short fact a correct grounded answer must contain (checked
    #: case-insensitively). The Phase 9 evaluation's faithfulness signal —
    #: `None` for refused cases and the summary, where no single fact applies.
    expect_answer_contains: str | None = None


GOLDEN: list[GoldenCase] = [
    GoldenCase(
        "How many vacation days do full-time employees get?",
        "qa",
        "answered",
        "hr-policy",
        False,
        "directly stated in the HR policy",
        expect_answer_contains="fifteen",
    ),
    GoldenCase(
        "When is a doctor's note required?",
        "qa",
        "answered",
        "hr-policy",
        False,
        "same document, a different section — tests within-document targeting",
        expect_answer_contains="fourth",
    ),
    GoldenCase(
        "What is the minimum password length?",
        "qa",
        "answered",
        "it-security",
        False,
        "exact term match, the lexical leg's home ground",
        expect_answer_contains="twelve",
    ),
    GoldenCase(
        "How long do I have to return something I bought?",
        "qa",
        "answered",
        "product-faq",
        False,
        "paraphrased with no term overlap — the dense leg has to carry it",
        expect_answer_contains="thirty",
    ),
    GoldenCase(
        "What is the parental leave allowance?",
        "qa",
        "refused",
        None,
        False,
        "plausible for an HR document and absent from it — the fabrication trap",
    ),
    GoldenCase(
        "Who won the 1998 football World Cup?",
        "qa",
        "refused",
        None,
        True,
        "off-topic: the relevance gate should stop it before any LLM call",
    ),
    GoldenCase(
        "Summarize the documents",
        "summarize",
        "answered",
        None,
        False,
        "whole-workspace request routes to the summarize tool",
    ),
]


def ingest_golden_workspace() -> None:
    """Reset the schema and ingest the fixture documents.

    Shared by `tests/integration/test_golden_qa.py` and `scripts/evaluate.py`
    so there is one definition of how the golden workspace is built, not two
    that could drift.
    """
    reset_schema()
    for filename, content in DOCUMENTS.items():
        ingest_document(filename, content)


class ObedientClient(LLMClient):
    """Always answers, always citing source [1] — or declines when told to.

    Stands in for a perfectly-behaved model so retrieval and governance are
    the only things that can fail a case. Deliberately does not attempt to
    contain any golden case's `expect_answer_contains` fact: faithfulness is
    a claim about a real model's judgement, and scoring a scripted reply
    against it would only prove the script was written to pass.
    """

    model = "fake/obedient"

    def __init__(self, *, decline: bool = False):
        self.decline = decline
        self.call_count = 0

    def complete(
        self, messages: list[Message], *, temperature=None, max_tokens=None
    ) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            text=REFUSAL_TOKEN if self.decline else "According to the sources, yes [1].",
            model=self.model,
            usage=TokenUsage(prompt_tokens=500, completion_tokens=25, cost_usd=0.00004),
            latency_ms=400.0,
        )
