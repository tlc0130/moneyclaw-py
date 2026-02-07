"""Telegram notification sender — pushes alerts and reports to the user."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from aiogram import Bot

    from moneyclaw.plugins.base import Opportunity

log = structlog.get_logger()


class Notifier:
    """Sends notifications to a Telegram chat."""

    def __init__(self, bot: Bot, chat_id: str) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def send(self, text: str) -> None:
        """Send a plain text message."""
        try:
            await self._bot.send_message(chat_id=self._chat_id, text=text[:4000])
        except Exception:
            log.exception("notify.send_error")

    async def request_approval(self, opp: Opportunity) -> None:
        """Send an approval request for a high-value opportunity."""
        text = (
            f"APPROVAL NEEDED\n\n"
            f"Strategy: {opp.strategy_name}\n"
            f"{opp.title}\n"
            f"Amount: ${opp.money_involved:.2f}\n\n"
            f"Reply /approve {opp.id} to execute\n"
            f"Reply /reject {opp.id} to skip"
        )
        await self.send(text)

    async def daily_report(self, report: str) -> None:
        """Send the daily summary report."""
        await self.send(f"DAILY REPORT\n\n{report}")

    async def alert(self, title: str, message: str) -> None:
        """Send an urgent alert."""
        await self.send(f"ALERT: {title}\n\n{message}")
