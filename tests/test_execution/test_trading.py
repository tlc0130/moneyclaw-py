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

    def test_connect_uses_async_ccxt(self) -> None:
        """RC1 regression: the executor awaits every ccxt call, so connect() MUST
        produce an async (ccxt.async_support) exchange whose methods are coroutines.

        With the old synchronous `import ccxt`, create_order/fetch_balance are plain
        functions; awaiting them raises TypeError, which the executor swallowed,
        marking every live order 'failed' and silently never trading.
        """
        import inspect

        em = ExchangeManager()
        em.connect("binanceus")  # no keys needed just to instantiate
        ex = em.get("binanceus")
        assert inspect.iscoroutinefunction(ex.create_order), (
            "create_order must be a coroutine (use ccxt.async_support)"
        )
        assert inspect.iscoroutinefunction(ex.fetch_balance), (
            "fetch_balance must be a coroutine (use ccxt.async_support)"
        )


class TestExecutorSafetyAndSizing:
    async def test_default_exchange_is_exposed(self) -> None:
        em = ExchangeManager()
        ex = TradeExecutor(em, dry_run=True, default_exchange="binanceus")
        assert ex.default_exchange == "binanceus"

    async def test_market_buy_cost_dry_run(self) -> None:
        """RC4: buying a USD *cost* (quote amount), not a base quantity."""
        em = ExchangeManager()
        ex = TradeExecutor(em, dry_run=True, default_exchange="binanceus")
        order = await ex.market_buy_cost("binanceus", "BTC/USDT", 10.0)
        assert order.side == "buy"
        assert order.type == "market"
        assert order.dry_run is True
        assert order.status == "closed"
        assert order.cost == 10.0

    async def test_per_order_usd_cap_blocks_oversized(self) -> None:
        """Hard safety guard: a BUY above max_order_usd must not place."""
        em = ExchangeManager()
        ex = TradeExecutor(em, dry_run=True, default_exchange="binanceus", max_order_usd=25.0)
        order = await ex.market_buy_cost("binanceus", "BTC/USDT", 1000.0)
        assert order.status == "blocked"
        assert order.filled == 0.0

    async def test_cap_never_blocks_a_sell(self) -> None:
        """Sells/exits reduce risk and must never be blocked by the USD cap."""

        class _FakeSell:
            async def create_order(self, *a, **k):
                return {"id": "s1", "filled": a[3] if len(a) > 3 else 0, "status": "closed"}

            async def close(self):
                pass

        em = ExchangeManager()
        em._exchanges["binanceus"] = _FakeSell()
        ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus", max_order_usd=25.0)
        # Huge sell at an explicit price (notional far above the $25 cap) must still place.
        order = await ex.limit_sell("binanceus", "BTC/USDT", 1.0, 95000.0)
        assert order.status != "blocked"


class _FakeBalanceExchange:
    """Minimal async ccxt stand-in exposing fetch_balance."""

    async def fetch_balance(self) -> dict:
        return {"free": {"USD": 30.0, "USDT": 12.5, "BTC": 0.01}, "total": {}}

    async def close(self) -> None:  # pragma: no cover - cleanup hook
        pass


class TestAvailableQuoteBalance:
    async def test_sums_free_quote_currencies(self) -> None:
        em = ExchangeManager()
        em._exchanges["binanceus"] = _FakeBalanceExchange()  # inject without network
        free = await em.get_available_quote_balance("binanceus", ("USD", "USDT", "USDC"))
        assert free == pytest.approx(42.5)  # 30 USD + 12.5 USDT; BTC ignored


class _FakeStopExchange:
    """Async ccxt stand-in that records stop orders and reports their status."""

    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.next_status = "open"

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return f"{amount:.6f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        return f"{price:.2f}"

    async def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        rec = {
            "id": f"stop_{len(self.orders) + 1}",
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params or {},
            "status": "open",
        }
        self.orders.append(rec)
        return rec

    async def fetch_order(self, order_id, symbol=None):
        return {"id": order_id, "status": self.next_status}

    async def close(self) -> None:  # pragma: no cover
        pass


