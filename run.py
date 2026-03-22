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
import json
import argparse
import base64
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


# ─── L1 score dimension mapping (0-10 internal → 0.0-1.0 digest) ─────

L1_DIMENSION_MAP = {
    "project_relevance": "relevance",
    "mental_model_update": "novelty",
    "serendipity": "actionability",
    "social_currency": "timeliness",
    "time_sensitivity": "source_quality",
}

# Map primary_value to suggested_category
CATEGORY_MAP = {
    "project_relevance": "Work",
    "mental_model_update": "Thinking",
    "serendipity": "Growth",
    "social_currency": "World",
    "time_sensitivity": "Urgent",
}


def _build_digest_json(feed):
    """Transform scored-feed into the digest JSON format for the shared bus.

    Follows the schema in docs/news-digest-format.md.
    """
    now = datetime.now()
    all_items = feed.get("all_items", [])
    digest_items = feed.get("digest", [])
    digest_ids = set(item.get("id") for item in digest_items)

    def _format_item(item):
        """Convert one scored item to digest format."""
        raw_scores = item.get("scores", {})
        # Normalize 0-10 scores to 0.0-1.0
        l1_scores = {
            "relevance": round(raw_scores.get("project_relevance", 0) / 10.0, 2),
            "novelty": round(raw_scores.get("mental_model_update", 0) / 10.0, 2),
            "actionability": round(raw_scores.get("serendipity", 0) / 10.0, 2),
            "timeliness": round(raw_scores.get("time_sensitivity", 0) / 10.0, 2),
            "source_quality": round(raw_scores.get("social_currency", 0) / 10.0, 2),
        }
        l1_scores["composite"] = round(
            sum(l1_scores.values()) / max(len(l1_scores), 1), 2
        )

        pv = item.get("primary_value", "")
        category = CATEGORY_MAP.get(pv, "World")
        tags = []
        tag = item.get("digest_tag", "")
        if tag:
            tags = [t.strip() for t in tag.replace(",", " ").split() if t.strip()][:5]

        # L2 analysis (only present for top items)
        l2 = None
        if item.get("rationale"):
            connections = item.get("connections", [])
            memory_ids = []
            aspects_hit = []
            is_surprise = False
            for c in connections:
                mid = c.get("memory_id", "")
                if mid:
                    memory_ids.append(mid)
                if c.get("type") == "bridge":
                    is_surprise = True

            l2 = {
                "connection_note": item.get("rationale", ""),
                "memory_connections": memory_ids,
                "rubric_aspects_hit": aspects_hit,
                "surprise": is_surprise,
                "deep_score": l1_scores["composite"],
            }

        source_lang = "en"
        if item.get("source_system") == "rss_cn" or item.get("translated_title"):
            source_lang = "zh"

        return {
            "id": item.get("id", 0),
            "rank": item.get("rank"),
            "title": item.get("title", ""),
            "source": item.get("source_name", ""),
            "source_lang": source_lang,
            "url": item.get("url", ""),
            "published_at": str(item.get("published_at", "")),
            "l1_scores": l1_scores,
            "suggested_category": category,
            "theme_tags": tags,
            "l2_analysis": l2,
            "summary": item.get("summary", "")[:300] if l2 else None,
            "nudge_line": item.get("rationale", "")[:200] if l2 else None,
        }

    # Format all items (ranked digest items + unranked L1-only items)
    formatted_items = []
    # Add digest items first (ranked)
    for item in digest_items:
        formatted_items.append(_format_item(item))
    # Add remaining items (unranked)
    for item in all_items:
        if item.get("id") not in digest_ids:
            formatted_items.append(_format_item(item))

    digest_json = {
        "metadata": {
            "generated_at": now.isoformat() + "Z",
            "pipeline_version": "1.0",
            "cycle": "daily",
            "items_fetched": feed.get("total_items", 0),
            "items_after_l1": feed.get("l1_scored", 0),
            "items_after_l2": feed.get("l2_analyzed", 0),
            "items_ranked": feed.get("briefing_items", 0),
            "feeds_succeeded": feed.get("_feeds_succeeded", 13),
            "feeds_failed": feed.get("_feeds_failed", []),
            "l1_model": feed.get("architecture", "deepseek-v3").split("|")[0].strip().split(":")[-1].strip() if feed.get("architecture") else "deepseek-v3",
            "l2_model": feed.get("architecture", "claude-haiku").split("|")[-1].strip().split(":")[-1].strip() if feed.get("architecture") else "claude-haiku",
            "web_view_url": "https://web-production-cb275.up.railway.app",
        },
        "items": formatted_items,
    }

    return digest_json


