import re
from io import BytesIO

import pdfplumber

from .base import Block, ParsedDocument

_BLANK_LINE_RE = re.compile(r"\n\s*\n+")


def parse_pdf(filename: str, content: bytes) -> ParsedDocument:
    blocks: list[Block] = []
    offset = 0

    with pdfplumber.open(BytesIO(content)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            for para in _BLANK_LINE_RE.split(page_text):
                para = para.strip()
                if not para:
                    continue
                start, end = offset, offset + len(para)
                blocks.append(
                    Block(
                        text=para,
                        section_path=None,
                        page_number=page_index,
                        char_start=start,
                        char_end=end,
                    )
                )
                offset = end + 2

    return ParsedDocument(filename=filename, blocks=blocks)
