"""Tests for DuckDB market data storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from moneyclaw.data.feeds.base import OHLCV, Quote
from moneyclaw.data.storage import MarketStorage


@pytest.fixture
def storage(tmp_path) -> MarketStorage:
    db_path = str(tmp_path / "test_market.duckdb")
    s = MarketStorage(db_path)
    yield s
    s.close()


class TestStoreQuotes:
    def test_store_and_retrieve(self, storage: MarketStorage) -> None:
        ts = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        quotes = [
            Quote(symbol="BTC", price=65000.0, timestamp=ts, volume=30e9, change_24h=2.5),
            Quote(symbol="BTC", price=65100.0, timestamp=ts + timedelta(minutes=5), volume=31e9),
        ]
        count = storage.store_quotes(quotes)
        assert count == 2

        result = storage.query_prices("BTC")
        assert len(result) == 2
        assert result[0].price == 65000.0
        assert result[1].price == 65100.0

    def test_query_with_time_range(self, storage: MarketStorage) -> None:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        quotes = [
            Quote(symbol="ETH", price=3000 + i * 10, timestamp=base + timedelta(hours=i))
            for i in range(24)
        ]
        storage.store_quotes(quotes)

        start = base + timedelta(hours=10)
        end = base + timedelta(hours=15)
        result = storage.query_prices("ETH", start=start, end=end)
        assert len(result) == 6  # hours 10-15 inclusive
        assert result[0].price == 3100.0

    def test_empty_store(self, storage: MarketStorage) -> None:
        assert storage.store_quotes([]) == 0


class TestStoreOHLCV:
    def test_store_and_retrieve(self, storage: MarketStorage) -> None:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        bars = [
            OHLCV(
                timestamp=base + timedelta(days=i),
                open=100 + i,
                high=110 + i,
                low=90 + i,
                close=105 + i,
            )
            for i in range(5)
        ]
        count = storage.store_ohlcv("BTC", bars)
        assert count == 5

        result = storage.query_ohlcv("BTC")
        assert len(result) == 5
        assert result[0].open == 100
        assert result[4].close == 109

    def test_query_different_symbols(self, storage: MarketStorage) -> None:
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        storage.store_ohlcv(
            "BTC", [OHLCV(timestamp=ts, open=65000, high=66000, low=64000, close=65500)]
        )
        storage.store_ohlcv(
            "ETH", [OHLCV(timestamp=ts, open=3000, high=3100, low=2900, close=3050)]
        )

        btc = storage.query_ohlcv("BTC")
        eth = storage.query_ohlcv("ETH")
        assert len(btc) == 1
        assert len(eth) == 1
        assert btc[0].open == 65000
        assert eth[0].open == 3000
