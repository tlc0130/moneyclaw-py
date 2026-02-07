"""Tests for DataFeed base classes and data types."""

from datetime import UTC, datetime

from moneyclaw.data.feeds.base import OHLCV, Quote


class TestQuote:
    def test_defaults(self) -> None:
        q = Quote(symbol="BTC", price=50000.0)
        assert q.symbol == "BTC"
        assert q.price == 50000.0
        assert q.bid == 0.0
        assert q.ask == 0.0
        assert q.volume == 0.0
        assert q.change_24h == 0.0
        assert isinstance(q.timestamp, datetime)

    def test_full_quote(self) -> None:
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        q = Quote(
            symbol="ETH",
            price=3000.0,
            timestamp=ts,
            bid=2999.0,
            ask=3001.0,
            volume=1_000_000.0,
            change_24h=5.2,
        )
        assert q.bid == 2999.0
        assert q.ask == 3001.0
        assert q.change_24h == 5.2
        assert q.timestamp == ts


class TestOHLCV:
    def test_creation(self) -> None:
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        bar = OHLCV(timestamp=ts, open=100, high=110, low=90, close=105, volume=5000)
        assert bar.open == 100
        assert bar.high == 110
        assert bar.low == 90
        assert bar.close == 105
        assert bar.volume == 5000

    def test_default_volume(self) -> None:
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        bar = OHLCV(timestamp=ts, open=100, high=110, low=90, close=105)
        assert bar.volume == 0.0