def _push_digest_to_bus(digest_json, date_str):
    """Push digest JSON to my-memories/news-digests/ via GitHub API.

    Returns True on success, False on failure. Never raises.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("[digest-push] ERROR: No GITHUB_TOKEN env var — skipping bus push")
        return False

    print("[digest-push] Token present (%s...%s), %d items in digest" % (
        token[:4], token[-4:], len(digest_json.get("items", []))
    ))

    try:
        import requests
    except ImportError:
        print("[digest-push] requests not available, trying urllib")
        try:
            import urllib.request
            import urllib.error
        except ImportError:
            print("[digest-push] ERROR: No HTTP library available — skipping")
            return False
        return _push_digest_urllib(digest_json, date_str, token)

    return _push_digest_requests(digest_json, date_str, token)


def _push_digest_requests(digest_json, date_str, token):
    """Push using requests library."""
    import requests as req

    repo = "yangxiang5136/my-memories"
    path = "news-digests/news-digest-%s.json" % date_str
    api_url = "https://api.github.com/repos/%s/contents/%s" % (repo, path)
    headers = {
        "Authorization": "token %s" % token,
        "Accept": "application/vnd.github.v3+json",
    }

    content_bytes = json.dumps(digest_json, indent=2, default=str).encode("utf-8")
    encoded = base64.b64encode(content_bytes).decode("utf-8")

    print("[digest-push] Pushing %s to %s" % (path, repo))

    # Check if file already exists (need sha to update)
    sha = None
    try:
        print("[digest-push] GET %s" % api_url)
        resp = req.get(api_url, headers=headers, timeout=30)
        print("[digest-push] GET status=%d" % resp.status_code)
        if resp.status_code == 200:
            sha = resp.json().get("sha")
            print("[digest-push] File exists, updating (sha=%s)" % sha[:8])
        elif resp.status_code == 404:
            print("[digest-push] File does not exist yet — will create")
        elif resp.status_code == 401:
            print("[digest-push] ERROR: 401 Unauthorized — GITHUB_TOKEN may be expired or lack repo access")
        elif resp.status_code == 403:
            print("[digest-push] ERROR: 403 Forbidden — rate limit or token permissions issue")
            print("[digest-push] Response: %s" % resp.text[:300])
        else:
            print("[digest-push] Unexpected GET status %d: %s" % (resp.status_code, resp.text[:200]))
    except Exception as e:
        print("[digest-push] GET exception (non-fatal): %s" % e)

    body = {
        "message": "News digest %s" % date_str,
        "content": encoded,
        "branch": "main",
    }
    if sha:
        body["sha"] = sha

    try:
        content_size = len(json.dumps(body).encode("utf-8"))
        print("[digest-push] PUT %s (%d bytes, sha=%s)" % (api_url, content_size, sha or "none"))
        resp = req.put(api_url, headers=headers, json=body, timeout=30)
        print("[digest-push] PUT status=%d" % resp.status_code)
        if resp.status_code in (200, 201):
            print("[digest-push] SUCCESS — pushed %s" % path)
            return True
        elif resp.status_code == 409:
            print("[digest-push] CONFLICT (409) — file changed since GET. Retrying with fresh sha...")
            # Retry once with fresh sha
            try:
                r2 = req.get(api_url, headers=headers, timeout=30)
                if r2.status_code == 200:
                    body["sha"] = r2.json().get("sha")
                    r3 = req.put(api_url, headers=headers, json=body, timeout=30)
                    if r3.status_code in (200, 201):
                        print("[digest-push] SUCCESS on retry")
                        return True
                    print("[digest-push] Retry also failed: %d" % r3.status_code)
            except Exception as e2:
                print("[digest-push] Retry exception: %s" % e2)
            return False
        elif resp.status_code == 422:
            print("[digest-push] VALIDATION ERROR (422) — may need sha for existing file")
            print("[digest-push] Response: %s" % resp.text[:400])
            return False
        else:
            print("[digest-push] FAILED — status %d: %s" % (resp.status_code, resp.text[:400]))
            return False
    except Exception as e:
        print("[digest-push] PUT EXCEPTION — %s" % e)
        return False


def _push_digest_urllib(digest_json, date_str, token):
    """Push using urllib (fallback if requests not available)."""
    import urllib.request
    import urllib.error

    repo = "yangxiang5136/my-memories"
    path = "news-digests/news-digest-%s.json" % date_str
    api_url = "https://api.github.com/repos/%s/contents/%s" % (repo, path)

    content_bytes = json.dumps(digest_json, indent=2, default=str).encode("utf-8")
    encoded = base64.b64encode(content_bytes).decode("utf-8")

    print("[digest-push] Pushing %s to %s (urllib)" % (path, repo))

    # Check if file exists
    sha = None
    try:
        print("[digest-push] GET %s (urllib)" % api_url)
        req = urllib.request.Request(api_url, headers={
            "Authorization": "token %s" % token,
            "Accept": "application/vnd.github.v3+json",
        })
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode("utf-8"))
        sha = data.get("sha")
        print("[digest-push] File exists, sha=%s" % sha[:8])
    except Exception as e:
        print("[digest-push] GET exception (non-fatal): %s" % e)

    body = {
        "message": "News digest %s" % date_str,
        "content": encoded,
        "branch": "main",
    }
    if sha:
        body["sha"] = sha

    try:
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(api_url, data=payload, method="PUT", headers={
            "Authorization": "token %s" % token,
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        })
        resp = urllib.request.urlopen(req, timeout=30)
        print("[digest-push] SUCCESS — pushed %s" % path)
        return True
    except urllib.error.HTTPError as e:
        print("[digest-push] FAILED — HTTP %d: %s" % (e.code, e.read()[:300]))
        return False
    except Exception as e:
        print("[digest-push] EXCEPTION — %s" % e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Personal News Agent")
    parser.add_argument("--profile-only", action="store_true", help="Only rebuild profile, don't score")
    parser.add_argument("--skip-cn", action="store_true", help="Skip Chinese feeds")
    parser.add_argument("--max-per-feed", type=int, default=10, help="Max items per feed")
    parser.add_argument("--cycle", type=str, default=None, help="Cycle name for digest filename (e.g. morning, evening)")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════════╗")
    print("║   Personal News Agent — Daily Run                ║")
    print("╚══════════════════════════════════════════════╝\n")

    # Detect Railway environment
    railway_vars = {k: v for k, v in os.environ.items() if k.startswith("RAILWAY")}
    is_railway = bool(railway_vars) or os.path.exists("/app/run.py")
    print("  Railway env vars: %s" % (railway_vars if railway_vars else "(none)"))
    print("  is_railway = %s" % is_railway)

    # ── Step 1: Build Profile ──
    print("Step 1: Building profile...")

    if is_railway:
        # Railway: fetch data from GitHub, then build profile via wrapper
        print("  [Railway mode] Fetching data from GitHub...")
        from engine.github_fetcher import ensure_data
        paths = ensure_data("data")
        if not paths["scores_path"]:
            print("  ERROR: No scored memories found (local or GitHub)")
            sys.exit(1)
        os.environ["SCORES_PATH"] = paths["scores_path"]
        if paths["direction_path"]:
            os.environ["DIRECTION_PATH"] = paths["direction_path"]
        if paths["rubric_path"]:
            os.environ["RUBRIC_PATH"] = paths["rubric_path"]
        from engine.build_profile_wrapper import build_profile as _build_profile
        _build_profile()
    else:
        # Local: call profile_builder directly (uses macOS paths)
        from engine.profile_builder import main as _build_profile_main
        try:
            sys.argv = ["profile_builder.py"]  # Reset argv for sub-module
            _build_profile_main()
        except SystemExit:
            pass
        # Enrich profile with memory index for L2 connection analysis
        try:
            from engine.build_profile_wrapper import _enrich_profile_with_memory_index
            _enrich_profile_with_memory_index()
        except Exception as e:
            print(f"  Note: Could not enrich memory index: {e}")

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

    # ── Step 5: Push digest to shared bus ──
    print("\n" + "=" * 50)
    print("Step 5: Pushing digest to shared bus...")
    print("=" * 50)

    try:
        digest_json = _build_digest_json(feed)
        print("  Built digest: %d items, metadata keys: %s" % (
            len(digest_json.get("items", [])),
            ", ".join(digest_json.get("metadata", {}).keys())
        ))
    except Exception as e:
        print("  ERROR building digest JSON: %s" % e)
        import traceback; traceback.print_exc()
        digest_json = None

    if digest_json:
        # Override cycle name if provided
        if args.cycle:
            digest_json["metadata"]["cycle"] = args.cycle
        digest_filename = date_str
        if args.cycle:
            digest_filename = "%s-%s" % (date_str, args.cycle)
        push_ok = _push_digest_to_bus(digest_json, digest_filename)
        if push_ok:
            print("  Digest pushed to my-memories/news-digests/")
        else:
            print("  ERROR: Digest push FAILED — check logs above for details")
    else:
        print("  ERROR: No digest to push (build failed)")

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
    sys.exit(0)
