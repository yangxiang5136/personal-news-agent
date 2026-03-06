"""
Profile Builder — Personal News Agent
======================================

Reads your scored memory index and direction.yaml to produce a compact
profile.json that the news scorer uses to evaluate items.

Part of the Digital Me system. Follows the agent contract:
  Reads:  connections/scores-*.json, direction.yaml, rubric.yaml
  Writes: output/profile.json

Usage:
  python profile_builder.py --memories-dir ~/Library/Mobile\ Documents/com~apple~CloudDocs/MemoryAgent/my-memories --config-dir ~/memory-agent --output output/profile.json

Or with defaults:
  python profile_builder.py
"""

import json
import yaml
import glob
import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MEMORIES_DIR = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/MemoryAgent/my-memories"
)
DEFAULT_CONFIG_DIR = os.path.expanduser("~/memory-agent")
DEFAULT_OUTPUT = "output/profile.json"

# How far back to look for recent memories (days)
RECENCY_WINDOW = 30

# Minimum score to consider a memory "strong" on an aspect
STRONG_SCORE_THRESHOLD = 0.6

# Minimum confidence to trust a score
CONFIDENCE_WEIGHTS = {"high": 1.0, "medium": 0.7, "low": 0.4}


# ---------------------------------------------------------------------------
# Step 1: Find and load the most recent scores file
# ---------------------------------------------------------------------------

def find_latest_scores(memories_dir):
    """Find the most recent scores-*.json file in connections/."""
    pattern = os.path.join(memories_dir, "connections", "scores-*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"ERROR: No scores-*.json found in {memories_dir}/connections/")
        print("Run your Connection Mapper first to generate scored data.")
        sys.exit(1)
    latest = files[-1]
    print(f"  Found: {os.path.basename(latest)}")
    return latest


def load_scores(scores_path):
    """Load and parse the scores JSON file."""
    with open(scores_path, "r") as f:
        data = json.load(f)
    memories = data.get("scored_memories", [])
    print(f"  Loaded {len(memories)} scored memories")
    return data


# ---------------------------------------------------------------------------
# Step 2: Load direction and rubric
# ---------------------------------------------------------------------------

def load_direction(config_dir):
    """Load direction.yaml."""
    path = os.path.join(config_dir, "direction.yaml")
    if not os.path.exists(path):
        # Also check if it's in the memories repo
        alt_path = os.path.join(DEFAULT_MEMORIES_DIR, "direction.yaml")
        if os.path.exists(alt_path):
            path = alt_path
        else:
            print(f"WARNING: direction.yaml not found at {path}")
            return None
    with open(path, "r") as f:
        direction = yaml.safe_load(f)
    print(f"  Loaded direction.yaml (version: {direction.get('version', 'unknown')})")
    return direction


def load_rubric(config_dir):
    """Load rubric.yaml for aspect definitions."""
    path = os.path.join(config_dir, "rubric.yaml")
    if not os.path.exists(path):
        print(f"WARNING: rubric.yaml not found at {path}")
        return None
    with open(path, "r") as f:
        rubric = yaml.safe_load(f)
    aspects = list(rubric.get("aspects", {}).keys())
    print(f"  Loaded rubric.yaml ({len(aspects)} aspects: {', '.join(aspects)})")
    return rubric


# ---------------------------------------------------------------------------
# Step 3: Analyze scored memories → extract current interest profile
# ---------------------------------------------------------------------------

def extract_memory_date(filename):
    """Try to extract a date from the memory filename (e.g., 2026-02-08-143420.md)."""
    try:
        # Format: YYYY-MM-DD-HHMMSS.md
        date_str = filename.replace(".md", "")[:10]
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, IndexError):
        return None


