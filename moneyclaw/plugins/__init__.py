"""Plugin system — strategies are plugins."""

from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy
from moneyclaw.plugins.registry import StrategyRegistry

__all__ = ["Strategy", "Opportunity", "Score", "Result", "StrategyRegistry"]
