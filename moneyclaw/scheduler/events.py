"""Event-driven triggers — react to market events in real-time."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

log = structlog.get_logger()


class EventType(Enum):
    PRICE_ALERT = "price_alert"
    NEWS_ALERT = "news_alert"
    DEAL_FOUND = "deal_found"
    TRADE_EXECUTED = "trade_executed"
    APPROVAL_RECEIVED = "approval_received"
    ERROR = "error"


@dataclass
class Event:
    type: EventType
    data: dict[str, Any]
    source: str = ""


EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Simple pub-sub event bus for internal coordination."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = {}

    def on(self, event_type: EventType, handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def emit(self, event: Event) -> None:
        handlers = self._handlers.get(event.type, [])
        if not handlers:
            return

        log.debug("event.emit", type=event.type.value, handlers=len(handlers))
        await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )
