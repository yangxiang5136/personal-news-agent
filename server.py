"""
News Agent Server (Railway-ready)
===================================

Serves the card-swipe UI and runs the scoring pipeline on a schedule.

Endpoints:
  GET /           → UI (index.html)
  GET /api/feed   → scored-feed.json
  GET /api/refresh → trigger pipeline re-run
  GET /api/status  → pipeline status

Local:
  python server.py

Railway:
  Deploys automatically, runs pipeline on startup + every 6 hours.
"""

import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8080))
UI_DIR = "ui"
FEED_PATH = "output/scored-feed.json"
REFRESH_INTERVAL = int(os.environ.get("REFRESH_HOURS", 6)) * 3600  # seconds

pipeline_status = {
    "last_run": None,
    "last_success": None,
    "running": False,
    "error": None,
    "items_scored": 0,
    "items_selected": 0,
}


def run_pipeline():
    """Run the full scoring pipeline."""
    global pipeline_status
    if pipeline_status["running"]:
        print("  Pipeline already running, skipping.")
        return False

    pipeline_status["running"] = True
    pipeline_status["last_run"] = datetime.now().isoformat()
    pipeline_status["error"] = None

    try:
        print(f"\n{'=' * 50}")
        print(f"  Pipeline run: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'=' * 50}\n")

        # Step 1: Ensure data files exist (fetch from GitHub if needed)
        print("  Step 1: Fetching data...")
        from engine.github_fetcher import ensure_data
        paths = ensure_data("data")

        if not paths["scores_path"]:
            raise Exception("No scored memories found (local or GitHub)")

        # Step 2: Build profile using fetched data
        print("\n  Step 2: Building profile...")
        from engine.build_profile_wrapper import build_profile
        os.makedirs("output", exist_ok=True)

        # Override paths for Railway
        os.environ["SCORES_PATH"] = paths["scores_path"]
        if paths["direction_path"]:
            os.environ["DIRECTION_PATH"] = paths["direction_path"]
        if paths["rubric_path"]:
            os.environ["RUBRIC_PATH"] = paths["rubric_path"]

        build_profile()

        # Step 3: Fetch news
        print("\n  Step 3: Fetching news...")
        from adapters.rss_adapter import RSSAdapter
        from adapters.cn_rss_adapter import ChineseRSSAdapter

        all_items = []

        en_adapter = RSSAdapter()
        en_items = en_adapter.fetch(max_per_feed=10)
        all_items.extend([item.to_dict() for item in en_items])

        try:
            cn_adapter = ChineseRSSAdapter()
            cn_items = cn_adapter.fetch(max_per_feed=10)
            all_items.extend([item.to_dict() for item in cn_items])
        except Exception as e:
            print(f"  WARNING: Chinese feeds failed: {e}")

        if not all_items:
            raise Exception("No items fetched from any source")

        # Step 4: Score
        print(f"\n  Step 4: Scoring {len(all_items)} items...")
        from engine.scorer import TwoTierScorer

        with open("output/profile.json") as f:
            profile = json.load(f)

        scorer = TwoTierScorer(profile)
        feed = scorer.score(all_items)

        # Step 5: Write output
        with open(FEED_PATH, "w") as f:
            json.dump(feed, f, indent=2, default=str)

        pipeline_status["last_success"] = datetime.now().isoformat()
        pipeline_status["items_scored"] = feed.get("total_items", 0)
        pipeline_status["items_selected"] = feed.get("briefing_items", 0)

        print(f"\n  ✓ Pipeline complete: {feed.get('briefing_items', 0)} items selected from {feed.get('total_items', 0)}")
        return True

    except Exception as e:
        pipeline_status["error"] = str(e)
        print(f"\n  ✗ Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        pipeline_status["running"] = False


def scheduler():
    """Background thread that runs the pipeline periodically."""
    # Run on startup after a short delay
    time.sleep(5)
    print(f"\n  Scheduler: initial pipeline run...")
    run_pipeline()

    # Then run every REFRESH_INTERVAL
    while True:
        time.sleep(REFRESH_INTERVAL)
        print(f"\n  Scheduler: periodic pipeline run...")
        run_pipeline()


class NewsAgentHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=UI_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/api/feed":
            self._serve_json(FEED_PATH)
        elif self.path == "/api/refresh":
            self._trigger_refresh()
        elif self.path == "/api/status":
            self._serve_status()
        elif self.path == "/" or self.path == "":
            self.path = "/index.html"
            super().do_GET()
        else:
            super().do_GET()

    def _serve_json(self, path):
        if not os.path.exists(path):
            self._json_response(404, {"error": "Feed not ready. Pipeline may still be running.", "status": pipeline_status})
            return

        with open(path, "r") as f:
            data = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def _trigger_refresh(self):
        if pipeline_status["running"]:
            self._json_response(409, {"message": "Pipeline already running", "status": pipeline_status})
            return

        t = threading.Thread(target=run_pipeline, daemon=True)
        t.start()
        self._json_response(200, {"message": "Pipeline refresh started", "status": pipeline_status})

    def _serve_status(self):
        self._json_response(200, pipeline_status)

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode("utf-8"))

    def log_message(self, format, *args):
        path = str(args[0]) if args else ""
        if "/api/" in path:
            print(f"  API: {args[0]}")


def main():
    os.makedirs(UI_DIR, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    if not os.path.exists(os.path.join(UI_DIR, "index.html")):
        print(f"  ERROR: {UI_DIR}/index.html not found")
        return

    # Start background scheduler
    t = threading.Thread(target=scheduler, daemon=True)
    t.start()

    server = HTTPServer(("0.0.0.0", PORT), NewsAgentHandler)
    print(f"\n  ✦ News Agent running at http://localhost:{PORT}")
    print(f"  ✦ Feed API:    /api/feed")
    print(f"  ✦ Refresh:     /api/refresh")
    print(f"  ✦ Status:      /api/status")
    print(f"  ✦ Auto-refresh every {REFRESH_INTERVAL // 3600}h")
    print(f"  ✦ Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
