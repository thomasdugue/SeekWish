# SPDX-License-Identifier: GPL-3.0-or-later
#
# Audiophile Wishlist — Nicotine+ Plugin
# Enhanced wishlist with lossless quality filtering and playlist import.
#
# Providers: Deezer (public API), Spotify (embed parse), YouTube Music (page parse)
# All using Python stdlib only (urllib, json, re) — no external dependencies.

import json
import os
import re
import time
import urllib.request
import urllib.error
import urllib.parse

from threading import Thread

from pynicotine.pluginsystem import BasePlugin


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

LOSSLESS_EXTENSIONS = {"flac", "wav", "alac", "ape", "wv", "aiff", "aif", "dsf", "dff"}

# FileAttribute indices (from pynicotine.slskmessages)
ATTR_BITRATE = 0
ATTR_LENGTH = 1
ATTR_VBR = 2
ATTR_SAMPLE_RATE = 4
ATTR_BIT_DEPTH = 5

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────────────────────────────────────

def _fetch(url, headers=None, timeout=15):
    """Fetch URL content using stdlib urllib. Returns string or None on error."""

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
    """Detect playlist provider and extract ID from URL.

    Returns:
        (provider_name, playlist_id) or (None, None) if unrecognized.
    """

    url = url.strip()

    # Deezer: https://www.deezer.com/playlist/908622995
    #         https://deezer.com/fr/playlist/908622995
    #         https://deezer.page.link/xxxxx (short link, not handled yet)
    m = re.search(r"deezer\.com/(?:\w+/)?playlist/(\d+)", url)
    if m:
        return "deezer", m.group(1)

    # Spotify: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
    #          spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
    m = re.search(r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)", url)
    if m:
        return "spotify", m.group(1)

    m = re.search(r"spotify:playlist:([a-zA-Z0-9]+)", url)
    if m:
        return "spotify", m.group(1)

    # YouTube Music: https://music.youtube.com/playlist?list=PLxxxxxxx
    #                https://www.youtube.com/playlist?list=PLxxxxxxx
    m = re.search(r"(?:music\.)?youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)", url)
    if m:
        return "ytmusic", m.group(1)

    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Provider: Deezer (public REST API — most reliable)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_deezer(playlist_id):
    """Fetch tracks from a public Deezer playlist.

    Uses the public Deezer API (no auth required).
    Returns list of {"artist": str, "title": str} dicts.
    """

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

            if artist and title:
                tracks.append({"artist": artist, "title": title})

        # Pagination
        url = data.get("next")

    return tracks


# ─────────────────────────────────────────────────────────────────────────────
# Provider: Spotify (anonymous token + official API)
# ─────────────────────────────────────────────────────────────────────────────

def _spotify_get_anonymous_token(playlist_id):
    """Get an anonymous access token from Spotify's embed page."""

    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    html = _fetch(embed_url)
    if html:
        m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1)

    token_url = "https://open.spotify.com/get_access_token?reason=transport&productType=embed"
    raw = _fetch(token_url, headers={"User-Agent": USER_AGENT})
    if raw:
        try:
            data = json.loads(raw)
            token = data.get("accessToken")
            if token:
                return token
        except json.JSONDecodeError:
            pass

    return None


def _extract_spotify(playlist_id):
    """Fetch tracks from a public Spotify playlist.

    Gets an anonymous token from the embed page, then calls the official API.
    Returns list of {"artist": str, "title": str} dicts.
    """

    token = _spotify_get_anonymous_token(playlist_id)
    if not token:
        return []

    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100&fields=items(track(name,artists(name),album(name),duration_ms)),next"
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}

    while url and len(tracks) < 500:
        raw = _fetch(url, headers=headers)
        if raw is None:
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            break
        if "error" in data:
            break

        for item in data.get("items", []):
            track = item.get("track")
            if not track:
                continue
            title = track.get("name", "")
            artists = track.get("artists", [])
            artist_names = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            if artist_names and title:
                tracks.append({"artist": artist_names, "title": title})

        url = data.get("next")

    return tracks


