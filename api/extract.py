"""Vercel serverless function — extract tracks from playlist URLs."""

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))  # noqa: E402

from _extractors import EXTRACTORS, detect_provider  # noqa: E402

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://seekwish.vercel.app")
MAX_BODY = 10_000  # 10 KB — only needs a URL
MAX_TRACKS = 500  # Cap response size
# Simple playlist ID format check to reject garbage input
PLAYLIST_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._json({"error": "Invalid request"}, 400)
            return

        if content_length > MAX_BODY:
            self._json({"error": "Payload too large"}, 413)
            return

        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json({"error": "Invalid JSON"}, 400)
            return

        url = data.get("url", "").strip()
        if not url:
            self._json({"error": "No URL provided"})
            return

        provider, playlist_id = detect_provider(url)
        if provider is None:
            self._json({"error": "URL non reconnue. Supporte Deezer, Spotify, YouTube Music."})
            return

        # Validate playlist ID format
        if not PLAYLIST_ID_RE.match(playlist_id):
            self._json({"error": "Format de playlist invalide."}, 400)
            return

        # Small delay to make bulk abuse more expensive
        time.sleep(0.5)

        try:
            result = EXTRACTORS[provider](playlist_id)
        except Exception:
            self._json({"error": "Erreur d'extraction. Réessaie plus tard."})
            return

        # Spotify returns (tracks, name, extra); others return (tracks, name)
        if len(result) == 3:
            tracks, _name, extra = result
        else:
            tracks, _name, extra = result[0], result[1], {}

        # Cap tracks to prevent huge responses
        resp = {"provider": provider, "tracks": tracks[:MAX_TRACKS]}
        # Pass Spotify token to frontend for client-side pagination
        if extra.get("spotify_token"):
            resp["spotify_token"] = extra["spotify_token"]
            resp["spotify_total"] = extra.get("spotify_total", 0)
        self._json(resp)

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
