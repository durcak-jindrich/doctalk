from functools import lru_cache

from .base import LLMClient, LLMError, LLMResponse, Message, Role, TokenUsage
from .openrouter import OpenRouterClient


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """The process-wide LLM client. Cached so the HTTP client is reused."""
    return OpenRouterClient()


__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "Message",
    "OpenRouterClient",
    "Role",
    "TokenUsage",
    "get_llm_client",
]