# ─────────────────────────────────────────────────────────────────────────────
# Provider: YouTube Music (ytInitialData parsing — fragile)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_ytmusic(playlist_id):
    """Fetch tracks from a public YouTube Music playlist.

    Parses the ytInitialData JSON variable embedded in the page HTML.
    ⚠ FRAGILE: This may break if Google changes their page structure.

    Returns list of {"artist": str, "title": str} dicts.
    """

    url = f"https://music.youtube.com/playlist?list={playlist_id}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    html = _fetch(url, headers=headers)

    if html is None:
        # Fallback: try regular YouTube
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        html = _fetch(url, headers=headers)

    if html is None:
        return []

    tracks = []

    # Extract ytInitialData
    m = re.search(r"var\s+ytInitialData\s*=\s*({.*?});\s*</script>", html, re.DOTALL)
    if not m:
        m = re.search(r'window\["ytInitialData"\]\s*=\s*({.*?});\s*', html, re.DOTALL)
    if not m:
        # Try another common pattern
        m = re.search(r"ytInitialData\s*=\s*'({.*?})'", html, re.DOTALL)

    if not m:
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    # Navigate the deeply nested YT Music structure to find track info
    _ytmusic_find_tracks(data, tracks)

    return tracks


def _ytmusic_find_tracks(data, results, depth=0):
    """Recursively search YouTube Music JSON for track information."""

    if depth > 20:
        return

    if isinstance(data, dict):
        # YouTube Music playlist items have musicResponsiveListItemRenderer
        if "musicResponsiveListItemRenderer" in data:
            renderer = data["musicResponsiveListItemRenderer"]
            title, artist = _ytmusic_parse_renderer(renderer)
            if title and artist:
                results.append({"artist": artist, "title": title})
                return

        # Regular YouTube playlist items have playlistVideoRenderer
        if "playlistVideoRenderer" in data:
            renderer = data["playlistVideoRenderer"]
            title_obj = renderer.get("title", {})
            title_text = ""
            for run in title_obj.get("runs", []):
                title_text += run.get("text", "")

            # YouTube titles are typically "Artist - Title"
            if " - " in title_text:
                parts = title_text.split(" - ", 1)
                results.append({"artist": parts[0].strip(), "title": parts[1].strip()})
            elif title_text:
                results.append({"artist": "", "title": title_text.strip()})
            return

        for value in data.values():
            _ytmusic_find_tracks(value, results, depth + 1)

    elif isinstance(data, list):
        for item in data:
            _ytmusic_find_tracks(item, results, depth + 1)


def _ytmusic_parse_renderer(renderer):
    """Extract artist and title from a YT Music list item renderer."""

    title = ""
    artist = ""

    flex_columns = renderer.get("flexColumns", [])
    if not flex_columns:
        return title, artist

    # First column is usually the track title
    if len(flex_columns) > 0:
        col = flex_columns[0]
        text_obj = (
            col.get("musicResponsiveListItemFlexColumnRenderer", {})
               .get("text", {})
        )
        for run in text_obj.get("runs", []):
            title += run.get("text", "")

    # Second column is usually the artist
    if len(flex_columns) > 1:
        col = flex_columns[1]
        text_obj = (
            col.get("musicResponsiveListItemFlexColumnRenderer", {})
               .get("text", {})
        )
        parts = []
        for run in text_obj.get("runs", []):
            text = run.get("text", "")
            if text and text not in (" & ", " • ", ", ", " · "):
                parts.append(text)
            elif text in (" & ", ", "):
                parts.append(text)
        artist = "".join(parts).strip()
        # Remove trailing type indicators like " • Album" or " · 2023"
        artist = re.split(r"\s*[•·]\s*", artist)[0].strip()

    return title.strip(), artist.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Quality filter
# ─────────────────────────────────────────────────────────────────────────────

def _get_extension(filepath):
    """Extract lowercase extension from a file path (without dot)."""
    _, ext = os.path.splitext(filepath.replace("\\", "/"))
    return ext.lstrip(".").lower()


