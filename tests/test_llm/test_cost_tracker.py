"""Tests for cost tracking."""

from moneyclaw.llm.cost_tracker import CostTracker
from moneyclaw.llm.router import LLMLayer


class TestCostTracker:
    def test_initial_state(self) -> None:
        tracker = CostTracker(daily_budget=1.0)
        assert tracker.today_cost == 0.0
        assert tracker.today_calls == 0
        assert not tracker.is_over_budget()

    def test_record_updates_cost(self) -> None:
        tracker = CostTracker()
        tracker.record(
            layer=LLMLayer.LOCAL,
            model="test",
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
            latency=0.5,
        )
        assert tracker.today_cost == 0.01
        assert tracker.today_calls == 1

    def test_budget_detection(self) -> None:
        tracker = CostTracker(daily_budget=0.05)
        for _ in range(5):
            tracker.record(
                layer=LLMLayer.CHEAP,
                model="test",
                input_tokens=100,
                output_tokens=50,
                cost=0.01,
                latency=0.1,
            )
        assert tracker.is_over_budget()

    def test_format_status(self) -> None:
        tracker = CostTracker(daily_budget=1.0)
        tracker.record(
            layer=LLMLayer.LOCAL,
            model="ollama",
            input_tokens=100,
            output_tokens=50,
            cost=0.0,
            latency=0.5,
        )
        status = tracker.format_status()
        assert "Today's LLM Cost" in status
        assert "LOCAL" in status

    def test_total_cost_across_days(self) -> None:
        tracker = CostTracker()
        tracker.record(
            layer=LLMLayer.CHEAP,
            model="test",
            input_tokens=100,
            output_tokens=50,
            cost=0.05,
            latency=0.2,
        )
        assert tracker.get_total_cost() == 0.05
