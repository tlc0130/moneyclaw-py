"""Tests for the crypto_price_alert strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from strategies.crypto_price_alert import CryptoPriceAlert, PriceAlert


class TestCryptoPriceAlert:
    @pytest.mark.asyncio
    async def test_scan_detects_threshold_crossing(self) -> None:
        strategy = CryptoPriceAlert(alerts=[PriceAlert("bitcoin", "above", 50000, "BTC mooning!")])

        # Mock price above threshold
        with patch.object(
            CryptoPriceAlert,
            "_fetch_prices",
            new_callable=AsyncMock,
            return_value={"bitcoin": 55000},
        ):
            opps = await strategy.scan()
            assert len(opps) == 1
            assert opps[0].title == "BTC mooning!"
            assert opps[0].pre_score == 0.8

    @pytest.mark.asyncio
    async def test_scan_no_alert_when_below_threshold(self) -> None:
        strategy = CryptoPriceAlert(alerts=[PriceAlert("bitcoin", "above", 100000)])

        with patch.object(
            CryptoPriceAlert,
            "_fetch_prices",
            new_callable=AsyncMock,
            return_value={"bitcoin": 55000},
        ):
            opps = await strategy.scan()
            assert len(opps) == 0

    @pytest.mark.asyncio
    async def test_scan_below_condition(self) -> None:
        strategy = CryptoPriceAlert(alerts=[PriceAlert("ethereum", "below", 3000, "ETH cheap!")])

        with patch.object(
            CryptoPriceAlert,
            "_fetch_prices",
            new_callable=AsyncMock,
            return_value={"ethereum": 2500},
        ):
            opps = await strategy.scan()
            assert len(opps) == 1
            assert opps[0].data["price"] == 2500

    @pytest.mark.asyncio
    async def test_alert_only_fires_once(self) -> None:
        strategy = CryptoPriceAlert(alerts=[PriceAlert("bitcoin", "above", 50000)])

        mock_fetch = AsyncMock(return_value={"bitcoin": 55000})
        with patch.object(CryptoPriceAlert, "_fetch_prices", mock_fetch):
            opps1 = await strategy.scan()
            opps2 = await strategy.scan()
            assert len(opps1) == 1
            assert len(opps2) == 0  # Already triggered

    @pytest.mark.asyncio
    async def test_alert_resets_when_condition_unmet(self) -> None:
        strategy = CryptoPriceAlert(alerts=[PriceAlert("bitcoin", "above", 50000)])

        # Trigger
        with patch.object(
            CryptoPriceAlert,
            "_fetch_prices",
            new_callable=AsyncMock,
            return_value={"bitcoin": 55000},
        ):
            await strategy.scan()

        # Price drops below — should reset
        with patch.object(
            CryptoPriceAlert,
            "_fetch_prices",
            new_callable=AsyncMock,
            return_value={"bitcoin": 45000},
        ):
            await strategy.scan()

        # Price rises again — should fire again
        with patch.object(
            CryptoPriceAlert,
            "_fetch_prices",
            new_callable=AsyncMock,
            return_value={"bitcoin": 55000},
        ):
            opps = await strategy.scan()
            assert len(opps) == 1

    @pytest.mark.asyncio
    async def test_execute_returns_success(self) -> None:
        strategy = CryptoPriceAlert()
        from moneyclaw.plugins.base import Opportunity

        opp = Opportunity(
            strategy_name="crypto_price_alert",
            title="Test",
            data={"coin": "bitcoin", "price": 55000},
        )
        result = await strategy.execute(opp)
        assert result.success is True
        assert result.profit_loss == 0

    def test_estimate_roi(self) -> None:
        strategy = CryptoPriceAlert()
        assert strategy.estimate_roi() == 0.0  # Alerts don't directly make money
