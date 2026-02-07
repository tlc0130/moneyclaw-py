"""Tests for Smart Rebalance strategy."""

from __future__ import annotations

import pytest

from moneyclaw.execution.trading import ExchangeManager, TradeExecutor
from strategies.smart_rebalance import SmartRebalance


@pytest.fixture
def executor() -> TradeExecutor:
    return TradeExecutor(ExchangeManager(), dry_run=True)


@pytest.fixture
def strategy(executor: TradeExecutor) -> SmartRebalance:
    return SmartRebalance(
        targets={"BTC/USDT": 0.6, "ETH/USDT": 0.3, "USDT": 0.1},
        current_holdings={"BTC/USDT": 7000, "ETH/USDT": 2000, "USDT": 1000},
        executor=executor,
        deviation_threshold=0.05,
    )


class TestSmartRebalanceScan:
    async def test_detects_deviation(self, strategy: SmartRebalance) -> None:
        # BTC is 70% (target 60%), ETH is 20% (target 30%)
        opps = await strategy.scan()
        assert len(opps) == 1
        assert "Rebalance" in opps[0].title

    async def test_no_rebalance_when_balanced(self, executor: TradeExecutor) -> None:
        strategy = SmartRebalance(
            targets={"BTC/USDT": 0.6, "ETH/USDT": 0.3, "USDT": 0.1},
            current_holdings={"BTC/USDT": 6000, "ETH/USDT": 3000, "USDT": 1000},
            executor=executor,
        )
        opps = await strategy.scan()
        assert len(opps) == 0

    async def test_no_scan_without_holdings(self, executor: TradeExecutor) -> None:
        strategy = SmartRebalance(executor=executor)
        opps = await strategy.scan()
        assert len(opps) == 0


class TestSmartRebalanceEvaluate:
    async def test_score_proportional_to_deviation(self, strategy: SmartRebalance) -> None:
        opps = await strategy.scan()
        score = await strategy.evaluate(opps[0])
        assert 0 < score.value <= 1.0
        assert score.threshold == 0.4


class TestSmartRebalanceExecute:
    async def test_dry_run_execution(self, strategy: SmartRebalance) -> None:
        opps = await strategy.scan()
        result = await strategy.execute(opps[0])
        assert result.success is True
        trades = result.details["trades_executed"]
        assert len(trades) > 0
        assert all(t["dry_run"] for t in trades)

    async def test_sells_overweight_buys_underweight(self, strategy: SmartRebalance) -> None:
        opps = await strategy.scan()
        result = await strategy.execute(opps[0])
        trades = result.details["trades_executed"]

        # BTC overweight → should sell, ETH underweight → should buy
        sides = {t["symbol"]: t["side"] for t in trades}
        assert sides.get("BTC/USDT") == "sell"
        assert sides.get("ETH/USDT") == "buy"

    async def test_no_executor_fails(self) -> None:
        strategy = SmartRebalance(
            current_holdings={"BTC/USDT": 7000, "ETH/USDT": 2000, "USDT": 1000},
            executor=None,
        )
        opps = await strategy.scan()
        result = await strategy.execute(opps[0])
        assert result.success is False


class TestSmartRebalanceRefreshHoldings:
    async def test_refresh_from_exchange(self) -> None:
        from unittest.mock import AsyncMock

        em = AsyncMock(spec=ExchangeManager)
        em.get_balance.return_value = {
            "total": {"BTC": 1.5, "ETH": 10.0, "USDT": 500.0},
        }
        strategy = SmartRebalance(exchange_manager=em, deviation_threshold=0.05)
        await strategy.refresh_holdings()

        assert strategy._holdings == {
            "BTC/USDT": 1.5,
            "ETH/USDT": 10.0,
            "USDT": 500.0,
        }
        em.get_balance.assert_awaited_once_with("binance")

    async def test_refresh_no_exchange_manager(self) -> None:
        strategy = SmartRebalance(deviation_threshold=0.05)
        await strategy.refresh_holdings()
        assert strategy._holdings == {}

    async def test_refresh_error_keeps_existing(self) -> None:
        from unittest.mock import AsyncMock

        em = AsyncMock(spec=ExchangeManager)
        em.get_balance.side_effect = Exception("API error")
        strategy = SmartRebalance(
            exchange_manager=em,
            current_holdings={"BTC/USDT": 5000},
            deviation_threshold=0.05,
        )
        await strategy.refresh_holdings()
        # Holdings should remain unchanged on error
        assert strategy._holdings == {"BTC/USDT": 5000}

    async def test_scan_auto_refreshes(self) -> None:
        from unittest.mock import AsyncMock

        em = AsyncMock(spec=ExchangeManager)
        em.get_balance.return_value = {
            "total": {"BTC": 7000, "ETH": 2000, "USDT": 1000},
        }
        strategy = SmartRebalance(exchange_manager=em, deviation_threshold=0.05)
        opps = await strategy.scan()
        # Should have auto-refreshed and detected deviation
        em.get_balance.assert_awaited_once()
        assert len(opps) == 1


class TestSmartRebalanceMeta:
    def test_attributes(self) -> None:
        s = SmartRebalance()
        assert s.name == "smart_rebalance"
        assert s.risk_level == "medium"
        assert s.min_llm_layer == 2
        assert s.estimate_roi() == 1.02

    def test_set_holdings(self, executor: TradeExecutor) -> None:
        s = SmartRebalance(executor=executor)
        assert s._holdings == {}
        s.set_holdings({"BTC/USDT": 5000})
        assert s._holdings == {"BTC/USDT": 5000}
