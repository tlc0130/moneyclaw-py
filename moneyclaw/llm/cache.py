"""Simple in-memory LLM response cache with TTL."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from moneyclaw.llm.providers.base import LLMResponse


@dataclass
class _CacheEntry:
    response: LLMResponse
    expires_at: float


class ResponseCache:
    """TTL-based cache for LLM responses. Avoids paying twice for the same question."""

    def __init__(self, max_size: int = 500) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._max_size = max_size

    def get(self, key: str) -> LLMResponse | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.response

    def set(self, key: str, response: LLMResponse, ttl: int) -> None:
        # Evict oldest entries if full
        if len(self._store) >= self._max_size:
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]

        self._store[key] = _CacheEntry(
            response=response,
            expires_at=time.monotonic() + ttl,
        )

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)
