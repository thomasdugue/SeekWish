# CLAUDE.md — SeekWish

## What is this project?

A Nicotine+ plugin + companion web app for audiophiles who want to:
1. Import tracks from public playlists (Deezer, Spotify, YouTube Music) into the Nicotine+ wishlist
2. Automatically filter search results by audio quality (format, bitrate, sample rate, bit depth, file size)
3. Auto-download or queue for review the best quality matches

Nicotine+ is a Python/GTK client for the Soulseek peer-to-peer music sharing network.

## Architecture

Two components that communicate via a JSON file:

### 1. Plugin (`audiophile_wishlist/`)
- Lives inside Nicotine+ at `~/.local/share/nicotine/plugins/audiophile_wishlist/`
- Hooks into N+ via `events.connect("file-search-response")` for quality filtering
- Reads `pending_import.json` (written by companion) every 10s to import tracks
- Also supports chat commands: `/aw-import`, `/aw-status`, `/aw-reset-stats`
- **Constraint: stdlib Python ONLY** — N+ cannot load external packages

### 2. Companion app (`companion.py`)
- Standalone Python HTTP server (stdlib only) on localhost:8484
- Opens a web UI in the browser for playlist import
- Extracts tracks from Deezer (public REST API), Spotify (anonymous token + official API), YouTube Music (ytInitialData parsing)
- Writes `pending_import.json` that the plugin picks up

### Communication flow
```
User pastes URL in companion web UI
  → companion.py extracts tracks via provider API
  → writes pending_import.json to plugin directory
  → N+ plugin detects file, creates wishes via core.search.add_wish()
  → N+ wishlist searches Soulseek periodically
  → Plugin intercepts results, applies quality filter
  → Auto-downloads or queues paused (configurable)
```

## Provider status

| Provider | Method | Reliability | Notes |
|----------|--------|-------------|-------|
| Deezer | Public REST API `api.deezer.com/playlist/{id}/tracks` | ⭐⭐⭐ Stable | No auth needed, JSON response, pagination supported |
| Spotify | Anonymous token from embed page → official API | ⭐⭐ Medium | Token extracted via regex from embed HTML, may break |
| YouTube Music | Parse `ytInitialData` from page HTML | ⭐ Fragile | Google changes structure frequently |

## Key technical decisions

- **Single __init__.py for plugin**: N+ has import limitations for plugin sub-modules. One file avoids path injection issues.
- **Commands use flat dict format**: N+ 3.3+ expects `self.commands = {"command-name": {"callback": ..., ...}}` — NOT nested by interface type.
- **Callback signature**: `def callback(self, args, **_unused)` where `args` is the raw string after the command. Use `self.output()` for responses (not `self.echo_message()`).
- **Quality scoring**: When multiple results pass the filter, the plugin picks the highest score (lossless format bonus + bit depth + sample rate + file size).
- **File watcher**: Plugin checks for `pending_import.json` every 10 seconds in a daemon thread. Deletes file after reading to prevent re-import.

## N+ Plugin API reference (used in this plugin)

```python
# Available via self.core:
self.core.search.add_wish(term)              # Add to wishlist
self.core.search.is_wish(term)               # Check if already exists
self.core.downloads.enqueue_download(user, path, size=0, file_attributes=None, paused=False)

# Event system:
from pynicotine.events import events
events.connect("file-search-response", handler)   # Intercept search results
events.disconnect("file-search-response", handler)

# File attributes in search results:
# fileinfo = (code, filepath, size, ext, attrs_dict)
# attrs_dict keys: 0=BITRATE, 1=LENGTH, 2=VBR, 4=SAMPLE_RATE, 5=BIT_DEPTH

# Output:
self.output(text)   # Display in chat (command context)
self.log(msg, args) # Log to N+ console
```

## File structure

```
audiophile_wishlist/    # The N+ plugin (copy to ~/.local/share/nicotine/plugins/)
  __init__.py           # Plugin code (~900 lines)
  PLUGININFO            # N+ plugin metadata
companion.py            # Standalone web server + UI (~660 lines)
tests/                  # Unit tests (pytest)
  test_plugin.py        # 26 tests for URL detection, quality filter, scoring
docs/
  wireframes.html       # Interactive wireframes of the UX
.vscode/                # VS Code config (settings, tasks, launch, extensions)
pyproject.toml          # Ruff linting, pytest config
Makefile                # make install, make test, make lint (cross-platform)
```

## What works now

- ✅ Deezer playlist import (live, tested, stable)
- ✅ Plugin loads in N+ 3.3.x, commands register correctly
- ✅ Quality filter logic (all unit tests pass)
- ✅ Companion web UI (dark theme, URL detection, track list display)
- ✅ Plugin ↔ companion communication via JSON file
- ✅ Cross-platform install (macOS/Linux/Windows)

## What needs work

- ⚠️ Spotify provider: anonymous token approach coded but not yet tested live
- ⚠️ YouTube Music provider: ytInitialData parsing coded but not tested live
- ⚠️ Quality filter auto-download: hook is connected but waiting for wishlist search results (Soulseek throttles searches to ~1 every 12-15 min)
- 🔲 Installation UX: currently manual `cp` commands, should be simpler
- 🔲 No way to deselect individual tracks before sending to N+
- 🔲 No progress indicator for wishlist search status
- 🔲 Companion app has no persistence (close = lose state)

## Dev commands

```bash
make install     # Copy plugin to N+ plugins folder (auto-detects OS)
make test        # Run pytest
make lint        # Run ruff
make format      # Auto-format
python3 companion.py  # Launch companion web UI
```
