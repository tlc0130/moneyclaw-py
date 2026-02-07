"""Tests for the event bus."""

from __future__ import annotations

import pytest

from moneyclaw.scheduler.events import Event, EventBus, EventType


class TestEventBus:
    @pytest.mark.asyncio
    async def test_emit_calls_handler(self) -> None:
        bus = EventBus()
        received = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.on(EventType.PRICE_ALERT, handler)
        await bus.emit(Event(type=EventType.PRICE_ALERT, data={"price": 100}))

        assert len(received) == 1
        assert received[0].data["price"] == 100

    @pytest.mark.asyncio
    async def test_no_handler_is_fine(self) -> None:
        bus = EventBus()
        # Should not raise
        await bus.emit(Event(type=EventType.NEWS_ALERT, data={}))

    @pytest.mark.asyncio
    async def test_multiple_handlers(self) -> None:
        bus = EventBus()
        results: list[str] = []

        async def h1(event: Event) -> None:
            results.append("h1")

        async def h2(event: Event) -> None:
            results.append("h2")

        bus.on(EventType.DEAL_FOUND, h1)
        bus.on(EventType.DEAL_FOUND, h2)
        await bus.emit(Event(type=EventType.DEAL_FOUND, data={}))

        assert set(results) == {"h1", "h2"}
