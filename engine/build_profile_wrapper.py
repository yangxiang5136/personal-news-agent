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
