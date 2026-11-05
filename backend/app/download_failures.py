"""Failed document detection/list/retry/delete helpers (task-58).

Archives that cannot be read/tagged stay in the raw downloads folder.  This module
records them in ``download_failures`` (one row per downloads-relative path), detects the
real file format from magic bytes, matches a release by series+volume, and supports safe
delete/retry.
"""

from __future__ import annotations

import json
import logging
import os
import zipfile
from typing import Any, Dict, List, Optional

from app.parser import extract_manga_bundle_and_volumes, normalize_text
from app.staging_duplicates import _release_for

logger = logging.getLogger("mangatrax.download_failures")

MAGIC_TYPES = (
    (b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip"), (b"Rar!", "rar"), (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"%PDF-", "pdf"), (b"BOOKMOBI", "mobi"),
)
HTML_HINTS = (b"<!doctype", b"<html", b"<?xml", b"<head", b"<body")


def detect_document_type(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            head = handle.read(64)
    except OSError:
        return "unknown"
    for signature, label in MAGIC_TYPES:
        if head.startswith(signature):
            return label
    lowered = head.lower()
    if any(hint in lowered for hint in HTML_HINTS):
        return "html"
    return "unknown"


def is_valid_cbz(path: str) -> bool:
    """Cheap central-directory validation; never reads archive contents."""
    try:
        if not os.path.isfile(path) or not path.lower().endswith(".cbz"):
            return False
        return zipfile.is_zipfile(path)
    except (OSError, zipfile.BadZipFile):
        return False


def _relative_to(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def record_failure(db, downloads_dir: str, path: str, reason: str,
                   detected_type: Optional[str] = None) -> int:
    relative = _relative_to(downloads_dir, path)
    try:
        parsed = extract_manga_bundle_and_volumes(os.path.basename(path))
    except Exception:
        parsed = {}
    series = str(parsed.get("series_name") or "").strip()
    volumes = [float(value) for value in (parsed.get("volumes") or [])]
    release = _release_for(db, series, volumes) if series else None
    return db.record_download_failure(
        relative, reason, detected_type or detect_document_type(path), series, volumes,
        int(release.get("id")) if release else None,
    )


def record_pipeline_failure(db, downloads_dir: str, path: str, reason: str) -> None:
    try:
        record_failure(db, downloads_dir, path, reason)
    except Exception as exc:  # failure recording must never break routing
        logger.debug("Could not record download failure for %s: %s", path, exc)


def clear_pipeline_success(db, downloads_dir: str, path: str) -> None:
    try:
        db.clear_download_failure(_relative_to(downloads_dir, path))
    except Exception as exc:
        logger.debug("Could not clear download failure for %s: %s", path, exc)


def scan_failures(db, downloads_dir: str) -> Dict[str, Any]:
    """Walk .cbz files, record non-ZIP archives, clear now-valid records."""
    checked = failed = cleared = 0
    if not downloads_dir or not os.path.isdir(downloads_dir):
        return {"checked": 0, "failed": 0, "cleared": 0}
    root = os.path.realpath(downloads_dir)
    for current, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in names:
            if not name.lower().endswith(".cbz"):
                continue
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            checked += 1
            if is_valid_cbz(path):
                cleared += 1
                try:
                    db.clear_download_failure(_relative_to(root, path))
                except Exception:
                    pass
                continue
            detected = detect_document_type(path)
            reason = "File is not a zip file"
            if detected not in ("unknown", "zip"):
                reason = f"File is not a zip file (detected {detected.upper()})"
            try:
                record_failure(db, root, path, reason, detected)
                failed += 1
            except Exception as exc:
                logger.debug("Could not record scan failure for %s: %s", path, exc)
    return {"checked": checked, "failed": failed, "cleared": cleared}


def serialize_failure(db, row: Dict[str, Any], downloads_dir: str) -> Dict[str, Any]:
    relative = str(row.get("file_relative") or "")
    path = os.path.join(downloads_dir, relative.replace("/", os.sep)) if downloads_dir else ""
    try:
        size = os.path.getsize(path) if path and os.path.isfile(path) else 0
    except OSError:
        size = 0
    try:
        volumes = json.loads(row.get("volumes_json") or "[]")
    except (TypeError, ValueError):
        volumes = []
    release = None
    release_id = row.get("release_id")
    if release_id:
        try:
            release = db.get_release_by_id(int(release_id))
        except Exception:
            release = None
    if not release:
        series = str(row.get("series_name") or "")
        matched = _release_for(db, series, volumes) if series else None
        if matched:
            release = matched
            try:
                with db.get_connection() as conn:
                    conn.execute(
                        "UPDATE download_failures SET release_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                        (int(matched.get("id")), int(row["id"])),
                    )
                    conn.commit()
            except Exception:
                pass
    status = str((release or {}).get("status") or "").upper()
    url = str((release or {}).get("download_url") or (release or {}).get("link") or "").strip()
    can_retry = bool(release and url and status not in ("IN_CALIBRE", "IGNORED", "BLACKLISTED"))
    if release and not can_retry:
        retry_reason = (
            "matched release is in Calibre/ignored" if status in ("IN_CALIBRE", "IGNORED", "BLACKLISTED")
            else "matched release has no download URL"
        )
    elif not release:
        retry_reason = "no matching release"
    else:
        retry_reason = ""
    return {
        "id": int(row["id"]),
        "file_relative": relative,
        "name": os.path.basename(relative),
        "size": size,
        "reason": row.get("reason") or "",
        "detected_type": row.get("detected_type") or "unknown",
        "series_name": row.get("series_name") or "",
        "volumes": volumes,
        "attempts": int(row.get("attempts") or 0),
        "status": row.get("status") or "failed",
        "release_id": int((release or {}).get("id") or 0) or None,
        "release_title": (release or {}).get("title") or "",
        "release_status": (release or {}).get("status") or "",
        "release_volumes": (release or {}).get("volumes") or [],
        "can_retry": can_retry,
        "retry_reason": retry_reason,
        "created_at": row.get("created_at") or "",
    }


def list_failures(db, downloads_dir: str) -> List[Dict[str, Any]]:
    return [serialize_failure(db, row, downloads_dir) for row in db.list_download_failures()]
