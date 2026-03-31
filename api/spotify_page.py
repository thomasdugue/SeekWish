"""Fetch one page of Spotify tracks via the Web API (proxy for CORS)."""

import json
import os
import re
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://seekwish.vercel.app")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PLAYLIST_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,64}$")


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (json.JSONDecodeError, ValueError):
            self._json({"error": "bad request"}, 400)
            return

        token = body.get("token", "")
        playlist_id = body.get("playlist_id", "")
        offset = int(body.get("offset", 0))

        if not token or not playlist_id or not PLAYLIST_ID_RE.match(playlist_id):
            self._json({"error": "invalid params"}, 400)
            return

        url = (
            f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
            f"?offset={offset}&limit=100"
            f"&fields=next,total,items(track(name,duration_ms,album(name),artists(name)))"
        )
        headers = {"Authorization": f"Bearer {token}", "User-Agent": UA}

        # Single fetch with up to 2 retries for 429
        tracks = []
        has_next = False
        total = 0
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    wait = min(int(e.headers.get("Retry-After", "3")), 10)
                    time.sleep(wait)
                    continue
                self._json({"error": f"spotify error", "tracks": [], "has_next": False}, 502)
                return
            except Exception:
                self._json({"error": "fetch failed", "tracks": [], "has_next": False}, 502)
                return

            total = data.get("total", 0)
            has_next = bool(data.get("next"))
            for item in data.get("items", []):
                track = item.get("track")
                if not track:
                    continue
                title = track.get("name", "")
                artists = track.get("artists", [])
                artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
                duration = round(track.get("duration_ms", 0) / 1000)
                album = track.get("album", {}).get("name", "")
                if artist and title:
                    tracks.append({"artist": artist, "title": title, "duration": duration, "album": album})
            break

        self._json({"tracks": tracks, "has_next": has_next, "total": total})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
