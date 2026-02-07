"""Risk manager — prevents the agent from losing too much money."""

from __future__ import annotations

import structlog

from moneyclaw.config.settings import RiskSettings
from moneyclaw.plugins.base import Opportunity

log = structlog.get_logger()


class RiskManager:
    """Enforces risk limits: per-trade caps, daily loss limits, cooldown periods."""

    def __init__(self, settings: RiskSettings) -> None:
        self._settings = settings
        self._daily_loss = 0.0
        self._consecutive_losses = 0
        self._paused = False

    def allow(self, opp: Opportunity) -> bool:
        """Check if this opportunity passes risk controls."""
        if self._paused:
            log.info("risk.paused")
            return False

        # Daily loss limit
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

        return True

    def needs_approval(self, opp: Opportunity) -> bool:
        """Does this opportunity need human approval?"""
        return opp.money_involved >= self._settings.approval_threshold

    def record_outcome(self, profit_loss: float) -> None:
        """Update risk state based on trade outcome."""
        if profit_loss < 0:
            self._daily_loss += abs(profit_loss)
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def reset_daily(self) -> None:
        """Called at start of each day."""
        self._daily_loss = 0.0
        self._consecutive_losses = 0

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
            "daily_loss": self._daily_loss,
            "daily_loss_limit": self._settings.max_daily_loss,
            "consecutive_losses": self._consecutive_losses,
            "cooldown_threshold": self._settings.cooldown_after_losses,
        }
