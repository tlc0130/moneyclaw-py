"""Tests for the LLM four-layer router."""

from __future__ import annotations

import pytest

from moneyclaw.llm.cost_tracker import CostTracker
from moneyclaw.llm.providers.base import LLMProvider, LLMResponse
from moneyclaw.llm.router import LLMLayer, LLMRouter, TaskRequest


class MockProvider(LLMProvider):
    """Mock LLM provider for testing."""

    def __init__(self, name: str = "mock") -> None:
        self._name = name

    @property
    def model_name(self) -> str:
        return self._name

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        return LLMResponse(
            text=f"response from {self._name}",
            model=self._name,
            input_tokens=10,
            output_tokens=20,
            cost=0.001,
        )

    async def is_available(self) -> bool:
        return True


def make_router(layers: list[LLMLayer] | None = None) -> LLMRouter:
    if layers is None:
        layers = [LLMLayer.LOCAL, LLMLayer.CHEAP, LLMLayer.PREMIUM]
    providers = {layer: MockProvider(layer.name) for layer in layers}
    return LLMRouter(providers=providers, cost_tracker=CostTracker())


class TestRouterLayerSelection:
    def test_small_simple_goes_local(self) -> None:
        router = make_router()
        req = TaskRequest(prompt="test", money_involved=5, complexity=0.1)
        assert router.select_layer(req) == LLMLayer.LOCAL

    def test_medium_goes_cheap(self) -> None:
        router = make_router()
        req = TaskRequest(prompt="test", money_involved=50, complexity=0.4)
        assert router.select_layer(req) == LLMLayer.CHEAP

    def test_high_money_goes_premium(self) -> None:
        router = make_router()
        req = TaskRequest(prompt="test", money_involved=500, complexity=0.2)
        assert router.select_layer(req) == LLMLayer.PREMIUM

    def test_high_complexity_goes_premium(self) -> None:
        router = make_router()
        req = TaskRequest(prompt="test", money_involved=5, complexity=0.8)
        assert router.select_layer(req) == LLMLayer.PREMIUM

    def test_min_layer_respected(self) -> None:
        router = make_router()
        req = TaskRequest(
            prompt="test",
            money_involved=1,
            complexity=0.1,
            min_layer=LLMLayer.CHEAP,
        )
        assert router.select_layer(req) == LLMLayer.CHEAP

    def test_max_layer_respected(self) -> None:
        router = make_router()
        req = TaskRequest(
            prompt="test",
            money_involved=1000,
            complexity=0.9,
            max_layer=LLMLayer.CHEAP,
        )
        assert router.select_layer(req) == LLMLayer.CHEAP

    def test_fallback_when_layer_missing(self) -> None:
        # Only LOCAL and PREMIUM available — CHEAP request should fall back
        router = make_router([LLMLayer.LOCAL, LLMLayer.PREMIUM])
        req = TaskRequest(prompt="test", money_involved=50, complexity=0.4)
        # Should go to LOCAL (fallback down) since CHEAP is missing
        layer = router.select_layer(req)
        assert layer in (LLMLayer.LOCAL, LLMLayer.PREMIUM)


class TestRouterComplete:
    @pytest.mark.asyncio
    async def test_complete_returns_response(self) -> None:
        router = make_router()
        req = TaskRequest(prompt="hello")
        resp = await router.complete(req)
        assert resp.text.startswith("response from")
        assert resp.input_tokens == 10

    @pytest.mark.asyncio
    async def test_complete_tracks_cost(self) -> None:
        router = make_router()
        req = TaskRequest(prompt="hello")
        await router.complete(req)
        assert router.cost_tracker.today_calls == 1
        assert router.cost_tracker.today_cost > 0

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        router = make_router()
        req = TaskRequest(prompt="cached_query", cache_ttl=300)
        resp1 = await router.complete(req)
        resp2 = await router.complete(req)
        # Second call should be cached — only 1 actual call tracked
        assert router.cost_tracker.today_calls == 1
        assert resp1.text == resp2.text
