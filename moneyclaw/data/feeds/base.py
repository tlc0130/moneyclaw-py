"""Shared data types for market feeds."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Quote:
    symbol: str
    price: float
    volume: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class OHLCV:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