def _check_quality(filepath, file_size, file_attrs, settings):
    """Check if a file meets the audiophile quality criteria.

    Args:
        filepath: Virtual file path from search result
        file_size: File size in bytes
        file_attrs: Dict of {FileAttribute_id: value}
        settings: Plugin settings dict

    Returns:
        (passes: bool, reason: str)
    """

    # Check format
    ext = _get_extension(filepath)
    allowed = {f.strip().lower() for f in settings["allowed_formats"].split()}

    if ext not in allowed:
        return False, f"format '{ext}' not in allowed list"

    # Check file size (filter out tiny files that are likely bad rips)
    min_size_bytes = settings["min_file_size_mb"] * 1024 * 1024
    if min_size_bytes > 0 and file_size < min_size_bytes:
        size_mb = file_size / (1024 * 1024)
        return False, f"file too small ({size_mb:.1f} MB < {settings['min_file_size_mb']} MB)"

    if not file_attrs:
        # No attributes available — accept if format and size are OK
        return True, "no attributes to verify (format and size OK)"

    # Check bitrate (lossless files typically report 800-1500+ kbps)
    bitrate = file_attrs.get(ATTR_BITRATE, 0)
    if settings["min_bitrate"] > 0 and bitrate > 0 and bitrate < settings["min_bitrate"]:
        return False, f"bitrate too low ({bitrate} < {settings['min_bitrate']} kbps)"

    # Check sample rate
    sample_rate = file_attrs.get(ATTR_SAMPLE_RATE, 0)
    if settings["min_sample_rate"] > 0 and sample_rate > 0 and sample_rate < settings["min_sample_rate"]:
        return False, f"sample rate too low ({sample_rate} < {settings['min_sample_rate']} Hz)"

    # Check bit depth
    bit_depth = file_attrs.get(ATTR_BIT_DEPTH, 0)
    if settings["min_bit_depth"] > 0 and bit_depth > 0 and bit_depth < settings["min_bit_depth"]:
        return False, f"bit depth too low ({bit_depth} < {settings['min_bit_depth']} bit)"

    return True, "quality OK"


def _score_result(filepath, file_size, file_attrs):
    """Score a search result for ranking. Higher = better quality."""

    score = 0
    ext = _get_extension(filepath)

    # Prefer lossless formats
    if ext in LOSSLESS_EXTENSIONS:
        score += 10000

    # Prefer higher bit depth
    bit_depth = file_attrs.get(ATTR_BIT_DEPTH, 0) if file_attrs else 0
    score += bit_depth * 100

    # Prefer higher sample rate
    sample_rate = file_attrs.get(ATTR_SAMPLE_RATE, 0) if file_attrs else 0
    score += sample_rate // 100

    # Prefer larger files (usually better quality)
    score += file_size // (1024 * 1024)

    return score


# ─────────────────────────────────────────────────────────────────────────────
# Plugin class
# ─────────────────────────────────────────────────────────────────────────────

