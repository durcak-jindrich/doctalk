import hashlib
import re
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "document"


def make_document_id(filename: str, content: bytes) -> str:
    stem = Path(filename).stem
    digest = hashlib.sha256(content).hexdigest()[:6]
    return f"{slugify(stem)}-{digest}"


def make_chunk_id(document_id: str, chunk_index: int) -> str:
    return f"{document_id}#c{chunk_index:04d}"
