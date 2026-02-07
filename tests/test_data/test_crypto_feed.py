"""Tests for CryptoFeed (mocked HTTP via patching _get)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from moneyclaw.data.feeds.crypto import CryptoFeed


@pytest.fixture
def feed() -> CryptoFeed:
    return CryptoFeed()


class TestCryptoFeedGetPrice:
    async def test_returns_quote(self, feed: CryptoFeed) -> None:
        mock_data = {"bitcoin": {"usd": 65000.0, "usd_24h_vol": 30e9, "usd_24h_change": 2.5}}
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=mock_data):
            quote = await feed.get_price("bitcoin")

        assert quote is not None
        assert quote.symbol == "bitcoin"
        assert quote.price == 65000.0
        assert quote.volume == 30e9
        assert quote.change_24h == 2.5

    async def test_returns_none_on_missing_symbol(self, feed: CryptoFeed) -> None:
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value={}):
            quote = await feed.get_price("nonexistent")
        assert quote is None

    async def test_returns_none_on_error(self, feed: CryptoFeed) -> None:
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=None):
            quote = await feed.get_price("bitcoin")
        assert quote is None


class TestCryptoFeedGetOHLCV:
    async def test_parses_ohlcv_data(self, feed: CryptoFeed) -> None:
        mock_data = [
            [1704067200000, 42000.0, 43000.0, 41000.0, 42500.0],
            [1704153600000, 42500.0, 44000.0, 42000.0, 43500.0],
        ]
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=mock_data):
            bars = await feed.get_ohlcv("bitcoin", days=7)

        assert len(bars) == 2
        assert bars[0].open == 42000.0
        assert bars[0].close == 42500.0
        assert bars[1].high == 44000.0

    async def test_empty_on_error(self, feed: CryptoFeed) -> None:
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=None):
            bars = await feed.get_ohlcv("bitcoin")
        assert bars == []

    async def test_skips_short_rows(self, feed: CryptoFeed) -> None:
        mock_data = [[1704067200000, 42000.0], [1704153600000, 42500.0, 44000.0, 42000.0, 43500.0]]
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=mock_data):
            bars = await feed.get_ohlcv("bitcoin")
        assert len(bars) == 1


class TestCryptoFeedTopMovers:
    async def test_sorted_by_change(self, feed: CryptoFeed) -> None:
        mock_data = [
            {
                "id": "bitcoin",
                "current_price": 65000,
                "total_volume": 30e9,
                "price_change_percentage_24h": 2.0,
            },
            {
                "id": "ethereum",
                "current_price": 3000,
                "total_volume": 15e9,
                "price_change_percentage_24h": -5.0,
            },
            {
                "id": "solana",
                "current_price": 100,
                "total_volume": 5e9,
                "price_change_percentage_24h": 8.0,
            },
        ]
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=mock_data):
            movers = await feed.get_top_movers(limit=10)

        assert len(movers) == 3
        # Sorted by abs(change): solana(8), ethereum(-5), bitcoin(2)
        assert movers[0].symbol == "solana"
        assert movers[1].symbol == "ethereum"

    async def test_empty_on_error(self, feed: CryptoFeed) -> None:
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=None):
            movers = await feed.get_top_movers()
        assert movers == []


class TestCryptoFeedFundingRates:
    async def test_parses_rates(self, feed: CryptoFeed) -> None:
        mock_data = [
            {
                "symbol": "BTCUSDT",
                "market": "binance",
                "funding_rate": 0.0015,
                "index": 65000,
                "open_interest": 1e9,
            },
            {
                "symbol": "ETHUSDT",
                "market": "binance",
                "funding_rate": 0.0008,
                "index": 3000,
                "open_interest": 5e8,
            },
            {"symbol": "SOLUSDT", "market": "okx"},  # No funding_rate
        ]
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=mock_data):
            rates = await feed.get_funding_rates()

        assert len(rates) == 2
        assert rates[0]["symbol"] == "BTCUSDT"
        assert rates[0]["funding_rate"] == 0.0015

    async def test_empty_on_error(self, feed: CryptoFeed) -> None:
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=None):
            rates = await feed.get_funding_rates()
        assert rates == []


class TestCryptoFeedSearch:
    async def test_search(self, feed: CryptoFeed) -> None:
        mock_data = {
            "coins": [
                {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"},
                {"id": "bitcoin-cash", "symbol": "bch", "name": "Bitcoin Cash"},
            ]
        }
        with patch.object(feed, "_get", new_callable=AsyncMock, return_value=mock_data):
            results = await feed.search("bitcoin")

        assert len(results) == 2
        assert results[0]["id"] == "bitcoin"
