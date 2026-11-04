"""Read-only ``<Series>`` audit / repair planning for staged CBZ files.

Why this module exists
----------------------
The naming contract (``series_tracking.name`` / ``releases.series_name``) is the
*stable matching key*: the Calibre library name when the series exists in
Calibre, otherwise the name the user typed.  AniList's canonical English /
romaji titles are display-only.

Before that split, the tagger inherited its metadata from the naming layer, so a
series that was stored under an AniList title got that display title baked into
``ComicInfo.xml`` (``<Series>Delicious in Dungeon</Series>`` instead of the
Calibre name ``Dungeon Meshi``).  Calibre matches an imported book to a series by
the ``<Series>`` value, so those files silently created a *second*, wrong series
in the library.  The rename bug is fixed for future tags, but files already on
disk stay wrong -- this module finds them.

Safety model (what is "safe to repair")
---------------------------------------
Repair is an unambiguous rewrite of one element; there is no guessing:

1. ``ok`` -- ``<Series>`` already equals a tracked/Calibre name exactly.
2. ``repairable`` -- the file can be proven to belong to exactly one known
   series, so rewriting ``<Series>`` is deterministic:
     * ``series_match == "normalized"``: the value matches a known name up to
       case/punctuation.  Calibre's name is the stable key, so adopting it is safe.
     * ``series_match == "anilist_alias"``: the value is an authoritative AniList
       alias (canonical/romaji, ``source == 'anilist'``) of exactly one tracked
       series.  That is the exact fingerprint of the old rename bug.
     * A file whose ``<Series>`` is already right but whose ``<Writer>`` /
       ``<Publisher>`` disagree with the tracked series metadata is also
       repairable (only those elements are rewritten).
3. ``ambiguous`` -- the value is an AniList alias claimed by more than one tracked
   series.  Two genuinely distinct series can share an alias, so no winner is
   picked; the file is reported and left untouched.
4. ``unknown_series`` -- the value matches no tracked/Calibre name and no
   authoritative alias.  The correct name cannot be derived, so it is left to
   manual tagging (``POST /api/tagger/manual-tag``) instead of a guess.
5. ``missing_series`` / ``untagged`` / ``invalid_comicinfo`` / ``unreadable`` --
   the Series value cannot be read reliably, so the audit offers no rewrite.  The
   manual-tagger (explicit, full-metadata write) is the supported path.

The audit itself never opens an archive for writing: it only lists the staging
directory and parses ComicInfo.xml.  Files are only modified by the explicit
repair action in ``main.py`` for the files the user named.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from app.parser import normalize_text
from app.tagger.naming import (
    format_authors,
    format_series,
    format_tags,
    format_title_for_volume_value,
    format_volume,
)
from app.tagger.cbz_tagger import (
    MalformedComicInfoError,
    UnreadableArchiveError,
    read_comicinfo,
    scan_staging_cbz_files,
)

logger = logging.getLogger("mangatrax.tagger.audit")

STATUS_OK = "ok"
STATUS_REPAIRABLE = "repairable"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNKNOWN_SERIES = "unknown_series"
STATUS_MISSING_SERIES = "missing_series"
STATUS_UNTAGGED = "untagged"
STATUS_INVALID_COMICINFO = "invalid_comicinfo"
STATUS_UNREADABLE = "unreadable"

ALL_STATUSES = (
    STATUS_OK,
    STATUS_REPAIRABLE,
    STATUS_AMBIGUOUS,
    STATUS_UNKNOWN_SERIES,
    STATUS_MISSING_SERIES,
    STATUS_UNTAGGED,
    STATUS_INVALID_COMICINFO,
    STATUS_UNREADABLE,
)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def build_series_reference(db, calibre_series: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build the known-series index used to judge a file's ``<Series>`` value.

    Returns ``{"names": {normalized: canonical}, "templates": {...}, "aliases":
    {normalized_alias: {canonical names claiming it}}}``.

    * ``names`` are the authoritative matching keys: the Calibre series names
      (live library preferred, then the synced ``series_tracking`` rows).
    * ``aliases`` come from the authoritative AniList metadata cache.  An alias
      claimed by two series is kept with both claims (and therefore classified as
      ambiguous rather than resolved).
    """
    from app.database import is_authoritative_metadata

    names: Dict[str, str] = {}
    templates: Dict[str, Dict[str, str]] = {}

    for row in db.get_all_series() or []:
        name = _clean(row.get("name"))
        norm = normalize_text(name)
        if not norm:
            continue
        names.setdefault(norm, name)
        templates.setdefault(norm, {
            "authors": _clean(row.get("authors")),
            "publisher": _clean(row.get("publisher")),
            "tags": _clean(row.get("tags")),
        })

    # A live Calibre scan is the most authoritative source: its spelling is the
    # stable matching key, so it overrides a synced/typed row of the same name.
    for series in calibre_series or []:
        name = _clean(series.get("series_name") or series.get("name"))
        norm = normalize_text(name)
        if not norm:
            continue
        names[norm] = name
        templates[norm] = {
            "authors": _clean(series.get("authors")),
            "publisher": _clean(series.get("publisher")),
            "tags": _clean(series.get("tags")),
        }

    aliases: Dict[str, Set[str]] = {}

    def _claim(alias: str, canonical: str) -> None:
        alias_norm = normalize_text(alias)
        stable_norm = normalize_text(canonical)
        if not alias_norm or alias_norm == stable_norm:
            return
        aliases.setdefault(alias_norm, set()).add(canonical)

    # Tracked series -> authoritative AniList alias sets (the same evidence the
    # re-key repair uses, so the two repair paths cannot disagree).
    try:
        alias_sets = db.get_authoritative_alias_sets() or {}
    except Exception as exc:  # pragma: no cover - audit must never crash the endpoint
        logger.debug("Could not load tracked alias sets for the CBZ audit: %s", exc)
        alias_sets = {}

    for stable_norm, alias_norms in alias_sets.items():
        canonical = names.get(stable_norm)
        if not canonical:
            continue
        for alias_norm in alias_norms or ():
            _claim(alias_norm, canonical)

    # Calibre-only series (scanned but not synced yet) have no tracked row, so
    # their aliases come straight from the authoritative metadata cache.
    try:
        cache_by_norm: Dict[str, Dict[str, Any]] = {}
        for row in (db.get_all_manga_metadata_cache() or {}).values():
            if not is_authoritative_metadata(row):
                continue
            for field in ("search_term", "canonical_title", "romaji_title"):
                alias_norm = normalize_text(_clean(row.get(field)))
                if alias_norm:
                    cache_by_norm.setdefault(alias_norm, row)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not load metadata cache for the CBZ audit: %s", exc)
        cache_by_norm = {}

    for stable_norm, canonical in names.items():
        meta = cache_by_norm.get(stable_norm)
        if not meta:
            continue
        for field in ("canonical_title", "romaji_title"):
            _claim(_clean(meta.get(field)), canonical)

    return {"names": names, "templates": templates, "aliases": aliases}


