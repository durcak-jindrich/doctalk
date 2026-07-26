import time

from openai import NotFoundError, OpenAI, OpenAIError, RateLimitError

from app.config import settings

from .base import LLMClient, LLMError, LLMResponse, Message, TokenUsage


def _usage_from_response(raw_usage: object) -> TokenUsage:
    """Read token counts off an OpenAI-shaped usage object.

    `cost` is an OpenRouter extension (returned because we send
    `usage: {include: true}`), so it is read defensively — a provider that
    omits it yields `cost_usd=None` rather than an error.
    """
    if raw_usage is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
        cost_usd=getattr(raw_usage, "cost", None),
    )


class OpenRouterClient(LLMClient):
    """`LLMClient` over OpenRouter's OpenAI-compatible chat completions API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        key = api_key if api_key is not None else settings.openrouter_api_key
        if not key:
            raise LLMError(
                "OPENROUTER_API_KEY is not set — copy .env.example to .env and add a key "
                "(https://openrouter.ai). Retrieval works without one; answering does not."
            )
        self.model = model or settings.llm_model
        self._client = OpenAI(
            api_key=key,
            base_url=base_url or settings.openrouter_base_url,
            timeout=timeout if timeout is not None else settings.llm_timeout_seconds,
            # The SDK retries 429/5xx with exponential backoff; free-tier slugs
            # are rate-limited upstream often enough to need more than its default.
            max_retries=max_retries if max_retries is not None else settings.llm_max_retries,
        )

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=(settings.llm_temperature if temperature is None else temperature),
                max_tokens=max_tokens if max_tokens is not None else settings.llm_max_tokens,
                # OpenRouter-specific: return the call's actual credit cost in `usage`.
                extra_body={"usage": {"include": True}},
            )
        except RateLimitError as exc:
            # The two foreseeable ones on a free key: the provider throttling the
            # `:free` slug upstream, and OpenRouter's own free-tier daily cap.
            raise LLMError(
                f"{self.model!r} is rate-limited (retried {settings.llm_max_retries}x). "
                "Free model slugs are throttled upstream and free-tier keys have a daily "
                "cap — switch LLM_MODEL to another model or add OpenRouter credit."
            ) from exc
        except NotFoundError as exc:
            raise LLMError(
                f"{self.model!r} is not available on this account. `:free` slugs get "
                "retired without notice — pick a current model and update LLM_MODEL."
            ) from exc
        except OpenAIError as exc:
            raise LLMError(f"OpenRouter call failed: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        if not completion.choices:
            raise LLMError(f"{self.model!r} returned no choices.")
        content = completion.choices[0].message.content
        if content is None:
            raise LLMError(f"{self.model!r} returned an empty message.")

        return LLMResponse(
            text=content,
            model=completion.model or self.model,
            usage=_usage_from_response(completion.usage),
            latency_ms=latency_ms,
        )
