from pathlib import Path

from .base import Block, ParsedDocument
from .docx import parse_docx
from .markdown import parse_markdown
from .pdf import parse_pdf


def parse_document(filename: str, content: bytes) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(filename, content)
    if suffix == ".docx":
        return parse_docx(filename, content)
    if suffix == ".md":
        return parse_markdown(filename, content.decode("utf-8"))
    raise ValueError(f"Unsupported file type: {suffix or filename!r}. Supported: PDF, DOCX, MD.")


__all__ = ["Block", "ParsedDocument", "parse_document"]
