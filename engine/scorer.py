from __future__ import annotations
"""
News Scorer — Personal News Agent (Two-Tier Architecture)
==========================================================

Layer 1: DeepSeek V3.2 (cheap, bulk scoring + translation)
Layer 2: Claude Haiku 4.5 (smart, connection analysis on top items)

Usage:
  export DEEPSEEK_API_KEY="sk-..."
  export ANTHROPIC_API_KEY="sk-ant-..."
  python engine/scorer.py
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.contracts import NewsItem
from adapters.rss_adapter import RSSAdapter


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Layer 1: Bulk scoring (cheap)
L1_PROVIDER = os.environ.get("L1_PROVIDER", "deepseek")  # "deepseek" or "anthropic"
L1_MODEL = os.environ.get("L1_MODEL", "deepseek-chat")
L1_BASE_URL = os.environ.get("L1_BASE_URL", "https://api.deepseek.com")
L1_API_KEY_ENV = "DEEPSEEK_API_KEY"

# Layer 2: Connection analysis (smart)
L2_PROVIDER = os.environ.get("L2_PROVIDER", "anthropic")
L2_MODEL = os.environ.get("L2_MODEL", "claude-haiku-4-5-20251001")
L2_API_KEY_ENV = "ANTHROPIC_API_KEY"

# Fallback: use Anthropic for everything if no DeepSeek key
FALLBACK_TO_ANTHROPIC = True

MAX_BATCH = 10
L1_TOP_N = 30       # How many items pass from Layer 1 to Layer 2
BUDGET = 10          # Final briefing items

DEFAULT_PROFILE = "output/profile.json"
DEFAULT_OUTPUT = "output/scored-feed.json"


# ---------------------------------------------------------------------------
# LLM Client Abstraction
# ---------------------------------------------------------------------------

class LLMClient:
    """Unified client for both OpenAI-compatible (DeepSeek) and Anthropic APIs."""

    def __init__(self, provider, model, api_key=None, base_url=None):
        self.provider = provider
        self.model = model

        if provider == "anthropic":
            try:
                from anthropic import Anthropic
                self.client = Anthropic(api_key=api_key)
            except ImportError:
                print("  pip install anthropic --break-system-packages")
                sys.exit(1)
        else:
            # OpenAI-compatible (DeepSeek, MiniMax, GLM, etc.)
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=api_key, base_url=base_url)
            except ImportError:
                print("  pip install openai --break-system-packages")
                sys.exit(1)

    def chat(self, system, user, max_tokens=4096):
        """Send a chat completion and return the text response."""
        try:
            if self.provider == "anthropic":
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}]
                )
                return response.content[0].text
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ]
                )
                return response.choices[0].message.content
        except Exception as e:
            print(f"    ✗ API error ({self.provider}/{self.model}): {e}")
            return None


# ---------------------------------------------------------------------------
# Layer 1: Bulk Scoring + Translation
# ---------------------------------------------------------------------------

L1_SYSTEM = """You are a news relevance scorer. Score each item on 5 dimensions (0-10).

Dimensions:
1. project_relevance — Useful for active projects within 7 days?
2. mental_model_update — Changes understanding of something being tracked?
3. serendipity — Connects two previously unlinked areas?
4. social_currency — Useful in conversations with others?
5. time_sensitivity — Loses value if seen tomorrow?

ALSO: If the item is NOT in English or Chinese, translate its title and summary into simplified Chinese (简体中文).

Respond with JSON only. No markdown fences."""


def build_l1_prompt(profile, items):
    """Build Layer 1 scoring prompt."""
    # Compact profile context (cheaper)
    keywords = profile.get("current_interests", {}).get("keywords", [])[:10]
    growth = profile.get("growth_directions", {}).get("topics", [])
    aspects = list(profile.get("current_interests", {}).get("aspect_profiles", {}).keys())[:4]

    context = f"""User focuses on: {', '.join(aspects)}
Keywords: {', '.join(keywords)}
Growth directions: {'; '.join(growth) if growth else 'none specified'}"""

    items_text = []
    for i, item in enumerate(items):
        title = item.get("title", "Untitled")
        summary = item.get("summary", "")[:200]
        source = item.get("source_name", "")
        items_text.append(f"[{i+1}] {title}\n    {source} | {summary}")

    return f"""{context}

--- ITEMS ---
{chr(10).join(items_text)}

