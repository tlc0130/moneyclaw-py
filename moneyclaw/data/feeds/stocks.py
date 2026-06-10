from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
import yfinance as yf

from moneyclaw.data.feeds.base import Quote

log = structlog.get_logger()


class StockFeed:
    """Stock market data feed backed by yfinance."""

    async def get_price(self, symbol: str) -> Quote | None:
        try:
            ticker = yf.Ticker(symbol)
            fast_info = getattr(ticker, "fast_info", None)
            price = getattr(fast_info, "last_price", None) if fast_info else None
            if price is None:
                return None

            year_change = getattr(fast_info, "year_change", 0.0) if fast_info else 0.0
            last_volume = getattr(fast_info, "last_volume", 0.0) if fast_info else 0.0
            return Quote(
                symbol=symbol,
                price=float(price),
                timestamp=datetime.now(timezone.utc),
                volume=float(last_volume or 0.0),
                change_24h=float(year_change or 0.0),
            )
        except Exception as e:
            log.warning("stock_feed.price_failed", symbol=symbol, error=str(e))
            return None

    async def get_info(self, symbol: str) -> dict[str, Any] | None:
        try:
            ticker = yf.Ticker(symbol)
            info = getattr(ticker, "info", None)
            if not info:
                return None

            dividend_yield = info.get("dividendYield")
            if isinstance(dividend_yield, (int, float)) and dividend_yield > 1:
                dividend_yield = float(dividend_yield) / 100.0

            return {
                "symbol": symbol,
                "name": info.get("shortName"),
                "sector": info.get("sector"),
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "market_cap": info.get("marketCap"),
                "dividend_yield": dividend_yield,
                "ex_dividend_date": info.get("exDividendDate"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            }
        except Exception as e:
            log.warning("stock_feed.info_failed", symbol=symbol, error=str(e))
            return None

    async def get_dividends(self, symbol: str) -> list[dict[str, Any]]:
        try:
            ticker = yf.Ticker(symbol)
            dividends = getattr(ticker, "dividends", None)
            if dividends is None or len(dividends) == 0:
                return []

            results: list[dict[str, Any]] = []
            for index, amount in dividends.items():
                ts = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
                results.append(
                    {
                        "date": ts.date().isoformat() if hasattr(ts, "date") else str(ts),
                        "amount": float(amount),
                    }
                )
            return results
        except Exception as e:
            log.warning("stock_feed.dividends_failed", symbol=symbol, error=str(e))
            return []
