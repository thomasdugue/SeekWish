#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Audiophile Wishlist — Companion App
# A local web UI for importing playlists into Nicotine+.
#
# Usage: python3 companion.py
# Opens http://localhost:8484 in your browser.
#
# Zero dependencies — Python 3.9+ stdlib only.

import http.server
import json
import os
import platform
import re
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

PORT = 8484
SEEKWISH_URL = "https://seekwish.vercel.app"
SUPABASE_URL = "https://lyfdaagdqmkdcndstgkf.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx5ZmRhYWdkcW1rZGNuZHN0Z2tmIiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3NzM3Mzc2NTEsImV4cCI6MjA4OTMxMzY1MX0."
    "LriH69a5ucJCd8IkD325sXioD_G1qUFfxhn4BBHDfGc"
)
POLL_INTERVAL = 60  # seconds
TOKEN_REFRESH_INTERVAL = 50 * 60  # 50 minutes
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _get_nicotine_plugins_dir():
    """Get the Nicotine+ plugins directory for the current OS."""
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return os.path.join(appdata, "nicotine", "plugins", "audiophile_wishlist")
    return os.path.expanduser("~/.local/share/nicotine/plugins/audiophile_wishlist")


def _get_import_file_path():
    """Path to the shared JSON file the N+ plugin reads."""
    return os.path.join(_get_nicotine_plugins_dir(), "pending_import.json")


