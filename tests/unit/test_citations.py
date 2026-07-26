from app.synthesis import normalize_markers, validate_citations
from app.synthesis.citations import describe_problem
from app.synthesis.prompt import source_label
from tests.fakes import make_chunk

CHUNKS = [
    make_chunk("handbook-abc123#c0001", text="25 days of annual leave."),
    make_chunk("handbook-abc123#c0007", text="Sick leave requires a note after 3 days."),
    make_chunk("policy-def456#c0002", text="Expenses are reimbursed within 30 days."),
]


def test_resolves_markers_to_the_chunks_that_were_sent():
    report = validate_citations("Leave is 25 days [1]. Notes are needed [2].", CHUNKS)

    assert [c.marker for c in report.citations] == [1, 2]
    assert [c.chunk_id for c in report.citations] == [
        "handbook-abc123#c0001",
        "handbook-abc123#c0007",
    ]
    assert not report.has_invalid


def test_out_of_range_marker_is_invalid():
    report = validate_citations("Reimbursement is immediate [9].", CHUNKS)

    assert report.invalid_markers == ["9"]
    assert report.citations == []


def test_zero_is_invalid_because_markers_are_one_based():
    assert validate_citations("Something [0].", CHUNKS).invalid_markers == ["0"]


def test_repeated_marker_yields_one_citation_ordered_by_first_use():
    report = validate_citations("A [3]. B [1]. C [3].", CHUNKS)

    assert [c.marker for c in report.citations] == [3, 1]


def test_grouped_markers_are_split():
    report = validate_citations("Both sources agree [1, 3].", CHUNKS)

    assert [c.marker for c in report.citations] == [1, 3]


def test_chunk_id_markers_resolve_to_their_source_number():
    report = validate_citations("Leave is 25 days [handbook-abc123#c0001].", CHUNKS)

    assert [c.marker for c in report.citations] == [1]
    assert not report.has_invalid


def test_unretrieved_chunk_id_is_invalid():
    report = validate_citations("Invented [made-up-999999#c0001].", CHUNKS)

    assert report.invalid_markers == ["made-up-999999#c0001"]


def test_prose_brackets_are_not_treated_as_citations():
    report = validate_citations("The policy [sic] applies to staff [Figure 3].", CHUNKS)

    assert report.citations == []
    assert not report.has_invalid


def test_citation_carries_provenance_for_the_ui():
    chunk = make_chunk(
        "handbook-abc123#c0004",
        filename="handbook.pdf",
        section_path=["Leave", "Sick Leave"],
        page_number=4,
    )
    citation = validate_citations("Answer [1].", [chunk]).citations[0]

    assert citation.label == "handbook.pdf > Leave > Sick Leave (p. 4)"
    assert citation.page_number == 4
    assert citation.dense_rank == 1


def test_source_label_falls_back_to_filename_without_structure():
    assert source_label(make_chunk(filename="notes.md")) == "notes.md"


def test_normalize_markers_canonicalizes_groups_and_chunk_ids():
    text = "A [1, 2] and B [handbook-abc123#c0007]."

    assert normalize_markers(text, CHUNKS) == "A [1][2] and B [2]."


def test_full_width_brackets_are_read_as_citations():
    """Some models emit 【1】 instead of [1]; a formatting habit is not a refusal."""
    report = validate_citations("Leave is 25 days 【1】.", CHUNKS)

    assert [c.marker for c in report.citations] == [1]
    assert normalize_markers("Leave is 25 days 【1】.", CHUNKS) == "Leave is 25 days [1]."


def test_normalize_markers_leaves_unresolvable_and_prose_brackets_alone():
    text = "A [nope-000000#c0001] and B [see appendix]."

    assert normalize_markers(text, CHUNKS) == text


def test_describe_problem_flags_missing_and_invalid_citations():
    assert describe_problem(validate_citations("No markers here.", CHUNKS)) is not None
    assert "[9]" in describe_problem(validate_citations("Bad [9].", CHUNKS))
    assert describe_problem(validate_citations("Good [1].", CHUNKS)) is None
