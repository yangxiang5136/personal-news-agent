"""
server.py — Daily Brief web server for personal-news-agent

Serves the card-swipe UI and exposes two API endpoints:
  GET  /api/feed  → returns scored-feed.json
  POST /api/run   → triggers run.py in the background

Deployment:
  Local:   python server.py
  Railway: set start command to "python server.py"
            PORT is auto-injected by Railway

Feed path resolves in order:
  1. FEED_PATH env var (override)
  2. ./output/scored-feed.json  (default run.py output location)
  3. ./scored-feed.json          (fallback)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, send_from_directory

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
UI_DIR = BASE / "ui"

def _resolve_feed_path() -> Path:
    if os.environ.get("FEED_PATH"):
        return Path(os.environ["FEED_PATH"])
    candidate = BASE / "output" / "scored-feed.json"
    if candidate.exists():
        return candidate
    return BASE / "scored-feed.json"

FEED_PATH = _resolve_feed_path()
RUN_SCRIPT = BASE / "run.py"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=str(UI_DIR))

# Simple lock so we don't launch two pipeline runs at once
_run_lock = threading.Lock()
_run_in_progress = False


@app.route("/")
def index():
    return send_from_directory(str(UI_DIR), "index.html")


@app.route("/api/feed")
def api_feed():
    """Return the latest scored-feed.json."""
    feed_path = _resolve_feed_path()
    if not feed_path.exists():
        return jsonify({"error": "Feed not generated yet. Run: python run.py"}), 404
    try:
        with open(feed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Malformed JSON: {e}"}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    """Trigger the pipeline as a background process. Returns immediately."""
    global _run_in_progress

    if not RUN_SCRIPT.exists():
        return jsonify({"error": "run.py not found"}), 404

    if not _run_lock.acquire(blocking=False):
        return jsonify({"status": "already_running", "message": "Pipeline is already running"}), 409

    _run_in_progress = True

    def _execute():
        global _run_in_progress
        try:
            subprocess.run(
                [sys.executable, str(RUN_SCRIPT)],
                cwd=str(BASE),
                timeout=600,  # 10 min max
                capture_output=True,
                text=True,
            )
        except Exception:
            pass
        finally:
            _run_in_progress = False
            _run_lock.release()

    t = threading.Thread(target=_execute, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "Pipeline running in background (~2 min)"})


@app.route("/api/status")
def api_status():
    """Poll this to know if a pipeline run is in progress."""
    feed_path = _resolve_feed_path()
    feed_info = {}
    if feed_path.exists():
        try:
            with open(feed_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            feed_info = {
                "generated_at": data.get("generated_at"),
                "item_count": len(data.get("items", [])),
            }
        except Exception:
            pass
    return jsonify({
        "running": _run_in_progress,
        "feed": feed_info,
    })


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Dev: serve sample data when no feed exists (optional)
# ---------------------------------------------------------------------------
SAMPLE_FEED = {
    "generated_at": "2026-03-06T08:00:00",
    "items": [
        {
            "id": "sample-1",
            "title": "No feed generated yet — run the pipeline",
            "url": "#",
            "source_name": "System",
            "language": "en",
            "rank": 1,
            "selected": True,
            "summary": "Run 'python run.py' to generate your first daily brief. "
                       "Or click ↺ REFRESH to trigger it from the UI.",
            "scores": {
                "project_relevance": 0,
                "mental_model_update": 0,
                "serendipity": 0,
                "social_currency": 0,
                "time_sensitivity": 0,
            },
            "connections": [],
            "rationale": "This is a placeholder — your scored feed will appear here.",
        }
    ],
}


@app.route("/api/sample")
def api_sample():
    return jsonify(SAMPLE_FEED)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    print(f"Daily Brief server → http://localhost:{port}")
    print(f"Feed path: {_resolve_feed_path()}")
    app.run(host="0.0.0.0", port=port, debug=debug)
