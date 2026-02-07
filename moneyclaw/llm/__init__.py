"""LLM abstraction layer with smart automatic routing."""

# Core router (new)
from moneyclaw.llm.smart_router import SmartRouter

# Legacy router (backward compatibility)
from moneyclaw.llm.router import LLMRouter

# Types
from moneyclaw.llm.types import LLMLayer, TaskRequest, TaskType

# Model management
from moneyclaw.llm.model_profile import ModelProfile, CostTier
from moneyclaw.llm.model_registry import SmartModelRegistry
from moneyclaw.llm.model_discovery import ModelDiscoveryService
from moneyclaw.llm.model_intelligence import create_profile_from_model_id

# Supporting components
from moneyclaw.llm.task_analyzer import TaskAnalyzer, TaskAnalysis
from moneyclaw.llm.budget_manager import BudgetManager, BudgetPolicy, BudgetStatus
from moneyclaw.llm.performance_tracker import PerformanceTracker

__all__ = [
    # Routers
    "SmartRouter",
    "LLMRouter",  # Legacy
    # Types
    "LLMLayer",
    "TaskRequest",
    "TaskType",
    # Model management
    "ModelProfile",
    "CostTier",
    "SmartModelRegistry",
    "ModelDiscoveryService",
    "create_profile_from_model_id",
    # Task analysis
    "TaskAnalyzer",
    "TaskAnalysis",
    # Budget
    "BudgetManager",
    "BudgetPolicy",
    "BudgetStatus",
    # Performance
    "PerformanceTracker",
]
