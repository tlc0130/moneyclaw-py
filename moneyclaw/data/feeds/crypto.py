from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from moneyclaw.data.feeds.base import OHLCV, Quote

log = structlog.get_logger()


class CryptoFeed:
    """Crypto market data feed backed by public HTTP APIs."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            log.warning(
                "crypto_feed.request_failed",
                url=url,
                error=str(e),
                error_type=type(e).__name__,
                error_repr=repr(e),
            )
            return None

    async def get_price(self, symbol: str) -> Quote | None:
        data = await self._get(
            "https://api.coingecko.com/api/v3/simple/price",
            {
                "ids": symbol,
                "vs_currencies": "usd",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
        )
        if not data or symbol not in data:
            return None

        entry = data[symbol]
        price = entry.get("usd")
        if price is None:
            return None

        return Quote(
            symbol=symbol,
            price=float(price),
            volume=float(entry.get("usd_24h_vol") or 0.0),
            change_24h=float(entry.get("usd_24h_change") or 0.0),
        )

    async def get_ohlcv(self, symbol: str, days: int = 30) -> list[OHLCV]:
        data = await self._get(
            f"https://api.coingecko.com/api/v3/coins/{symbol}/ohlc",
            {"vs_currency": "usd", "days": days},
        )
        if not data:
            return []

        bars: list[OHLCV] = []
        for row in data:
            if not isinstance(row, list) or len(row) < 5:
                continue
            ts_ms, open_, high, low, close = row[:5]
            bars.append(
                OHLCV(
                    timestamp=datetime.fromtimestamp(float(ts_ms) / 1000, tz=timezone.utc),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                )
            )
        return bars

    async def get_top_movers(self, limit: int = 10) -> list[Quote]:
        data = await self._get(
            "https://api.coingecko.com/api/v3/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": max(limit, 1),
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
        )
        if not data:
            return []

        quotes = [
            Quote(
                symbol=str(item.get("id", "")),
                price=float(item.get("current_price") or 0.0),
                volume=float(item.get("total_volume") or 0.0),
                change_24h=float(item.get("price_change_percentage_24h") or 0.0),
            )
            for item in data
        ]
        quotes.sort(key=lambda q: abs(q.change_24h), reverse=True)
        return quotes[:limit]

    async def get_funding_rates(self) -> list[dict[str, Any]]:
        data = await self._get("https://fapi.binance.com/fapi/v1/premiumIndex")
        if not data:
            return []

        items = data if isinstance(data, list) else [data]
        rates: list[dict[str, Any]] = []
        for item in items:
            symbol = item.get("symbol")
            funding_rate = item.get("lastFundingRate")
            if funding_rate is None:
                funding_rate = item.get("funding_rate")
            if not symbol or funding_rate is None:
                continue
            rates.append(
                {
                    "symbol": str(symbol),
                    "market": str(item.get("market") or "binance"),
                    "funding_rate": float(funding_rate or 0.0),
                    "index_price": float(item.get("indexPrice") or item.get("index") or item.get("index_price") or 0.0),
                    "open_interest": float(item.get("open_interest") or 0.0),
                    "mark_price": float(item.get("markPrice") or item.get("mark_price") or 0.0),
                    "next_funding_time": int(item.get("nextFundingTime") or item.get("next_funding_time") or 0),
                }
            )

        rates.sort(key=lambda x: abs(float(x.get("funding_rate", 0.0))), reverse=True)
        return rates

    async def search(self, query: str) -> list[dict[str, Any]]:
        data = await self._get(
            "https://api.coingecko.com/api/v3/search",
            {"query": query},
        )
        if not data:
            return []
        coins = data.get("coins")
        return coins if isinstance(coins, list) else []
