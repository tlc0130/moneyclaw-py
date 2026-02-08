"""Real-time cost tracking for LLM usage — the agent tracks its own expenses."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

import structlog

from moneyclaw.llm.types import LLMLayer

log = structlog.get_logger()


@dataclass
class UsageRecord:
    """Single LLM call record."""

    timestamp: float
    layer: LLMLayer
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    latency: float


@dataclass
class DailySummary:
    """Aggregated daily stats."""

    date: date
    total_cost: float = 0.0
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_by_layer: dict[str, float] = field(default_factory=dict)
    calls_by_layer: dict[str, int] = field(default_factory=dict)


class CostTracker:
    """Tracks every LLM call's cost. The agent's self-awareness of its expenses."""

    def __init__(self, daily_budget: float = 1.0) -> None:
        self._daily_budget = daily_budget
        self._records: list[UsageRecord] = []
        self._daily: dict[date, DailySummary] = {}

    def record(
        self,
        layer: LLMLayer | int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        latency: float,
    ) -> None:
        # Convert int to LLMLayer if needed (SmartRouter passes int for dynamic routing)
        if isinstance(layer, int):
            layer = LLMLayer(layer)

        rec = UsageRecord(
            timestamp=time.time(),
            layer=layer,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            latency=latency,
        )
        self._records.append(rec)

        # Update daily summary
        today = date.today()
        summary = self._daily.setdefault(
            today,
            DailySummary(date=today),
        )
        summary.total_cost += cost
        summary.total_calls += 1
        summary.total_input_tokens += input_tokens
        summary.total_output_tokens += output_tokens
        layer_name = layer.name
        summary.cost_by_layer[layer_name] = summary.cost_by_layer.get(layer_name, 0) + cost
        summary.calls_by_layer[layer_name] = summary.calls_by_layer.get(layer_name, 0) + 1

        # Warn if approaching daily budget
        if summary.total_cost > self._daily_budget * 0.8:
            log.warning(
                "cost.budget_warning",
                spent=round(summary.total_cost, 4),
                budget=self._daily_budget,
            )

    @property
    def today_cost(self) -> float:
        summary = self._daily.get(date.today())
        return summary.total_cost if summary else 0.0

    @property
    def today_calls(self) -> int:
        summary = self._daily.get(date.today())
        return summary.total_calls if summary else 0

    def is_over_budget(self) -> bool:
        return self.today_cost >= self._daily_budget

    def get_daily_summary(self, day: date | None = None) -> DailySummary | None:
        return self._daily.get(day or date.today())

    def get_total_cost(self) -> float:
        return sum(s.total_cost for s in self._daily.values())

    def format_status(self) -> str:
        """Human-readable cost status."""
        summary = self.get_daily_summary()
        if not summary:
            return "No LLM usage today."

        lines = [
            f"Today's LLM Cost: ${summary.total_cost:.4f} / ${self._daily_budget:.2f}",
            f"Calls: {summary.total_calls}",
            f"Tokens: {summary.total_input_tokens:,} in / {summary.total_output_tokens:,} out",
        ]
        if summary.cost_by_layer:
            lines.append("By layer:")
            for layer, cost in sorted(summary.cost_by_layer.items()):
                calls = summary.calls_by_layer.get(layer, 0)
                lines.append(f"  {layer}: ${cost:.4f} ({calls} calls)")
        return "\n".join(lines)
