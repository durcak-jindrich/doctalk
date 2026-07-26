"""Test doubles shared by the fast suite."""

from app.llm import LLMClient, LLMResponse, Message, TokenUsage
from app.retrieval import RetrievedChunk


class FakeLLMClient(LLMClient):
    """Replays scripted replies and records what it was asked.

    Lets the whole synthesis/governance loop — including the corrective retry —
    be tested deterministically, with no network call and no API key.
    """

    def __init__(self, replies: list[str], *, model: str = "fake/model"):
        self._replies = list(replies)
        self.model = model
        self.calls: list[list[Message]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_prompt(self) -> str:
        return "\n\n".join(m.content for m in self.calls[-1])

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError("FakeLLMClient ran out of scripted replies")
        return LLMResponse(
            text=self._replies.pop(0),
            model=self.model,
            usage=TokenUsage(prompt_tokens=100, completion_tokens=20, cost_usd=0.0001),
            latency_ms=12.5,
        )


def make_chunk(
    chunk_id: str = "handbook-abc123#c0001",
    *,
    text: str = "Employees accrue 25 days of annual leave per calendar year.",
    filename: str = "handbook.md",
    section_path: list[str] | None = None,
    page_number: int | None = None,
    rerank_score: float = 5.0,
    dense_rank: int | None = 1,
    lexical_rank: int | None = 1,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=chunk_id.split("#")[0],
        filename=filename,
        text=text,
        section_path=section_path,
        page_number=page_number,
        rerank_score=rerank_score,
        dense_rank=dense_rank,
        lexical_rank=lexical_rank,
    )
