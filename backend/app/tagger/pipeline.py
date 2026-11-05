"""Downloads routing pipeline (task-22).

Two-folder model:
  * ``DOWNLOADS_DIR`` — raw downloads from both engines; files sit here untouched.
  * ``STAGING_DIR`` — final destination, only reached after a successful tag.

The rule is series-level (from the user, supersedes the earlier import_dir idea):
  * series NOT in Calibre (no template) -> keep all of its volumes in DOWNLOADS_DIR;
  * series in Calibre -> tag each .cbz with ComicInfo.xml, then move it to the
    FLAT root of STAGING_DIR (``staging/<basename>``; collisions get ``(2)`` names);
  * a file that cannot be tagged never moves.

Both directories are expected on the same drive, so moves prefer ``os.replace`` and
only fall back to copy+delete when ``st_dev`` differs (with a warning).  Downloads stay
per-torrent; staging is deliberately flat and ``flatten_staging_dir()`` migrates any
legacy nested staging tree.
"""

import json
import logging
import os
import difflib
import re
import shutil
import time
from typing import Any, Dict, List, Optional

from app.parser import (
    extract_manga_bundle_and_volumes,
    normalize_text,
    split_series_candidate_parts,
)
from app.tagger import cbz_tagger
from app.tagger.naming import format_authors, format_comicinfo_title, format_tags, format_volume
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


_TAG_GROUP_RE = re.compile(r"[\[{][^\]}]*[\]}]")
_FUZZY_MIN_RATIO = 0.86
_FUZZY_AMBIGUITY_MARGIN = 0.05


def _clean_template_key(series_name: str) -> str:
    """Matching-only key: brace tags, paren/bracket tag groups, trailing ranges."""
    from app.parser import clean_series_for_matching

    cleaned = clean_series_for_matching(series_name)
    cleaned = _TAG_GROUP_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:,|./")
    return normalize_text(cleaned)


def resolve_series_template(series_name: str, series_templates: Any) -> Optional[Dict[str, Any]]:
    """Resolve a parsed series name against templates + user alias overrides.

    Precedence: exact normalized key (includes task-42 override keys merged by
    ``get_series_templates_map``) -> cleaned key (brace/paren/bracket tags and
    trailing range clauses stripped) -> unique fuzzy match (difflib ratio >= 0.86,
    rejected when two candidates are within 0.05 of each other).
    """
    templates = _template_map(series_templates)
    if not templates:
        return None
    candidates: List[str] = []
    if series_name:
        candidates.append(series_name)
    try:
        from app.parser import series_name_candidates
        candidates.extend(series_name_candidates(series_name or ""))
    except Exception:
        pass
    try:
        candidates.extend(split_series_candidate_parts(series_name or ""))
    except Exception:
        pass
    for candidate in candidates:
        key = normalize_text(str(candidate))
        if key and key in templates:
            return templates[key]
    try:
        from app.parser import clean_series_for_matching
        preserved_cleaned = normalize_text(clean_series_for_matching(series_name or ""))
    except Exception:
        preserved_cleaned = ""
    if preserved_cleaned and preserved_cleaned in templates:
        logger.info(
            "Series template resolved '%s' to tracked series '%s' (cleaned).",
            series_name, templates[preserved_cleaned]["series_name"],
        )
        return templates[preserved_cleaned]
    cleaned_key = _clean_template_key(series_name or "")
    if cleaned_key and cleaned_key in templates:
        logger.info(
            "Series template resolved '%s' to tracked series '%s' (cleaned).",
            series_name, templates[cleaned_key]["series_name"],
        )
        return templates[cleaned_key]
    if not cleaned_key:
        return None
    def _first_token(value: str) -> str:
        tokens = re.split(r"[^a-z0-9]+", (value or "").lower())
        return next((token for token in tokens if token), "")

    source_first = _first_token(cleaned_key)
    scored = sorted(
        (
            (difflib.SequenceMatcher(None, cleaned_key, key).ratio(), key)
            for key in templates
            if not source_first or _first_token(key) == source_first
        ),
        reverse=True,
    )
    if not scored:
        return None
    best_ratio, best_key = scored[0]
    if best_ratio < _FUZZY_MIN_RATIO:
        return None
    if len(scored) > 1 and best_ratio - scored[1][0] < _FUZZY_AMBIGUITY_MARGIN:
        logger.info(
            "Series template fuzzy match for '%s' is ambiguous (%s vs %s); leaving it awaiting.",
            series_name, templates[best_key]["series_name"], templates[scored[1][1]]["series_name"],
        )
        return None
    logger.info(
        "Series template resolved '%s' to tracked series '%s' (fuzzy %.2f).",
        series_name, templates[best_key]["series_name"], best_ratio,
    )
    return templates[best_key]


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
        "title": format_comicinfo_title(
            series_name,
            volumes=parsed.get("volumes"),
            volume=parsed.get("volume_start"),
            volume_start=parsed.get("volume_start"),
            volume_end=parsed.get("volume_end"),
            is_bundle=bool(parsed.get("is_bundle")),
            fallback_name=parsed.get("raw_title") or parsed.get("clean_title") or "",
        ),
        "series_name": series_name,
        "volume": format_volume(display or parsed.get("volume_start") or ""),
        "authors": format_authors(template.get("authors")),
        "publisher": template.get("publisher", ""),
        "tags": format_tags(template.get("tags")),
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
            bucket = awaiting.setdefault(
                key, {"series": series_name, "count": 0, "volumes": set(), "files": []},
            )
            bucket["count"] += 1
            try:
                size_mb = round(os.path.getsize(os.path.join(root, filename)) / (1024 * 1024), 2)
            except OSError:
                size_mb = 0.0
            bucket["files"].append({
                "relative": os.path.relpath(os.path.join(root, filename), downloads_dir).replace(os.sep, "/"),
                "name": filename,
                "size_mb": size_mb,
            })
            display = _volume_display(parsed)
            if display:
                bucket["volumes"].add(display)
    result = []
    for bucket in sorted(awaiting.values(), key=lambda item: item["series"].lower()):
        result.append({
            "series": bucket["series"],
            "count": bucket["count"],
            "volumes": sorted(bucket["volumes"]),
            "files": sorted(bucket["files"], key=lambda item: item["relative"].lower()),
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


def _safe_target_dirname(title: str) -> str:
    """Mirror the scheduler's safe torrent folder name for busy-dir detection."""
    cleaned = re.sub(r"[^\w\s.()\[\]-]+", "_", str(title or "")).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:120] or "torbox-download")


