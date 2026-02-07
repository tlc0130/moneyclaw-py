"""Tests for the strategy plugin registry."""

from __future__ import annotations

import pytest

from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy
from moneyclaw.plugins.registry import StrategyRegistry


class DummyStrategy(Strategy):
    name = "dummy"
    description = "A test strategy"
    risk_level = "low"
    min_llm_layer = 0

    async def scan(self) -> list[Opportunity]:
        return [Opportunity(strategy_name="dummy", title="Test opp", money_involved=10)]

    async def evaluate(self, opp: Opportunity) -> Score:
        return Score(value=0.9)

    async def execute(self, opp: Opportunity) -> Result:
        return Result(success=True, profit_loss=1.0)

    def estimate_roi(self) -> float:
        return 1.5


class TestStrategyRegistry:
    @pytest.mark.asyncio
    async def test_register_and_list(self) -> None:
        registry = StrategyRegistry()
        await registry.register(DummyStrategy())
        assert len(registry.active) == 1
        assert registry.get("dummy") is not None

    @pytest.mark.asyncio
    async def test_enable_disable(self) -> None:
        registry = StrategyRegistry()
        await registry.register(DummyStrategy())
        assert registry.is_enabled("dummy")

        registry.disable("dummy")
        assert not registry.is_enabled("dummy")
        assert len(registry.active) == 0

        registry.enable("dummy")
        assert registry.is_enabled("dummy")
        assert len(registry.active) == 1

    @pytest.mark.asyncio
    async def test_status_report(self) -> None:
        registry = StrategyRegistry()
        await registry.register(DummyStrategy())
        statuses = registry.status()
        assert len(statuses) == 1
        assert statuses[0]["name"] == "dummy"
        assert statuses[0]["roi_estimate"] == 1.5
