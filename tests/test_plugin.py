# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for audiophile_wishlist standalone functions.

These tests don't require Nicotine+ — they test URL detection,
quality filtering, and scoring logic in isolation.
"""

import pytest
import sys
import os
import re
import json

# ─── We need to extract standalone functions without importing pynicotine ─────
# Parse the source and exec only the parts that don't depend on pynicotine.

_SOURCE_PATH = os.path.join(os.path.dirname(__file__), "..", "audiophile_wishlist", "__init__.py")


def _load_standalone_functions():
    """Load standalone functions from __init__.py without importing pynicotine."""

    with open(_SOURCE_PATH) as f:
        source = f.read()

    # Extract everything before the pynicotine import and the function definitions
    # We'll exec the constants and standalone functions in a clean namespace
    namespace = {"__builtins__": __builtins__}

    # Execute imports that don't need pynicotine
    exec("import json, os, re, time, urllib.request, urllib.error, urllib.parse", namespace)
    exec("from threading import Thread", namespace)

    # Extract and exec each standalone block
    blocks_to_extract = [
        # Constants
        (r"LOSSLESS_EXTENSIONS\s*=.*", r"^(?:USER_AGENT|ATTR_|LOSSLESS)"),
        # Functions (between markers)
    ]

    # Simpler approach: extract lines between markers
    lines = source.split("\n")
    in_standalone = False
    standalone_lines = []

    for line in lines:
        # Skip the pynicotine import and Plugin class
        if "from pynicotine" in line:
            continue
        if line.startswith("class Plugin("):
            break

        standalone_lines.append(line)

    standalone_code = "\n".join(standalone_lines)
    exec(standalone_code, namespace)

    return namespace


NS = _load_standalone_functions()

# Bind functions for easy access
_detect_provider = NS["_detect_provider"]
_check_quality = NS["_check_quality"]
_score_result = NS["_score_result"]
_get_extension = NS["_get_extension"]

ATTR_BITRATE = NS["ATTR_BITRATE"]
ATTR_SAMPLE_RATE = NS["ATTR_SAMPLE_RATE"]
ATTR_BIT_DEPTH = NS["ATTR_BIT_DEPTH"]

DEFAULT_SETTINGS = {
    "allowed_formats": "flac wav alac ape wv aiff",
    "min_bitrate": 800,
    "min_sample_rate": 44100,
    "min_bit_depth": 16,
    "min_file_size_mb": 5,
}


# ─── URL Detection ───────────────────────────────────────────────────────────

class TestDetectProvider:

    def test_deezer_standard(self):
        assert _detect_provider("https://www.deezer.com/playlist/908622995") == ("deezer", "908622995")

    def test_deezer_with_locale(self):
        assert _detect_provider("https://deezer.com/fr/playlist/123456") == ("deezer", "123456")

    def test_spotify_url(self):
        assert _detect_provider("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") == (
            "spotify", "37i9dQZF1DXcBWIGoYBM5M"
        )

    def test_spotify_uri(self):
        assert _detect_provider("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == (
            "spotify", "37i9dQZF1DXcBWIGoYBM5M"
        )

    def test_ytmusic_url(self):
        assert _detect_provider("https://music.youtube.com/playlist?list=PLrAXtm") == ("ytmusic", "PLrAXtm")

    def test_youtube_regular_url(self):
        assert _detect_provider("https://www.youtube.com/playlist?list=PLxxx") == ("ytmusic", "PLxxx")

    def test_unknown_url(self):
        assert _detect_provider("https://random.com/something") == (None, None)

    def test_empty_string(self):
        assert _detect_provider("") == (None, None)

    def test_whitespace_handling(self):
        assert _detect_provider("  https://www.deezer.com/playlist/123  ") == ("deezer", "123")


# ─── File Extension ──────────────────────────────────────────────────────────

class TestGetExtension:

    def test_flac(self):
        assert _get_extension("\\Music\\Album\\track.flac") == "flac"

    def test_mp3(self):
        assert _get_extension("\\Music\\track.MP3") == "mp3"

    def test_no_extension(self):
        assert _get_extension("\\Music\\track") == ""

    def test_forward_slashes(self):
        assert _get_extension("/Music/Album/track.wav") == "wav"

    def test_dots_in_path(self):
        assert _get_extension("\\Music\\Mr. Smith\\track.v2.flac") == "flac"


# ─── Quality Filter ──────────────────────────────────────────────────────────

class TestCheckQuality:

    def test_flac_cd_quality_passes(self):
        passes, _ = _check_quality(
            "\\track.flac", 30 * 1024 * 1024,
            {ATTR_BITRATE: 1411, ATTR_SAMPLE_RATE: 44100, ATTR_BIT_DEPTH: 16},
            DEFAULT_SETTINGS,
        )
        assert passes is True

    def test_mp3_rejected(self):
        passes, reason = _check_quality(
            "\\track.mp3", 8 * 1024 * 1024,
            {ATTR_BITRATE: 320},
            DEFAULT_SETTINGS,
        )
        assert passes is False
        assert "format" in reason

    def test_too_small_rejected(self):
        passes, reason = _check_quality(
            "\\track.flac", 2 * 1024 * 1024,
            {ATTR_BITRATE: 1411, ATTR_SAMPLE_RATE: 44100, ATTR_BIT_DEPTH: 16},
            DEFAULT_SETTINGS,
        )
        assert passes is False
        assert "small" in reason

    def test_low_bitrate_rejected(self):
        passes, reason = _check_quality(
            "\\track.flac", 30 * 1024 * 1024,
            {ATTR_BITRATE: 400, ATTR_SAMPLE_RATE: 44100, ATTR_BIT_DEPTH: 16},
            DEFAULT_SETTINGS,
        )
        assert passes is False
        assert "bitrate" in reason

    def test_low_sample_rate_rejected(self):
        passes, reason = _check_quality(
            "\\track.flac", 30 * 1024 * 1024,
            {ATTR_BITRATE: 1411, ATTR_SAMPLE_RATE: 22050, ATTR_BIT_DEPTH: 16},
            DEFAULT_SETTINGS,
        )
        assert passes is False
        assert "sample rate" in reason

    def test_low_bit_depth_rejected(self):
        passes, reason = _check_quality(
            "\\track.flac", 30 * 1024 * 1024,
            {ATTR_BITRATE: 1411, ATTR_SAMPLE_RATE: 44100, ATTR_BIT_DEPTH: 8},
            DEFAULT_SETTINGS,
        )
        assert passes is False
        assert "bit depth" in reason

    def test_hires_wav_passes(self):
        passes, _ = _check_quality(
            "\\track.wav", 80 * 1024 * 1024,
            {ATTR_BITRATE: 4608, ATTR_SAMPLE_RATE: 96000, ATTR_BIT_DEPTH: 24},
            DEFAULT_SETTINGS,
        )
        assert passes is True

    def test_no_attrs_format_ok_passes(self):
        passes, reason = _check_quality(
            "\\track.flac", 30 * 1024 * 1024,
            {},
            DEFAULT_SETTINGS,
        )
        assert passes is True
        assert "no attributes" in reason

    def test_disabled_filters(self):
        """All filters set to 0 = disabled — only format matters."""
        settings = {**DEFAULT_SETTINGS, "min_bitrate": 0, "min_sample_rate": 0,
                    "min_bit_depth": 0, "min_file_size_mb": 0}
        passes, _ = _check_quality("\\track.flac", 100, {ATTR_BITRATE: 1}, settings)
        assert passes is True


# ─── Scoring ─────────────────────────────────────────────────────────────────

class TestScoreResult:

    def test_lossless_higher_than_lossy(self):
        flac_score = _score_result("\\track.flac", 30 * 1024 * 1024, {ATTR_BIT_DEPTH: 16, ATTR_SAMPLE_RATE: 44100})
        mp3_score = _score_result("\\track.mp3", 8 * 1024 * 1024, {ATTR_BIT_DEPTH: 0, ATTR_SAMPLE_RATE: 44100})
        assert flac_score > mp3_score

    def test_hires_higher_than_cd(self):
        hires = _score_result("\\track.flac", 80 * 1024 * 1024, {ATTR_BIT_DEPTH: 24, ATTR_SAMPLE_RATE: 96000})
        cd = _score_result("\\track.flac", 30 * 1024 * 1024, {ATTR_BIT_DEPTH: 16, ATTR_SAMPLE_RATE: 44100})
        assert hires > cd

    def test_larger_file_higher_score(self):
        big = _score_result("\\track.flac", 50 * 1024 * 1024, {})
        small = _score_result("\\track.flac", 20 * 1024 * 1024, {})
        assert big > small
