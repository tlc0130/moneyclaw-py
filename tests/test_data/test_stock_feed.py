"""Tests for StockFeed (mocked yfinance)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from moneyclaw.data.feeds.stocks import StockFeed


@pytest.fixture
def feed() -> StockFeed:
    return StockFeed()


class TestStockFeedGetPrice:
    async def test_returns_quote(self, feed: StockFeed) -> None:
        mock_fast_info = MagicMock()
        mock_fast_info.last_price = 185.50
        mock_fast_info.last_volume = 50_000_000
        mock_fast_info.year_change = 0.25

        mock_ticker = MagicMock()
        mock_ticker.fast_info = mock_fast_info

        with patch("yfinance.Ticker", return_value=mock_ticker):
            quote = await feed.get_price("AAPL")

        assert quote is not None
        assert quote.symbol == "AAPL"
        assert quote.price == 185.50
        assert quote.volume == 50_000_000

    async def test_returns_none_on_no_price(self, feed: StockFeed) -> None:
        mock_fast_info = MagicMock()
        mock_fast_info.last_price = None

        mock_ticker = MagicMock()
        mock_ticker.fast_info = mock_fast_info

        with patch("yfinance.Ticker", return_value=mock_ticker):
            quote = await feed.get_price("INVALID")

        assert quote is None


class TestStockFeedGetInfo:
    async def test_returns_fundamentals(self, feed: StockFeed) -> None:
        mock_info = {
            "shortName": "Apple Inc.",
            "sector": "Technology",
            "trailingPE": 28.5,
            "forwardPE": 25.0,
            "marketCap": 3_000_000_000_000,
            "dividendYield": 0.005,
            "exDividendDate": 1704067200,
            "fiftyTwoWeekHigh": 200.0,
            "fiftyTwoWeekLow": 150.0,
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info

        with patch("yfinance.Ticker", return_value=mock_ticker):
            info = await feed.get_info("AAPL")

        assert info is not None
        assert info["name"] == "Apple Inc."
        assert info["pe_ratio"] == 28.5
        assert info["dividend_yield"] == 0.005


class TestStockFeedGetDividends:
    async def test_returns_dividends(self, feed: StockFeed) -> None:
        import pandas as pd

        dates = pd.to_datetime(["2024-01-15", "2024-04-15", "2024-07-15"])
        divs = pd.Series([0.24, 0.24, 0.25], index=dates)

        mock_ticker = MagicMock()
        mock_ticker.dividends = divs

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = await feed.get_dividends("AAPL")

        assert len(result) == 3
        assert result[0]["amount"] == 0.24

    async def test_empty_dividends(self, feed: StockFeed) -> None:
        import pandas as pd

        mock_ticker = MagicMock()
        mock_ticker.dividends = pd.Series(dtype=float)

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = await feed.get_dividends("TSLA")

        assert result == []
