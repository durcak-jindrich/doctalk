"""Simplest possible manual check that the LLM half of Phase 3 works: two
hardcoded sources -> one real OpenRouter call -> a grounded answer whose
citations resolve back to those sources.

No database and no retrieval: the sources below stand in for what the retriever
would return, so this isolates config + provider + prompt + citation validation.

Run from the project root:
    uv run python -m scripts.manual_verify_llm

Needs OPENROUTER_API_KEY in .env. Costs exactly one LLM call (the retry loop is
pinned to a single attempt).

Edit SOURCES / QUESTION / EXPECTED below and re-run to see the effect
immediately -- e.g. delete source 1 and the model should refuse instead.
"""

from app.config import settings
from app.llm import LLMError, OpenRouterClient
from app.retrieval import RetrievedChunk
from app.synthesis import build_messages, synthesize

# Stand-ins for retriever output. Source 2 is a plausible distractor: a
# grounded answer must cite [1] and leave it alone. `rerank_score` is set well
# above `min_rerank_score` so the pre-LLM relevance gate does not fire here.
SOURCES = [
    RetrievedChunk(
        chunk_id="hr-policy#c0001",
        document_id="hr-policy",
        filename="hr-policy.md",
        text=(
            "Full-time employees accrue fifteen days of paid vacation per calendar year. "
            "Requests must be approved by a line manager two weeks in advance."
        ),
        section_path=["HR Policy", "Vacation"],
        page_number=2,
        rerank_score=5.0,
        dense_rank=1,
        lexical_rank=1,
    ),
    RetrievedChunk(
        chunk_id="it-security#c0003",
        document_id="it-security",
        filename="it-security.md",
        text=(
            "Passwords must be at least twelve characters long and rotated every "
            "ninety days. Reuse across systems is prohibited."
        ),
        section_path=["IT Security", "Passwords"],
        page_number=1,
        rerank_score=1.0,
        dense_rank=2,
        lexical_rank=None,
    ),
]

QUESTION = "How much paid holiday do full-time staff get each year?"
EXPECTED = "fifteen"  # set to "" to skip the content check

SEP = "-" * 72


def main() -> None:
    print(f"\nModel:       {settings.llm_model}")
    print(f"Base URL:    {settings.openrouter_base_url}")
    print(f"Temperature: {settings.llm_temperature}   max_tokens: {settings.llm_max_tokens}")

    print(f"\n{SEP}\nSTEP 1 - THE EXACT PROMPT BEING SENT\n{SEP}")
    for message in build_messages(QUESTION, SOURCES):
        print(f"\n[{message.role}]\n{message.content}")

    print(f"\n{SEP}\nSTEP 2 - ONE REAL OPENROUTER CALL\n{SEP}")
    try:
        client = OpenRouterClient()
        # max_attempts=1: no corrective retry, so this script can never spend
        # more than the single call it advertises.
        answer = synthesize(QUESTION, SOURCES, client, max_attempts=1)
    except LLMError as exc:
        print(f"\nFAIL - the call did not go through:\n       {exc}\n")
        raise SystemExit(1) from exc

    usage = answer.total_usage
    cost = "n/a" if usage.cost_usd is None else f"${usage.cost_usd:.6f}"
    print(f"\nReplied as:  {answer.model}")
    print(f"Latency:     {answer.llm_latency_ms:.0f} ms")
    print(f"Tokens:      {usage.prompt_tokens} in + {usage.completion_tokens} out   cost: {cost}")

    print(f"\n{SEP}\nSTEP 3 - THE ANSWER\n{SEP}")
    print(f"\n{answer.text}\n")
    if answer.refused:
        print(f"Refused, reason: {answer.refusal_reason}")
    for citation in answer.citations:
        print(f"  [{citation.marker}] -> {citation.chunk_id}   {citation.label}")

    print(f"\n{SEP}")
    if answer.refused:
        print(f"FAIL - refused ({answer.refusal_reason}) where a grounded answer was expected.")
        print("       If you edited SOURCES so the answer is no longer in them, this is")
        print("       the correct behaviour and the check above is what needs updating.")
        raise SystemExit(1)
    if not answer.citations:
        print("FAIL - answered without a single citation.")
        raise SystemExit(1)
    if EXPECTED and EXPECTED.lower() not in answer.text.lower():
        print(f"FAIL - answer does not contain the expected fact {EXPECTED!r}.")
        raise SystemExit(1)

    cited = ", ".join(f"[{c.marker}]={c.chunk_id}" for c in answer.citations)
    print("PASS - the key works, the model answered from the sources only, and every")
    print(f"       citation resolved: {cited}")
    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
