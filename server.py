"""
News Agent UI Server
=====================

Serves the scored-feed.json as an API endpoint and the card-swipe UI.

Usage:
  cd ~/personal-news-agent
  python server.py

Then open: http://localhost:8080
"""

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler


PORT = 8080
FEED_PATH = "output/scored-feed.json"
UI_DIR = "ui"


class NewsAgentHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=UI_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/api/feed":
            self._serve_feed()
        elif self.path == "/" or self.path == "":
            self.path = "/index.html"
            super().do_GET()
        else:
            super().do_GET()

    def _serve_feed(self):
        feed_path = os.path.join(os.path.dirname(__file__), FEED_PATH)
        if not os.path.exists(feed_path):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Run 'python run.py' first to generate scored-feed.json"}).encode())
            return

        with open(feed_path, "r") as f:
            data = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def log_message(self, format, *args):
        # Quieter logging
        if "/api/" in str(args[0]):
            print(f"  API: {args[0]}")


def main():
    os.makedirs(UI_DIR, exist_ok=True)

    if not os.path.exists(os.path.join(UI_DIR, "index.html")):
        print(f"  ERROR: {UI_DIR}/index.html not found")
        return

    if not os.path.exists(FEED_PATH):
        print(f"  WARNING: {FEED_PATH} not found — run 'python run.py' first")
        print(f"  The UI will show an error until feed data is available.\n")

    server = HTTPServer(("0.0.0.0", PORT), NewsAgentHandler)
    print(f"\n  ✦ News Agent UI running at http://localhost:{PORT}")
    print(f"  ✦ Feed API at http://localhost:{PORT}/api/feed")
    print(f"  ✦ Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
