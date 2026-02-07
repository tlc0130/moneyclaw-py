"""Tests for strategy config.yaml loading."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from moneyclaw.plugins.base import Strategy, load_strategy_config


class _DummyStrategy(Strategy):
    name = "dummy"
    description = "test"

    async def scan(self):
        return []

    async def evaluate(self, opp):
        pass

    async def execute(self, opp):
        pass

    def estimate_roi(self):
        return 1.0


class TestLoadStrategyConfig:
    def test_returns_empty_when_no_file(self) -> None:
        result = load_strategy_config(_DummyStrategy)
        assert result == {}

    def test_loads_yaml_from_strategy_dir(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("coin: bitcoin\namount_usd: 25.0\n")

        # Patch __file__ of the module to point to tmp_path
        import types

        fake_module = types.ModuleType("fake_strat")
        fake_module.__file__ = str(tmp_path / "__init__.py")

        with patch.dict("sys.modules", {_DummyStrategy.__module__: fake_module}):
            result = load_strategy_config(_DummyStrategy)

        assert result == {"coin": "bitcoin", "amount_usd": 25.0}

    def test_returns_empty_on_invalid_yaml(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text(": : invalid: [\n")

        import types

        fake_module = types.ModuleType("fake_strat")
        fake_module.__file__ = str(tmp_path / "__init__.py")

        with patch.dict("sys.modules", {_DummyStrategy.__module__: fake_module}):
            result = load_strategy_config(_DummyStrategy)

        assert result == {}

    def test_returns_empty_when_yaml_is_not_dict(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("- item1\n- item2\n")

        import types

        fake_module = types.ModuleType("fake_strat")
        fake_module.__file__ = str(tmp_path / "__init__.py")

        with patch.dict("sys.modules", {_DummyStrategy.__module__: fake_module}):
            result = load_strategy_config(_DummyStrategy)

        assert result == {}


class TestStrategiesUseConfig:
    def test_crypto_dca_uses_config(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("coin: ethereum\nsymbol: ETH/USDT\namount_usd: 50.0\n")

        import types

        fake_module = types.ModuleType("strategies.crypto_dca")
        fake_module.__file__ = str(tmp_path / "__init__.py")

        from strategies.crypto_dca import CryptoDCA

        with patch.dict("sys.modules", {"strategies.crypto_dca": fake_module}):
            s = CryptoDCA()

        assert s._coin == "ethereum"
        assert s._symbol == "ETH/USDT"
        assert s._amount_usd == 50.0

    def test_crypto_dca_explicit_params_override_config(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("coin: ethereum\namount_usd: 50.0\n")

        import types

        fake_module = types.ModuleType("strategies.crypto_dca")
        fake_module.__file__ = str(tmp_path / "__init__.py")

        from strategies.crypto_dca import CryptoDCA

        with patch.dict("sys.modules", {"strategies.crypto_dca": fake_module}):
            s = CryptoDCA(coin="bitcoin", amount_usd=100.0)

        assert s._coin == "bitcoin"
        assert s._amount_usd == 100.0

    def test_stock_dividend_uses_config(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("watchlist:\n  - AAPL\n  - MSFT\nmin_yield: 0.03\n")

        import types

        fake_module = types.ModuleType("strategies.stock_dividend")
        fake_module.__file__ = str(tmp_path / "__init__.py")

        from strategies.stock_dividend import StockDividend

        with patch.dict("sys.modules", {"strategies.stock_dividend": fake_module}):
            s = StockDividend()

        assert s._watchlist == ["AAPL", "MSFT"]
        assert s._min_yield == 0.03

    def test_smart_rebalance_uses_config(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text(
            "targets:\n  BTC/USDT: 0.50\n  ETH/USDT: 0.50\ndeviation_threshold: 0.10\n"
        )

        import types

        fake_module = types.ModuleType("strategies.smart_rebalance")
        fake_module.__file__ = str(tmp_path / "__init__.py")

        from strategies.smart_rebalance import SmartRebalance

        with patch.dict("sys.modules", {"strategies.smart_rebalance": fake_module}):
            s = SmartRebalance()

        assert s._targets == {"BTC/USDT": 0.50, "ETH/USDT": 0.50}
        assert s._threshold == 0.10
