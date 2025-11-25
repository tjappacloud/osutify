"""Utility functions for osu! MP3 Browser."""

import re
import os


def strip_leading_numbers(s: str) -> str:
    """Remove leading numeric IDs and separators (e.g. '311328 Foo' -> 'Foo')."""
    if not s:
        return s
    return re.sub(r'^\s*\d+[\s._-]*', '', s)


def parse_artist_from_folder(folder_name: str) -> str:
    """Try to extract an artist name from a folder name like 'Artist - Title' or 'Artist: Title'.
    Returns the artist string or empty if not identifiable.
    """
    if not folder_name:
        return ''
    # remove leading/trailing whitespace and common surrounding characters
    name = folder_name.strip()
    # Try a regex that captures everything up to the first separator (handles multi-word names)
    try:
        m = re.match(r"^\s*(?P<artist>.+?)\s*(?:[-–—:|~]+)\s+", name)
        if m:
            return m.group('artist').strip()
    except Exception:
        pass
    # fallback: split on first occurrence of any common separator
    try:
        parts = re.split(r"\s*[-–—:|~]+\s*", name, maxsplit=1)
        if parts and parts[0].strip():
            return parts[0].strip()
    except Exception:
        pass
    return ''


def format_duration(sec: int) -> str:
    """Format duration in seconds as M:SS string."""
    if not sec:
        return ''
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def os_walk(path):
    """Simple wrapper for os.walk so we can mock/test easily."""
    for root, dirs, files in os.walk(path):
        yield root, dirs, files
