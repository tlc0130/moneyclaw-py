"""Scheduler engine — wraps APScheduler for cron-style and interval jobs."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = structlog.get_logger()

AsyncJobFunc = Callable[..., Coroutine[Any, Any, Any]]


class Scheduler:
    """Manages scheduled jobs (data collection, reports, etc.)."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._jobs: dict[str, str] = {}  # name → job_id

    def add_interval(
        self,
        name: str,
        func: AsyncJobFunc,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        **kwargs: Any,
    ) -> None:
        """Add a job that runs at a fixed interval."""
        job = self._scheduler.add_job(
            func,
            IntervalTrigger(seconds=seconds, minutes=minutes, hours=hours),
            id=name,
            name=name,
            replace_existing=True,
            kwargs=kwargs,
        )
        self._jobs[name] = job.id
        log.info("scheduler.job_added", name=name, type="interval")

    def add_cron(
        self,
        name: str,
        func: AsyncJobFunc,
        hour: int | str = "*",
        minute: int | str = "0",
        day_of_week: str = "*",
        **kwargs: Any,
    ) -> None:
        """Add a cron-style job."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute, day_of_week=day_of_week),
            id=name,
            name=name,
            replace_existing=True,
            kwargs=kwargs,
        )
        self._jobs[name] = job.id
        log.info("scheduler.job_added", name=name, type="cron")

    def remove(self, name: str) -> None:
        job_id = self._jobs.pop(name, None)
        if job_id:
            self._scheduler.remove_job(job_id)

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
            log.info("scheduler.started", jobs=len(self._jobs))

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler.stopped")

    @property
    def jobs(self) -> list[str]:
        return list(self._jobs.keys())
