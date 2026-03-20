"""Vercel serverless function — pending tracks for companion polling."""

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))  # noqa: E402

from _supabase import get_service_client, get_user_id  # noqa: E402

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://seekwish.vercel.app")
MAX_BODY = 100_000  # 100 KB — can contain many track IDs
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        """Return tracks not yet sent to N+ for this user."""
        user_id = get_user_id(self.headers)
        if not user_id:
            self._json({"error": "Unauthorized"}, 401)
            return

        sb = get_service_client()

        query = sb.rpc(
            "get_pending_tracks",
            {"uid": user_id},
        ).execute()

        tracks = query.data or []
        self._json({"tracks": tracks})

    def do_POST(self):
        """ACK tracks as sent to N+."""
        user_id = get_user_id(self.headers)
        if not user_id:
            self._json({"error": "Unauthorized"}, 401)
            return

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

        track_ids = data.get("track_ids", [])
        if not track_ids or not isinstance(track_ids, list):
            self._json({"error": "No track_ids provided"}, 400)
            return

        # Validate all IDs are UUIDs
        if not all(isinstance(tid, str) and UUID_RE.match(tid) for tid in track_ids):
            self._json({"error": "Invalid track_id format"}, 400)
            return

        if len(track_ids) > 500:
            self._json({"error": "Too many track_ids (max 500)"}, 400)
            return

        sb = get_service_client()

        rows = [{"user_id": user_id, "track_id": tid} for tid in track_ids]
        try:
            sb.table("user_track_status").upsert(
                rows,
                on_conflict="user_id,track_id",
            ).execute()
        except Exception:
            self._json({"error": "Erreur serveur"}, 500)
            return

        self._json({"acknowledged": len(track_ids)})

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
