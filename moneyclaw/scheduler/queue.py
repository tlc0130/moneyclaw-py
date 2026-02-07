"""Priority queue for tasks — higher-value opportunities execute first."""

from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class PrioritizedTask:
    priority: float  # Lower = higher priority (negate score for max-first)
    task: Any = field(compare=False)


class TaskQueue:
    """Async-safe priority queue for opportunity execution."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._heap: list[PrioritizedTask] = []
        self._lock = asyncio.Lock()
        self._maxsize = maxsize

    async def push(self, task: Any, priority: float) -> None:
        """Add a task. Priority: lower number = executes first."""
        async with self._lock:
            if len(self._heap) >= self._maxsize:
                # Drop lowest priority item
                heapq.heapreplace(self._heap, PrioritizedTask(priority=-priority, task=task))
            else:
                heapq.heappush(self._heap, PrioritizedTask(priority=-priority, task=task))

    async def pop(self) -> Any | None:
        """Get the highest-priority task."""
        async with self._lock:
            if self._heap:
                return heapq.heappop(self._heap).task
            return None

    @property
    def size(self) -> int:
        return len(self._heap)

    @property
    def empty(self) -> bool:
        return len(self._heap) == 0
