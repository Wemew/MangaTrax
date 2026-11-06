#!/usr/bin/env python3
"""D1 regression suite: non-destructive media typing replaces the startup purge.

Before this change, EVERY database initialisation ran a title-only purge:

    DELETE FROM releases WHERE id = ?   -- for every row whose title tripped
                                        -- is_light_novel() / is_chapter_only()

There was no status filter, so a single false positive silently destroyed a
tracked release (a REQUESTED wishlist row, an IGNORED decision, a Calibre-matched
row).  The replacement (RELEASE_DETECTION_SPEC.md §4.7 / §9.6-D1) is strictly
non-destructive:

* ``releases.media_type`` is added by a guarded PRAGMA migration;
* a one-shot, marker-gated pass RE-TYPES only rows still ``unknown`` - it never
  deletes and never writes ``status`` (the resolve_status() precedence model
  stays the single owner of status transitions);
* the tracked views filter non-manga types out (``unknown`` stays visible:
  inconclusive means kept);
* a separate, explicit, opt-in maintenance action is the only thing that can
  delete non-manga rows, and it never runs automatically.

Run through the assert-aware runner (assertions compiled live, optimize=0):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import inspect
import os
import re
import sqlite3
import sys
import tempfile
import shutil
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
for _path in (BACKEND_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import database as database_module
from app.database import (
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_CHAPTER_ONLY,
    MEDIA_TYPE_LIGHT_NOVEL,
    MEDIA_TYPE_MANGA,
    MEDIA_TYPE_UNKNOWN,
    MangaDatabase,
    NON_MANGA_MEDIA_TYPES,
    tracked_media_clause,
)

# Titles that trip the two title-only heuristics the old purge used.
LN_TITLE = "Overlord v01-14 [Yen On] [Lu自主规制]"
CHAPTER_TITLE = "Chainsaw Man Ch. 180"
MANGA_TITLE = "Chainsaw Man Vol. 5"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _create_legacy_db(path, rows, settings=None):
    """Create a pre-media_type database exactly like the previous release schema."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
            series_name TEXT, release_group TEXT DEFAULT '', description TEXT DEFAULT '',
            mangaupdates_url TEXT DEFAULT '', is_bundle INTEGER DEFAULT 0, volume_start REAL,
            volume_end REAL, volumes_json TEXT DEFAULT '[]', missing_vols_json TEXT DEFAULT '[]',
            download_url TEXT, link TEXT, cover_url TEXT, source_feed_name TEXT,
            status TEXT DEFAULT 'REQUESTED', download_status TEXT DEFAULT 'IDLE', torrent_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    for key, value in (settings or {}).items():
        cur.execute("INSERT INTO app_settings (key, value) VALUES (?, ?);", (key, value))
    for guid, title, status in rows:
        cur.execute(
            "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, ?);",
            (guid, title, title, status),
        )
    conn.commit()
    conn.close()


def _rows(db):
    """All stored rows by guid -> {status, media_type, title} (bypasses view guards)."""
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT guid, title, status, media_type FROM releases;")
        return {row["guid"]: dict(row) for row in cur.fetchall()}


def _titles(items):
    return [item["title"] for item in items]


