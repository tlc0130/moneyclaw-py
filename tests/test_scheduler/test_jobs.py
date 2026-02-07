"""Tests for scheduled jobs registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from moneyclaw.scheduler.jobs import register_all_jobs


class TestRegisterAllJobs:
    def test_registers_expected_jobs(self) -> None:
        scheduler = MagicMock()
        brain = MagicMock()
        risk = MagicMock()
        notifier = AsyncMock()
        memory = AsyncMock()
        crypto_feed = AsyncMock()
        storage = MagicMock()
        settings = MagicMock()
        settings.data.price_poll_interval = 300

        # Track what gets registered
        registered_names = []
        scheduler.add_cron = MagicMock(
            side_effect=lambda name, *a, **kw: registered_names.append(name)
        )
        scheduler.add_interval = MagicMock(
            side_effect=lambda name, *a, **kw: registered_names.append(name)
        )
        scheduler.jobs = registered_names

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

        assert "risk_reset" in registered_names
        assert "collect_crypto_prices" in registered_names
        assert "daily_report" in registered_names
        assert len(registered_names) == 3