def _get_plugin_source_dir():
    """Get the plugin source directory (next to this script)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiophile_wishlist")


def _install_plugin():
    """Install/update the plugin into the Nicotine+ plugins directory.

    Returns (success: bool, message: str).
    """

    source_dir = _get_plugin_source_dir()
    target_dir = _get_nicotine_plugins_dir()

    if not os.path.isdir(source_dir):
        return False, f"Plugin source not found at {source_dir}"

    files_to_copy = ["__init__.py", "PLUGININFO"]

    try:
        os.makedirs(target_dir, exist_ok=True)

        updated = []
        for filename in files_to_copy:
            src = os.path.join(source_dir, filename)
            dst = os.path.join(target_dir, filename)

            if not os.path.exists(src):
                continue

            # Check if update needed
            needs_update = True
            if os.path.exists(dst):
                with open(src, "rb") as f:
                    src_content = f.read()
                with open(dst, "rb") as f:
                    dst_content = f.read()
                needs_update = (src_content != dst_content)

            if needs_update:
                import shutil
                shutil.copy2(src, dst)
                updated.append(filename)

        if updated:
            return True, f"Plugin installed ({', '.join(updated)}) → {target_dir}"
        return True, "Plugin already up to date"

    except Exception as e:
        return False, f"Installation failed: {e}"


def _get_install_status():
    """Check if the plugin is installed and up to date."""

    source_dir = _get_plugin_source_dir()
    target_dir = _get_nicotine_plugins_dir()
    init_src = os.path.join(source_dir, "__init__.py")
    init_dst = os.path.join(target_dir, "__init__.py")

    if not os.path.exists(init_dst):
        return {"installed": False, "up_to_date": False, "path": target_dir}

    up_to_date = False
    if os.path.exists(init_src):
        with open(init_src, "rb") as f:
            src = f.read()
        with open(init_dst, "rb") as f:
            dst = f.read()
        up_to_date = (src == dst)

    return {"installed": True, "up_to_date": up_to_date, "path": target_dir}


# ─────────────────────────────────────────────────────────────────────────────
# Auth / Config persistence
# ─────────────────────────────────────────────────────────────────────────────

def _get_config_path():
    """Path to the companion config file."""
    config_dir = os.path.expanduser("~/.config/seekwish")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "config.json")


def _load_config():
    """Load config from disk. Returns dict."""
    path = _get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(config):
    """Save config to disk with restricted permissions."""
    path = _get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.chmod(path, 0o600)


# Global auth state
_auth = {
    "access_token": None,
    "refresh_token": None,
    "email": None,
    "expires_at": 0,
}


def _refresh_access_token():
    """Exchange refresh token for a new access token via Supabase."""
    if not _auth["refresh_token"]:
        return False

    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token"
    payload = json.dumps({"refresh_token": _auth["refresh_token"]}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
    }

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        _auth["access_token"] = data.get("access_token")
        _auth["refresh_token"] = data.get("refresh_token", _auth["refresh_token"])
        _auth["expires_at"] = time.time() + data.get("expires_in", 3600) - 60

        user = data.get("user", {})
        _auth["email"] = user.get("email", "")

        # Persist refresh token
        config = _load_config()
        config["refresh_token"] = _auth["refresh_token"]
        config["email"] = _auth["email"]
        _save_config(config)

        return True
    except Exception as e:
        print(f"  Token refresh failed: {e}")
        return False


def _ensure_valid_token():
    """Ensure we have a valid access token, refreshing if needed."""
    if _auth["access_token"] and time.time() < _auth["expires_at"]:
        return True
    return _refresh_access_token()


def _init_auth_from_config():
    """Load saved refresh token from config on startup."""
    config = _load_config()
    refresh_token = config.get("refresh_token")
    if refresh_token:
        _auth["refresh_token"] = refresh_token
        _auth["email"] = config.get("email", "")
        print("  Found saved auth token, refreshing...")
        if _refresh_access_token():
            print(f"  ✓ Authenticated as {_auth['email']}")
        else:
            print("  ✗ Token refresh failed — please re-authenticate")


# ─────────────────────────────────────────────────────────────────────────────
# Polling thread — fetch pending tracks and write to N+
# ─────────────────────────────────────────────────────────────────────────────

_poll_force = threading.Event()


def _polling_loop():
    """Background daemon: poll /api/pending, write tracks, ACK."""
    while True:
        _poll_force.wait(timeout=POLL_INTERVAL)
        _poll_force.clear()

        if not _auth["access_token"]:
            continue

        if not _ensure_valid_token():
            continue

        try:
            # GET pending tracks
            req = urllib.request.Request(
                f"{SEEKWISH_URL}/api/pending",
                headers={
                    "Authorization": f"Bearer {_auth['access_token']}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            pending_tracks = data.get("tracks", [])
            if not pending_tracks:
                continue

            print(f"  → {len(pending_tracks)} pending tracks from server")

            # Write to pending_import.json
            import_path = _get_import_file_path()
            plugin_dir = os.path.dirname(import_path)
            os.makedirs(plugin_dir, exist_ok=True)

            payload = {
                "timestamp": time.time(),
                "tracks": [
                    {
                        "artist": t.get("artist", ""),
                        "title": t.get("title", ""),
                        "album": t.get("album", ""),
                        "duration": t.get("duration", 0),
                    }
                    for t in pending_tracks
                ],
            }
            with open(import_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            print(f"  ✓ Wrote {len(pending_tracks)} tracks to {import_path}")

            # ACK
            track_ids = [t["id"] for t in pending_tracks if "id" in t]
            if track_ids:
                ack_payload = json.dumps({"track_ids": track_ids}).encode("utf-8")
                ack_req = urllib.request.Request(
                    f"{SEEKWISH_URL}/api/pending",
                    data=ack_payload,
                    headers={
                        "Authorization": f"Bearer {_auth['access_token']}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(ack_req, timeout=15) as ack_resp:
                    ack_data = json.loads(ack_resp.read().decode("utf-8"))
                print(f"  ✓ ACK'd {ack_data.get('acknowledged', 0)} tracks")

        except Exception as e:
            print(f"  Poll error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────────────────────────────────────

def _fetch(url, headers=None, timeout=15):
    if headers is None:
        headers = {}
    if "User-Agent" not in headers:
        headers["User-Agent"] = USER_AGENT
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# URL detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_provider(url):
    url = url.strip()
    m = re.search(r"deezer\.com/(?:\w+/)?playlist/(\d+)", url)
    if m:
        return "deezer", m.group(1)
    m = re.search(r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)", url)
    if m:
        return "spotify", m.group(1)
    m = re.search(r"spotify:playlist:([a-zA-Z0-9]+)", url)
    if m:
        return "spotify", m.group(1)
    m = re.search(r"(?:music\.)?youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)", url)
    if m:
        return "ytmusic", m.group(1)
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Providers (same as plugin, standalone)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_deezer(playlist_id):
    tracks = []
    url = f"https://api.deezer.com/playlist/{playlist_id}/tracks?limit=100"
    while url:
        raw = _fetch(url)
        if raw is None:
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            break
        if "error" in data:
            break
        for item in data.get("data", []):
            artist = item.get("artist", {}).get("name", "")
            title = item.get("title", "")
            duration = item.get("duration", 0)
            album = item.get("album", {}).get("title", "")
            if artist and title:
                tracks.append({"artist": artist, "title": title, "duration": duration, "album": album})
        url = data.get("next")
    return tracks


def _extract_spotify(playlist_id):
    """Fetch tracks from a public Spotify playlist.

    Strategy: Parse __NEXT_DATA__ from the embed page which contains
    the full track list without needing any API calls.
    """

    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    html = _fetch(embed_url)
    if not html:
        return []

    # Extract __NEXT_DATA__ JSON from the embed page
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
    track_list = entity.get("trackList", [])

    tracks = []
    for item in track_list:
        title = item.get("title", "")
        # subtitle contains artist(s), separated by ",\xa0" for multi-artist
        artist = item.get("subtitle", "").replace("\xa0", " ")
        duration = round(item.get("duration", 0) / 1000)
        if artist and title:
            tracks.append({"artist": artist, "title": title, "duration": duration, "album": ""})

    return tracks


def _extract_ytmusic(playlist_id):
    url = f"https://music.youtube.com/playlist?list={playlist_id}"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    html = _fetch(url, headers=headers)
    if html is None:
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        html = _fetch(url, headers=headers)
    if html is None:
        return []
    tracks = []
    m = re.search(r"var\s+ytInitialData\s*=\s*({.*?});\s*</script>", html, re.DOTALL)
    if not m:
        m = re.search(r'window\["ytInitialData"\]\s*=\s*({.*?});\s*', html, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    _ytmusic_find_tracks(data, tracks)
    return tracks


def _ytmusic_find_tracks(data, results, depth=0):
    if depth > 20:
        return
    if isinstance(data, dict):
        if "musicResponsiveListItemRenderer" in data:
            renderer = data["musicResponsiveListItemRenderer"]
            title, artist = _ytmusic_parse_renderer(renderer)
            if title and artist:
                results.append({"artist": artist, "title": title, "duration": 0, "album": ""})
                return
        if "playlistVideoRenderer" in data:
            renderer = data["playlistVideoRenderer"]
            title_text = ""
            for run in renderer.get("title", {}).get("runs", []):
                title_text += run.get("text", "")
            if " - " in title_text:
                parts = title_text.split(" - ", 1)
                results.append({"artist": parts[0].strip(), "title": parts[1].strip(), "duration": 0, "album": ""})
            elif title_text:
                results.append({"artist": "", "title": title_text.strip(), "duration": 0, "album": ""})
            return
        for value in data.values():
            _ytmusic_find_tracks(value, results, depth + 1)
    elif isinstance(data, list):
        for item in data:
            _ytmusic_find_tracks(item, results, depth + 1)


def _ytmusic_parse_renderer(renderer):
    title = ""
    artist = ""
    flex_columns = renderer.get("flexColumns", [])
    if not flex_columns:
        return title, artist
    if len(flex_columns) > 0:
        col = flex_columns[0]
        text_obj = col.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text", {})
        for run in text_obj.get("runs", []):
            title += run.get("text", "")
    if len(flex_columns) > 1:
        col = flex_columns[1]
        text_obj = col.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text", {})
        parts = []
        for run in text_obj.get("runs", []):
            text = run.get("text", "")
            if text and text not in (" & ", " • ", ", ", " · "):
                parts.append(text)
            elif text in (" & ", ", "):
                parts.append(text)
        artist = "".join(parts).strip()
        artist = re.split(r"\s*[•·]\s*", artist)[0].strip()
    return title.strip(), artist.strip()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Server
# ─────────────────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Audiophile Wishlist</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0f0f0f;--bg2:#1a1a1a;--bg3:#222;--bg4:#2a2a2a;
  --text:#e0e0e0;--text2:#888;--text3:#555;
  --accent:#4ecdc4;--accent2:#44a39d;--accent-dim:rgba(78,205,196,.08);
  --red:#e74c3c;--green:#2ecc71;--orange:#f39c12;
  --border:#2a2a2a;--border2:#333;
  --radius:10px;
  --font:-apple-system,"SF Pro Display","Segoe UI",system-ui,sans-serif;
  --mono:"SF Mono","Fira Code","Cascadia Code",monospace;
}
body{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;padding:0}
.container{max-width:720px;margin:0 auto;padding:32px 20px}

/* Header */
.header{text-align:center;margin-bottom:40px}
.header h1{font-size:24px;font-weight:600;letter-spacing:-.5px;margin-bottom:6px;color:var(--text)}
.header h1 span{color:var(--accent)}
.header p{font-size:13px;color:var(--text2)}

/* Card */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-bottom:20px}
.card-title{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:var(--accent);font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.step{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;background:var(--accent);color:var(--bg);font-size:10px;font-weight:700}

/* URL input */
.url-row{display:flex;gap:10px}
.url-input{flex:1;background:var(--bg);border:1px solid var(--border2);border-radius:8px;color:var(--text);font-family:var(--mono);font-size:14px;padding:12px 16px;outline:none;transition:border-color .2s}
.url-input:focus{border-color:var(--accent)}
.url-input::placeholder{color:var(--text3)}
.btn{background:var(--accent);color:var(--bg);border:none;border-radius:8px;padding:12px 24px;font-size:14px;font-weight:600;cursor:pointer;transition:all .15s;font-family:var(--font);white-space:nowrap}
.btn:hover{opacity:.88;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn:disabled{background:var(--bg4);color:var(--text3);cursor:not-allowed;transform:none}
.btn-sm{padding:8px 16px;font-size:12px}
.btn-outline{background:transparent;border:1px solid var(--accent);color:var(--accent)}
.btn-outline:hover{background:var(--accent-dim)}

/* Provider badge */
.provider{display:inline-flex;align-items:center;gap:5px;font-size:11px;padding:4px 10px;border-radius:20px;font-weight:600;margin-top:10px}
.pv-deezer{background:rgba(78,205,196,.1);color:var(--accent)}
.pv-spotify{background:rgba(46,204,113,.1);color:var(--green)}
.pv-ytmusic{background:rgba(231,76,60,.1);color:var(--red)}

/* Examples */
.examples{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.example{font-size:11px;padding:5px 12px;background:var(--bg3);border:1px solid var(--border);border-radius:20px;cursor:pointer;color:var(--text2);transition:all .15s;font-family:var(--mono)}
.example:hover{border-color:var(--accent);color:var(--text)}

/* Status */
.status{font-size:13px;color:var(--text2);margin-top:12px;display:flex;align-items:center;gap:8px}
.spinner{width:16px;height:16px;border:2px solid var(--bg4);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.error{color:var(--red);font-size:13px;margin-top:10px}

/* Track list */
.track-header{display:grid;grid-template-columns:36px 1fr 1fr 50px;gap:8px;padding:8px 12px;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--text3);font-weight:600;border-bottom:1px solid var(--border)}
.track-row{display:grid;grid-template-columns:36px 1fr 1fr 50px;gap:8px;padding:10px 12px;font-size:13px;border-bottom:1px solid rgba(255,255,255,.02);transition:background .1s}
.track-row:hover{background:var(--accent-dim)}
.track-num{color:var(--text3);text-align:center;font-family:var(--mono);font-size:11px}
.track-title{color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.track-artist{color:var(--accent2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.track-dur{color:var(--text3);text-align:right;font-family:var(--mono);font-size:11px}
.track-count{text-align:center;padding:16px;color:var(--text2);font-size:13px;border-top:1px solid var(--border)}
.track-count strong{color:var(--accent)}
.track-scroll{max-height:400px;overflow-y:auto}
.track-scroll::-webkit-scrollbar{width:6px}
.track-scroll::-webkit-scrollbar-thumb{background:var(--bg4);border-radius:3px}

/* Send to N+ */
.send-row{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:16px}
.send-info{font-size:12px;color:var(--text2)}
.send-info strong{color:var(--text)}

/* Footer */
.footer{text-align:center;padding:24px;font-size:11px;color:var(--text3)}

/* Animations */
.hidden{display:none!important}
.fade-in{animation:fadeIn .3s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

/* Success banner */
.success-banner{background:rgba(46,204,113,.08);border:1px solid rgba(46,204,113,.2);border-radius:var(--radius);padding:16px 20px;display:flex;align-items:center;gap:12px;margin-top:16px}
.success-icon{width:32px;height:32px;border-radius:50%;background:var(--green);display:flex;align-items:center;justify-content:center;flex-shrink:0;color:#fff;font-weight:700;font-size:16px}
.success-text{font-size:13px;color:var(--text)}
.success-text strong{color:var(--green)}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1>Audiophile <span>Wishlist</span></h1>
    <p>Import playlists into Nicotine+ with audiophile quality filters</p>
  </div>

  <!-- STEP 1: URL Input -->
  <div class="card">
    <div class="card-title"><span class="step">1</span> Importer une playlist</div>
    <div class="url-row">
      <input type="text" class="url-input" id="urlInput" placeholder="Coller une URL Deezer, Spotify ou YouTube Music..." spellcheck="false"/>
      <button class="btn" id="btnExtract" onclick="extract()">Extraire</button>
    </div>
    <div id="providerBadge" class="hidden"></div>
    <div class="examples">
      <span style="font-size:11px;color:var(--text3);line-height:28px">Tester :</span>
      <div class="example" onclick="setUrl('https://www.deezer.com/playlist/908622995')">Deezer</div>
      <div class="example" onclick="setUrl('https://www.deezer.com/playlist/1313621735')">Deezer Jazz</div>
      <div class="example" onclick="setUrl('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M')">Spotify Hits</div>
      <div class="example" onclick="setUrl('https://music.youtube.com/playlist?list=RDCLAK5uy_kmPRjHDECIo1OFBqklhFTbTa7x-eZ0zOo')">YT Music</div>
    </div>
    <div id="statusLine" class="status hidden"></div>
    <div id="errorLine" class="error hidden"></div>
  </div>

  <!-- STEP 2: Track List -->
  <div class="card hidden" id="trackCard">
    <div class="card-title"><span class="step">2</span> <span id="trackLabel">Tracks trouvées</span></div>
    <div class="track-header"><span>#</span><span>Titre</span><span>Artiste</span><span>Durée</span></div>
    <div class="track-scroll" id="trackList"></div>
    <div class="track-count" id="trackCount"></div>
  </div>

  <!-- STEP 3: Send to N+ -->
  <div class="card hidden" id="sendCard">
    <div class="card-title"><span class="step">3</span> Envoyer à Nicotine+</div>
    <div class="send-row">
      <div class="send-info">
        <strong id="sendCount">0 tracks</strong> seront ajoutées à la wishlist.<br/>
        La recherche Soulseek démarrera automatiquement.
      </div>
      <button class="btn" id="btnSend" onclick="sendToNicotine()">Envoyer à Nicotine+</button>
    </div>
    <div id="successBanner" class="success-banner hidden fade-in">
      <div class="success-icon">✓</div>
      <div class="success-text">
        <strong>Import réussi.</strong> Ouvre Nicotine+ → la wishlist contient tes tracks.<br/>
        Le filtre qualité s'appliquera automatiquement aux résultats.
      </div>
    </div>
  </div>

  <div class="footer">
    Audiophile Wishlist — companion app · localhost:""" + str(PORT) + r"""
  </div>

</div>

<script>
let currentTracks = [];

function setUrl(u) {
  document.getElementById('urlInput').value = u;
  document.getElementById('errorLine').classList.add('hidden');
  showBadge(u);
}

function showBadge(u) {
  const el = document.getElementById('providerBadge');
  const providers = [
    [/deezer\.com\/.*playlist\/\d+/, 'Deezer — API REST publique', 'pv-deezer'],
    [/open\.spotify\.com\/playlist\//, 'Spotify — Embed parsing', 'pv-spotify'],
    [/spotify:playlist:/, 'Spotify — Embed parsing', 'pv-spotify'],
    [/youtube\.com\/playlist\?list=/, 'YouTube Music — Page parsing', 'pv-ytmusic'],
  ];
  for (const [rx, label, cls] of providers) {
    if (rx.test(u)) { el.className = 'provider fade-in ' + cls; el.textContent = label; return; }
  }
  el.classList.add('hidden');
}

document.getElementById('urlInput').addEventListener('input', function() {
  showBadge(this.value);
  document.getElementById('errorLine').classList.add('hidden');
});
document.getElementById('urlInput').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') extract();
});

async function extract() {
  const url = document.getElementById('urlInput').value.trim();
  const btn = document.getElementById('btnExtract');
  const status = document.getElementById('statusLine');
  const err = document.getElementById('errorLine');
  err.classList.add('hidden');
  document.getElementById('trackCard').classList.add('hidden');
  document.getElementById('sendCard').classList.add('hidden');
  document.getElementById('successBanner').classList.add('hidden');

  if (!url) { err.textContent = 'Colle une URL pour commencer.'; err.classList.remove('hidden'); return; }

  btn.disabled = true; btn.textContent = 'Extraction...';
  status.innerHTML = '<div class="spinner"></div> Extraction des tracks...';
  status.classList.remove('hidden');

  try {
    const resp = await fetch('/api/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await resp.json();

    if (data.error) {
      err.textContent = data.error; err.classList.remove('hidden');
    } else if (data.tracks && data.tracks.length > 0) {
      currentTracks = data.tracks;
      showTracks(data.tracks, data.provider);
    } else {
      err.textContent = 'Aucune track trouvée. La playlist est peut-être privée.';
      err.classList.remove('hidden');
    }
  } catch (e) {
    err.textContent = 'Erreur de connexion au serveur local.'; err.classList.remove('hidden');
  }

  btn.disabled = false; btn.textContent = 'Extraire';
  status.classList.add('hidden');
}

function fmtDur(s) {
  if (!s) return '';
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function showTracks(tracks, provider) {
  const names = { deezer: 'Deezer', spotify: 'Spotify', ytmusic: 'YouTube Music' };
  document.getElementById('trackLabel').textContent = tracks.length + ' tracks — ' + (names[provider] || provider);

  let html = '';
  tracks.forEach((t, i) => {
    html += '<div class="track-row">'
      + '<span class="track-num">' + (i + 1) + '</span>'
      + '<span class="track-title">' + esc(t.title) + '</span>'
      + '<span class="track-artist">' + esc(t.artist) + '</span>'
      + '<span class="track-dur">' + fmtDur(t.duration) + '</span>'
      + '</div>';
  });
  document.getElementById('trackList').innerHTML = html;
  document.getElementById('trackCount').innerHTML = '<strong>' + tracks.length + '</strong> tracks prêtes à importer';

  document.getElementById('trackCard').classList.remove('hidden');
  document.getElementById('trackCard').classList.add('fade-in');

  document.getElementById('sendCount').textContent = tracks.length + ' tracks';
  document.getElementById('sendCard').classList.remove('hidden');
  document.getElementById('sendCard').classList.add('fade-in');

  document.getElementById('trackCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function sendToNicotine() {
  const btn = document.getElementById('btnSend');
  btn.disabled = true; btn.textContent = 'Envoi...';

  try {
    const resp = await fetch('/api/send-to-nicotine', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tracks: currentTracks })
    });
    const data = await resp.json();

    if (data.success) {
      document.getElementById('successBanner').classList.remove('hidden');
      btn.textContent = 'Envoyé !';
    } else {
      btn.textContent = 'Erreur';
    }
  } catch (e) {
    btn.textContent = 'Erreur';
  }

  setTimeout(() => { btn.disabled = false; btn.textContent = 'Envoyer à Nicotine+'; }, 3000);
}
</script>
</body>
</html>"""


class RequestHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        """Suppress default HTTP logs."""
        pass

    def do_GET(self):
        """Serve HTML pages and auth callback."""
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/auth-callback":
            # OAuth redirect from browser: receive refresh token
            params = urllib.parse.parse_qs(parsed.query)
            refresh_token = params.get("refresh_token", [None])[0]
            if refresh_token:
                _auth["refresh_token"] = refresh_token
                if _refresh_access_token():
                    import html as _html
                    safe_email = _html.escape(_auth.get("email", ""))
                    self._serve_html(
                        "<h2>Authentification réussie</h2>"
                        f"<p>Connecté en tant que <strong>{safe_email}</strong></p>"
                        "<p>Tu peux fermer cet onglet.</p>"
                    )
                else:
                    self._serve_html("<h2>Erreur</h2><p>Token invalide.</p>")
            else:
                self._serve_html("<h2>Erreur</h2><p>Pas de token reçu.</p>")
            return

        if parsed.path == "/api/auth-status":
            self._json_response({
                "authenticated": bool(_auth["access_token"]),
                "email": _auth.get("email", ""),
            })
            return

        if parsed.path == "/test":
            # Serve the test providers page from disk
            test_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_providers.html")
            try:
                with open(test_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"test_providers.html not found")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode("utf-8"))

    def _serve_html(self, body_html):
        """Serve a simple HTML response page."""
        html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<style>body{font-family:system-ui;text-align:center;padding:60px;background:#FAFAFA}</style>'
            f'</head><body>{body_html}</body></html>'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        """Handle API requests."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._json_response({"error": "Invalid request"}, 400)
            return
        if content_length > 1_048_576:  # 1 MB
            self._json_response({"error": "Payload too large"}, 413)
            return
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        if self.path == "/api/extract":
            self._handle_extract(data)
        elif self.path == "/api/send-to-nicotine":
            self._handle_send(data)
        elif self.path == "/api/install-plugin":
            self._handle_install(data)
        elif self.path == "/api/install-status":
            self._json_response(_get_install_status())
        elif self.path == "/api/logout":
            _auth["access_token"] = None
            _auth["refresh_token"] = None
            _auth["email"] = None
            _auth["expires_at"] = 0
            config = _load_config()
            config.pop("refresh_token", None)
            config.pop("email", None)
            _save_config(config)
            self._json_response({"success": True})
        elif self.path == "/api/sync-now":
            _poll_force.set()
            self._json_response({"success": True, "message": "Poll triggered"})
        else:
            self._json_response({"error": "Not found"}, 404)

    def _handle_extract(self, data):
        """Extract tracks from a playlist URL."""
        url = data.get("url", "").strip()
        if not url:
            self._json_response({"error": "No URL provided"})
            return

        provider, playlist_id = _detect_provider(url)
        if provider is None:
            self._json_response({"error": "URL non reconnue. Supporte Deezer, Spotify, YouTube Music."})
            return

        extractors = {
            "deezer": _extract_deezer,
            "spotify": _extract_spotify,
            "ytmusic": _extract_ytmusic,
        }

        try:
            tracks = extractors[provider](playlist_id)
        except Exception as e:
            self._json_response({"error": f"Erreur d'extraction: {e}"})
            return

        self._json_response({"provider": provider, "tracks": tracks})

    def _handle_install(self, _data):
        """Install or update the plugin."""
        success, message = _install_plugin()
        self._json_response({"success": success, "message": message})

    def _handle_send(self, data):
        """Write tracks to a JSON file that the N+ plugin will read."""
        tracks = data.get("tracks", [])
        if not tracks:
            self._json_response({"error": "No tracks"})
            return

        import_path = _get_import_file_path()
        plugin_dir = os.path.dirname(import_path)

        try:
            os.makedirs(plugin_dir, exist_ok=True)

            payload = {
                "timestamp": time.time(),
                "tracks": tracks,
            }

            with open(import_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            print(f"  Wrote {len(tracks)} tracks to {import_path}")
            self._json_response({"success": True, "count": len(tracks), "path": import_path})

        except Exception as e:
            self._json_response({"error": f"Failed to write file: {e}"})

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print()
    print("  ╔═══════════════════════════════════════╗")
    print("  ║        SeekWish — Companion App        ║")
    print("  ╚═══════════════════════════════════════╝")
    print()

    # Auto-install/update plugin
    success, msg = _install_plugin()
    if success:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")

    # Init auth from saved config
    _init_auth_from_config()

    print()
    print(f"  → http://localhost:{PORT}")
    print(f"  → http://localhost:{PORT}/test  (test providers)")
    if _auth["access_token"]:
        print(f"  → Polling enabled (every {POLL_INTERVAL}s)")
    else:
        print(f"  → Auth: open {SEEKWISH_URL} and login, then visit")
        print(f"    {SEEKWISH_URL}/auth/companion to link this companion")
    print()
    print("  Ctrl+C to stop")
    print()

    # Start polling daemon thread
    threading.Thread(target=_polling_loop, daemon=True).start()

    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("127.0.0.1", PORT), RequestHandler)

    # Open browser after a short delay
    def open_browser():
        time.sleep(0.5)
        webbrowser.open(f"http://localhost:{PORT}")

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
