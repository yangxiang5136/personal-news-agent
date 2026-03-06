"""
News Source Contract
====================

Every news source adapter must output items conforming to this schema.
This is the interface boundary between swappable sources and the permanent engine.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SourceScoring:
    """Optional scoring from the news source itself (e.g., World Monitor threat classification)."""
    severity: Optional[str] = None       # "critical"/"high"/"medium"/"low"/"info"
    category: Optional[str] = None       # "conflict", "tech", "finance", etc.
    confidence: Optional[float] = None   # 0.0–1.0
    tags: list[str] = field(default_factory=list)


@dataclass
class GeoContext:
    """Optional geographic context."""
    country: Optional[str] = None
    region: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


@dataclass
class NewsItem:
    """
    A single news item, normalized from any source.

    Required: id, title, summary, url, published_at, source_name
    Optional: source_scoring, geo, image_url, video_url
    """
    id: str
    title: str
    summary: str                         # REQUIRED — adapter must generate if source doesn't provide
    url: str
    published_at: datetime
    source_name: str

    # Optional enrichment
    source_scoring: Optional[SourceScoring] = None
    geo: Optional[GeoContext] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None

    # Metadata
    source_system: str = "unknown"       # "worldmonitor", "rss", etc.
    adapter_version: str = "1.0"
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self):
        """Serialize to dict for JSON output."""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "source_name": self.source_name,
            "source_scoring": {
                "severity": self.source_scoring.severity,
                "category": self.source_scoring.category,
                "confidence": self.source_scoring.confidence,
                "tags": self.source_scoring.tags,
            } if self.source_scoring else None,
            "geo": {
                "country": self.geo.country,
                "region": self.geo.region,
                "lat": self.geo.lat,
                "lon": self.geo.lon,
            } if self.geo else None,
            "image_url": self.image_url,
            "video_url": self.video_url,
            "meta": {
                "source_system": self.source_system,
                "adapter_version": self.adapter_version,
                "fetched_at": self.fetched_at.isoformat(),
            }
        }
