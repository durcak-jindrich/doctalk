"""Wire format for the HTTP API.

Deliberately separate from the internal dataclasses: `Answer`, `Citation` and
the storage rows are free to change shape without breaking the frontend, and
what the API exposes is an explicit decision rather than whatever a dataclass
happens to hold.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.llm import TokenUsage
from app.observability import NodeStep
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


class NodeStepOut(BaseModel):
    """One graph node's execution, as rendered in the panel."""

    node: str
    duration_ms: float
    detail: dict[str, Any]

    @classmethod
    def of(cls, step: NodeStep) -> "NodeStepOut":
        return cls(node=step.node, duration_ms=step.duration_ms, detail=step.detail)


class ObservabilityOut(BaseModel):
    """What the "under the hood" panel renders.

    `steps` is the graph's node path with per-node timings and verdicts, so a
    run that needed a corrective retry is visibly different from one that did
    not — and slow stages are attributable rather than lumped into one total.
    """

    trace_id: str | None
    route: str
    steps: list[NodeStepOut]
    attempts: int
    model: str | None
    usage: UsageOut
    llm_latency_ms: float
    total_latency_ms: float
    #: Pipeline time not spent waiting on the model — retrieval, reranking, SQL.
    overhead_ms: float


class AnswerOut(BaseModel):
    text: str
    refused: bool
    refusal_reason: str | None
    citations: list[CitationOut]
    observability: ObservabilityOut

    @classmethod
    def of(cls, answer: Answer) -> "AnswerOut":
        return cls(
            text=answer.text,
            refused=answer.refused,
            refusal_reason=answer.refusal_reason,
            citations=[CitationOut.of(c) for c in answer.citations],
            observability=ObservabilityOut(
                trace_id=answer.trace_id,
                route=answer.route,
                steps=[NodeStepOut.of(s) for s in answer.steps],
                attempts=answer.attempts,
                model=answer.model,
                usage=UsageOut.of(answer.total_usage),
                llm_latency_ms=round(answer.llm_latency_ms, 1),
                total_latency_ms=answer.total_latency_ms,
                overhead_ms=round(max(0.0, answer.total_latency_ms - answer.llm_latency_ms), 1),
            ),
        )


class ErrorOut(BaseModel):
    detail: str
