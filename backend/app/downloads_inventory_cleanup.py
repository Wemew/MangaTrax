"""Raw downloads inventory cleanup (task-59).

Three explicit, server-revalidated actions for the Raw Downloads inventory:
* per-row delete of one currently listed entry;
* clear exact duplicate entries (same CBZ basename+size signature), keeping one;
* clear raw copies whose CBZs are all present in staging or provably owned in Calibre.

No action ever removes the only copy; every deletion is revalidated against a fresh audit
and constrained to the real downloads root without following symlinks.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app.downloads_inventory import _collect_files, _parse_names, _release_volumes, _summarize_files
from app.parser import extract_manga_bundle_and_volumes, match_manga_series_in_calibre, normalize_text
from app.staging_duplicates import _calibre_volumes

logger = logging.getLogger("mangatrax.inventory_cleanup")

STAGING_SUFFIXES = (".cbz", ".cbr", ".zip", ".epub")


def _load_entries(downloads_dir: str, db) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    try:
        templates = db.get_series_templates_map()
    except Exception:
        templates = {}
    try:
        releases = db.get_tracked_downloads(limit=500)
    except Exception:
        releases = []
    from app.downloads_inventory import scan_downloads
    inventory = scan_downloads(downloads_dir, series_templates=templates, releases=releases)
    return inventory, [dict(entry) for entry in inventory.get("entries") or []]


def _entry_cbz_files(root: str, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = os.path.join(root, str(entry.get("relative") or "").replace("/", os.sep))
    files: List[Dict[str, Any]] = []
    try:
        if os.path.isfile(path) and not os.path.islink(path):
            if str(entry.get("name") or "").lower().endswith(STAGING_SUFFIXES):
                files.append({"name": os.path.basename(path), "size": os.path.getsize(path)})
            return files
        for current, dirs, names in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(current, name))]
            for name in names:
                if not name.lower().endswith(STAGING_SUFFIXES):
                    continue
                full = os.path.join(current, name)
                if os.path.islink(full):
                    continue
                try:
                    files.append({"name": name, "size": os.path.getsize(full)})
                except OSError:
                    continue
    except OSError:
        return files
    return sorted(files, key=lambda item: (item["name"].lower(), item["size"]))


def _staging_index(staging_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    index: Dict[str, List[Dict[str, Any]]] = {}
    root = os.path.realpath(staging_dir or "")
    if not root or not os.path.isdir(root):
        return index
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in names:
            if not name.lower().endswith(STAGING_SUFFIXES):
                continue
            path = os.path.join(current, name)
            if os.path.islink(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            index.setdefault(name.lower(), []).append({"path": path, "size": size})
    return index


def _all_in_staging(cbz_files: List[Dict[str, Any]], staging: Dict[str, List[Dict[str, Any]]]) -> Tuple[bool, str]:
    if not cbz_files:
        return False, ""
    alternate = ""
    for item in cbz_files:
        candidates = staging.get(str(item["name"]).lower()) or []
        match = None
        for candidate in candidates:
            if not item["size"] or not candidate["size"] or abs(int(item["size"]) - int(candidate["size"])) <= max(1024, int(item["size"]) // 100):
                match = candidate
                break
        if not match:
            return False, ""
        alternate = alternate or match["path"]
    return True, alternate


def _calibre_match_for_files(cbz_files: List[Dict[str, Any]], calibre_series: Optional[list]) -> Tuple[bool, str, List[float]]:
    if not cbz_files or not calibre_series:
        return False, "", []
    all_volumes: List[float] = []
    calibre_name = ""
    for item in cbz_files:
        parsed = extract_manga_bundle_and_volumes(item["name"])
        series = str(parsed.get("series_name") or "").strip()
        volumes = [float(value) for value in (parsed.get("volumes") or [])]
        if not series or not volumes:
            return False, "", []
        matched = match_manga_series_in_calibre(series, calibre_series)
        if not matched:
            return False, "", []
        owned = _calibre_volumes(matched)
        if not owned or not set(volumes).issubset(owned):
            return False, "", []
        calibre_name = calibre_name or str(matched.get("name") or matched.get("series_name") or series)
        all_volumes.extend(volumes)
    return True, calibre_name, sorted(set(all_volumes))


def audit_inventory_cleanup(db, downloads_dir: str, staging_dir: str,
                            calibre_series: Optional[list] = None) -> Dict[str, Any]:
    inventory, entries = _load_entries(downloads_dir, db)
    root = os.path.realpath(downloads_dir or "")
    staging = _staging_index(staging_dir)
    candidates: List[Dict[str, Any]] = []

    signatures: Dict[Tuple, List[Dict[str, Any]]] = {}
    for entry in entries:
        files = _entry_cbz_files(root, entry)
        signature = tuple(sorted((str(item["name"]).lower(), int(item["size"])) for item in files))
        entry["_cbz_files"] = files
        entry["_signature"] = signature
        signatures.setdefault(signature, []).append(entry)

    duplicate_relatives = set()
    for signature, group in signatures.items():
        if not signature or len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: (0 if item.get("is_dir") else 1, str(item.get("relative") or "").lower()))
        kept = ordered[0]
        for entry in ordered[1:]:
            files = entry["_cbz_files"]
            candidates.append({
                "name": entry.get("name"),
                "relative": entry.get("relative"),
                "is_dir": bool(entry.get("is_dir")),
                "file_count": len(files),
                "size_bytes": sum(int(item["size"]) for item in files),
                "reason": "duplicate",
                "detail": f"Duplicate of {kept.get('relative')}",
                "alternate": kept.get("relative") or "",
                "release": entry.get("release"),
            })
            duplicate_relatives.add(entry.get("relative"))

    for entry in entries:
        if entry.get("relative") in duplicate_relatives:
            continue
        files = entry.get("_cbz_files") or []
        if not files:
            continue
        in_staging, alternate = _all_in_staging(files, staging)
        if in_staging:
            candidates.append({
                "name": entry.get("name"), "relative": entry.get("relative"),
                "is_dir": bool(entry.get("is_dir")), "file_count": len(files),
                "size_bytes": sum(int(item["size"]) for item in files),
                "reason": "in_staging", "detail": f"Already in staging: {os.path.basename(alternate)}",
                "alternate": alternate, "release": entry.get("release"),
            })
            continue
        owned, calibre_name, volumes = _calibre_match_for_files(files, calibre_series)
        if owned:
            candidates.append({
                "name": entry.get("name"), "relative": entry.get("relative"),
                "is_dir": bool(entry.get("is_dir")), "file_count": len(files),
                "size_bytes": sum(int(item["size"]) for item in files),
                "reason": "in_calibre",
                "detail": f"Already in Calibre: {calibre_name} v{', '.join(str(value) for value in volumes)}",
                "alternate": calibre_name, "calibre_volumes": volumes,
                "release": entry.get("release"),
            })

    for entry in entries:
        entry.pop("_cbz_files", None)
        entry.pop("_signature", None)
    duplicate_items = [item for item in candidates if item["reason"] == "duplicate"]
    present_items = [item for item in candidates if item["reason"] in ("in_staging", "in_calibre")]
    return {
        "status": "success",
        "entries": entries,
        "candidates": candidates,
        "duplicates": {
            "count": len(duplicate_items),
            "bytes": sum(item["size_bytes"] for item in duplicate_items),
            "candidates": duplicate_items,
        },
        "already_present": {
            "count": len(present_items),
            "bytes": sum(item["size_bytes"] for item in present_items),
            "candidates": present_items,
        },
        "totals": {"count": len(candidates), "bytes": sum(item["size_bytes"] for item in candidates)},
    }


def _resolve_entry(entry: Dict[str, Any], root: str) -> str:
    relative = str(entry.get("relative") or "").replace("\\", "/")
    parts = [part for part in relative.split("/") if part]
    if not parts or any(part == ".." for part in parts) or relative.startswith("/") or (len(relative) > 1 and relative[1] == ":"):
        raise ValueError("entry path is not downloads-relative")
    path = os.path.join(root, relative.replace("/", os.sep))
    if os.path.islink(path):
        raise ValueError("symlinked inventory entries are not accepted")
    real = os.path.realpath(path)
    if real == root or not real.startswith(root + os.sep):
        raise ValueError("entry escapes the downloads root")
    return real


def _delete_entry(path: str, root: str) -> Tuple[int, int]:
    """Delete a file or directory tree without following symlinks; returns (files, bytes)."""
    files = deleted_bytes = 0
    if os.path.islink(path):
        raise ValueError("symlinked inventory entries are not accepted")
    if os.path.isfile(path):
        size = os.path.getsize(path)
        os.remove(path)
        return 1, size
    if not os.path.isdir(path):
        return 0, 0
    for current, dirs, names in os.walk(path, topdown=False, followlinks=False):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(current, name))]
        for name in names:
            full = os.path.join(current, name)
            if os.path.islink(full):
                continue
            real = os.path.realpath(full)
            if not real.startswith(root + os.sep):
                continue
            try:
                size = os.path.getsize(full)
                os.remove(full)
                files += 1
                deleted_bytes += size
            except OSError:
                continue
        try:
            if os.path.realpath(current) != root:
                os.rmdir(current)
        except OSError:
            pass
    return files, deleted_bytes


def _prune_parents(path: str, root: str) -> None:
    parent = os.path.dirname(os.path.realpath(path))
    while parent and parent != root and parent.startswith(root + os.sep):
        try:
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)


def clear_inventory(db, downloads_dir: str, staging_dir: str, mode: str,
                    relatives: Optional[List[str]] = None, calibre_series: Optional[list] = None,
                    report=None) -> Dict[str, Any]:
    if mode not in ("duplicates", "already_present", "all"):
        raise ValueError("mode must be duplicates, already_present or all")
    root = os.path.realpath(downloads_dir or "")
    if not root or not os.path.isdir(root):
        raise ValueError("downloads_dir is unavailable")
    audit = audit_inventory_cleanup(db, root, staging_dir, calibre_series)
    if mode == "duplicates":
        approved = {item["relative"]: item for item in audit["duplicates"]["candidates"]}
    elif mode == "already_present":
        approved = {item["relative"]: item for item in audit["already_present"]["candidates"]}
    else:
        approved = {item["relative"]: item for item in audit["candidates"]}
    selected = []
    for relative in relatives or []:
        item = approved.get(str(relative).replace("\\", "/").lstrip("/"))
        if item:
            selected.append(item)
    if not relatives:
        selected = list(approved.values())
    total = len(selected)
    deleted = skipped = failed = 0
    bytes_freed = files_deleted = 0
    for index, item in enumerate(selected, 1):
        if report:
            report(index - 1, total, f"Deleting {item.get('relative')}…")
        relative = str(item.get("relative") or "")
        try:
            path = _resolve_entry(item, root)
            count, freed = _delete_entry(path, root)
            if not count and not os.path.exists(path):
                skipped += 1
                continue
            deleted += 1
            files_deleted += count
            bytes_freed += freed
            _prune_parents(path, root)
            db.log_activity(
                "DOWNLOAD_INVENTORY_DUPLICATE_CLEARED" if item["reason"] == "duplicate" else "DOWNLOAD_RAW_COPY_CLEARED",
                f"Cleared raw inventory entry '{relative}' ({item['reason']})",
                json.dumps({"relative": relative, "reason": item["reason"], "files": count, "bytes": freed,
                            "alternate": item.get("alternate") or ""}),
            )
        except Exception as exc:
            failed += 1
            logger.warning("Could not clear raw inventory entry %s: %s", relative, exc)
        if report:
            report(index, total, f"Processed {relative}")
    return {"status": "success", "mode": mode, "deleted": deleted, "files_deleted": files_deleted,
            "bytes_freed": bytes_freed, "skipped": skipped, "failed": failed,
            "remaining": len(audit["entries"]) - deleted}


def delete_inventory_entry(db, downloads_dir: str, relative: str) -> Dict[str, Any]:
    root = os.path.realpath(downloads_dir or "")
    if not root or not os.path.isdir(root):
        raise ValueError("downloads_dir is unavailable")
    inventory, entries = _load_entries(downloads_dir, db)
    match = next((entry for entry in entries if str(entry.get("relative")).replace("\\", "/") == str(relative).replace("\\", "/")), None)
    if not match:
        raise ValueError("entry is not in the current raw inventory")
    path = _resolve_entry(match, root)
    count, freed = _delete_entry(path, root)
    if not count and not os.path.exists(path):
        raise ValueError("entry no longer exists")
    _prune_parents(path, root)
    db.log_activity(
        "DOWNLOAD_INVENTORY_ENTRY_DELETED",
        f"Deleted raw inventory entry '{relative}'",
        json.dumps({"relative": relative, "files": count, "bytes": freed}),
    )
    from app.downloads_inventory import refresh_inventory
    refreshed = refresh_inventory(db, downloads_dir)
    return {"status": "success", "deleted": True, "relative": relative, "files": count,
            "bytes_freed": freed, "remaining_files": len(refreshed.get("entries") or [])}
