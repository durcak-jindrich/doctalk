import re
from dataclasses import dataclass

import tiktoken

from app.parsers.base import Block

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_ENCODING = "cl100k_base"


@dataclass
class Chunk:
    chunk_index: int
    text: str
    section_path: list[str] | None
    page_number: int | None
    char_start: int
    char_end: int


@dataclass
class _Atom:
    text: str
    section_path: list[str] | None
    page_number: int | None
    char_start: int
    char_end: int


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text.strip()) if s]


def _atoms_from_block(block: Block, enc: tiktoken.Encoding, target_tokens: int) -> list[_Atom]:
    if len(enc.encode(block.text)) <= target_tokens:
        return [
            _Atom(
                block.text, block.section_path, block.page_number, block.char_start, block.char_end
            )
        ]

    atoms: list[_Atom] = []
    cursor = 0
    for sentence in _split_sentences(block.text):
        idx = block.text.index(sentence, cursor)
        start = block.char_start + idx
        end = start + len(sentence)
        cursor = idx + len(sentence)

        tokens = enc.encode(sentence)
        if len(tokens) <= target_tokens:
            atoms.append(_Atom(sentence, block.section_path, block.page_number, start, end))
        else:
            # Last-resort hard split for a single sentence longer than the
            # chunk budget — char offsets are approximate for these pieces.
            for i in range(0, len(tokens), target_tokens):
                sub_text = enc.decode(tokens[i : i + target_tokens])
                atoms.append(_Atom(sub_text, block.section_path, block.page_number, start, end))
    return atoms


def _tail_atom(buffer: list[_Atom], overlap_tokens: int, enc: tiktoken.Encoding) -> _Atom | None:
    if overlap_tokens <= 0 or not buffer:
        return None
    text = "\n\n".join(a.text for a in buffer)
    tokens = enc.encode(text)
    tail_text = enc.decode(tokens[-overlap_tokens:]) if len(tokens) > overlap_tokens else text
    last = buffer[-1]
    return _Atom(
        text=tail_text,
        section_path=last.section_path,
        page_number=last.page_number,
        char_start=max(last.char_end - len(tail_text), buffer[0].char_start),
        char_end=last.char_end,
    )


def chunk_blocks(
    blocks: list[Block], target_tokens: int = 400, overlap_ratio: float = 0.125
) -> list[Chunk]:
    """Pack paragraph-level blocks into token-budgeted chunks.

    Chunks never cross a section boundary. Paragraphs are merged up to
    ``target_tokens`` with a sliding overlap of ``overlap_ratio``; a single
    paragraph longer than the budget is split on sentence boundaries (never
    mid-sentence, except as a last resort for a single oversized sentence).
    """
    enc = tiktoken.get_encoding(_ENCODING)
    overlap_tokens = int(target_tokens * overlap_ratio)

    atoms: list[_Atom] = []
    for block in blocks:
        atoms.extend(_atoms_from_block(block, enc, target_tokens))

    chunks: list[Chunk] = []
    buffer: list[_Atom] = []
    buffer_tokens = 0
    current_section: object = object()

    def flush() -> None:
        nonlocal buffer, buffer_tokens
        if not buffer:
            return
        text = "\n\n".join(a.text for a in buffer)
        chunks.append(
            Chunk(
                chunk_index=len(chunks),
                text=text,
                section_path=buffer[0].section_path,
                page_number=buffer[0].page_number,
                char_start=buffer[0].char_start,
                char_end=buffer[-1].char_end,
            )
        )
        buffer = []
        buffer_tokens = 0

    for atom in atoms:
        if atom.section_path != current_section and buffer:
            flush()
        current_section = atom.section_path

        atom_tokens = len(enc.encode(atom.text))
        if buffer and buffer_tokens + atom_tokens > target_tokens:
            tail = _tail_atom(buffer, overlap_tokens, enc)
            flush()
            if tail is not None:
                buffer.append(tail)
                buffer_tokens = len(enc.encode(tail.text))

        buffer.append(atom)
        buffer_tokens += atom_tokens

    flush()
    return chunks
