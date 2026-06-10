from __future__ import annotations

from typing import Any

import feedparser
import structlog

log = structlog.get_logger()


class NewsFeed:
    """Simple RSS-based news feed for future news-driven strategies."""

    def __init__(self) -> None:
        self._sources = {
            "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "cointelegraph": "https://cointelegraph.com/rss",
            "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
        }

    async def fetch(self, source: str = "coindesk", limit: int = 10) -> list[dict[str, Any]]:
        url = self._sources.get(source)
        if not url:
            return []
        try:
            parsed = feedparser.parse(url)
            entries = getattr(parsed, "entries", [])[:limit]
            return [
                {
                    "title": getattr(entry, "title", ""),
                    "link": getattr(entry, "link", ""),
                    "summary": getattr(entry, "summary", ""),
                    "published": getattr(entry, "published", ""),
                    "source": source,
                }
                for entry in entries
            ]
        except Exception as e:
            log.warning("news_feed.fetch_failed", source=source, error=str(e))
            return []

    async def latest(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self.fetch(limit=limit)
