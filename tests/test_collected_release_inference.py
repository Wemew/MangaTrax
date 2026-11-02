#!/usr/bin/env python3
"""task-10 suite: infer a collected batch's volume set from metadata (no torrent).

A "collected release" is a year-range batch with NO volume markers and NO chapter
markers, e.g. ``Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)``.  The
volume count is not in the title, so it is resolved from AUTHORITATIVE metadata
only (AniList, then the MangaUpdates fallback, cache-aware) -- never by fetching a
torrent or any payload -- and only when the series is FINISHED/PUBLISHED and the
count is known.  Otherwise the release stays volume-less and visible (retried on
the next poll); explicit volume/complete markers always win.

Run through the assert-aware runner (assertions compiled live, optimize=0):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import sqlite3
import sys
import tempfile
import shutil
import traceback
from unittest.mock import MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
for _path in (BACKEND_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.covers import CoverCache
from app.database import MangaDatabase
from app.parser import is_collected_release_candidate
from app.providers.base import ReleaseItem
from app.scheduler import MangaScheduler

BLUE_LOCK_BATCH = "Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)"


def _write_calibre_library(db_path, series_map):
    """Minimal scanner-complete Calibre metadata.db: {series_name: [volumes]}."""
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, sort TEXT,
            author_sort TEXT, series_index REAL DEFAULT 1.0, path TEXT DEFAULT '', has_cover INTEGER DEFAULT 0);
        CREATE TABLE series (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, series INTEGER NOT NULL);
        CREATE TABLE authors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT, link TEXT DEFAULT '');
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, author INTEGER NOT NULL);
        CREATE TABLE publishers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT);
        CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, publisher INTEGER NOT NULL);
        CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, tag INTEGER NOT NULL);
    """)
    for series_id, (name, volumes) in enumerate(series_map.items(), start=1):
        cur.execute("INSERT INTO series (id, name) VALUES (?, ?);", (series_id, name))
        cur.execute("INSERT OR IGNORE INTO authors (id, name) VALUES (?, ?);", (series_id, "Author"))
        for volume in volumes:
            cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"{name} Vol. {int(volume)}", float(volume)))
            book_id = cur.lastrowid
            cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?);", (book_id, series_id))
            cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, ?);", (book_id, series_id))
    conn.commit()
    conn.close()


def _anilist_meta(canonical, romaji, total_volumes, status, source="anilist"):
    return {
        "canonical_title": canonical,
        "english_title": canonical,
        "romaji_title": romaji,
        "cover_url": "",
        "total_volumes": total_volumes,
        "status": status,
        "synopsis": "",
        "source": source,
    }


