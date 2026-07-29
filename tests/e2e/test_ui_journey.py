"""The demo journey, in a browser: clear → upload all three formats → ask.

Everything below the browser is real — parsing, chunking, embedding, hybrid
retrieval, reranking, citation governance and Postgres. Only the model is
swapped, and only in the default mode.

What each mode proves:

* **fake** (default, no quota) — that the *plumbing* holds: each parser
  reaches the UI, a question lands on the right document, citations resolve to
  passages the browser can open, an off-topic question is refused before any
  model is called, and the trace renders. The answer text is scripted, so it
  is never asserted on.
* **live** (`-m live`) — the same journey against a real provider, where the
  answer must actually contain the fact and the model itself must decline the
  fabrication trap. ~5 LLM calls.

DESTRUCTIVE: the journey starts by deleting every document in the local
workspace, through the UI, because that is also the delete path under test.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

SAMPLES = Path(__file__).resolve().parents[2] / "samples"

#: One per supported format, so all three parsers are exercised end to end.
CORPUS = [
    SAMPLES / "hr-policy.md",
    SAMPLES / "onboarding-guide.docx",
    SAMPLES / "data-retention-policy.pdf",
]


@dataclass(frozen=True)
class Probe:
    question: str
    #: The document the top citation must name — "did it reach the right file".
    document: str
    #: Substring the citation label must carry, which is format-specific:
    #: headings become a `>` path, PDF pages become "(page n)".
    provenance: str
    #: Asserted only against a real model — see `tests/golden.py` on why a
    #: scripted reply must not be scored for faithfulness.
    fact: str


ANSWERABLE = [
    Probe(
        "How many vacation days do full-time employees get?",
        document="hr-policy.md",
        provenance="hr-policy.md > HR Policy > Vacation",
        fact="fifteen",
    ),
    Probe(
        "How long are the onboarding buddy's check-ins with a new joiner?",
        document="onboarding-guide.docx",
        provenance="onboarding-guide.docx > New Joiner Onboarding Guide",
        fact="thirty",
    ),
    Probe(
        "How long are application logs kept?",
        document="data-retention-policy.pdf",
        provenance="(page ",
        fact="sixty",
    ),
]

#: Off-topic enough that the relevance gate refuses it before an LLM call —
#: which is why it is deterministic with a fake model too.
OFF_TOPIC = "Who won the 1998 football World Cup?"

#: Plausible for an HR document and absent from it. Only a real model can
#: refuse this one; a scripted "obedient" client would answer it.
FABRICATION_TRAP = "What is the parental leave allowance for new parents?"

#: Ingesting the PDF embeds every chunk on CPU. Generous, but bounded: this
#: never waits on a network provider. Answers use `app_server.answer_timeout_ms`.
INGEST_MS = 120_000


def clear_workspace(page: Page) -> None:
    """Empty the workspace through the UI, one confirmed delete at a time."""
    # The slot indicator is an em dash until the first workspace fetch lands.
    expect(page.locator("#slots")).to_contain_text("/")

    documents = page.locator("#doclist .doc")
    while (remaining := documents.count()) > 0:
        documents.first.locator(".doc__delete").click()
        expect(documents).to_have_count(remaining - 1)

    expect(documents).to_have_count(0)
    expect(page.locator("#slots")).to_have_text("0 / 5")
    expect(page.locator(".empty")).to_be_visible()


def ask(page: Page, question: str, timeout_ms: float):
    """Ask, and return the assistant turn once its answer has rendered."""
    page.fill("#question", question)
    page.click("#send")

    turn = page.locator(".turn--assistant").last
    expect(turn.locator(".bubble")).to_be_visible(timeout=timeout_ms)
    return turn


def graph_path(turn) -> list[str]:
    turn.locator(".obs__toggle").click()
    return turn.locator(".obs__nodes .node__name").all_inner_texts()


@pytest.mark.parametrize(
    "app_server",
    ["fake", pytest.param("live", marks=pytest.mark.live)],
    indirect=True,
)
def test_upload_ask_and_refuse(page: Page, app_server):
    # Deleting a document asks for confirmation; Playwright dismisses native
    # dialogs unless told otherwise, which would silently cancel the delete.
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(app_server.base_url)

    clear_workspace(page)

    page.set_input_files("#file-input", [str(path) for path in CORPUS])
    expect(page.locator("#slots")).to_have_text("3 / 5", timeout=INGEST_MS)

    # Every file reports as newly ingested, with a chunk count — a document
    # that parsed to nothing would still be listed, so the count matters.
    added = page.locator("#report .report__item[data-kind='ingested']")
    expect(added).to_have_count(3)
    for path in CORPUS:
        expect(added.filter(has_text=path.name)).to_contain_text("chunks)")
        expect(page.locator("#doclist .doc__name", has_text=path.name)).to_be_visible()

    for probe in ANSWERABLE:
        turn = ask(page, probe.question, app_server.answer_timeout_ms)

        expect(turn.locator(".bubble")).to_have_attribute("data-refused", "false")
        expect(turn.locator("button.cite").first).to_be_visible()

        # The top source must be the document that actually holds the answer,
        # carrying the provenance its format can support.
        top_source = turn.locator(".source").first
        expect(top_source.locator(".source__label")).to_contain_text(probe.provenance)

        # Clicking a citation chip opens the passage it points at: the claim
        # "every citation resolves" as a user can check it.
        turn.locator("button.cite").first.click()
        expect(top_source.locator(".source__text")).to_be_visible()
        expect(top_source.locator(".source__text")).not_to_have_text("")

        if app_server.live:
            expect(turn.locator(".bubble")).to_contain_text(probe.fact, ignore_case=True)

    refusal = ask(page, OFF_TOPIC, app_server.answer_timeout_ms)
    expect(refusal.locator(".bubble")).to_have_attribute("data-refused", "true")
    expect(refusal.locator(".bubble")).not_to_have_text("")
    expect(refusal.locator(".sources")).to_have_count(0)
    # Refused by the relevance gate, so no draft was ever generated: the
    # cheapest possible refusal, and visible in the trace.
    assert "draft" not in graph_path(refusal), "an off-topic question reached the model"

    if app_server.live:
        # The claim a fake cannot test: the model declines rather than
        # inventing a plausible number.
        trap = ask(page, FABRICATION_TRAP, app_server.answer_timeout_ms)
        expect(trap.locator(".bubble")).to_have_attribute("data-refused", "true")
        expect(trap.locator(".sources")).to_have_count(0)
