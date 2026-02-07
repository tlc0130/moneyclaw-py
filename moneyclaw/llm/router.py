"""Four-layer LLM routing — pick the cheapest model that can handle the task."""

from __future__ import annotations

import hashlib
import time

import structlog

from moneyclaw.llm.cache import ResponseCache
from moneyclaw.llm.cost_tracker import CostTracker
from moneyclaw.llm.providers.base import LLMProvider, LLMResponse
from moneyclaw.llm.types import LLMLayer, TaskRequest

log = structlog.get_logger()

# Re-export for convenience
__all__ = ["LLMLayer", "LLMRouter", "TaskRequest"]


class LLMRouter:
    """Routes tasks to the cheapest adequate LLM layer.

    Routing logic:
    - money_involved < $10 and complexity < 0.3 → Layer 1 (local)
    - money_involved < $100 or complexity < 0.6 → Layer 2 (cheap API)
    - Otherwise → Layer 3 (premium)
    - Always respects min_layer / max_layer bounds
    """

    def __init__(
        self,
        providers: dict[LLMLayer, LLMProvider],
        cost_tracker: CostTracker,
        cache: ResponseCache | None = None,
    ) -> None:
        self._providers = providers
        self._cost_tracker = cost_tracker
        self._cache = cache or ResponseCache()

    @property
    def cost_tracker(self) -> CostTracker:
        return self._cost_tracker

    def select_layer(self, request: TaskRequest) -> LLMLayer:
        """Determine which layer should handle this request."""
        if request.money_involved >= 100 or request.complexity >= 0.6:
            ideal = LLMLayer.PREMIUM
        elif request.money_involved >= 10 or request.complexity >= 0.3:
            ideal = LLMLayer.CHEAP
        else:
            ideal = LLMLayer.LOCAL

        # Clamp to allowed range
        layer = max(request.min_layer, min(ideal, request.max_layer))

        # Fall back down if the chosen provider isn't available
        while layer > request.min_layer and layer not in self._providers:
            layer = LLMLayer(layer - 1)
        # Fall back up if still missing
        while layer < request.max_layer and layer not in self._providers:
            layer = LLMLayer(layer + 1)

        return layer

    async def complete(self, request: TaskRequest) -> LLMResponse:
        """Route the request to the appropriate LLM and return the response."""
        # Check cache first
        if request.cache_ttl > 0:
            cache_key = self._cache_key(request)
            cached = self._cache.get(cache_key)
            if cached is not None:
                log.debug("llm.cache_hit", key=cache_key[:12])
                return cached

        layer = self.select_layer(request)
        provider = self._providers.get(layer)
        if provider is None:
            raise RuntimeError(f"No provider configured for layer {layer.name}")

        log.info(
            "llm.routing",
            layer=layer.name,
            model=provider.model_name,
            money=request.money_involved,
            complexity=request.complexity,
        )

        start = time.monotonic()
        response = await provider.complete(
            prompt=request.prompt,
            system=request.system,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        elapsed = time.monotonic() - start

        # Track cost
        self._cost_tracker.record(
            layer=layer,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost=response.cost,
            latency=elapsed,
        )

        # Cache if requested
        if request.cache_ttl > 0:
            self._cache.set(cache_key, response, ttl=request.cache_ttl)

        return response

    @staticmethod
    def _cache_key(request: TaskRequest) -> str:
        raw = f"{request.system}|{request.prompt}|{request.temperature}"
        return hashlib.sha256(raw.encode()).hexdigest()
