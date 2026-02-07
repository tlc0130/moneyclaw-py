"""Tests for Stock Dividend Tracker strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from moneyclaw.data.feeds.stocks import StockFeed
from strategies.stock_dividend import StockDividend


@pytest.fixture
def feed() -> StockFeed:
    return StockFeed()


class TestStockDividendScan:
    async def test_finds_high_yield_stocks(self, feed: StockFeed) -> None:
        # get_info returns mapped keys, not raw yfinance keys
        mock_info = {
            "symbol": "VZ",
            "name": "Verizon",
            "sector": "Telecom",
            "pe_ratio": 8.0,
            "forward_pe": None,
            "market_cap": 150e9,
            "dividend_yield": 0.065,
            "ex_dividend_date": 1704067200,
            "fifty_two_week_high": 45.0,
            "fifty_two_week_low": 30.0,
        }
        strategy = StockDividend(feed=feed, watchlist=["VZ"], min_yield=0.04)
        with patch.object(feed, "get_info", new_callable=AsyncMock, return_value=mock_info):
            opps = await strategy.scan()

        assert len(opps) == 1
        assert "VZ" in opps[0].title
        assert opps[0].data["dividend_yield"] == 0.065

    async def test_skips_low_yield(self, feed: StockFeed) -> None:
        mock_info = {
            "symbol": "AAPL",
            "name": "Apple",
            "sector": "Technology",
            "pe_ratio": 28.0,
            "dividend_yield": 0.005,
            "market_cap": 3e12,
        }
        strategy = StockDividend(feed=feed, watchlist=["AAPL"], min_yield=0.04)
        with patch.object(feed, "get_info", new_callable=AsyncMock, return_value=mock_info):
            opps = await strategy.scan()

        assert len(opps) == 0

    async def test_skips_already_alerted(self, feed: StockFeed) -> None:
        mock_info = {
            "name": "Verizon",
            "dividend_yield": 0.065,
        }
        strategy = StockDividend(feed=feed, watchlist=["VZ"])
        strategy._alerted.add("VZ")

        with patch.object(feed, "get_info", new_callable=AsyncMock, return_value=mock_info):
            opps = await strategy.scan()

        assert len(opps) == 0


class TestStockDividendEvaluate:
    async def test_high_yield_high_score(self, feed: StockFeed) -> None:
        strategy = StockDividend(feed=feed)
        from moneyclaw.plugins.base import Opportunity

        opp = Opportunity(
            strategy_name="stock_dividend",
            data={"ticker": "VZ", "dividend_yield": 0.08, "pe_ratio": 10},
        )
        score = await strategy.evaluate(opp)
        assert score.value > 0.5

    async def test_high_pe_penalized(self, feed: StockFeed) -> None:
        strategy = StockDividend(feed=feed)
        from moneyclaw.plugins.base import Opportunity

        opp = Opportunity(
            strategy_name="stock_dividend",
            data={"ticker": "X", "dividend_yield": 0.06, "pe_ratio": 50},
        )
        score = await strategy.evaluate(opp)
        # High PE should reduce score
        assert score.value < 0.6


class TestStockDividendExecute:
    async def test_advisory_execution(self, feed: StockFeed) -> None:
        strategy = StockDividend(feed=feed, watchlist=["VZ"])
        from moneyclaw.plugins.base import Opportunity

        opp = Opportunity(
            strategy_name="stock_dividend",
            data={"ticker": "VZ", "dividend_yield": 0.065},
        )
        result = await strategy.execute(opp)
        assert result.success is True
        assert result.details["action"] == "advisory"
        assert "VZ" in strategy._alerted


class TestStockDividendMeta:
    def test_attributes(self) -> None:
        s = StockDividend()
        assert s.name == "stock_dividend"
        assert s.risk_level == "low"
        assert s.min_llm_layer == 1
        assert s.estimate_roi() == 1.05
