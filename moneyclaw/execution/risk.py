"""Risk manager — prevents the agent from losing too much money."""

from __future__ import annotations

import structlog

from moneyclaw.config.settings import RiskSettings
from moneyclaw.plugins.base import Opportunity

log = structlog.get_logger()


class RiskManager:
    """Enforces risk limits: per-trade caps, daily loss limits, cooldown periods,
    per-strategy limits, max position ratios, and dry_run mode.
    """

    def __init__(self, settings: RiskSettings) -> None:
        self._settings = settings
        self._daily_loss = 0.0
        self._consecutive_losses = 0
        self._paused = False
        # Per-strategy daily loss tracking
        self._strategy_daily_loss: dict[str, float] = {}

    def allow(self, opp: Opportunity) -> bool:
        """Check if this opportunity passes risk controls."""
        if self._paused:
            log.info("risk.paused")
            return False

        # Daily loss limit (global)
        if self._daily_loss >= self._settings.max_daily_loss:
            log.warning("risk.daily_loss_limit", loss=self._daily_loss)
            return False

        # Per-trade limit
        if opp.money_involved > self._settings.max_trade_amount:
            log.warning(
                "risk.trade_too_large",
                amount=opp.money_involved,
                limit=self._settings.max_trade_amount,
            )
            return False

        # Cooldown after consecutive losses
        if self._consecutive_losses >= self._settings.cooldown_after_losses:
            log.warning("risk.cooldown", losses=self._consecutive_losses)
            return False

        # Per-strategy daily loss limit
        if self._settings.per_strategy_daily_loss > 0 and opp.strategy_name:
            strat_loss = self._strategy_daily_loss.get(opp.strategy_name, 0.0)
            if strat_loss >= self._settings.per_strategy_daily_loss:
                log.warning(
                    "risk.strategy_daily_limit",
                    strategy=opp.strategy_name,
                    loss=strat_loss,
                )
                return False

        # Max position ratio check (advisory — caller passes current ratio)
        position_ratio = opp.data.get("position_ratio", 0.0)
        if position_ratio > self._settings.max_position_ratio:
            log.warning(
                "risk.position_too_large",
                ratio=position_ratio,
                limit=self._settings.max_position_ratio,
            )
            return False

        return True

    @property
    def is_dry_run(self) -> bool:
        """Whether all trades should be simulated."""
        return self._settings.dry_run

    def needs_approval(self, opp: Opportunity) -> bool:
        """Does this opportunity need human approval?"""
        return opp.money_involved >= self._settings.approval_threshold

    def record_outcome(self, profit_loss: float, strategy_name: str = "") -> None:
        """Update risk state based on trade outcome."""
        if profit_loss < 0:
            self._daily_loss += abs(profit_loss)
            self._consecutive_losses += 1
            if strategy_name:
                prev = self._strategy_daily_loss.get(strategy_name, 0.0)
                self._strategy_daily_loss[strategy_name] = prev + abs(profit_loss)
        else:
            self._consecutive_losses = 0

    def reset_daily(self) -> None:
        """Called at start of each day."""
        self._daily_loss = 0.0
        self._consecutive_losses = 0
        self._strategy_daily_loss.clear()

    def pause(self) -> None:
        self._paused = True
        log.info("risk.paused_manually")

    def resume(self) -> None:
        self._paused = False
        log.info("risk.resumed")

    @property
    def is_paused(self) -> bool:
        return self._paused

    def status(self) -> dict:
        return {
            "paused": self._paused,
            "dry_run": self._settings.dry_run,
            "daily_loss": self._daily_loss,
            "daily_loss_limit": self._settings.max_daily_loss,
            "consecutive_losses": self._consecutive_losses,
            "cooldown_threshold": self._settings.cooldown_after_losses,
            "strategy_daily_losses": dict(self._strategy_daily_loss),
            "max_position_ratio": self._settings.max_position_ratio,
        }
