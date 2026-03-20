"""Vercel serverless function — extract tracks from playlist URLs."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))  # noqa: E402

from _extractors import EXTRACTORS, detect_provider  # noqa: E402

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://seekwish.vercel.app")
MAX_BODY = 10_000  # 10 KB — only needs a URL


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

        try:
            tracks, _name = EXTRACTORS[provider](playlist_id)
        except Exception:
            self._json({"error": "Erreur d'extraction. Réessaie ou utilise le companion local."})
            return

        self._json({"provider": provider, "tracks": tracks})

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