class _Context:
    """Real DB + scheduler + mock Calibre library; metadata services stubbed."""

    def __init__(self, calibre_series=None, settings=None):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_infer_")
        self.calibre_dir = os.path.join(self.tmpdir, "calibre")
        os.makedirs(self.calibre_dir, exist_ok=True)
        _write_calibre_library(os.path.join(self.calibre_dir, "metadata.db"), calibre_series or {})
        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.db.set_setting("calibre_path", self.calibre_dir)
        self.db.set_setting("qbit_enabled", "false")
        self.db.set_setting("auto_download", "false")
        for key, value in (settings or {}).items():
            self.db.set_setting(key, value)
        self.scheduler = MangaScheduler(self.db, CoverCache(os.path.join(self.tmpdir, "covers")))
        self.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: _anilist_meta(str(raw), str(raw), None, "RELEASING")
        )
        self.scheduler.mangaupdates.get_series_info = MagicMock(return_value=None)
        self.scheduler.cover_cache.cache_sync = MagicMock()

    def process(self, title, guid=None):
        parsed = self.scheduler.anilist  # noqa: F841  (keep reference explicit)
        from app.parser import extract_manga_bundle_and_volumes

        parsed = extract_manga_bundle_and_volumes(title)
        release = ReleaseItem(
            guid=guid or f"infer-{abs(hash(title))}",
            title=title,
            link="https://nyaa.si/view/1",
            series_name=parsed["series_name"],
            volumes=parsed["volumes"],
            is_bundle=parsed["is_bundle"],
            is_complete=parsed["is_complete"],
            volume_start=parsed["volume_start"],
            volume_end=parsed["volume_end"],
        )
        _, matcher = self.scheduler._build_calibre_context()
        result = self.scheduler._process_release(release, "Nyaa", matcher)
        return release, result

    def row(self, guid):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM releases WHERE guid = ?;", (guid,))
            found = cur.fetchone()
            if not found:
                return None
            row = dict(found)
            import json

            row["volumes"] = json.loads(row.get("volumes_json") or "[]")
            row["missing_volumes"] = json.loads(row.get("missing_vols_json") or "[]")
            return row

    def activities(self, event_type):
        return [r for r in self.db.get_logs(limit=200) if r["event_type"] == event_type]

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# 1. Detection: collected batch candidate vs explicit markers
# ===========================================================================
def test_collected_candidate_detection():
    print("--> Testing collected-release candidate detection...")
    assert is_collected_release_candidate(BLUE_LOCK_BATCH) is True
    assert is_collected_release_candidate("Blue Lock - Episode Nagi (2024-2026)") is True
    # Explicit volume/chapter markers always win: never a collected candidate.
    assert is_collected_release_candidate("Blue Lock - Episode Nagi Vol 1-3 (2024-2026)") is False
    assert is_collected_release_candidate("Blue Lock - Episode Nagi v01-08 (2024-2026)") is False
    assert is_collected_release_candidate("Blue Lock - Episode Nagi Ch. 5 (2024-2026)") is False
    # No year range -> not a collected-batch signal.
    assert is_collected_release_candidate("Blue Lock - Episode Nagi (Digital) (Stick)") is False
    # Plain single-year marker is not a batch.
    assert is_collected_release_candidate("Blue Lock - Episode Nagi (2024) (Digital)") is False
    print("    [PASSED] year-range + no volume/chapter markers identifies a collected batch.")


