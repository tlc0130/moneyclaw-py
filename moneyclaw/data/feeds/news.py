"""News headline feed via RSS (no API key required)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

import structlog

log = structlog.get_logger()

_DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
]
_TIMEOUT = 8.0


@dataclass
class Headline:
    title: str
    source: str
    url: str = ""
    published: datetime = field(default_factory=datetime.utcnow)


class NewsFeed:
    """Fetch recent headlines from RSS feeds."""

    def __init__(self, feed_urls: list[str] | None = None) -> None:
        self._feeds = feed_urls or _DEFAULT_FEEDS

    async def get_headlines(self, max_per_feed: int = 8) -> List[Headline]:
        loop = asyncio.get_running_loop()

        async def _fetch_one(url: str) -> list[Headline]:
            try:
                import feedparser

                feed = await asyncio.wait_for(
                    loop.run_in_executor(None, feedparser.parse, url),
                    timeout=_TIMEOUT,
                )
                items = []
                for entry in (feed.entries or [])[:max_per_feed]:
                    title = entry.get("title", "").strip()
                    if not title:
                        continue
                    items.append(
                        Headline(
                            title=title,
                            source=url,
                            url=entry.get("link", ""),
                        )
                    )
                return items
            except Exception as exc:
                log.warning("news_feed.fetch_failed", url=url, error=str(exc))
                return []

        results = await asyncio.gather(*[_fetch_one(u) for u in self._feeds])
        headlines: list[Headline] = []
        for batch in results:
            headlines.extend(batch)
        return headlines