class TestStopLoss:
    async def test_place_stop_loss_dry_run(self) -> None:
        ex = TradeExecutor(ExchangeManager(), dry_run=True, default_exchange="binanceus")
        order = await ex.place_stop_loss("binanceus", "BTC/USDT", 0.01, 95000.0)
        assert order.type == "stop"
        assert order.side == "sell"
        assert order.status == "open"
        assert order.dry_run is True

    async def test_place_stop_loss_live(self) -> None:
        em = ExchangeManager()
        fake = _FakeStopExchange()
        em._exchanges["binanceus"] = fake
        ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus", max_order_usd=25.0)
        order = await ex.place_stop_loss("binanceus", "BTC/USDT", 0.01, 95000.0)
        # A stop reduces risk: the USD cap must NOT block it even though notional > cap.
        assert order.status == "open"
        assert order.id == "stop_1"
        placed = fake.orders[0]
        assert placed["type"] == "STOP_LOSS_LIMIT"
        assert placed["side"] == "sell"
        assert placed["params"]["stopPrice"] == pytest.approx(95000.0, rel=1e-4)
        # limit price sits below the stop so it stays marketable on a fast drop
        assert placed["price"] < placed["params"]["stopPrice"]

    async def test_get_order_status_live(self) -> None:
        em = ExchangeManager()
        fake = _FakeStopExchange()
        fake.next_status = "closed"
        em._exchanges["binanceus"] = fake
        ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus")
        status = await ex.get_order_status("binanceus", "stop_1", "BTC/USDT")
        assert status == "closed"


class _FakeMarketExchange:
    """Async ccxt stand-in with lot-size precision and min limits."""

    def __init__(self, last=100000.0, min_amount=0.0001, min_cost=10.0, step=0.0001):
        self._last = last
        self._min_amount = min_amount
        self._min_cost = min_cost
        self._step = step
        self.markets = {"BTC/USDT": {}}
        self.placed: list[tuple[str, float]] = []

    async def load_markets(self):  # pragma: no cover - markets pre-populated
        return self.markets

    def market(self, symbol):
        return {"limits": {"amount": {"min": self._min_amount}, "cost": {"min": self._min_cost}}}

    def amount_to_precision(self, symbol, amount):
        import math

        return f"{math.floor(amount / self._step) * self._step:.8f}"

    async def fetch_ticker(self, symbol):
        return {"last": self._last}

    async def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        self.placed.append((side, amount))
        return {"id": "m1", "filled": amount, "status": "closed"}

    async def close(self):  # pragma: no cover
        pass


class TestLotSizeNormalization:
    async def test_market_buy_rounds_to_step(self) -> None:
        em = ExchangeManager()
        fake = _FakeMarketExchange(step=0.0001)
        em._exchanges["binanceus"] = fake
        ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus")
        order = await ex.market_buy("binanceus", "BTC/USDT", 0.0013372)
        assert order.status == "closed"
        assert fake.placed[0][1] == pytest.approx(0.0013)  # floored to step

    async def test_market_buy_below_min_qty_rejected(self) -> None:
        em = ExchangeManager()
        fake = _FakeMarketExchange(min_amount=0.001, step=0.0001)
        em._exchanges["binanceus"] = fake
        ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus")
        order = await ex.market_buy("binanceus", "BTC/USDT", 0.0002)  # < minQty 0.001
        assert order.status == "rejected"
        assert fake.placed == []  # never sent to the exchange

    async def test_market_buy_below_min_notional_rejected(self) -> None:
        em = ExchangeManager()
        # step 1e-5 so qty passes minQty, but $8 notional < $10 min cost
        fake = _FakeMarketExchange(last=100000.0, min_amount=1e-5, min_cost=10.0, step=1e-5)
        em._exchanges["binanceus"] = fake
        ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus")
        order = await ex.market_buy("binanceus", "BTC/USDT", 0.00008)  # $8
        assert order.status == "rejected"
        assert fake.placed == []

    async def test_market_sell_rounds_but_never_rejected(self) -> None:
        em = ExchangeManager()
        fake = _FakeMarketExchange(min_amount=0.001, step=0.0001)
        em._exchanges["binanceus"] = fake
        ex = TradeExecutor(em, dry_run=False, default_exchange="binanceus")
        # Tiny sell below minQty still attempts (exiting must not be blocked here)
        order = await ex.market_sell("binanceus", "BTC/USDT", 0.0123456)
        assert order.status != "rejected"
        assert fake.placed[0][1] == pytest.approx(0.0123)
