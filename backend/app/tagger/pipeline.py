"""Downloads routing pipeline (task-22).

Two-folder model:
  * ``DOWNLOADS_DIR`` — raw downloads from both engines; files sit here untouched.
  * ``STAGING_DIR`` — final destination, only reached after a successful tag.

The rule is series-level (from the user, supersedes the earlier import_dir idea):
  * series NOT in Calibre (no template) -> keep all of its volumes in DOWNLOADS_DIR;
  * series in Calibre -> tag each .cbz with ComicInfo.xml, then move it to
    STAGING_DIR preserving its relative subpath;
  * a file that cannot be tagged never moves.

Both directories are expected on the same drive, so moves prefer ``os.replace`` and
only fall back to copy+delete when ``st_dev`` differs (with a warning).
"""

import json
import logging
import os
import shutil
from typing import Any, Dict, List, Optional

from app.parser import (
    extract_manga_bundle_and_volumes,
    normalize_text,
    split_series_candidate_parts,
)
from app.tagger import cbz_tagger
from app import storage_health

logger = logging.getLogger("mangatrax.tagger.pipeline")

DEFAULT_DOWNLOADS_DIR = "/downloads"
_IGNORED_SUFFIXES = (".part", ".tmp", ".mangatrax-part", ".mangatrax-rewrite-tmp")


def _template_map(series_templates: Any) -> Dict[str, Dict[str, Any]]:
    """Accept either the tagger's normalised map or a list of series dicts."""
    if isinstance(series_templates, dict):
        return {
            str(key): value
            for key, value in series_templates.items()
            if isinstance(value, dict) and value.get("series_name")
        }
    mapping: Dict[str, Dict[str, Any]] = {}
    for item in series_templates or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("series_name") or item.get("name") or "").strip()
        if name:
            mapping[normalize_text(name)] = {
                "series_name": name,
                "authors": item.get("authors", ""),
                "publisher": item.get("publisher", ""),
                "tags": item.get("tags", ""),
            }
    return mapping


def resolve_series_template(series_name: str, series_templates: Any) -> Optional[Dict[str, Any]]:
    """Resolve a parsed series name against the Calibre templates.

    Mirrors the tagger's ``normalize_text`` lookup and additionally tries the
    dual-title candidate parts (``"A | B"``, ``"A (B)"``) so a release parsed under
    an alias still finds its Calibre series.
    """
    templates = _template_map(series_templates)
    if not templates:
        return None
    candidates: List[str] = []
    if series_name:
        candidates.append(series_name)
    try:
        candidates.extend(split_series_candidate_parts(series_name or ""))
    except Exception:
        pass
    for candidate in candidates:
        key = normalize_text(str(candidate))
        if key and key in templates:
            return templates[key]
    return None


def _volume_display(parsed: Dict[str, Any]) -> str:
    volume = parsed.get("volume_start")
    if parsed.get("is_bundle") and parsed.get("volume_end") and parsed.get("volume_end") != volume:
        return f"{int(parsed['volume_start'])}-{int(parsed['volume_end'])}"
    if volume:
        return str(int(volume)) if float(volume) == int(volume) else str(volume)
    return ""


