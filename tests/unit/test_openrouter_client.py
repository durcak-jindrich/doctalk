"""Offline tests for the OpenRouter adapter.

Complements the single live test in `tests/live/`: that one proves a real call
works, these cover what a real provider will not produce on demand — a
choice-less response, a null message, a missing cost field, and the error
mapping. No network, no API key, no quota.
"""

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, NotFoundError, RateLimitError

from app.llm import LLMError, Message, OpenRouterClient
from app.llm.openrouter import _usage_from_response


def _client(stub_create) -> OpenRouterClient:
    client = OpenRouterClient(api_key="test-key")
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=stub_create))
    )
    return client


def _completion(content: str | None, *, usage=None, model="vendor/model"):
    choices = [] if content is None else [SimpleNamespace(message=SimpleNamespace(content=content))]
    return SimpleNamespace(choices=choices, model=model, usage=usage)


def _error(cls, status: int):
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


MESSAGES = [Message(role="user", content="hi")]


def test_missing_api_key_fails_fast_with_an_actionable_message():
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(api_key="")


def test_returns_text_usage_and_latency():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=4, cost=0.0002)
    client = _client(lambda **kw: _completion("hello", usage=usage))

    response = client.complete(MESSAGES)

    assert response.text == "hello"
    assert response.model == "vendor/model"
    assert response.usage.total_tokens == 14
    assert response.usage.cost_usd == 0.0002
    assert response.latency_ms >= 0


def test_usage_parsing_tolerates_providers_that_omit_cost_or_usage():
    assert _usage_from_response(None).total_tokens == 0
    partial = _usage_from_response(SimpleNamespace(prompt_tokens=5, completion_tokens=None))
    assert partial.total_tokens == 5
    assert partial.cost_usd is None


def test_choice_less_response_is_an_error_not_an_empty_answer():
    client = _client(lambda **kw: _completion(None))

    with pytest.raises(LLMError, match="no choices"):
        client.complete(MESSAGES)


def test_null_message_content_is_an_error_not_an_empty_answer():
    """Reasoning models can return a null `content` — that must not read as
    an empty answer, which the citation validator would reject as ungrounded."""
    null_content = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
        model="vendor/model",
        usage=None,
    )
    client = _client(lambda **kw: null_content)

    with pytest.raises(LLMError, match="empty message"):
        client.complete(MESSAGES)


def test_rate_limit_maps_to_an_error_that_names_the_fix():
    def raise_429(**kwargs):
        raise _error(RateLimitError, 429)

    with pytest.raises(LLMError, match="rate-limited"):
        _client(raise_429).complete(MESSAGES)


def test_retired_model_slug_maps_to_an_error_that_names_the_fix():
    def raise_404(**kwargs):
        raise _error(NotFoundError, 404)

    with pytest.raises(LLMError, match="not available"):
        _client(raise_404).complete(MESSAGES)


def test_other_provider_failures_still_surface_as_llm_error():
    def raise_connection(**kwargs):
        raise APIConnectionError(request=httpx.Request("POST", "https://openrouter.ai"))

    with pytest.raises(LLMError, match="OpenRouter call failed"):
        _client(raise_connection).complete(MESSAGES)
