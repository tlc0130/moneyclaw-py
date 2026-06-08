"""Trading execution via ccxt — supports multiple exchanges with dry_run mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

log = structlog.get_logger()


@dataclass
class Order:
    """Represents a placed (or simulated) order."""

    id: str
    exchange: str
    symbol: str
    side: str  # "buy" or "sell"
    type: str  # "market" or "limit"
    amount: float
    price: float | None = None
    cost: float | None = None  # quote-denominated spend (for market buys by USD cost)
    filled: float = 0.0
    status: str = "open"  # open, closed, canceled, failed, blocked
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dry_run: bool = False


class ExchangeManager:
    """Manage connections to crypto exchanges via ccxt."""

    def __init__(self) -> None:
        self._exchanges: dict[str, object] = {}

    def connect(
        self, exchange_id: str, api_key: str = "", secret: str = "", password: str = ""
    ) -> None:
        """Connect to an exchange. In dry_run mode, keys can be empty.

        Uses ccxt.async_support — the executor awaits every call, so the exchange
        instance must expose coroutine methods. (A plain ``import ccxt`` gives
        synchronous methods; awaiting them raises TypeError and silently fails.)
        """
        import ccxt.async_support as ccxt

        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Unknown exchange: {exchange_id}")
        config: dict[str, object] = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
        }
        if password:
            config["password"] = password
        self._exchanges[exchange_id] = exchange_class(config)
        log.info("exchange.connected", exchange=exchange_id)

    async def close_all(self) -> None:
        """Close all async ccxt sessions (aiohttp connectors). Call on shutdown."""
        for exchange_id, ex in self._exchanges.items():
            try:
                await ex.close()  # type: ignore[union-attr]
            except Exception:
                log.warning("exchange.close_error", exchange=exchange_id)

    def get(self, exchange_id: str) -> object:
        """Get a connected exchange instance."""
        if exchange_id not in self._exchanges:
            raise ValueError(f"Exchange not connected: {exchange_id}")
        return self._exchanges[exchange_id]

    async def get_balance(self, exchange_id: str) -> dict:
        """Fetch account balance from exchange."""
        ex = self.get(exchange_id)
        return await ex.fetch_balance()  # type: ignore[union-attr]

    async def get_available_quote_balance(
        self,
        exchange_id: str,
        quote_currencies: tuple[str, ...] = ("USD", "USDT", "USDC"),
    ) -> float:
        """Return the FREE (deployable) balance across the given quote currencies.

        This is what live position sizing should be based on — actual spendable
        funds on the exchange, not a hardcoded paper balance.
        """
        ex = self.get(exchange_id)
        balance = await ex.fetch_balance()  # type: ignore[union-attr]
        free = balance.get("free", {}) or {}
        total = 0.0
        for currency in quote_currencies:
            try:
                total += float(free.get(currency, 0) or 0)
            except (TypeError, ValueError):
                continue
        return total

    async def get_total_value(self, exchange_id: str) -> float:
        """Calculate total portfolio value in USD."""
        try:
            balance = await self.get_balance(exchange_id)
            total = balance.get("total", {})
            # Sum up all non-zero balances
            # In real implementation, we'd convert each to USD using current prices
            usd_value = total.get("USDT", 0) + total.get("USD", 0) + total.get("BUSD", 0)

            # For crypto assets, we'd need price conversion
            # For now, return a simplified calculation
            total_value = usd_value
            for asset, amount in total.items():
                if asset not in ["USDT", "USD", "BUSD"] and amount > 0:
                    # In dry_run mode, use placeholder values
                    # In live mode, fetch prices and calculate
                    total_value += amount * 0  # Would multiply by price

            return total_value
        except Exception as e:
            log.warning("exchange.balance_error", exchange=exchange_id, error=str(e))
            return 0.0

    @property
    def connected(self) -> list[str]:
        return list(self._exchanges)


class TradeExecutor:
    """Execute trades through exchanges or simulate in dry_run mode.

    IMPORTANT: dry_run=True by default. No real orders without explicit opt-in.
    """

    def __init__(
        self,
        exchange_manager: ExchangeManager,
        dry_run: bool = True,
        default_exchange: str = "binanceus",
        max_order_usd: float | None = None,
    ) -> None:
        self._em = exchange_manager
        self._dry_run = dry_run
        self._default_exchange = default_exchange
        # Hard per-order USD notional cap. None = disabled. Guards against a
        # mis-sized order (e.g. base/quote unit mix-ups) draining the account.
        self._max_order_usd = max_order_usd
        self._order_counter = 0
        self._orders: list[Order] = []

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def default_exchange(self) -> str:
        return self._default_exchange

    @property
    def max_order_usd(self) -> float | None:
        return self._max_order_usd

    @property
    def exchange_manager(self) -> ExchangeManager:
        return self._em

    def _next_id(self) -> str:
        self._order_counter += 1
        return f"dry_{self._order_counter}" if self._dry_run else f"ord_{self._order_counter}"

    async def market_buy(self, exchange_id: str, symbol: str, amount: float) -> Order:
        """Place a market buy order by BASE quantity (e.g. 0.01 BTC), or simulate."""
        return await self._place("buy", "market", exchange_id, symbol, amount)

    async def market_buy_cost(self, exchange_id: str, symbol: str, cost_usd: float) -> Order:
        """Market buy by QUOTE cost — spend ``cost_usd`` of the quote currency.

        This is what $-denominated strategies (DCA, rebalance) actually want:
        "spend $10 on BTC", not "buy 10 BTC". On Binance/Binance.US this maps to a
        ``quoteOrderQty`` market order so the exchange computes the base quantity.
        """
        return await self._place(
            "buy", "market", exchange_id, symbol, amount=None, price=None, cost=cost_usd
        )

    async def market_sell(self, exchange_id: str, symbol: str, amount: float) -> Order:
        """Place a market sell order (or simulate)."""
        return await self._place("sell", "market", exchange_id, symbol, amount)

    async def limit_buy(self, exchange_id: str, symbol: str, amount: float, price: float) -> Order:
        """Place a limit buy order (or simulate)."""
        return await self._place("buy", "limit", exchange_id, symbol, amount, price)

    async def limit_sell(self, exchange_id: str, symbol: str, amount: float, price: float) -> Order:
        """Place a limit sell order (or simulate)."""
        return await self._place("sell", "limit", exchange_id, symbol, amount, price)

    async def _place(
        self,
        side: str,
        order_type: str,
        exchange_id: str,
        symbol: str,
        amount: float | None = None,
        price: float | None = None,
        cost: float | None = None,
    ) -> Order:
        order = Order(
            id=self._next_id(),
            exchange=exchange_id,
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount or 0.0,
            price=price,
            cost=cost,
            dry_run=self._dry_run,
        )

        # --- Hard safety cap (USD notional) ---
        # Resolve a USD notional. For base-quantity orders with no price (e.g. a
        # market order sized by risk), fetch the live price so the cap still applies.
        notional = cost if cost is not None else (amount * price if (amount and price) else None)
        if (
            self._max_order_usd is not None
            and notional is None
            and amount
            and not self._dry_run
        ):
            try:
                ex = self._em.get(exchange_id)
                ticker = await ex.fetch_ticker(symbol)  # type: ignore[union-attr]
                last = ticker.get("last") or ticker.get("close")
                if last:
                    notional = amount * float(last)
            except Exception:
                # Fail safe: if we can't price it, don't risk an uncapped live order.
                order.status = "blocked"
                log.warning("trade.cap_price_unavailable", symbol=symbol, side=side)
                self._orders.append(order)
                return order
        if (
            self._max_order_usd is not None
            and notional is not None
            and notional > self._max_order_usd
        ):
            order.status = "blocked"
            log.warning(
                "trade.blocked_by_cap",
                notional=notional,
                cap=self._max_order_usd,
                symbol=symbol,
                side=side,
            )
            self._orders.append(order)
            return order

        if self._dry_run:
            order.filled = amount or 0.0
            order.status = "closed"
            log.info(
                "trade.dry_run",
                side=side,
                type=order_type,
                symbol=symbol,
                amount=amount,
                cost=cost,
                price=price,
            )
        else:
            ex = self._em.get(exchange_id)
            try:
                if cost is not None:
                    # Quote-denominated market buy (spend `cost` of quote currency).
                    if getattr(ex, "has", {}).get("createMarketBuyOrderWithCost"):
                        result = await ex.create_market_buy_order_with_cost(symbol, cost)  # type: ignore[union-attr]
                    else:
                        result = await ex.create_order(  # type: ignore[union-attr]
                            symbol, "market", side, cost, None, {"quoteOrderQty": cost}
                        )
                elif order_type == "market":
                    result = await ex.create_order(symbol, "market", side, amount)  # type: ignore[union-attr]
                else:
                    result = await ex.create_order(symbol, "limit", side, amount, price)  # type: ignore[union-attr]
                order.id = str(result.get("id", order.id))
                order.filled = float(result.get("filled", 0) or 0)
                order.status = result.get("status", "open") or "open"
                log.info("trade.placed", order_id=order.id, status=order.status, symbol=symbol)
            except Exception:
                order.status = "failed"
                log.exception("trade.error", symbol=symbol, side=side)

        self._orders.append(order)
        return order

    async def get_open_orders(self, exchange_id: str) -> list[Order]:
        """Get open orders — from local list in dry_run, from exchange otherwise."""
        if self._dry_run:
            return [o for o in self._orders if o.exchange == exchange_id and o.status == "open"]
        ex = self._em.get(exchange_id)
        open_orders = await ex.fetch_open_orders()  # type: ignore[union-attr]
        return [
            Order(
                id=str(o["id"]),
                exchange=exchange_id,
                symbol=o.get("symbol", ""),
                side=o.get("side", ""),
                type=o.get("type", ""),
                amount=float(o.get("amount", 0)),
                price=float(o["price"]) if o.get("price") else None,
                filled=float(o.get("filled", 0)),
                status=o.get("status", "open"),
            )
            for o in open_orders
        ]

    async def cancel_order(self, exchange_id: str, order_id: str) -> bool:
        """Cancel an order."""
        if self._dry_run:
            for o in self._orders:
                if o.id == order_id and o.status == "open":
                    o.status = "canceled"
                    return True
            return False
        try:
            ex = self._em.get(exchange_id)
            await ex.cancel_order(order_id)  # type: ignore[union-attr]
            return True
        except Exception:
            log.exception("trade.cancel_error", order_id=order_id)
            return False

    @property
    def order_history(self) -> list[Order]:
        return list(self._orders)