def analyze_memories(scores_data, rubric):
    """
    Analyze scored memories to build the current interest profile.

    Returns:
        aspect_profiles: dict of aspect -> {strength, top_memos, keywords, memory_count}
        recent_themes: list of dominant themes from recent memories
        memory_stats: summary statistics
    """
    memories = scores_data.get("scored_memories", [])
    cutoff = datetime.now() - timedelta(days=RECENCY_WINDOW)

    # Aggregate per aspect
    aspect_data = defaultdict(lambda: {
        "scores": [],
        "weighted_scores": [],
        "memos": [],
        "memory_count": 0,
        "strong_count": 0,
    })

    total_memories = len(memories)
    recent_count = 0

    for mem in memories:
        filename = mem.get("filename", "")
        mem_date = extract_memory_date(filename)
        is_recent = mem_date and mem_date >= cutoff

        if is_recent:
            recent_count += 1

        # Recency weight: recent memories count more
        recency_weight = 1.5 if is_recent else 0.7

        aspects = mem.get("aspects", {})
        for aspect_name, aspect_data_item in aspects.items():
            score = aspect_data_item.get("score", 0)
            confidence = aspect_data_item.get("confidence", "medium")
            memo = aspect_data_item.get("memo", "")

            conf_weight = CONFIDENCE_WEIGHTS.get(confidence, 0.5)
            weighted = score * conf_weight * recency_weight

            ad = aspect_data[aspect_name]
            ad["scores"].append(score)
            ad["weighted_scores"].append(weighted)
            ad["memory_count"] += 1

            if score >= STRONG_SCORE_THRESHOLD:
                ad["strong_count"] += 1
                if memo:
                    ad["memos"].append(memo)

    # Build aspect profiles
    aspect_profiles = {}
    for aspect_name, ad in aspect_data.items():
        if not ad["weighted_scores"]:
            continue

        avg_weighted = sum(ad["weighted_scores"]) / len(ad["weighted_scores"])
        avg_raw = sum(ad["scores"]) / len(ad["scores"])

        # Extract keywords from memos (simple word frequency)
        keywords = extract_keywords_from_memos(ad["memos"])

        aspect_profiles[aspect_name] = {
            "strength": round(avg_weighted, 3),
            "raw_average": round(avg_raw, 3),
            "memory_count": ad["memory_count"],
            "strong_count": ad["strong_count"],
            "top_memos": ad["memos"][:5],  # Keep top 5 memos for context
            "keywords": keywords[:10],
        }

    # Sort by strength
    aspect_profiles = dict(
        sorted(aspect_profiles.items(), key=lambda x: x[1]["strength"], reverse=True)
    )

    # Identify dominant themes from surprise memories and emerging aspects
    surprise_themes = []
    for s in scores_data.get("surprise_memories", []):
        surprise_themes.append(s.get("what_surprised", ""))

    emerging = []
    for e in scores_data.get("emerging_aspects", []):
        emerging.append(e.get("suggested_name", ""))

    memory_stats = {
        "total_memories": total_memories,
        "recent_memories": recent_count,
        "recency_window_days": RECENCY_WINDOW,
        "aspects_analyzed": len(aspect_profiles),
    }

    return aspect_profiles, surprise_themes, emerging, memory_stats


def extract_keywords_from_memos(memos):
    """Extract frequently occurring meaningful words from memo texts."""
    # Common stop words to filter out
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "about", "this", "that",
        "these", "those", "with", "from", "into", "for", "and", "but", "or",
        "not", "no", "so", "if", "then", "than", "too", "very", "just",
        "how", "what", "which", "who", "whom", "where", "when", "why",
        "it", "its", "of", "to", "in", "on", "at", "by", "as", "he", "she",
        "they", "them", "their", "his", "her", "more", "some", "any", "all",
        "also", "between", "through", "both", "each", "other", "here", "there",
        "up", "out", "over", "under", "again", "once", "score", "scoring",
        "memory", "memories", "aspect", "discusses", "mentions", "describes",
        "related", "relevant", "involves", "includes", "focus", "focused",
    }

    word_counts = defaultdict(int)
    for memo in memos:
        words = memo.lower().split()
        for word in words:
            # Clean punctuation
            clean = word.strip(".,;:!?\"'()[]{}—–-")
            if len(clean) > 3 and clean not in stop_words and clean.isalpha():
                word_counts[clean] += 1

    # Sort by frequency, return top keywords
    sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
    return [word for word, count in sorted_words if count >= 2]


# ---------------------------------------------------------------------------
# Step 4: Build the profile
# ---------------------------------------------------------------------------

