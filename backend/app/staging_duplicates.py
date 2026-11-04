"""Audit/purge staging copies whose series+volumes are already in Calibre (task-55).

Read-only detection first; purge only re-validated duplicates under the real staging root.
Never touches the Calibre library or the raw downloads folder, and never follows symlinks
outside staging.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from app.downloads_inventory import _parse_names, _release_volumes
from app.parser import extract_manga_bundle_and_volumes, match_manga_series_in_calibre, normalize_text

logger = logging.getLogger("mangatrax.staging_duplicates")

STAGING_SUFFIXES = (".cbz", ".cbr", ".zip", ".epub")
SKIP_SUFFIXES = (".part", ".tmp", ".torbox", ".partial")


def _is_temp(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(".") or any(lowered.endswith(suffix) for suffix in SKIP_SUFFIXES)


def _within(root: str, path: str) -> bool:
    try:
        real_root = os.path.realpath(root)
        real_path = os.path.realpath(path)
        return os.path.commonpath([real_root, real_path]) == real_root and real_path != real_root
    except (OSError, ValueError):
        return False


def _calibre_volumes(matched: Dict[str, Any]) -> set:
    volumes = set()
    for key in ("volumes", "owned_volumes", "volume_numbers"):
        value = matched.get(key)
        if isinstance(value, (list, tuple, set)):
            for item in value:
                try:
                    volumes.add(float(item))
                except (TypeError, ValueError):
                    continue
    for book in matched.get("books") or []:
        if not isinstance(book, dict):
            continue
        for key in ("series_index", "volume", "vol"):
            try:
                if book.get(key) is not None:
                    volumes.add(float(book[key]))
                    break
            except (TypeError, ValueError):
                continue
    if not volumes:
        try:
            count = int(matched.get("book_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            volumes = {float(index) for index in range(1, count + 1)}
    return volumes


def _release_for(db, series: str, volumes: List[float]) -> Optional[Dict[str, Any]]:
    normalized = normalize_text(series)
    if not normalized:
        return None
    wanted = set(volumes or [])
    try:
        rows = db.get_releases(limit=2000, offset=0)
    except Exception:
        return None
    best = None
    for row in rows:
        if normalize_text(str(row.get("series_name") or "")) != normalized:
            continue
        release_vols = set(_release_volumes(row))
        if not wanted or not release_vols or (wanted & release_vols):
            if best is None or int(row.get("id") or 0) > int(best.get("id") or 0):
                best = row
    return best


def audit_staging_duplicates(db, staging_dir: str, calibre_series: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    root = os.path.realpath(staging_dir or "")
    groups: List[Dict[str, Any]] = []
    total_bytes = 0
    if not root or not os.path.isdir(root):
        return {"status": "success", "duplicates": 0, "total_bytes": 0, "files": [], "groups": []}
    for current, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in names:
            if _is_temp(name) or not name.lower().endswith(STAGING_SUFFIXES):
                continue
            path = os.path.join(current, name)
            if os.path.islink(path) or not _within(root, path):
                continue
            try:
                if not os.path.isfile(path) or os.path.getsize(path) <= 0:
                    continue
            except OSError:
                continue
            try:
                parsed = extract_manga_bundle_and_volumes(name)
            except Exception:
                continue
            series = str(parsed.get("series_name") or "").strip()
            volumes = [float(value) for value in (parsed.get("volumes") or [])]
            if not series or not volumes or not calibre_series:
                continue
            matched = match_manga_series_in_calibre(series, calibre_series)
            if not matched:
                continue
            owned = _calibre_volumes(matched)
            if not owned or not set(volumes).issubset(owned):
                continue
            release = _release_for(db, series, volumes)
            entry = {
                "filename": name,
                "relative": os.path.relpath(path, root).replace("\\", "/"),
                "size": os.path.getsize(path),
                "series": series,
                "volumes": volumes,
                "calibre_series": str(matched.get("name") or matched.get("series_name") or series),
                "calibre_volumes": sorted(owned),
                "release_id": int(release.get("id")) if release else None,
                "release_title": str(release.get("title") or "") if release else "",
                "release_status": str(release.get("status") or "") if release else "",
                "release_download_status": str(release.get("download_status") or "") if release else "",
            }
            groups.append(entry)
            total_bytes += entry["size"]
    groups.sort(key=lambda item: (item["series"].lower(), item["volumes"]))
    return {
        "status": "success", "duplicates": len(groups), "total_bytes": total_bytes,
        "files": groups, "groups": groups,
    }


def purge_staging_duplicates(db, staging_dir: str, filenames: Optional[List[str]] = None,
                             all_files: bool = False, confirm: bool = False,
                             report=None, calibre_series: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if all_files and not confirm:
        raise ValueError("confirm must be true to purge all duplicates")
    root = os.path.realpath(staging_dir or "")
    if not root or not os.path.isdir(root):
        raise ValueError("Staging directory is unavailable")
    audit = audit_staging_duplicates(
        db, root, calibre_series=calibre_series if calibre_series is not None else _calibre_snapshot_for_purge(db),
    )
    verified = {item["relative"]: item for item in audit["files"]}
    selected = list(verified.values()) if all_files else []
    unmatched = 0
    if not all_files:
        lookup = {}
        for item in verified.values():
            lookup[item["relative"]] = item
            lookup[item["filename"]] = item
        for raw in filenames or []:
            candidate = str(raw or "").replace("\\", "/").strip().lstrip("/")
            item = lookup.get(candidate) or lookup.get(os.path.basename(candidate))
            if item:
                selected.append(item)
            else:
                unmatched += 1
    unique = {item["relative"]: item for item in selected}
    ordered = list(unique.values())
    total = len(ordered)
    purged = bytes_freed = failed = 0
    skipped = int(unmatched)
    purged_files = []
    for index, item in enumerate(ordered, 1):
        if report:
            report(index - 1, total, f"Purging {item['filename']}…")
        path = os.path.join(root, item["relative"])
        try:
            if not os.path.isfile(path):
                skipped += 1
            elif os.path.islink(path) or not _within(root, path):
                skipped += 1
            else:
                size = os.path.getsize(path)
                os.remove(path)
                purged += 1
                bytes_freed += size
                purged_files.append(item)
                db.log_activity(
                    "STAGING_DUPLICATE_PURGED",
                    f"Purged staging duplicate '{item['filename']}'",
                    f"series={item['series']}; volumes={item['volumes']}; bytes={size}",
                )
        except OSError as exc:
            failed += 1
            logger.warning("Could not purge staging duplicate %s: %s", path, exc)
        if report:
            report(index, total, f"Processed {item['filename']}")
    _update_release_bookkeeping(db, purged_files, verified, root)
    return {
        "status": "success", "purged": purged, "bytes_freed": bytes_freed,
        "skipped": skipped, "failed": failed, "purged_files": [item["filename"] for item in purged_files],
    }


def _calibre_snapshot_for_purge(db) -> list:
    try:
        return db.get_all_series()
    except Exception:
        return []


def _update_release_bookkeeping(db, purged_files: List[Dict[str, Any]],
                                verified: Dict[str, Dict[str, Any]],
                                staging_root: str = "") -> None:
    by_release: Dict[int, List[Dict[str, Any]]] = {}
    for item in purged_files:
        if item.get("release_id"):
            by_release.setdefault(int(item["release_id"]), []).append(item)
    for release_id in by_release:
        purged_names = {item["filename"].lower() for item in by_release[release_id]}
        try:
            with db.get_connection() as conn:
                row = conn.execute("SELECT download_files_json FROM releases WHERE id = ?;", (release_id,)).fetchone()
                files = []
                try:
                    files = json.loads(row["download_files_json"] or "[]") if row else []
                except (TypeError, ValueError):
                    files = []
            # Any recorded staged file still on disk means this is a partial purge.
            existing_staged = [
                path for path in files
                if staging_root and _within(staging_root, str(path)) and os.path.exists(str(path))
            ]
            if existing_staged:
                continue
            remaining = [
                item for item in verified.values()
                if int(item.get("release_id") or 0) == release_id
                and item["filename"].lower() not in purged_names
            ]
            if remaining:
                continue
            db.update_release_status(release_id, "IN_CALIBRE", enforce_precedence=True)
            with db.get_connection() as conn:
                kept = [
                    path for path in files
                    if not (staging_root and _within(staging_root, str(path))) and os.path.basename(str(path)).lower() not in purged_names
                ]
                conn.execute(
                    "UPDATE releases SET routed_at = '', download_files_json = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (json.dumps(kept), release_id),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("Could not update release %s after staging purge: %s", release_id, exc)