class _FreshDatabase:
    """Real MangaDatabase over a throwaway file, with controlled stored rows."""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_media_typing_")
        self.path = os.path.join(self.tmpdir, "mangatrax.db")
        self.db = MangaDatabase(self.path)

    def insert(self, guid, title, status="DISCOVERY", media_type=None, series_name=None,
               volume_start=None, release_group=None):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO releases (guid, title, series_name, status, media_type, volume_start, release_group) "
                "VALUES (?, ?, ?, ?, ?, ?, ?);",
                (guid, title, series_name if series_name is not None else title, status,
                 media_type, volume_start, release_group),
            )
            conn.commit()

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# 1. The proof: a REQUESTED row matching the old heuristic survives re-init
# ===========================================================================
def test_requested_row_survives_reinitialisation():
    print("--> Testing D1: REQUESTED row matching the old purge heuristic survives init...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_d1_survive_")
    path = os.path.join(tmpdir, "legacy.db")
    try:
        # A pre-media_type database holding exactly the rows the old code deleted:
        # a light-novel-looking REQUESTED wishlist row and a chapter-looking row.
        _create_legacy_db(path, [
            ("ln-requested", LN_TITLE, "REQUESTED"),
            ("chapter-requested", CHAPTER_TITLE, "REQUESTED"),
            ("manga-requested", MANGA_TITLE, "REQUESTED"),
        ])
        db = MangaDatabase(path)

        rows = _rows(db)
        assert set(rows) == {"ln-requested", "chapter-requested", "manga-requested"}, rows
        # Status is never touched by typing (resolve_status() precedence model).
        assert rows["ln-requested"]["status"] == "REQUESTED", rows["ln-requested"]
        assert rows["chapter-requested"]["status"] == "REQUESTED", rows["chapter-requested"]
        # Legacy rows are re-typed (hidden from views), not deleted.
        assert rows["ln-requested"]["media_type"] == MEDIA_TYPE_LIGHT_NOVEL, rows["ln-requested"]
        assert rows["chapter-requested"]["media_type"] == MEDIA_TYPE_CHAPTER_ONLY, rows["chapter-requested"]
        # Inconclusive rows stay visible.
        assert rows["manga-requested"]["media_type"] == MEDIA_TYPE_UNKNOWN, rows["manga-requested"]

        # They are hidden from the tracked view but still retrievable by id (auditable).
        assert _titles(db.get_releases()) == [MANGA_TITLE], _titles(db.get_releases())
        assert _titles(db.get_pending_requests()) == [MANGA_TITLE], _titles(db.get_pending_requests())
        assert db.get_release_by_id(1) is not None
        print("    [PASSED] startup re-types instead of deleting; status and rows preserved.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 2. Structurally: nothing on the automatic startup path deletes releases
# ===========================================================================
def test_startup_path_contains_no_release_delete():
    print("--> Testing D1: no DELETE FROM releases on the automatic startup path...")
    # Structural check on the SQL actually executed (comments/docstrings may of
    # course mention the removed purge without executing anything).
    delete_sql = re.compile(r"execute(?:many)?\(\s*(?:\"\"\"|'''|\"|')\s*DELETE\b", re.IGNORECASE)
    init_source = inspect.getsource(MangaDatabase.init_db)
    retype_source = inspect.getsource(MangaDatabase._run_media_filter_once)
    assert not delete_sql.search(init_source), "init_db still executes a DELETE statement"
    assert not delete_sql.search(retype_source), "the re-typing pass must never execute a DELETE"
    # The only media-type delete is the explicit opt-in purge.
    purge_source = inspect.getsource(MangaDatabase.purge_non_manga_releases)
    assert delete_sql.search(purge_source), "explicit purge must still be able to delete"

    # Runtime proof: init_db() never calls the purge entry point.
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_d1_nopurge_")
    path = os.path.join(tmpdir, "db.sqlite")
    calls = []
    try:
        _create_legacy_db(path, [("ln-1", LN_TITLE, "REQUESTED")])
        with patch.object(
            MangaDatabase,
            "purge_non_manga_releases",
            side_effect=lambda *args, **kwargs: calls.append(1) or 0,
        ):
            db = MangaDatabase(path)
            db.init_db()
        assert calls == [], "init_db()/startup called the destructive purge"
        assert "ln-1" in _rows(db), "row disappeared during startup"
        print("    [PASSED] startup never calls the purge; only re-typing runs.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 3. Idempotency, already-typed rows, and legacy marker semantics
# ===========================================================================
def test_retyping_pass_is_idempotent_and_spares_typed_rows():
    print("--> Testing D1: re-typing is idempotent and never clobbers typed rows...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_d1_idem_")
    path = os.path.join(tmpdir, "legacy.db")
    try:
        _create_legacy_db(path, [
            ("ln-1", LN_TITLE, "REQUESTED"),
            ("manga-typed", MANGA_TITLE, "REQUESTED"),
            ("audio-typed", "Skeleton Knight in Another World v01-09 [Audiobook] [Seven Seas Siren]", "REQUESTED"),
            ("novel-typed", "Some Novel Vol. 1", "NEW"),
        ])
        # A row already typed by a previous pass / by a human decision.
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE releases ADD COLUMN media_type TEXT DEFAULT 'unknown';")
        conn.execute("UPDATE releases SET media_type = 'manga' WHERE guid = 'manga-typed';")
        conn.execute("UPDATE releases SET media_type = 'audiobook' WHERE guid = 'audio-typed';")
        conn.execute("UPDATE releases SET media_type = 'novel' WHERE guid = 'novel-typed';")
        conn.commit()
        conn.close()

        db = MangaDatabase(path)
        first = _rows(db)
        assert first["manga-typed"]["media_type"] == MEDIA_TYPE_MANGA, first["manga-typed"]
        assert first["audio-typed"]["media_type"] == MEDIA_TYPE_AUDIOBOOK, first["audio-typed"]
        assert first["novel-typed"]["media_type"] == "novel", first["novel-typed"]
        assert first["ln-1"]["media_type"] == MEDIA_TYPE_LIGHT_NOVEL, first["ln-1"]
        assert db.get_setting("media_filter_applied") == "1"

        # Second init: nothing changes, nothing is deleted, marker stays applied.
        db.init_db()
        second = _rows(db)
        assert second == first, (second, first)
        assert db.get_setting("media_filter_applied") == "1"
        print("    [PASSED] pass is idempotent and only touches unknown rows.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_legacy_true_marker_does_not_rerun():
    print("--> Testing D1: a legacy 'true' marker counts as already applied...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_d1_marker_")
    path = os.path.join(tmpdir, "legacy.db")
    try:
        # Older deployments may carry a non-'1' truthy marker; it must not re-run.
        _create_legacy_db(
            path,
            [("ln-1", LN_TITLE, "REQUESTED")],
            settings={"media_filter_applied": "true"},
        )
        db = MangaDatabase(path)
        rows = _rows(db)
        assert rows["ln-1"]["media_type"] == MEDIA_TYPE_UNKNOWN, rows["ln-1"]
        assert db.get_setting("media_filter_applied") == "true"
        # Marker '0' means "not applied": the pass runs on the next init.
        db.set_setting("media_filter_applied", "0")
        db.init_db()
        assert _rows(db)["ln-1"]["media_type"] == MEDIA_TYPE_LIGHT_NOVEL
        assert db.get_setting("media_filter_applied") == "1"
        print("    [PASSED] any marker other than ''/'0' means already applied.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_filter_non_manga_setting_disables_typing():
    print("--> Testing D1: filter_non_manga=false disables the pass...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_d1_filter_")
    path = os.path.join(tmpdir, "legacy.db")
    try:
        _create_legacy_db(
            path,
            [("ln-1", LN_TITLE, "REQUESTED")],
            settings={"filter_non_manga": "false"},
        )
        db = MangaDatabase(path)
        # Disabled: the legacy media-typing pass does nothing and its marker stays unset,
        # but task-70's persisted light-novel classification still hides known LN rows.
        assert _rows(db)["ln-1"]["media_type"] == MEDIA_TYPE_UNKNOWN
        assert db.get_setting("media_filter_applied") in ("", "0")
        assert LN_TITLE not in _titles(db.get_pending_requests())

        db.set_setting("filter_non_manga", "true")
        db.init_db()
        assert _rows(db)["ln-1"]["media_type"] == MEDIA_TYPE_LIGHT_NOVEL
        assert db.get_setting("media_filter_applied") == "1"
        print("    [PASSED] the re-typing pass honours filter_non_manga.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 4. Query guards: tracked views hide non-manga, keep unknown visible
# ===========================================================================
def test_query_guards_hide_non_manga_keep_unknown():
    print("--> Testing D1 query guards across every tracked view...")
    ctx = _FreshDatabase()
    try:
        db = ctx.db
        db.add_prioritized_group("stick-group")
        # Three rows per view, differing only by media_type.
        ctx.insert("vis-manga", "Manga Alpha Vol. 1", status="REQUESTED",
                   media_type=MEDIA_TYPE_MANGA, series_name="Manga Alpha", volume_start=1.0,
                   release_group="stick-group")
        ctx.insert("vis-unknown", "Unknown Beta Vol. 1", status="REQUESTED",
                   media_type=MEDIA_TYPE_UNKNOWN, series_name="Unknown Beta", volume_start=1.0,
                   release_group="stick-group")
        ctx.insert("hid-ln", "Hidden Novel Vol. 1", status="REQUESTED",
                   media_type=MEDIA_TYPE_LIGHT_NOVEL, series_name="Hidden Novel", volume_start=1.0,
                   release_group="stick-group")
        ctx.insert("hid-chapter", "Hidden Chapter 5", status="DISCOVERY",
                   media_type=MEDIA_TYPE_CHAPTER_ONLY, series_name="Hidden Chapter")
        ctx.insert("hid-audio", "Hidden Audio Vol. 1", status="IN_CALIBRE",
                   media_type=MEDIA_TYPE_AUDIOBOOK, series_name="Hidden Audio", volume_start=1.0,
                   release_group="stick-group")
        db._invalidate_grouped_cache()

        flat = {row["guid"] for row in db.get_releases(limit=100)}
        assert flat == {"vis-manga", "vis-unknown"}, flat

        pending = {row["guid"] for row in db.get_pending_requests()}
        assert pending == {"vis-manga", "vis-unknown"}, pending

        grouped = db.get_releases_grouped_by_series()
        grouped_names = {group["series_name"] for group in grouped}
        assert grouped_names == {"Manga Alpha", "Unknown Beta"}, grouped_names

        discovery = {row["guid"] for row in db.get_discovery_releases(limit=100)}
        assert "hid-chapter" not in discovery, discovery
        assert db.count_discovery_releases() == len(discovery), (db.count_discovery_releases(), discovery)

        prioritized = {row["guid"] for row in db.get_prioritized_releases(limit=100)}
        assert prioritized == {"vis-manga", "vis-unknown"}, prioritized

        searched = {row["guid"] for row in db.search_releases("Hidden")}
        assert searched == set(), searched
        assert {row["guid"] for row in db.search_releases("Unknown Beta")} == {"vis-unknown"}

        stats = db.get_dashboard_stats()
        assert stats["pending_requests"] == 2, stats
        assert stats["acquired_books"] == 0, stats
        assert stats["discovered_count"] == len(discovery), stats

        # The single filter predicate is shared, not copy-pasted per query.
        clause = tracked_media_clause()
        for media in NON_MANGA_MEDIA_TYPES:
            assert f"'{media}'" in clause, clause
        assert "'manga'" not in clause.lower(), clause
        assert "COALESCE(media_type, 'unknown')" in clause, clause
        assert tracked_media_clause("r") == clause.replace("media_type", "r.media_type", 1), tracked_media_clause("r")
        print("    [PASSED] every tracked view hides non-manga and keeps unknown visible.")
    finally:
        ctx.close()


# ===========================================================================
# 5. The purge is explicit and only removes typed non-manga rows
# ===========================================================================
def test_opt_in_purge_only_removes_typed_non_manga():
    print("--> Testing D1: opt-in purge is explicit, scoped and never automatic...")
    ctx = _FreshDatabase()
    try:
        db = ctx.db
        ctx.insert("keep-manga", "Keep Manga Vol. 1", media_type=MEDIA_TYPE_MANGA)
        ctx.insert("keep-unknown", "Keep Unknown Vol. 1", media_type=MEDIA_TYPE_UNKNOWN)
        ctx.insert("keep-null", "Keep Null Vol. 1", media_type=None)
        ctx.insert("drop-ln", "Drop LN Vol. 1", media_type=MEDIA_TYPE_LIGHT_NOVEL)
        ctx.insert("drop-chapter", "Drop Chapter 1", media_type=MEDIA_TYPE_CHAPTER_ONLY)

        # Automatic paths (every init) leave everything alone...
        db.init_db()
        assert set(_rows(db)) == {
            "keep-manga", "keep-unknown", "keep-null", "drop-ln", "drop-chapter"
        }, set(_rows(db))

        # ...and only the explicit maintenance call deletes, scoped to typed non-manga.
        assert db.purge_non_manga_releases() == 2
        assert set(_rows(db)) == {"keep-manga", "keep-unknown", "keep-null"}, set(_rows(db))
        # Second run is a no-op.
        assert db.purge_non_manga_releases() == 0
        print("    [PASSED] purge is opt-in, scoped to typed non-manga rows, and idempotent.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Schema migration + ingestion defaults
# ===========================================================================
def test_media_type_column_migration_and_defaults():
    print("--> Testing D1: guarded media_type migration and ingestion default...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_d1_schema_")
    path = os.path.join(tmpdir, "legacy.db")
    try:
        _create_legacy_db(path, [("row-1", MANGA_TITLE, "REQUESTED")])
        db = MangaDatabase(path)
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(releases);")
            cols = {row["name"]: row for row in cur.fetchall()}
        assert "media_type" in cols, sorted(cols)
        assert cols["media_type"]["dflt_value"] == "'unknown'", cols["media_type"]
        # Repeatable migration (no duplicate-column error).
        db.init_db()

        # New rows stored after the pass default to 'unknown' and stay visible.
        db.upsert_release({
            "guid": "new-release",
            "title": "Brand New Manga Vol. 1",
            "series_name": "Brand New Manga",
            "status": "REQUESTED",
            "volumes": [1.0],
        })
        new_row = [row for row in _rows(db).values() if row["guid"] == "new-release"]
        assert new_row and new_row[0]["media_type"] == MEDIA_TYPE_UNKNOWN, new_row
        assert "new-release" in {row["guid"] for row in db.get_pending_requests()}

        # Re-upserting an already-typed row must not reset its media type.
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE releases SET media_type = 'manga' WHERE guid = 'new-release';")
            conn.commit()
        db.upsert_release({
            "guid": "new-release",
            "title": "Brand New Manga Vol. 1 (re-poll)",
            "series_name": "Brand New Manga",
            "status": "DISCOVERY",
        })
        re_row = [row for row in _rows(db).values() if row["guid"] == "new-release"][0]
        assert re_row["media_type"] == MEDIA_TYPE_MANGA, re_row
        print("    [PASSED] media_type migration is guarded; upserts never reset a type.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 78)
    print("Running D1 suite: non-destructive media typing (startup purge removed)")
    print("=" * 78)
    tests = [
        test_requested_row_survives_reinitialisation,
        test_startup_path_contains_no_release_delete,
        test_retyping_pass_is_idempotent_and_spares_typed_rows,
        test_legacy_true_marker_does_not_rerun,
        test_filter_non_manga_setting_disables_typing,
        test_query_guards_hide_non_manga_keep_unknown,
        test_opt_in_purge_only_removes_typed_non_manga,
        test_media_type_column_migration_and_defaults,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception:
            failures += 1
            print(f"  [FAILED] {test.__name__}")
            traceback.print_exc()
    print("=" * 78)
    if failures:
        print(f"D1 MEDIA-TYPING SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL D1 MEDIA-TYPING TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
