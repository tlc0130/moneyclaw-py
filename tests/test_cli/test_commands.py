"""Tests for CLI commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from moneyclaw.cli import main

_SETTINGS = "moneyclaw.config.settings.Settings"
_MEMORY = "moneyclaw.agent.memory.Memory"
_DISCOVER = "moneyclaw.plugins.loader.discover_strategies"


class TestVersionCommand:
    def test_version_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "MoneyClaw v" in result.output


class TestStatusCommand:
    def test_status_output(self) -> None:
        runner = CliRunner()
        mock_memory = AsyncMock()
        mock_memory.today_pnl = AsyncMock(return_value=12.50)
        mock_memory.pending_count = AsyncMock(return_value=2)
        mock_memory.get_history = AsyncMock(
            return_value=[
                {"strategy": "crypto_dca", "title": "Buy BTC", "profit_loss": 5.0},
            ]
        )
        mock_memory.close = AsyncMock()

        with (
            patch(_MEMORY, return_value=mock_memory),
            patch(_SETTINGS) as mock_settings_cls,
        ):
            s = MagicMock()
            s.db_path = ":memory:"
            s.exchange.dry_run = True
            mock_settings_cls.return_value = s

            result = runner.invoke(main, ["status"])

        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "+$12.50" in result.output
        assert "2 approvals" in result.output
        assert "crypto_dca" in result.output


class TestStrategiesCommand:
    def test_lists_strategies(self) -> None:
        runner = CliRunner()

        class FakeStrategy:
            name = "test_strat"
            description = "A test strategy"
            min_llm_layer = 1
            risk_level = "low"

        with (
            patch(_SETTINGS) as mock_settings_cls,
            patch(_DISCOVER, return_value=[FakeStrategy]),
        ):
            mock_settings_cls.return_value = MagicMock(strategies_dir="strategies")
            result = runner.invoke(main, ["strategies"])

        assert result.exit_code == 0
        assert "test_strat" in result.output
        assert "L1" in result.output
        assert "A test strategy" in result.output

    def test_no_strategies(self) -> None:
        runner = CliRunner()
        with (
            patch(_SETTINGS) as mock_settings_cls,
            patch(_DISCOVER, return_value=[]),
        ):
            mock_settings_cls.return_value = MagicMock(strategies_dir="strategies")
            result = runner.invoke(main, ["strategies"])

        assert result.exit_code == 0
        assert "No strategies found" in result.output


class TestCostCommand:
    def test_cost_output(self) -> None:
        runner = CliRunner()
        with patch(_SETTINGS) as mock_settings_cls:
            s = MagicMock()
            s.llm.daily_llm_budget = 1.0
            mock_settings_cls.return_value = s

            result = runner.invoke(main, ["cost"])

        assert result.exit_code == 0
        assert "$1.00/day" in result.output
        assert "Over budget: No" in result.output


class TestPauseResumeCommands:
    def test_pause_connection_error(self) -> None:
        runner = CliRunner()
        with patch(_SETTINGS) as mock_settings_cls:
            mock_settings_cls.return_value = MagicMock(web_port=9999)
            result = runner.invoke(main, ["pause"])

        assert result.exit_code == 0
        assert "Could not connect" in result.output

    def test_resume_connection_error(self) -> None:
        runner = CliRunner()
        with patch(_SETTINGS) as mock_settings_cls:
            mock_settings_cls.return_value = MagicMock(web_port=9999)
            result = runner.invoke(main, ["resume"])

        assert result.exit_code == 0
        assert "Could not connect" in result.output
