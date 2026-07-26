"""Wire format for the HTTP API.

Deliberately separate from the internal dataclasses: `Answer`, `Citation` and
the storage rows are free to change shape without breaking the frontend, and
what the API exposes is an explicit decision rather than whatever a dataclass
happens to hold.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.llm import TokenUsage
from app.synthesis import Answer, Citation


class ChunkOut(BaseModel):
    id: str
    chunk_index: int
    section_path: list[str] | None
    page_number: int | None
    char_start: int
    char_end: int
    text: str


class DocumentOut(BaseModel):
    id: str
    filename: str
    file_type: str
    char_count: int
    chunk_count: int
    uploaded_at: datetime


class DocumentDetailOut(DocumentOut):
    chunks: list[ChunkOut]


class WorkspaceOut(BaseModel):
    """Documents plus capacity — the upload widget needs both in one response."""

    documents: list[DocumentOut]
    used: int
    capacity: int
    remaining: int


class UploadResultOut(BaseModel):
    """Outcome for one file in a batch.

    Per-file rather than per-request: uploading three files where one is an
    unreadable PDF should ingest the other two and say what went wrong with
    the third, not fail the whole batch.
    """

    filename: str
    status: Literal["ingested", "duplicate", "rejected"]
    document_id: str | None = None
    chunk_count: int | None = None
    error: str | None = None


class UploadResponseOut(BaseModel):
    results: list[UploadResultOut]
    workspace: WorkspaceOut


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class CitationOut(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    filename: str
    label: str
    text: str
    section_path: list[str] | None
    page_number: int | None
    rerank_score: float
    dense_rank: int | None
    lexical_rank: int | None

    @classmethod
    def of(cls, citation: Citation) -> "CitationOut":
        return cls(**vars(citation))


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None

    @classmethod
    def of(cls, usage: TokenUsage) -> "UsageOut":
        return cls(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=usage.cost_usd,
        )


class ObservabilityOut(BaseModel):
    """What the "under the hood" panel renders.

    `steps` is the graph's node path, so a run that needed a corrective retry
    is visibly different from one that did not.
    """

    route: str
    steps: list[str]
    attempts: int
    model: str | None
    usage: UsageOut
    llm_latency_ms: float
    total_latency_ms: float


class AnswerOut(BaseModel):
    text: str
    refused: bool
    refusal_reason: str | None
    citations: list[CitationOut]
    observability: ObservabilityOut

    @classmethod
    def of(cls, answer: Answer, *, total_latency_ms: float) -> "AnswerOut":
        return cls(
            text=answer.text,
            refused=answer.refused,
            refusal_reason=answer.refusal_reason,
            citations=[CitationOut.of(c) for c in answer.citations],
            observability=ObservabilityOut(
                route=answer.route,
                steps=answer.steps,
                attempts=answer.attempts,
                model=answer.model,
                usage=UsageOut.of(answer.total_usage),
                llm_latency_ms=round(answer.llm_latency_ms, 1),
                total_latency_ms=round(total_latency_ms, 1),
            ),
        )


class ErrorOut(BaseModel):
    detail: str
