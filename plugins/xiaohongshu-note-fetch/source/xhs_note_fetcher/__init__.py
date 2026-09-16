"""Extract a Xiaohongshu note from its complete share link."""

from .errors import FetchError

__version__ = "0.2.0"


def fetch_note(share_text: str, **options) -> dict:
    """Fetch one note. See client.fetch_note for available options."""
    from .client import fetch_note as _fetch_note
    return _fetch_note(share_text, **options)


__all__ = ["fetch_note", "FetchError"]