# ===========================================================================
# 2. Infer the full set from FINISHED authoritative metadata
# ===========================================================================
def test_infers_full_set_from_finished_metadata():
    print("--> Testing inference of the full volume set from FINISHED metadata...")
    ctx = _Context({})
    try:
        ctx.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: (
                _anilist_meta("BLUE LOCK -EPISODE NAGI-", "Blue Lock: EPISODE Nagi", 8, "FINISHED")
                if "episode nagi" in str(raw).lower()
                else _anilist_meta("Blue Lock", "Blue Lock", None, "RELEASING")
            )
        )
        release, result = ctx.process(BLUE_LOCK_BATCH, guid="infer-blue-lock")
        assert release.volumes == [float(v) for v in range(1, 9)], release.volumes
        assert release.is_bundle is True
        assert getattr(release, "inferred_volumes", False) is True
        row = ctx.row("infer-blue-lock")
        assert row["volumes"] == [float(v) for v in range(1, 9)], row["volumes"]
        assert row["inferred_volumes"] == 1, row["inferred_volumes"]
        assert row["is_bundle"] == 1, row["is_bundle"]
        assert row["missing_volumes"] == [float(v) for v in range(1, 9)], row["missing_volumes"]
        assert result[1] == "DISCOVERY", result  # not in Calibre -> discovery candidate
        # Flat view clearly marks it as an inferred full set.
        flat = {r["guid"]: r for r in ctx.db.get_releases()}
        assert flat["infer-blue-lock"]["is_inferred"] is True, flat["infer-blue-lock"]
        # Auditable scheduler event naming series/count/source/status.
        events = ctx.activities("COLLECTED_RELEASE_INFERRED")
        assert len(events) == 1, events
        event = events[0]
        assert event["actor"] == "scheduler", event
        assert "Blue Lock - Episode Nagi" in event["message"], event
        assert "8 volumes" in event["message"], event
        assert "anilist" in event["details"] and "FINISHED" in event["details"], event
        assert BLUE_LOCK_BATCH in event["details"], event
        print("    [PASSED] 8 volumes inferred, persisted, marked and audit-logged.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Reconcile against Calibre: request only the missing volumes
# ===========================================================================
def test_reconcile_against_calibre_requests_only_missing():
    print("--> Testing reconciliation: Calibre owns 1-5 -> missing 6-8...")
    ctx = _Context({"Blue Lock - Episode Nagi": [1.0, 2.0, 3.0, 4.0, 5.0]})
    try:
        ctx.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: (
                _anilist_meta("BLUE LOCK -EPISODE NAGI-", "Blue Lock: EPISODE Nagi", 8, "FINISHED")
                if "episode nagi" in str(raw).lower()
                else _anilist_meta("Blue Lock", "Blue Lock", None, "RELEASING")
            )
        )
        release, result = ctx.process(BLUE_LOCK_BATCH, guid="infer-blue-lock-owned")
        assert release.volumes == [float(v) for v in range(1, 9)], release.volumes
        status = result[1]
        missing = result[2]
        assert status == "REQUESTED", (status, result)
        assert missing == [6.0, 7.0, 8.0], missing
        row = ctx.row("infer-blue-lock-owned")
        assert row["missing_volumes"] == [6.0, 7.0, 8.0], row["missing_volumes"]
        assert row["status"] == "REQUESTED", row["status"]
        print("    [PASSED] owned 1-5; only 6-8 are missing/requested.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Gate: RELEASING / unknown count -> no expansion, retained and visible
# ===========================================================================
def test_releasing_or_unknown_count_does_not_expand():
    print("--> Testing gate: RELEASING / unknown count -> retained volume-less...")
    for meta in [
        _anilist_meta("Blue Lock", "Blue Lock", None, "RELEASING"),
        _anilist_meta("Blue Lock", "Blue Lock", 30, "RELEASING"),
        _anilist_meta("Blue Lock", "Blue Lock", None, "FINISHED"),
    ]:
        ctx = _Context({})
        try:
            ctx.scheduler.anilist.resolve_manga_series = MagicMock(return_value=meta)
            release, result = ctx.process(BLUE_LOCK_BATCH, guid="infer-gated")
            assert release.volumes == [], (meta, release.volumes)
            assert getattr(release, "inferred_volumes", False) is False
            row = ctx.row("infer-gated")
            assert row is not None, "release must be retained, not discarded"
            assert row["volumes"] == [], row
            assert row["status"] == "DISCOVERY", row["status"]
            # Unknown media type stays visible in the tracked view.
            assert row["media_type"] == "unknown", row["media_type"]
            assert "infer-gated" in {r["guid"] for r in ctx.db.get_releases()}
            assert ctx.activities("COLLECTED_RELEASE_INFERRED") == []
        finally:
            ctx.close()
    print("    [PASSED] inconclusive metadata never guesses and never discards.")


# ===========================================================================
# 5. Franchise-parent trap
# ===========================================================================
def test_franchise_parent_is_not_used():
    print("--> Testing franchise-parent trap (Blue Lock vs Episode Nagi)...")
    spin_off = _anilist_meta("BLUE LOCK -EPISODE NAGI-", "Blue Lock: EPISODE Nagi", 8, "FINISHED")
    parent_with_count = _anilist_meta("Blue Lock", "Blue Lock", 30, "FINISHED")
    ctx = _Context({})
    try:
        # Helper identity guard: parent metadata must not satisfy the spin-off key...
        assert ctx.scheduler._metadata_name_matches("Blue Lock", "Blue Lock - Episode Nagi") is False
        assert ctx.scheduler._metadata_name_matches("BLUE LOCK -EPISODE NAGI-", "Blue Lock - Episode Nagi") is True
        count, info = ctx.scheduler._infer_collected_release_volumes(
            ReleaseItem(title=BLUE_LOCK_BATCH, link="https://nyaa.si/view/1"),
            "Blue Lock - Episode Nagi",
            parent_with_count,
            [],
        )
        assert count is None, (count, info)
        # ...while the real spin-off metadata resolves to its own 8 volumes.
        count, info = ctx.scheduler._infer_collected_release_volumes(
            ReleaseItem(title=BLUE_LOCK_BATCH, link="https://nyaa.si/view/1"),
            "Blue Lock - Episode Nagi",
            spin_off,
            [],
        )
        assert count == 8, (count, info)
        # Worst case end-to-end: the parent is FINISHED with 30 volumes, but the
        # spin-off's own row wins.
        ctx.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: (
                spin_off if "episode nagi" in str(raw).lower() else parent_with_count
            )
        )
        release, _ = ctx.process(BLUE_LOCK_BATCH, guid="infer-spin-off")
        assert release.volumes == [float(v) for v in range(1, 9)], release.volumes
        # A release of the parent itself resolves to the parent's 30 volumes.
        parent_title = "Blue Lock (2020-2024) (Digital) (Stick)"
        parent_release, _ = ctx.process(parent_title, guid="infer-parent")
        assert parent_release.volumes == [float(v) for v in range(1, 31)], parent_release.volumes
        print("    [PASSED] spin-off uses its own count; parent data cannot leak in.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Explicit volume markers always win
# ===========================================================================
def test_explicit_volume_markers_win():
    print("--> Testing explicit volume markers always win...")
    ctx = _Context({})
    try:
        ctx.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: _anilist_meta("BLUE LOCK -EPISODE NAGI-", "Blue Lock: EPISODE Nagi", 8, "FINISHED")
        )
        title = "Blue Lock - Episode Nagi Vol 1-3 (2024-2026) (Digital) (Stick)"
        assert is_collected_release_candidate(title) is False
        release, _ = ctx.process(title, guid="explicit-volumes")
        assert release.volumes == [1.0, 2.0, 3.0], release.volumes
        assert getattr(release, "inferred_volumes", False) is False
        row = ctx.row("explicit-volumes")
        assert row["volumes"] == [1.0, 2.0, 3.0], row["volumes"]
        assert row["inferred_volumes"] == 0, row["inferred_volumes"]
        assert ctx.activities("COLLECTED_RELEASE_INFERRED") == []
        print("    [PASSED] explicit Vol 1-3 template untouched by inference.")
    finally:
        ctx.close()


