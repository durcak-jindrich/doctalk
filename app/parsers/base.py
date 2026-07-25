from dataclasses import dataclass


@dataclass
class Block:
    """One paragraph-level unit of extracted text, before chunking."""

    text: str
    section_path: list[str] | None
    page_number: int | None
    char_start: int
    char_end: int


@dataclass
class ParsedDocument:
    filename: str
    blocks: list[Block]
