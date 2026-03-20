"""Vercel serverless function — CRUD for synced playlists."""

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))  # noqa: E402

from _extractors import EXTRACTORS, detect_provider, normalize  # noqa: E402
from _supabase import get_service_client, get_user_id  # noqa: E402

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://seekwish.vercel.app")
MAX_BODY = 10_000
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _upsert_tracks(sb, playlist_uuid, tracks):
    """Upsert tracks and link them to the playlist."""
    for i, t in enumerate(tracks):
        artist_norm = normalize(t.get("artist", ""))
        title_norm = normalize(t.get("title", ""))
        if not artist_norm and not title_norm:
            continue

        track_result = sb.table("tracks").upsert(
            {
                "artist": t.get("artist", ""),
                "title": t.get("title", ""),
                "album": t.get("album", ""),
                "duration": t.get("duration", 0),
                "artist_norm": artist_norm,
                "title_norm": title_norm,
            },
            on_conflict="artist_norm,title_norm",
        ).execute()

        if not track_result.data:
            continue
        track_id = track_result.data[0]["id"]

        existing = (
            sb.table("playlist_tracks")
            .select("track_id")
            .eq("playlist_id", playlist_uuid)
            .eq("track_id", track_id)
            .execute()
        )
        if not existing.data:
            sb.table("playlist_tracks").insert(
                {"playlist_id": playlist_uuid, "track_id": track_id, "position": i}
            ).execute()


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        """List user's playlists with track counts."""
        user_id = get_user_id(self.headers)
        if not user_id:
            self._json({"error": "Unauthorized"}, 401)
            return

        sb = get_service_client()
        result = sb.table("playlists").select("*, playlist_tracks(count)").eq("user_id", user_id).execute()
        playlists = result.data or []
        for p in playlists:
            pt = p.pop("playlist_tracks", [])
            p["track_count"] = pt[0]["count"] if pt else 0
        self._json({"playlists": playlists})

    def do_POST(self):
        """Add a new playlist: detect provider, extract tracks, upsert into DB."""
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

        url = data.get("url", "").strip()
        if not url:
            self._json({"error": "No URL provided"}, 400)
            return

        provider, playlist_id = detect_provider(url)
        if not provider:
            self._json({"error": "URL non reconnue."}, 400)
            return

        try:
            tracks, name = EXTRACTORS[provider](playlist_id)
        except Exception:
            self._json({"error": "Erreur d'extraction. Réessaie plus tard."}, 500)
            return

        sb = get_service_client()

        playlist_data = {
            "user_id": user_id,
            "provider": provider,
            "playlist_id": playlist_id,
            "url": url,
            "name": name or f"{provider} playlist",
            "enabled": True,
        }
        result = sb.table("playlists").upsert(
            playlist_data, on_conflict="user_id,provider,playlist_id"
        ).execute()

        if not result.data:
            self._json({"error": "Failed to save playlist"}, 500)
            return

        playlist_uuid = result.data[0]["id"]
        _upsert_tracks(sb, playlist_uuid, tracks)

        self._json({"playlist": result.data[0], "tracks_count": len(tracks)})

    def do_DELETE(self):
        """Remove a playlist by id."""
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

        playlist_id = data.get("id", "")
        if not playlist_id or not UUID_RE.match(str(playlist_id)):
            self._json({"error": "Invalid playlist id"}, 400)
            return

        sb = get_service_client()
        sb.table("playlists").delete().eq("id", playlist_id).eq("user_id", user_id).execute()
        self._json({"deleted": True})

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
