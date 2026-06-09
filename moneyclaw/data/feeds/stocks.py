"""Stock/equity price feed (stub — extend with a real provider as needed)."""

from __future__ import annotations

from typing import Optional

import structlog

from .base import Quote

log = structlog.get_logger()


class StockFeed:
    """Placeholder stock feed. Returns None until a provider is configured."""

    async def get_price(self, ticker: str) -> Optional[Quote]:
        log.debug("stock_feed.no_provider", ticker=ticker)
        return None
