"""Staging Directory grouping by series + master retag (task-63)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from app.parser import extract_manga_bundle_and_volumes, normalize_text
from app.tagger import cbz_tagger
from app import volume_naming

logger = logging.getLogger("mangatrax.staging_groups")


def build_staging_groups(
    staging_dir: str, owner_labels: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Group staged CBZs by owner folder and ComicInfo <Series>.

    When ``owner_labels`` maps a top-level staging folder name to a user label, files
    below that folder get ``owner``/``owner_label`` and are grouped per owner, so an
    admin scanning the whole staging root can separate their own files from other
    users' downloads. Files at the root belong to the legacy/shared owner (``""``).
    """
    groups: Dict[str, Dict[str, Any]] = {}
    if not staging_dir or not os.path.isdir(staging_dir):
        return []
    root = os.path.realpath(staging_dir)
    labels = {
        str(folder).strip().lower(): str(label).strip()
        for folder, label in (owner_labels or {}).items()
        if str(folder).strip()
    }

    def _owner_of(relative: str) -> str:
        candidate = str(relative or "").replace("\\", "/")
        if "/" not in candidate:
            return ""
        head = candidate.split("/", 1)[0].strip().lower()
        return head if head in labels else ""

    def _owner_label(owner: str) -> str:
        if not owner:
            return "Shared / legacy"
        return labels.get(owner, owner)
    previous_index = volume_naming.build_previous_volume_index(root)
    for entry in cbz_tagger.scan_staging_cbz_files(root):
        path = entry["path"]
        series = ""
        tags: Dict[str, str] = {}
        try:
            tags = cbz_tagger.read_comicinfo(path) or {}
            series = str(tags.get("Series") or "").strip()
        except Exception:
            tags = {}
        parsed = {}
        try:
            parsed = extract_manga_bundle_and_volumes(entry["name"])
        except Exception:
            parsed = {}
        if not series:
            series = str(parsed.get("series_name") or "").strip() or "Unknown Series"
        owner = _owner_of(entry.get("relative") or "")
        owner_label = _owner_label(owner)
        series_key = normalize_text(series) or series.lower()
        key = f"{owner}|{series_key}" if owner else series_key
        group = groups.setdefault(key, {
            "series": series, "source_series": series, "files": [],
            "owner": owner, "owner_label": owner_label,
            "count": 0, "tagged": 0, "untagged": 0, "volumes": set(), "size_bytes": 0,
            "needs_intervention": 0,
        })
        assessment = volume_naming.assess_volume_naming(entry["name"], parsed, tags.get("Title") or "")
        previous, suggested_filename, suggested_title = volume_naming.inherit_for_file(previous_index, parsed)
        if previous and not suggested_filename and not assessment.get("needs_intervention"):
            assessment["needs_intervention"] = True
            assessment["reason"] = "Previous volume template has no replaceable volume marker"
        volumes = []
        for value in (parsed.get("volumes") or tags.get("Volume") or []):
            try:
                volumes.append(float(value))
            except (TypeError, ValueError):
                continue
        for value in volumes:
            group["volumes"].add(value)
        group["files"].append({
            "name": entry["name"],
            "relative": entry["relative"],
            "size_bytes": entry["size_bytes"],
            "size_mb": entry["size_mb"],
            "tagged": bool(tags),
            "comic_series": series,
            "comic_title": str(tags.get("Title") or ""),
            "volumes": sorted(volumes),
            "parsed_series": assessment.get("parsed_series") or "",
            "parsed_volume": assessment.get("parsed_volume"),
            "needs_intervention": bool(assessment.get("needs_intervention")),
            "intervention_reason": assessment.get("reason") or "",
            "suggested_volume": assessment.get("suggested_volume") or "",
            "suggested_filename": suggested_filename or "",
            "suggested_title": suggested_title or "",
            "previous_filename": (previous or {}).get("filename") or "",
            "owner": owner,
            "owner_label": owner_label,
        })
        group["count"] += 1
        group["tagged"] += 1 if tags else 0
        group["untagged"] += 0 if tags else 1
        group["needs_intervention"] += 1 if assessment.get("needs_intervention") else 0
        group["size_bytes"] += int(entry["size_bytes"] or 0)
    result = []
    for group in groups.values():
        group["volumes"] = sorted(group["volumes"])
        group["files"].sort(key=lambda item: (item["volumes"] or [999999], item["relative"].lower()))
        # A series with an Omnibus release cannot also have a v01/v02 file without a
        # decision: flag the other volumes for review and suggest the volume marker.
        def _is_omnibus_file(item: Dict[str, Any]) -> bool:
            # The marker is a trailing "Omnibus" word, never the series name itself
            # (a series may legitimately be called "Omnibus Test").
            title = str(item.get("comic_title") or "").strip().lower()
            if title:
                return title.endswith("omnibus")
            stem = os.path.splitext(str(item.get("name") or ""))[0].strip().lower()
            return stem.endswith("omnibus")

        omnibus_files = [item for item in group["files"] if _is_omnibus_file(item)]
        if omnibus_files:
            from app.tagger.naming import format_comicinfo_title

            # An Omnibus is intentionally volume-less: the generic "unparseable
            # volume" intervention does not apply to the omnibus file itself.
            for item in omnibus_files:
                reason = str(item.get("intervention_reason") or "")
                if item.get("needs_intervention") and "volume" in reason.lower():
                    item["needs_intervention"] = False
                    item["intervention_reason"] = ""
                    group["needs_intervention"] = max(0, group["needs_intervention"] - 1)

            for item in group["files"]:
                if item in omnibus_files:
                    continue
                was_flagged = bool(item.get("needs_intervention"))
                item["needs_intervention"] = True
                omnibus_reason = (
                    "Series already has an Omnibus release; confirm this volume's numbering"
                )
                existing = str(item.get("intervention_reason") or "").strip()
                item["intervention_reason"] = (
                    f"{existing}; {omnibus_reason}" if existing and omnibus_reason not in existing
                    else omnibus_reason
                )
                volume = (item.get("volumes") or [None])[0]
                if volume is not None and not re.search(r"\bv\d", item.get("comic_title") or "", re.IGNORECASE):
                    item["suggested_title"] = format_comicinfo_title(group["series"], volume=volume)
                if not was_flagged:
                    group["needs_intervention"] += 1
        result.append(group)
    result.sort(key=lambda item: (item.get("owner") or "", item["series"].lower()))
    return result