def test_complete_marker_never_overwrites_explicit_volumes():
    print("--> Testing is_complete alone never overwrites a parsed volume set...")
    ctx = _Context({})
    try:
        ctx.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: _anilist_meta("BLUE LOCK -EPISODE NAGI-", "Blue Lock: EPISODE Nagi", 8, "FINISHED")
        )
        title = "Blue Lock - Episode Nagi Vol 1-3 [Completed]"
        release, _ = ctx.process(title, guid="complete-explicit")
        assert release.is_complete is True, "title is a complete-series release"
        assert release.volumes == [1.0, 2.0, 3.0], release.volumes
        row = ctx.row("complete-explicit")
        assert row["is_complete"] == 1, row["is_complete"]
        assert row["volumes"] == [1.0, 2.0, 3.0], row["volumes"]
        assert row["inferred_volumes"] == 0, row["inferred_volumes"]
        print("    [PASSED] is_complete is metadata only; the explicit set is authoritative.")
    finally:
        ctx.close()


# ===========================================================================
# 7. Metadata unreachable: retain + log + retry (no crash, no discard)
# ===========================================================================
def test_metadata_unreachable_is_graceful_and_retried():
    print("--> Testing metadata-unreachable path: retain, log, retry...")
    ctx = _Context({})
    try:
        ctx.scheduler.anilist.resolve_manga_series = MagicMock(side_effect=RuntimeError("metadata offline"))
        ctx.scheduler.mangaupdates.get_series_info = MagicMock(side_effect=RuntimeError("MU offline"))
        release, _ = ctx.process(BLUE_LOCK_BATCH, guid="infer-retry")
        assert release.volumes == [], release.volumes
        row = ctx.row("infer-retry")
        assert row is not None, "release must be retained when metadata is unreachable"
        assert row["volumes"] == [], row
        assert row["status"] == "DISCOVERY", row["status"]
        assert "infer-retry" in {r["guid"] for r in ctx.db.get_releases()}, "kept visible"
        assert ctx.activities("COLLECTED_RELEASE_INFERRED") == []

        # Retry later (next poll) with working metadata: the SAME release expands.
        ctx.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: _anilist_meta("BLUE LOCK -EPISODE NAGI-", "Blue Lock: EPISODE Nagi", 8, "FINISHED")
        )
        ctx.scheduler.mangaupdates.get_series_info = MagicMock(return_value=None)
        ctx.process(BLUE_LOCK_BATCH, guid="infer-retry")
        row = ctx.row("infer-retry")
        assert row["volumes"] == [float(v) for v in range(1, 9)], row["volumes"]
        assert row["inferred_volumes"] == 1, row["inferred_volumes"]
        assert len(ctx.activities("COLLECTED_RELEASE_INFERRED")) == 1
        print("    [PASSED] unreachable metadata keeps the release; the next poll retries.")
    finally:
        ctx.close()


