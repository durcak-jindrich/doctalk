import tiktoken

from app.chunking import chunk_blocks
from app.chunking.chunker import _ENCODING
from app.parsers.base import Block

ENC = tiktoken.get_encoding(_ENCODING)

# `all-MiniLM-L6-v2`'s max_seq_length. Anything longer is silently truncated at
# embed time, leaving the chunk's tail unsearchable by the dense leg.
EMBEDDER_WINDOW = 256


def _block(sentences: int, section: str = "Policy") -> Block:
    text = " ".join(
        f"Sentence number {i} carries a little bit of policy detail worth indexing."
        for i in range(sentences)
    )
    return Block(text=text, section_path=[section], page_number=1, char_start=0, char_end=len(text))


def test_no_chunk_exceeds_the_embedding_models_input_window():
    chunks = chunk_blocks([_block(200)])

    assert len(chunks) > 1, "expected the oversized block to be split"
    oversized = {c.chunk_index: len(ENC.encode(c.text)) for c in chunks}
    assert all(n <= EMBEDDER_WINDOW for n in oversized.values()), (
        f"chunk token counts exceed the {EMBEDDER_WINDOW}-token window: {oversized}"
    )


def test_consecutive_chunks_share_an_overlap():
    chunks = chunk_blocks([_block(200)])

    for previous, current in zip(chunks, chunks[1:], strict=False):
        head = current.text[:40].strip()
        assert head in previous.text, (
            f"chunk {current.chunk_index} does not overlap its predecessor"
        )


def test_chunks_never_cross_a_section_boundary():
    chunks = chunk_blocks([_block(3, "Vacation"), _block(3, "Sick Leave")])

    assert [c.section_path for c in chunks] == [["Vacation"], ["Sick Leave"]]