--- OUTPUT ---
Return JSON:
{{
  "items": [
    {{
      "index": 1,
      "scores": {{
        "project_relevance": 0,
        "mental_model_update": 0,
        "serendipity": 0,
        "social_currency": 0,
        "time_sensitivity": 0
      }},
      "primary_value": "serendipity",
      "digest_tag": "5-8 word label",
      "translated_title": null,
      "translated_summary": null
    }}
  ]
}}

If item is NOT in English or Chinese, fill translated_title and translated_summary with Chinese translation. Otherwise set them to null.
Score ALL {len(items)} items."""


# ---------------------------------------------------------------------------
# Layer 2: Connection Analysis
# ---------------------------------------------------------------------------

L2_SYSTEM = """You are a personal intelligence analyst. For each news item, analyze how it
connects to the user's existing thinking (provided as scored memories with #ID references).

For each item provide:
- rationale: one specific sentence on why this matters to this person
- connections: list of memory references using the EXACT #IDs from the MEMORY INDEX provided
- connection_type: reinforcement (confirms thinking), challenge (contradicts),
  or bridge (links two previously unlinked areas)

CRITICAL: Use REAL memory IDs from the MEMORY INDEX (e.g. #1, #5, #14).
Do NOT use "?" as a memory_id. If no memory connects, use an empty connections list.

Challenge items should be flagged as HIGH VALUE — being contradicted is
more cognitively valuable than being confirmed.

Cross-language connections are especially valuable — flag when a non-English
item connects to memories in a different language context.

Respond with JSON only. No markdown fences."""


def build_l2_prompt(profile, items):
    """Build Layer 2 connection analysis prompt."""
    scorer_context = profile.get("scorer_context", "No context available.")

    items_text = []
    for i, item in enumerate(items):
        title = item.get("translated_title") or item.get("title", "")
        summary = item.get("translated_summary") or item.get("summary", "")[:300]
        source = item.get("source_name", "")
        tag = item.get("digest_tag", "")
        pv = item.get("primary_value", "")
        items_text.append(f"[{i+1}] {title}\n    {source} | {tag} | primary: {pv}\n    {summary}")

    return f"""{scorer_context}

--- TOP ITEMS TO ANALYZE ---
{chr(10).join(items_text)}

--- OUTPUT ---
Return JSON:
{{
  "items": [
    {{
      "index": 1,
      "rationale": "One sentence on why this matters to Sean specifically",
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

Analyze ALL {len(items)} items. Use ONLY real #IDs from the MEMORY INDEX above.
For bridge connections, specify which two memories are being bridged using their #IDs.
If a news item doesn't connect to any specific memory, return an empty connections list."""


# ---------------------------------------------------------------------------
# Selection Algorithm
# ---------------------------------------------------------------------------

def select_digest(scored_items, budget=10):
    """Select final briefing items."""
    if not scored_items:
        return []

    def get_score(item, dim):
        return item.get("scores", {}).get(dim, 0)

    selected = []
    selected_ids = set()

    def add(item):
        iid = item.get("id", id(item))
        if iid not in selected_ids:
            selected.append(item)
            selected_ids.add(iid)

    # Rule 1: Top serendipity item
    by_ser = sorted(scored_items, key=lambda x: get_score(x, "serendipity"), reverse=True)
    if by_ser:
        add(by_ser[0])

    # Rule 2: Urgent items
    for item in scored_items:
        if get_score(item, "time_sensitivity") >= 7:
            add(item)

    # Rule 3: At least 1 mental model update
    by_mental = sorted(scored_items, key=lambda x: get_score(x, "mental_model_update"), reverse=True)
    has_mental = any(get_score(s, "mental_model_update") >= 5 for s in selected)
    if not has_mental and by_mental:
        add(by_mental[0])

    # Rule 4: Fill by composite score
    def composite(item):
        return max(get_score(item, "project_relevance"), get_score(item, "serendipity"))

    remaining = sorted(
        [i for i in scored_items if i.get("id", id(i)) not in selected_ids],
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

    # Mark and rank
    for item in scored_items:
        item["included_in_briefing"] = item.get("id", id(item)) in selected_ids
    for rank, item in enumerate(selected):
        item["rank"] = rank + 1

    return selected


# ---------------------------------------------------------------------------
# Parse JSON safely
# ---------------------------------------------------------------------------

def parse_json(text):
    """Parse JSON from LLM response, handling markdown fences."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    ✗ JSON parse error: {e}")
        print(f"    First 200 chars: {text[:200]}...")
        return None


# ---------------------------------------------------------------------------
# Two-Tier Scorer
# ---------------------------------------------------------------------------

class TwoTierScorer:
    def __init__(self, profile):
        self.profile = profile

        # Initialize Layer 1
        l1_key = os.environ.get(L1_API_KEY_ENV)
        if l1_key and L1_PROVIDER != "anthropic":
            self.l1 = LLMClient(L1_PROVIDER, L1_MODEL, api_key=l1_key, base_url=L1_BASE_URL)
            self.l1_name = f"{L1_PROVIDER}/{L1_MODEL}"
        elif FALLBACK_TO_ANTHROPIC:
            ant_key = os.environ.get("ANTHROPIC_API_KEY")
            if ant_key:
                self.l1 = LLMClient("anthropic", "claude-haiku-4-5-20251001", api_key=ant_key)
                self.l1_name = "anthropic/haiku-4.5 (fallback)"
            else:
                print("  ERROR: No API key found. Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY")
                sys.exit(1)
        else:
            print("  ERROR: Set DEEPSEEK_API_KEY for Layer 1")
            sys.exit(1)

        # Initialize Layer 2
        l2_key = os.environ.get("ANTHROPIC_API_KEY")
        if l2_key:
            self.l2 = LLMClient("anthropic", L2_MODEL, api_key=l2_key)
            self.l2_name = f"anthropic/{L2_MODEL}"
        else:
            # Fall back to Layer 1 for everything
            self.l2 = self.l1
            self.l2_name = f"{self.l1_name} (shared)"

    def score(self, items):
        """Run the full two-tier scoring pipeline."""
        if not items:
            return self._empty_feed()

        # ── Layer 1: Bulk score all items ──
        print(f"\n  Layer 1: Scoring {len(items)} items [{self.l1_name}]...")
        all_l1 = []
        for start in range(0, len(items), MAX_BATCH):
            batch = items[start:start + MAX_BATCH]
            scored = self._run_l1(batch)
            all_l1.extend(scored)

        # Merge L1 scores onto items
        for i, item in enumerate(items):
            if i < len(all_l1) and all_l1[i]:
                item["scores"] = all_l1[i].get("scores", self._zero_scores())
                item["primary_value"] = all_l1[i].get("primary_value", "none")
                item["digest_tag"] = all_l1[i].get("digest_tag", "")
                # Translation
                if all_l1[i].get("translated_title"):
                    item["translated_title"] = all_l1[i]["translated_title"]
                if all_l1[i].get("translated_summary"):
                    item["translated_summary"] = all_l1[i]["translated_summary"]
            else:
                item["scores"] = self._zero_scores()
                item["primary_value"] = "none"
                item["digest_tag"] = "scoring failed"

        # ── Select top N for Layer 2 ──
        def total_score(item):
            s = item.get("scores", {})
            return sum(s.get(k, 0) for k in s)

        top_items = sorted(items, key=total_score, reverse=True)[:L1_TOP_N]
        print(f"\n  Layer 2: Deep analysis on top {len(top_items)} items [{self.l2_name}]...")

        # ── Layer 2: Connection analysis ──
        all_l2 = []
        for start in range(0, len(top_items), MAX_BATCH):
            batch = top_items[start:start + MAX_BATCH]
            analyzed = self._run_l2(batch)
            all_l2.extend(analyzed)

        # Merge L2 analysis onto top items
        for i, item in enumerate(top_items):
            if i < len(all_l2) and all_l2[i]:
                item["rationale"] = all_l2[i].get("rationale", "")
                item["connections"] = all_l2[i].get("connections", [])
            else:
                item["rationale"] = item.get("digest_tag", "")
                item["connections"] = []

        # Items not in top N get empty connections
        top_ids = {id(i) for i in top_items}
        for item in items:
            if id(item) not in top_ids:
                item["rationale"] = item.get("digest_tag", "")
                item["connections"] = []

        # ── Selection ──
        print("  Applying selection algorithm...")
        digest = select_digest(items, budget=BUDGET)

        return {
            "feed_version": "2.0",
            "generated_at": datetime.now().isoformat(),
            "architecture": f"L1:{self.l1_name} | L2:{self.l2_name}",
            "total_items": len(items),
            "l1_scored": len(items),
            "l2_analyzed": len(top_items),
            "briefing_items": len(digest),
            "digest": [self._fmt(item) for item in digest],
            "all_items": items,
        }

    def _run_l1(self, batch):
        """Run Layer 1 scoring on a batch."""
        prompt = build_l1_prompt(self.profile, batch)
        text = self.l1.chat(L1_SYSTEM, prompt, max_tokens=3000)
        data = parse_json(text)
        if data and "items" in data:
            print(f"    ✓ L1 scored {len(data['items'])} items")
            return data["items"]
        else:
            print(f"    ✗ L1 batch failed")
            return [None] * len(batch)

    def _run_l2(self, batch):
        """Run Layer 2 connection analysis on a batch."""
        prompt = build_l2_prompt(self.profile, batch)
        text = self.l2.chat(L2_SYSTEM, prompt, max_tokens=3000)
        data = parse_json(text)
        if data and "items" in data:
            print(f"    ✓ L2 analyzed {len(data['items'])} items")
            return data["items"]
        else:
            print(f"    ✗ L2 batch failed")
            return [None] * len(batch)

    def _zero_scores(self):
        return {"project_relevance": 0, "mental_model_update": 0,
                "serendipity": 0, "social_currency": 0, "time_sensitivity": 0}

    def _fmt(self, item):
        return {
            "rank": item.get("rank", 0),
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "translated_title": item.get("translated_title"),
            "summary": item.get("summary", ""),
            "translated_summary": item.get("translated_summary"),
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
        return {"feed_version": "2.0", "generated_at": datetime.now().isoformat(),
                "total_items": 0, "briefing_items": 0, "digest": [], "all_items": []}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

VALUE_LABELS = {
    "project_relevance": "WORK",
    "mental_model_update": "THINKING",
    "serendipity": "DISCOVERY",
    "social_currency": "SOCIAL",
    "time_sensitivity": "URGENT"
}


def main():
    parser = argparse.ArgumentParser(description="Two-tier news scorer")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════╗")
    print("║  Personal News Agent — Two-Tier Scorer       ║")
    print("╚══════════════════════════════════════════╝\n")

    # Load profile
    print("Loading profile...")
    if not os.path.exists(args.profile):
        print(f"  ERROR: {args.profile} not found. Run profile_builder.py first.")
        sys.exit(1)
    with open(args.profile) as f:
        profile = json.load(f)
    mem_count = profile.get("memory_stats", {}).get("total_memories", "?")
    print(f"  ✓ Profile loaded ({mem_count} memories)")

    # Fetch news
    print("\nFetching news...")
    adapter = RSSAdapter()
    raw_items = adapter.fetch(max_per_feed=10)
    if not raw_items:
        print("  No items fetched.")
        sys.exit(1)
    items = [item.to_dict() for item in raw_items]

    # Score
    scorer = TwoTierScorer(profile)
    feed = scorer.score(items)

    # Write
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(feed, f, indent=2, default=str)
    print(f"\n  ✓ Scored feed written to: {args.output}")

    # Print digest
    print(f"\n{'━' * 55}")
    print(f"  YOUR BRIEFING — {datetime.now().strftime('%B %d, %Y')}")
    print(f"  {feed['architecture']}")
    print(f"{'━' * 55}\n")

    for item in feed["digest"]:
        pv = item.get("primary_value", "none")
        label = VALUE_LABELS.get(pv, pv.upper())
        score = item.get("scores", {}).get(pv, 0)
        tag = item.get("digest_tag", "")
        title = item.get("translated_title") or item.get("title", "")
        title = title[:70]
        source = item.get("source_name", "")
        rationale = item.get("rationale", "")
        connections = item.get("connections", [])

        print(f"  [{label}] {title}")
        print(f"    {tag} | {source} | score: {score}")
        if rationale:
            print(f"    → {rationale[:80]}")
        if connections:
            for c in connections[:2]:
                ctype = c.get("type", "?")
                mid = c.get("memory_id", "?")
                bridge = c.get("bridge_to", "")
                bstr = f" → {bridge}" if bridge else ""
                print(f"    {ctype}: {mid}{bstr}")
        print()

    print(f"{'━' * 55}")
    print(f"  {feed['briefing_items']} selected from {feed['total_items']} scored")
    print(f"  L1: {feed.get('l1_scored', '?')} items | L2: {feed.get('l2_analyzed', '?')} items")
    print(f"{'━' * 55}\n")


if __name__ == "__main__":
    main()
