"""Tests for Telegram Notifier."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from moneyclaw.interface.telegram.notify import Notifier
from moneyclaw.plugins.base import Opportunity, Result


@pytest.fixture
def notifier() -> Notifier:
    bot = AsyncMock()
    return Notifier(bot=bot, chat_id="12345")


class TestNotifier:
    async def test_send(self, notifier: Notifier) -> None:
        await notifier.send("Hello")
        notifier._bot.send_message.assert_called_once_with(chat_id="12345", text="Hello")

    async def test_send_truncates_long_messages(self, notifier: Notifier) -> None:
        await notifier.send("x" * 5000)
        call_args = notifier._bot.send_message.call_args
        assert len(call_args.kwargs["text"]) == 4000

    async def test_request_approval(self, notifier: Notifier) -> None:
        opp = Opportunity(id="abc123", strategy_name="test", title="Big trade", money_involved=100)
        await notifier.request_approval(opp)
        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "APPROVAL" in text
        assert "abc123" in text
        assert "$100.00" in text

    async def test_trade_executed(self, notifier: Notifier) -> None:
        opp = Opportunity(strategy_name="dca", title="Buy BTC")
        result = Result(
            success=True, profit_loss=5.0, details={"dry_run": True, "order_id": "dry_1"}
        )
        await notifier.trade_executed(opp, result)
        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "TRADE" in text
        assert "DRY RUN" in text
        assert "+$5.00" in text
        assert "dry_1" in text

    async def test_trade_executed_loss(self, notifier: Notifier) -> None:
        opp = Opportunity(strategy_name="funding", title="Short")
        result = Result(success=True, profit_loss=-3.0, details={})
        await notifier.trade_executed(opp, result)
        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "$-3.00" in text

    async def test_daily_report(self, notifier: Notifier) -> None:
        await notifier.daily_report("P&L: +$10.00\nTrades: 5")
        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "DAILY REPORT" in text
        assert "P&L: +$10.00" in text

    async def test_alert(self, notifier: Notifier) -> None:
        await notifier.alert("Risk triggered", "Daily loss exceeded")
        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "ALERT" in text

    async def test_risk_alert(self, notifier: Notifier) -> None:
        await notifier.risk_alert("Daily loss limit", {"loss": "$50", "limit": "$100"})
        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "RISK ALERT" in text
        assert "loss" in text

    async def test_strategy_alert(self, notifier: Notifier) -> None:
        await notifier.strategy_alert(
            "stock_dividend", "VZ yields 6.5%", {"message": "Consider buying"}
        )
        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "SIGNAL" in text
        assert "Consider buying" in text

    async def test_send_error_does_not_raise(self, notifier: Notifier) -> None:
        notifier._bot.send_message.side_effect = Exception("network error")
        await notifier.send("test")
