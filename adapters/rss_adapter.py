"""
RSS Adapter
===========

Simplest possible news source adapter. Fetches from a curated list of RSS feeds
and produces NewsItem objects conforming to the News Source Contract.

Usage:
    from adapters.rss_adapter import RSSAdapter
    adapter = RSSAdapter(feeds=["https://hnrss.org/newest?points=100"])
    items = adapter.fetch()
"""

import hashlib
from datetime import datetime
from time import mktime

try:
    import feedparser
except ImportError:
    print("Install feedparser: pip install feedparser --break-system-packages")
    raise

from engine.contracts import NewsItem


# Default feeds — curated for Sean's interests
DEFAULT_FEEDS = [
    # AI / ML
    "https://hnrss.org/newest?points=100",                  # Hacker News (popular)
    "https://arxiv.org/rss/cs.AI",                           # arXiv AI papers
    "https://arxiv.org/rss/cs.HC",                           # arXiv Human-Computer Interaction
    # Design / Creativity
    "https://feeds.feedburner.com/AListApart",               # A List Apart (web design)
    # Tech / Engineering
    "https://blog.railway.app/feed.xml",                     # Railway blog
]


class RSSAdapter:
    def __init__(self, feeds=None):
        self.feeds = feeds or DEFAULT_FEEDS
        self.source_system = "rss"
        self.adapter_version = "1.0"

    def fetch(self, max_per_feed=10) -> list[NewsItem]:
        """Fetch recent items from all configured feeds."""
        items = []
        for feed_url in self.feeds:
            try:
                feed_items = self._fetch_feed(feed_url, max_per_feed)
                items.extend(feed_items)
            except Exception as e:
                print(f"  WARNING: Failed to fetch {feed_url}: {e}")
        
        # Deduplicate by title similarity
        items = self._deduplicate(items)
        print(f"  Fetched {len(items)} items from {len(self.feeds)} feeds")
        return items

    def _fetch_feed(self, url, max_items):
        """Fetch a single RSS feed and convert entries to NewsItem."""
        feed = feedparser.parse(url)
        feed_name = feed.feed.get("title", url.split("/")[2])
        items = []

        for entry in feed.entries[:max_items]:
            # Extract summary — required by contract
            summary = entry.get("summary", "")
            if not summary:
                summary = entry.get("description", entry.get("title", ""))
            # Clean HTML tags from summary
            summary = self._strip_html(summary)[:500]

            # Parse published date
            published = datetime.now()
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime.fromtimestamp(mktime(entry.published_parsed))
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime.fromtimestamp(mktime(entry.updated_parsed))

            # Generate stable ID
            item_id = f"rss-{hashlib.md5(entry.get('link', entry.get('title', '')).encode()).hexdigest()[:10]}"

            # Extract image if available
            image_url = None
            if hasattr(entry, "media_content") and entry.media_content:
                image_url = entry.media_content[0].get("url")
            elif hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0].get("url")

            items.append(NewsItem(
                id=item_id,
                title=entry.get("title", "Untitled"),
                summary=summary if summary else entry.get("title", ""),
                url=entry.get("link", ""),
                published_at=published,
                source_name=feed_name,
                source_scoring=None,  # RSS has no scoring
                geo=None,             # RSS has no geo
                image_url=image_url,
                source_system=self.source_system,
                adapter_version=self.adapter_version,
            ))

        return items

    def _strip_html(self, text):
        """Remove HTML tags from text."""
        import re
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def _deduplicate(self, items):
        """Remove duplicate items based on title similarity."""
        seen_titles = set()
        unique = []
        for item in items:
            # Normalize title for comparison
            normalized = item.title.lower().strip()
            if normalized not in seen_titles:
                seen_titles.add(normalized)
                unique.append(item)
        return unique


if __name__ == "__main__":
    print("Testing RSS Adapter...\n")
    adapter = RSSAdapter()
    items = adapter.fetch(max_per_feed=3)
    for item in items[:5]:
        print(f"  [{item.source_name}] {item.title}")
        print(f"    {item.summary[:80]}...")
        print()
