"""Shared playlist extraction logic — reused by extract.py, playlists.py, sync.py."""

import json
import re
import time
import urllib.error
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch(url, headers=None, timeout=15, retries=2):
    if headers is None:
        headers = {}
    if "User-Agent" not in headers:
        headers["User-Agent"] = USER_AGENT
    for attempt in range(1 + retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = int(e.headers.get("Retry-After", 2 * (attempt + 1)))
                time.sleep(min(wait, 10))
                continue
            return None
        except Exception:
            return None


def detect_provider(url):
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


def normalize(text):
    """Normalize text for deduplication: lowercase, strip parentheses/brackets, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", text)  # remove (feat. X), [Remix], etc.
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Deezer ──

def extract_deezer(playlist_id):
    tracks = []
    url = f"https://api.deezer.com/playlist/{playlist_id}/tracks?limit=100"
    name = None
    # Fetch playlist name
    info_raw = fetch(f"https://api.deezer.com/playlist/{playlist_id}")
    if info_raw:
        try:
            info = json.loads(info_raw)
            name = info.get("title")
        except json.JSONDecodeError:
            pass
    max_pages = 50
    page = 0
    while url and page < max_pages:
        # SSRF guard: only follow Deezer API URLs
        if not url.startswith("https://api.deezer.com/"):
            break
        raw = fetch(url)
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
        page += 1
    return tracks, name


# ── Spotify ──

def _spotify_get_token():
    """Get an anonymous access token from Spotify's embed page."""
    html = fetch("https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M", timeout=15)
    if html:
        m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1)
    return None


def extract_spotify(playlist_id):
    # Try API with embed token (supports pagination, all tracks)
    token = _spotify_get_token()
    if token:
        tracks, name = _extract_spotify_api(playlist_id, token)
        if tracks:
            return tracks, name

    # Fallback: embed page parsing (limited to ~100 tracks)
    return _extract_spotify_embed(playlist_id)


def _extract_spotify_api(playlist_id, token):
    """Extract via Spotify Web API with embed token — supports full pagination."""
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }

    # Single call to get name + first page of tracks (saves a rate-limited request)
    tracks = []
    name = None
    first_url = (
        f"https://api.spotify.com/v1/playlists/{playlist_id}"
        f"?fields=name,tracks(next,items(track(name,duration_ms,album(name),artists(name))))"
    )
    raw = fetch(first_url, headers=headers, timeout=15, retries=3)
    if not raw:
        return [], None
    try:
        playlist_data = json.loads(raw)
    except json.JSONDecodeError:
        return [], None
    if "error" in playlist_data:
        return [], None

    name = playlist_data.get("name")
    tracks_obj = playlist_data.get("tracks", {})

    for item in tracks_obj.get("items", []):
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

    # Paginate remaining tracks
    url = tracks_obj.get("next")
    max_pages = 50
    page = 1

    while url and page < max_pages:
        # SSRF guard
        if not url.startswith("https://api.spotify.com/"):
            break
        time.sleep(0.5)  # avoid 429 between pages
        raw = fetch(url, headers=headers, timeout=15, retries=3)
        if not raw:
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
            artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            duration = round(track.get("duration_ms", 0) / 1000)
            album = track.get("album", {}).get("name", "")
            if artist and title:
                tracks.append({"artist": artist, "title": title, "duration": duration, "album": album})

        url = data.get("next")
        page += 1

    return tracks, name


def _extract_spotify_embed(playlist_id):
    """Fallback: parse the embed page (limited to ~100 tracks)."""
    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    html = fetch(embed_url, timeout=25)
    if not html:
        raise Exception("Impossible de contacter Spotify.")

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        raise Exception("Spotify a changé sa page.")

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return [], None

    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
    name = entity.get("name")
    track_list = entity.get("trackList", [])

    tracks = []
    for item in track_list:
        title = item.get("title", "")
        artist = item.get("subtitle", "").replace("\xa0", " ")
        duration = round(item.get("duration", 0) / 1000)
        if artist and title:
            tracks.append({"artist": artist, "title": title, "duration": duration, "album": ""})
    return tracks, name


# ── YouTube Music ──

def extract_ytmusic(playlist_id):
    url = f"https://music.youtube.com/playlist?list={playlist_id}"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    html = fetch(url, headers=headers)
    if html is None:
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        html = fetch(url, headers=headers)
    if html is None:
        return [], None

    m = re.search(r"var\s+ytInitialData\s*=\s*({.*?});\s*</script>", html, re.DOTALL)
    if not m:
        m = re.search(r'window\["ytInitialData"\]\s*=\s*({.*?});\s*', html, re.DOTALL)
    if not m:
        return [], None

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return [], None

    # Try to extract playlist name
    name = None
    try:
        header = data.get("header", {}).get("musicImmersiveHeaderRenderer", {})
        if not header:
            header = data.get("header", {}).get("playlistHeaderRenderer", {})
        for run in header.get("title", {}).get("runs", []):
            name = run.get("text", "")
            if name:
                break
    except Exception:
        pass

    tracks = []
    _ytmusic_find_tracks(data, tracks)
    return tracks, name


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


EXTRACTORS = {
    "deezer": extract_deezer,
    "spotify": extract_spotify,
    "ytmusic": extract_ytmusic,
}
