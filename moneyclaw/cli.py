"""CLI entry point — `moneyclaw run`, `moneyclaw status`, etc."""

from __future__ import annotations

import asyncio
import logging
import socket
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


def _can_bind_web_port(host: str, port: int) -> bool:
    """Return True when the configured web port is available for binding."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _summarize_storage_error(error: Exception) -> str:
    """Condense noisy DuckDB open errors into a short operator-friendly message."""
    text = " ".join(str(error).split())
    lowered = text.lower()
    if "being used by another process" in lowered or "file is already open in" in lowered:
        return "database file is locked by another process"
    return text


def _force_ipv4() -> None:
    """Force all DNS resolution in this process to IPv4.

    Binance.US rejects SIGNED API calls made over IPv6 (error -71012
    "IPv6 not supported"). Sync clients (requests) honor /etc/gai.conf IPv4
    preference, but aiohttp (used by ccxt.async_support, the live order/balance
    path) does not — so we pin getaddrinfo to AF_INET process-wide. Affects only
    this process, not the host (SSH etc. are untouched).
    """
    import socket

    if getattr(socket, "_moneyclaw_ipv4_forced", False):
        return

    # 1) Sync clients (requests/httpx/sync-ccxt + aiohttp's ThreadedResolver).
    _orig_getaddrinfo = socket.getaddrinfo

    def _ipv4_only(host, port, family=0, *args, **kwargs):
        return _orig_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = _ipv4_only

    # 2) aiohttp (ccxt.async_support — live order/balance path). When aiodns is
    #    installed, aiohttp uses c-ares and bypasses socket.getaddrinfo, so also
    #    force the TCPConnector itself to IPv4 (resolver-agnostic).
    try:
        import functools

        import aiohttp

        _orig_conn_init = aiohttp.TCPConnector.__init__

        @functools.wraps(_orig_conn_init)
        def _ipv4_conn_init(self, *args, **kwargs):
            kwargs.setdefault("family", socket.AF_INET)
            return _orig_conn_init(self, *args, **kwargs)

        aiohttp.TCPConnector.__init__ = _ipv4_conn_init
    except Exception:
        pass

    socket._moneyclaw_ipv4_forced = True


async def _run(web: bool, telegram: bool) -> None:
    """Async entry point — wire everything together and start."""
    _force_ipv4()  # must run before any network call (LLM discovery, exchanges, feeds)
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
    from moneyclaw.llm.router import LLMLayer, LLMRouter
    from moneyclaw.llm.model_registry import SmartModelRegistry
    from moneyclaw.llm.performance_tracker import PerformanceTracker
    from moneyclaw.llm.providers.base import NoOpLLMProvider
    from moneyclaw.llm.smart_router import SmartRouter
    from moneyclaw.plugins.loader import discover_strategies
    from moneyclaw.plugins.registry import StrategyRegistry
    from moneyclaw.scheduler.engine import Scheduler

    settings = Settings()

    if web and not _can_bind_web_port(settings.web_host, settings.web_port):
        raise click.ClickException(
            f"Web dashboard port {settings.web_port} is already in use. "
            f"Stop the existing service or change WEB_PORT before starting MoneyClaw again."
        )

    # Initialize SmartRouter components with API keys from settings
    api_keys = {
        "OPENAI_API_KEY": settings.llm.openai_api_key,
        "ANTHROPIC_API_KEY": settings.llm.anthropic_api_key,
        "DEEPSEEK_API_KEY": settings.llm.deepseek_api_key,
        "GROQ_API_KEY": settings.llm.groq_api_key,
        "GOOGLE_API_KEY": settings.llm.google_api_key,
        "MOONSHOT_API_KEY": settings.llm.moonshot_api_key,
    }
    discovery = ModelDiscoveryService(
        timeout=settings.llm.llm_discovery_timeout,
        api_keys={k: v for k, v in api_keys.items() if v},  # 只传递非空的key
    )
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
    if not available_models and settings.risk.dry_run:
        click.echo("No healthy LLM models found, using dry-run fallback evaluator.")
        llm = LLMRouter(
            providers={LLMLayer.LOCAL: NoOpLLMProvider()},
            cost_tracker=cost_tracker,
        )

    # Memory
    memory = Memory(db_path=settings.db_path)
    await memory.init()

    # Trading execution
    exchange_mgr = ExchangeManager()
    dry_run = settings.exchange.dry_run
    default_exchange = settings.exchange.default_exchange
    if not dry_run:
        api_key, secret, password = _resolve_exchange_keys(settings.exchange, default_exchange)
        if api_key and secret:
            exchange_mgr.connect(default_exchange, api_key, secret, password)
        else:
            click.echo(
                f"WARNING: live mode (dry_run=false) but no API keys resolved for "
                f"'{default_exchange}'. Orders will fail. Set EXCHANGE_{default_exchange.upper()}_API_KEY "
                f"(or EXCHANGE_BINANCE_API_KEY as a fallback).",
                err=True,
            )
    executor = TradeExecutor(
        exchange_mgr,
        dry_run=dry_run,
        default_exchange=default_exchange,
        max_order_usd=(settings.exchange.max_order_usd or None),
    )

    # Data feeds
    from moneyclaw.data.feeds.crypto import CryptoFeed
    from moneyclaw.data.feeds.news import NewsFeed
    from moneyclaw.data.feeds.stocks import StockFeed
    from moneyclaw.data.storage import MarketStorage

    crypto_feed = CryptoFeed()
    stock_feed = StockFeed()
    _news_feed = NewsFeed()  # noqa: F841 — reserved for news-based strategies
    storage_path = settings.data.duckdb_path
    try:
        storage = MarketStorage(storage_path)
    except Exception as e:
        fallback_path = "data/market.runtime.duckdb"
        reason = _summarize_storage_error(e)
        click.echo(
            f"Warning: could not open {storage_path} ({reason}). Falling back to {fallback_path}.",
            err=True,
        )
        storage = MarketStorage(fallback_path)

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

    # Discover and register strategies — inject deps based on what they need.
    # Also scan strategies_live/ alongside the primary dir so live strategies
    # (e.g. combined_crypto_strategy) are loaded without requiring a config change.
    from pathlib import Path as _Path

    strategy_classes = discover_strategies(settings.strategies_dir)
    _live_dir = _Path("strategies_live")
    if _live_dir.exists() and _live_dir.resolve() != _Path(settings.strategies_dir).resolve():
        strategy_classes = strategy_classes + discover_strategies(_live_dir)
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

    # Create strategy chat interface for AI-powered strategy management
    from moneyclaw.agent.strategy_chat import StrategyChatInterface

    strategy_chat = StrategyChatInterface(llm_router=llm, strategy_registry=strategies)

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
            strategy_chat=strategy_chat,
        )
        tasks.append(asyncio.create_task(tg_bot.start()))

    # Start web dashboard
    if web:
        import uvicorn

        from moneyclaw.interface.web.app import create_app

        app = create_app(
            brain=brain, memory=memory, llm=llm, strategies=strategies, risk=risk, executor=executor, strategy_chat=strategy_chat
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
    if model_info:
        click.echo(f"LLM models: {', '.join(model_info)}")
    else:
        click.echo("LLM models: none available (using dry-run fallback)")
    click.echo(f"Budget: ${settings.llm.daily_llm_budget:.2f}/day")

    if web:
        click.echo(f"Dashboard: http://{settings.web_host}:{settings.web_port}")

    # Graceful shutdown on SIGTERM/SIGINT (systemd stop) so we don't hang ~90s
    # waiting for SIGKILL. A stop signal cancels the long-running tasks, then the
    # finally block closes resources promptly.
    import contextlib
    import signal

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, AttributeError):
            pass  # not supported on Windows

    gather_task = asyncio.gather(*tasks)
    stop_waiter = asyncio.ensure_future(stop_event.wait())
    try:
        await asyncio.wait({gather_task, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_waiter.cancel()
        with contextlib.suppress(Exception):
            await brain.stop()
        gather_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await gather_task
        storage.close()
        await memory.close()
        await exchange_mgr.close_all()  # close async ccxt aiohttp sessions


def _resolve_exchange_keys(ex_settings, exchange_id: str) -> tuple[str, str, str]:
    """Resolve (api_key, secret, password) for an exchange.

    Prefers the exchange-specific slot (e.g. EXCHANGE_BINANCEUS_API_KEY) and falls
    back to the generic EXCHANGE_BINANCE_API_KEY slot — a common place to stash keys.
    """
    direct_key = getattr(ex_settings, f"{exchange_id}_api_key", "") or ""
    direct_secret = getattr(ex_settings, f"{exchange_id}_secret", "") or ""
    password = getattr(ex_settings, f"{exchange_id}_password", "") or ""
    if direct_key and direct_secret:
        return direct_key, direct_secret, password
    return ex_settings.binance_api_key, ex_settings.binance_secret, password


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
    from moneyclaw.llm.router import LLMLayer, LLMRouter
    from moneyclaw.llm.model_registry import SmartModelRegistry

    settings = Settings()

    # Pass API keys from settings
    api_keys = {
        "OPENAI_API_KEY": settings.llm.openai_api_key,
        "ANTHROPIC_API_KEY": settings.llm.anthropic_api_key,
        "DEEPSEEK_API_KEY": settings.llm.deepseek_api_key,
        "GROQ_API_KEY": settings.llm.groq_api_key,
        "GOOGLE_API_KEY": settings.llm.google_api_key,
        "MOONSHOT_API_KEY": settings.llm.moonshot_api_key,
    }
    discovery = ModelDiscoveryService(
        timeout=settings.llm.llm_discovery_timeout,
        api_keys={k: v for k, v in api_keys.items() if v},
    )
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


@main.command()
@click.argument("message", nargs=-1, required=True)
def strategy(message: tuple[str, ...]) -> None:
    """AI策略管理 — 使用自然语言生成、优化和管理策略.

    示例:
        moneyclaw strategy "创建一个定投比特币的策略"
        moneyclaw strategy "优化crypto_dca策略"
        moneyclaw strategy "列出所有策略"
    """
    msg = " ".join(message)
    asyncio.run(_strategy_chat(msg))


async def _strategy_chat(message: str) -> None:
    """处理策略聊天命令."""
    from moneyclaw.agent.strategy_chat import StrategyChatInterface
    from moneyclaw.config.settings import Settings
    from moneyclaw.llm.budget_manager import BudgetManager, BudgetPolicy
    from moneyclaw.llm.cache import ResponseCache
    from moneyclaw.llm.cost_tracker import CostTracker
    from moneyclaw.llm.model_discovery import ModelDiscoveryService
    from moneyclaw.llm.router import LLMLayer, LLMRouter
    from moneyclaw.llm.model_registry import SmartModelRegistry
    from moneyclaw.llm.performance_tracker import PerformanceTracker
    from moneyclaw.llm.providers.base import NoOpLLMProvider
    from moneyclaw.llm.smart_router import SmartRouter
    from moneyclaw.plugins.registry import StrategyRegistry

    settings = Settings()

    # 初始化LLM组件
    api_keys = {
        "OPENAI_API_KEY": settings.llm.openai_api_key,
        "ANTHROPIC_API_KEY": settings.llm.anthropic_api_key,
        "DEEPSEEK_API_KEY": settings.llm.deepseek_api_key,
        "GROQ_API_KEY": settings.llm.groq_api_key,
        "GOOGLE_API_KEY": settings.llm.google_api_key,
        "MOONSHOT_API_KEY": settings.llm.moonshot_api_key,
    }
    discovery = ModelDiscoveryService(
        timeout=settings.llm.llm_discovery_timeout,
        api_keys={k: v for k, v in api_keys.items() if v},
    )
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

    # 初始化策略注册表
    strategy_registry = StrategyRegistry()

    click.echo("🤖 初始化AI策略管理系统...")
    await llm.initialize()

    # 创建策略聊天接口
    strategy_chat = StrategyChatInterface(
        llm_router=llm,
        strategy_registry=strategy_registry,
    )

    # 处理消息
    click.echo(f"👤 用户: {message}")
    click.echo()

    response = await strategy_chat.handle_message(message)

    # 显示响应
    if response.success:
        click.echo(f"🤖 AI: {response.message}")

        # 检查是否需要确认保存
        if response.data and response.data.get("pending_confirm"):
            strategy = response.data.get("strategy")
            if strategy:
                click.echo()
                confirm = click.confirm("是否保存此策略?", default=False)
                if confirm:
                    save_response = await strategy_chat.confirm_save_strategy(strategy)
                    click.echo(f"🤖 AI: {save_response.message}")
                else:
                    click.echo("❎ 已取消保存策略。")
    else:
        click.echo(f"⚠️  {response.message}")


if __name__ == "__main__":
    main()
