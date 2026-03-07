from __future__ import annotations
"""
Profile Builder Wrapper
========================

Wraps profile_builder.main() to work on Railway by setting
correct paths for data fetched from GitHub.
"""

import os
import sys
import shutil


def build_profile():
    """Build profile, using env var paths if set (Railway mode)."""
    scores_path = os.environ.get("SCORES_PATH")
    direction_path = os.environ.get("DIRECTION_PATH")
    rubric_path = os.environ.get("RUBRIC_PATH")

    if scores_path:
        # profile_builder looks in {memories_dir}/connections/
        # So we create ./connections/ and copy scores there
        conn_dir = "connections"
        os.makedirs(conn_dir, exist_ok=True)
        dest = os.path.join(conn_dir, os.path.basename(scores_path))
        if not os.path.exists(dest):
            shutil.copy2(scores_path, dest)
            print(f"    Copied scores -> {dest}")

    if direction_path or rubric_path:
        # profile_builder looks in {config_dir}/direction.yaml and rubric.yaml
        # So we create ./config-data/ and copy them there
        config_dir = "config-data"
        os.makedirs(config_dir, exist_ok=True)
        if direction_path and os.path.exists(direction_path):
            shutil.copy2(direction_path, os.path.join(config_dir, "direction.yaml"))
            print(f"    Copied direction.yaml -> {config_dir}/")
        if rubric_path and os.path.exists(rubric_path):
            shutil.copy2(rubric_path, os.path.join(config_dir, "rubric.yaml"))
            print(f"    Copied rubric.yaml -> {config_dir}/")

    from engine.profile_builder import main as profile_main

    # Pass correct paths as CLI arguments
    # memories_dir = "." so it finds ./connections/scores-*.json
    # config_dir = "config-data" so it finds config-data/direction.yaml
    old_argv = sys.argv
    sys.argv = [
        "profile_builder.py",
        "--memories-dir", ".",
        "--config-dir", "config-data" if (direction_path or rubric_path) else os.path.expanduser("~/memory-agent"),
        "--output", "output/profile.json",
    ]
    try:
        profile_main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv

    # Enrich profile with memory index (so L2 scorer can reference real IDs)
    _enrich_profile_with_memory_index()


def _enrich_profile_with_memory_index():
    """Add a memory_index to profile.json from the raw scores data."""
    import json
    import glob

    profile_path = "output/profile.json"
    if not os.path.exists(profile_path):
        return

    # Find scores file
    scores_files = sorted(glob.glob("connections/scores-*.json"))
    if not scores_files:
        scores_files = sorted(glob.glob("data/scores-*.json"))
    if not scores_files:
        return

    with open(scores_files[-1]) as f:
        scores_data = json.load(f)

    # Build compact memory index
    memory_index = []
    for i, mem in enumerate(scores_data.get("scored_memories", [])):
        filename = mem.get("filename", "")
        aspects = mem.get("aspects", {})

        # Find top aspect and its memo
        top_aspect = None
        top_score = 0
        top_memo = ""
        for aspect_name, aspect_data in aspects.items():
            score = aspect_data.get("score", 0)
            if score > top_score:
                top_score = score
                top_aspect = aspect_name
                top_memo = aspect_data.get("memo", "")

        # Also collect all high-scoring aspects
        strong_aspects = []
        for aspect_name, aspect_data in aspects.items():
            if aspect_data.get("score", 0) >= 0.6:
                strong_aspects.append(aspect_name)

        memory_index.append({
            "id": f"#{i+1}",
            "filename": filename,
            "top_aspect": top_aspect,
            "top_score": round(top_score, 2),
            "memo": top_memo[:120],
            "aspects": strong_aspects,
        })

    # Write back to profile
    with open(profile_path) as f:
        profile = json.load(f)

    profile["memory_index"] = memory_index

    # Also add memory index to scorer_context
    if memory_index:
        index_text = "\n\nSEAN'S MEMORY INDEX (reference by #ID):\n"
        for m in memory_index:
            aspects_str = ", ".join(m["aspects"]) if m["aspects"] else m["top_aspect"] or "?"
            index_text += f"  {m['id']} [{aspects_str}] {m['memo']}\n"
        profile["scorer_context"] = profile.get("scorer_context", "") + index_text

    with open(profile_path, "w") as f:
        json.dump(profile, f, indent=2, default=str)

    print(f"    Enriched profile with {len(memory_index)} memory references")
