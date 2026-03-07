from __future__ import annotations
"""
Enhanced RSS Adapter
=====================

Improved media extraction:
- Parses images from HTML description/content fields
- Detects YouTube and Bilibili video URLs
- Extracts Open Graph images from article pages (optional)

Drop-in replacement for rss_adapter.py
"""

import hashlib
import re
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


DEFAULT_FEEDS = [
    "https://hnrss.org/newest?points=100",
    "https://arxiv.org/rss/cs.AI",
    "https://arxiv.org/rss/cs.HC",
    "https://feeds.feedburner.com/AListApart",
    "https://blog.railway.app/feed.xml",
]

# Patterns for video detection
YOUTUBE_PATTERNS = [
    r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
    r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
    r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
]
BILIBILI_PATTERNS = [
    r'(?:https?://)?(?:www\.)?bilibili\.com/video/(BV[a-zA-Z0-9]+)',
    r'(?:https?://)?b23\.tv/([a-zA-Z0-9]+)',
]

# Image extraction patterns from HTML
IMG_PATTERNS = [
    r'<img[^>]+src=["\']([^"\']+)["\']',
    r'<img[^>]+src=([^\s>]+)',
]
OG_IMAGE_PATTERN = r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'


class RSSAdapter:
    def __init__(self, feeds=None):
        self.feeds = feeds or DEFAULT_FEEDS
        self.source_system = "rss"
        self.adapter_version = "2.0"

    def fetch(self, max_per_feed=10) -> list[NewsItem]:
        items = []
        for feed_url in self.feeds:
            try:
                feed_items = self._fetch_feed(feed_url, max_per_feed)
                items.extend(feed_items)
            except Exception as e:
                print(f"  WARNING: Failed to fetch {feed_url}: {e}")
        items = self._deduplicate(items)
        print(f"  Fetched {len(items)} items from {len(self.feeds)} feeds")
        return items

    def _fetch_feed(self, url, max_items):
        feed = feedparser.parse(url)
        feed_name = feed.feed.get("title", url.split("/")[2])
        items = []

        for entry in feed.entries[:max_items]:
            # Get raw HTML content for media extraction
            html_content = (
                entry.get("content", [{}])[0].get("value", "")
                if entry.get("content") else ""
            ) or entry.get("description", "") or entry.get("summary", "")

            # Extract clean summary
            summary = self._strip_html(html_content)[:500]
            if not summary:
                summary = entry.get("title", "")

            # Parse date
            published = datetime.now()
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime.fromtimestamp(mktime(entry.published_parsed))
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime.fromtimestamp(mktime(entry.updated_parsed))

            # Generate ID
            item_id = f"rss-{hashlib.md5(entry.get('link', entry.get('title', '')).encode()).hexdigest()[:10]}"

            # Extract media
            image_url = self._extract_image(entry, html_content)
            video_url, video_type = self._extract_video(entry, html_content)

            items.append(NewsItem(
                id=item_id,
                title=entry.get("title", "Untitled"),
                summary=summary,
                url=entry.get("link", ""),
                published_at=published,
                source_name=feed_name,
                source_scoring=None,
                geo=None,
                image_url=image_url,
                video_url=video_url,
                source_system=self.source_system,
                adapter_version=self.adapter_version,
            ))

        return items

    def _extract_image(self, entry, html_content):
        """Extract best image from multiple sources."""
        # Priority 1: media_content
        if hasattr(entry, "media_content") and entry.media_content:
            for media in entry.media_content:
                if media.get("medium") == "image" or "image" in media.get("type", ""):
                    return media.get("url")
                if media.get("url"):
                    return media.get("url")

        # Priority 2: media_thumbnail
        if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
            return entry.media_thumbnail[0].get("url")

        # Priority 3: enclosure with image type
        if hasattr(entry, "enclosures") and entry.enclosures:
            for enc in entry.enclosures:
                if "image" in enc.get("type", ""):
                    return enc.get("href") or enc.get("url")

        # Priority 4: Parse from HTML content
        if html_content:
            for pattern in IMG_PATTERNS:
                match = re.search(pattern, html_content)
                if match:
                    img_url = match.group(1)
                    # Skip tiny tracking pixels and icons
                    if self._is_valid_image(img_url):
                        return img_url

        # Priority 5: links with image extensions
        link = entry.get("link", "")
        if any(link.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
            return link

        return None

    def _extract_video(self, entry, html_content):
        """Extract video URL and type (youtube/bilibili/direct)."""
        # Check entry link first
        link = entry.get("link", "")
        all_text = f"{link} {html_content}"

        # YouTube
        for pattern in YOUTUBE_PATTERNS:
            match = re.search(pattern, all_text)
            if match:
                video_id = match.group(1)
                return f"https://www.youtube.com/embed/{video_id}", "youtube"

        # Bilibili
        for pattern in BILIBILI_PATTERNS:
            match = re.search(pattern, all_text)
            if match:
                video_id = match.group(1)
                return f"https://player.bilibili.com/player.html?bvid={video_id}", "bilibili"

        # Direct video in enclosures
        if hasattr(entry, "enclosures") and entry.enclosures:
            for enc in entry.enclosures:
                if "video" in enc.get("type", ""):
                    return enc.get("href") or enc.get("url"), "direct"

        # Media content with video type
        if hasattr(entry, "media_content") and entry.media_content:
            for media in entry.media_content:
                if media.get("medium") == "video" or "video" in media.get("type", ""):
                    return media.get("url"), "direct"

        return None, None

    def _is_valid_image(self, url):
        """Filter out tracking pixels, icons, and tiny images."""
        if not url:
            return False
        skip_patterns = [
            "1x1", "pixel", "tracking", "spacer", "blank",
            "favicon", "icon", "logo", "badge", "button",
            "feeds.feedburner", "feedburner.com/~",
            "wp-includes", "s.w.org",
        ]
        url_lower = url.lower()
        return not any(p in url_lower for p in skip_patterns)

    def _strip_html(self, text):
        if not text:
            return ""
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"&[a-zA-Z]+;", " ", clean)
        clean = re.sub(r"&#\d+;", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def _deduplicate(self, items):
        seen = set()
        unique = []
        for item in items:
            normalized = item.title.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                unique.append(item)
        return unique


if __name__ == "__main__":
    print("Testing Enhanced RSS Adapter...\n")
    adapter = RSSAdapter()
    items = adapter.fetch(max_per_feed=5)
    for item in items[:10]:
        media = ""
        if item.video_url:
            media = f" [VIDEO]"
        elif item.image_url:
            media = f" [IMG]"
        print(f"  [{item.source_name}]{media} {item.title}")
        if item.image_url:
            print(f"    img: {item.image_url[:60]}...")
        if item.video_url:
            print(f"    vid: {item.video_url[:60]}...")
        print()
