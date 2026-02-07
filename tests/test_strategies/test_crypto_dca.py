"""Tests for Crypto DCA strategy."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moneyclaw.execution.trading import ExchangeManager, TradeExecutor
from strategies.crypto_dca import CryptoDCA


@pytest.fixture
def executor() -> TradeExecutor:
    return TradeExecutor(ExchangeManager(), dry_run=True)


@pytest.fixture
def strategy(executor: TradeExecutor) -> CryptoDCA:
    return CryptoDCA(
        coin="bitcoin",
        symbol="BTC/USDT",
        amount_usd=10.0,
        executor=executor,
    )


class TestCryptoDCAScan:
    async def test_generates_opportunity_when_due(self, strategy: CryptoDCA) -> None:
        opps = await strategy.scan()
        assert len(opps) == 1
        assert opps[0].strategy_name == "crypto_dca"
        assert opps[0].money_involved == 10.0
        assert opps[0].pre_score == 0.9

    async def test_no_duplicate_same_day(self, strategy: CryptoDCA) -> None:
        strategy._last_buy = datetime.now(UTC)
        opps = await strategy.scan()
        assert len(opps) == 0


class TestCryptoDCAEvaluate:
    async def test_pre_scored(self, strategy: CryptoDCA) -> None:
        opps = await strategy.scan()
        score = await strategy.evaluate(opps[0])
        assert score.value == 0.9
        assert score.threshold == 0.3


class TestCryptoDCAExecute:
    async def test_dry_run_execution(self, strategy: CryptoDCA) -> None:
        opps = await strategy.scan()
        result = await strategy.execute(opps[0])
        assert result.success is True
        assert result.details["dry_run"] is True
        assert "order_id" in result.details

    async def test_no_executor_fails(self) -> None:
        strategy = CryptoDCA(executor=None)
        opps = await strategy.scan()
        result = await strategy.execute(opps[0])
        assert result.success is False

    async def test_updates_last_buy(self, strategy: CryptoDCA) -> None:
        assert strategy._last_buy is None
        opps = await strategy.scan()
        await strategy.execute(opps[0])
        assert strategy._last_buy is not None


class TestCryptoDCAMeta:
    def test_roi_estimate(self, strategy: CryptoDCA) -> None:
        assert strategy.estimate_roi() == 1.5

    def test_attributes(self) -> None:
        s = CryptoDCA()
        assert s.name == "crypto_dca"
        assert s.risk_level == "low"
        assert s.min_llm_layer == 0
