"""Configuration and constants for osu! MP3 Browser."""

from pathlib import Path

# Supported audio extensions
SUPPORTED_AUDIO_EXTS = ('.mp3', '.ogg')

# Minimum duration threshold (seconds)
MIN_DURATION_SECONDS = 30

# Cache filename
CACHE_FILENAME = '.osu_mp3_browser_cache.json'


def get_default_osu_songs_dir():
    """Return the default osu! Songs directory path on Windows."""
    home = Path.home()
    default = home / "AppData" / "Local" / "osu!" / "Songs"
    return default
