from __future__ import annotations
"""
run.py — Personal News Agent main entry point
===============================================

Orchestrates: profile build → fetch (EN + CN) → two-tier score → output

Usage:
  # With DeepSeek Layer 1 + Claude Layer 2 (cheapest):
  export DEEPSEEK_API_KEY="sk-..."
  export ANTHROPIC_API_KEY="sk-ant-..."
  python run.py

  # With Claude only (fallback):
  export ANTHROPIC_API_KEY="sk-ant-..."
  python run.py

  # Just rebuild profile:
  python run.py --profile-only
"""

import os
import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser(description="Personal News Agent")
    parser.add_argument("--profile-only", action="store_true", help="Only rebuild profile, don't score")
    parser.add_argument("--skip-cn", action="store_true", help="Skip Chinese feeds")
    parser.add_argument("--max-per-feed", type=int, default=10, help="Max items per feed")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════════╗")
    print("║   Personal News Agent — Daily Run                ║")
    print("╚══════════════════════════════════════════════╝\n")

    # ── Step 1: Build Profile ──
    print("Step 1: Building profile...")
    from engine.profile_builder import main as build_profile
    # Run profile builder (it writes to output/profile.json)
    try:
        sys.argv = ["profile_builder.py"]  # Reset argv for sub-module
        build_profile()
    except SystemExit:
        pass

    if args.profile_only:
        print("\nProfile-only mode. Done.")
        return

    # ── Step 2: Fetch News ──
    print("\n" + "=" * 50)
    print("Step 2: Fetching news from all sources...")
    print("=" * 50)

    from adapters.rss_adapter import RSSAdapter
    all_items = []

    # English feeds
    print("\n  English feeds:")
    en_adapter = RSSAdapter()
    en_items = en_adapter.fetch(max_per_feed=args.max_per_feed)
    all_items.extend([item.to_dict() for item in en_items])

    # Chinese feeds
    if not args.skip_cn:
        print("\n  Chinese feeds:")
        try:
            from adapters.cn_rss_adapter import ChineseRSSAdapter
            cn_adapter = ChineseRSSAdapter()
            cn_items = cn_adapter.fetch(max_per_feed=args.max_per_feed)
            all_items.extend([item.to_dict() for item in cn_items])
        except Exception as e:
            print(f"  WARNING: Chinese feeds failed: {e}")
            print("  Continuing with English feeds only...")

    print(f"\n  Total: {len(all_items)} items from all sources")

    if not all_items:
        print("  No items fetched. Check internet connection.")
        sys.exit(1)

    # ── Step 3: Score ──
    print("\n" + "=" * 50)
    print("Step 3: Two-tier scoring...")
    print("=" * 50)

    import json
    from engine.scorer import TwoTierScorer

    with open("output/profile.json") as f:
        profile = json.load(f)

    scorer = TwoTierScorer(profile)
    feed = scorer.score(all_items)

    # ── Step 4: Write Output ──
    os.makedirs("output", exist_ok=True)
    with open("output/scored-feed.json", "w") as f:
        json.dump(feed, f, indent=2, default=str)

    # Also write a daily digest markdown
    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")
    digest_dir = "output/news-digests"
    os.makedirs(digest_dir, exist_ok=True)
    digest_path = f"{digest_dir}/{date_str}.md"

    with open(digest_path, "w") as f:
        f.write(f"# Daily Briefing — {datetime.now().strftime('%B %d, %Y')}\n\n")
        f.write(f"Architecture: {feed.get('architecture', 'unknown')}\n")
        f.write(f"Scanned: {feed['total_items']} items | L2: {feed.get('l2_analyzed', '?')} | Selected: {feed['briefing_items']}\n\n")
        f.write("---\n\n")

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
            title = item.get("translated_title") or item.get("title", "")
            source = item.get("source_name", "")
            tag = item.get("digest_tag", "")
            rationale = item.get("rationale", "")
            url = item.get("url", "")
            score = item.get("scores", {}).get(pv, 0)

            f.write(f"## [{label}] {title}\n\n")
            f.write(f"**{tag}** | {source} | score: {score}\n\n")
            if rationale:
                f.write(f"> {rationale}\n\n")
            if item.get("connections"):
                for c in item["connections"][:3]:
                    ctype = c.get("type", "?")
                    mid = c.get("memory_id", "?")
                    bridge = c.get("bridge_to", "")
                    f.write(f"- {ctype}: {mid}{' → ' + bridge if bridge else ''}\n")
                f.write("\n")
            f.write(f"[Read →]({url})\n\n---\n\n")

    # ── Print Summary ──
    print(f"\n{'━' * 55}")
    print(f"  YOUR BRIEFING — {datetime.now().strftime('%B %d, %Y')}")
    print(f"  {feed.get('architecture', '')}")
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
                print(f"    {ctype}: {mid}{' → ' + bridge if bridge else ''}")
        print()

    print(f"{'━' * 55}")
    print(f"  {feed['briefing_items']} selected from {feed['total_items']} scanned")
    print(f"  L1: {feed.get('l1_scored', '?')} | L2: {feed.get('l2_analyzed', '?')}")
    print(f"  Digest saved: {digest_path}")
    print(f"  Feed saved: output/scored-feed.json")
    print(f"{'━' * 55}\n")


if __name__ == "__main__":
    main()