def classify_series_value(series_value: Optional[str], reference: Dict[str, Any]) -> Dict[str, Any]:
    """Classify one ``<Series>`` value against the known-series index.

    Returns ``{"status", "series_match", "suggested_series", "safe_to_repair",
    "reason"}``.  Only exact/normalized/alias-proven matches ever yield a
    suggestion; everything else is reported for manual handling.
    """
    names: Dict[str, str] = reference.get("names") or {}
    aliases: Dict[str, Set[str]] = reference.get("aliases") or {}

    raw = _clean(series_value)
    if not raw:
        return {
            "status": STATUS_MISSING_SERIES,
            "series_match": "none",
            "suggested_series": None,
            "safe_to_repair": False,
            "reason": "ComicInfo.xml has no <Series> value; use manual tagging or the staging auto-tagger.",
        }

    norm = normalize_text(raw)
    if not norm:
        return {
            "status": STATUS_UNKNOWN_SERIES,
            "series_match": "none",
            "suggested_series": None,
            "safe_to_repair": False,
            "reason": "the <Series> value has no comparable characters, so no known series can be derived.",
        }

    canonical = names.get(norm)
    if canonical is not None:
        if canonical == raw:
            return {
                "status": STATUS_OK,
                "series_match": "exact",
                "suggested_series": None,
                "safe_to_repair": False,
                "reason": "matches the tracked/Calibre series name exactly.",
            }
        return {
            "status": STATUS_REPAIRABLE,
            "series_match": "normalized",
            "suggested_series": canonical,
            "safe_to_repair": True,
            "reason": (
                f"'{raw}' differs from the tracked series name '{canonical}' only in "
                "case/punctuation; the tracked spelling is the Calibre matching key."
            ),
        }

    claims = sorted(aliases.get(norm) or ())
    if len(claims) == 1:
        canonical = claims[0]
        return {
            "status": STATUS_REPAIRABLE,
            "series_match": "anilist_alias",
            "suggested_series": canonical,
            "safe_to_repair": True,
            "reason": (
                f"'{raw}' is an authoritative AniList alias of exactly one tracked series "
                f"('{canonical}'); the old rename bug wrote AniList display titles into <Series>."
            ),
        }
    if len(claims) > 1:
        return {
            "status": STATUS_AMBIGUOUS,
            "series_match": "ambiguous_alias",
            "suggested_series": None,
            "safe_to_repair": False,
            "reason": (
                f"'{raw}' is an AniList alias claimed by {len(claims)} tracked series "
                f"({', '.join(claims)}); refusing to pick a winner."
            ),
        }

    return {
        "status": STATUS_UNKNOWN_SERIES,
        "series_match": "none",
        "suggested_series": None,
        "safe_to_repair": False,
        "reason": (
            f"'{raw}' is not a tracked/Calibre series name and not an authoritative AniList "
            "alias; track the series or tag the file manually first."
        ),
    }


