import os
import re
import sqlite3
import json
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple, Set

import logging

from app.security import escape_like
from app.nyaa_source import DEFAULT_NYAA_BASE_URL, NyaaBaseURLError, normalize_nyaa_base_url

logger = logging.getLogger("mangatrax.database")

DEFAULT_AUDIT_ACTOR = "system"

# The auto-seeded built-in feed.  These exact values identify the stock row that the
# mirror migration may repoint; user-created feeds are never touched.
SEEDED_NYAA_FEED_NAME = "Manga Releases (RSS)"
SEEDED_NYAA_FEED_URL = "https://nyaa.si/?page=rss&q=manga+cbz&c=3_1&f=0"


def _resolve_audit_actor(explicit: Optional[str] = None) -> str:
    """Resolve the audit actor: explicit value, else the request ContextVar.

    Imported lazily to avoid an import cycle (``app.auth`` never imports the
    database module, but the database is imported very early by tests/CLI tools).
    """
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    try:
        from app.auth import get_current_actor

        return get_current_actor()
    except Exception:  # pragma: no cover - auth module always importable
        return DEFAULT_AUDIT_ACTOR


def _like_contains(term: str) -> str:
    """LIKE pattern with SQL wildcards escaped (clauses must add ESCAPE '\\')."""
    return f"%{escape_like(str(term).lower())}%"


# ---------------------------------------------------------------------------
# Release media typing (D1: non-destructive replacement of the startup purge)
# ---------------------------------------------------------------------------
# MangaTrax tracks manga.  A release can additionally be typed as one of the
# other media classes, derived from the *title only* (torrent verification / D2
# is deliberately out of scope).  The rules that keep typing safe:
#
# * typing NEVER deletes a row and NEVER writes ``status`` -- the resolve_status()
#   precedence model stays the single owner of status transitions;
# * only rows still typed ``unknown`` are ever (re)typed, so human/library
#   decisions and previously typed rows survive;
# * ``unknown`` means "inconclusive" and is therefore always visible in the
#   tracking views.  Silence is not evidence of non-manga.
MEDIA_TYPE_MANGA = "manga"
MEDIA_TYPE_LIGHT_NOVEL = "light_novel"
MEDIA_TYPE_CHAPTER_ONLY = "chapter_only"
MEDIA_TYPE_NOVEL = "novel"
MEDIA_TYPE_AUDIOBOOK = "audiobook"
# JPEG XL releases are discarded by the feed filters and typed so they stay hidden
# from the manga views (retained rows can be inspected with the media filter off).
MEDIA_TYPE_JPEGXL = "jpegxl"
MEDIA_TYPE_UNKNOWN = "unknown"
MEDIA_TYPES = (
    MEDIA_TYPE_MANGA,
    MEDIA_TYPE_LIGHT_NOVEL,
    MEDIA_TYPE_CHAPTER_ONLY,
    MEDIA_TYPE_NOVEL,
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_JPEGXL,
    MEDIA_TYPE_UNKNOWN,
)
# Types hidden from the manga tracking views (they are retained in the DB and can
# be inspected/purged explicitly -- never deleted automatically).
NON_MANGA_MEDIA_TYPES = (
    MEDIA_TYPE_LIGHT_NOVEL,
    MEDIA_TYPE_CHAPTER_ONLY,
    MEDIA_TYPE_NOVEL,
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_JPEGXL,
)


def _parse_volume_list(raw: Any) -> List[float]:
    """Parse a JSON volume list column, returning [] for missing/invalid data."""
    if not raw:
        return []
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(values, (list, tuple)):
        return []
    parsed: List[float] = []
    for value in values:
        try:
            parsed.append(float(value))
        except (TypeError, ValueError):
            continue
    return parsed


def tracked_media_clause(alias: str = "") -> str:
    """SQL predicate: keep ``manga`` and inconclusive ``unknown`` rows visible.

    ``alias`` is the table alias used by the enclosing query (e.g. ``"r"``), so
    the clause reads ``COALESCE(r.media_type, 'unknown') NOT IN (...types)``.
    Both the grouped and flat tracked views (Releases, Discovery, wishlist /
    pending requests, status counters, prioritized-releases tab, search) use
    this single predicate; there must be no second definition of the filter.
    """
    prefix = f"{alias}." if alias else ""
    types = ", ".join(f"'{t}'" for t in NON_MANGA_MEDIA_TYPES)
    return f"COALESCE({prefix}media_type, 'unknown') NOT IN ({types})"


# ---------------------------------------------------------------------------
# Multi-group release tags (task-11)
# ---------------------------------------------------------------------------
# A release can carry several scanlation groups in one tag: "(1r0n, Ushi)",
# "(1r0n & Ushi)", "[1r0n] [Ushi]".  The full set is persisted in
# ``releases.release_groups_json`` next to the primary ``release_group``; every
# prioritized-group comparison is SET MEMBERSHIP ("any stored group is a
# prioritized group"), never whole-string equality.

def release_groups_of(rel: Dict[str, Any]) -> List[str]:
    """Return the release's group set (validated, deduplicated, primary first).

    Reads ``release_groups_json`` when present and falls back to splitting the
    legacy ``release_group`` value (legacy rows / direct SQL inserts) so both
    the in-memory and SQL paths agree on the set.
    """
    if not isinstance(rel, dict):
        return []
    # In-memory payloads (scheduler rel_dict, webhook) may already carry the list.
    explicit = rel.get("release_groups")
    if isinstance(explicit, list):
        groups = []
        for value in explicit:
            name = str(value or "").strip()
            if name and name not in groups:
                groups.append(name)
        if groups:
            return groups
    raw = rel.get("release_groups_json")
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            groups: List[str] = []
            for value in parsed:
                name = str(value or "").strip()
                if name and name not in groups:
                    groups.append(name)
            if groups:
                return groups
    from app.parser import split_release_group_value

    groups = split_release_group_value(rel.get("release_group") or "")
    if not groups:
        primary = str(rel.get("release_group") or "").strip()
        if primary:
            groups = [primary]
    return groups


def release_prioritized_priority(rel: Dict[str, Any], priority_map: Dict[str, int]) -> int:
    """Highest configured priority among the release's groups (0 = not prioritized).

    A release tagged ``(1r0n, Ushi)`` is prioritized when EITHER group is a
    prioritized group; the user's higher-ranked matching group wins.
    """
    if not priority_map:
        return 0
    best = 0
    for group in release_groups_of(rel):
        priority = priority_map.get(group.lower(), 0)
        if priority > best:
            best = priority
    return best


def release_is_prioritized(rel: Dict[str, Any], priority_map: Dict[str, int]) -> bool:
    """True when any of the release's groups is a prioritized group."""
    return release_prioritized_priority(rel, priority_map) > 0


def release_dedupe_signature(rel: Dict[str, Any]):
    """Dedupe signature: guid, else (link/download, volumes, sorted group set)."""
    return rel.get("guid") or (
        rel.get("download_url") or rel.get("link") or rel.get("title"),
        tuple(sorted(rel.get("volumes") or [])),
        tuple(sorted(group.lower() for group in release_groups_of(rel))),
    )


def prioritized_release_clause(alias: str = "") -> str:
    """SQL predicate: any of the release's groups is a prioritized group.

    Uses ``json_each(release_groups_json)`` (set membership) and keeps a legacy
    whole-group fallback for rows inserted directly via SQL before the backfill
    ran, so a joint release can never silently fall out of the VIP views.
    ``alias`` is the releases table alias in the enclosing query (e.g. ``"r"``).
    """
    prefix = f"{alias}." if alias else ""
    group_col = f"{prefix}release_group"
    json_col = f"{prefix}release_groups_json"
    return (
        f"(lower(COALESCE({group_col}, '')) IN (SELECT lower(name) FROM prioritized_groups)"
        f" OR EXISTS (SELECT 1 FROM json_each(COALESCE(NULLIF({json_col}, ''), '[]')) AS jg"
        f" JOIN prioritized_groups pg ON lower(jg.value) = lower(pg.name)))"
    )


# ---------------------------------------------------------------------------
# Release status precedence model
# ---------------------------------------------------------------------------
# Release statuses fall into two families:
#
# * RECOMPUTABLE (``DISCOVERY``, ``NEW``): a background feed poll / Calibre sync
#   owns these.  Automation may freely recompute them while the stored value is
#   also recomputable (or unset).
# * STICKY (``REQUESTED``, ``IN_CALIBRE``, ``IGNORED``, ``BLACKLISTED``): they
#   encode a decision that a poll classification must never undo.
#
# Within the sticky family two sub-rules apply:
# * the USER-VETO tier (``IGNORED``, ``BLACKLISTED``) is absolute for automated
#   writes -- only an explicit user action (status endpoint, unignore,
#   unblacklist, re-track) can lift it;
# * ``REQUESTED`` and ``IN_CALIBRE`` may transition into each other because both
#   are derived from Calibre ownership reconciliation, not from feed
#   classification (Feature 8: DISCOVERY/NEW -> REQUESTED when a tracked series
#   newly appears in Calibre; REQUESTED -> IN_CALIBRE when the missing volumes
#   are acquired; and the reverse when a full-series release turns out to be
#   missing volumes).
#
# Deliberate API transitions deliberately do NOT go through this model: they
# call ``MangaDatabase.update_release_status()`` without ``enforce_precedence``
# so a user can always override a sticky state (e.g. IGNORED -> REQUESTED via
# POST /api/releases/{id}/status or /unignore).
STICKY_STATUSES = frozenset({"REQUESTED", "IGNORED", "BLACKLISTED", "IN_CALIBRE"})
RECOMPUTABLE_STATUSES = frozenset({"DISCOVERY", "NEW"})
USER_VETO_STATUSES = frozenset({"IGNORED", "BLACKLISTED"})

# Higher rank = stronger; automation may only move a status to an equal or
# higher rank (except that the user-veto tier is immutable, see resolve_status).
_STATUS_RANK = {
    "DISCOVERY": 0,
    "NEW": 0,
    "REQUESTED": 1,
    "IN_CALIBRE": 1,
    "IGNORED": 2,
    "BLACKLISTED": 2,
}