def retag_staging_group(db, staging_dir: str, source_series: str,
                        target_series: str, calibre_series: Optional[Dict[str, Any]] = None,
                        owner: Optional[str] = None,
                        owner_labels: Optional[Dict[str, str]] = None,
                        actor: str = "user", reporter=None) -> Dict[str, Any]:
    """Rewrite ComicInfo <Series> for one staging group, then link/merge/alias.

    ``owner`` scopes the retag to one owner folder (``""`` = shared/root, a folder
    name = that user) so an admin matching another user's group never retags the
    same series staged by somebody else.
    """
    root = os.path.realpath(staging_dir or "")
    if not root or not os.path.isdir(root):
        raise ValueError("staging_dir is unavailable")
    target = str(target_series or "").strip()
    if not target:
        raise ValueError("target series name is required")
    groups = build_staging_groups(root, owner_labels=owner_labels)
    wanted_owner = str(owner or "").strip().lower()
    group = next(
        (item for item in groups
         if normalize_text(item["series"]) == normalize_text(source_series)
         and str(item.get("owner") or "") == wanted_owner),
        None,
    )
    if not group:
        raise ValueError(f"staging series '{source_series}' not found")
    files = group.get("files") or []
    tagged = failed = skipped = 0
    errors = []
    total = len(files)
    for index, item in enumerate(files, 1):
        if reporter:
            reporter(index - 1, total, f"Retagging {item['name']} → {target}")
        try:
            entry = next(
                (entry for entry in cbz_tagger.scan_staging_cbz_files(root)
                 if entry["relative"] == item["relative"]),
                None,
            )
            if not entry:
                skipped += 1
                continue
            path = entry["path"]
            if os.path.islink(path) or not os.path.isfile(path):
                skipped += 1
                continue
            real = os.path.realpath(path)
            if not real.startswith(root + os.sep):
                skipped += 1
                continue
            existing_xml = cbz_tagger.read_comicinfo_xml(path)
            xml = cbz_tagger.build_comicinfo_xml(existing_xml, {"Series": target})
            if not cbz_tagger.rewrite_comicinfo(path, xml):
                failed += 1
                errors.append({"file": item["name"], "error": "atomic rewrite failed"})
                continue
            tagged += 1
        except Exception as exc:
            failed += 1
            errors.append({"file": item.get("name"), "error": str(exc)[:300]})
        if reporter:
            reporter(index, total, f"Processed {item['name']}")
    # Alias + tracker link/merge + release re-keying (task-62 helpers).
    linked = None
    try:
        db.add_series_alias_override(source_series, target,
                                     (calibre_series or {}).get("series_id") or (calibre_series or {}).get("id"),
                                     actor=actor)
    except Exception:
        pass
    try:
        tracker = next(
            (row for row in db.get_all_series()
             if normalize_text(str(row.get("name") or "")) == normalize_text(source_series)),
            None,
        )
        if tracker:
            linked = db.link_tracked_series(
                tracker["name"], calibre_series or {"series_name": target}, actor=actor,
            )
        else:
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE releases SET series_name = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE lower(trim(series_name)) = lower(trim(?));",
                    (target, source_series),
                )
                conn.commit()
    except Exception as exc:
        errors.append({"file": "<link>", "error": str(exc)[:300]})
    try:
        from app.staged_ownership import invalidate_staged_ownership_cache
        invalidate_staged_ownership_cache()
    except Exception:
        pass
    try:
        db._invalidate_grouped_cache()
    except Exception:
        pass
    return {
        "status": "success", "source": source_series, "target": target,
        "matched": tagged, "tagged": tagged, "failed": failed, "skipped": skipped,
        "errors": errors, "files": total, "linked": linked,
    }