class Plugin(BasePlugin):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.settings = {
            "allowed_formats": "flac wav alac ape wv aiff",
            "min_bitrate": 800,
            "min_sample_rate": 44100,
            "min_bit_depth": 16,
            "min_file_size_mb": 5,
            "download_mode": "semi",
            "enable_quality_filter": True,
        }

        self.metasettings = {
            "allowed_formats": {
                "description": (
                    "Allowed audio formats (space-separated, lowercase).\n"
                    "Common lossless: flac wav alac ape wv aiff dsf dff"
                ),
                "type": "textview",
            },
            "min_bitrate": {
                "description": (
                    "Minimum bitrate in kbps (0 to disable).\n"
                    "Lossless files typically report 800–1500+ kbps."
                ),
                "type": "int", "minimum": 0, "maximum": 10000, "stepsize": 100,
            },
            "min_sample_rate": {
                "description": (
                    "Minimum sample rate in Hz (0 to disable).\n"
                    "CD quality = 44100, Hi-Res = 96000+."
                ),
                "type": "int", "minimum": 0, "maximum": 384000, "stepsize": 100,
            },
            "min_bit_depth": {
                "description": (
                    "Minimum bit depth (0 to disable).\n"
                    "CD quality = 16, Hi-Res = 24."
                ),
                "type": "int", "minimum": 0, "maximum": 64, "stepsize": 1,
            },
            "min_file_size_mb": {
                "description": (
                    "Minimum file size in MB (0 to disable).\n"
                    "Helps filter out bad rips. A 4-min FLAC is typically 20-40 MB."
                ),
                "type": "int", "minimum": 0, "maximum": 500, "stepsize": 1,
            },
            "download_mode": {
                "description": "Download mode for quality-matched results.",
                "type": "dropdown",
                "options": ("semi", "auto"),
            },
            "enable_quality_filter": {
                "description": "Enable automatic quality filtering on wishlist search results.",
                "type": "bool",
            },
        }

        # Commands (N+ 3.3+ flat command system)
        self.commands = {
            "aw-import": {
                "callback": self._cmd_import,
                "description": "Import tracks from a public playlist URL (Deezer, Spotify, YouTube Music)",
                "aliases": ["aw-i"],
                "disable": ["cli"],
                "parameters": ["<url>"],
            },
            "aw-status": {
                "callback": self._cmd_status,
                "description": "Show current quality filter settings and statistics",
                "aliases": [],
                "disable": ["cli"],
            },
            "aw-reset-stats": {
                "callback": self._cmd_reset_stats,
                "description": "Reset download and filter statistics",
                "aliases": [],
                "disable": ["cli"],
            },
        }

        # Internal state
        self._managed_wishes = set()   # Wish terms created by the plugin
        self._fulfilled_wishes = set()  # Wish terms already downloaded (one download per wish)
        self._pending = {}  # wish_term -> {"best": (user, path, size, attrs), "score": int, "first_seen": float}
        self._stats = {
            "filtered_out": 0,
            "quality_matched": 0,
            "downloaded": 0,
            "playlists_imported": 0,
            "tracks_imported": 0,
        }
        self._event_connected = False

        # How long to collect results before picking the best (seconds)
        self._collect_window = 30

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _get_import_file_path(self):
        """Path to the JSON file written by the companion app."""
        return os.path.join(self.path, "pending_import.json")

    def loaded_notification(self):
        self.log("Audiophile Wishlist loaded. Quality filter: %s, mode: %s.",
                 ("ON" if self.settings["enable_quality_filter"] else "OFF",
                  self.settings["download_mode"]))

        # Check for pending imports from companion app on load
        self._check_pending_import()

    def server_connect_notification(self):
        """Connect to search response event when we're online."""

        if self._event_connected:
            return

        try:
            from pynicotine.events import events
            events.connect("file-search-response", self._on_search_response)
            self._event_connected = True
            self.log("Quality filter hooked into search responses.")
        except Exception as e:
            self.log("Warning: Could not hook search responses: %s", (str(e),))

        # Check for pending imports from companion app
        self._check_pending_import()

        # Schedule periodic checks (every 10 seconds)
        self._start_import_watcher()

    def server_disconnect_notification(self, userchoice):
        """Disconnect from search response event when offline."""
        self._disconnect_event()

    def disable(self):
        """Clean up when plugin is disabled."""
        self._disconnect_event()

    def _disconnect_event(self):
        if not self._event_connected:
            return

        try:
            from pynicotine.events import events
            events.disconnect("file-search-response", self._on_search_response)
            self._event_connected = False
        except Exception:
            pass

    def _start_import_watcher(self):
        """Start a background thread that checks for companion app imports."""

        def watcher():
            while self._event_connected:
                try:
                    self._check_pending_import()
                except Exception:
                    pass
                time.sleep(10)

        thread = Thread(target=watcher, daemon=True)
        thread.start()

    def _check_pending_import(self):
        """Check if the companion app has written a pending_import.json file."""

        import_path = self._get_import_file_path()

        if not os.path.exists(import_path):
            return

        try:
            with open(import_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        tracks = data.get("tracks", [])
        if not tracks:
            os.remove(import_path)
            return

        self.log("Companion app sent %s tracks. Importing...", (len(tracks),))

        # Remove the file immediately to avoid re-importing
        try:
            os.remove(import_path)
        except OSError:
            pass

        # Import the tracks as wishes (reuse existing logic)
        added = 0
        skipped = 0

        for track in tracks:
            artist = track.get("artist", "").strip()
            title = track.get("title", "").strip()
            if not title:
                continue

            wish_term = f"{artist} {title}" if artist else title
            wish_term = re.sub(r"[(\[\{].*?[)\]\}]", "", wish_term)
            wish_term = re.sub(r"\s+", " ", wish_term).strip()
            if not wish_term:
                continue

            try:
                if self.core.search.is_wish(wish_term):
                    skipped += 1
                    continue
            except Exception:
                pass

            try:
                self.core.search.add_wish(wish_term)
                self._managed_wishes.add(wish_term)
                added += 1
            except Exception as e:
                self.log("Error adding wish '%s': %s", (wish_term, str(e)))

        self._stats["playlists_imported"] += 1
        self._stats["tracks_imported"] += added

        self.log(
            "Import complete: %s wishes added, %s already existed.",
            (added, skipped),
        )

    # ── Commands ──────────────────────────────────────────────────────────

    def _cmd_import(self, args, **_unused):
        """Handle /aw-import <url> command."""

        if not args:
            self.output("Usage: /aw-import <playlist URL>")
            self.output("Supported: Deezer, Spotify, YouTube Music public playlists")
            return

        url = args.strip()
        provider, playlist_id = _detect_provider(url)

        if provider is None:
            self.output(
                "Unrecognized playlist URL. Supported formats:\n"
                "  • https://www.deezer.com/playlist/123456\n"
                "  • https://open.spotify.com/playlist/abc123\n"
                "  • https://music.youtube.com/playlist?list=PLxxx"
            )
            return

        self.output(f"Importing from {provider} (playlist {playlist_id})...")

        # Run extraction in a background thread to avoid freezing N+
        thread = Thread(
            target=self._import_playlist,
            args=(provider, playlist_id),
            daemon=True,
        )
        thread.start()

    def _import_playlist(self, provider, playlist_id):
        """Background thread: extract tracks and create wishes."""

        extractors = {
            "deezer": _extract_deezer,
            "spotify": _extract_spotify,
            "ytmusic": _extract_ytmusic,
        }

        extractor = extractors.get(provider)
        if extractor is None:
            self.log("Unknown provider: %s", (provider,))
            return

        try:
            tracks = extractor(playlist_id)
        except Exception as e:
            self.log("Error extracting from %s: %s", (provider, str(e)))
            return

        if not tracks:
            self.log("No tracks found. The playlist may be private or the URL may be invalid.")
            return

        self.log("Found %s tracks from %s. Creating wishes...", (len(tracks), provider))

        added = 0
        skipped = 0

        for track in tracks:
            artist = track.get("artist", "").strip()
            title = track.get("title", "").strip()

            if not title:
                continue

            # Build search term: "artist title" for best Soulseek results
            if artist:
                wish_term = f"{artist} {title}"
            else:
                wish_term = title

            # Clean up the wish term (remove special chars that hurt search)
            wish_term = re.sub(r"[(\[\{].*?[)\]\}]", "", wish_term)  # Remove (feat. X), [Remix], etc.
            wish_term = re.sub(r"\s+", " ", wish_term).strip()

            if not wish_term:
                continue

            # Check if already a wish in N+
            try:
                if self.core.search.is_wish(wish_term):
                    skipped += 1
                    continue
            except Exception:
                pass

            try:
                self.core.search.add_wish(wish_term)
                self._managed_wishes.add(wish_term)
                added += 1
            except Exception as e:
                self.log("Error adding wish '%s': %s", (wish_term, str(e)))

        self._stats["playlists_imported"] += 1
        self._stats["tracks_imported"] += added

        self.log(
            "Import complete: %s wishes added, %s already existed. "
            "Wishlist will search automatically.",
            (added, skipped),
        )

    def _cmd_status(self, _args, **_unused):
        """Handle /aw-status command."""

        lines = [
            "── Audiophile Wishlist Status ──",
            f"Quality filter: {'ON' if self.settings['enable_quality_filter'] else 'OFF'}",
            f"Download mode: {self.settings['download_mode']}",
            f"Formats: {self.settings['allowed_formats']}",
            f"Min bitrate: {self.settings['min_bitrate']} kbps",
            f"Min sample rate: {self.settings['min_sample_rate']} Hz",
            f"Min bit depth: {self.settings['min_bit_depth']} bit",
            f"Min file size: {self.settings['min_file_size_mb']} MB",
            f"Event hook: {'connected' if self._event_connected else 'disconnected'}",
            "",
            f"Playlists imported: {self._stats['playlists_imported']}",
            f"Tracks imported: {self._stats['tracks_imported']}",
            f"Quality matched: {self._stats['quality_matched']}",
            f"Filtered out: {self._stats['filtered_out']}",
            f"Auto-downloaded: {self._stats['downloaded']}",
            f"Managed wishes: {len(self._managed_wishes)}",
            f"Fulfilled wishes: {len(self._fulfilled_wishes)}",
            f"Pending (collecting): {len(self._pending)}",
            f"Collect window: {self._collect_window}s",
        ]

        for line in lines:
            self.output(line)

    def _cmd_reset_stats(self, _args, **_unused):
        """Handle /aw-reset-stats command."""

        self._stats = {
            "filtered_out": 0,
            "quality_matched": 0,
            "downloaded": 0,
            "playlists_imported": 0,
            "tracks_imported": 0,
        }
        self._fulfilled_wishes.clear()
        self._pending.clear()
        self.output("Statistics reset.")

    # ── Search response hook ─────────────────────────────────────────────

    def _on_search_response(self, msg):
        """Called for every file search response. Collects candidates
        over a time window, then downloads the single best match per wish.
        """

        if not self.settings["enable_quality_filter"]:
            return

        # Skip rejected responses (N+ sets token to None for filtered results)
        if msg.token is None or not msg.list:
            return

        try:
            search = self.core.search.searches.get(msg.token)
        except Exception:
            return

        if search is None:
            return

        if getattr(search, "mode", None) != "wishlist":
            return

        wish_term = getattr(search, "term", None)
        if not wish_term:
            return

        # Already downloaded for this wish — skip
        if wish_term in self._fulfilled_wishes:
            return

        # msg.username = peer sharing files (set by PeerMessage base class)
        username = getattr(msg, "username", None)
        if not username:
            return

        # Peer upload speed (bytes/sec) from the search response
        peer_speed = getattr(msg, "ulspeed", 0) or 0
        has_free_slots = getattr(msg, "freeulslots", False)

        # Find best quality match in this response
        for file_list in (msg.list, getattr(msg, "privatelist", None) or []):
            for fileinfo in file_list:
                if len(fileinfo) < 5:
                    continue

                _code, filepath, file_size, _ext, file_attrs = fileinfo

                passes, _reason = _check_quality(
                    filepath, file_size, file_attrs or {}, self.settings)

                if not passes:
                    self._stats["filtered_out"] += 1
                    continue

                self._stats["quality_matched"] += 1

                # Score: quality + peer speed bonus
                score = _score_result(filepath, file_size, file_attrs or {})
                # Peer speed bonus (normalized: 10 MB/s = +5000 points)
                score += min(peer_speed, 50_000_000) // 10_000
                # Free slot bonus
                if has_free_slots:
                    score += 2000

                # Update pending candidate if this is better
                current = self._pending.get(wish_term)
                if current is None:
                    self._pending[wish_term] = {
                        "best": (username, filepath, file_size, file_attrs),
                        "score": score,
                        "first_seen": time.time(),
                    }
                    self.log("Candidate for '%s': %s from %s (score=%s, speed=%s KB/s)",
                             (wish_term, os.path.basename(filepath.replace("\\", "/")),
                              username, score, peer_speed // 1024))
                elif score > current["score"]:
                    self._pending[wish_term] = {
                        "best": (username, filepath, file_size, file_attrs),
                        "score": score,
                        "first_seen": current["first_seen"],
                    }
                    self.log("Better candidate for '%s': %s from %s (score=%s, speed=%s KB/s)",
                             (wish_term, os.path.basename(filepath.replace("\\", "/")),
                              username, score, peer_speed // 1024))

        # Check if the collect window has elapsed for any pending wishes
        self._flush_pending()

    def _flush_pending(self):
        """Download the best candidate for wishes whose collect window has elapsed."""

        now = time.time()
        to_remove = []

        for wish_term, entry in self._pending.items():
            if wish_term in self._fulfilled_wishes:
                to_remove.append(wish_term)
                continue

            elapsed = now - entry["first_seen"]
            if elapsed < self._collect_window:
                continue

            # Time's up — download the best candidate
            b_user, b_path, b_size, b_attrs = entry["best"]
            ext = _get_extension(b_path)
            sr = b_attrs.get(ATTR_SAMPLE_RATE, "?") if b_attrs else "?"
            bd = b_attrs.get(ATTR_BIT_DEPTH, "?") if b_attrs else "?"
            br = b_attrs.get(ATTR_BITRATE, "?") if b_attrs else "?"

            try:
                self.core.downloads.enqueue_download(
                    b_user, b_path, size=b_size, file_attributes=b_attrs,
                )
                self._fulfilled_wishes.add(wish_term)
                self._stats["downloaded"] += 1
                self.log(
                    "Downloading: %s from %s [%s %s/%sbit %skbps] (score=%s)",
                    (os.path.basename(b_path.replace("\\", "/")),
                     b_user, ext.upper(), sr, bd, br, entry["score"]),
                )
                # Remove from wishlist now that we have a download
                try:
                    self.core.search.remove_wish(wish_term)
                    self._managed_wishes.discard(wish_term)
                except Exception:
                    pass
            except Exception as e:
                self.log("Error enqueuing download: %s", (str(e),))

            to_remove.append(wish_term)

        for wish_term in to_remove:
            self._pending.pop(wish_term, None)
