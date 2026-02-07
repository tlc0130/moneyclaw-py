"""Tests for TradeExecutor in dry_run mode."""

from __future__ import annotations

import pytest

from moneyclaw.execution.trading import ExchangeManager, Order, TradeExecutor


@pytest.fixture
def executor() -> TradeExecutor:
    em = ExchangeManager()
    return TradeExecutor(em, dry_run=True)


class TestOrder:
    def test_order_creation(self) -> None:
        order = Order(
            id="test_1",
            exchange="binance",
            symbol="BTC/USDT",
            side="buy",
            type="market",
            amount=0.01,
        )
        assert order.status == "open"
        assert order.filled == 0.0
        assert order.dry_run is False


class TestTradeExecutorDryRun:
    async def test_market_buy(self, executor: TradeExecutor) -> None:
        order = await executor.market_buy("binance", "BTC/USDT", 100.0)
        assert order.dry_run is True
        assert order.side == "buy"
        assert order.type == "market"
        assert order.amount == 100.0
        assert order.filled == 100.0  # Dry run fills immediately
        assert order.status == "closed"

    async def test_market_sell(self, executor: TradeExecutor) -> None:
        order = await executor.market_sell("binance", "ETH/USDT", 50.0)
        assert order.side == "sell"
        assert order.filled == 50.0
        assert order.status == "closed"

    async def test_limit_buy(self, executor: TradeExecutor) -> None:
        order = await executor.limit_buy("binance", "BTC/USDT", 0.01, 60000.0)
        assert order.type == "limit"
        assert order.price == 60000.0
        assert order.status == "closed"  # Dry run completes immediately

    async def test_limit_sell(self, executor: TradeExecutor) -> None:
        order = await executor.limit_sell("binance", "ETH/USDT", 1.0, 3500.0)
        assert order.type == "limit"
        assert order.side == "sell"
        assert order.price == 3500.0

    async def test_order_history(self, executor: TradeExecutor) -> None:
        await executor.market_buy("binance", "BTC/USDT", 100)
        await executor.market_sell("binance", "ETH/USDT", 50)
        assert len(executor.order_history) == 2

    async def test_sequential_order_ids(self, executor: TradeExecutor) -> None:
        o1 = await executor.market_buy("binance", "BTC/USDT", 10)
        o2 = await executor.market_buy("binance", "BTC/USDT", 20)
        assert o1.id == "dry_1"
        assert o2.id == "dry_2"

    async def test_no_open_orders_in_dry_run(self, executor: TradeExecutor) -> None:
        await executor.market_buy("binance", "BTC/USDT", 100)
        open_orders = await executor.get_open_orders("binance")
        assert len(open_orders) == 0  # All dry_run orders close immediately

    async def test_cancel_nonexistent(self, executor: TradeExecutor) -> None:
        result = await executor.cancel_order("binance", "nonexistent")
        assert result is False


class TestExchangeManager:
    def test_connected_initially_empty(self) -> None:
        em = ExchangeManager()
        assert em.connected == []

    def test_get_unconnected_raises(self) -> None:
        em = ExchangeManager()
        with pytest.raises(ValueError, match="not connected"):
            em.get("binance")