def build_profile(aspect_profiles, surprise_themes, emerging, direction, rubric, memory_stats):
    """
    Assemble the final profile.json.

    The profile has two layers:
      Layer 1 — Current interests (from scored memories)
      Layer 2 — Growth directions (from direction.yaml)

    Plus metadata for the scoring engine.
    """
    # --- Layer 1: Current Interests ---
    # Extract topics and keywords from the strongest aspects
    current_topics = []
    current_keywords = []
    aspect_summaries = []

    for aspect_name, profile in aspect_profiles.items():
        if profile["strength"] > 0.3:  # Only include meaningful aspects
            current_topics.append(aspect_name)
            current_keywords.extend(profile["keywords"])
            aspect_summaries.append(
                f"{aspect_name} (strength: {profile['strength']:.2f}, "
                f"{profile['strong_count']} strong memories)"
            )

    # Deduplicate keywords while preserving order
    seen = set()
    unique_keywords = []
    for kw in current_keywords:
        if kw not in seen:
            seen.add(kw)
            unique_keywords.append(kw)

    # Build natural language summary from memos
    top_memos = []
    for aspect_name, profile in list(aspect_profiles.items())[:3]:
        top_memos.extend(profile["top_memos"][:2])

    current_summary = (
        f"Currently focused on {memory_stats['total_memories']} captured thoughts "
        f"across {memory_stats['aspects_analyzed']} aspects. "
        f"Strongest aspects: {', '.join(aspect_summaries[:3])}. "
    ) if isinstance(memory_stats.get('total_memories'), int) else (
        f"Analyzing {memory_stats['total_memories']} memories "
        f"({memory_stats['recent_memories']} from last {RECENCY_WINDOW} days). "
        f"Strongest aspects: {', '.join(aspect_summaries[:3])}. "
    )

    if top_memos:
        current_summary += "Recent thinking includes: " + "; ".join(top_memos[:3]) + "."

    # --- Layer 2: Growth Directions ---
    growth = {}
    if direction:
        growing_toward = direction.get("growing_toward", [])
        growing_away = direction.get("growing_away_from", [])
        blind_spots = direction.get("blind_spot_watch", [])

        # Extract keywords from growth directions
        growth_keywords = []
        for item in growing_toward:
            words = item.lower().split()
            for w in words:
                clean = w.strip(".,;:!?\"'()[]{}—–-")
                if len(clean) > 3 and clean.isalpha():
                    growth_keywords.append(clean)

        growth = {
            "topics": growing_toward,
            "keywords": growth_keywords[:15],
            "growing_away_from": growing_away,
            "blind_spots": blind_spots,
            "summary": " | ".join(growing_toward) if growing_toward else "No growth directions defined.",
        }

    # --- Aspiration Gap ---
    # Measure how much growth directions overlap with current activity
    growth_kw_set = set(growth.get("keywords", []))
    current_kw_set = set(unique_keywords)
    overlap = growth_kw_set & current_kw_set
    gap = growth_kw_set - current_kw_set

    aspiration_gap = {
        "overlap_keywords": list(overlap),
        "gap_keywords": list(gap),
        "gap_ratio": round(len(gap) / max(len(growth_kw_set), 1), 2),
        "interpretation": (
            "High gap — growth directions are mostly unexplored in current thinking. "
            "Increase exploration weight in news scoring."
            if len(gap) > len(overlap)
            else "Low gap — growth directions are already showing up in current thinking. "
            "Current scoring balance is appropriate."
        ),
    }

    # --- Assemble profile ---
    profile = {
        "profile_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "source_files": {
            "scores": memory_stats.get("scores_file", "unknown"),
            "direction": "direction.yaml",
            "rubric": "rubric.yaml",
        },
        "current_interests": {
            "topics": current_topics,
            "keywords": unique_keywords[:20],
            "regions": [],  # Can be populated later from memory content
            "summary": current_summary,
            "aspect_profiles": aspect_profiles,
        },
        "growth_directions": growth,
        "aspiration_gap": aspiration_gap,
        "attention_budget": {
            "daily_items": 10,
            "max_scan_time_seconds": 120,
            "current_weight": 0.6,
            "growth_weight": 0.4,
            "min_growth_items": 2,
        },
        "signals": {
            "surprise_themes": surprise_themes,
            "emerging_aspects": emerging,
        },
        "memory_stats": memory_stats,
    }

    return profile


# ---------------------------------------------------------------------------
# Step 5: Generate the Claude prompt context
# ---------------------------------------------------------------------------

