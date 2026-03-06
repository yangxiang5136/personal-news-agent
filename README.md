# Personal News Agent

Part of the [Digital Me](https://github.com/yangxiang5136/digital-me) system.

Surfaces the external information most relevant to your cognitive context.
Reads your scored memories and growth direction, fetches news from swappable
sources, scores each item across 5 dimensions, and delivers a curated
briefing through a card-swipe UI.

## Architecture

```
News Sources (swappable)     Engine (permanent)        UI (swappable)
RSS feeds  ──► adapter ──►   Profile Builder           Card-swipe feed
World Monitor ► adapter ──►   Scorer (5 dimensions)    Dashboard
Future repo ─► adapter ──►   Selection Algorithm       Future UI
                              Reaction Processor
```

## Quick Start

```bash
# 1. Install dependencies
pip install feedparser pyyaml anthropic --break-system-packages

# 2. Build your profile from scored memories
python engine/profile_builder.py

# 3. Test the RSS adapter
python adapters/rss_adapter.py
```

## Agent Contract

```
Reads:
  ├── connections/scores-*.json  (from Connection Mapper)
  ├── direction.yaml             (manually maintained)
  └── News source APIs           (from adapters)

Writes to own folder:
  ├── output/profile.json
  ├── output/scored-feed.json
  ├── output/reactions/YYYY-MM-DD.yaml
  └── output/news-digests/YYYY-MM-DD.md
```

## Project Structure

```
personal-news-agent/
├── engine/
│   ├── profile_builder.py     ← Reads memories + direction → profile.json
│   ├── contracts.py           ← NewsItem dataclass (source contract)
│   ├── scorer.py              ← Claude scoring (5 dimensions) [TODO]
│   └── selector.py            ← Attention budget + selection [TODO]
├── adapters/
│   ├── rss_adapter.py         ← Simple RSS feed adapter
│   └── worldmonitor.py        ← World Monitor adapter [TODO]
├── config/
│   └── sources.yaml           ← Active sources configuration
├── output/                    ← Agent writes here (gitignored)
└── README.md
```

## Design Document

Full design at: `digital-me/design/personal-news-agent-design-v1.md`
