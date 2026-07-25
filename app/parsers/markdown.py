import re

from .base import Block, ParsedDocument

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def parse_markdown(filename: str, content: str) -> ParsedDocument:
    blocks: list[Block] = []
    section_stack: list[str] = []
    paragraph_lines: list[str] = []
    paragraph_start: int | None = None
    offset = 0

    def flush(end: int) -> None:
        nonlocal paragraph_lines, paragraph_start
        if paragraph_lines:
            text = "".join(paragraph_lines).strip()
            if text:
                blocks.append(
                    Block(
                        text=text,
                        section_path=list(section_stack) or None,
                        page_number=None,
                        char_start=paragraph_start,
                        char_end=end,
                    )
                )
        paragraph_lines = []
        paragraph_start = None

    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            flush(offset)
            level = len(heading_match.group(1))
            section_stack = section_stack[: level - 1]
            section_stack.append(heading_match.group(2).strip())
        elif stripped == "":
            flush(offset)
        else:
            if paragraph_start is None:
                paragraph_start = offset
            paragraph_lines.append(line)
        offset += len(line)
    flush(offset)

    return ParsedDocument(filename=filename, blocks=blocks)
