"""Crypto Price Alert — Layer 0 strategy that monitors crypto prices and alerts on thresholds.

The simplest possible strategy: no LLM needed, just price checks.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy, load_strategy_config

log = structlog.get_logger()

# CoinGecko free API (no key needed, 30 calls/min)
COINGECKO_API = "https://api.coingecko.com/api/v3"

DEFAULT_ALERTS = [
    {"coin": "bitcoin", "condition": "above", "threshold": 100000, "message": "BTC above $100k!"},
    {
        "coin": "bitcoin",
        "condition": "below",
        "threshold": 50000,
        "message": "BTC below $50k — buy opportunity?",
    },
    {"coin": "ethereum", "condition": "below", "threshold": 2000, "message": "ETH below $2k"},
]


@dataclass
class PriceAlert:
    coin: str  # e.g. "bitcoin"
    condition: str  # "above" or "below"
    threshold: float  # USD
    message: str = ""


class CryptoPriceAlert(Strategy):
    """Monitor crypto prices and fire alerts when thresholds are crossed.

    This is a Layer 0 (rules-only) strategy — zero LLM cost.
    """

    name = "crypto_price_alert"
    description = "Monitor crypto prices, alert on threshold crossings"
    risk_level = "low"
    min_llm_layer = 0

    def __init__(self, alerts: list[PriceAlert] | None = None) -> None:
        cfg = load_strategy_config(CryptoPriceAlert)
        alert_defs = cfg.get("alerts", DEFAULT_ALERTS) if not alerts else None
        self._alerts = alerts or [PriceAlert(**a) for a in alert_defs]
        self._last_prices: dict[str, float] = {}
        self._triggered: set[str] = set()

    async def scan(self) -> list[Opportunity]:
        """Check current prices against alert thresholds."""
        coins = list({a.coin for a in self._alerts})
        prices = await self._fetch_prices(coins)
        if not prices:
            return []

        self._last_prices = prices
        opportunities = []

        for alert in self._alerts:
            price = prices.get(alert.coin)
            if price is None:
                continue

            alert_key = f"{alert.coin}_{alert.condition}_{alert.threshold}"
            triggered = False

            if (
                alert.condition == "above"
                and price > alert.threshold
                or alert.condition == "below"
                and price < alert.threshold
            ):
                triggered = True

            if triggered and alert_key not in self._triggered:
                self._triggered.add(alert_key)
                opportunities.append(
                    Opportunity(
                        strategy_name=self.name,
                        title=(
                            alert.message
                            or f"{alert.coin}: ${price:,.2f}"
                            f" ({alert.condition} ${alert.threshold:,.2f})"
                        ),
                        money_involved=0,  # Alerts don't involve money
                        data={
                            "coin": alert.coin,
                            "price": price,
                            "condition": alert.condition,
                            "threshold": alert.threshold,
                        },
                        pre_score=0.8,  # Pre-scored, no LLM needed
                    )
                )
            elif not triggered:
                # Reset trigger so it fires again next time condition is met
                self._triggered.discard(alert_key)

        return opportunities

    async def evaluate(self, opp: Opportunity) -> Score:
        """Already pre-scored — just pass through."""
        return Score(value=opp.pre_score or 0.8, threshold=0.5)

    async def execute(self, opp: Opportunity) -> Result:
        """For alerts, 'execution' is just confirming the alert was sent."""
        # The notification is handled by the brain/notifier
        return Result(success=True, profit_loss=0, details=opp.data)

    def estimate_roi(self) -> float:
        """Alerts are free to run, ROI is infinite in a sense — but 0 direct profit."""
        return 0.0

    @staticmethod
    async def _fetch_prices(coins: list[str]) -> dict[str, float]:
        """Fetch prices from CoinGecko free API."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{COINGECKO_API}/simple/price",
                    params={"ids": ",".join(coins), "vs_currencies": "usd"},
                )
                resp.raise_for_status()
                data = resp.json()
                return {coin: data[coin]["usd"] for coin in coins if coin in data}
        except Exception:
            log.exception("crypto_price_alert.fetch_error")
            return {}
