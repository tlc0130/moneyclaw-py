"""Tests for risk management."""

from moneyclaw.config.settings import RiskSettings
from moneyclaw.execution.risk import RiskManager
from moneyclaw.plugins.base import Opportunity


def make_risk(
    max_trade: float = 50,
    max_daily_loss: float = 100,
    approval_threshold: float = 50,
    cooldown: int = 3,
) -> RiskManager:
    return RiskManager(
        RiskSettings(
            max_trade_amount=max_trade,
            max_daily_loss=max_daily_loss,
            approval_threshold=approval_threshold,
            cooldown_after_losses=cooldown,
        )
    )


class TestRiskManager:
    def test_allows_small_trade(self) -> None:
        risk = make_risk()
        opp = Opportunity(money_involved=10)
        assert risk.allow(opp) is True

    def test_blocks_trade_over_limit(self) -> None:
        risk = make_risk(max_trade=50)
        opp = Opportunity(money_involved=100)
        assert risk.allow(opp) is False

    def test_blocks_after_daily_loss_limit(self) -> None:
        risk = make_risk(max_daily_loss=20)
        # Simulate losses
        risk.record_outcome(-10)
        risk.record_outcome(-10)
        opp = Opportunity(money_involved=5)
        assert risk.allow(opp) is False

    def test_cooldown_after_consecutive_losses(self) -> None:
        risk = make_risk(cooldown=2)
        risk.record_outcome(-5)
        risk.record_outcome(-5)
        opp = Opportunity(money_involved=5)
        assert risk.allow(opp) is False

    def test_cooldown_resets_on_profit(self) -> None:
        risk = make_risk(cooldown=3)
        risk.record_outcome(-5)
        risk.record_outcome(-5)
        risk.record_outcome(10)  # Win resets counter
        opp = Opportunity(money_involved=5)
        assert risk.allow(opp) is True

    def test_needs_approval(self) -> None:
        risk = make_risk(approval_threshold=50)
        small = Opportunity(money_involved=30)
        big = Opportunity(money_involved=100)
        assert risk.needs_approval(small) is False
        assert risk.needs_approval(big) is True

    def test_pause_resume(self) -> None:
        risk = make_risk()
        opp = Opportunity(money_involved=5)
        assert risk.allow(opp) is True

        risk.pause()
        assert risk.allow(opp) is False

        risk.resume()
        assert risk.allow(opp) is True

    def test_daily_reset(self) -> None:
        risk = make_risk(max_daily_loss=20)
        risk.record_outcome(-20)
        opp = Opportunity(money_involved=5)
        assert risk.allow(opp) is False

        risk.reset_daily()
        assert risk.allow(opp) is True