# ===========================================================================
# 8. Setting off restores previous behaviour
# ===========================================================================
def test_setting_off_disables_inference():
    print("--> Testing infer_collected_releases=false disables inference...")
    ctx = _Context({}, settings={"infer_collected_releases": "false"})
    try:
        ctx.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: _anilist_meta("BLUE LOCK -EPISODE NAGI-", "Blue Lock: EPISODE Nagi", 8, "FINISHED")
        )
        release, _ = ctx.process(BLUE_LOCK_BATCH, guid="infer-disabled")
        assert release.volumes == [], release.volumes
        assert getattr(release, "inferred_volumes", False) is False
        row = ctx.row("infer-disabled")
        assert row["volumes"] == [], row
        assert ctx.activities("COLLECTED_RELEASE_INFERRED") == []
        # The raw metadata lookups were not even needed for the inference (the
        # setting gate short-circuits before resolution).
        print("    [PASSED] setting off keeps the volume-less release as before.")
    finally:
        ctx.close()


# ===========================================================================
# 9. Settings whitelist + defaults
# ===========================================================================
def test_settings_whitelist_and_defaults():
    print("--> Testing /api/settings whitelist + DB defaults...")
    import inspect

    from app import schemas

    # The embedded interpreter may not ship pydantic (schemas falls back to a shim),
    # so assert the whitelist contract structurally: both settings are fields of the
    # strict (extra="forbid") SettingsUpdateRequest model.
    source = inspect.getsource(schemas.SettingsUpdateRequest)
    assert "filter_non_manga: Optional[str]" in source, source
    assert "infer_collected_releases: Optional[str]" in source, source
    assert 'extra="forbid"' in source, source
    try:  # when pydantic is available, the model also validates/forbids correctly.
        model = schemas.SettingsUpdateRequest(filter_non_manga="false", infer_collected_releases="true")
        provided = model.model_dump(exclude_unset=True, exclude_none=True)
        assert provided.get("filter_non_manga") == "false", provided
        assert provided.get("infer_collected_releases") == "true", provided
    except TypeError:
        pass  # pydantic-less shim: structural assertion above is the contract.

    ctx = _Context({})
    try:
        settings = ctx.db.get_all_settings()
        assert settings.get("filter_non_manga") == "true", settings.get("filter_non_manga")
        assert settings.get("infer_collected_releases") == "true", settings.get("infer_collected_releases")
    finally:
        ctx.close()
    print("    [PASSED] both settings are configurable via /api/settings and default to on.")


def main():
    print("=" * 78)
    print("Running collected-release volume inference suite (task-10)")
    print("=" * 78)
    tests = [
        test_collected_candidate_detection,
        test_infers_full_set_from_finished_metadata,
        test_reconcile_against_calibre_requests_only_missing,
        test_releasing_or_unknown_count_does_not_expand,
        test_franchise_parent_is_not_used,
        test_explicit_volume_markers_win,
        test_complete_marker_never_overwrites_explicit_volumes,
        test_metadata_unreachable_is_graceful_and_retried,
        test_setting_off_disables_inference,
        test_settings_whitelist_and_defaults,
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
        print(f"COLLECTED-RELEASE INFERENCE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL COLLECTED-RELEASE INFERENCE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
