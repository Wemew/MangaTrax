"""Unified search across series, releases and AniList alternate titles.

PROJECT.md M2 feature 17.  One query must find a series no matter which spelling
the user remembers: the stable matching key (``series_tracking.name``), the
AniList canonical English title, or the AniList romaji title.

Naming contract respected here
------------------------------
The result's ``stable_name`` is the authoritative matching key (what the whole
app -- grouping, Calibre matching, ComicInfo tagging -- must continue to use).
``display_title`` is the cosmetic AniList title, resolved the same way the UI
resolves it (``database.display_title_from_meta``).  A query that only matches an
AniList alias returns the tracked series under its stable key, with
``match == "anilist_alias"`` and the alias that matched in ``matched_alias``.

Alias resolution only uses authoritative cached metadata (``source ==
'anilist'`` with a non-empty canonical title), so it never invents a translation
while the metadata service is offline.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from app.database import display_title_from_meta, is_authoritative_metadata
from app.parser import normalize_text

logger = logging.getLogger("mangatrax.search")

_MATCH_RANK = {"stable_name": 0, "normalized_name": 1, "anilist_alias": 2}


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _build_metadata_index(db) -> Dict[str, Dict[str, Any]]:
    """Map normalized search_term / canonical / romaji -> authoritative cache row."""
    index: Dict[str, Dict[str, Any]] = {}
    try:
        rows = db.get_all_manga_metadata_cache() or {}
    except Exception as exc:  # pragma: no cover - search must never fail hard
        logger.debug("Could not read metadata cache for unified search: %s", exc)
        return index

    for row in rows.values():
        if not is_authoritative_metadata(row):
            continue
        for field in ("search_term", "canonical_title", "romaji_title"):
            norm = normalize_text(_clean(row.get(field)))
            if norm:
                index.setdefault(norm, row)
    return index


def _display_title(stable_name: str, metadata_index: Dict[str, Dict[str, Any]]) -> str:
    meta = metadata_index.get(normalize_text(stable_name))
    return display_title_from_meta(stable_name, meta) or stable_name


def _alias_display(alias_norm: str, metadata_index: Dict[str, Dict[str, Any]]) -> str:
    row = metadata_index.get(alias_norm)
    if row:
        for field in ("canonical_title", "romaji_title", "search_term"):
            value = _clean(row.get(field))
            if value and normalize_text(value) == alias_norm:
                return value
    return alias_norm


def unified_search(db, query: str, limit: int = 20) -> Dict[str, Any]:
    """Search tracked series, their AniList aliases, and stored releases.

    Matching is literal substring matching on normalized text (lowercase,
    punctuation/unicode-normalized), so a query never needs the exact spelling.
    Returns the stable key alongside the display title for every series hit.
    """
    cleaned_query = _clean(query)
    query_norm = normalize_text(cleaned_query)
    limit = max(1, min(int(limit or 20), 50))

    payload: Dict[str, Any] = {
        "status": "success",
        "query": cleaned_query,
        "limit": limit,
        "series": [],
        "releases": [],
        "counts": {"series": 0, "releases": 0},
        "total": 0,
    }
    if not query_norm:
        return payload

    metadata_index = _build_metadata_index(db)
    try:
        alias_sets: Dict[str, Set[str]] = db.get_authoritative_alias_sets() or {}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not build alias sets for unified search: %s", exc)
        alias_sets = {}
    try:
        tracked_rows = db.get_all_series() or []
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not read tracked series for unified search: %s", exc)
        tracked_rows = []

    rows_by_norm = {
        normalize_text(_clean(row.get("name"))): row
        for row in tracked_rows
        if normalize_text(_clean(row.get("name")))
    }

    series_hits: Dict[str, Dict[str, Any]] = {}

    def _add_series_hit(stable_name: str, match: str, matched_alias: Optional[str] = None) -> None:
        stable_name = _clean(stable_name)
        key = normalize_text(stable_name)
        if not key:
            return
        existing = series_hits.get(key)
        if existing is not None and _MATCH_RANK.get(existing["match"], 9) <= _MATCH_RANK.get(match, 9):
            return
        row = rows_by_norm.get(key) or {}
        display = _display_title(stable_name, metadata_index)
        series_hits[key] = {
            "stable_name": stable_name,
            "display_title": display,
            "match": match,
            "matched_alias": matched_alias,
            "authors": _clean(row.get("authors")),
            "publisher": _clean(row.get("publisher")),
            "is_monitored": bool(row.get("is_monitored", 1)),
            "cover_url": _clean(row.get("cover_url")),
        }

    # 1. Tracked stable names (the Calibre/typed matching keys).
    for row in tracked_rows:
        stable_name = _clean(row.get("name"))
        stable_norm = normalize_text(stable_name)
        if not stable_norm:
            continue
        if query_norm in stable_norm:
            _add_series_hit(stable_name, "stable_name")
            continue
        # 2. AniList canonical / romaji aliases of that series.
        for alias_norm in sorted(alias_sets.get(stable_norm) or ()):
            if alias_norm != stable_norm and query_norm in alias_norm:
                _add_series_hit(stable_name, "anilist_alias", _alias_display(alias_norm, metadata_index))
                break

    try:
        release_rows = db.search_releases(cleaned_query, limit=limit)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Release search failed: %s", exc)
        release_rows = []

    releases: List[Dict[str, Any]] = []
    for row in release_rows:
        stable_name = _clean(row.get("series_name")) or _clean(row.get("title"))
        releases.append({
            "release_id": row.get("id"),
            "title": _clean(row.get("title")),
            "stable_name": stable_name,
            "display_title": _display_title(stable_name, metadata_index),
            "status": _clean(row.get("status")),
            "release_group": _clean(row.get("release_group")),
            "volumes": row.get("volumes") or [],
            "is_bundle": bool(row.get("is_bundle")),
            "is_complete": bool(row.get("is_complete")),
            "cover_url": _clean(row.get("cover_url")),
        })

    ordered_series = sorted(
        series_hits.values(),
        key=lambda hit: (_MATCH_RANK.get(hit["match"], 9), hit["stable_name"].lower()),
    )[:limit]

    payload["series"] = ordered_series
    payload["releases"] = releases
    payload["counts"] = {"series": len(ordered_series), "releases": len(releases)}
    payload["total"] = len(ordered_series) + len(releases)
    return payload
