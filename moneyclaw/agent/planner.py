"""Planner — daily and weekly strategy planning."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from moneyclaw.llm.router import LLMLayer, LLMRouter, TaskRequest

log = structlog.get_logger()

PLANNER_SYSTEM = """You are MoneyClaw's strategy planner. Your job is to analyze recent performance
and plan the next period's focus. Be concise and actionable. Output a prioritized list of actions."""


@dataclass
class Plan:
    summary: str
    actions: list[str]
    focus_strategies: list[str]


class Planner:
    """Plans strategy focus based on recent results."""

    def __init__(self, llm: LLMRouter) -> None:
        self._llm = llm

    async def daily_plan(self, context: str) -> Plan:
        """Generate a daily action plan."""
        response = await self._llm.complete(
            TaskRequest(
                prompt=(
                    f"Based on recent performance and market context, "
                    f"plan today's focus:\n\n{context}\n\n"
                    f"Output: 1) Summary (1 line) 2) Top 3 actions 3) Strategies to focus on"
                ),
                system=PLANNER_SYSTEM,
                min_layer=LLMLayer.LOCAL,
                max_layer=LLMLayer.CHEAP,
                complexity=0.4,
            )
        )

        # Simple parsing — the LLM output is free-form text
        return Plan(
            summary=response.text.split("\n")[0],
            actions=self._extract_list(response.text),
            focus_strategies=[],
        )

    async def weekly_review(self, context: str) -> str:
        """Generate a weekly performance review (uses higher-tier LLM)."""
        response = await self._llm.complete(
            TaskRequest(
                prompt=(
                    f"Generate a weekly review for MoneyClaw:\n\n{context}\n\n"
                    f"Include: P&L summary, best/worst strategies, "
                    f"LLM costs, recommendations for next week."
                ),
                system=PLANNER_SYSTEM,
                min_layer=LLMLayer.CHEAP,
                max_layer=LLMLayer.PREMIUM,
                complexity=0.7,
            )
        )
        return response.text

    @staticmethod
    def _extract_list(text: str) -> list[str]:
        """Extract numbered/bulleted items from text."""
        items = []
        for line in text.split("\n"):
            line = line.strip()
            if line and (line[0].isdigit() or line[0] in "-*•"):
                # Strip the bullet/number prefix
                cleaned = line.lstrip("0123456789.-*•) ").strip()
                if cleaned:
                    items.append(cleaned)
        return items
