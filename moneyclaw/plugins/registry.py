"""Strategy registry — manages loaded strategies and their lifecycle."""

from __future__ import annotations

import structlog

from moneyclaw.plugins.base import Strategy

log = structlog.get_logger()


class StrategyRegistry:
    """Manages loaded and active strategies."""

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}
        self._enabled: set[str] = set()

    async def register(self, strategy: Strategy) -> None:
        """Register and initialize a strategy."""
        await strategy.setup()
        self._strategies[strategy.name] = strategy
        self._enabled.add(strategy.name)
        log.info("registry.registered", strategy=strategy.name)

    async def unregister(self, name: str) -> None:
        """Unload a strategy."""
        strategy = self._strategies.pop(name, None)
        if strategy:
            self._enabled.discard(name)
            await strategy.teardown()
            log.info("registry.unregistered", strategy=name)

    def enable(self, name: str) -> bool:
        if name in self._strategies:
            self._enabled.add(name)
            return True
        return False

    def disable(self, name: str) -> bool:
        return (
            bool(self._enabled.discard(name))
            or name in self._strategies
            and name not in self._enabled
        )

    def get(self, name: str) -> Strategy | None:
        return self._strategies.get(name)

    @property
    def active(self) -> list[Strategy]:
        """Strategies that are both registered and enabled."""
        return [s for name, s in self._strategies.items() if name in self._enabled]

    @property
    def all_strategies(self) -> dict[str, Strategy]:
        return dict(self._strategies)

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    def status(self) -> list[dict]:
        """Get status of all strategies."""
        return [
            {
                "name": name,
                "description": s.description,
                "risk_level": s.risk_level,
                "enabled": name in self._enabled,
                "roi_estimate": s.estimate_roi(),
            }
            for name, s in self._strategies.items()
        ]