def _metadata_changes(fields: Dict[str, str], template: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Elements whose value is provably wrong versus the tracked series metadata.

    An empty current value is never treated as wrong (the file may legitimately
    omit Writer/Publisher); only a non-empty value that disagrees with known,
    non-empty tracked metadata is rewritten.
    """
    template = template or {}
    changes: Dict[str, str] = {}
    known_writer = _clean(template.get("authors"))
    current_writer = _clean(fields.get("Writer"))
    if current_writer and known_writer and normalize_text(current_writer) != normalize_text(known_writer):
        changes["Writer"] = known_writer
    known_publisher = _clean(template.get("publisher"))
    current_publisher = _clean(fields.get("Publisher"))
    if current_publisher and known_publisher and normalize_text(current_publisher) != normalize_text(known_publisher):
        changes["Publisher"] = known_publisher
    return changes


def audit_entry(entry: Dict[str, Any], reference: Dict[str, Any]) -> Dict[str, Any]:
    """Audit one staging-scan entry; read-only and exception-safe per file."""
    record: Dict[str, Any] = {
        "file": entry.get("relative") or entry.get("name") or "",
        "name": entry.get("name") or "",
        "size_mb": entry.get("size_mb", 0.0),
        "volume": None,
        "status": STATUS_UNTAGGED,
        "series_match": "none",
        "current_series": None,
        "suggested_series": None,
        "current_writer": None,
        "suggested_writer": None,
        "current_publisher": None,
        "suggested_publisher": None,
        "current_title": None,
        "suggested_title": None,
        "current_penciller": None,
        "suggested_penciller": None,
        "current_genre": None,
        "suggested_genre": None,
        "current_number": None,
        "suggested_number": None,
        "current_volume": None,
        "suggested_volume": None,
        "changes": [],
        "safe_to_repair": False,
        "reason": "",
    }

    try:
        fields = read_comicinfo(entry["path"])
    except UnreadableArchiveError as exc:
        record["status"] = STATUS_UNREADABLE
        record["reason"] = str(exc)
        return record
    except MalformedComicInfoError as exc:
        record["status"] = STATUS_INVALID_COMICINFO
        record["reason"] = (
            f"{exc}; the Series value cannot be read reliably, so manual tagging is the "
            "supported repair (it rewrites the whole ComicInfo.xml)."
        )
        return record

    if fields is None:
        record["status"] = STATUS_UNTAGGED
        record["reason"] = "no ComicInfo.xml yet; the staging auto-tagger or manual tagging can create one."
        return record

    record["current_series"] = _clean(fields.get("Series")) or None
    record["volume"] = _clean(fields.get("Volume")) or _clean(fields.get("Number")) or None
    record["current_writer"] = _clean(fields.get("Writer")) or None
    record["current_publisher"] = _clean(fields.get("Publisher")) or None
    record["current_title"] = _clean(fields.get("Title")) or None
    record["current_penciller"] = _clean(fields.get("Penciller")) or None
    record["current_genre"] = _clean(fields.get("Genre")) or None
    record["current_number"] = _clean(fields.get("Number")) or None
    record["current_volume"] = _clean(fields.get("Volume")) or None

    classification = classify_series_value(record["current_series"], reference)
    record.update(classification)

    target = classification.get("suggested_series")
    if not target and record["current_series"]:
        target = (reference.get("names") or {}).get(normalize_text(record["current_series"]))

    if not target:
        return record

    template = (reference.get("templates") or {}).get(normalize_text(target))
    template = template or {}
    changes: List[str] = []
    reasons: List[str] = []
    if classification["status"] == STATUS_REPAIRABLE and classification.get("suggested_series"):
        changes.append("Series")

    def _consider(element: str, current: Optional[str], suggested: Optional[str], note: str) -> None:
        # Absent optional elements are not "wrong"; only format differences are repaired.
        if not current or suggested is None or current == suggested:
            return
        changes.append(element)
        record[f"suggested_{element.lower()}"] = suggested
        reasons.append(note)

    # Title: shared Calibre-compatible formatter; only unambiguous series are formatted.
    volume_value = _clean(fields.get("Volume")) or _clean(fields.get("Number"))
    suggested_title = format_title_for_volume_value(
        target, volume_value, fallback_name=entry.get("name") or entry.get("relative"),
    )
    if suggested_title:
        record["suggested_title"] = suggested_title
        if _clean(fields.get("Title")) != suggested_title:
            changes.append("Title")
            reasons.append(
                f"title uses {record['current_title']!r}; standard Calibre title is "
                f"{suggested_title!r} (Series vNN)"
            )

    # Author-matching columns: Calibre's display separator is " & ".
    expected_authors = format_authors(template.get("authors"))
    if expected_authors:
        _consider("Writer", record["current_writer"], expected_authors,
                  f"authors not Calibre ' & ' format ({record['current_writer']!r})")
        _consider("Penciller", record["current_penciller"],
                  format_authors(template.get("authors")),
                  f"penciller not Calibre ' & ' format ({record['current_penciller']!r})")

    # Publisher is an exact Calibre string.
    expected_publisher = str(template.get("publisher") or "").strip()
    if expected_publisher and record["current_publisher"]:
        if record["current_publisher"] != expected_publisher:
            changes.append("Publisher")
            record["suggested_publisher"] = expected_publisher
            reasons.append(f"publisher differs from Calibre ({record['current_publisher']!r})")

    # Tag-matching columns: Calibre displays tags comma-space separated.
    expected_genre = format_tags(template.get("tags"))
    if expected_genre:
        _consider("Genre", record["current_genre"], expected_genre,
                  f"tags not comma-space format ({record['current_genre']!r})")

    # Number/Volume mirror Calibre's series index display (integer/decimal; bundle
    # range kept as extra metadata).  Never guess a value that is not already present.
    for element in ("Number", "Volume"):
        current = record[f"current_{element.lower()}"]
        if not current:
            continue
        suggested = format_volume(current)
        if suggested and current != suggested:
            changes.append(element)
            record[f"suggested_{element.lower()}"] = suggested
            reasons.append(f"{element} not Calibre series-index format ({current!r} -> {suggested!r})")

    record["changes"] = changes
    record["safe_to_repair"] = bool(changes)
    if changes and classification["status"] == STATUS_OK:
        # Series is already the correct key; only inherited metadata is stale.
        record["status"] = STATUS_REPAIRABLE
    if reasons:
        record["reason"] = (record.get("reason") + " " + "; ".join(reasons)).strip() if record.get("reason") else "; ".join(reasons)
    return record


def plan_repair(
    record: Dict[str, Any],
    *,
    repair_writer: bool = True,
    repair_publisher: bool = True,
) -> Dict[str, Any]:
    """Turn an audit record into the exact ComicInfo element updates to apply.

    Returns ``{"updates": {...}, "changes": [...], "skipped_reason": str|None}``.
    A record that is not ``repairable``, or whose only differences are disabled by
    the caller's ``repair_writer`` / ``repair_publisher`` flags, yields no updates
    (and a human-readable skip reason) so the caller can never widen the write.
    """
    if record.get("status") != STATUS_REPAIRABLE or not record.get("safe_to_repair"):
        return {
            "updates": {},
            "changes": [],
            "skipped_reason": record.get("reason") or f"status is '{record.get('status')}', not repairable.",
        }

    updates: Dict[str, str] = {}
    changes: List[str] = []
    if "Series" in (record.get("changes") or []) and record.get("suggested_series"):
        updates["Series"] = record["suggested_series"]
        changes.append("Series")
    if repair_writer and "Writer" in (record.get("changes") or []) and record.get("suggested_writer"):
        updates["Writer"] = record["suggested_writer"]
        changes.append("Writer")
    if repair_publisher and "Publisher" in (record.get("changes") or []) and record.get("suggested_publisher"):
        updates["Publisher"] = record["suggested_publisher"]
        changes.append("Publisher")
    for element in ("Penciller", "Genre", "Number", "Volume"):
        if element in (record.get("changes") or []) and record.get(f"suggested_{element.lower()}"):
            updates[element] = record[f"suggested_{element.lower()}"]
            changes.append(element)
    if "Title" in (record.get("changes") or []) and record.get("suggested_title"):
        updates["Title"] = record["suggested_title"]
        changes.append("Title")

    if not updates:
        return {
            "updates": {},
            "changes": [],
            "skipped_reason": "the only differing fields are disabled for this request (repair_writer/repair_publisher).",
        }
    return {"updates": updates, "changes": changes, "skipped_reason": None}


def audit_staging_cbz_files(
    db,
    staging_dir: str,
    calibre_series: Optional[List[Dict[str, Any]]] = None,
    progress=None,
) -> Dict[str, Any]:
    """Audit every staged ``.cbz`` and return a per-file report plus summary.

    Strictly read-only: archives are opened in ``"r"`` mode only.  Nothing in
    this function can modify a file, which is what makes the audit safe to run
    automatically (e.g. from the downloads page) before the user selects files for
    the explicit repair action.
    """
    reference = build_series_reference(db, calibre_series=calibre_series)
    entries = scan_staging_cbz_files(staging_dir)

    summary: Dict[str, Any] = {"scanned": 0}
    for status in ALL_STATUSES:
        summary[status] = 0

    files: List[Dict[str, Any]] = []
    total_entries = len(entries)
    for index, entry in enumerate(entries, 1):
        summary["scanned"] += 1
        if progress:
            progress(index - 1, total_entries, f"Auditing {entry.get('name') or entry.get('relative') or ''}")
        record = audit_entry(entry, reference)
        summary[record["status"]] = summary.get(record["status"], 0) + 1
        files.append(record)

    summary["files_with_changes"] = sum(1 for record in files if record.get("changes"))
    summary["needs_attention"] = sum(
        summary.get(status, 0)
        for status in (STATUS_REPAIRABLE, STATUS_AMBIGUOUS, STATUS_UNKNOWN_SERIES)
    )
    summary["tracked_series"] = len(reference.get("names") or {})
    summary["alias_claims"] = len(reference.get("aliases") or {})
    if progress:
        progress(total_entries, total_entries, "ComicInfo audit complete")

    return {
        "status": "success",
        "staging_dir": staging_dir or "",
        "summary": summary,
        "files": files,
    }
