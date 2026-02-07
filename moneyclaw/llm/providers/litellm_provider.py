"""LiteLLM-based provider — covers DeepSeek, Groq, OpenAI, Anthropic via unified API."""

from __future__ import annotations

import structlog
from litellm import acompletion, cost_per_token

from moneyclaw.llm.providers.base import LLMProvider, LLMResponse

log = structlog.get_logger()

# Preset configurations for each layer
PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {"model": "deepseek/deepseek-chat", "label": "DeepSeek V3"},
    "groq": {"model": "groq/llama-3.3-70b-versatile", "label": "Groq Llama 3.3 70B"},
    "openai": {"model": "gpt-4o", "label": "GPT-4o"},
    "anthropic": {"model": "claude-sonnet-4-5-20250929", "label": "Claude Sonnet 4.5"},
}


class LiteLLMProvider(LLMProvider):
    """Any provider supported by LiteLLM."""

    def __init__(self, model: str, api_key: str | None = None, api_base: str | None = None) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base

        resp = await acompletion(**kwargs)

        usage = resp.usage
        input_tokens = usage.prompt_tokens or 0
        output_tokens = usage.completion_tokens or 0

        # Calculate cost via litellm's built-in pricing
        try:
            input_cost, output_cost = cost_per_token(
                model=self._model,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
            )
            cost = input_cost + output_cost
        except Exception:
            cost = 0.0

        return LLMResponse(
            text=resp.choices[0].message.content or "",
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )

    async def is_available(self) -> bool:
        try:
            resp = await acompletion(
                model=self._model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                api_key=self._api_key,
                api_base=self._api_base,
            )
            return bool(resp.choices)
        except Exception:
            log.warning("litellm.unavailable", model=self._model)
            return False
