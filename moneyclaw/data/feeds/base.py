from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class Quote:
    symbol: str
    price: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    change_24h: float = 0.0


@dataclass(slots=True)
class OHLCV:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
