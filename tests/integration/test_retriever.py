from app.retrieval import HybridRerankRetriever
from app.retrieval.retriever import _lexical_search
from app.storage import get_connection, ingest_document

FIXTURES = {
    "hr-policy.md": b"""# Sick Leave Policy

## Eligibility

All full-time employees are eligible for sick leave starting on day one of
employment. Part-time employees accrue leave on a pro-rated basis.

## Vacation

Full-time employees accrue fifteen days of vacation per year. Vacation
requests must be submitted two weeks in advance to a manager.
""",
    "it-policy.md": b"""# Password Policy

## Requirements

Passwords must be at least twelve characters and rotated every ninety
days. Multi-factor authentication is required for all remote access.

## VPN Access

Employees connecting from outside the office must use the corporate VPN
client, configured by the IT helpdesk.
""",
    "product-faq.md": b"""# Refund Policy

## Standard Refunds

Customers may request a refund within thirty days of purchase, provided
the product is unused and in its original packaging.
""",
}


def _ingest_fixtures():
    for filename, content in FIXTURES.items():
        ingest_document(filename, content)


def test_retriever_ranks_the_relevant_document_first(clean_schema):
    _ingest_fixtures()
    retriever = HybridRerankRetriever()

    cases = [
        ("How many vacation days do full-time employees get?", "hr-policy"),
        ("What are the password requirements for IT security?", "it-policy"),
        ("Can I get a refund on a product I bought?", "product-faq"),
    ]
    for query, expected_prefix in cases:
        with get_connection() as conn:
            results = retriever.retrieve(conn, query, top_k=3)
        assert results, f"expected results for {query!r}"
        assert results[0].document_id.startswith(expected_prefix), (
            f"query {query!r} expected top hit from {expected_prefix!r}, "
            f"got {results[0].document_id!r}"
        )


def test_retriever_records_which_leg_contributed(clean_schema):
    _ingest_fixtures()
    retriever = HybridRerankRetriever()

    with get_connection() as conn:
        results = retriever.retrieve(conn, "vacation days", top_k=3)

    assert results
    assert any(r.dense_rank is not None or r.lexical_rank is not None for r in results)


def test_lexical_leg_matches_an_exact_rare_term(clean_schema):
    """Guards against the BM25 leg silently returning nothing, which would
    leave the 'hybrid' retriever running on the dense leg alone."""
    _ingest_fixtures()

    with get_connection() as conn:
        assert _lexical_search(conn, "helpdesk", 10)


def test_retriever_survives_punctuation_in_the_query(clean_schema):
    """User punctuation must never reach ParadeDB's query-string parser —
    `:`, `-`, `"` and `(` are query syntax there and raise a parse error."""
    _ingest_fixtures()
    retriever = HybridRerankRetriever()

    for query in [
        "What is the vacation policy: 15 days?",
        "-vacation",
        'unclosed "quote',
        "a AND (b",
        "C++",
    ]:
        with get_connection() as conn:
            assert retriever.retrieve(conn, query, top_k=1), f"no results for {query!r}"


def test_retriever_scores_off_topic_query_lower_than_on_topic(clean_schema):
    _ingest_fixtures()
    retriever = HybridRerankRetriever()

    with get_connection() as conn:
        on_topic = retriever.retrieve(conn, "How many vacation days do employees get?", top_k=1)
        off_topic = retriever.retrieve(conn, "What is the meaning of life?", top_k=1)

    assert on_topic[0].rerank_score > off_topic[0].rerank_score
