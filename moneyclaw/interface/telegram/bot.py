"""Telegram bot — primary interface for controlling MoneyClaw."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

if TYPE_CHECKING:
    from moneyclaw.agent.brain import AgentBrain
    from moneyclaw.agent.memory import Memory
    from moneyclaw.execution.risk import RiskManager
    from moneyclaw.llm.router import LLMRouter
    from moneyclaw.plugins.registry import StrategyRegistry

log = structlog.get_logger()


class TelegramBot:
    """Telegram bot for controlling MoneyClaw and receiving notifications."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        brain: AgentBrain,
        memory: Memory,
        llm: LLMRouter,
        strategies: StrategyRegistry,
        risk: RiskManager,
    ) -> None:
        self._bot = Bot(token=token)
        self._dp = Dispatcher()
        self._chat_id = chat_id
        self._brain = brain
        self._memory = memory
        self._llm = llm
        self._strategies = strategies
        self._risk = risk
        self._register_handlers()

    def _register_handlers(self) -> None:
        dp = self._dp

        @dp.message(Command("start"))
        async def cmd_start(message: Message) -> None:
            await message.answer(
                "MoneyClaw is active.\n"
                "I'm working 24/7 to save and make money for you.\n"
                "Use /status to check current state."
            )

        @dp.message(Command("status"))
        async def cmd_status(message: Message) -> None:
            status = await self._brain.get_status()
            text = (
                f"Running: {'Yes' if status['running'] else 'No'}\n"
                f"Strategies: {status['strategies_active']} active\n"
                f"Today P&L: ${status['today_pnl']:.2f}\n"
                f"Pending approvals: {status['pending_approvals']}\n\n"
                f"{status['llm_cost']}"
            )
            await message.answer(text)

        @dp.message(Command("cost"))
        async def cmd_cost(message: Message) -> None:
            await message.answer(self._llm.cost_tracker.format_status())

        @dp.message(Command("strategies"))
        async def cmd_strategies(message: Message) -> None:
            statuses = self._strategies.status()
            if not statuses:
                await message.answer("No strategies loaded.")
                return
            lines = []
            for s in statuses:
                status_icon = "ON" if s["enabled"] else "OFF"
                lines.append(
                    f"[{status_icon}] {s['name']} ({s['risk_level']})\n"
                    f"  {s['description']}\n"
                    f"  Est. ROI: {s['roi_estimate']:.1f}x"
                )
            await message.answer("\n\n".join(lines))

        @dp.message(Command("pause"))
        async def cmd_pause(message: Message) -> None:
            self._risk.pause()
            await message.answer("All strategies paused.")

        @dp.message(Command("resume"))
        async def cmd_resume(message: Message) -> None:
            self._risk.resume()
            await message.answer("Strategies resumed.")

        @dp.message(Command("approve"))
        async def cmd_approve(message: Message) -> None:
            args = (message.text or "").split(maxsplit=1)
            if len(args) < 2:
                await message.answer("Usage: /approve <id>")
                return
            opp_id = args[1].strip()
            if await self._memory.approve(opp_id):
                await message.answer(f"Approved: {opp_id}")
            else:
                await message.answer(f"Not found or already processed: {opp_id}")

        @dp.message(Command("reject"))
        async def cmd_reject(message: Message) -> None:
            args = (message.text or "").split(maxsplit=1)
            if len(args) < 2:
                await message.answer("Usage: /reject <id>")
                return
            opp_id = args[1].strip()
            if await self._memory.reject(opp_id):
                await message.answer(f"Rejected: {opp_id}")
            else:
                await message.answer(f"Not found or already processed: {opp_id}")

        @dp.message(Command("history"))
        async def cmd_history(message: Message) -> None:
            history = await self._memory.get_history(limit=10)
            if not history:
                await message.answer("No history yet.")
                return
            lines = []
            for h in history:
                pnl = h["profit_loss"]
                sign = "+" if pnl >= 0 else ""
                lines.append(f"{h['strategy']}: {h['title']} ({sign}${pnl:.2f})")
            await message.answer("\n".join(lines))

        @dp.message(Command("ask"))
        async def cmd_ask(message: Message) -> None:
            args = (message.text or "").split(maxsplit=1)
            if len(args) < 2:
                await message.answer("Usage: /ask <question>")
                return
            from moneyclaw.llm.router import LLMLayer, TaskRequest

            response = await self._llm.complete(
                TaskRequest(
                    prompt=args[1],
                    system="You are MoneyClaw, a money-saving AI assistant. Be concise.",
                    min_layer=LLMLayer.CHEAP,
                    max_layer=LLMLayer.PREMIUM,
                    complexity=0.5,
                )
            )
            await message.answer(response.text[:4000])  # Telegram message limit

        @dp.message(F.text)
        async def fallback(message: Message) -> None:
            await message.answer(
                "I don't understand that command."
                " Try /status, /strategies, /cost, or /ask <question>."
            )

    @property
    def bot(self) -> Bot:
        return self._bot

    async def start(self) -> None:
        """Start polling for Telegram updates."""
        log.info("telegram.starting")
        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        await self._bot.session.close()
