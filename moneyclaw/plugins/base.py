"""Strategy plugin base class — all money-making/saving strategies implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


@dataclass
class Opportunity:
    """A detected money-saving or money-making opportunity."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    strategy_name: str = ""
    title: str = ""
    money_involved: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    # Pre-computed score from rules engine (Layer 0). None means "needs LLM evaluation".
    pre_score: float | None = None


@dataclass
class Score:
    """Evaluation result."""

    value: float  # 0-1
    threshold: float = 0.5
    reasoning: str = ""


@dataclass
class Result:
    """Execution result."""

    success: bool = True
    profit_loss: float = 0.0  # Positive = profit, negative = loss
    details: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    """Base class for all strategies. Drop a strategy in strategies/ and it auto-registers."""

    name: str
    description: str
    risk_level: Literal["low", "medium", "high"] = "low"
    min_llm_layer: int = 0  # Minimum LLM layer this strategy needs

    @abstractmethod
    async def scan(self) -> list[Opportunity]:
        """Scan for opportunities. Called periodically by the agent."""
        ...

    @abstractmethod
    async def evaluate(self, opp: Opportunity) -> Score:
        """Evaluate an opportunity's value."""
        ...

    @abstractmethod
    async def execute(self, opp: Opportunity) -> Result:
        """Execute on an opportunity."""
        ...

    @abstractmethod
    def estimate_roi(self) -> float:
        """Estimated ROI multiplier (e.g., 2.0 = 2x expected return)."""
        ...

    async def setup(self) -> None:
        """Called once when strategy is loaded. Override for initialization."""

    async def teardown(self) -> None:
        """Called when strategy is unloaded. Override for cleanup."""