def _build_metadata(template: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
    display = _volume_display(parsed)
    series_name = template.get("series_name") or parsed.get("series_name") or ""
    return {
        "title": f"{series_name} Vol. {display}" if display else series_name,
        "series_name": series_name,
        "volume": display or parsed.get("volume_start") or "",
        "authors": template.get("authors", ""),
        "publisher": template.get("publisher", ""),
        "tags": template.get("tags", ""),
    }


def scan_awaiting_series(downloads_dir: str, series_templates: Any) -> List[Dict[str, Any]]:
    """Read-only summary of .cbz files whose series is not in Calibre (for the UI)."""
    awaiting: Dict[str, Dict[str, Any]] = {}
    if not downloads_dir or not os.path.isdir(downloads_dir):
        return []
    for root, dirs, files in os.walk(downloads_dir):
        dirs.sort()
        for filename in sorted(files):
            if not filename.lower().endswith(".cbz"):
                continue
            if any(filename.lower().endswith(suffix) for suffix in _IGNORED_SUFFIXES):
                continue
            try:
                parsed = extract_manga_bundle_and_volumes(filename)
            except Exception:
                continue
            series_name = str(parsed.get("series_name") or "").strip() or filename
            template = resolve_series_template(series_name, series_templates)
            if template:
                continue
            key = normalize_text(series_name)
            bucket = awaiting.setdefault(key, {"series": series_name, "count": 0, "volumes": set()})
            bucket["count"] += 1
            display = _volume_display(parsed)
            if display:
                bucket["volumes"].add(display)
    result = []
    for bucket in sorted(awaiting.values(), key=lambda item: item["series"].lower()):
        result.append({
            "series": bucket["series"],
            "count": bucket["count"],
            "volumes": sorted(bucket["volumes"]),
        })
    return result


def _device_of(path: str) -> int:
    """Device id of *path* (kept separate so tests can simulate cross-device)."""
    return os.stat(path).st_dev


def _move_with_preference(source_path: str, dest_path: str) -> str:
    """Move *source_path* to *dest_path*; return ``rename`` or ``copy``.

    Same-device moves use ``os.replace`` (atomic rename).  Cross-device moves copy
    to a temporary sibling, atomically rename it into place and only then delete the
    source, warning because the deployment skipped the same-drive recommendation.
    """
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    if _device_of(source_path) == _device_of(os.path.dirname(dest_path) or "."):
        os.replace(source_path, dest_path)
        return "rename"
    logger.warning(
        "Cross-device move for '%s' (downloads_dir and staging_dir are on different "
        "devices; the same-drive recommendation was not met): copying instead of rename.",
        os.path.basename(source_path),
    )
    temp_path = f"{dest_path}.mangatrax-part"
    try:
        shutil.copy2(source_path, temp_path)
        os.replace(temp_path, dest_path)
        os.remove(source_path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
    return "copy"


def process_downloads_to_staging(
    downloads_dir: str,
    staging_dir: str,
    series_templates: Any,
    db,
    actor: str = "scheduler",
) -> Dict[str, Any]:
    """Tag and route raw downloads into staging; see the module docstring.

    Returns ``{scanned, tagged_and_moved, awaiting_calibre, failed,
    skipped_existing, series_awaiting}``.  Never raises for per-file problems: one
    corrupt archive cannot abort the pass.
    """
    result: Dict[str, Any] = {
        "scanned": 0,
        "tagged_and_moved": 0,
        "awaiting_calibre": 0,
        "failed": 0,
        "skipped_existing": 0,
        "series_awaiting": [],
    }
    if not downloads_dir or not os.path.isdir(downloads_dir):
        return result
    downloads_root = os.path.realpath(downloads_dir)
    staging_root = os.path.realpath(staging_dir)

    awaiting: Dict[str, Dict[str, Any]] = {}
    failed_files: List[str] = []

    for root, dirs, files in os.walk(downloads_dir):
        dirs.sort()
        for filename in sorted(files):
            if not filename.lower().endswith(".cbz"):
                continue
            if any(filename.lower().endswith(suffix) for suffix in _IGNORED_SUFFIXES):
                continue
            source_path = os.path.join(root, filename)
            result["scanned"] += 1
            try:
                if os.path.islink(source_path):
                    logger.warning("Skipping symlinked downloads file: %s", source_path)
                    result["failed"] += 1
                    failed_files.append(filename)
                    continue
                parsed = extract_manga_bundle_and_volumes(filename)
                series_name = str(parsed.get("series_name") or "").strip()
                template = resolve_series_template(series_name, series_templates)
                if not template:
                    key = normalize_text(series_name or filename)
                    bucket = awaiting.setdefault(
                        key, {"series": series_name or filename, "count": 0, "volumes": set()},
                    )
                    bucket["count"] += 1
                    display = _volume_display(parsed)
                    if display:
                        bucket["volumes"].add(display)
                    result["awaiting_calibre"] += 1
                    continue

                metadata = _build_metadata(template, parsed)
                # Validate readability BEFORE mutating: a corrupt .cbz must stay in
                # downloads untagged (zipfile's append mode can otherwise fabricate a
                # "successful" tag on garbage bytes).
                try:
                    existing_xml = cbz_tagger.read_comicinfo_xml(source_path)
                except cbz_tagger.UnreadableArchiveError as exc:
                    logger.warning("Unreadable archive '%s': %s", filename, exc)
                    result["failed"] += 1
                    failed_files.append(filename)
                    continue
                if existing_xml is None:
                    tagged = cbz_tagger.inject_comicinfo(source_path, metadata)
                    if not tagged or not cbz_tagger.has_comicinfo(source_path):
                        logger.warning("Tagging failed; leaving '%s' in downloads.", filename)
                        result["failed"] += 1
                        failed_files.append(filename)
                        continue

                relative = os.path.relpath(source_path, downloads_dir)
                dest_path = os.path.join(staging_dir, relative)
                dest_real = os.path.realpath(dest_path)
                if dest_real != staging_root and not dest_real.startswith(staging_root + os.sep):
                    logger.warning("Refusing move outside staging_dir: %s -> %s", source_path, dest_path)
                    result["failed"] += 1
                    failed_files.append(filename)
                    continue
                if os.path.exists(dest_path):
                    same_size = False
                    try:
                        same_size = os.path.getsize(source_path) == os.path.getsize(dest_path)
                    except OSError:
                        same_size = False
                    logger.warning(
                        "Destination already exists, skipping '%s'%s.",
                        filename, " (identical size)" if same_size else "",
                    )
                    result["skipped_existing"] += 1
                    continue

                move_kind = _move_with_preference(source_path, dest_path)
                result["tagged_and_moved"] += 1
                try:
                    db.log_activity(
                        "TAGGED_AND_MOVED_TO_STAGING",
                        f"Tagged and moved '{filename}' to staging for series "
                        f"'{metadata['series_name']}' Vol {metadata['volume']}",
                        json.dumps({"source": source_path, "destination": dest_path, "move": move_kind}),
                        actor=actor,
                    )
                except Exception as exc:  # pragma: no cover - audit must not break routing
                    logger.debug("Could not audit staged move: %s", exc)
            except Exception as exc:  # one corrupt archive must not abort the pass
                hint = ""
                try:
                    hint = storage_health.format_hint(staging_dir, exc)
                except Exception:  # diagnostics must never abort the pass either
                    hint = ""
                logger.warning(
                    "Failed to route '%s': %s%s",
                    source_path, exc, f"; {hint}" if hint else "",
                )
                result["failed"] += 1
                failed_files.append(filename)

    result["series_awaiting"] = [
        {
            "series": bucket["series"],
            "count": bucket["count"],
            "volumes": sorted(bucket["volumes"]),
        }
        for bucket in sorted(awaiting.values(), key=lambda item: item["series"].lower())
    ]
    if awaiting:
        signature = json.dumps(result["series_awaiting"], sort_keys=True)
        previous = ""
        try:
            previous = db.get_setting("downloads_awaiting_signature", "")
        except Exception:
            previous = ""
        if signature != previous:
            names = ", ".join(
                f"{bucket['series']} ({bucket['count']})"
                for bucket in sorted(awaiting.values(), key=lambda item: item["series"].lower())[:8]
            )
            try:
                db.log_activity(
                    "AWAITING_CALIBRE_SERIES",
                    f"{result['awaiting_calibre']} download(s) across {len(awaiting)} series await a "
                    f"Calibre series: {names}",
                    json.dumps(result["series_awaiting"]),
                    actor=actor,
                )
                db.set_setting("downloads_awaiting_signature", signature)
            except Exception as exc:  # pragma: no cover
                logger.debug("Could not audit awaiting series: %s", exc)
    if failed_files:
        signature = json.dumps(sorted(failed_files))
        previous = ""
        try:
            previous = db.get_setting("downloads_routing_failed_signature", "")
        except Exception:
            previous = ""
        if signature != previous:
            try:
                db.log_activity(
                    "DOWNLOADS_ROUTING_FAILED",
                    f"{len(failed_files)} download(s) could not be tagged/routed and stay in downloads.",
                    json.dumps(failed_files[:20]),
                    actor=actor,
                )
                db.set_setting("downloads_routing_failed_signature", signature)
            except Exception as exc:  # pragma: no cover
                logger.debug("Could not audit routing failures: %s", exc)
    if result["scanned"]:
        logger.info(
            "Downloads routing: scanned %s, moved %s, awaiting %s, failed %s, skipped %s.",
            result["scanned"], result["tagged_and_moved"], result["awaiting_calibre"],
            result["failed"], result["skipped_existing"],
        )
    return result
