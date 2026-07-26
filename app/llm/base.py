"""Provider-agnostic LLM interface.

Everything above this layer (synthesis, governance, the LangGraph nodes)
depends only on `LLMClient`, so swapping OpenRouter for Azure OpenAI is a
constructor change rather than a rewrite — see `docs/technical-decisions.md`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant"]


class LLMError(RuntimeError):
    """The provider call failed, or returned nothing usable."""


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class TokenUsage:
    """Token counts (and cost, where the provider reports it) for one call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: TokenUsage
    latency_ms: float


class LLMClient(ABC):
    """One synchronous chat-completion call.

    Sync on purpose: retrieval (psycopg, sentence-transformers) is sync too,
    so the whole `/ask` pipeline runs in FastAPI's threadpool as one blocking
    unit instead of mixing execution models for no concurrency gain.
    """

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Return the model's reply. Raises `LLMError` on any provider failure."""