def _busy_download_dirnames(db: Any) -> Optional[set]:
    """Target folder names of releases currently DOWNLOADING (None = lookup unusable)."""
    if db is None:
        return None
    try:
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT title FROM releases WHERE download_status = 'DOWNLOADING';")
            rows = cur.fetchall()
    except Exception:
        return None
    return {_safe_target_dirname(row["title"]) for row in rows if row["title"]}


def prune_empty_dirs(
    root: str,
    db: Any = None,
    busy_names: Any = None,
    min_age_seconds: float = 60.0,
    label: str = "downloads",
    actor: str = "scheduler",
) -> Dict[str, Any]:
    """Bottom-up rmdir of EMPTY directories under *root*.  Never rmtree/symlink-follow.

    Keeps any directory that still has entries (hidden files, .part remnants, junk),
    skips directories belonging to an in-flight download or touched in the last
    ``min_age_seconds``, and refuses to leave the configured root.  Emits one
    ``DOWNLOADS_PRUNED`` audit/log line per pass when anything was removed.
    """
    result: Dict[str, Any] = {"removed": 0, "dirs": [], "skipped_busy": 0}
    if not root:
        return result
    try:
        root_real = os.path.realpath(root)
    except OSError:
        return result
    if not os.path.isdir(root_real):
        return result
    use_age_fallback = False
    if busy_names is not None:
        busy = set(busy_names)
    else:
        looked_up = _busy_download_dirnames(db)
        if looked_up is None:
            # Ambiguous/no DB: fall back to a ~60 s mtime guard so an in-flight
            # download's target directory is never pruned just because it is empty
            # for a moment.
            busy = set()
            use_age_fallback = True
        else:
            busy = looked_up
    removed: List[str] = []
    skipped_busy = 0
    now = time.time()
    try:
        for dirpath, dirnames, _filenames in os.walk(root_real, topdown=False, followlinks=False):
            current = os.path.realpath(dirpath)
            if current == root_real:
                continue
            if not current.startswith(root_real + os.sep):
                continue  # containment guard
            if os.path.islink(dirpath):
                continue  # never traverse/delete symlinked dirs
            name = os.path.basename(dirpath)
            if name in busy:
                skipped_busy += 1
                logger.debug("Prune: keeping busy download dir '%s'", dirpath)
                continue
            try:
                with os.scandir(dirpath) as iterator:
                    has_entries = any(True for _ in iterator)
            except OSError:
                continue
            if has_entries:
                logger.debug("Prune: keeping non-empty dir '%s'", dirpath)
                continue
            try:
                mtime = os.stat(dirpath).st_mtime
            except OSError:
                continue
            if use_age_fallback and min_age_seconds and (now - mtime) < min_age_seconds:
                skipped_busy += 1
                logger.debug("Prune: keeping recently modified dir '%s'", dirpath)
                continue
            try:
                os.rmdir(dirpath)
            except OSError as exc:
                logger.debug("Prune: could not remove '%s': %s", dirpath, exc)
                continue
            removed.append(os.path.relpath(dirpath, root_real))
    except OSError as exc:
        logger.debug("Prune walk failed for '%s': %s", root_real, exc)
    result.update({"removed": len(removed), "dirs": sorted(removed), "skipped_busy": skipped_busy})
    if removed:
        capped = sorted(removed)[:20]
        logger.info(
            "Downloads prune (%s): removed %s empty folder(s): %s%s",
            label, len(removed), ", ".join(capped),
            " …" if len(removed) > len(capped) else "",
        )
        try:
            if db is not None:
                db.log_activity(
                    "DOWNLOADS_PRUNED",
                    f"Pruned {len(removed)} empty folder(s) from {label}",
                    json.dumps({"dirs": capped, "removed": len(removed), "skipped_busy": skipped_busy}),
                    actor=actor,
                )
        except Exception as exc:  # pragma: no cover - audit must not break routing
            logger.debug("Could not audit downloads prune: %s", exc)
    return result


