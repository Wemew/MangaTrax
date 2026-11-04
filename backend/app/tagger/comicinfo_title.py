"""Backward-compatible re-export of :mod:`app.tagger.naming`.

The shared formatters live in ``naming.py``; this module remains so older imports of
``comicinfo_title`` keep working.
"""

from app.tagger.naming import (  # noqa: F401
    format_comicinfo_title,
    format_title_for_volume_value,
    format_volume_number,
    volume_title_parts,
)

__all__ = [
    "format_comicinfo_title",
    "format_title_for_volume_value",
    "format_volume_number",
    "volume_title_parts",
]
