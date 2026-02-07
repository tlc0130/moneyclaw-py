"""Shared LLM types — avoids circular imports between router and cost_tracker."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

# Import TaskType from model_profile for convenience
from moneyclaw.llm.model_profile import TaskType


class LLMLayer(enum.IntEnum):
    """Cost tiers — lower is cheaper."""

    RULES = 0  # Free: regex, thresholds, simple math
    LOCAL = 1  # Ollama: ~$0/day
    CHEAP = 2  # DeepSeek/Groq: <$0.50/day
    PREMIUM = 3  # Claude/GPT-4: on-demand


@dataclass
class TaskRequest:
    """A request to the LLM router.

    Enhanced for SmartRouter with automatic model discovery and intelligent routing.
    """

    prompt: str
    system: str = ""

    # Legacy layer constraints (optional, for backward compatibility)
    min_layer: LLMLayer = LLMLayer.RULES
    max_layer: LLMLayer = LLMLayer.PREMIUM

    # Financial risk — higher amounts justify more capable models
    money_involved: float = 0.0

    # Task complexity hint (0-1, higher = more complex)
    # If not provided, will be auto-detected by TaskAnalyzer
    complexity: float = 0.0

    # Task type — will be auto-detected if not specified
    task_type: TaskType | None = None

    # Urgency — urgent tasks can use reserved budget
    is_urgent: bool = False

    # Model preferences
    preferred_model: str | None = None  # Specific model ID if user has preference
    require_tools: bool = False         # Whether tool calling is required
    require_vision: bool = False        # Whether vision is required
    require_json_mode: bool = False     # Whether JSON mode is required

    # Generation parameters
    temperature: float = 0.0
    max_tokens: int = 1024

    # Caching
    cache_ttl: int = 0  # Seconds; 0 = no cache

    # Metadata for routing decisions
    metadata: dict[str, Any] = field(default_factory=dict)