def _safe_staging_basename(filename: str) -> str:
    """Basename only; no path separators/control chars (staging is flat)."""
    base = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    base = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', "_", base).strip().strip(".")
    return base or "file.cbz"


def _staging_collision_path(staging_dir: str, filename: str, source_path: str) -> tuple:
    """Resolve flat-staging destination + collision status.

    Returns ``(dest_path, status)`` where status is ``new``, ``skip`` (same name and
    same size -> already staged) or ``renamed`` (same name, different size -> ``(2)``).
    """
    basename = _safe_staging_basename(filename)
    dest = os.path.join(staging_dir, basename)
    if not os.path.exists(dest):
        return dest, "new"
    same_size = False
    try:
        same_size = os.path.getsize(source_path) == os.path.getsize(dest)
    except OSError:
        same_size = False
    if same_size:
        return dest, "skip"
    stem, ext = os.path.splitext(basename)
    counter = 2
    while True:
        candidate = os.path.join(staging_dir, f"{stem} ({counter}){ext}")
        if not os.path.exists(candidate):
            return candidate, "renamed"
        counter += 1


def _is_temp_staging_file(filename: str) -> bool:
    lower = str(filename or "").lower()
    return lower.endswith(".part") or lower.endswith(".mangatrax-part")


def flatten_staging_dir(staging_dir: str, db: Any = None) -> Dict[str, Any]:
    """Move every file in nested staging subdirs to the flat staging root.

    Collisions follow the routing policy (same size -> skip, different size ->
    ``"<stem> (2)<ext>"``). Temp files (``*.part`` / ``*.mangatrax-part``) are left
    untouched, symlinks are never followed/removed, and emptied dirs are pruned with
    the task-34 safe helper.
    """
    stats: Dict[str, Any] = {
        "flattened": 0, "skipped_existing": 0, "conflicts_renamed": 0, "dirs_removed": 0,
    }
    if not staging_dir:
        return stats
    root = os.path.realpath(staging_dir)
    if not os.path.isdir(root):
        return stats
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = os.path.realpath(dirpath)
        if current == root:
            continue
        if not current.startswith(root + os.sep):
            continue
        dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
        for filename in sorted(filenames):
            if _is_temp_staging_file(filename):
                continue
            source = os.path.join(dirpath, filename)
            if os.path.islink(source) or not os.path.isfile(source):
                continue
            try:
                dest, status = _staging_collision_path(root, filename, source)
                if status == "skip":
                    stats["skipped_existing"] += 1
                    # Flat staging: the identical-size duplicate is already staged at the
                    # root, so drop the nested copy (never overwrite the root file).
                    try:
                        os.remove(source)
                    except OSError as exc:
                        logger.debug("Flatten staging: could not remove duplicate '%s': %s", source, exc)
                    logger.debug("Flatten staging: dropped duplicate '%s' (same size)", source)
                    continue
                dest_real = os.path.realpath(dest)
                if dest_real != root and not dest_real.startswith(root + os.sep):
                    logger.warning("Refusing flatten outside staging_dir: %s -> %s", source, dest)
                    continue
                _move_with_preference(source, dest)
                stats["flattened"] += 1
                if status == "renamed":
                    stats["conflicts_renamed"] += 1
                    logger.info("Flatten staging: collision, moved '%s' to '%s'", filename, os.path.basename(dest))
            except Exception as exc:  # one bad file must not abort the flatten
                logger.warning("Flatten staging failed for '%s': %s", source, exc)
    pruned = prune_empty_dirs(root, db=None, busy_names=set(), label="staging", actor="user")
    stats["dirs_removed"] = int(pruned.get("removed") or 0)
    if stats["flattened"]:
        logger.info(
            "Flattened %s file(s) from staging subfolders (%s renamed, %s dirs removed).",
            stats["flattened"], stats["conflicts_renamed"], stats["dirs_removed"],
        )
    return stats


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
        "conflicts_renamed": 0,
        "series_awaiting": [],
        "flattened": 0,
        "flatten_skipped_existing": 0,
        "flatten_conflicts_renamed": 0,
        "flatten_dirs_removed": 0,
    }

    def _merge_flatten(stats: Dict[str, Any]) -> None:
        result["flattened"] += int(stats.get("flattened") or 0)
        result["flatten_skipped_existing"] += int(stats.get("skipped_existing") or 0)
        result["flatten_conflicts_renamed"] += int(stats.get("conflicts_renamed") or 0)
        result["flatten_dirs_removed"] += int(stats.get("dirs_removed") or 0)

    # Legacy nested staging trees are flattened before and after routing (cheap:
    # flatten exits immediately when staging is already flat).
    if staging_dir:
        _merge_flatten(flatten_staging_dir(staging_dir, db=db))
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

                # Staging is FLAT: always the sanitised basename directly in the root.
                dest_path, collision = _staging_collision_path(staging_dir, filename, source_path)
                dest_real = os.path.realpath(dest_path)
                if dest_real != staging_root and not dest_real.startswith(staging_root + os.sep):
                    logger.warning("Refusing move outside staging_dir: %s -> %s", source_path, dest_path)
                    result["failed"] += 1
                    failed_files.append(filename)
                    continue
                if collision == "skip":
                    logger.debug(
                        "Staging already has '%s' with the same size; skipping.",
                        os.path.basename(dest_path),
                    )
                    result["skipped_existing"] += 1
                    continue
                if collision == "renamed":
                    logger.info(
                        "Staging collision for '%s' (different size); using '%s'.",
                        filename, os.path.basename(dest_path),
                    )
                    result["conflicts_renamed"] = int(result.get("conflicts_renamed") or 0) + 1

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

    if staging_dir:
        _merge_flatten(flatten_staging_dir(staging_dir, db=db))

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
    prune = prune_empty_dirs(downloads_dir, db=db, actor=actor, label="downloads")
    result.update({
        "removed": prune["removed"],
        "pruned_count": prune["removed"],
        "pruned_dirs": prune["dirs"],
        "skipped_busy": prune["skipped_busy"],
    })
    if result["scanned"]:
        logger.info(
            "Downloads routing: scanned %s, moved %s, awaiting %s, failed %s, skipped %s.",
            result["scanned"], result["tagged_and_moved"], result["awaiting_calibre"],
            result["failed"], result["skipped_existing"],
        )
    return result


def prune_empty_folders(
    downloads_dir: str,
    staging_dir: Optional[str] = None,
    db: Any = None,
    include_staging: bool = False,
    actor: str = "scheduler",
) -> Dict[str, Any]:
    """Manual prune action: empty download dirs (+ optional staging dirs) only."""
    downloads = prune_empty_dirs(downloads_dir, db=db, actor=actor, label="downloads")
    staging: Dict[str, Any] = {"removed": 0, "dirs": [], "skipped_busy": 0}
    if include_staging and staging_dir:
        staging = prune_empty_dirs(staging_dir, db=None, busy_names=set(), actor=actor, label="staging")
    return {
        "removed": int(downloads["removed"]) + int(staging["removed"]),
        "dirs": list(downloads["dirs"]) + list(staging["dirs"]),
        "skipped_busy": int(downloads["skipped_busy"]),
        "downloads": downloads,
        "staging": staging,
    }
