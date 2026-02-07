"""CLI entry point — `moneyclaw run`, `moneyclaw status`, etc."""

from __future__ import annotations

import asyncio
import logging

import click
import structlog

_LOG_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


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
    from moneyclaw.interface.telegram.notify import Notifier
    from moneyclaw.llm.cache import ResponseCache
    from moneyclaw.llm.cost_tracker import CostTracker
    from moneyclaw.llm.providers.litellm_provider import LiteLLMProvider
    from moneyclaw.llm.providers.ollama import OllamaProvider
    from moneyclaw.llm.router import LLMLayer, LLMRouter
    from moneyclaw.plugins.loader import discover_strategies
    from moneyclaw.plugins.registry import StrategyRegistry
    from moneyclaw.scheduler.engine import Scheduler

    settings = Settings()

    # Build LLM providers
    providers: dict[LLMLayer, any] = {}

    # Layer 1: local (Ollama) — always try
    providers[LLMLayer.LOCAL] = OllamaProvider(
        model=settings.llm.ollama_model,
        base_url=settings.llm.ollama_base_url,
    )

    # Layer 2: cheap APIs
    if settings.llm.deepseek_api_key:
        providers[LLMLayer.CHEAP] = LiteLLMProvider(
            model="deepseek/deepseek-chat",
            api_key=settings.llm.deepseek_api_key,
        )
    elif settings.llm.groq_api_key:
        providers[LLMLayer.CHEAP] = LiteLLMProvider(
            model="groq/llama-3.3-70b-versatile",
            api_key=settings.llm.groq_api_key,
        )

    # Layer 3: premium
    if settings.llm.anthropic_api_key:
        providers[LLMLayer.PREMIUM] = LiteLLMProvider(
            model="claude-sonnet-4-5-20250929",
            api_key=settings.llm.anthropic_api_key,
        )
    elif settings.llm.openai_api_key:
        providers[LLMLayer.PREMIUM] = LiteLLMProvider(
            model="gpt-4o",
            api_key=settings.llm.openai_api_key,
        )

    cost_tracker = CostTracker(daily_budget=settings.llm.daily_llm_budget)
    llm = LLMRouter(providers=providers, cost_tracker=cost_tracker, cache=ResponseCache())

    # Memory
    memory = Memory(db_path=settings.db_path)
    await memory.init()

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
    )

    # Discover and register strategies
    strategy_classes = discover_strategies(settings.strategies_dir)
    for cls in strategy_classes:
        await strategies.register(cls())

    # Schedule daily risk reset
    scheduler.add_cron("risk_reset", _make_risk_reset(risk), hour=0, minute=0)

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

        app = create_app(brain=brain, memory=memory, llm=llm, strategies=strategies, risk=risk)
        config = uvicorn.Config(app, host=settings.web_host, port=settings.web_port, log_level="warning")
        server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(server.serve()))

    click.echo(f"MoneyClaw running — {len(strategy_classes)} strategies loaded")
    click.echo(f"LLM layers: {', '.join(l.name for l in providers)}")
    if web:
        click.echo(f"Dashboard: http://{settings.web_host}:{settings.web_port}")

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        await brain.stop()
        await memory.close()


def _make_risk_reset(risk: RiskManager):
    async def _reset() -> None:
        risk.reset_daily()

    return _reset


@main.command()
def version() -> None:
    """Show version."""
    from moneyclaw import __version__

    click.echo(f"MoneyClaw v{__version__}")


if __name__ == "__main__":
    main()
