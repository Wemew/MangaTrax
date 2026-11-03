"""Cheap, non-destructive inventory of the raw downloads folder (task-30).

The Downloads page used to show only the engine queue / awaiting-Calibre / staging, so
files sitting in ``/downloads/<torrent dir>`` were invisible until "Process Downloads"
was pressed.  This module scans the raw folder (``os.scandir``, depth <= 2), derives a
small per-entry state and persists the result for a synchronous page render.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.parser import extract_manga_bundle_and_volumes, normalize_text

logger = logging.getLogger("mangatrax.downloads_inventory")

MAX_ENTRIES = 200
MAX_DEPTH = 2
_PARTIAL_SUFFIXES = (".part", ".mangatrax-part", ".torbox-download.zip.part")
_CBZ_SUFFIXES = (".cbz", ".cbr")


def _iso(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return ""


def _is_partial(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in _PARTIAL_SUFFIXES)


def _parse_names(names: List[str]) -> Dict[str, Any]:
    """Parse series/volumes from the first parseable cbz name."""
    series_name = ""
    volumes = set()
    for name in names:
        if not name.lower().endswith(_CBZ_SUFFIXES):
            continue
        try:
            parsed = extract_manga_bundle_and_volumes(name)
        except Exception:
            continue
        candidate = str(parsed.get("series_name") or "").strip()
        if candidate and not series_name:
            series_name = candidate
        for value in parsed.get("volumes") or []:
            try:
                volumes.add(float(value))
            except (TypeError, ValueError):
                continue
    return {"series_name": series_name, "volumes": sorted(volumes)}


def _summarize_files(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_size = sum(int(item.get("size") or 0) for item in files)
    cbz_names = [item["name"] for item in files if item["name"].lower().endswith(_CBZ_SUFFIXES)]
    mtimes = [float(item["mtime"]) for item in files if item.get("mtime")]
    return {
        "size_bytes": total_size,
        "file_count": len(files),
        "cbz_count": len(cbz_names),
        "has_partials": any(item["is_partial"] for item in files),
        "oldest_mtime": _iso(min(mtimes)) if mtimes else "",
        "newest_mtime": _iso(max(mtimes)) if mtimes else "",
        "age_seconds": int(max(0.0, time.time() - max(mtimes))) if mtimes else None,
        "cbz_names": cbz_names,
    }


def _collect_files(directory: str) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    try:
        with os.scandir(directory) as iterator:
            for item in iterator:
                try:
                    if not item.is_file(follow_symlinks=False):
                        continue
                    stat = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                files.append({
                    "name": item.name,
                    "size": int(stat.st_size),
                    "mtime": float(stat.st_mtime),
                    "is_partial": _is_partial(item.name),
                })
    except OSError:
        return files
    return files


def _match_release(entry_path: str, entry_name: str, releases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    target = os.path.normcase(os.path.abspath(entry_path))
    for release in releases:
        try:
            paths = json.loads(release.get("download_files_json") or "[]")
        except (TypeError, ValueError):
            paths = []
        if not isinstance(paths, list):
            continue
        for path in paths:
            try:
                candidate = os.path.normcase(os.path.abspath(str(path)))
            except (TypeError, ValueError):
                continue
            if candidate == target or candidate.startswith(target + os.sep) or target.startswith(candidate + os.sep):
                return {
                    "title": str(release.get("title") or ""),
                    "download_status": str(release.get("download_status") or ""),
                    "wanted_volumes": _release_volumes(release),
                    "downloaded_at": str(release.get("downloaded_at") or ""),
                }
    lowered = normalize_text(entry_name)
    for release in releases:
        if lowered and lowered in normalize_text(str(release.get("title") or "")):
            return {
                "title": str(release.get("title") or ""),
                "download_status": str(release.get("download_status") or ""),
                "wanted_volumes": _release_volumes(release),
                "downloaded_at": str(release.get("downloaded_at") or ""),
            }
    return None


def _release_volumes(release: Dict[str, Any]) -> List[float]:
    for key in ("download_wanted_json", "missing_vols_json"):
        try:
            values = json.loads(release.get(key) or "[]")
        except (TypeError, ValueError):
            continue
        if isinstance(values, list) and values:
            try:
                return sorted({float(value) for value in values})
            except (TypeError, ValueError):
                continue
    return []


def _state_for(summary: Dict[str, Any], parsed: Dict[str, Any], series_templates: Dict[str, Any]) -> str:
    if summary["has_partials"]:
        return "partial"
    if summary["cbz_count"] <= 0:
        return "unmatched"
    series = str(parsed.get("series_name") or "").strip()
    if not series:
        return "unmatched"
    key = normalize_text(series)
    if key and key in (series_templates or {}):
        return "ready"
    return "awaiting_calibre"


def scan_downloads(
    downloads_dir: str,
    series_templates: Optional[Dict[str, Any]] = None,
    releases: Optional[List[Dict[str, Any]]] = None,
    max_entries: int = MAX_ENTRIES,
) -> Dict[str, Any]:
    """Scan the raw downloads folder (depth <= 2) and return a JSON-safe inventory."""
    series_templates = series_templates or {}
    releases = releases or []
    scanned_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result: Dict[str, Any] = {
        "downloads_dir": str(downloads_dir or ""),
        "exists": bool(downloads_dir) and os.path.isdir(downloads_dir),
        "scanned_at": scanned_at,
        "truncated": False,
        "entries": [],
    }
    if not result["exists"]:
        return result
    entries: List[Dict[str, Any]] = []
    try:
        with os.scandir(downloads_dir) as iterator:
            for item in iterator:
                if len(entries) >= int(max_entries):
                    result["truncated"] = True
                    break
                try:
                    if item.is_dir(follow_symlinks=False):
                        files = _collect_files(item.path)
                        summary = _summarize_files(files)
                        parsed = _parse_names([item.name] + summary["cbz_names"])
                        entry_path = item.path
                        is_dir = True
                    elif item.is_file(follow_symlinks=False):
                        stat = item.stat(follow_symlinks=False)
                        summary = _summarize_files([{
                            "name": item.name, "size": int(stat.st_size),
                            "mtime": float(stat.st_mtime), "is_partial": _is_partial(item.name),
                        }])
                        parsed = _parse_names([item.name])
                        entry_path = item.path
                        is_dir = False
                    else:
                        continue
                except OSError:
                    continue
                summary.pop("cbz_names", None)
                state = _state_for(summary, parsed, series_templates)
                entries.append({
                    "name": item.name,
                    "relative": os.path.relpath(entry_path, downloads_dir),
                    "is_dir": is_dir,
                    **summary,
                    "series_name": parsed.get("series_name") or "",
                    "volumes": parsed.get("volumes") or [],
                    "state": state,
                    "release": _match_release(entry_path, item.name, releases),
                })
    except OSError as exc:
        result["error"] = str(exc)
        return result
    result["entries"] = entries
    result["entry_count"] = len(entries)
    return result


def refresh_inventory(db: Any, downloads_dir: str, max_entries: int = MAX_ENTRIES) -> Dict[str, Any]:
    """Scan + persist the inventory (`downloads_inventory_json`/`downloads_scanned_at`)."""
    try:
        templates = db.get_series_templates_map()
    except Exception:
        templates = {}
    try:
        releases = db.get_tracked_downloads(limit=500)
    except Exception:
        releases = []
    inventory = scan_downloads(
        downloads_dir, series_templates=templates, releases=releases, max_entries=max_entries,
    )
    payload = json.dumps(inventory)
    try:
        db.set_setting("downloads_inventory_json", payload)
        db.set_setting("downloads_scanned_at", inventory.get("scanned_at") or "")
    except Exception as exc:  # diagnostics must never break the worker
        logger.debug("Could not persist downloads inventory: %s", exc)
    return inventory


def load_inventory(db: Any) -> Dict[str, Any]:
    """Return the last persisted inventory (empty structure when never scanned)."""
    try:
        raw = db.get_setting("downloads_inventory_json", "")
        inventory = json.loads(raw) if raw else {}
    except Exception:
        inventory = {}
    if not isinstance(inventory, dict):
        inventory = {}
    inventory.setdefault("entries", [])
    inventory.setdefault("scanned_at", "")
    inventory.setdefault("exists", False)
    return inventory
