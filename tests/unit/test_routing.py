import pytest

from app.graph import classify


@pytest.mark.parametrize(
    "question",
    [
        "summarize",
        "Summarize the documents",
        "summarise these docs",
        "Give me a summary of the uploaded files",
        "Can you please provide a brief overview of all of the documents?",
        "tldr",
        "TL;DR of everything",
        "key points",
        "What are the main points of these documents?",
        "What is this document about?",
        "what are these about",
    ],
)
def test_whole_workspace_summary_requests_route_to_the_summarize_tool(question):
    assert classify(question) == "summarize"


@pytest.mark.parametrize(
    "question",
    [
        "How many vacation days do I get?",
        # Topical: retrieval serves these better than every document's opening.
        "Summarize the leave policy",
        "Give me an overview of the security requirements",
        "What is the notice period about equipment returns?",
        "What does the handbook say about sick leave?",
        # Not a summary request, despite containing the word.
        "Who wrote the summary section?",
    ],
)
def test_topical_questions_stay_on_the_retrieval_route(question):
    assert classify(question) == "qa"
