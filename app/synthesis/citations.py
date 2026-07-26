"""Deterministic citation validation — the governance half of Phase 3.

This is code, not a second LLM pass: every marker in an answer is resolved
against the exact list of chunks that was sent to the model, so a citation can
only survive if it points at something actually retrieved. What this does *not*
check is whether the cited chunk entails the claim — that is faithfulness, and
it is measured separately in the Phase 9 evaluation.
"""

import re
from dataclasses import dataclass

from app.retrieval import RetrievedChunk

from .prompt import source_label

# Any bracketed run without nesting; the tokens inside decide whether it is a
# citation attempt at all.
_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")
_INDEX_RE = re.compile(r"^\d{1,3}$")
# The `chunk_id` form, in case a model cites IDs instead of source numbers.
_CHUNK_ID_RE = re.compile(r"^[A-Za-z0-9._-]+#c\d+$")
# Some models (observed: nvidia/nemotron-3-nano) emit CJK full-width brackets
# for citations. Folded to ASCII before parsing so a formatting habit doesn't
# read as a missing citation and cost a retry.
_BRACKET_ALIASES = str.maketrans({"【": "[", "】": "]", "［": "[", "］": "]"})


@dataclass(frozen=True)
class Citation:
    """A validated pointer from the answer back to a retrieved chunk."""

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


@dataclass(frozen=True)
class CitationReport:
    citations: list[Citation]
    invalid_markers: list[str]

    @property
    def has_invalid(self) -> bool:
        return bool(self.invalid_markers)


def _citation_tokens(text: str) -> list[str]:
    """Bracketed tokens that are *attempts* at a citation.

    Only all-digit tokens and `chunk_id`-shaped tokens qualify, so ordinary
    prose brackets ("[sic]", "[Figure 3]") are ignored rather than reported as
    fabricated citations. A hallucinated non-numeric source therefore reads as
    "no citation" and is caught by the at-least-one-citation rule instead.
    """
    tokens: list[str] = []
    for group in _BRACKET_RE.findall(text.translate(_BRACKET_ALIASES)):
        for raw in re.split(r"[,;]", group):
            token = raw.strip()
            if _INDEX_RE.match(token) or _CHUNK_ID_RE.match(token):
                tokens.append(token)
    return tokens


def normalize_markers(answer: str, chunks: list[RetrievedChunk]) -> str:
    """Rewrite citation markers into one canonical `[n]` form per source.

    Folds full-width brackets to ASCII, collapses `[1, 2]` to `[1][2]`, and
    resolves `chunk_id` markers back to their source number, so the frontend has
    a single shape to render. Markers that resolve to nothing are left untouched
    for `validate_citations` to flag; prose brackets are left untouched entirely.
    """
    by_chunk_id = {chunk.chunk_id: number for number, chunk in enumerate(chunks, start=1)}

    def rewrite(match: re.Match[str]) -> str:
        tokens = [token.strip() for token in re.split(r"[,;]", match.group(1))]
        if not all(_INDEX_RE.match(t) or _CHUNK_ID_RE.match(t) for t in tokens):
            return match.group(0)
        numbers = [int(t) if _INDEX_RE.match(t) else by_chunk_id.get(t) for t in tokens]
        if any(number is None for number in numbers):
            return match.group(0)
        return "".join(f"[{number}]" for number in numbers)

    return _BRACKET_RE.sub(rewrite, answer.translate(_BRACKET_ALIASES))


def validate_citations(answer: str, chunks: list[RetrievedChunk]) -> CitationReport:
    """Resolve every citation marker in `answer` against the chunks we sent.

    Markers are 1-based positions in `chunks`. Duplicates collapse to one
    `Citation`, ordered by first appearance in the answer.
    """
    by_number = {number: chunk for number, chunk in enumerate(chunks, start=1)}
    by_chunk_id = {chunk.chunk_id: number for number, chunk in by_number.items()}

    citations: list[Citation] = []
    seen: set[int] = set()
    invalid: list[str] = []

    for token in _citation_tokens(answer):
        number = int(token) if _INDEX_RE.match(token) else by_chunk_id.get(token)
        chunk = by_number.get(number) if number is not None else None
        if chunk is None:
            if token not in invalid:
                invalid.append(token)
            continue
        if number in seen:
            continue
        seen.add(number)
        citations.append(
            Citation(
                marker=number,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                filename=chunk.filename,
                label=source_label(chunk),
                text=chunk.text,
                section_path=chunk.section_path,
                page_number=chunk.page_number,
                rerank_score=chunk.rerank_score,
                dense_rank=chunk.dense_rank,
                lexical_rank=chunk.lexical_rank,
            )
        )

    return CitationReport(citations=citations, invalid_markers=invalid)


def describe_problem(report: CitationReport) -> str | None:
    """The governance verdict: `None` if the answer may be shown as-is."""
    if report.has_invalid:
        listed = ", ".join(f"[{marker}]" for marker in report.invalid_markers)
        return f"it cited {listed}, which is not in the SOURCES list"
    if not report.citations:
        return "it contained no [n] citation markers at all"
    return None
