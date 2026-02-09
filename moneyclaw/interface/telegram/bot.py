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
    from moneyclaw.agent.strategy_chat import StrategyChatInterface
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
        strategy_chat: StrategyChatInterface | None = None,
    ) -> None:
        self._bot = Bot(token=token)
        self._dp = Dispatcher()
        self._chat_id = chat_id
        self._brain = brain
        self._memory = memory
        self._llm = llm
        self._strategies = strategies
        self._risk = risk
        self._strategy_chat = strategy_chat
        self._pending_strategy_confirmations: dict[str, Any] = {}
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

        @dp.message(Command("strategy"))
        async def cmd_strategy(message: Message) -> None:
            """AI策略管理命令."""
            if not self._strategy_chat:
                await message.answer(
                    "AI策略管理功能未启用。请确保系统已正确配置。"
                )
                return

            args = (message.text or "").split(maxsplit=1)
            if len(args) < 2:
                await message.answer(
                    "🤖 **AI策略管理系统**\n\n"
                    "使用自然语言管理策略:\n"
                    "• `/strategy 创建一个定投BTC的策略`\n"
                    "• `/strategy 优化crypto_dca`\n"
                    "• `/strategy 列出所有策略`\n"
                    "• `/strategy 禁用crypto_funding`\n\n"
                    "或直接发送策略相关消息。"
                )
                return

            user_message = args[1]
            await self._handle_strategy_message(message, user_message)

        @dp.message(F.text)
        async def fallback(message: Message) -> None:
            # 先检查是否有待确认的策略保存
            if await self._handle_strategy_confirmation(message):
                return

            # 检查是否是策略相关消息
            if self._strategy_chat and message.text:
                text = message.text.strip()
                # 策略相关关键词
                strategy_keywords = [
                    "策略", "创建", "生成", "优化", "启用", "禁用", "删除",
                    "strategy", "create", "generate", "optimize", "enable",
                    "disable", "delete", "list", "列出"
                ]
                if any(kw in text.lower() for kw in strategy_keywords):
                    await self._handle_strategy_message(message, text)
                    return

            await message.answer(
                "I don't understand that command."
                " Try /status, /strategies, /cost, /ask <question>, or /strategy <command>."
            )

    async def _handle_strategy_message(self, message: Message, text: str) -> None:
        """处理策略相关的自然语言消息."""
        if not self._strategy_chat:
            return

        import structlog
        log = structlog.get_logger()
        log.info("telegram.strategy_command", text=text[:50])

        try:
            response = await self._strategy_chat.handle_message(text)

            # 检查是否需要确认保存策略
            if response.data and response.data.get("pending_confirm"):
                strategy = response.data.get("strategy")
                if strategy:
                    # 存储待确认的策略
                    self._pending_strategy_confirmations[str(message.from_user.id)] = strategy

                    # 添加确认按钮提示
                    response_msg = response.message + "\n\n💡 回复 '是' 或 'yes' 确认保存，回复其他内容取消。"
                    await message.answer(response_msg[:4000])
                    return

            await message.answer(response.message[:4000])

        except Exception as e:
            log.exception("telegram.strategy_error")
            await message.answer(f"处理策略命令时出错: {e}")

    async def _handle_strategy_confirmation(self, message: Message) -> bool:
        """处理策略保存确认. 返回是否处理了确认."""
        user_id = str(message.from_user.id)
        text = message.text or ""

        # 检查是否有待确认的策略
        if user_id not in self._pending_strategy_confirmations:
            return False

        # 检查确认回复
        if text.strip().lower() in ("是", "yes", "确认", "保存", "ok", "y"):
            strategy = self._pending_strategy_confirmations.pop(user_id)
            try:
                from moneyclaw.agent.strategy_generator import GeneratedStrategy

                if isinstance(strategy, GeneratedStrategy):
                    path = await self._strategy_chat.confirm_save_strategy(strategy)
                    await message.answer(f"✅ 策略已保存！\n路径: {path.message}")
                return True
            except Exception as e:
                await message.answer(f"❌ 保存策略失败: {e}")
                return True
        else:
            # 用户取消
            self._pending_strategy_confirmations.pop(user_id, None)
            await message.answer("❎ 已取消保存策略。")
            return True

    @property
    def bot(self) -> Bot:
        return self._bot

    async def start(self) -> None:
        """Start polling for Telegram updates."""
        log.info("telegram.starting")
        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        await self._bot.session.close()
