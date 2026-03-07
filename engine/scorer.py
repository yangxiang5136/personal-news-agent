from __future__ import annotations
"""
News Scorer — Personal News Agent
===================================

Takes a profile.json + list of NewsItems, scores each item across 5 dimensions
using Claude, applies the selection algorithm, and outputs scored-feed.json.

Part of the Digital Me system. Follows the agent contract:
  Reads:  output/profile.json, news items from adapters
  Writes: output/scored-feed.json

Usage:
  python engine/scorer.py --profile output/profile.json

Or as a module:
  from engine.scorer import NewsScorer
  scorer = NewsScorer(profile)
  scored_feed = scorer.score(items)
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

try:
    from anthropic import Anthropic
except ImportError:
    print("Install anthropic: pip install anthropic --break-system-packages")
    sys.exit(1)

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.contracts import NewsItem
from adapters.rss_adapter import RSSAdapter


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-20250514"
MAX_ITEMS_PER_BATCH = 10  # Items per Claude call
DEFAULT_PROFILE = "output/profile.json"
DEFAULT_OUTPUT = "output/scored-feed.json"


# ---------------------------------------------------------------------------
# Scoring prompt
# ---------------------------------------------------------------------------

SCORING_SYSTEM_PROMPT = """You are a personal news relevance scorer. You evaluate news items 
for a specific person based on their cognitive context — what they're working on, 
what they're thinking about, and where they want to grow.

You score each item on 5 dimensions (0-10):

1. PROJECT RELEVANCE — Can this person act on this within 7 days? 
   0=no connection, 5=directly relevant, 7=answers a current question, 10=changes an active decision

2. MENTAL MODEL UPDATE — Does this change how they understand something they're tracking?
   0=already known, 5=meaningful shift, 7=contradicts current understanding, 10=fundamentally reframes thinking
   IMPORTANT: Items that CHALLENGE existing beliefs should score HIGHER than items that confirm them.

3. SERENDIPITY — Does this connect two things they haven't linked yet?
   0=connects to nothing, 5=touches two already-linked areas, 7=bridges two unlinked areas, 10=bridges 3+ unlinked areas
   Look for items that touch MULTIPLE aspects of their profile in unexpected ways.

4. SOCIAL CURRENCY — Would knowing this make them more effective with other people?
   0=no social relevance, 5=relevant to their professional circle, 7=useful for an upcoming interaction

5. TIME SENSITIVITY — Will this lose value if seen tomorrow instead of today?
   0=evergreen, 5=relevant today less so tomorrow, 7=should see today, 10=see NOW

For each item, also provide:
- primary_value: which dimension scored highest
- digest_tag: 5-8 word label for quick scanning (this is the "information scent")
- rationale: one sentence explaining why this matters to them specifically
- connections: list of memory IDs from their context that this relates to, with connection type (reinforcement/challenge/bridge)

Respond with valid JSON only. No markdown fences, no preamble."""


def build_scoring_prompt(profile, items):
    """Build the user prompt with profile context + items to score."""
    scorer_context = profile.get("scorer_context", "No profile context available.")

    items_text = []
    for i, item in enumerate(items):
        entry = f"[{i+1}] {item['title']}\n"
        entry += f"    Source: {item['source_name']} | Published: {item['published_at']}\n"
        entry += f"    Summary: {item['summary']}\n"
        if item.get('source_scoring') and item['source_scoring'].get('category'):
            entry += f"    Category: {item['source_scoring']['category']}\n"
        if item.get('image_url'):
            entry += f"    [Has image]\n"
        items_text.append(entry)

    prompt = f"""{scorer_context}

--- NEWS ITEMS TO SCORE ---

{chr(10).join(items_text)}

--- INSTRUCTIONS ---

Score each item on the 5 dimensions (0-10). Return JSON in this exact format:
{{
  "scored_items": [
    {{
      "index": 1,
      "scores": {{
        "project_relevance": 0,
        "mental_model_update": 0,
        "serendipity": 0,
        "social_currency": 0,
        "time_sensitivity": 0
      }},
      "primary_value": "project_relevance",
      "digest_tag": "5-8 word scanning label",
      "rationale": "One sentence on why this matters to Sean",
      "connections": [
        {{
          "memory_id": "#14",
          "type": "bridge",
          "bridge_to": "#27"
        }}
      ]
    }}
  ]
}}

