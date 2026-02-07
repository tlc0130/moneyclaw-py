"""CLI entry point — `moneyclaw run`, `moneyclaw status`, etc."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import click
import structlog

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


@click.group()
@click.option("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
def main(log_level: str) -> None:
    """MoneyClaw — 7x24 AI Agent that saves and makes money."""
    level = _LOG_LEVELS.get(log_level.upper(), logging.INFO)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


@main.command()
@click.option("--web/--no-web", default=True, help="Start web dashboard")
@click.option("--telegram/--no-telegram", default=True, help="Start Telegram bot")
def run(web: bool, telegram: bool) -> None:
    """Start the MoneyClaw agent."""
    asyncio.run(_run(web=web, telegram=telegram))


async def _run(web: bool, telegram: bool) -> None:
    """Async entry point — wire everything together and start."""
    from moneyclaw.agent.brain import AgentBrain
    from moneyclaw.agent.evaluator import Evaluator
    from moneyclaw.agent.memory import Memory
    from moneyclaw.agent.planner import Planner
    from moneyclaw.config.settings import Settings
    from moneyclaw.execution.risk import RiskManager
    from moneyclaw.execution.trading import ExchangeManager, TradeExecutor
    from moneyclaw.interface.telegram.notify import Notifier
    from moneyclaw.llm.budget_manager import BudgetManager, BudgetPolicy
    from moneyclaw.llm.cache import ResponseCache
    from moneyclaw.llm.cost_tracker import CostTracker
    from moneyclaw.llm.model_discovery import ModelDiscoveryService
    from moneyclaw.llm.model_registry import SmartModelRegistry
    from moneyclaw.llm.performance_tracker import PerformanceTracker
    from moneyclaw.llm.smart_router import SmartRouter
    from moneyclaw.plugins.loader import discover_strategies
    from moneyclaw.plugins.registry import StrategyRegistry
    from moneyclaw.scheduler.engine import Scheduler

    settings = Settings()

    # Initialize SmartRouter components
    discovery = ModelDiscoveryService(timeout=settings.llm.model_discovery_timeout)
    registry = SmartModelRegistry(discovery_service=discovery)
    cost_tracker = CostTracker(daily_budget=settings.llm.daily_llm_budget)
    budget_policy = BudgetPolicy(
        daily_budget=settings.llm.daily_llm_budget,
        caution_threshold=settings.llm.budget_caution_threshold,
        critical_threshold=settings.llm.budget_critical_threshold,
        enable_auto_downgrade=settings.llm.enable_auto_downgrade,
        reserve_for_urgent=settings.llm.reserve_budget_for_urgent,
    )
    budget_manager = BudgetManager(cost_tracker=cost_tracker, policy=budget_policy)
    performance_tracker = PerformanceTracker()

    llm = SmartRouter(
        registry=registry,
        cost_tracker=cost_tracker,
        budget_manager=budget_manager,
        performance_tracker=performance_tracker,
        cache=ResponseCache(),
    )

    # Discover and initialize available models
    click.echo("Discovering available LLM models...")
    await llm.initialize()
    available_models = llm.get_available_models()

    # Memory
    memory = Memory(db_path=settings.db_path)
    await memory.init()

    # Trading execution
    exchange_mgr = ExchangeManager()
    dry_run = settings.exchange.dry_run
    if not dry_run:
        # Connect configured exchanges
        if settings.exchange.binance_api_key:
            exchange_mgr.connect(
                "binance", settings.exchange.binance_api_key, settings.exchange.binance_secret
            )
        if settings.exchange.okx_api_key:
            exchange_mgr.connect("okx", settings.exchange.okx_api_key, settings.exchange.okx_secret)
    executor = TradeExecutor(exchange_mgr, dry_run=dry_run)

    # Data feeds
    from moneyclaw.data.feeds.crypto import CryptoFeed
    from moneyclaw.data.feeds.news import NewsFeed
    from moneyclaw.data.feeds.stocks import StockFeed
    from moneyclaw.data.storage import MarketStorage

    crypto_feed = CryptoFeed()
    stock_feed = StockFeed()
    _news_feed = NewsFeed()  # noqa: F841 — reserved for news-based strategies
    storage = MarketStorage(settings.data.duckdb_path)

    # Components
    evaluator = Evaluator(llm=llm)
    planner = Planner(llm=llm)
    risk = RiskManager(settings=settings.risk)
    scheduler = Scheduler()
    strategies = StrategyRegistry()

    # Notifier (optional)
    notifier = None
    if telegram and settings.telegram.token:
        from aiogram import Bot

        bot = Bot(token=settings.telegram.token)
        notifier = Notifier(bot=bot, chat_id=settings.telegram.chat_id)

    # Agent brain
    brain = AgentBrain(
        llm=llm,
        memory=memory,
        planner=planner,
        evaluator=evaluator,
        strategies=strategies,
        risk=risk,
        scheduler=scheduler,
        notifier=notifier,
        executor=executor,
    )

    # Discover and register strategies — inject deps based on what they need
    strategy_classes = discover_strategies(settings.strategies_dir)
    for cls in strategy_classes:
        instance = _instantiate_strategy(
            cls,
            crypto_feed=crypto_feed,
            stock_feed=stock_feed,
            executor=executor,
            exchange_manager=exchange_mgr,
        )
        await strategies.register(instance)

    # --- Schedule jobs ---
    from moneyclaw.scheduler.jobs import register_all_jobs

    register_all_jobs(
        scheduler=scheduler,
        brain=brain,
        risk=risk,
        notifier=notifier,
        memory=memory,
        crypto_feed=crypto_feed,
        storage=storage,
        settings=settings,
    )

    # Build tasks list
    tasks: list[asyncio.Task] = []

    # Start agent brain
    tasks.append(asyncio.create_task(brain.start()))

    # Start Telegram bot
    if telegram and settings.telegram.token:
        from moneyclaw.interface.telegram.bot import TelegramBot

        tg_bot = TelegramBot(
            token=settings.telegram.token,
            chat_id=settings.telegram.chat_id,
            brain=brain,
            memory=memory,
            llm=llm,
            strategies=strategies,
            risk=risk,
        )
        tasks.append(asyncio.create_task(tg_bot.start()))

    # Start web dashboard
    if web:
        import uvicorn

        from moneyclaw.interface.web.app import create_app

        app = create_app(
            brain=brain, memory=memory, llm=llm, strategies=strategies, risk=risk, executor=executor
        )
        config = uvicorn.Config(
            app, host=settings.web_host, port=settings.web_port, log_level="warning"
        )
        server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(server.serve()))

    mode = "DRY RUN" if dry_run else "LIVE"
    click.echo(f"MoneyClaw running [{mode}] — {len(strategy_classes)} strategies loaded")

    # Show discovered models
    model_info = []
    for m in available_models[:5]:  # Show top 5 models
        model_info.append(f"{m.display_name} ({m.cost_tier.name})")
    if len(available_models) > 5:
        model_info.append(f"... and {len(available_models) - 5} more")
    click.echo(f"LLM models: {', '.join(model_info)}")
    click.echo(f"Budget: ${settings.llm.daily_llm_budget:.2f}/day")

    if web:
        click.echo(f"Dashboard: http://{settings.web_host}:{settings.web_port}")

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        await brain.stop()
        storage.close()
        await memory.close()


def _instantiate_strategy(cls, *, crypto_feed, stock_feed, executor, exchange_manager):
    """Create a strategy instance, injecting dependencies based on its constructor."""
    name = cls.__name__ if hasattr(cls, "__name__") else str(cls)

    import inspect

    sig = inspect.signature(cls.__init__)
    kwargs: dict[str, Any] = {}
    for param_name in sig.parameters:
        if param_name == "self":
            continue
        if param_name == "feed" and "Crypto" in name:
            kwargs["feed"] = crypto_feed
        elif param_name == "feed" and "Stock" in name:
            kwargs["feed"] = stock_feed
        elif param_name == "executor":
            kwargs["executor"] = executor
        elif param_name == "exchange_manager":
            kwargs["exchange_manager"] = exchange_manager

    try:
        return cls(**kwargs)
    except TypeError:
        return cls()


@main.command()
def version() -> None:
    """Show version."""
    from moneyclaw import __version__

    click.echo(f"MoneyClaw v{__version__}")


@main.command()
def status() -> None:
    """Show agent status (reads local DB)."""
    asyncio.run(_status())


async def _status() -> None:
    from moneyclaw.agent.memory import Memory
    from moneyclaw.config.settings import Settings

    settings = Settings()
    memory = Memory(db_path=settings.db_path)
    await memory.init()

    pnl = await memory.today_pnl()
    pending = await memory.pending_count()
    history = await memory.get_history(limit=5)

    mode = "DRY RUN" if settings.exchange.dry_run else "LIVE"
    pnl_sign = "+" if pnl >= 0 else ""
    click.echo(f"MoneyClaw [{mode}]")
    click.echo(f"  Today P&L:  {pnl_sign}${pnl:.2f}")
    click.echo(f"  Pending:    {pending} approvals")
    click.echo(f"  Recent trades: {len(history)}")
    for h in history:
        pl = h["profit_loss"]
        s = "+" if pl >= 0 else ""
        click.echo(f"    {h['strategy']:20s} {h['title'][:30]:30s} {s}${pl:.2f}")
    await memory.close()


@main.command()
def strategies() -> None:
    """List discovered strategies."""
    from moneyclaw.config.settings import Settings
    from moneyclaw.plugins.loader import discover_strategies

    settings = Settings()
    classes = discover_strategies(settings.strategies_dir)
    if not classes:
        click.echo("No strategies found.")
        return
    click.echo(f"Found {len(classes)} strategies:")
    for cls in classes:
        layer = getattr(cls, "min_llm_layer", "?")
        risk = getattr(cls, "risk_level", "?")
        click.echo(f"  {cls.name:25s} L{layer}  risk={risk:8s} {cls.description}")


@main.command()
def cost() -> None:
    """Show LLM cost summary (reads local tracker state)."""
    from moneyclaw.config.settings import Settings
    from moneyclaw.llm.cost_tracker import CostTracker

    settings = Settings()
    tracker = CostTracker(daily_budget=settings.llm.daily_llm_budget)
    click.echo("LLM Cost (current session)")
    click.echo(f"  Budget:     ${settings.llm.daily_llm_budget:.2f}/day")
    click.echo(f"  Today cost: ${tracker.today_cost:.4f}")
    click.echo(f"  Today calls: {tracker.today_calls}")
    click.echo(f"  Over budget: {'Yes' if tracker.is_over_budget() else 'No'}")


@main.command()
def models() -> None:
    """List discovered LLM models."""
    asyncio.run(_models())


async def _models() -> None:
    """Async model discovery and display."""
    from moneyclaw.config.settings import Settings
    from moneyclaw.llm.model_discovery import ModelDiscoveryService
    from moneyclaw.llm.model_registry import SmartModelRegistry

    settings = Settings()
    discovery = ModelDiscoveryService(timeout=settings.llm.model_discovery_timeout)
    registry = SmartModelRegistry(discovery_service=discovery)

    click.echo("Discovering models...")
    models = await registry.discover()

    if not models:
        click.echo("No models found. Check your API keys in .env file.")
        return

    # Group by provider
    by_provider: dict[str, list] = {}
    for m in models:
        by_provider.setdefault(m.provider, []).append(m)

    for provider, provider_models in sorted(by_provider.items()):
        click.echo(f"\n{provider.upper()}:")
        for m in sorted(provider_models, key=lambda x: x.capability_score, reverse=True):
            cost_str = f"${m.estimated_cost_per_call:.6f}" if m.cost_per_1k_input > 0 else "FREE"
            click.echo(
                f"  {m.display_name:30s} "
                f"cap={m.capability_score:.2f} "
                f"cost={cost_str:12s} "
                f"ctx={m.context_length:,}"
            )


@main.command()
def pause() -> None:
    """Pause the agent via the web API."""
    import httpx

    from moneyclaw.config.settings import Settings

    settings = Settings()
    url = f"http://127.0.0.1:{settings.web_port}/api/pause"
    try:
        resp = httpx.post(url, timeout=5)
        click.echo(f"Agent paused: {resp.json()}")
    except httpx.ConnectError:
        click.echo("Could not connect — is the agent running?")


@main.command()
def resume() -> None:
    """Resume the agent via the web API."""
    import httpx

    from moneyclaw.config.settings import Settings

    settings = Settings()
    url = f"http://127.0.0.1:{settings.web_port}/api/resume"
    try:
        resp = httpx.post(url, timeout=5)
        click.echo(f"Agent resumed: {resp.json()}")
    except httpx.ConnectError:
        click.echo("Could not connect — is the agent running?")


if __name__ == "__main__":
    main()