def generate_scorer_context(profile):
    """
    Generate the natural language context block that gets sent to Claude
    when scoring news items. This is what the scorer will use.
    """
    ctx_parts = []

    ctx_parts.append("=== SEAN'S COGNITIVE CONTEXT ===\n")

    # Current interests
    ci = profile["current_interests"]
    ctx_parts.append("CURRENT FOCUS:")
    ctx_parts.append(ci["summary"])
    ctx_parts.append(f"Keywords: {', '.join(ci['keywords'][:15])}")
    ctx_parts.append("")

    # Top aspect details with memos
    ctx_parts.append("TOP ASPECTS (what Sean is thinking about):")
    for aspect_name, ap in list(ci["aspect_profiles"].items())[:3]:
        ctx_parts.append(f"  {aspect_name} (strength {ap['strength']:.2f}):")
        for memo in ap["top_memos"][:2]:
            ctx_parts.append(f"    - {memo}")
    ctx_parts.append("")

    # Growth directions
    gd = profile.get("growth_directions", {})
    if gd.get("topics"):
        ctx_parts.append("GROWTH DIRECTIONS (where Sean wants to go):")
        for topic in gd["topics"]:
            ctx_parts.append(f"  → {topic}")
        ctx_parts.append("")

    if gd.get("growing_away_from"):
        ctx_parts.append("PATTERNS TO BREAK:")
        for pattern in gd["growing_away_from"]:
            ctx_parts.append(f"  ✗ {pattern}")
        ctx_parts.append("")

    if gd.get("blind_spots"):
        ctx_parts.append("BLIND SPOT QUESTIONS:")
        for q in gd["blind_spots"]:
            ctx_parts.append(f"  ? {q}")
        ctx_parts.append("")

    # Aspiration gap
    gap = profile.get("aspiration_gap", {})
    if gap:
        ctx_parts.append(f"ASPIRATION GAP: {gap.get('interpretation', 'unknown')}")
        if gap.get("gap_keywords"):
            ctx_parts.append(f"  Unexplored growth keywords: {', '.join(gap['gap_keywords'][:8])}")
        ctx_parts.append("")

    # Surprise signals
    signals = profile.get("signals", {})
    if signals.get("surprise_themes"):
        ctx_parts.append("RECENT SURPRISES (things that don't fit the rubric):")
        for s in signals["surprise_themes"]:
            ctx_parts.append(f"  ! {s}")
        ctx_parts.append("")

    ctx_parts.append("=== END CONTEXT ===")

    return "\n".join(ctx_parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build profile from scored memories")
    parser.add_argument(
        "--memories-dir", default=DEFAULT_MEMORIES_DIR,
        help="Path to my-memories repo"
    )
    parser.add_argument(
        "--config-dir", default=DEFAULT_CONFIG_DIR,
        help="Path to config directory (rubric.yaml, direction.yaml)"
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT,
        help="Output path for profile.json"
    )
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════╗")
    print("║   Personal News Agent — Profile Builder  ║")
    print("╚══════════════════════════════════════╝\n")

    # Load inputs
    print("Loading inputs...")
    scores_path = find_latest_scores(args.memories_dir)
    scores_data = load_scores(scores_path)
    direction = load_direction(args.config_dir)
    rubric = load_rubric(args.config_dir)

    # Analyze
    print("\nAnalyzing memories...")
    aspect_profiles, surprise_themes, emerging, memory_stats = analyze_memories(
        scores_data, rubric
    )
    memory_stats["scores_file"] = os.path.basename(scores_path)

    # Report
    print(f"\n  Aspect strengths:")
    for name, p in aspect_profiles.items():
        bar = "█" * int(p["strength"] * 20) + "░" * (20 - int(p["strength"] * 20))
        print(f"    {name:15s} [{bar}] {p['strength']:.2f}  ({p['strong_count']} strong)")

    # Build profile
    print("\nBuilding profile...")
    profile = build_profile(
        aspect_profiles, surprise_themes, emerging, direction, rubric, memory_stats
    )

    # Generate scorer context
    scorer_context = generate_scorer_context(profile)
    profile["scorer_context"] = scorer_context

    # Write output
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(profile, f, indent=2, default=str)
    print(f"\n  ✓ Profile written to: {args.output}")

    # Also write the scorer context as readable text
    context_path = args.output.replace(".json", "-context.txt")
    with open(context_path, "w") as f:
        f.write(scorer_context)
    print(f"  ✓ Scorer context written to: {context_path}")

    # Print summary
    print(f"\n{'─' * 50}")
    print(f"  Memories analyzed:  {memory_stats['total_memories']}")
    print(f"  Recent (last {RECENCY_WINDOW}d): {memory_stats['recent_memories']}")
    print(f"  Aspects:            {memory_stats['aspects_analyzed']}")
    print(f"  Growth topics:      {len(profile['growth_directions'].get('topics', []))}")
    gap_ratio = profile["aspiration_gap"]["gap_ratio"]
    print(f"  Aspiration gap:     {gap_ratio:.0%} {'(high — explore more)' if gap_ratio > 0.5 else '(balanced)'}")
    print(f"{'─' * 50}\n")

    # Preview the scorer context
    print("Scorer context preview:")
    print("─" * 50)
    for line in scorer_context.split("\n")[:20]:
        print(f"  {line}")
    print("  ...")
    print("─" * 50)
    print("\nDone. Next step: run the news scorer with this profile.\n")


if __name__ == "__main__":
    main()
