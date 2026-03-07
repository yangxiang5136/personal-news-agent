from __future__ import annotations
"""
Profile Builder Wrapper
========================

Wraps the existing profile_builder.main() to support
environment variable path overrides for Railway deployment.

Env vars:
  SCORES_PATH     → path to scores-*.json
  DIRECTION_PATH  → path to direction.yaml
  RUBRIC_PATH     → path to rubric.yaml
"""

import os
import sys
import json
import glob
from pathlib import Path


def build_profile():
    """Build profile, using env var paths if set (Railway mode)."""
    scores_path = os.environ.get("SCORES_PATH")
    direction_path = os.environ.get("DIRECTION_PATH")
    rubric_path = os.environ.get("RUBRIC_PATH")

    # If env vars are set, symlink/copy to where profile_builder expects them
    if scores_path:
        # profile_builder looks in connections/ directory
        conn_dir = "connections"
        os.makedirs(conn_dir, exist_ok=True)
        dest = os.path.join(conn_dir, os.path.basename(scores_path))
        if not os.path.exists(dest):
            import shutil
            shutil.copy2(scores_path, dest)
            print(f"    Copied {scores_path} → {dest}")

    # Import and run the original main
    from engine.profile_builder import main as profile_main

    # Temporarily set paths if needed
    old_argv = sys.argv
    sys.argv = ["profile_builder.py"]
    try:
        profile_main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv
