"""Crypto DCA (Dollar-Cost Averaging) — Layer 0 strategy.

The simplest money-making strategy: buy a fixed amount of crypto at regular intervals.
No LLM needed — pure rules engine.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
        self._exchange_id = exchange_id or cfg.get("exchange_id", "binance")
        self._executor = executor
        self._last_buy: datetime | None = None

    async def scan(self) -> list[Opportunity]:
        """Check if it's time to DCA. Returns opportunity if due."""
        now = datetime.now(UTC)

        # Simple: if we haven't bought today, generate opportunity
        if self._last_buy and self._last_buy.date() == now.date():
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
            return Result(success=False, profit_loss=0, details={"error": "no executor configured"})

        try:
            order = await self._executor.market_buy(
                self._exchange_id,
                self._symbol,
                opp.data["amount_usd"],
            )
            self._last_buy = datetime.now(UTC)
            return Result(
                success=order.status != "failed",
                profit_loss=0,  # DCA P&L is long-term
                details={
                    "order_id": order.id,
                    "filled": order.filled,
                    "dry_run": order.dry_run,
                },
            )
        except Exception:
            log.exception("crypto_dca.execute_error")
            return Result(success=False, details={"error": "execution failed"})

    def estimate_roi(self) -> float:
        """Historical BTC DCA yields ~1.5-3x over multi-year periods."""
        return 1.5
