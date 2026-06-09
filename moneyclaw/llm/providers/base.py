"""Base interface for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float  # USD


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this provider is reachable."""
        ...


class NoOpLLMProvider(LLMProvider):
    """Safe fallback provider for dry-run evaluation when real LLMs are unavailable."""

    def __init__(self, model_name: str = "noop/dry-run") -> None:
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        **kwargs,
    ) -> LLMResponse:
        return LLMResponse(
            text="0.55",
            model=self._model_name,
            input_tokens=0,
            output_tokens=1,
            cost=0.0,
        )

    async def is_available(self) -> bool:
        return True
