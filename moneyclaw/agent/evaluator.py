"""Evaluator — scores opportunities and decides if they're worth pursuing."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from moneyclaw.llm.router import LLMLayer, LLMRouter, TaskRequest
from moneyclaw.plugins.base import Opportunity

log = structlog.get_logger()

EVAL_SYSTEM = """You are a financial opportunity evaluator. Score opportunities from 0 to 1.
Consider: potential profit, probability of success, risk, time sensitivity, effort required.
Respond with ONLY a number between 0 and 1."""


@dataclass
class Score:
    value: float  # 0-1
    threshold: float = 0.5  # Must exceed this to proceed
    reasoning: str = ""


class Evaluator:
    """Evaluates and prioritizes opportunities."""

    def __init__(self, llm: LLMRouter, threshold: float = 0.5) -> None:
        self._llm = llm
        self._threshold = threshold

    async def score(self, opp: Opportunity) -> Score:
        """Score an opportunity. Uses rules for simple cases, LLM for complex ones."""
        # Layer 0: rule-based scoring for simple, well-defined opportunities
        if opp.pre_score is not None:
            return Score(value=opp.pre_score, threshold=self._threshold, reasoning="pre-scored")

        # Layer 1-2: LLM evaluation for complex opportunities
        prompt = (
            f"Evaluate this opportunity:\n"
            f"Title: {opp.title}\n"
            f"Strategy: {opp.strategy_name}\n"
            f"Money involved: ${opp.money_involved:.2f}\n"
            f"Data: {opp.data}\n"
            f"Score it 0-1."
        )

        try:
            response = await self._llm.complete(
                TaskRequest(
                    prompt=prompt,
                    system=EVAL_SYSTEM,
                    min_layer=LLMLayer.LOCAL,
                    max_layer=LLMLayer.CHEAP,
                    money_involved=opp.money_involved,
                    complexity=0.3,
                    cache_ttl=300,  # Cache evaluations for 5 minutes
                )
            )

            try:
                value = float(response.text.strip())
                value = max(0.0, min(1.0, value))
            except ValueError:
                log.warning("evaluator.parse_error", text=response.text)
                value = 0.0

            return Score(value=value, threshold=self._threshold, reasoning=response.text)
        except Exception as e:
            log.warning("evaluator.llm_unavailable", strategy=opp.strategy_name, error=str(e))
            fallback = self._fallback_score(opp)
            return Score(value=fallback, threshold=self._threshold, reasoning="fallback:no-llm")

    def _fallback_score(self, opp: Opportunity) -> float:
        """Conservative heuristic when no LLM model is available."""
        data = opp.data
        score = 0.3

        if opp.money_involved <= 0:
            score += 0.1

        div_yield = data.get("dividend_yield")
        if isinstance(div_yield, (int, float)):
            if 0.04 <= div_yield <= 0.12:
                score += 0.3
            elif 0.02 <= div_yield < 0.04:
                score += 0.15
            elif div_yield > 0.12:
                score -= 0.1

        pe_ratio = data.get("pe_ratio")
        if isinstance(pe_ratio, (int, float)):
            if pe_ratio <= 20:
                score += 0.2
            elif pe_ratio >= 35:
                score -= 0.1

        return max(0.0, min(1.0, score))
