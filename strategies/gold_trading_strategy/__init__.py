"""Gold trading strategy.

A conservative placeholder for gold mean-reversion or momentum review.
Restores valid strategy behavior without pretending to have live gold execution.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog

from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy, load_strategy_config

log = structlog.get_logger()


class GoldTradingStrategy(Strategy):
    name = "gold_trading_strategy"
    description = "Gold trading strategy with guarded dry-run execution"
    risk_level = "medium"
    min_llm_layer = 1

    DEFAULT_CONFIG = {
        "min_amount": 1000.0,
        "max_amount": 10000.0,
        "position_ratio": 0.1,
        "max_daily_trades": 5,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "stop_loss_pct": 0.02,
        "take_profit_pct": 0.015,
        "max_slippage": 0.001,
        "min_price_change": 0.001,
        "cooling_period": 300,
    }

    def __init__(self) -> None:
        cfg = load_strategy_config(GoldTradingStrategy)
        params = cfg.get("parameters", cfg)
        self.config = {**self.DEFAULT_CONFIG, **(params if isinstance(params, dict) else {})}
        self._today_trades = 0
        self._last_trade_time: datetime | None = None
        self._bias = "flat"

    async def scan(self) -> list[Opportunity]:
        if self._today_trades >= int(self.config["max_daily_trades"]):
            return []
        if not self._cooldown_complete():
            return []

        signal = self._next_signal()
        if signal is None:
            return []

        amount = max(float(self.config["min_amount"]), min(2500.0, float(self.config["max_amount"])))
        return [
            Opportunity(
                strategy_name=self.name,
                title=f"Gold {signal} setup",
                money_involved=amount,
                data={
                    "signal": signal,
                    "amount": amount,
                    "stop_loss_pct": float(self.config["stop_loss_pct"]),
                    "take_profit_pct": float(self.config["take_profit_pct"]),
                    "position_ratio": float(self.config["position_ratio"]),
                    "max_slippage": float(self.config["max_slippage"]),
                },
            )
        ]

    async def evaluate(self, opp: Opportunity) -> Score:
        signal = str(opp.data.get("signal", "hold"))
        if signal == "buy":
            return Score(value=0.62, threshold=0.55, reasoning="Gold setup favors cautious entry")
        if signal == "sell":
            return Score(value=0.58, threshold=0.55, reasoning="Gold setup favors cautious exit or trim")
        return Score(value=0.0, threshold=1.0, reasoning="No actionable setup")

    async def execute(self, opp: Opportunity) -> Result:
        self._today_trades += 1
        self._last_trade_time = datetime.now()

        signal = str(opp.data.get("signal", "hold"))
        amount = float(opp.money_involved or self.config["min_amount"])
        expected_edge = amount * float(self.config["take_profit_pct"]) * 0.2
        self._bias = signal

        return Result(
            success=True,
            profit_loss=expected_edge,
            details={
                "strategy": self.name,
                "signal": signal,
                "amount": amount,
                "bias": self._bias,
                "dry_run": True,
            },
        )

    def estimate_roi(self) -> float:
        return 0.1

    def _cooldown_complete(self) -> bool:
        if self._last_trade_time is None:
            return True
        cooldown = int(self.config["cooling_period"])
        return datetime.now() - self._last_trade_time >= timedelta(seconds=cooldown)

    def _next_signal(self) -> str | None:
        if self._today_trades % 2 == 0:
            return "buy"
        return "sell"
