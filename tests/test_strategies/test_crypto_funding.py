"""Tests for Crypto Funding Rate Arbitrage strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from moneyclaw.data.feeds.crypto import CryptoFeed
from moneyclaw.execution.trading import ExchangeManager, TradeExecutor
from strategies.crypto_funding import CryptoFunding


@pytest.fixture
def executor() -> TradeExecutor:
    return TradeExecutor(ExchangeManager(), dry_run=True)


@pytest.fixture
def feed() -> CryptoFeed:
    return CryptoFeed()


class TestCryptoFundingScan:
    async def test_finds_high_rate_opportunities(
        self, feed: CryptoFeed, executor: TradeExecutor
    ) -> None:
        mock_rates = [
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
                "funding_rate": 0.0003,
                "index": 3000,
                "open_interest": 5e8,
            },
        ]
        strategy = CryptoFunding(feed=feed, executor=executor, threshold=0.001)

        with patch.object(
            feed, "get_funding_rates", new_callable=AsyncMock, return_value=mock_rates
        ):
            opps = await strategy.scan()

        # Only BTCUSDT exceeds 0.001 threshold
        assert len(opps) == 1
        assert "BTCUSDT" in opps[0].title
        assert opps[0].data["funding_rate"] == 0.0015

    async def test_no_opportunities_below_threshold(self, feed: CryptoFeed) -> None:
        mock_rates = [
            {
                "symbol": "BTCUSDT",
                "market": "binance",
                "funding_rate": 0.0001,
                "index": 65000,
                "open_interest": 1e9,
            },
        ]
        strategy = CryptoFunding(feed=feed, threshold=0.001)

        with patch.object(
            feed, "get_funding_rates", new_callable=AsyncMock, return_value=mock_rates
        ):
            opps = await strategy.scan()

        assert len(opps) == 0

    async def test_handles_empty_rates(self, feed: CryptoFeed) -> None:
        strategy = CryptoFunding(feed=feed)
        with patch.object(feed, "get_funding_rates", new_callable=AsyncMock, return_value=[]):
            opps = await strategy.scan()
        assert len(opps) == 0


class TestCryptoFundingEvaluate:
    async def test_score_proportional_to_rate(self, feed: CryptoFeed) -> None:
        strategy = CryptoFunding(feed=feed, threshold=0.001)
        mock_rates = [
            {
                "symbol": "BTCUSDT",
                "market": "binance",
                "funding_rate": 0.003,
                "index": 65000,
                "open_interest": 1e9,
            },
        ]
        with patch.object(
            feed, "get_funding_rates", new_callable=AsyncMock, return_value=mock_rates
        ):
            opps = await strategy.scan()

        score = await strategy.evaluate(opps[0])
        assert 0 < score.value <= 1.0
        assert score.threshold == 0.5


class TestCryptoFundingExecute:
    async def test_dry_run_execution(self, feed: CryptoFeed, executor: TradeExecutor) -> None:
        strategy = CryptoFunding(feed=feed, executor=executor, threshold=0.001)
        mock_rates = [
            {
                "symbol": "BTCUSDT",
                "market": "binance",
                "funding_rate": 0.002,
                "index": 65000,
                "open_interest": 1e9,
            },
        ]
        with patch.object(
            feed, "get_funding_rates", new_callable=AsyncMock, return_value=mock_rates
        ):
            opps = await strategy.scan()

        result = await strategy.execute(opps[0])
        assert result.success is True
        assert result.details["dry_run"] is True

    async def test_no_executor_fails(self, feed: CryptoFeed) -> None:
        strategy = CryptoFunding(feed=feed, executor=None, threshold=0.001)
        mock_rates = [
            {
                "symbol": "BTCUSDT",
                "market": "binance",
                "funding_rate": 0.002,
                "index": 65000,
                "open_interest": 1e9,
            },
        ]
        with patch.object(
            feed, "get_funding_rates", new_callable=AsyncMock, return_value=mock_rates
        ):
            opps = await strategy.scan()

        result = await strategy.execute(opps[0])
        assert result.success is False


class TestCryptoFundingMeta:
    def test_attributes(self) -> None:
        s = CryptoFunding()
        assert s.name == "crypto_funding"
        assert s.risk_level == "medium"
        assert s.min_llm_layer == 1
        assert s.estimate_roi() == 1.3
