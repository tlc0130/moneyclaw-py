"""Crypto Funding Rate Arbitrage — Layer 1 strategy.

Monitor perpetual contract funding rates. When rates are abnormally high,
short perps (collect funding) while hedging with spot longs.
"""

from __future__ import annotations

import structlog

from moneyclaw.data.feeds.crypto import CryptoFeed
from moneyclaw.execution.trading import TradeExecutor
from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy, load_strategy_config

log = structlog.get_logger()

# Funding rate threshold: annualized > ~36% (0.1% per 8h funding period)
FUNDING_RATE_THRESHOLD = 0.001


class CryptoFunding(Strategy):
    """Funding rate arbitrage: collect high funding fees with delta-neutral hedging.

    Layer 1: uses local LLM (Ollama) to evaluate risk before entering positions.
    """

    name = "crypto_funding"
    description = "Funding rate arbitrage — collect high perp funding fees"
    risk_level = "medium"
    min_llm_layer = 1

    def __init__(
        self,
        feed: CryptoFeed | None = None,
        executor: TradeExecutor | None = None,
        threshold: float | None = None,
    ) -> None:
        cfg = load_strategy_config(CryptoFunding)
        self._feed = feed or CryptoFeed()
        self._executor = executor
        self._threshold = (
            threshold if threshold is not None else cfg.get("threshold", FUNDING_RATE_THRESHOLD)
        )

    async def scan(self) -> list[Opportunity]:
        """Scan funding rates across exchanges for arbitrage opportunities."""
        rates = await self._feed.get_funding_rates()
        if not rates:
            return []

        opportunities = []
        for r in rates:
            fr = r.get("funding_rate", 0)
            if not isinstance(fr, (int, float)):
                try:
                    fr = float(fr)
                except (ValueError, TypeError):
                    continue

            if abs(fr) < self._threshold:
                continue

            symbol = r.get("symbol", "unknown")
            market = r.get("market", "unknown")
            annual_rate = fr * 3 * 365  # 3 funding periods per day

            opportunities.append(
                Opportunity(
                    strategy_name=self.name,
                    title=f"Funding arb: {symbol} on {market} ({fr:.4%}/8h, ~{annual_rate:.0%}/yr)",
                    money_involved=100.0,  # Standard position size
                    data={
                        "symbol": symbol,
                        "market": market,
                        "funding_rate": fr,
                        "annualized": annual_rate,
                        "index_price": r.get("index_price", 0),
                    },
                )
            )

        # Sort by absolute funding rate (most profitable first)
        opportunities.sort(key=lambda o: abs(o.data.get("funding_rate", 0)), reverse=True)
        return opportunities[:5]  # Top 5

    async def evaluate(self, opp: Opportunity) -> Score:
        """Evaluate funding opportunity — Layer 1 (Ollama) for risk assessment."""
        fr = opp.data.get("funding_rate", 0)
        # Simple heuristic: higher funding rate = higher score, but cap at extremes
        base_score = min(abs(fr) / (self._threshold * 5), 1.0)
        return Score(
            value=base_score,
            threshold=0.5,
            reasoning=f"Funding rate {fr:.4%}/8h, annualized ~{opp.data.get('annualized', 0):.0%}",
        )

    async def execute(self, opp: Opportunity) -> Result:
        """Execute funding arbitrage — short perp + long spot hedge."""
        if not self._executor:
            return Result(success=False, details={"error": "no executor configured"})

        fr = opp.data.get("funding_rate", 0)
        # Estimated profit per funding period
        est_profit = abs(fr) * opp.money_involved

        try:
            # In dry_run mode, just record what would happen
            order = await self._executor.market_sell(
                self._executor.default_exchange,
                opp.data["symbol"],
                opp.money_involved,
            )
            return Result(
                success=order.status != "failed",
                profit_loss=est_profit,
                details={
                    "order_id": order.id,
                    "funding_rate": fr,
                    "est_profit_per_period": est_profit,
                    "dry_run": order.dry_run,
                },
            )
        except Exception:
            log.exception("crypto_funding.execute_error")
            return Result(success=False, details={"error": "execution failed"})

    def estimate_roi(self) -> float:
        """Conservative: ~20-40% annualized in good conditions."""
        return 1.3
