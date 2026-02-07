"""Ollama provider — Layer 1: local models, zero API cost."""

from __future__ import annotations

import structlog
from ollama import AsyncClient

from moneyclaw.llm.providers.base import LLMProvider, LLMResponse

log = structlog.get_logger()


class OllamaProvider(LLMProvider):
    """Local LLM via Ollama. Cost: electricity only."""

    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434") -> None:
        self._model = model
        self._client = AsyncClient(host=base_url)

    @property
    def model_name(self) -> str:
        return f"ollama/{self._model}"

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

        resp = await self._client.chat(
            model=self._model,
            messages=messages,
            options={"temperature": temperature, "num_predict": max_tokens},
        )

        return LLMResponse(
            text=resp["message"]["content"],
            model=self._model,
            input_tokens=resp.get("prompt_eval_count", 0),
            output_tokens=resp.get("eval_count", 0),
            cost=0.0,  # Local = free
        )

    async def is_available(self) -> bool:
        try:
            models = await self._client.list()
            names = [m["name"] for m in models.get("models", [])]
            return any(self._model in n for n in names)
        except Exception:
            log.warning("ollama.unavailable")
            return False
