"""Crypto DCA (Dollar-Cost Averaging) — Layer 0 strategy.

The simplest money-making strategy: buy a fixed amount of crypto at regular intervals.
No LLM needed — pure rules engine.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import structlog

from moneyclaw.execution.trading import TradeExecutor
from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy, load_strategy_config

log = structlog.get_logger()


class CryptoDCA(Strategy):
    """Dollar-cost averaging into crypto. Buy fixed amounts on schedule.

    Layer 0: no LLM calls — pure timer + fixed amount.
    """

    name = "crypto_dca"
    description = "DCA into crypto — buy fixed amounts on a regular schedule"
    risk_level = "low"
    min_llm_layer = 0

    def __init__(
        self,
        coin: str | None = None,
        symbol: str | None = None,
        amount_usd: float | None = None,
        exchange_id: str | None = None,
        executor: TradeExecutor | None = None,
    ) -> None:
        cfg = load_strategy_config(CryptoDCA)
        self._coin = coin or cfg.get("coin", "bitcoin")
        self._symbol = symbol or cfg.get("symbol", "BTC/USDT")
        self._amount_usd = amount_usd if amount_usd is not None else cfg.get("amount_usd", 10.0)
        # None => resolve to the executor's configured default exchange at run time.
        self._exchange_id = exchange_id or cfg.get("exchange_id")
        self._executor = executor
        self._last_buy: datetime | None = None
        # A failed buy must NOT retry on every brain tick (60s): with a live
        # account that lacks free quote balance, that meant ~1,440 rejected
        # orders/day hammering the exchange. Failed attempts back off for a
        # cooldown and give up for the day after a few tries.
        self._retry_cooldown_min = float(cfg.get("failed_buy_retry_minutes", 60.0))
        self._max_attempts_per_day = int(cfg.get("max_attempts_per_day", 3))
        self._retry_after: datetime | None = None
        self._failed_attempts = 0
        self._attempt_date: date | None = None

    async def scan(self) -> list[Opportunity]:
        """Check if it's time to DCA. Returns opportunity if due."""
        now = datetime.now(timezone.utc)

        # Simple: if we haven't bought today, generate opportunity
        if self._last_buy and self._last_buy.date() == now.date():
            return []

        # Failure backoff: fresh day resets the counter; otherwise honor the
        # cooldown and the per-day attempt cap.
        if self._attempt_date and self._attempt_date != now.date():
            self._failed_attempts = 0
            self._retry_after = None
        if self._failed_attempts >= self._max_attempts_per_day:
            return []
        if self._retry_after and now < self._retry_after:
            return []

        return [
            Opportunity(
                strategy_name=self.name,
                title=f"DCA: Buy ${self._amount_usd} of {self._coin}",
                money_involved=self._amount_usd,
                data={
                    "coin": self._coin,
                    "symbol": self._symbol,
                    "amount_usd": self._amount_usd,
                    "exchange": self._exchange_id,
                },
                pre_score=0.9,  # DCA needs almost no judgment
            ),
        ]

    async def evaluate(self, opp: Opportunity) -> Score:
        """DCA is pre-scored — no LLM evaluation needed."""
        return Score(value=opp.pre_score or 0.9, threshold=0.3, reasoning="DCA: scheduled buy")

    async def execute(self, opp: Opportunity) -> Result:
        """Execute the DCA buy."""
        if not self._executor:
            self._record_failed_attempt()
            return Result(success=False, profit_loss=0, details={"error": "no executor configured"})

        try:
            exchange_id = self._exchange_id or self._executor.default_exchange
            # Buy by USD COST (spend $amount_usd), not base quantity — see market_buy_cost.
            order = await self._executor.market_buy_cost(
                exchange_id,
                self._symbol,
                float(opp.data["amount_usd"]),
            )
            success = order.status not in ("failed", "blocked", "rejected")
            if success:
                self._last_buy = datetime.now(timezone.utc)
            else:
                self._record_failed_attempt()
            return Result(
                success=success,
                profit_loss=0,  # DCA P&L is long-term
                details={
                    "order_id": order.id,
                    "filled": order.filled,
                    "status": order.status,
                    "dry_run": order.dry_run,
                },
            )
        except Exception:
            log.exception("crypto_dca.execute_error")
            self._record_failed_attempt()
            return Result(success=False, details={"error": "execution failed"})

    def _record_failed_attempt(self) -> None:
        now = datetime.now(timezone.utc)
        if self._attempt_date != now.date():
            self._attempt_date = now.date()
            self._failed_attempts = 0
        self._failed_attempts += 1
        self._retry_after = now + timedelta(minutes=self._retry_cooldown_min)
        log.warning(
            "crypto_dca.buy_failed_backoff",
            attempt=self._failed_attempts,
            max_attempts=self._max_attempts_per_day,
            retry_after=self._retry_after.isoformat(),
        )

    def estimate_roi(self) -> float:
        """Historical BTC DCA yields ~1.5-3x over multi-year periods."""
        return 1.5
