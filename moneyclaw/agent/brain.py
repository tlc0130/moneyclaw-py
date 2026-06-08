"""Agent Brain — the 24/7 sense-think-act-learn main loop."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from moneyclaw.data.feeds.crypto import CryptoFeed

import structlog

if TYPE_CHECKING:
    from moneyclaw.agent.evaluator import Evaluator
    from moneyclaw.agent.memory import Memory
    from moneyclaw.agent.planner import Planner
    from moneyclaw.execution.risk import RiskManager
    from moneyclaw.execution.trading import TradeExecutor
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
        executor: TradeExecutor | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._planner = planner
        self._evaluator = evaluator
        self._strategies = strategies
        self._risk = risk
        self._scheduler = scheduler
        self._notifier = notifier
        self._executor = executor
        self._running = False
        self._tick_count = 0
        self._paper_prices = CryptoFeed()

    async def start(self) -> None:
        """Start the agent's main loop."""
        self._running = True
        log.info("agent.starting", strategies=len(self._strategies.active))

        dry_run = self._risk.is_dry_run
        if self._notifier:
            mode = "DRY RUN" if dry_run else "LIVE"
            await self._notifier.send(
                f"MoneyClaw started [{mode}]\n"
                f"Strategies: {len(self._strategies.active)} active\n"
                f"Scanning for opportunities..."
            )

        self._scheduler.start()

        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("agent.tick_error")
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
        self._tick_count += 1

        # Process any approved-but-not-yet-executed opportunities
        await self._process_approved()

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
        for opp, _score in scored:
            if not self._risk.allow(opp):
                log.info("agent.risk_blocked", opportunity=opp.id)
                if self._notifier:
                    await self._notifier.alert(
                        "Risk blocked",
                        f"{opp.title} (${opp.money_involved:.2f})",
                    )
                continue

            if self._risk.needs_approval(opp):
                log.info("agent.needs_approval", opportunity=opp.id)
                if self._notifier:
                    await self._notifier.request_approval(opp)
                await self._memory.record_pending(opp)
                continue

            await self._execute_opportunity(opp)

    async def _execute_opportunity(self, opp) -> None:
        """Execute an opportunity and record the outcome."""
        try:
            strategy = self._strategies.get(opp.strategy_name)
            if not strategy:
                return

            result = await strategy.execute(opp)
            await self._memory.record_result(opp, result)

            if self._risk.is_dry_run:
                await self._update_paper_portfolio(opp, result)

            # LEARN — record outcome for risk tracking
            self._risk.record_outcome(result.profit_loss, strategy_name=opp.strategy_name)

            log.info(
                "agent.executed",
                strategy=opp.strategy_name,
                profit=result.profit_loss,
                success=result.success,
                status=result.details.get("status") if isinstance(result.details, dict) else None,
                dry_run=self._risk.is_dry_run,
            )
            if self._notifier:
                if result.success:
                    await self._notifier.trade_executed(opp, result)
                else:
                    # Don't dress up a failed/blocked order as a completed trade —
                    # that masked the ccxt execution bug for a long time.
                    reason = (
                        result.details.get("error")
                        or result.details.get("status")
                        or "execution failed"
                    ) if isinstance(result.details, dict) else "execution failed"
                    await self._notifier.alert(
                        "Trade NOT executed",
                        f"{opp.strategy_name}: {opp.title}\nReason: {reason}",
                    )

        except Exception:
            log.exception("agent.execute_error", opportunity=opp.id)

    async def _process_approved(self) -> None:
        """Execute opportunities that have been approved by the user."""
        pending = await self._memory.get_pending()
        for item in pending:
            if item.get("status") != "approved":
                continue
            from moneyclaw.plugins.base import Opportunity

            opp = Opportunity(
                id=item["id"],
                strategy_name=item["strategy"],
                title=item["title"],
                data=item.get("data", {}),
            )
            await self._execute_opportunity(opp)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def tick_count(self) -> int:
        return self._tick_count

    async def _update_paper_portfolio(self, opp, result) -> None:
        if opp.strategy_name != "crypto_dca":
            return

        coin = opp.data.get("coin")
        amount_usd = float(opp.data.get("amount_usd", 0) or 0)
        if not coin or amount_usd <= 0:
            return

        quote = await self._paper_prices.get_price(str(coin))
        if not quote or quote.price <= 0:
            return

        quantity = amount_usd / quote.price
        symbol = str(coin).upper()
        await self._memory.paper_buy(
            symbol=symbol,
            quantity=quantity,
            price=quote.price,
            details={
                "strategy": opp.strategy_name,
                "order_id": result.details.get("order_id") if isinstance(result.details, dict) else None,
                "dry_run": True,
            },
        )
        await self._memory.paper_mark_price(symbol, quote.price)

    async def get_status(self) -> dict:
        """Current agent status for reporting."""
        today_pnl = await self._memory.today_pnl()

        # Get wallet balance if executor is available
        wallet_value = 0.0
        if self._executor and not self._risk.is_dry_run:
            try:
                for exchange_id in self._executor.exchange_manager.connected:
                    wallet_value += await self._executor.exchange_manager.get_total_value(exchange_id)
            except Exception:
                pass  # Fail silently, wallet value is optional

        paper_portfolio = await self._memory.get_paper_portfolio() if self._risk.is_dry_run else None

        return {
            "running": self._running,
            "strategies_active": len(self._strategies.active),
            "strategies": self._strategies.status(),
            "today_pnl": today_pnl,
            "wallet_value": wallet_value,
            "llm_cost": self._llm.cost_tracker.format_status(),
            "pending_approvals": await self._memory.pending_count(),
            "risk": self._risk.status(),
            "tick_count": self._tick_count,
            "dry_run": self._risk.is_dry_run,
            "paper_portfolio": paper_portfolio,
        }