def normalize_status(value: Optional[str]) -> Optional[str]:
    """Canonicalises a status value: ``None``/blank -> ``None``, else stripped upper-case."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def resolve_status(existing: Optional[str], incoming: Optional[str]) -> Optional[str]:
    """Single source of truth for every *automated* release-status write.

    ``existing`` is the status currently stored in the database, ``incoming`` is
    the status that automation (feed poll, Calibre sync, complete-series
    expansion) just computed.

    Rules:
      1. An incoming blank/``None`` status never clears an existing status.
      2. An unset existing status adopts the incoming status.
      3. The user-veto tier (``IGNORED``, ``BLACKLISTED``) is never changed by
         automation -- only an explicit user action may lift it (see
         ``update_release_status``/``remove_from_blacklist``).
      4. Otherwise the higher-ranked status wins, so a sticky status is never
         downgraded to a recomputable classification (``DISCOVERY``/``NEW`` ->
         keeps ``REQUESTED``/``IN_CALIBRE``), while recomputable statuses remain
         fully automation-owned and ``REQUESTED`` <-> ``IN_CALIBRE`` ownership
         reconciliation still works.
    """
    old = normalize_status(existing)
    new = normalize_status(incoming)
    if new is None:
        return old
    if old is None:
        return new
    if old in USER_VETO_STATUSES:
        return old
    if _STATUS_RANK.get(new, 0) >= _STATUS_RANK.get(old, 0):
        return new
    return old


# ---------------------------------------------------------------------------
# Naming model: STABLE MATCHING KEY vs. DISPLAY TITLE
# ---------------------------------------------------------------------------
# A series has exactly two names, with different jobs:
#
# * The STABLE MATCHING KEY lives in ``series_tracking.name`` and
#   ``releases.series_name``.  It is the Calibre library name when the series
#   exists in Calibre, otherwise the name the user typed when they tracked the
#   series.  Matching, grouping, dedupe and ComicInfo tagging key off it, so it
#   must never be rewritten by external metadata (that was the P3a rename bug:
#   ``Dungeon Meshi`` was rewritten to ``Delicious in Dungeon`` and Calibre
#   matching broke).
#
# * The DISPLAY TITLE is cosmetic.  It is AniList's canonical English title when
#   ``manga_metadata_cache`` holds an authoritative entry (``source == 'anilist'``
#   and a non-empty ``canonical_title``), otherwise it falls back to the stable
#   key.  It is resolved at render/group time and is NEVER fed back into a
#   matching lookup.
AUTHORITATIVE_METADATA_SOURCE = "anilist"


def is_authoritative_metadata(meta: Optional[Dict[str, Any]]) -> bool:
    """True when cached metadata may drive the cosmetic display title.

    Only AniList-sourced rows with a non-empty canonical title qualify.  Fallback
    / cleaned rows merely echo the raw search term and must not be treated as a
    translation (they would otherwise make display look authoritative when the
    service was simply offline).
    """
    if not meta:
        return False
    return (
        (meta.get("source") or "") == AUTHORITATIVE_METADATA_SOURCE
        and bool((meta.get("canonical_title") or "").strip())
    )


def display_title_from_meta(stable_key: str, meta: Optional[Dict[str, Any]]) -> str:
    """Resolve the cosmetic display title for a stable matching key.

    ``meta`` is the cached metadata row for the series (if any).  Display is the
    AniList canonical English title when authoritative, else the stable key.
    This helper must never be used to derive a matching key.
    """
    stable = (stable_key or "").strip()
    if is_authoritative_metadata(meta):
        return meta["canonical_title"].strip()
    return stable


class MangaDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._grouped_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        db_dir = os.path.dirname(os.path.abspath(db_path))
        try:
            os.makedirs(db_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating database directory '{db_dir}': {e}")
        self.init_db()

    def _invalidate_grouped_cache(self):
        try:
            self._grouped_cache.clear()
        except Exception:
            pass

    def get_connection(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
        except sqlite3.OperationalError as e:
            uid = getattr(os, "getuid", lambda: "N/A")()
            gid = getattr(os, "getgid", lambda: "N/A")()
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            dir_stat = ""
            try:
                st = os.stat(db_dir)
                dir_stat = f"Directory permissions: mode={oct(st.st_mode)}, uid={st.st_uid}, gid={st.st_gid}"
            except Exception:
                pass
            raise sqlite3.OperationalError(
                f"Failed to open/create database file '{self.db_path}'. {dir_stat}. "
                f"Running as UID={uid}, GID={gid}. "
                f"Please ensure the data directory on your host is writable by UID {uid} "
                f"(e.g. 'chmod -R 777 <host_folder>' on TrueNAS) or configure PUID/PGID without 'user:' in Docker."
            ) from e

        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode = DELETE;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self):
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()

            # Manga series tracking (from Calibre)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS series_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calibre_series_id INTEGER,
                    name TEXT UNIQUE NOT NULL,
                    authors TEXT DEFAULT '',
                    publisher TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    book_count INTEGER DEFAULT 0,
                    latest_index REAL DEFAULT 0.0,
                    is_monitored INTEGER DEFAULT 1,
                    website_url TEXT DEFAULT '',
                    owned_volumes_json TEXT DEFAULT '[]',
                    last_sync TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Add description and owned_volumes_json columns if missing on upgrade
            cur.execute("PRAGMA table_info(series_tracking);")
            cols = [r["name"] for r in cur.fetchall()]
            if "description" not in cols:
                cur.execute("ALTER TABLE series_tracking ADD COLUMN description TEXT DEFAULT '';")
            if "owned_volumes_json" not in cols:
                cur.execute("ALTER TABLE series_tracking ADD COLUMN owned_volumes_json TEXT DEFAULT '[]';")

            # Blacklisted manga series
            cur.execute("""
                CREATE TABLE IF NOT EXISTS series_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    reason TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Releases & Bundles table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guid TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    series_name TEXT,
                    release_group TEXT DEFAULT '',
                    release_groups_json TEXT DEFAULT '[]',
                    description TEXT DEFAULT '',
                    mangaupdates_url TEXT DEFAULT '',
                    is_bundle INTEGER DEFAULT 0,
                    volume_start REAL,
                    volume_end REAL,
                    volumes_json TEXT DEFAULT '[]',
                    missing_vols_json TEXT DEFAULT '[]',
                    download_url TEXT,
                    link TEXT,
                    cover_url TEXT,
                    source_feed_name TEXT,
                    status TEXT DEFAULT 'REQUESTED',
                    download_status TEXT DEFAULT 'IDLE',
                    torrent_hash TEXT,
                    media_type TEXT DEFAULT 'unknown',
                    revision INTEGER,
                    publisher TEXT DEFAULT '',
                    inferred_volumes INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Add release_group, description, mangaupdates_url, is_complete columns if missing on upgrade
            cur.execute("PRAGMA table_info(releases);")
            r_cols = [r["name"] for r in cur.fetchall()]
            if "release_group" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN release_group TEXT DEFAULT '';")
            # Full multi-group set (task-11): JSON list next to the primary group.
            if "release_groups_json" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN release_groups_json TEXT DEFAULT '[]';")
            if "description" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN description TEXT DEFAULT '';")
            if "mangaupdates_url" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN mangaupdates_url TEXT DEFAULT '';")
            if "is_complete" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN is_complete INTEGER DEFAULT 0;")
            # D1: media typing. Values: manga|light_novel|chapter_only|novel|audiobook|unknown.
            # Rows are TYPED instead of deleted; the tracked views filter non-manga types
            # out and 'unknown' deliberately stays visible (inconclusive means kept).
            if "media_type" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN media_type TEXT DEFAULT 'unknown';")
            # Re-release/fix revision ("(f)" = 1, "(f2)" = 2) and the publisher of an
            # official release (a quality signal, surfaced not filtered).
            if "revision" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN revision INTEGER;")
            if "publisher" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN publisher TEXT DEFAULT '';")
            # Full volume set inferred from metadata for a collected batch (no explicit
            # volume markers in the title): marks the release as an inferred full set.
            if "inferred_volumes" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN inferred_volumes INTEGER DEFAULT 0;")
            # task-20: download engine provenance.  qbit releases keep using
            # torrent_hash; Torbox releases persist their torrent_id in downloader_id.
            if "downloader" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN downloader TEXT DEFAULT '';")
            if "downloader_id" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN downloader_id TEXT DEFAULT '';")
            # Needed for the hard timeout (6h default); not in the plan's column list,
            # but a timeout cannot be computed without knowing when submission began.
            if "downloader_started_at" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN downloader_started_at TEXT DEFAULT '';")
            # Effective engine selection (auto-downloads may exclude volumes already
            # in flight from another release; Torbox polls this exact wanted set).
            if "download_wanted_json" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN download_wanted_json TEXT DEFAULT '[]';")
            # User-visible failure reason + attempt counter for retry (task-27).
            if "download_error" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN download_error TEXT DEFAULT '';")
            if "download_attempts" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN download_attempts INTEGER DEFAULT 0;")
            # Torbox local fetch phase/progress/timings (task-29): JSON blob with
            # phase, downloaded_bytes, total_bytes, speed_bps and ready/fetch/route
            # timestamps so the UI can show real local progress.
            if "torbox_progress_json" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN torbox_progress_json TEXT DEFAULT '{}';")
            # Downloaded-file tracking (task-30): files fetched by an engine, when they
            # arrived, and when routing moved them out of the raw downloads folder.
            if "download_files_json" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN download_files_json TEXT DEFAULT '[]';")
            if "downloaded_at" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN downloaded_at TEXT DEFAULT '';")
            if "routed_at" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN routed_at TEXT DEFAULT '';")

            # Backfill is_complete for existing releases matching complete indicators (local SQL/regex only, NO network calls)
            try:
                from app.parser import is_complete_series_release
                cur.execute("SELECT id, title FROM releases WHERE is_complete = 0 OR is_complete IS NULL;")
                for r_row in cur.fetchall():
                    if is_complete_series_release(r_row["title"]):
                        cur.execute("UPDATE releases SET is_complete = 1 WHERE id = ?;", (r_row["id"],))
            except Exception:
                pass

            # Upgrade any Nyaa links pointing to raw torrent downloads to their web post view URLs
            cur.execute("""
                UPDATE releases 
                SET link = replace(replace(link, '/download/', '/view/'), '.torrent', '')
                WHERE link LIKE '%/download/%.torrent';
            """)

            # Sanitize legacy uncleaned markdown/HTML descriptions in database
            try:
                cur.execute("SELECT id, description FROM releases WHERE description LIKE '%http%' OR description LIKE '%[%';")
                dirty_rows = cur.fetchall()
                if dirty_rows:
                    from app.metadata.mangaupdates import clean_description_text
                    for r_id, r_desc in dirty_rows:
                        if r_desc:
                            cur.execute("UPDATE releases SET description = ? WHERE id = ?;", (clean_description_text(r_desc), r_id))
            except Exception:
                pass

            # D1: the old startup purge deleted every release whose title tripped the
            # title-only is_light_novel()/is_chapter_only() heuristics, on EVERY init,
            # with no status filter -- one false positive destroyed tracked/wishlisted
            # rows.  It is replaced by a NON-DESTRUCTIVE one-shot re-typing pass
            # (_run_media_filter_once, run after this transaction); nothing here (or
            # anywhere else on the automatic startup path) may delete releases.

            # Multi-group support (task-11): backfill the full group set from the
            # legacy single ``release_group`` value.  Idempotent: rows whose list is
            # already populated are skipped, so repeated init_db() calls are no-ops.
            try:
                from app.parser import split_release_group_value
                cur.execute("SELECT id, release_group, release_groups_json FROM releases;")
                for r in cur.fetchall():
                    stored = (r["release_groups_json"] or "").strip()
                    if stored and stored not in ("[]", "null"):
                        continue
                    groups = split_release_group_value(r["release_group"] or "")
                    if groups:
                        cur.execute(
                            "UPDATE releases SET release_groups_json = ? WHERE id = ?;",
                            (json.dumps(groups), r["id"]),
                        )
            except Exception:
                pass

            # Re-evaluate and sanitize release_group in releases table
            try:
                cur.execute("SELECT id, title, release_group, release_groups_json FROM releases;")
                all_rel = cur.fetchall()
                from app.parser import extract_release_groups
                for r in all_rel:
                    title_groups = extract_release_groups(r["title"]) or []
                    if title_groups:
                        correct_rg = title_groups[0]
                        correct_json = json.dumps(title_groups)
                    else:
                        correct_rg = ""
                        correct_json = "[]"
                    if (r["release_group"] or "") != correct_rg:
                        cur.execute("UPDATE releases SET release_group = ? WHERE id = ?;", (correct_rg, r["id"]))
                    if (r["release_groups_json"] or "[]") != correct_json:
                        cur.execute(
                            "UPDATE releases SET release_groups_json = ? WHERE id = ?;",
                            (correct_json, r["id"]),
                        )
            except Exception:
                pass

            # Prioritized Release Groups table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS prioritized_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    notes TEXT DEFAULT '',
                    priority INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Seed well-known high quality release groups if empty
            cur.execute("SELECT count(*) FROM prioritized_groups;")
            if cur.fetchone()[0] == 0:
                for grp, note in [
                    ("danke-Empire", "Official digital manga releases and high-res rips"),
                    ("1R0N", "High quality digital manga releases"),
                    ("Lu自主规制", "Digital HQ manga volumes"),
                    ("a-sloth", "Clean digital CBZ editions")
                ]:
                    cur.execute("INSERT OR IGNORE INTO prioritized_groups (name, notes) VALUES (?, ?);", (grp, note))

            # Feeds configuration table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    feed_type TEXT NOT NULL,
                    url TEXT NOT NULL,
                    config_json TEXT DEFAULT '{}',
                    enabled INTEGER DEFAULT 1,
                    auto_download INTEGER DEFAULT 1,
                    check_interval_minutes INTEGER DEFAULT 60,
                    last_checked TIMESTAMP,
                    last_status TEXT,
                    last_error TEXT
                );
            """)

            # Activity logs (+ audit actor column for "which user triggered this")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,
                    actor TEXT DEFAULT 'system',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Upgrade path: add the actor column to pre-M3 databases.
            cur.execute("PRAGMA table_info(activity_logs);")
            log_cols = [r["name"] for r in cur.fetchall()]
            if "actor" not in log_cols:
                cur.execute("ALTER TABLE activity_logs ADD COLUMN actor TEXT DEFAULT 'system';")
            cur.execute(
                "UPDATE activity_logs SET actor = 'system' "
                "WHERE actor IS NULL OR trim(actor) = '';"
            )

            # Known users (JIT-provisioned on first successful OIDC login / Basic use).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT UNIQUE NOT NULL,
                    username TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    display_name TEXT DEFAULT '',
                    auth_source TEXT DEFAULT '',
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_subject ON users(subject);")

            # Settings
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            # Manga metadata cache (AniList / Canonical Manga Database)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS manga_metadata_cache (
                    search_term TEXT PRIMARY KEY,
                    canonical_title TEXT NOT NULL,
                    romaji_title TEXT DEFAULT '',
                    cover_url TEXT DEFAULT '',
                    total_volumes INTEGER,
                    status TEXT DEFAULT 'UNKNOWN',
                    synopsis TEXT DEFAULT '',
                    source TEXT DEFAULT 'anilist',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Default settings
            cur.execute("SELECT count(*) FROM app_settings;")
            if cur.fetchone()[0] == 0:
                defaults = {
                    "calibre_path": os.getenv("CALIBRE_DIR", "/calibre"),
                    "qbit_url": os.getenv("QBIT_URL", "http://localhost:8080"),
                    "qbit_username": os.getenv("QBIT_USER", "admin"),
                    "qbit_password": os.getenv("QBIT_PASS", "adminadmin"),
                    "qbit_enabled": "true",
                    "qbit_category": "manga",
                    "qbit_tags": "mangatrax",
                    "staging_dir": os.getenv("STAGING_DIR", "/staging"),
                    # Raw download destination for both engines; STAGING_DIR is the
                    # final destination after the tag+move routing pass (task-22).
                    "downloads_dir": os.getenv("DOWNLOADS_DIR", "/downloads"),
                    "auto_download": "true",
                    "auto_tag": "true",
                    # Context-guarded discard of marker-less chapter releases
                    # ("Chainsaw Man 180" as opposed to "Chainsaw Man Vol. 180").
                    # Default on; operators can turn it off without a code change.
                    "discard_unmarked_chapters": os.getenv("MANGATRAX_DISCARD_UNMARKED_CHAPTERS", "true"),
                    # Infer the full volume set of a collected batch (year-range title,
                    # no volume markers) from authoritative metadata.  Default on.
                    "infer_collected_releases": os.getenv("MANGATRAX_INFER_COLLECTED_RELEASES", "true"),
                    "calibre_sync_interval_minutes": "15",
                    "feed_check_interval_minutes": "60",
                    "backfill_max_pages": "15",
                    "backfill_delay_seconds": "2.0",
                    "discord_webhook_url": "",
                    # Nyaa/tracker base URL (mirror/proxy support).  Bare base or a
                    # full Nyaa-style URL is accepted and normalised on save.
                    "nyaa_base_url": os.getenv("MANGATRAX_NYAA_BASE_URL", DEFAULT_NYAA_BASE_URL),
                    # task-20: selectable download engine (qbit default => upgrades inert).
                    "downloader_engine": os.getenv("MANGATRAX_DOWNLOADER_ENGINE", "qbit"),
                    "torbox_enabled": os.getenv("MANGATRAX_TORBOX_ENABLED", "false"),
                    "torbox_api_key": os.getenv("MANGATRAX_TORBOX_API_KEY", ""),
                    "torbox_delete_after_download": os.getenv(
                        "MANGATRAX_TORBOX_DELETE_AFTER_DOWNLOAD", "true"
                    ),
                    "torbox_poll_seconds": os.getenv("MANGATRAX_TORBOX_POLL_SECONDS", "5"),
                    "torbox_max_concurrent": os.getenv("MANGATRAX_TORBOX_MAX_CONCURRENT", "2"),
        "torbox_max_download_gb": os.getenv("MANGATRAX_TORBOX_MAX_DOWNLOAD_GB", "64"),
        "torbox_download_concurrency": os.getenv("MANGATRAX_TORBOX_DOWNLOAD_CONCURRENCY", "4"),
        "torbox_parallel_chunks": os.getenv("MANGATRAX_TORBOX_PARALLEL_CHUNKS", "4"),
        "log_to_file": os.getenv("MANGATRAX_LOG_TO_FILE", "false"),
        "log_file_max_mb": os.getenv("MANGATRAX_LOG_FILE_MAX_MB", "10"),
        "log_file_backups": os.getenv("MANGATRAX_LOG_FILE_BACKUPS", "5"),
        "activity_retention_days": os.getenv("MANGATRAX_ACTIVITY_RETENTION_DAYS", "90"),
        "activity_max_rows": os.getenv("MANGATRAX_ACTIVITY_MAX_ROWS", "50000"),
        "torbox_delete_on_failure": os.getenv("MANGATRAX_TORBOX_DELETE_ON_FAILURE", "true"),
                    "torbox_timeout_hours": os.getenv("MANGATRAX_TORBOX_TIMEOUT_HOURS", "6"),
                    "torbox_strict_volume_selection": os.getenv("MANGATRAX_TORBOX_STRICT_VOLUMES", "true"),
                }
                for k, v in defaults.items():
                    cur.execute("INSERT INTO app_settings (key, value) VALUES (?, ?);", (k, v))

            # Ensure in-app downloader keys exist in case of upgrade
            for k, v in [("qbit_enabled", "true"), ("qbit_category", "manga"), ("qbit_tags", "mangatrax")]:
                cur.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);", (k, v))

            # Behaviour flags added after the first release: INSERT OR IGNORE keeps any
            # value the operator already chose (or the env seed for this deployment).
            cur.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);",
                ("discard_unmarked_chapters", os.getenv("MANGATRAX_DISCARD_UNMARKED_CHAPTERS", "true")),
            )
            cur.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);",
                ("filter_non_manga", os.getenv("MANGATRAX_FILTER_NON_MANGA", "true")),
            )
            cur.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);",
                ("infer_collected_releases", os.getenv("MANGATRAX_INFER_COLLECTED_RELEASES", "true")),
            )
            cur.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);",
                ("nyaa_base_url", os.getenv("MANGATRAX_NYAA_BASE_URL", DEFAULT_NYAA_BASE_URL)),
            )
            for torbox_key, torbox_default in (
                ("downloader_engine", os.getenv("MANGATRAX_DOWNLOADER_ENGINE", "qbit")),
                ("torbox_enabled", os.getenv("MANGATRAX_TORBOX_ENABLED", "false")),
                ("torbox_api_key", os.getenv("MANGATRAX_TORBOX_API_KEY", "")),
                ("torbox_delete_after_download", os.getenv("MANGATRAX_TORBOX_DELETE_AFTER_DOWNLOAD", "true")),
                ("torbox_poll_seconds", os.getenv("MANGATRAX_TORBOX_POLL_SECONDS", "5")),
                ("torbox_max_concurrent", os.getenv("MANGATRAX_TORBOX_MAX_CONCURRENT", "2")),
                ("torbox_max_download_gb", os.getenv("MANGATRAX_TORBOX_MAX_DOWNLOAD_GB", "64")),
                ("torbox_download_concurrency", os.getenv("MANGATRAX_TORBOX_DOWNLOAD_CONCURRENCY", "4")),
                ("torbox_parallel_chunks", os.getenv("MANGATRAX_TORBOX_PARALLEL_CHUNKS", "4")),
                ("log_to_file", os.getenv("MANGATRAX_LOG_TO_FILE", "false")),
                ("log_file_max_mb", os.getenv("MANGATRAX_LOG_FILE_MAX_MB", "10")),
                ("log_file_backups", os.getenv("MANGATRAX_LOG_FILE_BACKUPS", "5")),
                ("activity_retention_days", os.getenv("MANGATRAX_ACTIVITY_RETENTION_DAYS", "90")),
                ("activity_max_rows", os.getenv("MANGATRAX_ACTIVITY_MAX_ROWS", "50000")),
                ("torbox_delete_on_failure", os.getenv("MANGATRAX_TORBOX_DELETE_ON_FAILURE", "true")),
                ("torbox_timeout_hours", os.getenv("MANGATRAX_TORBOX_TIMEOUT_HOURS", "6")),
                ("torbox_strict_volume_selection", os.getenv("MANGATRAX_TORBOX_STRICT_VOLUMES", "true")),
            ):
                cur.execute(
                    "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);",
                    (torbox_key, torbox_default),
                )
            cur.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);",
                ("downloads_dir", os.getenv("DOWNLOADS_DIR", "/downloads")),
            )
            # One-shot marker for the D1 re-typing pass ('' = not applied yet).
            cur.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);",
                ("media_filter_applied", ""),
            )

            # Seed sample manga feed if empty
            cur.execute("SELECT count(*) FROM feeds;")
            if cur.fetchone()[0] == 0:
                cur.execute("""
                    INSERT INTO feeds (name, feed_type, url, config_json, enabled, auto_download, check_interval_minutes)
                    VALUES (?, ?, ?, ?, 1, 1, 60);
                """, (
                    SEEDED_NYAA_FEED_NAME,
                    "rss",
                    SEEDED_NYAA_FEED_URL,
                    json.dumps({"description": "Monitors CBZ Manga releases."})
                ))

            # Mirror/proxy support: repoint the built-in seeded feed at the configured
            # Nyaa base URL (idempotent; only the exact stock row, never user feeds).
            try:
                self._migrate_seeded_nyaa_feed_cur(cur)
            except Exception:
                pass

            # Performance indexes for fast queries and instant tab switches
            cur.execute("CREATE INDEX IF NOT EXISTS idx_releases_series_name ON releases(series_name);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_releases_lower_sname ON releases(lower(series_name));")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_releases_status ON releases(status);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_releases_updated_at ON releases(updated_at);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_releases_rg ON releases(release_group);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_releases_vol_start ON releases(volume_start);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_releases_media_type ON releases(media_type);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs(created_at);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_series_tracking_name ON series_tracking(name);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_series_tracking_monitored ON series_tracking(is_monitored);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_meta_cache_term ON manga_metadata_cache(search_term);")

            conn.commit()
        finally:
            conn.close()

        # D1: type legacy rows (non-destructively) once.  This runs AFTER the schema
        # transaction above so it never contends for the write lock, and it is the
        # only media-typing work on the automatic startup path.
        self._run_media_filter_once()
        self._run_jpegxl_filter_once()

    def _run_media_filter_once(self):
        """One-shot, NON-DESTRUCTIVE re-typing of rows stored before media typing.

        Replaces the old init-time ``DELETE FROM releases`` purge (D1).  Behaviour:

        * rows are re-labelled, never deleted;
        * only rows still typed ``unknown`` are examined, so anything already
          classified (or decided by a human) is untouched;
        * ``status`` is never written -- this pass cannot clobber the
          ``resolve_status()`` precedence model;
        * gated by the ``media_filter_applied`` marker: any value other than
          ``''``/``'0'`` counts as already applied (so a legacy ``'true'`` value
          cannot make it re-run), and by ``filter_non_manga`` (default true);
        * failure is logged and swallowed -- typing must never block startup.

        Runs on ONE explicitly-closed connection: this executes inside
        ``__init__`` and on Windows a leaked (cycle-held) sqlite handle would keep
        the database file locked.
        """
        typed = {MEDIA_TYPE_LIGHT_NOVEL: 0, MEDIA_TYPE_CHAPTER_ONLY: 0, MEDIA_TYPE_JPEGXL: 0}
        changed = 0
        try:
            conn = self.get_connection()
            try:
                cur = conn.cursor()

                def _setting(key: str, default: str) -> str:
                    cur.execute("SELECT value FROM app_settings WHERE key = ?;", (key,))
                    row = cur.fetchone()
                    return (row["value"] if row else default) or default

                marker = _setting("media_filter_applied", "").strip()
                if marker not in ("", "0"):
                    return 0
                filter_setting = _setting("filter_non_manga", "true").strip().lower()
                if filter_setting in ("false", "0", "no", "off"):
                    return 0

                from app.parser import is_chapter_only, is_jpegxl_release, is_light_novel

                cur.execute(
                    "SELECT id, title FROM releases "
                    "WHERE COALESCE(media_type, 'unknown') IN ('unknown', '');"
                )
                rows = cur.fetchall()
                for row in rows:
                    title = (row["title"] or "").strip()
                    if not title:
                        continue
                    if is_jpegxl_release(title):
                        kind = MEDIA_TYPE_JPEGXL
                    elif is_light_novel(title):
                        kind = MEDIA_TYPE_LIGHT_NOVEL
                    elif is_chapter_only(title):
                        kind = MEDIA_TYPE_CHAPTER_ONLY
                    else:
                        continue
                    cur.execute(
                        "UPDATE releases SET media_type = ? "
                        "WHERE id = ? AND COALESCE(media_type, 'unknown') IN ('unknown', '');",
                        (kind, row["id"]),
                    )
                    typed[kind] += 1
                    changed += 1

                cur.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = ?;",
                    ("media_filter_applied", "1", "1"),
                )
                if changed:
                    cur.execute(
                        "INSERT INTO activity_logs (event_type, message, details, actor) "
                        "VALUES (?, ?, ?, ?);",
                        (
                            "MEDIA_RECLASSIFY",
                            "Re-typed stored releases by media type (no rows deleted)",
                            f"Light novel: {typed[MEDIA_TYPE_LIGHT_NOVEL]}, "
                            f"chapter-only: {typed[MEDIA_TYPE_CHAPTER_ONLY]}, "
                            f"JPEG XL: {typed[MEDIA_TYPE_JPEGXL]}. "
                            "Non-manga types are hidden from the tracking views and kept in the database.",
                            _resolve_audit_actor("system"),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover - typing must never block startup
            logger.warning("Media re-typing pass skipped: %s", exc)
            return 0

        if changed:
            self._invalidate_grouped_cache()
            logger.info(
                "Media filter: typed %s light-novel, %s chapter-only and %s JPEG XL row(s); nothing deleted.",
                typed[MEDIA_TYPE_LIGHT_NOVEL], typed[MEDIA_TYPE_CHAPTER_ONLY],
                typed[MEDIA_TYPE_JPEGXL],
            )
        return changed

    def _run_jpegxl_filter_once(self):
        """One-shot, non-destructive typing of legacy JPEG XL rows (upgrade path).

        Feed ingestion discards JPEG XL releases before they are stored; this pass
        only labels rows that were stored by older versions so the manga views hide
        them and the UI can mark them ``[JPEGXL]``.  Idempotent via its own marker so
        databases that already ran the D1 media pass are still upgraded.
        """
        try:
            conn = self.get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT value FROM app_settings WHERE key = 'jpegxl_filter_applied';")
                row = cur.fetchone()
                if row and (row["value"] or "").strip() not in ("", "0"):
                    return 0
                from app.parser import is_jpegxl_release

                cur.execute(
                    "SELECT id, title FROM releases "
                    "WHERE COALESCE(media_type, 'unknown') IN ('unknown', '');"
                )
                rows = cur.fetchall()
                typed = 0
                for item in rows:
                    title = (item["title"] or "").strip()
                    if title and is_jpegxl_release(title):
                        cur.execute(
                            "UPDATE releases SET media_type = ? "
                            "WHERE id = ? AND COALESCE(media_type, 'unknown') IN ('unknown', '');",
                            (MEDIA_TYPE_JPEGXL, item["id"]),
                        )
                        typed += 1
                cur.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = ?;",
                    ("jpegxl_filter_applied", "1", "1"),
                )
                if typed:
                    cur.execute(
                        "INSERT INTO activity_logs (event_type, message, details, actor) "
                        "VALUES (?, ?, ?, ?);",
                        (
                            "MEDIA_RECLASSIFY",
                            "Re-typed stored releases as [JPEGXL] (no rows deleted)",
                            "JPEG XL releases are never auto-downloaded and are hidden from the "
                            "manga views; they stay inspectable in the database.",
                            _resolve_audit_actor("system"),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover - typing must never block startup
            logger.warning("JPEG XL re-typing pass skipped: %s", exc)
            return 0
        if typed:
            self._invalidate_grouped_cache()
            logger.info("Media filter: typed %s stored JPEG XL row(s); nothing deleted.", typed)
        return typed

    def prune_activity_logs(
        self, retention_days: int = 90, max_rows: int = 50000, checkpoint: bool = True,
    ) -> Dict[str, Any]:
        """Delete old/excess activity rows, then truncate-checkpoint the WAL."""
        result = {"deleted_old": 0, "deleted_excess": 0, "remaining": 0, "checkpointed": False}
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            retention_days = 90
        try:
            max_rows = int(max_rows)
        except (TypeError, ValueError):
            max_rows = 50000
        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                if retention_days > 0:
                    cutoff = (datetime.utcnow() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
                    cur.execute("DELETE FROM activity_logs WHERE created_at < ?;", (cutoff,))
                    result["deleted_old"] = max(0, int(cur.rowcount or 0))
                cur.execute("SELECT COUNT(*) AS count FROM activity_logs;")
                remaining = int(cur.fetchone()["count"] or 0)
                if max_rows > 0 and remaining > max_rows:
                    excess = remaining - max_rows
                    cur.execute(
                        "DELETE FROM activity_logs WHERE id IN "
                        "(SELECT id FROM activity_logs ORDER BY id ASC LIMIT ?);",
                        (excess,),
                    )
                    result["deleted_excess"] = max(0, int(cur.rowcount or 0))
                    remaining -= result["deleted_excess"]
                conn.commit()
                result["remaining"] = remaining
                if checkpoint:
                    try:
                        cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                        cur.fetchall()
                        result["checkpointed"] = True
                    except Exception as exc:
                        logger.debug("WAL checkpoint failed: %s", exc)
        except Exception as exc:
            logger.warning("Activity log prune failed: %s", exc)
        self._invalidate_grouped_cache()
        return result

    def activity_log_stats(self) -> Dict[str, Any]:
        stats = {"count": 0, "oldest_created_at": ""}
        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) AS count, MIN(created_at) AS oldest FROM activity_logs;")
                row = cur.fetchone()
                stats["count"] = int(row["count"] or 0) if row else 0
                stats["oldest_created_at"] = str((row["oldest"] if row else "") or "")
        except Exception as exc:
            logger.debug("Could not read activity log stats: %s", exc)
        return stats

    # --- Settings ---
    def get_setting(self, key: str, default: str = "") -> str:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = ?;", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?;", (key, value, value))
            conn.commit()

    def get_all_settings(self) -> Dict[str, str]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM app_settings;")
            return {row["key"]: row["value"] for row in cur.fetchall()}

    # --- Series Tracking ---
    def sync_calibre_series(self, calibre_series_list: List[Dict[str, Any]], volumes_map: Optional[Dict[str, Set[float]]] = None) -> int:
        updated_count = 0
        from urllib.parse import quote_plus
        from app.parser import normalize_text, split_series_candidate_parts
        with self.get_connection() as conn:
            cur = conn.cursor()
            for s in calibre_series_list:
                name = s["series_name"]
                ser_id = s["series_id"]
                count = s["book_count"]
                latest = s["latest_index"]
                authors = s.get("authors", "")
                publisher = s.get("publisher", "")
                tags = s.get("tags", "")
                default_web_url = f"https://www.mangaupdates.com/series.html?search={quote_plus(name)}"

                owned_vols_set: Set[float] = set()
                if volumes_map:
                    for k in (str(ser_id), ser_id, name.lower(), normalize_text(name)):
                        if k in volumes_map:
                            owned_vols_set.update(volumes_map[k])
                    parts = split_series_candidate_parts(name)
                    if len(parts) > 1:
                        for p in parts:
                            for pk in (p.lower(), normalize_text(p)):
                                if pk in volumes_map:
                                    owned_vols_set.update(volumes_map[pk])

                owned_json = json.dumps(sorted(list(owned_vols_set)))

                cur.execute("""
                    INSERT INTO series_tracking (
                        calibre_series_id, name, authors, publisher, tags,
                        book_count, latest_index, is_monitored, website_url, owned_volumes_json, last_sync
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(name) DO UPDATE SET
                        calibre_series_id = excluded.calibre_series_id,
                        authors = CASE WHEN excluded.authors != '' THEN excluded.authors ELSE series_tracking.authors END,
                        publisher = CASE WHEN excluded.publisher != '' THEN excluded.publisher ELSE series_tracking.publisher END,
                        tags = CASE WHEN excluded.tags != '' THEN excluded.tags ELSE series_tracking.tags END,
                        book_count = excluded.book_count,
                        latest_index = excluded.latest_index,
                        website_url = CASE WHEN series_tracking.website_url = '' OR series_tracking.website_url IS NULL THEN excluded.website_url ELSE series_tracking.website_url END,
                        owned_volumes_json = CASE WHEN excluded.owned_volumes_json != '[]' THEN excluded.owned_volumes_json ELSE series_tracking.owned_volumes_json END,
                        last_sync = CURRENT_TIMESTAMP;
                """, (ser_id, name, authors, publisher, tags, count, latest, default_web_url, owned_json))
                updated_count += 1
            conn.commit()
        if updated_count > 0:
            self._invalidate_grouped_cache()
        return updated_count

    def get_all_series(
        self,
        monitored_only: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            base_sql = """
                SELECT 
                    st.*,
                    (SELECT r.cover_url FROM releases r WHERE lower(r.series_name) = lower(st.name) AND r.cover_url IS NOT NULL AND r.cover_url != '' ORDER BY r.id DESC LIMIT 1) AS cover_url
                FROM series_tracking st
            """
            where_clauses = []
            params = []
            if monitored_only:
                where_clauses.append("st.is_monitored = 1")
            if search and search.strip():
                clean_s = _like_contains(search.strip())
                where_clauses.append(
                    "(lower(st.name) LIKE ? ESCAPE '\\' OR lower(COALESCE(st.authors, '')) LIKE ? ESCAPE '\\' "
                    "OR lower(COALESCE(st.publisher, '')) LIKE ? ESCAPE '\\')"
                )
                params.extend([clean_s, clean_s, clean_s])

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            order_str = "ORDER BY st.is_monitored DESC, st.name COLLATE NOCASE ASC"
            limit_str = ""
            if limit is not None:
                # Parameterised LIMIT/OFFSET instead of interpolating integers.
                limit_str = "LIMIT ? OFFSET ?"
                params.extend([int(limit), int(offset)])

            sql = f"{base_sql} {where_str} {order_str} {limit_str};"
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def count_all_series(self, monitored_only: bool = False, search: Optional[str] = None) -> int:
        with self.get_connection() as conn:
            cur = conn.cursor()
            where_clauses = []
            params = []
            if monitored_only:
                where_clauses.append("is_monitored = 1")
            if search and search.strip():
                clean_s = _like_contains(search.strip())
                where_clauses.append(
                    "(lower(name) LIKE ? ESCAPE '\\' OR lower(COALESCE(authors, '')) LIKE ? ESCAPE '\\' "
                    "OR lower(COALESCE(publisher, '')) LIKE ? ESCAPE '\\')"
                )
                params.extend([clean_s, clean_s, clean_s])
            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            cur.execute(f"SELECT COUNT(*) FROM series_tracking {where_str};", tuple(params))
            row = cur.fetchone()
            return row[0] if row else 0

    def toggle_series_monitored(self, series_id: int) -> bool:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE series_tracking SET is_monitored = 1 - is_monitored WHERE id = ?;", (series_id,))
            conn.commit()
            cur.execute("SELECT is_monitored FROM series_tracking WHERE id = ?;", (series_id,))
            row = cur.fetchone()
            return bool(row["is_monitored"]) if row else False

    def update_series_link(self, series_id: int, website_url: str):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE series_tracking SET website_url = ? WHERE id = ?;", (website_url.strip(), series_id))
            conn.commit()

    def get_series_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Case-insensitive lookup of one tracked series row."""
        clean = str(name or "").strip()
        if not clean:
            return None
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM series_tracking WHERE lower(name) = lower(?) LIMIT 1;", (clean,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_series_by_id(self, series_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM series_tracking WHERE id = ? LIMIT 1;", (series_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    _SERIES_UPDATABLE_COLUMNS = (
        "authors",
        "publisher",
        "tags",
        "description",
        "book_count",
        "latest_index",
        "owned_volumes_json",
        "website_url",
        "calibre_series_id",
        "last_sync",
    )

    def update_series_metadata(self, series_id: int, fields: Dict[str, Any]) -> bool:
        """Update whitelisted display/enrichment columns of one tracked series.

        Deliberately cannot touch ``name``/``is_monitored``: provider metadata is
        enrichment only and must never change the stable matching key.
        """
        if not series_id or not fields:
            return False
        updates = {
            key: value for key, value in fields.items()
            if key in self._SERIES_UPDATABLE_COLUMNS and value is not None
        }
        if not updates:
            return False
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params = list(updates.values()) + [series_id]
        if "last_sync" not in updates:
            assignments += ", last_sync = CURRENT_TIMESTAMP"
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE series_tracking SET {assignments} WHERE id = ?;",
                tuple(params),
            )
            conn.commit()
            return cur.rowcount > 0

    def update_series_cover_on_releases(self, series_name: str, cover_url: str) -> int:
        """Point this series' releases at a refreshed provider cover (display only)."""
        clean_name = str(series_name or "").strip()
        clean_url = str(cover_url or "").strip()
        if not clean_name or not clean_url:
            return 0
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE releases SET cover_url = ? WHERE lower(series_name) = lower(?);",
                (clean_url, clean_name),
            )
            conn.commit()
            return cur.rowcount or 0

    def get_releases_for_series(self, series_name: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Releases belonging to one series (case-insensitive, vetoed rows excluded)."""
        clean_name = str(series_name or "").strip()
        if not clean_name:
            return []
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM releases
                WHERE lower(series_name) = lower(?)
                  AND status NOT IN ('BLACKLISTED', 'IGNORED')
                ORDER BY id ASC
                LIMIT ?;
                """,
                (clean_name, int(limit)),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_inflight_reserved_volumes(
        self,
        series_names: Any,
        exclude_guid: Optional[str] = None,
    ) -> Set[float]:
        """Volumes already being downloaded / downloaded-but-not-in-Calibre.

        Used by auto-download only: a second release (another group/quality/source)
        for the same volume must not be auto-submitted while the first release is
        still DOWNLOADING or COMPLETED (files fetched but not yet imported into
        Calibre).  Failed/IDLE rows release their reservation.
        """
        names = sorted({str(name).strip().lower() for name in (series_names or []) if str(name or "").strip()})
        if not names:
            return set()
        placeholders = ", ".join("?" for _ in names)
        reserved: Set[float] = set()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT guid, download_wanted_json, volumes_json FROM releases
                WHERE lower(series_name) IN ({placeholders})
                  AND download_status IN ('DOWNLOADING', 'COMPLETED')
                  AND COALESCE(status, '') NOT IN ('IN_CALIBRE', 'IGNORED', 'BLACKLISTED');
                """,
                tuple(names),
            )
            rows = cur.fetchall()
        for row in rows:
            if exclude_guid and str(row["guid"] or "") == str(exclude_guid):
                continue
            # The effective wanted set is authoritative: a v01-05 bundle under strict
            # selection may only be fetching [3,4], so reserving the whole bundle would
            # wrongly block a legitimate release for volume 5.  Legacy rows without a
            # persisted wanted set fall back to the bundle's full volume list.
            effective = _parse_volume_list(row["download_wanted_json"])
            if not effective:
                effective = _parse_volume_list(row["volumes_json"])
            for volume in effective:
                try:
                    reserved.add(round(float(volume), 2))
                except (TypeError, ValueError):
                    continue
        return reserved

    def get_pending_dedupe_releases(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Releases suppressed by in-flight dedupe, oldest first (bounded)."""
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 50
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM releases WHERE download_status = 'PENDING_DEDUPE' "
                "ORDER BY id ASC LIMIT ?;",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_series_templates_map(self) -> Dict[str, Dict[str, Any]]:
        """Returns map of normalized series name -> template dict for ComicInfo.xml metadata injection."""
        series_list = self.get_all_series()
        from app.parser import normalize_text
        res = {}
        for s in series_list:
            norm = normalize_text(s["name"])
            res[norm] = {
                "series_name": s["name"],
                "authors": s.get("authors", ""),
                "publisher": s.get("publisher", ""),
                "tags": s.get("tags", "")
            }
        return res

    # --- Blacklist ---
    def add_to_blacklist(self, series_name: str, reason: str = "") -> bool:
        clean = series_name.strip()
        if not clean:
            return False
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO series_blacklist (name, reason, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET reason = excluded.reason;
            """, (clean, reason))
            # Cancel existing requests.  Explicit user action: blacklisting is a
            # deliberate transition, so it writes BLACKLISTED verbatim and bypasses
            # resolve_status() (automation may not override the veto afterwards).
            cur.execute("UPDATE releases SET status = 'BLACKLISTED', updated_at = CURRENT_TIMESTAMP WHERE lower(series_name) = lower(?);", (clean,))
            cur.execute("UPDATE series_tracking SET is_monitored = 0 WHERE lower(name) = lower(?);", (clean,))
            conn.commit()
            self._invalidate_grouped_cache()
            return True

    def remove_from_blacklist(self, blacklist_id: int):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM series_blacklist WHERE id = ?;", (blacklist_id,))
            row = cur.fetchone()
            cur.execute("DELETE FROM series_blacklist WHERE id = ?;", (blacklist_id,))
            if row and row["name"]:
                # Explicit user action: lift the BLACKLISTED veto so automation may
                # re-classify these releases again.  BLACKLISTED is otherwise immune to
                # automated re-upserts (see resolve_status), so without this reset the
                # releases would stay invisible forever after an unblacklist.
                cur.execute("""
                    UPDATE releases
                    SET status = 'DISCOVERY', updated_at = CURRENT_TIMESTAMP
                    WHERE lower(series_name) = lower(?) AND status = 'BLACKLISTED';
                """, (row["name"].strip(),))
            conn.commit()
            self._invalidate_grouped_cache()

    def get_blacklisted_series(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM series_blacklist ORDER BY name COLLATE NOCASE ASC;")
            return [dict(row) for row in cur.fetchall()]

    def is_series_blacklisted(self, series_name: str) -> bool:
        if not series_name:
            return False
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM series_blacklist WHERE lower(name) = lower(?);", (series_name.strip(),))
            return cur.fetchone() is not None

    # --- Releases & Bundles ---
    def upsert_release(self, rel: Dict[str, Any]) -> Tuple[bool, int]:
        guid = rel["guid"]
        vols_json = json.dumps(rel.get("volumes") or [])
        missing_json = json.dumps(rel.get("missing_volumes") or [])
        from app.parser import is_complete_series_release
        is_comp_val = 1 if (rel.get("is_complete") or is_complete_series_release(rel.get("title", ""))) else 0
        revision = rel.get("revision")
        try:
            revision = int(revision) if revision not in (None, "") else None
        except (TypeError, ValueError):
            revision = None
        publisher = (rel.get("publisher") or "").strip()
        # Inferred full volume set (collected batch resolved from metadata).
        inferred_val = 1 if rel.get("inferred_volumes") else 0
        # Full multi-group set: explicit list when provided, else split the primary.
        stored_groups = rel.get("release_groups")
        if not stored_groups:
            from app.parser import split_release_group_value

            stored_groups = split_release_group_value(rel.get("release_group") or "")
        groups_json = json.dumps([str(g).strip() for g in stored_groups if str(g).strip()])

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, status, download_status FROM releases WHERE guid = ?;", (guid,))
            existing = cur.fetchone()

            if existing:
                rel_id = existing["id"]
                # Automated re-upsert (feed poll / Calibre sync): the precedence model
                # decides whether the freshly computed classification may replace the
                # stored status.  This is what stops a background re-poll from wiping
                # REQUESTED / IN_CALIBRE / IGNORED / BLACKLISTED.
                resolved_status = resolve_status(existing["status"], rel.get("status"))
                cur.execute("""
                    UPDATE releases SET
                        title = ?,
                        series_name = ?,
                        release_group = COALESCE(?, release_group),
                        release_groups_json = CASE WHEN ? != '[]' THEN ? ELSE release_groups_json END,
                        description = CASE WHEN ? != '' AND ? IS NOT NULL THEN ? ELSE description END,
                        mangaupdates_url = CASE WHEN ? != '' AND ? IS NOT NULL THEN ? ELSE mangaupdates_url END,
                        is_bundle = ?,
                        is_complete = ?,
                        volume_start = ?,
                        volume_end = ?,
                        volumes_json = ?,
                        missing_vols_json = ?,
                        download_url = ?,
                        link = ?,
                        cover_url = ?,
                        revision = CASE WHEN COALESCE(?, 0) > COALESCE(revision, 0) THEN ? ELSE revision END,
                        publisher = CASE WHEN ? != '' THEN ? ELSE publisher END,
                        inferred_volumes = CASE WHEN ? = 1 THEN 1 ELSE inferred_volumes END,
                        status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                """, (
                    rel["title"],
                    rel.get("series_name"),
                    rel.get("release_group"),
                    groups_json,
                    groups_json,
                    rel.get("description", ""),
                    rel.get("description"),
                    rel.get("description", ""),
                    rel.get("mangaupdates_url", ""),
                    rel.get("mangaupdates_url"),
                    rel.get("mangaupdates_url", ""),
                    1 if rel.get("is_bundle") else 0,
                    is_comp_val,
                    rel.get("volume_start"),
                    rel.get("volume_end"),
                    vols_json,
                    missing_json,
                    rel.get("download_url"),
                    rel.get("link"),
                    rel.get("cover_url"),
                    revision,
                    revision,
                    publisher,
                    publisher,
                    inferred_val,
                    resolved_status,
                    rel_id
                ))
                conn.commit()
                self._invalidate_grouped_cache()
                return False, rel_id
            else:
                cur.execute("""
                    INSERT INTO releases (
                        guid, title, series_name, release_group, release_groups_json, description, mangaupdates_url,
                        is_bundle, is_complete, volume_start, volume_end, volumes_json, missing_vols_json,
                        download_url, link, cover_url, source_feed_name, status, download_status, torrent_hash,
                        revision, publisher, inferred_volumes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    guid,
                    rel["title"],
                    rel.get("series_name"),
                    rel.get("release_group") or "",
                    groups_json,
                    rel.get("description") or "",
                    rel.get("mangaupdates_url") or "",
                    1 if rel.get("is_bundle") else 0,
                    is_comp_val,
                    rel.get("volume_start"),
                    rel.get("volume_end"),
                    vols_json,
                    missing_json,
                    rel.get("download_url"),
                    rel.get("link"),
                    rel.get("cover_url"),
                    rel.get("source_feed_name"),
                    resolve_status(None, rel.get("status")) or "REQUESTED",
                    rel.get("download_status", "IDLE"),
                    rel.get("torrent_hash"),
                    revision,
                    publisher,
                    inferred_val,
                ))
                rel_id = cur.lastrowid
                conn.commit()
                self._invalidate_grouped_cache()
                return True, rel_id

    def update_release_status(self, release_id: int, status: str, enforce_precedence: bool = False):
        """Sets a release status.

        This is the deliberate-transition primitive used by the user-facing API
        endpoints (``POST /api/releases/{id}/status``, ``/ignore``, ``/unignore``),
        so by default the value is written verbatim: a sticky state can always be
        changed by an explicit user action (e.g. IGNORED -> REQUESTED).

        Automated callers (the scheduler) pass ``enforce_precedence=True`` so the
        shared ``resolve_status()`` precedence model protects user intent.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            if enforce_precedence:
                cur.execute("SELECT status FROM releases WHERE id = ?;", (release_id,))
                row = cur.fetchone()
                if row is None:
                    return
                status = resolve_status(row["status"], status)
            cur.execute("UPDATE releases SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (status, release_id))
            conn.commit()
            self._invalidate_grouped_cache()

    def update_release_volumes_and_status(
        self,
        release_id: int,
        volumes: List[float],
        missing_volumes: List[float],
        status: Optional[str] = None
    ):
        with self.get_connection() as conn:
            cur = conn.cursor()
            if status:
                # Automated reconciliation writer (Calibre sync): the freshly computed
                # status may not downgrade a sticky state -- resolve against the stored one.
                cur.execute("SELECT status FROM releases WHERE id = ?;", (release_id,))
                row = cur.fetchone()
                resolved_status = resolve_status(row["status"] if row else None, status)
                cur.execute("""
                    UPDATE releases
                    SET volumes_json = ?, missing_vols_json = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                """, (json.dumps(volumes), json.dumps(missing_volumes), resolved_status, release_id))
            else:
                cur.execute("""
                    UPDATE releases
                    SET volumes_json = ?, missing_vols_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                """, (json.dumps(volumes), json.dumps(missing_volumes), release_id))
            conn.commit()
        self._invalidate_grouped_cache()

    def update_download_status(self, release_id: int, download_status: str, torrent_hash: Optional[str] = None):
        clean_hash = str(torrent_hash).strip() if (isinstance(torrent_hash, str) and torrent_hash.strip()) else None
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE releases 
                SET download_status = ?, torrent_hash = COALESCE(?, torrent_hash), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (download_status, clean_hash, release_id))
            conn.commit()
            self._invalidate_grouped_cache()

    def update_download_handle(
        self,
        release_id: int,
        engine: str,
        downloader_id: Optional[str] = None,
        torrent_hash: Optional[str] = None,
        wanted_volumes: Optional[List[float]] = None,
    ):
        """Persist the engine provenance and effective wanted volumes for a release."""
        clean_engine = str(engine or "").strip().lower()
        clean_id = str(downloader_id).strip() if downloader_id is not None else ""
        clean_hash = str(torrent_hash).strip() if (isinstance(torrent_hash, str) and torrent_hash.strip()) else None
        clean_wanted = None
        if wanted_volumes is not None:
            clean_wanted = json.dumps(sorted({round(float(v), 2) for v in wanted_volumes}))
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE releases
                SET download_status = 'DOWNLOADING',
                    downloader = ?,
                    downloader_id = ?,
                    torrent_hash = COALESCE(?, torrent_hash),
                    download_wanted_json = COALESCE(?, download_wanted_json),
                    download_error = '',
                    download_attempts = 0,
                    downloader_started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (clean_engine, clean_id, clean_hash, clean_wanted, release_id))
            conn.commit()
            self._invalidate_grouped_cache()

    def update_downloader_state(self, release_id: int, download_status: str, engine: Optional[str] = None):
        """Only mutate download_status (and optionally engine) without touching hashes."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if engine:
                cur.execute(
                    "UPDATE releases SET download_status = ?, downloader = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (download_status, str(engine).strip().lower(), release_id),
                )
            else:
                cur.execute(
                    "UPDATE releases SET download_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (download_status, release_id),
                )
            conn.commit()
            self._invalidate_grouped_cache()

    def set_download_files(self, release_id: int, paths: List[str]) -> None:
        """Persist the files fetched for a release and mark when they arrived."""
        clean = [str(path) for path in (paths or [])]
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE releases SET download_files_json = ?, "
                "downloaded_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (json.dumps(clean), release_id),
            )
            conn.commit()
            self._invalidate_grouped_cache()

    def mark_release_routed(self, release_id: int) -> None:
        """Mark that the release's files have left the raw downloads folder."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE releases SET routed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?;",
                (release_id,),
            )
            conn.commit()
            self._invalidate_grouped_cache()

    def get_tracked_downloads(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Releases with recorded download files (for inventory matching)."""
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 500
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM releases WHERE COALESCE(download_files_json, '[]') NOT IN ('', '[]') "
                "ORDER BY id DESC LIMIT ?;",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def update_torbox_progress(self, release_id: int, **fields) -> Dict[str, Any]:
        """Merge the Torbox phase/progress/timing fields into the release row."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT torbox_progress_json FROM releases WHERE id = ?;", (release_id,))
            row = cur.fetchone()
            try:
                data = json.loads((row["torbox_progress_json"] if row else "") or "{}")
            except (TypeError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.update({key: value for key, value in fields.items() if value is not None})
            data["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "UPDATE releases SET torbox_progress_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (json.dumps(data), release_id),
            )
            conn.commit()
            self._invalidate_grouped_cache()
            return data

    def get_torbox_progress(self, release_id: int) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT torbox_progress_json FROM releases WHERE id = ?;", (release_id,))
            row = cur.fetchone()
        try:
            data = json.loads((row["torbox_progress_json"] if row else "") or "{}")
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def increment_download_attempt(self, release_id: int) -> int:
        """Bump the failure attempt counter and return the new value."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE releases SET download_attempts = COALESCE(download_attempts, 0) + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (release_id,),
            )
            conn.commit()
            cur.execute(
                "SELECT COALESCE(download_attempts, 0) AS attempts FROM releases WHERE id = ?;",
                (release_id,),
            )
            row = cur.fetchone()
            self._invalidate_grouped_cache()
            return int(row["attempts"]) if row else 0

    def set_download_error(self, release_id: int, message: str) -> None:
        """Persist a user-visible failure reason (attempt count is bumped separately)."""
        clean = str(message or "").strip()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE releases SET download_error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (clean, release_id),
            )
            conn.commit()
            self._invalidate_grouped_cache()

    def clear_download_error(self, release_id: int) -> None:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE releases SET download_error = '', download_attempts = 0, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (release_id,),
            )
            conn.commit()
            self._invalidate_grouped_cache()

    def set_downloader_id(self, release_id: int, downloader_id: str):
        """Replace the engine handle without resetting the submission timestamp.

        Used when a queued Torbox job is promoted to an active torrent; the overall
        timeout window must keep counting from the original submission.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE releases SET downloader_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (str(downloader_id or ""), release_id),
            )
            conn.commit()
            self._invalidate_grouped_cache()

    def get_downloader_releases(self, engine: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Releases currently representing work for *engine* (active first)."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM releases
                WHERE downloader = ?
                  AND download_status IN ('DOWNLOADING', 'FAILED')
                ORDER BY CASE download_status WHEN 'DOWNLOADING' THEN 0 ELSE 1 END, updated_at DESC
                LIMIT ?;
                """,
                (str(engine or "").strip().lower(), limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_active_torbox_downloads(self) -> List[Dict[str, Any]]:
        """Releases submitted through Torbox that are still DOWNLOADING."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM releases WHERE download_status = 'DOWNLOADING' AND downloader = 'torbox' "
                "ORDER BY downloader_started_at ASC, id ASC;"
            )
            return [dict(row) for row in cur.fetchall()]

    def _prioritized_priority_map(self, cur) -> Dict[str, int]:
        """Return {lower(group name): priority} for the prioritized groups."""
        cur.execute("SELECT name, priority FROM prioritized_groups;")
        return {
            str(row["name"] or "").strip().lower(): int(row["priority"] or 0)
            for row in cur.fetchall()
            if str(row["name"] or "").strip()
        }

    def get_releases(self, status: Optional[str] = None, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        media_guard = tracked_media_clause()
        with self.get_connection() as conn:
            cur = conn.cursor()
            priority_map = self._prioritized_priority_map(cur)

            if status == "COMPLETED":
                cur.execute(f"""
                    SELECT * FROM releases 
                    WHERE (is_complete = 1 OR lower(title) LIKE '%complete%')
                      AND status NOT IN ('BLACKLISTED', 'IGNORED')
                      AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                      AND {media_guard}
                    ORDER BY updated_at DESC LIMIT ? OFFSET ?;
                """, (limit, offset))
            elif status:
                cur.execute(
                    f"SELECT * FROM releases WHERE status = ? AND {media_guard} "
                    "ORDER BY updated_at DESC LIMIT ? OFFSET ?;",
                    (status, limit, offset),
                )
            else:
                cur.execute(f"""
                    SELECT * FROM releases 
                    WHERE status NOT IN ('BLACKLISTED', 'IGNORED')
                      AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                      AND {media_guard}
                    ORDER BY 
                        CASE status WHEN 'REQUESTED' THEN 1 WHEN 'DISCOVERY' THEN 2 WHEN 'IN_CALIBRE' THEN 3 ELSE 4 END,
                        updated_at DESC 
                    LIMIT ? OFFSET ?;
                """, (limit, offset))
            rows = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT search_term, canonical_title, romaji_title, cover_url, source FROM manga_metadata_cache;")
            meta_cache = {row["search_term"].lower(): dict(row) for row in cur.fetchall()}

            from app.parser import is_complete_series_release
            for r in rows:
                r["is_complete"] = bool(r.get("is_complete", 0) or is_complete_series_release(r.get("title", "")))
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
                r["missing_volumes"] = json.loads(r["missing_vols_json"] or "[]")
                r["is_rerelease"] = bool(r.get("revision"))
                r["is_inferred"] = bool(r.get("inferred_volumes"))
                # Multi-group set membership (task-11): ANY group may be prioritized.
                r["release_groups"] = release_groups_of(r)
                r["is_prioritized"] = release_is_prioritized(r, priority_map)
                r["prioritized_priority"] = release_prioritized_priority(r, priority_map)

                # Naming model: releases.series_name stays the STABLE matching key;
                # the AniList title is exposed separately as the cosmetic display name
                # (never written back into series_name here).
                s_name = (r.get("series_name") or "").strip()
                match = meta_cache.get(s_name.lower()) if s_name else None
                if match:
                    display = display_title_from_meta(s_name, match) or s_name
                    r["display_series_name"] = display
                    if display != s_name:
                        r["canonical_series_name"] = display
                    if match.get("romaji_title"):
                        r["romaji_name"] = match["romaji_title"]
                    if match.get("cover_url") and not r.get("cover_url"):
                        r["cover_url"] = match["cover_url"]
                else:
                    r["display_series_name"] = s_name
            return rows

    def search_releases(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Literal (LIKE-escaped) search over release titles and series names.

        Used by the unified search endpoint (M2 feature 17).  LIKE wildcards in the
        user's query are escaped so ``%``/``_`` are matched literally (M3 feature
        22); ordering prefers title matches, then series-name matches, then
        anything else, newest first.
        """
        term = (query or "").strip()
        if not term:
            return []
        contains = _like_contains(term)
        prefix = f"{escape_like(term.lower())}%"
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT * FROM releases
                WHERE (lower(title) LIKE ? ESCAPE '\\'
                   OR lower(COALESCE(series_name, '')) LIKE ? ESCAPE '\\'
                   OR lower(COALESCE(release_group, '')) LIKE ? ESCAPE '\\'
                   OR EXISTS (SELECT 1 FROM json_each(COALESCE(NULLIF(release_groups_json, ''), '[]')) AS jg
                              WHERE lower(jg.value) LIKE ? ESCAPE '\\'))
                  AND {tracked_media_clause()}
                ORDER BY
                    CASE
                        WHEN lower(title) LIKE ? ESCAPE '\\' THEN 0
                        WHEN lower(COALESCE(series_name, '')) LIKE ? ESCAPE '\\' THEN 1
                        WHEN lower(COALESCE(release_group, '')) LIKE ? ESCAPE '\\' THEN 2
                        ELSE 3
                    END,
                    updated_at DESC,
                    id DESC
                LIMIT ?;
            """, (contains, contains, contains, contains, prefix, prefix, prefix, int(limit)))
            rows = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT * FROM manga_metadata_cache;")
            meta_cache = {row["search_term"].lower(): dict(row) for row in cur.fetchall()}

        from app.parser import is_complete_series_release

        for row in rows:
            row["volumes"] = json.loads(row.get("volumes_json") or "[]")
            row["missing_volumes"] = json.loads(row.get("missing_vols_json") or "[]")
            row["is_complete"] = bool(row.get("is_complete", 0) or is_complete_series_release(row.get("title", "")))
            row["release_groups"] = release_groups_of(row)
            stable = (row.get("series_name") or "").strip()
            meta = meta_cache.get(stable.lower()) if stable else None
            row["display_series_name"] = (display_title_from_meta(stable, meta) or stable) if stable else ""
        return rows

    def get_pending_requests(self, limit: int = 1000) -> List[Dict[str, Any]]:
        return self.get_releases(status="REQUESTED", limit=limit)

    def get_release_by_id(self, release_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM releases WHERE id = ?;", (release_id,))
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["volumes"] = json.loads(d.get("volumes_json") or "[]")
                d["missing_volumes"] = json.loads(d.get("missing_vols_json") or "[]")
                return d
            return None

    def count_discovery_releases(self, search: Optional[str] = None) -> int:
        """
        Returns total count of unique discovered manga series (Volume 1 or unnumbered standalones)
        which are not in Calibre, not monitored in series_tracking, and not blacklisted.
        Deduplicates by series name so each series is counted at most once.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            search_clause = ""
            params = []
            if search and search.strip():
                clean_s = _like_contains(search.strip())
                search_clause = "AND (lower(r.title) LIKE ? ESCAPE '\\' OR lower(r.series_name) LIKE ? ESCAPE '\\')"
                params.extend([clean_s, clean_s])

            query = f"""
                WITH ranked_discovery AS (
                    SELECT r.id,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(trim(coalesce(nullif(r.series_name, ''), r.title)))
                               ORDER BY 
                                   (CASE WHEN r.cover_url IS NOT NULL AND length(trim(r.cover_url)) > 0 THEN 1 ELSE 0 END) DESC,
                                   (CASE WHEN r.description IS NOT NULL AND length(trim(r.description)) > 0 THEN 1 ELSE 0 END) DESC,
                                   (CASE WHEN r.mangaupdates_url IS NOT NULL AND length(trim(r.mangaupdates_url)) > 0 THEN 1 ELSE 0 END) DESC,
                                   r.updated_at DESC,
                                   r.id DESC
                           ) as rn
                    FROM releases r
                    WHERE (r.volume_start = 1.0 OR r.volume_start IS NULL)
                      AND r.status NOT IN ('IN_CALIBRE', 'BLACKLISTED', 'IGNORED')
                      AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                      AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_tracking WHERE is_monitored = 1))
                      AND {tracked_media_clause('r')}
                      {search_clause}
                )
                SELECT count(*) FROM ranked_discovery WHERE rn = 1;
            """
            cur.execute(query, tuple(params))
            row = cur.fetchone()
            return row[0] if row else 0

    def get_discovery_releases(self, limit: int = 24, offset: int = 0, search: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Returns releases that are Volume 1 or unnumbered (standalone),
        which are NOT currently in Calibre, NOT in series_tracking, and NOT blacklisted.
        Deduplicates by series name so each potential manga series appears at most once.
        Supports pagination with limit & offset, and optional keyword search.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            search_clause = ""
            params = []
            if search and search.strip():
                clean_s = _like_contains(search.strip())
                search_clause = "AND (lower(r.title) LIKE ? ESCAPE '\\' OR lower(r.series_name) LIKE ? ESCAPE '\\')"
                params.extend([clean_s, clean_s])

            query = f"""
                WITH ranked_discovery AS (
                    SELECT r.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(trim(coalesce(nullif(r.series_name, ''), r.title)))
                               ORDER BY 
                                   (CASE WHEN r.cover_url IS NOT NULL AND length(trim(r.cover_url)) > 0 THEN 1 ELSE 0 END) DESC,
                                   (CASE WHEN r.description IS NOT NULL AND length(trim(r.description)) > 0 THEN 1 ELSE 0 END) DESC,
                                   (CASE WHEN r.mangaupdates_url IS NOT NULL AND length(trim(r.mangaupdates_url)) > 0 THEN 1 ELSE 0 END) DESC,
                                   r.updated_at DESC,
                                   r.id DESC
                           ) as rn
                    FROM releases r
                    WHERE (r.volume_start = 1.0 OR r.volume_start IS NULL)
                      AND r.status NOT IN ('IN_CALIBRE', 'BLACKLISTED', 'IGNORED')
                      AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                      AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_tracking WHERE is_monitored = 1))
                      AND {tracked_media_clause('r')}
                      {search_clause}
                )
                SELECT * FROM ranked_discovery
                WHERE rn = 1
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?;
            """
            params.extend([limit, offset])
            cur.execute(query, tuple(params))
            rows = [dict(row) for row in cur.fetchall()]

            priority_map = self._prioritized_priority_map(cur)

            from app.parser import is_complete_series_release
            for r in rows:
                r["is_complete"] = bool(r.get("is_complete", 0) or is_complete_series_release(r.get("title", "")))
                r["is_inferred"] = bool(r.get("inferred_volumes"))
                r["release_groups"] = release_groups_of(r)
                r["is_prioritized"] = release_is_prioritized(r, priority_map)
                r["prioritized_priority"] = release_prioritized_priority(r, priority_map)
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
                # Fallback to cache if description, mangaupdates_url, or canonical English title wasn't directly populated on release
                s_name = r.get("series_name")
                from app.metadata.mangaupdates import clean_description_text
                if r.get("description"):
                    r["description"] = clean_description_text(r["description"])

                if s_name:
                    # Naming model: series_name stays the STABLE matching key; the
                    # AniList canonical title is only exposed as the cosmetic
                    # display name. Nothing here may rewrite series_name.
                    meta_cached = self.get_cached_manga_metadata(s_name.lower())
                    display_name = display_title_from_meta(s_name, meta_cached) or s_name
                    r["display_series_name"] = display_name
                    if display_name != s_name:
                        r["canonical_series_name"] = display_name
                        r["romaji_name"] = (meta_cached or {}).get("romaji_title") or s_name
                    elif meta_cached and meta_cached.get("romaji_title"):
                        r["romaji_name"] = meta_cached["romaji_title"]
                    if meta_cached and not r.get("cover_url") and meta_cached.get("cover_url"):
                        r["cover_url"] = meta_cached["cover_url"]

                    # Check MangaUpdates cache
                    if not r.get("description") or not r.get("mangaupdates_url") or not str(r.get("mangaupdates_url", "")).startswith("http"):
                        mu_cached = self.get_cached_manga_metadata(f"mu:{s_name.lower()}")
                        if mu_cached:
                            if not r.get("description") and mu_cached.get("synopsis"):
                                r["description"] = clean_description_text(mu_cached["synopsis"])
                            if mu_cached.get("romaji_title") and str(mu_cached["romaji_title"]).startswith("http"):
                                r["mangaupdates_url"] = mu_cached["romaji_title"]
                else:
                    r["display_series_name"] = ""

                if not r.get("mangaupdates_url") or not str(r.get("mangaupdates_url", "")).startswith("http"):
                    from urllib.parse import quote_plus
                    target_query = s_name or r.get("title") or "manga"
                    r["mangaupdates_url"] = f"https://www.mangaupdates.com/series.html?search={quote_plus(target_query)}"
            return rows

    def update_release_details(self, release_id: int, description: Optional[str] = None, link: Optional[str] = None, cover_url: Optional[str] = None, mangaupdates_url: Optional[str] = None):
        if description:
            from app.metadata.mangaupdates import clean_description_text
            description = clean_description_text(description)
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE releases 
                SET link = COALESCE(?, link),
                    cover_url = COALESCE(?, cover_url),
                    description = COALESCE(?, description),
                    mangaupdates_url = COALESCE(?, mangaupdates_url),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (link, cover_url, description, mangaupdates_url, release_id))
            conn.commit()

    def track_discovered_series(
        self,
        series_name: str,
        release_id: Optional[int] = None,
        legacy_names: Optional[List[str]] = None,
    ) -> bool:
        """Start tracking a series under its STABLE matching key.

        ``series_name`` must already be the resolved stable key (Calibre library
        name when the series is in Calibre, otherwise the user-typed name).
        ``legacy_names`` are the names the UI/source used before resolution (e.g.
        the parsed feed title); releases stored under one of them are re-keyed to
        the stable key so grouping and Calibre matching stay coherent.  This is
        an explicit user action, so the write is deliberate and never goes
        through resolve_status().
        """
        clean = series_name.strip()
        if not clean:
            return False
        from urllib.parse import quote_plus
        rekey_alias = []
        for name in (legacy_names or []):
            alias = (name or "").strip()
            if alias and alias.lower() != clean.lower() and alias not in rekey_alias:
                rekey_alias.append(alias)
        with self.get_connection() as conn:
            cur = conn.cursor()
            default_link = f"https://www.mangaupdates.com/series.html?search={quote_plus(clean)}"
            description = ""

            # If release has a valid link or mangaupdates_url, prioritize it
            if release_id:
                cur.execute("SELECT link, mangaupdates_url, description, series_name FROM releases WHERE id = ?;", (release_id,))
                row = cur.fetchone()
                if row:
                    if row["mangaupdates_url"]:
                        default_link = row["mangaupdates_url"]
                    elif row["link"] and not row["link"].startswith("magnet:"):
                        default_link = row["link"]
                    if row["description"]:
                        description = row["description"]
                    raw_series = (row["series_name"] or "").strip()
                    if raw_series and raw_series.lower() != clean.lower() and raw_series not in rekey_alias:
                        rekey_alias.append(raw_series)

            # If description is still empty, check cache
            if not description:
                cached = self.get_cached_manga_metadata(f"mu:{clean.lower()}")
                if cached and cached.get("synopsis"):
                    description = cached["synopsis"]
                    if not default_link or "search=" in default_link:
                        default_link = cached.get("romaji_title") or default_link

            cur.execute("""
                INSERT INTO series_tracking (name, book_count, latest_index, is_monitored, website_url, description, last_sync)
                VALUES (?, 0, 0.0, 1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET 
                    is_monitored = 1,
                    website_url = CASE WHEN series_tracking.website_url = '' OR series_tracking.website_url IS NULL OR series_tracking.website_url LIKE '%search=%' THEN excluded.website_url ELSE series_tracking.website_url END,
                    description = CASE WHEN series_tracking.description = '' OR series_tracking.description IS NULL THEN excluded.description ELSE series_tracking.description END;
            """, (clean, default_link, description))

            # Explicit user action (POST /api/discovery/track): the request is written
            # verbatim, bypassing resolve_status().  The following feed polls must not
            # revert it -- that is exactly what resolve_status() now guarantees.
            if release_id:
                cur.execute(
                    "UPDATE releases SET series_name = ?, status = 'REQUESTED', updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (clean, release_id),
                )
            else:
                cur.execute(
                    "UPDATE releases SET series_name = ?, status = 'REQUESTED', updated_at = CURRENT_TIMESTAMP WHERE lower(trim(series_name)) = lower(trim(?));",
                    (clean, clean),
                )
            # Re-key the releases that carried this series under a legacy/alias name.
            for alias in rekey_alias:
                cur.execute(
                    "UPDATE releases SET series_name = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE lower(trim(series_name)) = lower(trim(?));",
                    (clean, alias),
                )
            conn.commit()
            self._invalidate_grouped_cache()
            return True

    # --- Feeds ---
    @staticmethod
    def _nyaa_feed_url_for_base(base_url: str) -> str:
        """Rewrite the seeded feed URL onto *base_url*, preserving its query string."""
        query = urllib.parse.urlsplit(SEEDED_NYAA_FEED_URL).query
        base = base_url.rstrip("/")
        return f"{base}/?{query}" if query else f"{base}/"

    def _migrate_seeded_nyaa_feed_cur(self, cur, base_url: Optional[str] = None) -> int:
        """Repoint the built-in seeded feed at the configured Nyaa base URL.

        Only the exact stock row (``SEEDED_NYAA_FEED_NAME`` + ``SEEDED_NYAA_FEED_URL``
        + rss type) is touched; user-created feeds are never modified.  Idempotent:
        after the first rewrite the stock URL no longer matches, so repeated runs are
        no-ops.  A default base leaves everything unchanged.
        """
        if base_url is None:
            cur.execute("SELECT value FROM app_settings WHERE key = 'nyaa_base_url';")
            row = cur.fetchone()
            base_url = (row["value"] if row else "") or DEFAULT_NYAA_BASE_URL
        try:
            base = normalize_nyaa_base_url(base_url)
        except NyaaBaseURLError:
            return 0
        if base.rstrip("/") == DEFAULT_NYAA_BASE_URL.rstrip("/"):
            return 0
        new_url = self._nyaa_feed_url_for_base(base)
        cur.execute(
            "SELECT id FROM feeds WHERE name = ? AND url = ? AND feed_type = 'rss';",
            (SEEDED_NYAA_FEED_NAME, SEEDED_NYAA_FEED_URL),
        )
        feed_ids = [row["id"] for row in cur.fetchall()]
        for feed_id in feed_ids:
            cur.execute("UPDATE feeds SET url = ? WHERE id = ?;", (new_url, feed_id))
        return len(feed_ids)

    def migrate_seeded_nyaa_feed(self, base_url: Optional[str] = None) -> int:
        """Public entry point used after a settings save (idempotent)."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            count = self._migrate_seeded_nyaa_feed_cur(cur, base_url=base_url)
            conn.commit()
            return count

    def set_feed_enabled(self, feed_id: int, enabled: bool) -> bool:
        """Enable/disable a feed without deleting it (scheduler skips disabled rows)."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE feeds SET enabled = ? WHERE id = ?;", (1 if enabled else 0, feed_id))
            conn.commit()
            return cur.rowcount > 0

    def get_all_feeds(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM feeds ORDER BY id ASC;")
            return [dict(r) for r in cur.fetchall()]

    def get_feed(self, feed_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM feeds WHERE id = ?;", (feed_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def add_feed(self, name: str, feed_type: str, url: str, config_json: str = "{}", auto_download: bool = True, interval: int = 60) -> int:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO feeds (name, feed_type, url, config_json, enabled, auto_download, check_interval_minutes)
                VALUES (?, ?, ?, ?, 1, ?, ?);
            """, (name, feed_type, url, config_json, 1 if auto_download else 0, interval))
            conn.commit()
            return cur.lastrowid

    def delete_feed(self, feed_id: int):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM feeds WHERE id = ?;", (feed_id,))
            conn.commit()

    def update_feed_status(self, feed_id: int, status: str, error: Optional[str] = None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE feeds SET last_checked = CURRENT_TIMESTAMP, last_status = ?, last_error = ? WHERE id = ?;", (status, error, feed_id))
            conn.commit()

    # --- Activity Logs ---
    def log_activity(self, event_type: str, message: str, details: str = "", actor: Optional[str] = None):
        """Insert an activity log row.

        ``actor`` defaults to the request-scoped ``current_actor`` ContextVar set
        by the auth middleware (the logged-in user, or ``anonymous`` when auth is
        disabled).  Background worker threads have no request context and pass
        ``actor="scheduler"`` explicitly, so automated events remain
        distinguishable from user-triggered ones.  The explicit keyword argument
        keeps every pre-existing ``log_activity(type, message, details)`` call
        site working unchanged.
        """
        resolved_actor = _resolve_audit_actor(actor)
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO activity_logs (event_type, message, details, actor) VALUES (?, ?, ?, ?);",
                (event_type, message, details, resolved_actor),
            )
            conn.commit()

    def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT ?;", (limit,))
            return [dict(r) for r in cur.fetchall()]

    # --- Users (JIT identity provisioning) ---
    def upsert_user(
        self,
        subject: str,
        username: str = "",
        email: str = "",
        display_name: str = "",
        auth_source: str = "",
    ) -> Dict[str, Any]:
        """Insert or refresh a known user keyed on the stable ``subject`` claim.

        Called on the first successful OIDC login (and on Basic authentication),
        so the audit trail can map an actor label back to a persistent identity.
        """
        clean_subject = str(subject or "").strip()
        if not clean_subject:
            raise ValueError("users.subject must not be empty")
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO users (subject, username, email, display_name, auth_source, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(subject) DO UPDATE SET
                    username = CASE WHEN excluded.username != '' THEN excluded.username ELSE users.username END,
                    email = CASE WHEN excluded.email != '' THEN excluded.email ELSE users.email END,
                    display_name = CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE users.display_name END,
                    auth_source = CASE WHEN excluded.auth_source != '' THEN excluded.auth_source ELSE users.auth_source END,
                    last_seen = CURRENT_TIMESTAMP;
            """, (clean_subject, str(username or ""), str(email or ""), str(display_name or ""), str(auth_source or "")))
            conn.commit()
            cur.execute("SELECT * FROM users WHERE subject = ?;", (clean_subject,))
            row = cur.fetchone()
            return dict(row) if row else {}

    def get_user(self, subject: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE subject = ?;", (str(subject or "").strip(),))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users ORDER BY last_seen DESC, id DESC;")
            return [dict(r) for r in cur.fetchall()]

    # --- Dashboard Summary ---
    def get_dashboard_stats(self) -> Dict[str, Any]:
        # D1 query guard: non-manga types never contribute to the manga counters
        # (unknown stays counted -- inconclusive means kept).
        media_guard = tracked_media_clause()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM series_tracking WHERE is_monitored = 1;")
            monitored_series = cur.fetchone()[0]

            cur.execute(f"SELECT count(*) FROM releases WHERE status = 'REQUESTED' AND {media_guard};")
            pending_requests = cur.fetchone()[0]

            cur.execute(f"SELECT count(*) FROM releases WHERE status = 'IN_CALIBRE' AND {media_guard};")
            acquired_books = cur.fetchone()[0]

            cur.execute(f"SELECT count(*) FROM releases WHERE download_status = 'DOWNLOADING' AND {media_guard};")
            active_downloads = cur.fetchone()[0]

            discovered_count = self.count_discovery_releases()

            return {
                "monitored_series": monitored_series,
                "pending_requests": pending_requests,
                "acquired_books": acquired_books,
                "active_downloads": active_downloads,
                "discovered_count": discovered_count
            }

    # --- Debug / Maintenance Operations ---
    def clear_all_descriptions(self) -> int:
        """Blanks stored descriptions/synopses without deleting anything.

        Covers both release rows and tracked series; the providers re-populate the
        text the next time it is needed.  Returns the number of rows blanked (the
        safe-maintenance API surfaces it in the message/toast).
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE releases SET description = '' WHERE COALESCE(description, '') != '';")
            release_count = cur.rowcount
            cur.execute("UPDATE series_tracking SET description = '' WHERE COALESCE(description, '') != '';")
            series_count = cur.rowcount
            count = release_count + series_count
            conn.commit()
            self._invalidate_grouped_cache()
            self.log_activity(
                "DEBUG_CLEAR",
                "Cleared all manga descriptions",
                f"Reset descriptions across {release_count} release(s) and {series_count} tracked series.",
            )
            return count

    def clear_discovered_series(self) -> int:
        """Clears unreviewed discovery rows (status='DISCOVERY') only.

        Scoped to rows the Discovery page shows as unreviewed: tracked series and
        blacklisted series are excluded, so the safe maintenance action can never
        remove a wishlist request (REQUESTED), an owned/in-Calibre row, an IGNORED
        row or anything belonging to the user's tracked library.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM releases
                WHERE status = 'DISCOVERY'
                  AND (series_name IS NULL OR lower(trim(series_name)) NOT IN (SELECT lower(trim(name)) FROM series_tracking))
                  AND (series_name IS NULL OR lower(trim(series_name)) NOT IN (SELECT lower(trim(name)) FROM series_blacklist));
                """
            )
            count = cur.rowcount
            conn.commit()
            self._invalidate_grouped_cache()
            self.log_activity("DEBUG_CLEAR", "Cleared discovered manga series", f"Removed {count} unreviewed discovery releases.")
            return count

    def purge_non_manga_releases(self, actor: Optional[str] = None) -> int:
        """Explicit, opt-in maintenance purge of rows typed as non-manga (D1).

        This is the ONLY code path that deletes releases by media type, and it is
        never called automatically: the startup path merely *re-types* rows (see
        :meth:`_run_media_filter_once`).  It only removes rows whose ``media_type``
        is an explicit non-manga value, so ``manga``, ``unknown`` and NULL rows are
        never touched -- inconclusive means kept.

        Reachable through ``POST /api/debug/action`` with ``action='purge-non-manga'``
        (gated by ``MANGATRAX_DEBUG_ACTIONS=1``), never from ``init_db``.
        """
        non_manga = "(" + ", ".join(f"'{t}'" for t in NON_MANGA_MEDIA_TYPES) + ")"
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT count(*) FROM releases "
                f"WHERE COALESCE(media_type, 'unknown') IN {non_manga};"
            )
            count = cur.fetchone()[0]
            if count:
                cur.execute(
                    "DELETE FROM releases "
                    f"WHERE COALESCE(media_type, 'unknown') IN {non_manga};"
                )
                conn.commit()
        finally:
            conn.close()
        if count:
            self._invalidate_grouped_cache()
            self.log_activity(
                "MEDIA_PURGE",
                "Purged non-manga releases (explicit maintenance action)",
                f"Removed {count} row(s) typed light_novel/chapter_only/novel/audiobook. "
                "Manga and unknown rows were kept.",
                actor=actor,
            )
        return count

    # --- Manga Metadata Cache (AniList) ---
    def get_cached_manga_metadata(self, search_term: str) -> Optional[Dict[str, Any]]:
        clean_key = search_term.strip().lower()
        norm_key = re.sub(r"[-–—:_/\\]+", " ", clean_key).strip()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM manga_metadata_cache 
                WHERE lower(search_term) IN (lower(?), lower(?))
                   OR lower(canonical_title) IN (lower(?), lower(?))
                   OR lower(romaji_title) IN (lower(?), lower(?))
                   OR replace(lower(search_term), '-', ' ') = ?
                   OR replace(lower(romaji_title), '-', ' ') = ?
                   OR replace(lower(canonical_title), '-', ' ') = ?;
            """, (clean_key, norm_key, clean_key, norm_key, clean_key, norm_key, norm_key, norm_key, norm_key))
            row = cur.fetchone()
            if row:
                return {
                    "search_term": row["search_term"],
                    "canonical_title": row["canonical_title"],
                    "romaji_title": row["romaji_title"],
                    "cover_url": row["cover_url"],
                    "total_volumes": row["total_volumes"],
                    "status": row["status"],
                    "synopsis": row["synopsis"],
                    "source": row["source"]
                }
            return None

    def cache_manga_metadata(self, search_term: str, data: Dict[str, Any]):
        clean_key = search_term.strip().lower()
        canonical = data.get("canonical_title") or search_term
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO manga_metadata_cache (
                    search_term, canonical_title, romaji_title, cover_url,
                    total_volumes, status, synopsis, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(search_term) DO UPDATE SET
                    canonical_title = excluded.canonical_title,
                    romaji_title = excluded.romaji_title,
                    cover_url = CASE WHEN excluded.cover_url != '' THEN excluded.cover_url ELSE manga_metadata_cache.cover_url END,
                    total_volumes = excluded.total_volumes,
                    status = excluded.status,
                    synopsis = excluded.synopsis,
                    source = excluded.source;
            """, (
                clean_key,
                canonical,
                data.get("romaji_title", ""),
                data.get("cover_url", ""),
                data.get("total_volumes"),
                data.get("status", "UNKNOWN"),
                data.get("synopsis", ""),
                data.get("source", "anilist")
            ))
            conn.commit()

    def get_all_manga_metadata_cache(self) -> Dict[str, Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM manga_metadata_cache;")
            return {row["search_term"].lower(): dict(row) for row in cur.fetchall()}

    def get_display_title(self, stable_key: str) -> str:
        """Cosmetic display title for a STABLE matching key (never used for matching).

        AniList canonical English when the cache holds an authoritative entry,
        otherwise the stable key itself.  Dual-title stable keys (e.g.
        ``"Romaji | English"``) are resolved through their candidate parts, exactly
        like the grouped builder does at render time.
        """
        stable = (stable_key or "").strip()
        if not stable:
            return ""
        from app.parser import split_series_candidate_parts
        meta = self.get_cached_manga_metadata(stable)
        if not is_authoritative_metadata(meta):
            parts = split_series_candidate_parts(stable, extra_delimiters=":", alt_separators=False)
            for part in parts:
                if part == stable:
                    continue
                candidate = self.get_cached_manga_metadata(part)
                if is_authoritative_metadata(candidate):
                    meta = candidate
                    break
        return display_title_from_meta(stable, meta) or stable

    def get_authoritative_alias_sets(self) -> Dict[str, Set[str]]:
        """Map normalized tracked stable name -> normalized names it is known by.

        The set always contains the stable name itself plus, when the cache holds
        an authoritative entry, AniList's canonical and romaji titles.  This is the
        evidence used to recognise that a stale tracked row created by the old
        rename bug and a Calibre-synced row describe the same series.
        """
        from app.parser import normalize_text

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM series_tracking WHERE name IS NOT NULL AND trim(name) != '';")
            tracked = [row["name"].strip() for row in cur.fetchall()]
            cur.execute("SELECT * FROM manga_metadata_cache;")
            cache_rows = [dict(row) for row in cur.fetchall()]

        cache_by_norm: Dict[str, Dict[str, Any]] = {}
        for row in cache_rows:
            if not is_authoritative_metadata(row):
                continue
            for field in ("search_term", "canonical_title", "romaji_title"):
                alias = (row.get(field) or "").strip()
                alias_norm = normalize_text(alias)
                if alias_norm:
                    cache_by_norm.setdefault(alias_norm, row)

        alias_sets: Dict[str, Set[str]] = {}
        for stable in tracked:
            stable_norm = normalize_text(stable)
            if not stable_norm:
                continue
            names = {stable_norm}
            meta = cache_by_norm.get(stable_norm)
            if meta:
                for field in ("canonical_title", "romaji_title"):
                    alias_norm = normalize_text(meta.get(field) or "")
                    if alias_norm:
                        names.add(alias_norm)
            alias_sets[stable_norm] = names
        return alias_sets

    def get_authoritative_alias_index(self) -> Dict[str, Set[str]]:
        """Map normalized AniList aliases -> tracked stable name(s) claiming them.

        Built from ``series_tracking`` (the stable keys) cross-referenced with
        authoritative ``manga_metadata_cache`` rows.  Keys are normalized series
        names; values are the set of stable names that own that alias.  A key
        claimed by more than one tracked series is ambiguous and must never cause
        a relabel or a merge -- except for the one provable duplicate case handled
        by ``_prefer_calibre_duplicate_claim``.
        """
        from app.parser import normalize_text

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM series_tracking WHERE name IS NOT NULL AND trim(name) != '';")
            tracked = [row["name"].strip() for row in cur.fetchall()]

        alias_sets = self.get_authoritative_alias_sets()
        index: Dict[str, Set[str]] = {}
        for stable in tracked:
            stable_norm = normalize_text(stable)
            if not stable_norm:
                continue
            for alias_norm in alias_sets.get(stable_norm) or {stable_norm}:
                index.setdefault(alias_norm, set()).add(stable)
        return index

    def _get_calibre_origin_names(self) -> Set[str]:
        """Normalized names of tracked rows that came from the Calibre library sync."""
        from app.parser import normalize_text

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM series_tracking "
                "WHERE calibre_series_id IS NOT NULL AND name IS NOT NULL AND trim(name) != '';"
            )
            return {normalize_text(row["name"]) for row in cur.fetchall() if normalize_text(row["name"])}

    @staticmethod
    def _prefer_calibre_duplicate_claim(
        claims: Set[str],
        calibre_origin: Set[str],
        alias_sets: Dict[str, Set[str]],
    ) -> Optional[str]:
        """Resolve an ambiguous alias ONLY when it provably repeats one series.

        The old rename bug could leave two ``series_tracking`` rows for the same
        manga: a stale row created under AniList's title and the real Calibre-synced
        row.  That is safe to repair only when:

        * exactly one claim is a Calibre-origin tracked series, and
        * every other claim is a *bidirectional* AniList alias of it (each name is
          in the other's authoritative canonical+romaji set).

        Anything less certain returns ``None`` so genuinely distinct series are
        never merged.
        """
        from app.parser import normalize_text

        calibre_claims = [claim for claim in claims if normalize_text(claim) in calibre_origin]
        if len(calibre_claims) != 1:
            return None
        target = calibre_claims[0]
        target_norm = normalize_text(target)
        target_aliases = alias_sets.get(target_norm) or {target_norm}
        for other in claims:
            other_norm = normalize_text(other)
            if other_norm == target_norm:
                continue
            other_aliases = alias_sets.get(other_norm) or {other_norm}
            if other_norm not in target_aliases or target_norm not in other_aliases:
                return None
        return target

    def repair_rekeyed_series_names(self, actor: Optional[str] = None) -> int:
        """Guarded, idempotent legacy repair for the P3a rename bug.

        Older versions let AniList rewrite ``releases.series_name`` to its
        canonical English title (``Dungeon Meshi`` -> ``Delicious in Dungeon``).
        That broke Calibre matching because the stored key no longer matched the
        user's library name.  This repairs *only* the rows that a tracked series
        unambiguously claims as one of its AniList aliases, re-keying them back
        to the tracked STABLE name:

        * a release name claimed by exactly one tracked series is re-keyed;
        * when the old bug left both a stale tracked row under the AniList title
          and the real Calibre-synced row, the release is re-keyed to the Calibre
          name (provable bidirectional-alias duplicate, see
          ``_prefer_calibre_duplicate_claim``);
        * any other multi-claim name is skipped with a warning (never silently
          merge two genuinely distinct series);
        * rows already stored under the stable key are untouched, so running the
          repair repeatedly is a no-op after the first pass.

        Safe to call on every startup and from the maintenance endpoint; it logs
        what it changed and returns the number of re-keyed release rows.
        """
        from app.parser import normalize_text

        alias_index = self.get_authoritative_alias_index()
        alias_sets = self.get_authoritative_alias_sets()
        calibre_origin = self._get_calibre_origin_names()
        repaired = 0
        examples: List[str] = []
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT series_name FROM releases "
                "WHERE series_name IS NOT NULL AND trim(series_name) != '';"
            )
            release_names = [row["series_name"] for row in cur.fetchall()]

            for rel_name in release_names:
                rel_norm = normalize_text(rel_name)
                if not rel_norm:
                    continue
                claims = alias_index.get(rel_norm) or set()
                stable: Optional[str] = None
                if len(claims) == 1:
                    stable = next(iter(claims))
                elif len(claims) > 1:
                    stable = self._prefer_calibre_duplicate_claim(claims, calibre_origin, alias_sets)
                    if stable is None:
                        logger.warning(
                            "Series key repair: '%s' is an AniList alias of multiple tracked series %s; "
                            "skipping it rather than merging distinct series.",
                            rel_name, sorted(claims),
                        )
                        continue
                    logger.info(
                        "Series key repair: '%s' is a pre-fix duplicate of Calibre series '%s' "
                        "(bidirectional AniList alias evidence); re-keying to it.",
                        rel_name, stable,
                    )
                if not stable:
                    continue
                if normalize_text(stable) == rel_norm:
                    continue  # already the stable matching key
                cur.execute(
                    "UPDATE releases SET series_name = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE lower(trim(series_name)) = lower(trim(?));",
                    (stable, rel_name),
                )
                if cur.rowcount:
                    repaired += cur.rowcount
                    examples.append(f"'{rel_name}' -> '{stable}'")
            conn.commit()

        self._invalidate_grouped_cache()
        if repaired:
            logger.info(
                "Series key repair: re-keyed %d release row(s) from AniList display titles "
                "back to their tracked stable matching key.", repaired,
            )
            self.log_activity(
                "SERIES_KEYS_REPAIRED",
                f"Re-keyed {repaired} release(s) from AniList display titles back to their tracked series' stable matching key.",
                "; ".join(examples[:10]),
                actor=actor,
            )
        else:
            logger.debug("Series key repair: no legacy re-keyed releases found.")
        return repaired

    # --- Group Releases by Series ---
    def get_releases_grouped_by_series(
        self,
        status: Optional[str] = None,
        prioritized_only: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Groups releases under canonical series, aggregating all bundle volumes,
        highlighting owned vs missing volumes against Calibre tracking.
        If prioritized_only is True, filters to series having releases from prioritized groups.
        Supports fast pagination with limit & offset and instant keyword search.

        READ-ONLY: this is a GET path and performs no persistence.  Complete-series
        volume expansion / missing-volume reconciliation is still computed in memory
        for the response, but the resulting UPDATEs are applied only by the explicit
        maintenance entry point :meth:`reconcile_release_volume_state`.
        """
        groups, _persisted = self._build_grouped_releases(
            status=status,
            prioritized_only=prioritized_only,
            limit=limit,
            offset=offset,
            search=search,
            persist=False,
        )
        return groups

    def reconcile_release_volume_state(self) -> int:
        """Persist the volume expansion / reconciliation that a read computes in memory.

        ``get_releases_grouped_by_series()`` decorates releases on the fly (expanding a
        complete-series compilation from cached metadata, then reconciling missing
        volumes against Calibre ownership) without touching the database.  This
        maintenance path runs the exact same computation with persistence enabled and
        returns the number of release rows written, so the database converges to what
        the UI already shows.  It is idempotent: a second run writes nothing.
        """
        _groups, persisted = self._build_grouped_releases(persist=True)
        if persisted:
            self._invalidate_grouped_cache()
        return persisted

    def _build_grouped_releases(
        self,
        status: Optional[str] = None,
        prioritized_only: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
        search: Optional[str] = None,
        persist: bool = False
    ) -> Tuple[List[Dict[str, Any]], int]:
        from app.parser import normalize_text, split_series_candidate_parts

        cache_key = f"{status}_{prioritized_only}"
        now = time.time()
        if not persist and not search and cache_key in self._grouped_cache:
            cached_time, cached_data = self._grouped_cache[cache_key]
            if now - cached_time < 5.0:
                if limit is not None:
                    return cached_data[offset : offset + limit], 0
                return cached_data, 0

        persisted = 0
        media_guard = tracked_media_clause()
        prioritized_guard = prioritized_release_clause()
        with self.get_connection() as conn:
            cur = conn.cursor()
            priority_map = self._prioritized_priority_map(cur)

            if prioritized_only:
                if status == "COMPLETED":
                    cur.execute(f"""
                        SELECT * FROM releases 
                        WHERE (is_complete = 1 OR lower(title) LIKE '%complete%')
                          AND status NOT IN ('BLACKLISTED', 'IGNORED')
                          AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                          AND {prioritized_guard}
                          AND {media_guard}
                        ORDER BY updated_at DESC;
                    """)
                elif status:
                    cur.execute(f"""
                        SELECT * FROM releases 
                        WHERE status = ? AND {prioritized_guard}
                          AND {media_guard}
                        ORDER BY updated_at DESC;
                    """, (status,))
                else:
                    cur.execute(f"""
                        SELECT * FROM releases 
                        WHERE status NOT IN ('BLACKLISTED', 'IGNORED')
                          AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                          AND {prioritized_guard}
                          AND {media_guard}
                        ORDER BY 
                            CASE status WHEN 'REQUESTED' THEN 1 WHEN 'DISCOVERY' THEN 2 WHEN 'IN_CALIBRE' THEN 3 ELSE 4 END,
                            updated_at DESC;
                    """)
            else:
                if status == "COMPLETED":
                    cur.execute(f"""
                        SELECT * FROM releases 
                        WHERE (is_complete = 1 OR lower(title) LIKE '%complete%')
                          AND status NOT IN ('BLACKLISTED', 'IGNORED')
                          AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                          AND {media_guard}
                        ORDER BY updated_at DESC;
                    """)
                elif status:
                    cur.execute(
                        f"SELECT * FROM releases WHERE status = ? AND {media_guard} ORDER BY updated_at DESC;",
                        (status,),
                    )
                else:
                    cur.execute(f"""
                        SELECT * FROM releases 
                        WHERE status NOT IN ('BLACKLISTED', 'IGNORED')
                          AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                          AND {media_guard}
                        ORDER BY 
                            CASE status WHEN 'REQUESTED' THEN 1 WHEN 'DISCOVERY' THEN 2 WHEN 'IN_CALIBRE' THEN 3 ELSE 4 END,
                            updated_at DESC;
                    """)

            raw_releases = [dict(r) for r in cur.fetchall()]
            from app.parser import is_complete_series_release
            for r in raw_releases:
                r["is_complete"] = bool(r.get("is_complete", 0) or is_complete_series_release(r.get("title", "")))
                r["is_inferred"] = bool(r.get("inferred_volumes"))
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
                r["missing_volumes"] = json.loads(r["missing_vols_json"] or "[]")
                # Multi-group set membership (task-11).
                r["release_groups"] = release_groups_of(r)
                r["is_prioritized"] = release_is_prioritized(r, priority_map)
                r["prioritized_priority"] = release_prioritized_priority(r, priority_map)

            cur.execute("SELECT * FROM series_tracking;")
            tracked_map = {}
            norm_tracked_map = {}
            for row in cur.fetchall():
                t_row = dict(row)
                t_name = t_row["name"]
                t_lower = t_name.lower()
                tracked_map[t_lower] = t_row
                norm_tracked_map[normalize_text(t_name)] = t_row
                norm_tracked_map[t_lower] = t_row

            cur.execute("SELECT * FROM manga_metadata_cache;")
            cache_map = {}
            norm_cache_map = {}
            for row in cur.fetchall():
                c_row = dict(row)
                c_term = c_row["search_term"]
                c_lower = c_term.lower()
                cache_map[c_lower] = c_row
                norm_cache_map[normalize_text(c_term)] = c_row
                norm_cache_map[c_lower] = c_row
                if c_row.get("canonical_title"):
                    ct = c_row["canonical_title"].lower()
                    cache_map[ct] = c_row
                    norm_cache_map[normalize_text(ct)] = c_row
                if c_row.get("romaji_title"):
                    rt = c_row["romaji_title"].lower()
                    cache_map[rt] = c_row
                    norm_cache_map[normalize_text(rt)] = c_row

            # Alias index: normalized AniList alias -> the tracked STABLE name that
            # claims it.  Used only to recover an unrepaired legacy row's stable key
            # at read time (the startup repair persists the same change).  Ambiguous
            # aliases (claimed by >1 tracked series) never relabel anything.
            alias_claims: Dict[str, Set[str]] = {}
            for t_row in tracked_map.values():
                stable_name = t_row.get("name") or ""
                stable_norm = normalize_text(stable_name)
                if not stable_norm:
                    continue
                alias_claims.setdefault(stable_norm, set()).add(stable_name)
                t_meta = norm_cache_map.get(stable_norm) or cache_map.get(stable_name.lower())
                if not is_authoritative_metadata(t_meta):
                    continue
                for field in ("canonical_title", "romaji_title"):
                    alias_norm = normalize_text(t_meta.get(field) or "")
                    if alias_norm:
                        alias_claims.setdefault(alias_norm, set()).add(stable_name)
            alias_to_stable = {
                alias: next(iter(stables))
                for alias, stables in alias_claims.items()
                if len(stables) == 1
            }

            groups_dict: Dict[str, Dict[str, Any]] = {}

            for rel in raw_releases:
                s_name = (rel.get("series_name") or "Uncategorized").strip()
                s_norm = normalize_text(s_name) or "uncategorized"

                # Extract candidate parts from delimiters (|, /, //, -, —) and parentheses
                candidate_parts = []
                parts = split_series_candidate_parts(s_name)
                if len(parts) > 1:
                    candidate_parts.extend(parts)

                if "(" in s_name and ")" in s_name:
                    m_paren = re.findall(r"\((.*?)\)", s_name)
                    base_name = re.sub(r"\(.*?\)", "", s_name).strip()
                    if base_name:
                        candidate_parts.append(base_name)
                    candidate_parts.extend([p.strip() for p in m_paren if p.strip()])

                meta_match = norm_cache_map.get(s_norm) or cache_map.get(s_name.lower()) or {}
                if (not meta_match or not meta_match.get("total_volumes")) and candidate_parts:
                    for cand in candidate_parts:
                        cand_norm = normalize_text(cand)
                        cand_meta = norm_cache_map.get(cand_norm) or cache_map.get(cand.lower())
                        if cand_meta and (cand_meta.get("total_volumes") or not meta_match):
                            meta_match = cand_meta
                            if cand_meta.get("total_volumes"):
                                break

                # Use local cache for metadata matching (no synchronous network calls during page load)

                calibre_match = norm_tracked_map.get(s_norm)
                if not calibre_match and meta_match.get("canonical_title"):
                    calibre_match = norm_tracked_map.get(normalize_text(meta_match["canonical_title"])) or tracked_map.get(meta_match["canonical_title"].lower())

                if not calibre_match and candidate_parts:
                    for cand in candidate_parts:
                        cand_norm = normalize_text(cand)
                        if cand_norm in norm_tracked_map:
                            calibre_match = norm_tracked_map[cand_norm]
                            break
                        if cand.lower() in tracked_map:
                            calibre_match = tracked_map[cand.lower()]
                            break
                        cand_meta = norm_cache_map.get(cand_norm) or cache_map.get(cand.lower())
                        if cand_meta and cand_meta.get("canonical_title"):
                            cand_canon_norm = normalize_text(cand_meta["canonical_title"])
                            if cand_canon_norm in norm_tracked_map:
                                calibre_match = norm_tracked_map[cand_canon_norm]
                                break

                # Strict substring containment fallback against tracked series (requires >= 70% coverage)
                if not calibre_match and len(s_norm) >= 6:
                    for t_norm, t_row in norm_tracked_map.items():
                        if len(t_norm) >= 6:
                            len_ratio = min(len(t_norm), len(s_norm)) / max(len(t_norm), len(s_norm))
                            if len_ratio >= 0.70 and (t_norm in s_norm or s_norm in t_norm):
                                calibre_match = t_row
                                break

                # --- Naming model ---
                # releases.series_name is the STABLE matching key.  Recover the
                # tracked stable name when this row still carries a legacy AniList
                # alias, and prefer a Calibre/tracked hit when one exists.
                stable_name = alias_to_stable.get(s_norm) or s_name
                if calibre_match and calibre_match.get("name"):
                    stable_name = calibre_match["name"]

                # DISPLAY TITLE: cosmetic only, resolved from authoritative metadata
                # for the series; falls back to the stable key.  Never a matching key.
                display_meta = None
                if stable_name:
                    display_meta = (
                        norm_cache_map.get(normalize_text(stable_name))
                        or cache_map.get(stable_name.lower())
                    )
                if not is_authoritative_metadata(display_meta):
                    display_meta = meta_match if is_authoritative_metadata(meta_match) else display_meta
                display_title = display_title_from_meta(stable_name, display_meta) or s_name
                canonical_key = normalize_text(display_title) or "uncategorized"
                romaji_title = (display_meta or {}).get("romaji_title") or (
                    s_name if s_name.lower() != display_title.lower() else ""
                )
                if not romaji_title and candidate_parts:
                    other_parts = [p for p in candidate_parts if normalize_text(p) != normalize_text(display_title)]
                    if other_parts:
                        romaji_title = other_parts[0]

                # Decorate release itself.  NOTE: rel["series_name"] deliberately keeps
                # the stored matching key; the display title is an additional field.
                rel["canonical_series_name"] = display_title
                rel["display_series_name"] = display_title
                rel["stable_series_name"] = stable_name
                rel["romaji_name"] = romaji_title

                if canonical_key not in groups_dict:
                    cover = (
                        meta_match.get("cover_url")
                        or rel.get("cover_url")
                        or (calibre_match.get("cover_url") if calibre_match else "")
                        or ""
                    )

                    groups_dict[canonical_key] = {
                        "series_key": canonical_key,
                        # series_name is the DISPLAY title (cosmetic); stable_name is
                        # the matching key used for Track/Backfill/Blacklist actions.
                        "series_name": display_title,
                        "stable_name": stable_name,
                        "romaji_name": romaji_title,
                        "raw_name": s_name,
                        "cover_url": cover,
                        "in_calibre": calibre_match is not None,
                        "is_monitored": bool(calibre_match.get("is_monitored", 0)) if calibre_match else False,
                        "calibre_series_id": calibre_match.get("id") if calibre_match else None,
                        "calibre_book_count": calibre_match.get("book_count", 0) if calibre_match else 0,
                        "calibre_latest_index": calibre_match.get("latest_index", 0.0) if calibre_match else 0.0,
                        "total_volumes": meta_match.get("total_volumes"),
                        "status": meta_match.get("status", "UNKNOWN"),
                        "synopsis": meta_match.get("synopsis", "") or (calibre_match.get("description", "") if calibre_match else ""),
                        "releases": [],
                        "seen_release_sigs": set(),
                        "all_volumes_set": set(),
                        "missing_volumes_set": set(),
                        "is_complete": False,
                        "has_complete_release": False,
                        "is_publishing_finished": (meta_match.get("status") or "").upper() in ("FINISHED", "COMPLETED"),
                        "has_wanted": False,
                        "has_discovery": False,
                        "has_in_calibre": False,
                        "has_prioritized": False,
                        # Highest configured priority among any release's group set.
                        "prioritized_priority": 0
                    }

                group = groups_dict[canonical_key]
                # A later release in the same display group may carry the Calibre/tracked
                # identity or the resolved stable key; upgrade the group without ever
                # changing the display title.
                if calibre_match and not group.get("in_calibre"):
                    group["in_calibre"] = True
                    group["is_monitored"] = bool(calibre_match.get("is_monitored", 0))
                    group["calibre_series_id"] = calibre_match.get("id")
                    group["calibre_book_count"] = calibre_match.get("book_count", 0)
                    group["calibre_latest_index"] = calibre_match.get("latest_index", 0.0)
                    group["stable_name"] = calibre_match.get("name") or group.get("stable_name")
                elif (
                    normalize_text(group.get("stable_name") or "") != normalize_text(stable_name)
                    and normalize_text(group.get("stable_name") or "") == normalize_text(group.get("raw_name") or "")
                    and normalize_text(stable_name) != normalize_text(s_name)
                ):
                    # Prefer a recovered stable key over the raw stored name.
                    group["stable_name"] = stable_name
                if not group.get("romaji_name") and romaji_title:
                    group["romaji_name"] = romaji_title
                if not group.get("cover_url") and rel.get("cover_url"):
                    group["cover_url"] = rel["cover_url"]

                if rel.get("is_complete"):
                    group["is_complete"] = True
                    group["has_complete_release"] = True

                    # Dynamically expand volumes for complete series releases if empty
                    if not rel["volumes"]:
                        tot_v = group.get("total_volumes") or meta_match.get("total_volumes")
                        if tot_v and int(tot_v) > 0:
                            v_count = int(tot_v)
                            rel["volume_start"] = 1.0
                            rel["volume_end"] = float(v_count)
                            rel["volumes"] = [float(v) for v in range(1, v_count + 1)]
                            rel["is_bundle"] = True

                            owned_vols_raw = calibre_match.get("owned_volumes_json") if calibre_match else None
                            owned_vols = set(json.loads(owned_vols_raw)) if owned_vols_raw else set()
                            if owned_vols:
                                rel["missing_volumes"] = [v for v in rel["volumes"] if round(float(v), 2) not in owned_vols]
                            else:
                                if not rel["missing_volumes"] and rel["status"] in ("REQUESTED", "DISCOVERY"):
                                    rel["missing_volumes"] = list(rel["volumes"])

                            candidate_st = "IN_CALIBRE" if not rel["missing_volumes"] else ("REQUESTED" if calibre_match else rel["status"])
                            # Automated expansion side-effect: never downgrade a sticky status.
                            rel["status"] = resolve_status(rel["status"], candidate_st)

                            # A GET must not write: only the explicit maintenance path persists.
                            if persist:
                                try:
                                    cur.execute("""
                                        UPDATE releases 
                                        SET is_bundle = 1, is_complete = 1, volume_start = 1.0, volume_end = ?, 
                                            volumes_json = ?, missing_vols_json = ?, status = ? 
                                        WHERE id = ?;
                                    """, (float(v_count), json.dumps(rel["volumes"]), json.dumps(rel["missing_volumes"]), rel["status"], rel["id"]))
                                    conn.commit()
                                    persisted += 1
                                except Exception:
                                    pass

                # Reconcile rel["missing_volumes"] and status against Calibre owned volumes
                if calibre_match and rel.get("volumes"):
                    owned_vols_raw = calibre_match.get("owned_volumes_json")
                    owned_vols = set(json.loads(owned_vols_raw)) if owned_vols_raw else set()
                    if owned_vols:
                        actual_missing = [v for v in rel["volumes"] if round(float(v), 2) not in owned_vols]
                        if actual_missing != rel["missing_volumes"]:
                            rel["missing_volumes"] = actual_missing
                            candidate_rel_st = "IN_CALIBRE" if not actual_missing else ("REQUESTED" if calibre_match.get("is_monitored", 1) else "NEW")
                            # Automated reconcile side-effect: never downgrade a sticky status.
                            new_rel_st = resolve_status(rel["status"], candidate_rel_st)
                            rel["status"] = new_rel_st
                            # A GET must not write: only the explicit maintenance path persists.
                            if persist:
                                try:
                                    cur.execute("UPDATE releases SET missing_vols_json = ?, status = ? WHERE id = ?;", (json.dumps(actual_missing), new_rel_st, rel["id"]))
                                    conn.commit()
                                    persisted += 1
                                except Exception:
                                    pass

                # Deduplicate releases in group: same guid or (download_url, volume set,
                # sorted GROUP SET).  The re-release revision is part of the identity so
                # a "(f2)" fix is never silently collapsed into the original; the
                # supersede pass below keeps the higher revision visible where they do
                # coincide.
                sig = rel.get("guid") or (
                    rel.get("download_url") or rel.get("link") or rel.get("title"),
                    tuple(sorted(rel.get("volumes", []))),
                    tuple(sorted(g.lower() for g in release_groups_of(rel))),
                    rel.get("revision") or 0,
                )
                rel["is_superseded"] = False
                rel["is_rerelease"] = bool(rel.get("revision"))
                if sig not in group["seen_release_sigs"]:
                    group["seen_release_sigs"].add(sig)
                    group["releases"].append(rel)

                for v in rel["volumes"]:
                    group["all_volumes_set"].add(v)
                for mv in rel["missing_volumes"]:
                    group["missing_volumes_set"].add(mv)

                if rel["status"] == "REQUESTED":
                    group["has_wanted"] = True
                elif rel["status"] == "DISCOVERY":
                    group["has_discovery"] = True
                elif rel["status"] == "IN_CALIBRE":
                    group["has_in_calibre"] = True

                if rel.get("is_prioritized"):
                    group["has_prioritized"] = True
                # Highest-ranked matching prioritized group wins the ordering.
                rel_priority = int(rel.get("prioritized_priority") or 0)
                if rel_priority > group["prioritized_priority"]:
                    group["prioritized_priority"] = rel_priority

                if not group["cover_url"] and rel.get("cover_url"):
                    group["cover_url"] = rel["cover_url"]

            result_groups = []
            for group in groups_dict.values():
                # Re-release supersede: releases sharing series+volumes+group are the
                # same collection at different revisions ("(f)", "(f2)", ...).  The
                # highest revision wins and older revisions are flagged -- never
                # deleted, never status-rewritten.
                by_identity: Dict[Any, List[Dict[str, Any]]] = {}
                for rel_row in group["releases"]:
                    identity = (
                        tuple(sorted(rel_row.get("volumes") or [])),
                        tuple(sorted(g.lower() for g in release_groups_of(rel_row))),
                    )
                    by_identity.setdefault(identity, []).append(rel_row)
                for candidates in by_identity.values():
                    if len(candidates) < 2:
                        continue
                    highest_revision = max((c.get("revision") or 0) for c in candidates)
                    if highest_revision <= 0:
                        continue
                    for cand in candidates:
                        cand["is_superseded"] = (cand.get("revision") or 0) < highest_revision
                group["releases"].sort(
                    key=lambda r: (bool(r.get("is_superseded")), -(r.get("revision") or 0))
                )
                group["all_volumes"] = sorted(list(group["all_volumes_set"]))
                group["missing_volumes"] = sorted(list(group["missing_volumes_set"]))
                group["releases_count"] = len(group["releases"])
                group["wanted_count"] = sum(1 for r in group["releases"] if r["status"] == "REQUESTED")
                group["prioritized_count"] = sum(1 for r in group["releases"] if r.get("is_prioritized"))

                # Fallback: if owned volumes exist in Calibre or release was marked IN_CALIBRE, ensure in_calibre is True
                owned_count = len(set(group["all_volumes"]) - set(group["missing_volumes"]))
                if not group["in_calibre"] and (group["has_in_calibre"] or owned_count > 0):
                    group["in_calibre"] = True
                    if not group.get("calibre_book_count"):
                        group["calibre_book_count"] = owned_count

                # Remove temporary sets before passing to Jinja templates
                del group["all_volumes_set"]
                del group["missing_volumes_set"]
                del group["seen_release_sigs"]
                result_groups.append(group)

            def sort_key(g):
                if g["has_wanted"] and g["has_prioritized"]:
                    priority = 1
                elif g["has_wanted"]:
                    priority = 2
                elif g["has_prioritized"]:
                    priority = 3
                elif g["is_monitored"]:
                    priority = 4
                elif g["has_discovery"]:
                    priority = 5
                elif g["in_calibre"]:
                    priority = 6
                else:
                    priority = 7
                # Within a tier, the user's highest-ranked matching group sorts first.
                return (priority, -(g.get("prioritized_priority") or 0), g["series_name"].lower())

            result_groups.sort(key=sort_key)

            if not persist and not search:
                self._grouped_cache[cache_key] = (now, list(result_groups))

            if search and search.strip():
                clean_s = search.strip().lower()
                filtered = [
                    g for g in result_groups
                    if clean_s in g["series_name"].lower()
                    or clean_s in (g.get("stable_name") or "").lower()
                    or clean_s in (g.get("raw_name") or "").lower()
                    or clean_s in (g.get("romaji_name") or "").lower()
                    or clean_s in (g.get("synopsis") or "").lower()
                ]
                if limit is not None:
                    return filtered[offset : offset + limit], persisted
                return filtered, persisted

            if limit is not None:
                return result_groups[offset : offset + limit], persisted
            return result_groups, persisted

    def count_releases_grouped_by_series(
        self,
        status: Optional[str] = None,
        prioritized_only: bool = False,
        search: Optional[str] = None
    ) -> int:
        groups = self.get_releases_grouped_by_series(status=status, prioritized_only=prioritized_only, search=search)
        return len(groups)

    # --- Prioritized Release Groups ---
    def get_prioritized_groups(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            # release_count is SET-based: a joint tag "(1r0n, Ushi)" counts for both.
            cur.execute(f"""
                SELECT pg.*, 
                       (SELECT count(*) FROM releases r
                         WHERE (lower(COALESCE(r.release_group, '')) = lower(pg.name)
                                OR EXISTS (SELECT 1 FROM json_each(COALESCE(NULLIF(r.release_groups_json, ''), '[]')) AS jg
                                           WHERE lower(jg.value) = lower(pg.name)))
                           AND {tracked_media_clause('r')}) as release_count
                FROM prioritized_groups pg
                ORDER BY pg.priority DESC, pg.name COLLATE NOCASE ASC;
            """)
            return [dict(r) for r in cur.fetchall()]

    def add_prioritized_group(self, name: str, notes: str = "", priority: int = 1) -> bool:
        clean = name.strip()
        if not clean:
            return False
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO prioritized_groups (name, notes, priority, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET notes = excluded.notes, priority = excluded.priority;
            """, (clean, notes, priority))
            conn.commit()
            self._invalidate_grouped_cache()
            return True

    def remove_prioritized_group(self, group_id: int):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM prioritized_groups WHERE id = ?;", (group_id,))
            conn.commit()
            self._invalidate_grouped_cache()

    def is_group_prioritized(self, group_name: str) -> bool:
        """True when *group_name* (or ANY group in a joint tag) is prioritized.

        ``"Ushi"`` and ``"1r0n, Ushi"`` both answer True when either component is a
        prioritized group; the membership test is set-based, not whole-string.
        """
        if not group_name:
            return False
        from app.parser import split_release_group_value

        candidates = split_release_group_value(group_name) or [str(group_name).strip()]
        with self.get_connection() as conn:
            cur = conn.cursor()
            for candidate in candidates:
                cur.execute(
                    "SELECT id FROM prioritized_groups WHERE lower(name) = lower(?);",
                    (candidate,),
                )
                if cur.fetchone() is not None:
                    return True
            return False

    def get_prioritized_releases(self, status: Optional[str] = None, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        """Returns releases published by prioritized release groups.

        Set membership: a release tagged ``(1r0n, Ushi)`` is included when EITHER
        group is prioritized.  Rows are ordered by the user's highest-ranked
        matching group (descending), then the usual status/recency order.
        """
        media_guard = tracked_media_clause()
        prioritized_guard = prioritized_release_clause()
        with self.get_connection() as conn:
            cur = conn.cursor()
            priority_map = self._prioritized_priority_map(cur)
            if status == "COMPLETED":
                cur.execute(f"""
                    SELECT * FROM releases 
                    WHERE (is_complete = 1 OR lower(title) LIKE '%complete%')
                      AND status NOT IN ('BLACKLISTED', 'IGNORED')
                      AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                      AND {prioritized_guard}
                      AND {media_guard}
                    ORDER BY updated_at DESC LIMIT ? OFFSET ?;
                """, (limit, offset))
            elif status:
                cur.execute(f"""
                    SELECT * FROM releases 
                    WHERE status = ? AND {prioritized_guard}
                      AND {media_guard}
                    ORDER BY updated_at DESC LIMIT ? OFFSET ?;
                """, (status, limit, offset))
            else:
                cur.execute(f"""
                    SELECT * FROM releases 
                    WHERE status NOT IN ('BLACKLISTED', 'IGNORED')
                      AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                      AND {prioritized_guard}
                      AND {media_guard}
                    ORDER BY 
                        CASE status WHEN 'REQUESTED' THEN 1 WHEN 'DISCOVERY' THEN 2 WHEN 'IN_CALIBRE' THEN 3 ELSE 4 END,
                        updated_at DESC 
                    LIMIT ? OFFSET ?;
                """, (limit, offset))
            rows = [dict(r) for r in cur.fetchall()]
            from app.parser import is_complete_series_release
            for r in rows:
                r["is_complete"] = bool(r.get("is_complete", 0) or is_complete_series_release(r.get("title", "")))
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
                r["missing_volumes"] = json.loads(r["missing_vols_json"] or "[]")
                r["release_groups"] = release_groups_of(r)
                r["is_prioritized"] = True
                r["prioritized_priority"] = release_prioritized_priority(r, priority_map)
            # Highest-ranked matching group first (stable: SQL order kept within a rank).
            rows.sort(key=lambda r: -(r.get("prioritized_priority") or 0))
            return rows

    def reset_all_data(self) -> Dict[str, Any]:
        """
        Completely resets all application data: clears releases, tracked series,
        blacklist entries, activity logs, and metadata caches, while restoring default feeds and groups.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM releases;")
            cur.execute("DELETE FROM series_tracking;")
            cur.execute("DELETE FROM series_blacklist;")
            cur.execute("DELETE FROM activity_logs;")
            cur.execute("DELETE FROM manga_metadata_cache;")

            # Reset feeds to defaults
            cur.execute("DELETE FROM feeds;")
            cur.execute("""
                INSERT INTO feeds (name, feed_type, url, config_json, enabled, auto_download, check_interval_minutes)
                VALUES (?, ?, ?, ?, 1, 1, 60);
            """, (
                SEEDED_NYAA_FEED_NAME,
                "rss",
                SEEDED_NYAA_FEED_URL,
                json.dumps({"description": "Monitors CBZ Manga releases."}),
            ))
            # Re-apply the configured mirror base after the reset (idempotent).
            try:
                self._migrate_seeded_nyaa_feed_cur(cur)
            except Exception:
                pass

            # Reset prioritized groups to standard defaults
            cur.execute("DELETE FROM prioritized_groups;")
            default_groups = [
                ("danke-Empire", "High quality digital manga releases"),
                ("LuCirAn", "Official digital manga rips"),
                ("1stKind", "Clean digital CBZ releases"),
                ("TenmaLand", "Multi-language digital manga rips"),
                ("Stick", "Verified publisher digital releases")
            ]
            cur.executemany("INSERT INTO prioritized_groups (name, notes) VALUES (?, ?);", default_groups)

            # Reset backfill status in settings
            cur.execute("UPDATE app_settings SET value = 'idle' WHERE key = 'backfill_status';")
            cur.execute("UPDATE app_settings SET value = '' WHERE key = 'backfill_progress';")

            # Log system reset
            cur.execute("""
                INSERT INTO activity_logs (event_type, message, details, actor, created_at)
                VALUES ('SYSTEM_RESET', 'All application data, releases, and caches have been reset.', NULL, 'system', CURRENT_TIMESTAMP);
            """)
            conn.commit()
            self._invalidate_grouped_cache()
            return {"status": "success", "message": "All application data and release history have been reset successfully."}
