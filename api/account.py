"""Vercel serverless function — account management (delete)."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))  # noqa: E402

from _supabase import get_service_client, get_user_id  # noqa: E402

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://seekwish.vercel.app")


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_DELETE(self):
        """Delete the authenticated user's account and all associated data."""
        user_id = get_user_id(self.headers)
        if not user_id:
            self._json({"error": "Unauthorized"}, 401)
            return

        sb = get_service_client()

        try:
            # Delete playlists (cascade deletes playlist_tracks)
            sb.table("playlists").delete().eq("user_id", user_id).execute()

            # Delete user track status
            sb.table("user_track_status").delete().eq("user_id", user_id).execute()

            # Delete the auth user via admin API
            sb.auth.admin.delete_user(user_id)

            self._json({"deleted": True})
        except Exception:
            self._json({"error": "Erreur lors de la suppression."}, 500)

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
