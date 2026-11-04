"""Reconcile COMPLETED releases whose downloaded/staged files vanished (task-54).

A release is left "downloaded"/"in staging" forever if its files are deleted outside
MangaTrax.  This module checks the recorded file basenames (raw downloads + flat/legacy
staging), falls back to a Calibre match or a parsed staging match, and only resets the row
to IDLE when nothing can be found.  Read/keep-first; reset-only, never deletes user data.

Run via ``reconcile_missing_release_files(...)`` with a live Calibre series snapshot, or
with ``calibre_series=None`` to skip the Calibre fallback (offline/tests).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from app.downloads_inventory import _parse_names, _release_volumes
from app.parser import match_manga_series_in_calibre, normalize_text

logger = logging.getLogger("mangatrax.reconcile")

RESET_HINT = (
    "Tracked files disappeared from downloads and staging; reset — "
    "download again if still needed."
)
EXCLUDED_STATUSES = {"IN_CALIBRE", "IGNORED", "BLACKLISTED"}
EXCLUDED_DOWNLOAD_STATUSES = {
    "DOWNLOADING", "QUEUED", "PENDING_DEDUPE", "MANUAL_PICKUP", "FAILED",
}
STAGING_SUFFIXES = (".cbz", ".cbr", ".zip", ".epub")


def _load_files(raw: Any) -> List[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item or "").strip()]


def _basename(path: str) -> str:
    return os.path.basename(str(path or "").replace("\\", "/").strip())


def _files_in_tree(root: str) -> Dict[str, str]:
    index: Dict[str, str] = {}
    if not root or not os.path.isdir(root):
        return index
    try:
        for current, _dirs, names in os.walk(root):
            for name in names:
                index.setdefault(name.lower(), os.path.join(current, name))
    except OSError:
        return index
    return index


def _volumes_in_calibre(release: Dict[str, Any], matched: Dict[str, Any]) -> bool:
    volumes = _release_volumes(release)
    if not volumes:
        return True
    try:
        book_count = int(matched.get("book_count") or matched.get("books") or 0)
    except (TypeError, ValueError):
        book_count = 0
    if book_count <= 0:
        return True
    return max(volumes) <= book_count


def _staging_match(release: Dict[str, Any], staging_files: Dict[str, str]) -> Optional[str]:
    series = normalize_text(str(release.get("series_name") or ""))
    wanted = set(_release_volumes(release))
    if not series:
        return None
    for name_lower, path in staging_files.items():
        if not name_lower.endswith(STAGING_SUFFIXES):
            continue
        parsed = _parse_names([name_lower])
        if normalize_text(parsed.get("series_name") or "") != series:
            continue
        found_vols = set(parsed.get("volumes") or [])
        if not wanted or wanted & found_vols:
            return path
    return None


def _reset_release(db, release_id: int) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE releases SET download_status = 'IDLE', routed_at = '', downloaded_at = NULL, "
            "download_files_json = '[]', downloader_id = NULL, torrent_hash = NULL, "
            "download_error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (RESET_HINT, int(release_id)),
        )
        conn.commit()
    db.log_activity(
        "DOWNLOAD_FILES_MISSING",
        f"Release #{release_id} files missing; reset download state",
        RESET_HINT,
    )


def reconcile_missing_release_files(
    db,
    downloads_dir: str,
    staging_dir: str,
    calibre_series: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Check COMPLETED releases; reset only when no files/Calibre/staging match exists."""
    downloads_index = _files_in_tree(downloads_dir)
    staging_index = _files_in_tree(staging_dir)
    checked = kept = reset = in_calibre = 0
    reset_releases: List[int] = []
    errors = 0
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM releases WHERE download_status = 'COMPLETED' "
            "AND COALESCE(status, '') NOT IN ('IN_CALIBRE', 'IGNORED', 'BLACKLISTED');"
        ).fetchall()
    for raw_row in rows:
        release = dict(raw_row)
        release_id = int(release.get("id") or 0)
        try:
            if str(release.get("download_status") or "").upper() in EXCLUDED_DOWNLOAD_STATUSES:
                continue
            if str(release.get("status") or "").upper() in EXCLUDED_STATUSES:
                continue
            checked += 1
            expected = [_basename(path) for path in _load_files(release.get("download_files_json"))]
            found = None
            for name in expected:
                if not name:
                    continue
                found = downloads_index.get(name.lower()) or staging_index.get(name.lower())
                if found:
                    break
            if found:
                kept += 1
                if not dry_run:
                    with db.get_connection() as conn:
                        conn.execute(
                            "UPDATE releases SET download_files_json = ?, updated_at = CURRENT_TIMESTAMP "
                            "WHERE id = ?;",
                            (json.dumps([found]), release_id),
                        )
                        conn.commit()
                continue

            matched = None
            if calibre_series:
                matched = match_manga_series_in_calibre(
                    str(release.get("series_name") or release.get("title") or ""), calibre_series,
                )
            if matched and _volumes_in_calibre(release, matched):
                kept += 1
                in_calibre += 1
                if not dry_run:
                    db.update_release_status(release_id, "IN_CALIBRE", enforce_precedence=True)
                continue

            staging_path = _staging_match(release, staging_index)
            if staging_path:
                kept += 1
                if not dry_run:
                    with db.get_connection() as conn:
                        conn.execute(
                            "UPDATE releases SET download_files_json = ?, updated_at = CURRENT_TIMESTAMP "
                            "WHERE id = ?;",
                            (json.dumps([staging_path]), release_id),
                        )
                        conn.commit()
                continue

            reset += 1
            reset_releases.append(release_id)
            if not dry_run:
                _reset_release(db, release_id)
        except Exception as exc:  # per-release isolation: one bad row cannot abort a scan
            errors += 1
            logger.warning("Reconcile failed for release %s: %s", release_id, exc)
    return {
        "checked": checked,
        "kept": kept,
        "reset": reset,
        "in_calibre": in_calibre,
        "reset_releases": reset_releases,
        "errors": errors,
        "dry_run": bool(dry_run),
    }
