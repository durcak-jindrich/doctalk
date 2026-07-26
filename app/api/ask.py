"""The `/api/ask` route — one HTTP call onto the LangGraph pipeline.

A refusal is a successful response, not an error: "the documents do not answer
this" is the product working correctly, and the client renders it as an answer
with `refused: true`. Only a broken *provider* is a 5xx.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import Connection

from app.graph import answer_question
from app.llm import LLMError

from .deps import answer_graph, db
from .schemas import AnswerOut, AskRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ask"])


@router.post("/ask", response_model=AnswerOut)
def ask(
    request: AskRequest,
    conn: Annotated[Connection, Depends(db)],
    graph: Annotated[Any, Depends(answer_graph)],
) -> AnswerOut:
    try:
        answer = answer_question(conn, request.question, graph=graph)
    except LLMError as exc:
        # Missing key, retired model slug, rate limit, provider outage — all
        # upstream conditions the user cannot fix by rephrasing.
        logger.error(
            "LLM provider unavailable: %s", exc, extra={"event": "ask.provider_unavailable"}
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    # The run's own summary log is emitted by `answer_question`, which is where
    # the timings and token counts are complete.
    return AnswerOut.of(answer)
