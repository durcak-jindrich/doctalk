"""The golden set: a fixture workspace plus questions with known outcomes.

Shared rather than inlined in one test, because the Phase 9 evaluation scores
the same cases it asserts on — one definition of "right answer" for both.

What a fake-LLM run over this set genuinely proves is retrieval and
governance: which document a question reaches, whether an off-topic question
is refused *before* a model is ever called, and whether every citation
resolves. What it cannot prove is the model's judgement — a scripted refusal
only shows the refusal is plumbed through. That judgement is what the
`live`-marked end-to-end test exists to check.
"""

from dataclasses import dataclass
from typing import Literal

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


GOLDEN: list[GoldenCase] = [
    GoldenCase(
        "How many vacation days do full-time employees get?",
        "qa",
        "answered",
        "hr-policy",
        False,
        "directly stated in the HR policy",
    ),
    GoldenCase(
        "When is a doctor's note required?",
        "qa",
        "answered",
        "hr-policy",
        False,
        "same document, a different section — tests within-document targeting",
    ),
    GoldenCase(
        "What is the minimum password length?",
        "qa",
        "answered",
        "it-security",
        False,
        "exact term match, the lexical leg's home ground",
    ),
    GoldenCase(
        "How long do I have to return something I bought?",
        "qa",
        "answered",
        "product-faq",
        False,
        "paraphrased with no term overlap — the dense leg has to carry it",
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
