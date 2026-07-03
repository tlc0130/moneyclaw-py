"""Tests for Crypto DCA strategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
        strategy._last_buy = datetime.now(timezone.utc)
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


class TestCryptoDCAFailureBackoff:
    async def test_failed_buy_arms_cooldown(self) -> None:
        strategy = CryptoDCA(executor=None)  # no executor => execution fails
        opps = await strategy.scan()
        result = await strategy.execute(opps[0])
        assert result.success is False
        assert strategy._retry_after is not None
        # In cooldown: no new opportunity on the next tick
        assert await strategy.scan() == []

    async def test_retries_after_cooldown_expires(self) -> None:
        strategy = CryptoDCA(executor=None)
        await strategy.execute((await strategy.scan())[0])
        strategy._retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert len(await strategy.scan()) == 1

    async def test_gives_up_for_the_day_after_max_attempts(self) -> None:
        strategy = CryptoDCA(executor=None)
        for _ in range(strategy._max_attempts_per_day):
            opps = await strategy.scan()
            assert len(opps) == 1
            await strategy.execute(opps[0])
            strategy._retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert await strategy.scan() == []

    async def test_new_day_resets_backoff(self) -> None:
        strategy = CryptoDCA(executor=None)
        strategy._failed_attempts = strategy._max_attempts_per_day
        strategy._attempt_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        strategy._retry_after = datetime.now(timezone.utc) + timedelta(hours=1)
        assert len(await strategy.scan()) == 1

    async def test_successful_buy_does_not_arm_backoff(self, strategy: CryptoDCA) -> None:
        opps = await strategy.scan()
        result = await strategy.execute(opps[0])
        assert result.success is True
        assert strategy._retry_after is None
        assert strategy._failed_attempts == 0


class TestCryptoDCAMeta:
    def test_roi_estimate(self, strategy: CryptoDCA) -> None:
        assert strategy.estimate_roi() == 1.5

    def test_attributes(self) -> None:
        s = CryptoDCA()
        assert s.name == "crypto_dca"
        assert s.risk_level == "low"
        assert s.min_llm_layer == 0
