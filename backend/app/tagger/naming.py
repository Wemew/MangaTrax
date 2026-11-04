"""Shared Calibre-compatible ComicInfo formatters.

Every ComicInfo element that mirrors a Calibre column is formatted here, so the two
tagging paths (``cbz_tagger.auto_tag_staging_cbz_files`` and
``pipeline._build_metadata``) and the audit/repair can never disagree:

* ``Title``  -> ``Series vNN`` (``v01``, ``v01-03``, ``v01, v03``, ``v1.5``)
* ``Series`` -> exact Calibre series name
* ``Writer`` / ``Penciller`` -> Calibre author display: ``Name1 & Name2``
* ``Publisher`` -> exact Calibre publisher string
* ``Genre`` -> Calibre tag display: ``Tag1, Tag2``
* ``Number`` / ``Volume`` -> Calibre series-index display: ``28`` / ``1.5``;
  bundle ranges stay as the existing extra metadata (``1-3``)

Non-Calibre extras (``Manga``, page count, ...) are never touched here.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, List, Optional

from app.parser import extract_manga_bundle_and_volumes

_AMPERSAND_RE = re.compile(r"\s*(?:&|\band\b)\s*", re.IGNORECASE)
_AUTHOR_SPLIT_RE = re.compile(r"\s*(?:,|;|&|\band\b)\s*", re.IGNORECASE)
_TAG_SPLIT_RE = re.compile(r"\s*[,;]\s*")
_RANGE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$")


def _as_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _dedupe_preserve(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


# ---------------------------------------------------------------------------
# Authors (Writer / Penciller)
# ---------------------------------------------------------------------------
def format_authors(value: Any) -> str:
    """``"A,B; C & A"`` -> ``"A & B & C"`` (trim, dedupe, preserve first-seen order)."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        raw = " & ".join(str(item) for item in value)
    else:
        raw = str(value)
    parts = _AUTHOR_SPLIT_RE.split(raw)
    return " & ".join(_dedupe_preserve(parts))


# Backwards-compatible alias used by some callers.
format_writer = format_authors
format_penciller = format_authors


# ---------------------------------------------------------------------------
# Tags (Genre)
# ---------------------------------------------------------------------------
def format_tags(value: Any) -> str:
    """``"Action,Comedy; Action"`` -> ``"Action, Comedy"`` (trim, dedupe, order kept)."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        raw = ", ".join(str(item) for item in value)
    else:
        raw = str(value)
    return ", ".join(_dedupe_preserve(_TAG_SPLIT_RE.split(raw)))


format_genre = format_tags


# ---------------------------------------------------------------------------
# Publisher / Series
# ---------------------------------------------------------------------------
def format_publisher(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def format_series(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


# ---------------------------------------------------------------------------
# Number / Volume (Calibre series_index display)
# ---------------------------------------------------------------------------
def format_volume_number(value: Any) -> str:
    """``1`` -> ``01`` (title form), ``1.5`` -> ``1.5``, ``100`` -> ``100``."""
    number = _as_number(value)
    if number is None:
        return ""
    if number.is_integer():
        return f"{int(number):02d}"
    return _format_number(number)


def format_volume(value: Any) -> str:
    """Calibre series-index display: ``28``, ``1.5``; bundles keep ``1-3``."""
    if value is None:
        return ""
    text = " ".join(str(value).split()).strip()
    if not text:
        return ""
    match = _RANGE_RE.fullmatch(text)
    if match:
        start = _as_number(match.group(1))
        end = _as_number(match.group(2))
        if start is not None and end is not None:
            return f"{_format_number(start)}-{_format_number(end)}"
        return text
    number = _as_number(text)
    if number is not None:
        return _format_number(number)
    return text


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------
def _parse_volume_value(value: Any) -> List[float]:
    """Parse ``"28"``, ``"1-3"``, ``"1, 3"`` or ``"1.5"`` into sorted volumes."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        numbers = [_as_number(item) for item in value]
        return sorted({number for number in numbers if number is not None})
    text = str(value).strip()
    if not text:
        return []
    tokens = [token for token in re.split(r"[,;\s]+", text) if token]
    parsed: List[float] = []
    for token in tokens:
        match = _RANGE_RE.fullmatch(token)
        if match:
            start = _as_number(match.group(1))
            end = _as_number(match.group(2))
            if start is None or end is None:
                continue
            low, high = (start, end) if start <= end else (end, start)
            if high - low > 500:
                high = low
            cursor = low
            while cursor <= high + 1e-9:
                parsed.append(round(cursor, 4))
                cursor += 1.0
            continue
        number = _as_number(token)
        if number is not None:
            parsed.append(number)
    return sorted(set(parsed))


def _series_or_fallback(series_name: Optional[str], fallback_name: Optional[str]) -> str:
    series = format_series(series_name)
    if series:
        return series
    fallback = str(fallback_name or "").strip()
    if not fallback:
        return ""
    try:
        parsed = extract_manga_bundle_and_volumes(fallback)
        parsed_series = format_series(parsed.get("series_name"))
        if parsed_series:
            return parsed_series
    except Exception:  # pragma: no cover - parser must never break tagging
        pass
    return os.path.splitext(os.path.basename(fallback))[0].strip()


def format_comicinfo_title(
    series_name: Optional[str],
    *,
    volumes: Optional[Iterable[Any]] = None,
    volume: Any = None,
    volume_start: Any = None,
    volume_end: Any = None,
    is_bundle: bool = False,
    fallback_name: Optional[str] = None,
) -> str:
    """Return the standard Calibre-compatible title for the given series/volumes."""
    series = _series_or_fallback(series_name, fallback_name)
    numbers: List[float] = []
    if volumes:
        numbers.extend(value for value in (_as_number(item) for item in volumes) if value is not None)
    if volume is not None and not numbers:
        numbers.extend(_parse_volume_value(volume))
    start = _as_number(volume_start)
    end = _as_number(volume_end)
    if start is not None and not numbers:
        numbers.append(start)
        if is_bundle and end is not None and end > start:
            cursor = start
            while cursor <= end + 1e-9:
                numbers.append(round(cursor, 4))
                cursor += 1.0
    numbers = sorted({round(float(value), 4) for value in numbers})
    if not numbers:
        return series
    if len(numbers) == 1:
        return f"{series} v{format_volume_number(numbers[0])}".strip()
    contiguous = all(abs((b - a) - 1.0) < 1e-6 for a, b in zip(numbers, numbers[1:]))
    if contiguous:
        return f"{series} v{format_volume_number(numbers[0])}-{format_volume_number(numbers[-1])}".strip()
    return f"{series} " + ", ".join(f"v{format_volume_number(value)}" for value in numbers)


def format_title_for_volume_value(series_name: Optional[str], volume_value: Any,
                                  fallback_name: Optional[str] = None) -> str:
    """Formatter entry point for an existing ComicInfo ``<Volume>``/``<Number>`` value."""
    return format_comicinfo_title(
        series_name, volume=volume_value, fallback_name=fallback_name,
    )


def volume_title_parts(parsed: dict) -> dict:
    """Convenience kwargs from a parser result (used by both tagging paths)."""
    return {
        "volumes": parsed.get("volumes"),
        "volume": parsed.get("volume_start"),
        "volume_start": parsed.get("volume_start"),
        "volume_end": parsed.get("volume_end"),
        "is_bundle": bool(parsed.get("is_bundle")),
    }
