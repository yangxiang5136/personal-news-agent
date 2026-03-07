from __future__ import annotations
"""
GitHub Data Fetcher
====================

Downloads required files from GitHub repos so the pipeline
can run on Railway without local file access.

Fetches:
  - connections/scores-*.json from yangxiang5136/my-memories
  - direction.yaml from memory-agent-bot repo (or bundled)
  - rubric.yaml from memory-agent-bot repo (or bundled)
"""

import json
import os
import re
import sys

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass


MEMORIES_REPO = "yangxiang5136/my-memories"
MEMORIES_BRANCH = "main"
GITHUB_RAW = "https://raw.githubusercontent.com"
GITHUB_API = "https://api.github.com"

# Local fallback paths (when running on dev machine)
LOCAL_MEMORIES = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/MemoryAgent/my-memories"
)
LOCAL_AGENT = os.path.expanduser("~/memory-agent")


def fetch_file(repo, path, branch="main", token=None):
    """Fetch a single file from GitHub."""
    url = f"{GITHUB_RAW}/{repo}/{branch}/{path}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"    ✗ Failed to fetch {path}: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"    ✗ Failed to fetch {path}: {e}")
        return None


def list_files(repo, path, branch="main", token=None):
    """List files in a GitHub directory."""
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}?ref={branch}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github.v3+json")
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"    ✗ Failed to list {path}: {e}")
        return []


def fetch_latest_scores(token=None):
    """Find and download the most recent scores-*.json file."""
    # Try local first
    local_conn = os.path.join(LOCAL_MEMORIES, "connections")
    if os.path.isdir(local_conn):
        files = [f for f in os.listdir(local_conn) if f.startswith("scores-") and f.endswith(".json")]
        if files:
            latest = sorted(files)[-1]
            path = os.path.join(local_conn, latest)
            print(f"    ✓ Local: {latest}")
            with open(path) as f:
                return json.load(f), latest
    
    # Fetch from GitHub
    print("    Fetching scores from GitHub...")
    files = list_files(MEMORIES_REPO, "connections", MEMORIES_BRANCH, token)
    score_files = [f for f in files if f.get("name", "").startswith("scores-") and f["name"].endswith(".json")]
    
    if not score_files:
        print("    ✗ No scores files found in repo")
        return None, None
    
    latest = sorted(score_files, key=lambda f: f["name"])[-1]
    content = fetch_file(MEMORIES_REPO, f"connections/{latest['name']}", MEMORIES_BRANCH, token)
    if content:
        print(f"    ✓ GitHub: {latest['name']}")
        return json.loads(content), latest["name"]
    return None, None


def fetch_yaml(filename, token=None):
    """Fetch direction.yaml or rubric.yaml."""
    # Try local first
    local_path = os.path.join(LOCAL_AGENT, filename)
    if os.path.exists(local_path):
        print(f"    ✓ Local: {filename}")
        with open(local_path) as f:
            return f.read()

    # Try bundled in repo
    bundled = os.path.join(os.path.dirname(__file__), "..", "config", filename)
    if os.path.exists(bundled):
        print(f"    ✓ Bundled: {filename}")
        with open(bundled) as f:
            return f.read()

    # Fetch from GitHub (memory-agent-bot repo)
    print(f"    Fetching {filename} from GitHub...")
    content = fetch_file("yangxiang5136/memory-agent-bot", filename, "main", token)
    if content:
        print(f"    ✓ GitHub: {filename}")
        return content

    print(f"    ✗ Could not find {filename}")
    return None


def ensure_data(output_dir="data"):
    """Download all required data files. Returns paths dict."""
    token = os.environ.get("GITHUB_TOKEN")
    os.makedirs(output_dir, exist_ok=True)

    print("  Fetching data files...")

    # Scores
    scores, scores_name = fetch_latest_scores(token)
    scores_path = None
    if scores:
        scores_path = os.path.join(output_dir, scores_name or "scores-latest.json")
        with open(scores_path, "w") as f:
            json.dump(scores, f, indent=2)

    # direction.yaml
    direction = fetch_yaml("direction.yaml", token)
    direction_path = None
    if direction:
        direction_path = os.path.join(output_dir, "direction.yaml")
        with open(direction_path, "w") as f:
            f.write(direction)

    # rubric.yaml
    rubric = fetch_yaml("rubric.yaml", token)
    rubric_path = None
    if rubric:
        rubric_path = os.path.join(output_dir, "rubric.yaml")
        with open(rubric_path, "w") as f:
            f.write(rubric)

    return {
        "scores_path": scores_path,
        "direction_path": direction_path,
        "rubric_path": rubric_path,
    }


if __name__ == "__main__":
    print("\nTesting GitHub data fetcher...\n")
    paths = ensure_data()
    print(f"\n  Results: {json.dumps(paths, indent=2)}")
