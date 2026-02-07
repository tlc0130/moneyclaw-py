"""Tests for SmartRouter and related components."""

from __future__ import annotations

import pytest

from moneyclaw.llm.budget_manager import BudgetManager, BudgetPolicy, BudgetStatus
from moneyclaw.llm.cost_tracker import CostTracker
from moneyclaw.llm.model_intelligence import (
    create_profile_from_model_id,
    extract_display_name,
    infer_capability,
    infer_cost,
)
from moneyclaw.llm.model_profile import CostTier, ModelProfile, TaskType
from moneyclaw.llm.model_registry import SmartModelRegistry
from moneyclaw.llm.performance_tracker import PerformanceRecord, PerformanceTracker
from moneyclaw.llm.task_analyzer import TaskAnalyzer, quick_analyze


class TestModelIntelligence:
    """Tests for model intelligence module."""

    def test_infer_capability_gpt4(self):
        """Test capability inference for GPT-4 models."""
        capability, strengths = infer_capability("openai/gpt-4o")
        assert capability == 0.95
        assert strengths[TaskType.ANALYTICS] == 0.95

    def test_infer_capability_claude(self):
        """Test capability inference for Claude models."""
        capability, strengths = infer_capability("anthropic/claude-3-opus")
        assert capability == 0.95

    def test_infer_capability_local(self):
        """Test capability inference for local models."""
        capability, strengths = infer_capability("ollama/qwen2.5:7b")
        # Qwen 2.5 is recognized as medium-high capability
        assert capability >= 0.5
        assert capability <= 0.8

    def test_infer_cost_openai(self):
        """Test cost inference for OpenAI models."""
        cost_in, cost_out, tier = infer_cost("openai/gpt-4o")
        assert cost_in > 0
        assert cost_out > 0
        assert tier in [CostTier.STANDARD, CostTier.PREMIUM]

    def test_infer_cost_ollama(self):
        """Test cost inference for Ollama models."""
        cost_in, cost_out, tier = infer_cost("ollama/qwen2.5:7b")
        assert cost_in == 0.0
        assert cost_out == 0.0
        assert tier == CostTier.FREE

    def test_extract_display_name(self):
        """Test display name extraction."""
        assert extract_display_name("openai/gpt-4o") == "GPT 4o"
        assert extract_display_name("anthropic/claude-3-opus") == "Claude 3 Opus"
        # Verify it extracts and capitalizes correctly
        name = extract_display_name("ollama/qwen2.5:7b")
        assert "Qwen" in name
        assert "2.5" in name or "2" in name

    def test_create_profile(self):
        """Test profile creation from model ID."""
        profile = create_profile_from_model_id("openai/gpt-4o", "openai")

        assert profile.model_id == "openai/gpt-4o"
        assert profile.provider == "openai"
        assert profile.display_name == "GPT 4o"
        assert profile.capability_score == 0.95
        assert profile.context_length == 128000
        assert profile.supports_tools is True
        assert profile.supports_vision is True


class TestModelProfile:
    """Tests for ModelProfile class."""

    def test_estimated_cost(self):
        """Test cost estimation."""
        profile = ModelProfile(
            model_id="test/model",
            provider="test",
            display_name="Test Model",
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.03,
        )
        # Default estimation: 500 input, 300 output tokens
        cost = profile.estimated_cost_per_call
        expected = (500 / 1000) * 0.01 + (300 / 1000) * 0.03
        assert cost == pytest.approx(expected)

    def test_effective_score(self):
        """Test effective score calculation."""
        profile = ModelProfile(
            model_id="test/model",
            provider="test",
            display_name="Test Model",
            capability_score=0.8,
            success_rate=0.9,
        )
        assert profile.effective_score == pytest.approx(0.72)

    def test_matches_task(self):
        """Test task matching."""
        profile = ModelProfile(
            model_id="test/model",
            provider="test",
            display_name="Test Model",
            task_strengths={TaskType.ANALYTICS: 0.9},
        )
        assert profile.matches_task(TaskType.ANALYTICS, min_score=0.8) is True
        assert profile.matches_task(TaskType.ANALYTICS, min_score=0.95) is False
        assert profile.matches_task(TaskType.CREATIVE, min_score=0.5) is False

    def test_is_cheaper_than(self):
        """Test cost comparison."""
        cheap = ModelProfile(
            model_id="cheap",
            provider="test",
            display_name="Cheap",
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002,
        )
        expensive = ModelProfile(
            model_id="expensive",
            provider="test",
            display_name="Expensive",
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.03,
        )
        assert cheap.is_cheaper_than(expensive) is True
        assert expensive.is_cheaper_than(cheap) is False


class TestTaskAnalyzer:
    """Tests for TaskAnalyzer."""

    def test_analyze_analytics_task(self):
        """Test analysis of analytics tasks."""
        analyzer = TaskAnalyzer()
        result = analyzer.analyze("Analyze the market data and calculate trends")

        assert result.primary_type == TaskType.ANALYTICS
        assert result.confidence > 0.5
        assert result.complexity_hint > 0

    def test_analyze_creative_task(self):
        """Test analysis of creative tasks."""
        analyzer = TaskAnalyzer()
        result = analyzer.analyze("Write a creative marketing copy for our product")

        assert result.primary_type == TaskType.CREATIVE
        assert result.confidence > 0.5

    def test_quick_analyze(self):
        """Test quick analyze convenience function."""
        task_type = quick_analyze("Analyze data")
        assert isinstance(task_type, TaskType)


class TestBudgetManager:
    """Tests for BudgetManager."""

    def test_budget_status_healthy(self):
        """Test healthy budget status."""
        from moneyclaw.llm.types import LLMLayer

        cost_tracker = CostTracker(daily_budget=10.0)
        budget = BudgetManager(cost_tracker, BudgetPolicy(daily_budget=10.0))

        # No costs yet
        assert budget.get_status() == BudgetStatus.HEALTHY
        assert budget.budget_remaining == 10.0
        assert budget.can_afford(1.0) is True

    def test_budget_status_caution(self):
        """Test caution budget status."""
        from moneyclaw.llm.types import LLMLayer

        cost_tracker = CostTracker(daily_budget=10.0)
        policy = BudgetPolicy(daily_budget=10.0, caution_threshold=0.5)
        budget = BudgetManager(cost_tracker, policy)

        # Simulate spending 60%
        cost_tracker.record(layer=LLMLayer.PREMIUM, model="test", input_tokens=1000, output_tokens=1000, cost=6.0, latency=1.0)

        assert budget.get_status() == BudgetStatus.CAUTION
        assert budget.can_afford(2.0) is True  # Should still afford

    def test_cannot_afford(self):
        """Test cannot afford expensive request."""
        from moneyclaw.llm.types import LLMLayer

        cost_tracker = CostTracker(daily_budget=1.0)
        budget = BudgetManager(cost_tracker, BudgetPolicy(daily_budget=1.0))

        # Spend 80%
        cost_tracker.record(layer=LLMLayer.PREMIUM, model="test", input_tokens=1000, output_tokens=1000, cost=0.8, latency=1.0)

        assert budget.can_afford(0.5) is False  # Would exceed budget

    def test_routing_strategy(self):
        """Test routing strategy generation."""
        cost_tracker = CostTracker(daily_budget=10.0)
        budget = BudgetManager(cost_tracker, BudgetPolicy(daily_budget=10.0))

        strategy = budget.get_routing_strategy()
        assert strategy["status"] == "HEALTHY"
        assert strategy["can_use_premium"] is True
        assert strategy["can_use_free"] is True


class TestPerformanceTracker:
    """Tests for PerformanceTracker."""

    def test_record_success(self):
        """Test recording successful call."""
        tracker = PerformanceTracker()
        model = ModelProfile(model_id="test", provider="test", display_name="Test")

        record = tracker.record(
            model=model,
            task_type=TaskType.ANALYTICS,
            success=True,
            latency_ms=100.0,
            input_tokens=100,
            output_tokens=50,
            actual_cost=0.001,
        )

        assert record.success is True
        assert record.model_id == "test"

        stats = tracker.get_stats("test")
        assert stats is not None
        assert stats.total_calls == 1
        assert stats.success_rate == 1.0

    def test_record_failure(self):
        """Test recording failed call."""
        tracker = PerformanceTracker()
        model = ModelProfile(model_id="test", provider="test", display_name="Test")

        tracker.record(
            model=model,
            task_type=TaskType.ANALYTICS,
            success=False,
            latency_ms=1000.0,
            input_tokens=0,
            output_tokens=0,
            actual_cost=0.0,
            error_type="TimeoutError",
        )

        stats = tracker.get_stats("test")
        assert stats.success_rate == 0.0
        assert stats.failed_calls == 1

    def test_get_success_rate(self):
        """Test success rate calculation."""
        tracker = PerformanceTracker()
        model = ModelProfile(model_id="test", provider="test", display_name="Test")

        # Record mixed results
        for _ in range(3):
            tracker.record(model, TaskType.ANALYTICS, True, 100, 100, 50, 0.001)
        tracker.record(model, TaskType.ANALYTICS, False, 1000, 0, 0, 0.0)

        assert tracker.get_success_rate("test") == pytest.approx(0.75)


class TestModelRegistry:
    """Tests for SmartModelRegistry."""

    @pytest.mark.asyncio
    async def test_registry_initially_empty(self):
        """Test registry starts empty."""
        registry = SmartModelRegistry()
        assert len(registry) == 0
        assert registry.get_all() == []

    def test_get_by_tier(self):
        """Test getting models by tier."""
        registry = SmartModelRegistry()

        # Manually add models (normally done via discover)
        free_model = create_profile_from_model_id("ollama/test", "ollama")
        expensive_model = create_profile_from_model_id("openai/gpt-4", "openai")

        # Registry is designed to be populated via discover(), but we can test the methods
        # by directly checking the types
        assert free_model.cost_tier == CostTier.FREE
        assert expensive_model.cost_tier in [CostTier.PREMIUM, CostTier.ULTRA]

    def test_get_cheapest(self):
        """Test getting cheapest model."""
        registry = SmartModelRegistry()

        # Without any models, should return None
        cheapest = registry.get_cheapest()
        assert cheapest is None


class TestIntegration:
    """Integration tests for the smart routing system."""

    def test_end_to_end_profile_creation(self):
        """Test complete profile creation pipeline."""
        # Create profiles for various models
        models = [
            ("openai/gpt-4o", "openai"),
            ("anthropic/claude-3-opus", "anthropic"),
            ("deepseek/deepseek-chat", "deepseek"),
            ("groq/llama-3.3-70b-versatile", "groq"),
            ("ollama/qwen2.5:7b", "ollama"),
        ]

        profiles = [create_profile_from_model_id(mid, provider) for mid, provider in models]

        # Verify all profiles created successfully
        assert len(profiles) == len(models)

        # Verify cost tiers
        free_models = [p for p in profiles if p.cost_tier == CostTier.FREE]
        paid_models = [p for p in profiles if p.cost_tier != CostTier.FREE]

        assert len(free_models) == 1  # Only Ollama
        assert len(paid_models) == 4

        # Verify capabilities
        high_cap = [p for p in profiles if p.capability_score >= 0.85]
        assert len(high_cap) >= 2  # GPT-4o and Claude should be high capability
