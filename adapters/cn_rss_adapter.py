from __future__ import annotations
"""
Chinese RSS Adapter
====================

Fetches from Chinese-language RSS feeds. Outputs NewsItem objects
in the same contract as the English RSS adapter.

Chinese text passes through as-is — DeepSeek scores it natively.
"""

import hashlib
from datetime import datetime
from time import mktime

try:
    import feedparser
except ImportError:
    print("Install feedparser: pip install feedparser --break-system-packages")
    raise

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.contracts import NewsItem


# Chinese feeds — curated for Sean's interests
DEFAULT_CN_FEEDS = [
    # AI / Technology
    "https://www.jiqizhixin.com/rss",                    # 机器之心 — top CN AI media
    "https://www.qbitai.com/feed",                        # 量子位 — AI research
    "https://36kr.com/feed",                              # 36氪 — tech startups
    "https://sspai.com/feed",                             # 少数派 — productivity/tools
    # Design
    "https://www.uisdc.com/feed",                         # 优设 — UI/UX design
    # Business / Depth
    "https://www.huxiu.com/rss/0.xml",                    # 虎嗅 — business + tech
    # Developer
    "https://www.oschina.net/news/rss",                   # 开源中国 — open source
    "https://www.infoq.cn/feed",                          # InfoQ CN — developer news
]


class ChineseRSSAdapter:
    def __init__(self, feeds=None):
        self.feeds = feeds or DEFAULT_CN_FEEDS
        self.source_system = "rss_cn"
        self.adapter_version = "1.0"

    def fetch(self, max_per_feed=10) -> list[NewsItem]:
        """Fetch recent items from Chinese feeds."""
        items = []
        for feed_url in self.feeds:
            try:
                feed_items = self._fetch_feed(feed_url, max_per_feed)
                items.extend(feed_items)
            except Exception as e:
                print(f"  WARNING: Failed to fetch {feed_url}: {e}")

        items = self._deduplicate(items)
        print(f"  Fetched {len(items)} items from {len(self.feeds)} Chinese feeds")
        return items

    def _fetch_feed(self, url, max_items):
        """Fetch a single feed."""
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(20)  # 20s per feed
        try:
            feed = feedparser.parse(url)
        finally:
            socket.setdefaulttimeout(old_timeout)
        feed_name = feed.feed.get("title", url.split("/")[2])
        items = []

        for entry in feed.entries[:max_items]:
            summary = entry.get("summary", entry.get("description", entry.get("title", "")))
            summary = self._strip_html(summary)[:500]

            published = datetime.now()
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime.fromtimestamp(mktime(entry.published_parsed))
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime.fromtimestamp(mktime(entry.updated_parsed))

            item_id = f"cn-{hashlib.md5(entry.get('link', entry.get('title', '')).encode()).hexdigest()[:10]}"

            image_url = None
            if hasattr(entry, "media_content") and entry.media_content:
                image_url = entry.media_content[0].get("url")
            elif hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0].get("url")

            items.append(NewsItem(
                id=item_id,
                title=entry.get("title", "无标题"),
                summary=summary if summary else entry.get("title", ""),
                url=entry.get("link", ""),
                published_at=published,
                source_name=feed_name,
                source_scoring=None,
                geo=None,
                image_url=image_url,
                source_system=self.source_system,
                adapter_version=self.adapter_version,
            ))

        return items

    def _strip_html(self, text):
        import re
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def _deduplicate(self, items):
        seen = set()
        unique = []
        for item in items:
            normalized = item.title.strip()
            if normalized not in seen:
                seen.add(normalized)
                unique.append(item)
        return unique


if __name__ == "__main__":
    print("Testing Chinese RSS Adapter...\n")
    adapter = ChineseRSSAdapter()
    items = adapter.fetch(max_per_feed=3)
    for item in items[:5]:
        print(f"  [{item.source_name}] {item.title}")
        print(f"    {item.summary[:60]}...")
        print()
