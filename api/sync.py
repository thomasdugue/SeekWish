"""Vercel serverless function — cron job to sync all enabled playlists."""

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))  # noqa: E402

from _extractors import EXTRACTORS, normalize  # noqa: E402
from _supabase import get_service_client  # noqa: E402

CRON_SECRET = os.environ.get("CRON_SECRET", "")


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        # Vercel Cron sends GET with Authorization header
        auth = self.headers.get("Authorization", "")
        if not CRON_SECRET or not hmac.compare_digest(auth, f"Bearer {CRON_SECRET}"):
            self._json({"error": "Unauthorized"}, 401)
            return

        sb = get_service_client()

        # Fetch all enabled playlists
        result = sb.table("playlists").select("*").eq("enabled", True).execute()
        playlists = result.data or []

        total_new = 0
        synced = 0
        errors = []

        for p in playlists:
            provider = p["provider"]
            pid = p["playlist_id"]
            extractor = EXTRACTORS.get(provider)
            if not extractor:
                continue

            try:
                tracks, name = extractor(pid)
            except Exception:
                errors.append({"playlist_id": p["id"], "error": "extraction failed"})
                continue

            if not tracks:
                continue

            # Update playlist name if we got one
            update_data = {"last_synced": "now()"}
            if name and name != p.get("name"):
                update_data["name"] = name
            sb.table("playlists").update(update_data).eq("id", p["id"]).execute()

            # Upsert tracks
            new_in_playlist = 0
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

                # Check if this track is already linked
                existing = (
                    sb.table("playlist_tracks")
                    .select("track_id")
                    .eq("playlist_id", p["id"])
                    .eq("track_id", track_id)
                    .execute()
                )
                if not existing.data:
                    sb.table("playlist_tracks").insert(
                        {"playlist_id": p["id"], "track_id": track_id, "position": i}
                    ).execute()
                    new_in_playlist += 1

            total_new += new_in_playlist
            synced += 1

        self._json({
            "synced": synced,
            "total_playlists": len(playlists),
            "new_tracks": total_new,
            "errors": errors,
        })

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
