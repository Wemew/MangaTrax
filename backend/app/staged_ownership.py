"""Staged-volume ownership map (task-61).

Volumes already present in the staging folder (tagged, waiting for Calibre import) must
count as owned for wanted/missing/discovery/auto-download decisions, exactly like Calibre
volumes — while status/labels still distinguish "In staging".

The map is built from the actual staging scan (authoritative) plus a bounded fallback from
COMPLETED+routed release rows for unparseable file names, cached for ~60 s.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.parser import extract_manga_bundle_and_volumes, normalize_text, split_series_candidate_parts

logger = logging.getLogger("mangatrax.staged_ownership")

CACHE_TTL_SECONDS = 60.0
SUFFIXES = (".cbz", ".cbr", ".zip", ".epub")
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Set[float]]]] = {}
_CACHE_STATE: Dict[str, Any] = {"built_at": 0.0, "map": {}}


def _is_temp(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith((".")) or any(
        lowered.endswith(suffix) for suffix in (".part", ".tmp", ".torbox", ".partial")
    )


def _add_series_keys(mapping: Dict[str, Set[float]], series: str, volumes: Iterable[float]) -> None:
    clean = str(series or "").strip()
    if not clean:
        return
    values = {round(float(value), 2) for value in volumes}
    if not values:
        return
    keys = {normalize_text(clean)}
    for part in split_series_candidate_parts(clean):
        keys.add(normalize_text(part))
    for key in keys:
        if not key:
            continue
        mapping.setdefault(key, set()).update(values)


def build_staged_volumes_map(db, staging_dir: str) -> Dict[str, Set[float]]:
    """Mapping normalised series -> staged volumes (actual files first, DB rows fallback)."""
    root = os.path.realpath(staging_dir or "")
    mapping: Dict[str, Set[float]] = {}
    parsed_series: Set[str] = set()
    if root and os.path.isdir(root):
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".") and not os.path.islink(os.path.join(current, name))]
            for name in names:
                if _is_temp(name) or not name.lower().endswith(SUFFIXES):
                    continue
                path = os.path.join(current, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                try:
                    parsed = extract_manga_bundle_and_volumes(name)
                except Exception:
                    continue
                series = str(parsed.get("series_name") or "").strip()
                volumes = [float(value) for value in (parsed.get("volumes") or [])]
                if not series or not volumes:
                    continue
                parsed_series.add(normalize_text(series))
                _add_series_keys(mapping, series, volumes)
    # Bounded fallback for COMPLETED+routed rows whose staged names were unparseable:
    # only when the file scan has no mapping for that series.
    try:
        rows = db.get_releases(limit=2000, offset=0)
    except Exception:
        rows = []
    for row in rows or []:
        if str(row.get("download_status") or "").upper() != "COMPLETED":
            continue
        if not str(row.get("routed_at") or "").strip():
            continue
        if str(row.get("status") or "").upper() in ("IN_CALIBRE", "IGNORED", "BLACKLISTED", "IN_STAGING"):
            continue
        series = str(row.get("series_name") or "").strip()
        if not series:
            continue
        keys = {normalize_text(series)}
        for part in split_series_candidate_parts(series):
            keys.add(normalize_text(part))
        if any(key and key in parsed_series for key in keys):
            continue
        try:
            wanted = json.loads(row.get("download_wanted_json") or "[]")
        except (TypeError, ValueError):
            wanted = []
        if not isinstance(wanted, list) or not wanted:
            try:
                wanted = json.loads(row.get("volumes_json") or "[]")
            except (TypeError, ValueError):
                wanted = []
        try:
            volumes = [float(value) for value in wanted]
        except (TypeError, ValueError):
            volumes = []
        _add_series_keys(mapping, series, volumes)
    return mapping


def get_staged_volumes_map(db, staging_dir: str, force: bool = False) -> Dict[str, Set[float]]:
    """Cached staged ownership map (~60 s), invalidated explicitly after mutations."""
    key = (str(db), os.path.realpath(staging_dir or ""))
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and not force and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    mapping = build_staged_volumes_map(db, staging_dir)
    with _CACHE_LOCK:
        _CACHE[key] = (now, mapping)
        _CACHE_STATE["built_at"] = now
        _CACHE_STATE["map"] = mapping
    return mapping


def invalidate_staged_ownership_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_STATE["built_at"] = 0.0
        _CACHE_STATE["map"] = {}


def staged_volumes_for(mapping: Optional[Dict[str, Set[float]]], names: Iterable[str]) -> Set[float]:
    if not mapping:
        return set()
    volumes: Set[float] = set()
    for name in names or []:
        clean = str(name or "").strip()
        if not clean:
            continue
        candidates = {normalize_text(clean)}
        for part in split_series_candidate_parts(clean):
            candidates.add(normalize_text(part))
        for key in candidates:
            volumes.update(mapping.get(key, set()))
    return volumes
