"""Crypto price feed via CoinGecko (no API key required)."""

from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from .base import Quote

log = structlog.get_logger()

_COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
_TIMEOUT = 10.0


class CryptoFeed:
    """Fetch spot prices from CoinGecko."""

    async def get_price(self, coin_id: str) -> Optional[Quote]:
        """Return a Quote for *coin_id* (CoinGecko ID, e.g. 'bitcoin')."""
        try:
            import aiohttp

            params = {"ids": coin_id, "vs_currencies": "usd", "include_24hr_vol": "true"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _COINGECKO_URL, params=params, timeout=aiohttp.ClientTimeout(total=_TIMEOUT)
                ) as resp:
                    data = await resp.json()
            entry = data.get(coin_id, {})
            price = float(entry.get("usd", 0))
            volume = float(entry.get("usd_24h_vol", 0))
            if price <= 0:
                return None
            return Quote(symbol=coin_id.upper(), price=price, volume=volume)
        except Exception as exc:
            log.warning("crypto_feed.get_price_failed", coin=coin_id, error=str(exc))
            return None

    async def get_prices_bulk(self, coin_ids: list[str]) -> dict[str, Quote]:
        """Return a mapping of coin_id → Quote for multiple coins."""
        results = await asyncio.gather(*[self.get_price(c) for c in coin_ids])
        return {cid: q for cid, q in zip(coin_ids, results) if q}
