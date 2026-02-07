"""Agent Brain — the 24/7 sense-think-act-learn main loop."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from moneyclaw.agent.evaluator import Evaluator
    from moneyclaw.agent.memory import Memory
    from moneyclaw.agent.planner import Planner
    from moneyclaw.execution.risk import RiskManager
    from moneyclaw.interface.telegram.notify import Notifier
    from moneyclaw.llm.router import LLMRouter
    from moneyclaw.plugins.registry import StrategyRegistry
    from moneyclaw.scheduler.engine import Scheduler

log = structlog.get_logger()

# How long to sleep between main loop iterations when there's nothing to do
IDLE_SLEEP = 60  # seconds


class AgentBrain:
    """The agent's main loop: sense → think → act → learn → report."""

    def __init__(
        self,
        llm: LLMRouter,
        memory: Memory,
        planner: Planner,
        evaluator: Evaluator,
        strategies: StrategyRegistry,
        risk: RiskManager,
        scheduler: Scheduler,
        notifier: Notifier | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._planner = planner
        self._evaluator = evaluator
        self._strategies = strategies
        self._risk = risk
        self._scheduler = scheduler
        self._notifier = notifier
        self._running = False

    async def start(self) -> None:
        """Start the agent's main loop."""
        self._running = True
        log.info("agent.starting", strategies=len(self._strategies.active))

        if self._notifier:
            await self._notifier.send("MoneyClaw started. Scanning for opportunities...")

        self._scheduler.start()

        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("agent.tick_error")
                # Don't crash — log and continue
            await asyncio.sleep(IDLE_SLEEP)

    async def stop(self) -> None:
        """Gracefully stop the agent."""
        self._running = False
        self._scheduler.stop()
        log.info("agent.stopped")
        if self._notifier:
            await self._notifier.send("MoneyClaw stopped.")

    async def _tick(self) -> None:
        """One iteration of the main loop."""
        # 1. SENSE — scan all active strategies for opportunities
        all_opportunities = []
        for strategy in self._strategies.active:
            try:
                opps = await strategy.scan()
                all_opportunities.extend(opps)
            except Exception:
                log.exception("agent.scan_error", strategy=strategy.name)

        if not all_opportunities:
            return

        log.info("agent.opportunities_found", count=len(all_opportunities))

        # 2. THINK — evaluate and prioritize
        scored = []
        for opp in all_opportunities:
            score = await self._evaluator.score(opp)
            if score.value > score.threshold:
                scored.append((opp, score))

        scored.sort(key=lambda x: x[1].value, reverse=True)

        # 3. ACT — plan and execute (with risk checks)
        for opp, score in scored:
            # Risk check
            if not self._risk.allow(opp):
                log.info("agent.risk_blocked", opportunity=opp.id)
                if self._notifier:
                    await self._notifier.send(
                        f"Blocked by risk controls: {opp.title} "
                        f"(${opp.money_involved:.2f})"
                    )
                continue

            # Check if human approval is needed
            if self._risk.needs_approval(opp):
                log.info("agent.needs_approval", opportunity=opp.id)
                if self._notifier:
                    await self._notifier.request_approval(opp)
                await self._memory.record_pending(opp)
                continue

            # Execute
            try:
                strategy = self._strategies.get(opp.strategy_name)
                if strategy:
                    result = await strategy.execute(opp)
                    await self._memory.record_result(opp, result)

                    # 4. LEARN — record outcome
                    log.info(
                        "agent.executed",
                        strategy=opp.strategy_name,
                        profit=result.profit_loss,
                    )
                    if self._notifier:
                        emoji = "+" if result.profit_loss >= 0 else ""
                        await self._notifier.send(
                            f"Executed: {opp.title}\n"
                            f"P&L: {emoji}${result.profit_loss:.2f}"
                        )
            except Exception:
                log.exception("agent.execute_error", opportunity=opp.id)

    @property
    def is_running(self) -> bool:
        return self._running

    async def get_status(self) -> dict:
        """Current agent status for reporting."""
        today_pnl = await self._memory.today_pnl()
        return {
            "running": self._running,
            "strategies_active": len(self._strategies.active),
            "today_pnl": today_pnl,
            "llm_cost": self._llm.cost_tracker.format_status(),
            "pending_approvals": await self._memory.pending_count(),
        }
