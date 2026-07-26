"""The state passed between graph nodes.

Everything a node needs to decide what happens next lives here, so the nodes
stay pure functions of state plus injected dependencies. `usages` and `steps`
accumulate across nodes (including across a corrective retry); every other
field is overwritten by whichever node owns it.
"""

import operator
from typing import Annotated, TypedDict

from app.llm import Message, TokenUsage
from app.retrieval import RetrievedChunk
from app.synthesis import Answer, Route


class AskState(TypedDict, total=False):
    question: str
    route: Route
    # Sources sent to the model — from relevance retrieval, or from the
    # summarize tool's structural selection.
    chunks: list[RetrievedChunk]
    # The running conversation, extended with a correction on a failed attempt.
    messages: list[Message]
    # Raw model text for the current attempt, pre-validation.
    draft: str
    attempts: int
    usages: Annotated[list[TokenUsage], operator.add]
    model: str | None
    llm_latency_ms: float
    steps: Annotated[list[str], operator.add]
    # Set only when the run is finished — the graph's terminal condition.
    answer: Answer | None