Score ALL {len(items)} items. Be specific in rationales — reference Sean's actual 
projects, principles, and growth directions from the context above.
Items that challenge his thinking should score higher on mental_model_update than 
items that confirm it.
Items connecting to his growth directions (design, deeper thinking, making things 
others use) should get serendipity boosts even if they don't match current projects."""

    return prompt


# ---------------------------------------------------------------------------
# Selection algorithm
# ---------------------------------------------------------------------------

def select_digest(scored_items, budget=10, min_growth=2):
    """
    Apply the selection algorithm from the design doc:
    1. Must include: top serendipity item (anti-filter-bubble)
    2. Must include: any item with time_sensitivity >= 7
    3. Must include: at least 1 mental_model_update item
    4. Fill remaining by max(project_relevance, serendipity)
    5. Cap: max 2 items per topic cluster (by source)
    6. Cap: total <= budget
    """
    if not scored_items:
        return []

    selected = []
    selected_ids = set()

    def add(item):
        if item["id"] not in selected_ids:
            selected.append(item)
            selected_ids.add(item["id"])

    # Rule 1: Top serendipity item
    by_serendipity = sorted(scored_items, key=lambda x: x.get("scores", {}).get("serendipity", 0), reverse=True)
    if by_serendipity:
        add(by_serendipity[0])

    # Rule 2: Urgent items
    for item in scored_items:
        if item["scores"]["time_sensitivity"] >= 7:
            add(item)

    # Rule 3: At least 1 mental model update
    by_mental = sorted(scored_items, key=lambda x: x.get("scores", {}).get("mental_model_update", 0), reverse=True)
    mental_added = any(
        s.get("scores", {}).get("mental_model_update", 0) >= 5 for s in selected
    )
    if not mental_added and by_mental:
        add(by_mental[0])

    # Rule 4: Fill by max(project_relevance, serendipity)
    def composite(item):
        s = item.get("scores", {})
        return max(s["project_relevance"], s["serendipity"])

    remaining = sorted(
        [i for i in scored_items if i["id"] not in selected_ids],
        key=composite, reverse=True
    )

    # Rule 5: Cap per source
    source_counts = {}
    for item in selected:
        src = item.get("source_name", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1

    for item in remaining:
        if len(selected) >= budget:
            break
        src = item.get("source_name", "unknown")
        if source_counts.get(src, 0) >= 2:
            continue
        add(item)
        source_counts[src] = source_counts.get(src, 0) + 1

    # Mark items
    for item in scored_items:
        item["included_in_briefing"] = item["id"] in selected_ids

    # Assign ranks
    for rank, item in enumerate(selected):
        item["rank"] = rank + 1

    return selected


# ---------------------------------------------------------------------------
# Scorer class
# ---------------------------------------------------------------------------

class NewsScorer:
    def __init__(self, profile, api_key=None):
        self.profile = profile
        self.client = Anthropic(api_key=api_key) if api_key else Anthropic()

    def score(self, items: list[dict]) -> dict:
        """
        Score a list of news items against the profile.
        Returns the full scored feed with selection applied.
        """
        if not items:
            return self._empty_feed()

        print(f"\n  Scoring {len(items)} items with Claude ({MODEL})...")

        # Batch if needed
        all_scored = []
        for batch_start in range(0, len(items), MAX_ITEMS_PER_BATCH):
            batch = items[batch_start:batch_start + MAX_ITEMS_PER_BATCH]
            batch_scored = self._score_batch(batch)
            all_scored.extend(batch_scored)

        # Merge scores back onto items
        scored_items = []
        for i, item in enumerate(items):
            if i < len(all_scored):
                merged = {**item, **all_scored[i]}
            else:
                merged = {**item, "scores": self._zero_scores(), "primary_value": "none",
                          "digest_tag": "unscored", "rationale": "Scoring failed", "connections": []}
            scored_items.append(merged)

        # Selection
        print("  Applying selection algorithm...")
        budget = self.profile.get("attention_budget", {}).get("daily_items", 10)
        digest = select_digest(scored_items, budget=budget)

        # Build feed
        feed = {
            "feed_version": "1.0",
            "generated_at": datetime.now().isoformat(),
            "profile_version": self.profile.get("profile_version", "unknown"),
            "total_items": len(scored_items),
            "briefing_items": len(digest),
            "digest": [self._format_digest_item(item) for item in digest],
            "all_items": scored_items,
        }

        return feed

    def _score_batch(self, items):
        """Score a batch of items via Claude."""
        prompt = build_scoring_prompt(self.profile, items)

        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SCORING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text
            # Clean potential markdown fences
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            data = json.loads(text)
            scored = data.get("scored_items", [])
            print(f"    ✓ Scored {len(scored)} items")
            return scored

        except json.JSONDecodeError as e:
            print(f"    ✗ JSON parse error: {e}")
            print(f"    Raw response: {text[:200]}...")
            return [self._zero_item() for _ in items]
        except Exception as e:
            print(f"    ✗ Claude API error: {e}")
            return [self._zero_item() for _ in items]

    def _zero_scores(self):
        return {
            "project_relevance": 0, "mental_model_update": 0,
            "serendipity": 0, "social_currency": 0, "time_sensitivity": 0
        }

    def _zero_item(self):
        return {
            "scores": self._zero_scores(), "primary_value": "none",
            "digest_tag": "scoring failed", "rationale": "Could not score",
            "connections": []
        }

    def _format_digest_item(self, item):
        """Format an item for the digest output."""
        return {
            "rank": item.get("rank", 0),
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "url": item.get("url", ""),
            "source_name": item.get("source_name", ""),
            "published_at": item.get("published_at", ""),
            "image_url": item.get("image_url"),
            "scores": item.get("scores", self._zero_scores()),
            "primary_value": item.get("primary_value", "none"),
            "digest_tag": item.get("digest_tag", ""),
            "rationale": item.get("rationale", ""),
            "connections": item.get("connections", []),
        }

    def _empty_feed(self):
        return {
            "feed_version": "1.0",
            "generated_at": datetime.now().isoformat(),
            "total_items": 0,
            "briefing_items": 0,
            "digest": [],
            "all_items": [],
        }


# ---------------------------------------------------------------------------
# Main — run standalone
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Score news items against your profile")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="Path to profile.json")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output path for scored-feed.json")
    parser.add_argument("--api-key", default=None, help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════╗")
    print("║   Personal News Agent — Scorer           ║")
    print("╚══════════════════════════════════════╝\n")

    # Load profile
    print("Loading profile...")
    if not os.path.exists(args.profile):
        print(f"  ERROR: {args.profile} not found. Run profile_builder.py first.")
        sys.exit(1)
    with open(args.profile) as f:
        profile = json.load(f)
    print(f"  ✓ Profile loaded ({profile.get('memory_stats', {}).get('total_memories', '?')} memories)")

    # Fetch news from RSS adapter
    print("\nFetching news...")
    adapter = RSSAdapter()
    raw_items = adapter.fetch(max_per_feed=10)

    if not raw_items:
        print("  No items fetched. Check your internet connection or feed URLs.")
        sys.exit(1)

    # Convert to dicts for scoring
    items_dicts = [item.to_dict() for item in raw_items]

    # Score
    scorer = NewsScorer(profile, api_key=args.api_key)
    feed = scorer.score(items_dicts)

    # Write output
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(feed, f, indent=2, default=str)
    print(f"\n  ✓ Scored feed written to: {args.output}")

    # Print digest
    print(f"\n{'━' * 50}")
    print(f"  YOUR BRIEFING — {datetime.now().strftime('%B %d, %Y')}")
    print(f"{'━' * 50}\n")

    VALUE_LABELS = {
        "project_relevance": "WORK",
        "mental_model_update": "THINKING",
        "serendipity": "DISCOVERY",
        "social_currency": "SOCIAL",
        "time_sensitivity": "URGENT"
    }

    for item in feed["digest"]:
        pv = item.get("primary_value", "none")
        label = VALUE_LABELS.get(pv, pv.upper())
        score = item["scores"].get(pv, 0)
        tag = item.get("digest_tag", "")
        title = item.get("title", "")[:70]
        source = item.get("source_name", "")
        connections = item.get("connections", [])

        print(f"  [{label}] {title}")
        print(f"    {tag} | {source} | score: {score}")
        if connections:
            for c in connections[:2]:
                ctype = c.get("type", "?")
                mid = c.get("memory_id", "?")
                print(f"    {ctype}: {mid}")
        print()

    print(f"{'━' * 50}")
    print(f"  {feed['briefing_items']} items selected from {feed['total_items']} scored")
    print(f"{'━' * 50}\n")


if __name__ == "__main__":
    main()
