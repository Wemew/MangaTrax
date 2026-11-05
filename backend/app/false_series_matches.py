"""Audit/repair releases attributed by false fuzzy series matches (task-64)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.parser import (
    extract_manga_bundle_and_volumes,
    match_manga_series_in_calibre,
    normalize_text,
)

logger = logging.getLogger("mangatrax.false_series")


def _parsed_series(title: str) -> str:
    try:
        return str(extract_manga_bundle_and_volumes(title or "").get("series_name") or "").strip()
    except Exception:
        return ""


def audit_false_series_matches(db, calibre_series: Optional[List[Dict[str, Any]]] = None,
                              limit: int = 2000) -> Dict[str, Any]:
    """Find stored series names that the fixed matcher would no longer map."""
    calibre_series = calibre_series or []
    calibre_norms = {
        normalize_text(str(item.get("series_name") or item.get("name") or ""))
        for item in calibre_series
    }
    calibre_norms.discard("")
    with db.get_connection() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM releases ORDER BY id DESC LIMIT ?;", (max(1, int(limit)),),
        ).fetchall()]
    for row in rows:
        try:
            row["volumes"] = json.loads(row.get("volumes_json") or "[]")
            row["missing_volumes"] = json.loads(row.get("missing_vols_json") or "[]")
        except (TypeError, ValueError):
            row["volumes"] = []
            row["missing_volumes"] = []
    candidates = []
    for row in rows:
        title = str(row.get("title") or "")
        stored = str(row.get("series_name") or "").strip()
        parsed = _parsed_series(title)
        if not stored or not parsed or normalize_text(stored) == normalize_text(parsed):
            continue
        fixed = None
        if calibre_series:
            fixed = match_manga_series_in_calibre(parsed, calibre_series)
        fixed_name = str((fixed or {}).get("series_name") or (fixed or {}).get("name") or "").strip()
        if fixed_name and normalize_text(fixed_name) == normalize_text(stored):
            continue
        if fixed_name:
            # Parsed title maps to a different real series; still suspicious but the
            # repair target should be that fixed match, not the raw parsed title.
            target = fixed_name
        else:
            target = parsed
            # Only flag when the stored series is itself a known Calibre series (the
            # false match source); otherwise the difference may just be a custom name.
            if calibre_norms and normalize_text(stored) not in calibre_norms:
                continue
        candidates.append({
            "release_id": row.get("id"),
            "title": title,
            "stored_series": stored,
            "parsed_series": parsed,
            "suggested_series": target,
            "status": str(row.get("status") or ""),
            "missing_volumes": row.get("missing_volumes") or [],
            "can_repair": str(row.get("status") or "").upper() not in ("IGNORED", "BLACKLISTED"),
            "reason": (
                f"Stored '{stored}' no longer matches title series '{parsed}' "
                "under distinctive-head fuzzy rules"
            ),
        })
    return {"status": "success", "candidates": candidates, "count": len(candidates)}


def repair_false_series_matches(db, release_ids: List[int],
                                calibre_series: Optional[List[Dict[str, Any]]] = None,
                                progress=None) -> Dict[str, Any]:
    ids = [int(value) for value in (release_ids or [])]
    repaired = failed = skipped = 0
    details = []
    for index, release_id in enumerate(ids, 1):
        if progress:
            progress(index - 1, len(ids), f"Repairing release #{release_id}")
        try:
            release = db.get_release_by_id(release_id)
            if not release:
                skipped += 1
                continue
            title = str(release.get("title") or "")
            parsed = _parsed_series(title)
            if not parsed:
                skipped += 1
                continue
            old_status = str(release.get("status") or "")
            if old_status.upper() in ("IGNORED", "BLACKLISTED"):
                # Preserve user vetoes; still correct the attribution.
                new_status = old_status
                missing = release.get("missing_volumes") or []
            else:
                matched = match_manga_series_in_calibre(parsed, calibre_series or [])
                if matched:
                    new_status = "IN_CALIBRE" if not (release.get("missing_volumes") or []) else "REQUESTED"
                    missing = release.get("missing_volumes") or []
                else:
                    volumes = release.get("volumes") or []
                    missing = volumes
                    if volumes == [1.0] or not volumes or release.get("is_complete") or release.get("is_bundle"):
                        new_status = "DISCOVERY"
                    else:
                        new_status = "NEW"
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE releases SET series_name = ?, status = ?, missing_vols_json = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (parsed, new_status, json.dumps(missing), release_id),
                )
                conn.commit()
            repaired += 1
            details.append({
                "release_id": release_id, "from": release.get("series_name"),
                "to": parsed, "status": new_status,
            })
            try:
                db.log_activity(
                    "FALSE_SERIES_MATCH_REPAIRED",
                    f"Re-keyed release #{release_id} '{release.get('series_name')}' → '{parsed}'",
                    json.dumps({"title": title, "status": new_status}),
                )
            except Exception:
                pass
        except Exception as exc:
            failed += 1
            details.append({"release_id": release_id, "error": str(exc)[:300]})
        if progress:
            progress(index, len(ids), f"Processed release #{release_id}")
    try:
        db._invalidate_grouped_cache()
    except Exception:
        pass
    return {"status": "success", "repaired": repaired, "failed": failed,
            "skipped": skipped, "details": details}
