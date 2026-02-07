"""Shared LLM types — avoids circular imports between router and cost_tracker."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class LLMLayer(enum.IntEnum):
    """Cost tiers — lower is cheaper."""

    RULES = 0  # Free: regex, thresholds, simple math
    LOCAL = 1  # Ollama: ~$0/day
    CHEAP = 2  # DeepSeek/Groq: <$0.50/day
    PREMIUM = 3  # Claude/GPT-4: on-demand


@dataclass
class TaskRequest:
    """A request to the LLM router."""

    prompt: str
    system: str = ""
    min_layer: LLMLayer = LLMLayer.LOCAL
    max_layer: LLMLayer = LLMLayer.PREMIUM
    # Amount of money involved — higher amounts justify smarter models
    money_involved: float = 0.0
    # Task complexity hint (0-1, higher = more complex)
    complexity: float = 0.0
    temperature: float = 0.0
    max_tokens: int = 1024
    cache_ttl: int = 0  # Seconds; 0 = no cache
    metadata: dict[str, Any] = field(default_factory=dict)
