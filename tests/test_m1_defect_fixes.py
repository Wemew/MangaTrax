"""
tests/test_m1_defect_fixes.py
Comprehensive regression test suite for Milestone 1 defect remediations:
- Feature 6: Release Download by ID & Selective Volume Retention (main.py:564, database.py:712)
- Feature 7: Release Status Upsert Bug & IGNORED Preservation (database.py:549)
- Feature 8: Auto-Download Transition Detection & Deduplication (scheduler.py:258-285)
- Feature 9: Grouped Cache Invalidation (database.py:895, 984, 994, 1087)
- Feature 10: Unbounded Parser Loop Guard & Algorithmic DoS Defense (parser.py:64, 395)
- Feature 11: Calibre Sync Startup Deduplication & Non-Blocking Concurrency (scheduler.py, main.py)
- Feature 12: Polite Crawl Delay Respecting Start Page Offset (nyaa.py:252)
- Feature 13: HTML Scraper Robustness & Urllib Fallback (html_scraper.py:5-8, 47-60)
"""

import os
import sys
import json
import sqlite3
import time
import shutil
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

# Ensure backend modules are on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.database import MangaDatabase
from app.covers import CoverCache
from app.scheduler import MangaScheduler
from app.parser import extract_manga_bundle_and_volumes, MAX_BUNDLE_SPAN
from app.providers.base import ReleaseItem
from app.providers.nyaa import NyaaMangaProvider
import app.providers.html_scraper as hs_module
from app.providers.html_scraper import HtmlScraperProvider
from tests.mock_calibre_manga import create_mock_calibre_manga_db


# calibre-debug.exe -e runs Python with optimization level 2 (__debug__ == False),
# which silently strips every bare `assert` statement.  This suite therefore uses an
# explicit helper so its verification cannot be compiled away.
def _check(cond, msg=None):
    if not cond:
        raise AssertionError(msg if msg is not None else "check failed")


def _write_calibre_library(db_path, series_map):
    """Writes a minimal, scanner-complete Calibre metadata.db from {series_name: [volumes]}."""
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
    for sid, (name, vols) in enumerate(series_map.items(), start=1):
        cur.execute("INSERT INTO series (id, name) VALUES (?, ?);", (sid, name))
        cur.execute("INSERT OR IGNORE INTO authors (id, name) VALUES (?, ?);", (sid, "Author"))
        cur.execute("INSERT OR IGNORE INTO publishers (id, name) VALUES (?, ?);", (sid, "Publisher"))
        for v in vols:
            cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"{name} Vol. {int(v)}", float(v)))
            bid = cur.lastrowid
            cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?);", (bid, sid))
            cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, ?);", (bid, sid))
            cur.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, ?);", (bid, sid))
    conn.commit()
    conn.close()


# ===========================================================================
# Feature 6: Release Download Lookup & Selective Volume Retention
# ===========================================================================
def test_feature_6_release_download_lookup_and_bundle_volumes():
    print("--> Testing Feature 6: Release Download by ID & Selective Volume Retention...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_test_f6_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "test_f6.db"))

        # 1. Test lookup of IGNORED release (filtered out by get_releases(limit=1000))
        _, r_ignored_id = db.upsert_release({
            "guid": "guid-f6-ignored",
            "title": "Chainsaw Man Vol. 10 [Ignored]",
            "series_name": "Chainsaw Man",
            "status": "IGNORED"
        })
        active_releases = db.get_releases(limit=1000)
        _check(not any(r["id"] == r_ignored_id for r in active_releases), "IGNORED release should be excluded from get_releases")
        target_ignored = db.get_release_by_id(r_ignored_id)
        _check(target_ignored is not None, "get_release_by_id failed to locate IGNORED release")
        _check(target_ignored["id"] == r_ignored_id)
        _check(target_ignored["status"] == "IGNORED")

        # 2. Test lookup of BLACKLISTED series release
        db.add_to_blacklist("Berserk", "Dark fantasy")
        _, r_bl_id = db.upsert_release({
            "guid": "guid-f6-bl",
            "title": "Berserk Vol. 01 [Digital]",
            "series_name": "Berserk",
            "status": "BLACKLISTED"
        })
        _check(not any(r["id"] == r_bl_id for r in db.get_releases(limit=1000)), "BLACKLISTED release should be excluded")
        target_bl = db.get_release_by_id(r_bl_id)
        _check(target_bl is not None, "get_release_by_id failed to locate BLACKLISTED release")
        _check(target_bl["id"] == r_bl_id)

        # 3. Test releases beyond limit 1000
        for i in range(1005):
            db.upsert_release({
                "guid": f"guid-bulk-{i}",
                "title": f"Bulk Manga {i} Vol. 01",
                "series_name": f"Bulk Manga {i}",
                "status": "REQUESTED"
            })
        limit_1000 = db.get_releases(limit=1000)
        _check(len(limit_1000) == 1000, "get_releases limit=1000 did not clamp at 1000")
        # The first release inserted (r_ignored_id) is truncated from the first 1000
        first_rel = db.get_release_by_id(r_ignored_id)
        _check(first_rel is not None, "Truncated release could not be found by ID")

        # 4. Test missing_volumes deserialization on bundle release
        _, r_bundle_id = db.upsert_release({
            "guid": "guid-f6-bundle",
            "title": "One Piece Vol. 01-05 [Bundle]",
            "series_name": "One Piece",
            "is_bundle": 1,
            "volumes": [1.0, 2.0, 3.0, 4.0, 5.0],
            "missing_volumes": [4.0, 5.0],
            "status": "REQUESTED"
        })
        bundle_rel = db.get_release_by_id(r_bundle_id)
        _check(bundle_rel is not None)
        _check(bundle_rel["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0])
        _check(bundle_rel["missing_volumes"] == [4.0, 5.0], f"Expected [4.0, 5.0], got {bundle_rel.get('missing_volumes')}")

        print("    [PASSED] Feature 6: Release download lookup & bundle volume retention verified.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# Feature 7: Release Status Upsert Bug & IGNORED Preservation
# ===========================================================================
def test_feature_7_upsert_release_status_transitions_and_ignore_preservation():
    print("--> Testing Feature 7: Release Status Upsert & IGNORED Preservation...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_test_f7_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "test_f7.db"))

        # 1. Initial insert as DISCOVERY
        is_new, r_id = db.upsert_release({
            "guid": "guid-f7-transition",
            "title": "Spy x Family Vol. 01",
            "series_name": "Spy x Family",
            "status": "DISCOVERY"
        })
        _check(is_new is True)
        _check(db.get_release_by_id(r_id)["status"] == "DISCOVERY")

        # 2. Transition to REQUESTED (e.g. series tracked)
        is_new_up1, r_id_up1 = db.upsert_release({
            "guid": "guid-f7-transition",
            "title": "Spy x Family Vol. 01",
            "series_name": "Spy x Family",
            "status": "REQUESTED"
        })
        _check(is_new_up1 is False)
        _check(r_id_up1 == r_id)
        _check(db.get_release_by_id(r_id)["status"] == "REQUESTED", "Status failed to update from DISCOVERY to REQUESTED")

        # 3. Transition to IN_CALIBRE (e.g. acquired in library)
        is_new_up2, _ = db.upsert_release({
            "guid": "guid-f7-transition",
            "title": "Spy x Family Vol. 01",
            "series_name": "Spy x Family",
            "status": "IN_CALIBRE"
        })
        _check(is_new_up2 is False)
        _check(db.get_release_by_id(r_id)["status"] == "IN_CALIBRE", "Status failed to update to IN_CALIBRE")

        # 4. Manual IGNORE preservation test
        db.update_release_status(r_id, "IGNORED")
        _check(db.get_release_by_id(r_id)["status"] == "IGNORED")

        # Automated feed polling re-checks and sends status='REQUESTED'
        db.upsert_release({
            "guid": "guid-f7-transition",
            "title": "Spy x Family Vol. 01",
            "series_name": "Spy x Family",
            "status": "REQUESTED"
        })
        # Release MUST remain IGNORED
        _check(db.get_release_by_id(r_id)["status"] == "IGNORED", "Feed check overwrote manual IGNORED state")

        # 5. Upsert with status=None preserves existing status
        db.upsert_release({
            "guid": "guid-f7-transition",
            "title": "Spy x Family Vol. 01 (Updated Title)",
            "series_name": "Spy x Family"
        })
        _check(db.get_release_by_id(r_id)["status"] == "IGNORED")
        _check(db.get_release_by_id(r_id)["title"] == "Spy x Family Vol. 01 (Updated Title)")

        print("    [PASSED] Feature 7: upsert_release status transitions & IGNORED preservation verified.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# Feature 8: Auto-Download Transition Detection & Deduplication
# ===========================================================================
def test_feature_8_auto_download_transitions_and_deduplication():
    print("--> Testing Feature 8: Auto-Download Transition Detection & Deduplication...")
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_f8_")
    db_file = os.path.join(temp_dir, "test.db")
    calibre_db = os.path.join(temp_dir, "metadata.db")

    try:
        # Scanner-complete but initially EMPTY Calibre library: Dandadan is not in Calibre.
        _write_calibre_library(calibre_db, {})

        db = MangaDatabase(db_file)
        db.set_setting("calibre_path", calibre_db)
        db.set_setting("auto_download", "true")
        cover_cache = CoverCache(temp_dir)
        sched = MangaScheduler(db, cover_cache)

        # Mock metadata services to prevent network calls
        sched.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: {"canonical_title": raw, "romaji_title": raw, "english_title": raw})
        sched.mangaupdates.get_series_info = MagicMock(return_value=None)

        # Mock qBittorrent client
        mock_qbit = MagicMock()
        mock_qbit.add_torrent.return_value = True
        mock_qbit.extract_torrent_hash.return_value = "dandadan_hash_12345"
        mock_qbit.apply_selective_volume_download.return_value = {
            "status": "filtered", "skipped_volumes": [1.0], "kept_volumes": [2.0, 3.0]}
        sched.get_qbit_client = MagicMock(return_value=mock_qbit)

        # Delete the seeded default RSS feed so exactly one deterministic feed is polled
        for seeded in db.get_all_feeds():
            db.delete_feed(seeded["id"])
        db.add_feed(name="Nyaa Manga Feed", feed_type="nyaa",
                    url="https://nyaa.si/?page=rss", interval=60, auto_download=True)

        rel_item = ReleaseItem(
            guid="https://nyaa.si/view/999001",
            title="[Group] Dandadan Vol. 01-03 [Digital]",
            series_name="Dandadan", release_group="Group", is_bundle=True,
            volume_start=1.0, volume_end=3.0, volumes=[1.0, 2.0, 3.0],
            download_url="magnet:?xt=urn:btih:dandadan_hash_12345",
            link="https://nyaa.si/view/999001")
        mock_provider = MagicMock()
        mock_provider.fetch_releases.return_value = [rel_item]

        # Scenario 1: initial poll -> series not in Calibre -> DISCOVERY, no download
        with patch("app.scheduler.create_provider", return_value=mock_provider):
            res1 = sched.check_all_feeds()
            _check(res1["releases_found"] == 1, f"expected 1 release, got {res1['releases_found']}")
            _check(res1["requested"] == 0)
            _check(res1["sent_to_downloader"] == 0)
            _check(mock_qbit.add_torrent.call_count == 0)
            db_rel = db.get_release_by_id(1)
            _check(db_rel is not None)
            _check(db_rel["status"] == "DISCOVERY", f"expected DISCOVERY, got {db_rel['status']}")
            _check(db_rel["download_status"] == "IDLE")

        # Scenario 2: user adds Volume 1 of the series to Calibre -> re-poll computes
        # REQUESTED (missing 2,3) for a release that already existed as DISCOVERY.
        # This is the transition the Feature 8 fix must detect; before the fix
        # auto-download was gated on is_new only and never fired.
        _write_calibre_library(calibre_db, {"Dandadan": [1.0]})
        with patch("app.scheduler.create_provider", return_value=mock_provider):
            res2 = sched.check_all_feeds()
            _check(res2["releases_found"] == 1)
            _check(res2["requested"] == 1, f"expected 1 requested, got {res2['requested']}")
            _check(res2["sent_to_downloader"] == 1, "AUTO-DOWNLOAD MUST FIRE ON TRANSITION")
            _check(mock_qbit.add_torrent.call_count == 1)
            db_rel = db.get_release_by_id(1)
            _check(db_rel["status"] == "REQUESTED", f"expected REQUESTED, got {db_rel['status']}")
            _check(db_rel["download_status"] == "DOWNLOADING")
            _check(db_rel["torrent_hash"] == "dandadan_hash_12345")
            _check(mock_qbit.apply_selective_volume_download.call_args is not None,
                   "selective volume filtering must be applied to the bundle")
            _check(mock_qbit.apply_selective_volume_download.call_args[0][1] == [2.0, 3.0])

        # Scenario 3: poll 3 -> duplicate queuing prevention
        with patch("app.scheduler.create_provider", return_value=mock_provider):
            res3 = sched.check_all_feeds()
            _check(res3["releases_found"] == 1)
            _check(res3["sent_to_downloader"] == 0)
            _check(mock_qbit.add_torrent.call_count == 1)

        # Scenario 4: a NEW release (series absent from Calibre, non-Vol.1) transitions to
        # REQUESTED when the user adds the series to Calibre -> auto-download fires once.
        rel_v2 = ReleaseItem(
            guid="https://nyaa.si/view/999002",
            title="[Group] Spy x Family Vol. 02-03 [Digital]",
            series_name="Spy x Family", release_group="Group", is_bundle=True,
            volume_start=2.0, volume_end=3.0, volumes=[2.0, 3.0],
            download_url="magnet:?xt=urn:btih:sxf_hash_67890",
            link="https://nyaa.si/view/999002")
        mock_provider2 = MagicMock()
        mock_provider2.fetch_releases.return_value = [rel_v2]
        with patch("app.scheduler.create_provider", return_value=mock_provider2):
            res_new = sched.check_all_feeds()
            rel_v2_db = db.get_release_by_id(2)
            _check(rel_v2_db["status"] == "NEW", f"expected NEW, got {rel_v2_db['status']}")
            _check(rel_v2_db["download_status"] == "IDLE")
            _check(mock_qbit.add_torrent.call_count == 1)

            _write_calibre_library(calibre_db, {"Dandadan": [1.0], "Spy x Family": [1.0]})
            res_trans = sched.check_all_feeds()
            _check(res_trans["sent_to_downloader"] == 1, "NEW -> REQUESTED transition must auto-download")
            _check(mock_qbit.add_torrent.call_count == 2)
            rel_v2_db_after = db.get_release_by_id(2)
            _check(rel_v2_db_after["status"] == "REQUESTED")
            _check(rel_v2_db_after["download_status"] == "DOWNLOADING")

        print("    [PASSED] Feature 8: Auto-download transitions & deduplication verified.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# Feature 9: Grouped Cache Invalidation
# ===========================================================================
def test_feature_9_cache_invalidation_operations():
    print("--> Testing Feature 9: Grouped Cache Invalidation Operations...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_test_f9_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "test_f9.db"))

        db.upsert_release({
            "guid": "guid-f9-1",
            "title": "Dandadan Vol. 01",
            "series_name": "Dandadan",
            "status": "DISCOVERY"
        })

        # 1. Warm cache
        db.get_releases_grouped_by_series()
        _check(len(db._grouped_cache) > 0, "Cache should be populated after grouped call")

        # Test track_discovered_series invalidation
        db.track_discovered_series("Dandadan", 1)
        _check(db._grouped_cache == {}, "track_discovered_series failed to invalidate _grouped_cache")

        # 2. Warm cache again
        db.get_releases_grouped_by_series()
        _check(len(db._grouped_cache) > 0)

        # Test clear_all_descriptions invalidation
        db.clear_all_descriptions()
        _check(db._grouped_cache == {}, "clear_all_descriptions failed to invalidate _grouped_cache")

        # 3. Warm cache again
        db.get_releases_grouped_by_series()
        _check(len(db._grouped_cache) > 0)

        # Test clear_discovered_series invalidation
        db.clear_discovered_series()
        _check(db._grouped_cache == {}, "clear_discovered_series failed to invalidate _grouped_cache")

        # 4. Warm cache again
        db.get_releases_grouped_by_series()
        _check(len(db._grouped_cache) > 0)

        # Test legacy key repair invalidation (normalize_series_titles_to_english is
        # now a deprecated alias of the guarded P3a re-key repair).
        mock_anilist = MagicMock()
        mock_anilist.resolve_manga_series.return_value = {}
        db.normalize_series_titles_to_english(mock_anilist)
        _check(db._grouped_cache == {}, "legacy key repair failed to invalidate _grouped_cache")

        print("    [PASSED] Feature 9: Grouped cache invalidations across all 4 operations verified.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# Feature 10: Unbounded Parser Loop Guard
# ===========================================================================
def test_feature_10_parser_loop_bounds():
    print("--> Testing Feature 10: Unbounded Parser Loop Guard & Algorithmic DoS Defense...")

    # Test 1: Malicious / DoS extreme range
    dos_title = "Attack on Titan Vol. 1-10000000 [CBZ]"
    res = extract_manga_bundle_and_volumes(dos_title)
    _check(res["is_bundle"] is True)
    _check(res["volume_start"] == 1.0)
    # Bound must be clamped to start_vol + MAX_BUNDLE_SPAN
    _check(res["volume_end"] == 1.0 + MAX_BUNDLE_SPAN)
    _check(len(res["volumes"]) == MAX_BUNDLE_SPAN + 1)
    _check(res["volumes"][0] == 1.0)
    _check(res["volumes"][-1] == 1.0 + MAX_BUNDLE_SPAN)

    # Test 2: Standard bundle within bounds
    normal_title = "Chainsaw Man Vol. 01-05 [CBZ]"
    res_norm = extract_manga_bundle_and_volumes(normal_title)
    _check(res_norm["is_bundle"] is True)
    _check(res_norm["volume_start"] == 1.0)
    _check(res_norm["volume_end"] == 5.0)
    _check(res_norm["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0])

    # Test 3: Inverted bundle range
    inv_title = "Bleach Vol. 10-01 [Digital]"
    res_inv = extract_manga_bundle_and_volumes(inv_title)
    _check(res_inv["is_bundle"] is True)
    _check(res_inv["volume_start"] == 1.0)
    _check(res_inv["volume_end"] == 10.0)
    _check(len(res_inv["volumes"]) == 10)

    # Test 4: Exactly MAX_BUNDLE_SPAN
    span_title = f"Epic Saga Vol. 1-{MAX_BUNDLE_SPAN + 1}"
    res_span = extract_manga_bundle_and_volumes(span_title)
    _check(len(res_span["volumes"]) == MAX_BUNDLE_SPAN + 1)

    print("    [PASSED] Feature 10: Parser loop bounds strictly enforced.")


# ===========================================================================
# Feature 11: Calibre Sync Startup Deduplication & Non-Blocking Concurrency
# ===========================================================================
def test_feature_11_calibre_sync_startup_deduplication():
    print("--> Testing Feature 11: Calibre Sync Startup Deduplication & Concurrency Lock...")
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_f11_")
    db_file = os.path.join(temp_dir, "test.db")
    calibre_db = os.path.join(temp_dir, "metadata.db")

    try:
        create_mock_calibre_manga_db(calibre_db)

        db = MangaDatabase(db_file)
        db.set_setting("calibre_path", calibre_db)
        db.set_setting("calibre_sync_interval_minutes", "15")
        cover_cache = CoverCache(temp_dir)
        sched = MangaScheduler(db, cover_cache)

        # Scenario 1: Verify last_calibre_sync updates on call
        _check(sched.last_calibre_sync == 0.0)
        res = sched.sync_calibre_library()
        _check(res["success"] is True)
        _check(sched.last_calibre_sync > 0.0)

        # Scenario 2: Concurrency Lock Deduplication
        acquired = sched._calibre_sync_lock.acquire(blocking=False)
        _check(acquired is True)
        concurrent_res = sched.sync_calibre_library()
        _check(concurrent_res["success"] is True)
        _check("already in progress" in concurrent_res["message"])
        sched._calibre_sync_lock.release()

        # Scenario 3: Startup Sequence Deduplication
        sched2 = MangaScheduler(db, cover_cache)
        sync_call_count = 0
        original_sync = sched2.sync_calibre_library

        def instrumented_sync():
            nonlocal sync_call_count
            sync_call_count += 1
            return original_sync()

        sched2.sync_calibre_library = instrumented_sync

        # Lifespan step 1: sync_calibre_library() runs before scheduler.start()
        sched2.sync_calibre_library()
        _check(sync_call_count == 1)
        _check(sched2.last_calibre_sync > 0.0)

        # Lifespan step 2: scheduler.start() begins worker thread
        sched2.start()
        time.sleep(0.5)

        # Background worker thread must NOT have executed a redundant 2nd sync at boot!
        _check(sync_call_count == 1)
        sched2.stop()

        print("    [PASSED] Feature 11: Calibre sync startup deduplication & concurrency lock verified.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# Feature 12: Polite Crawl Delay Respecting Start Page Offset
# ===========================================================================
def test_feature_12_nyaa_crawl_delay():
    print("--> Testing Feature 12: Nyaa Crawl Delay With Start Offset...")
    provider = NyaaMangaProvider(name="Test Nyaa", config={"delay_seconds": 0.5})

    dummy_html = """
    <html><body>
    <table class="torrent-list">
      <tbody>
        <tr>
          <td>Category</td>
          <td><a href="/view/101">One Piece Vol. 101</a></td>
          <td><a href="/download/101.torrent">dl</a></td>
          <td>500 MiB</td>
          <td>2026-10-31</td>
          <td>10</td>
        </tr>
      </tbody>
    </table>
    </body></html>
    """

    class DummyResponse:
        status = 200
        def read(self):
            return dummy_html.encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    # Test 1: Crawl starting at page 5 with max_pages = 3 and delay = 0.25
    sleep_calls = []
    def fake_sleep(sec):
        sleep_calls.append(sec)

    with patch("urllib.request.urlopen", return_value=DummyResponse()):
        with patch("time.sleep", side_effect=fake_sleep):
            res = provider.crawl_pages(max_pages=3, start_page=5, delay=0.25)
            _check(len(res) >= 1)
            # Must sleep between page 5 and 6, and between page 6 and 7 -> exactly 2 sleeps!
            _check(len(sleep_calls) == 2, f"Expected 2 sleep calls, got {len(sleep_calls)}")
            _check(all(s == 0.25 for s in sleep_calls))

    # Test 2: Single page crawl with offset (max_pages=1, start_page=5) -> 0 sleeps
    sleep_calls.clear()
    with patch("urllib.request.urlopen", return_value=DummyResponse()):
        with patch("time.sleep", side_effect=fake_sleep):
            provider.crawl_pages(max_pages=1, start_page=5, delay=0.25)
            _check(len(sleep_calls) == 0, f"Expected 0 sleep calls for single page, got {len(sleep_calls)}")

    # Test 3: Normal crawl starting at page 1 (max_pages=3, start_page=1) -> 2 sleeps
    sleep_calls.clear()
    with patch("urllib.request.urlopen", return_value=DummyResponse()):
        with patch("time.sleep", side_effect=fake_sleep):
            provider.crawl_pages(max_pages=3, start_page=1, delay=0.25)
            _check(len(sleep_calls) == 2, f"Expected 2 sleep calls for page 1 crawl, got {len(sleep_calls)}")

    print("    [PASSED] Feature 12: Nyaa crawl delay properly honors start offsets.")


# ===========================================================================
# Feature 13: HTML Scraper Robustness & Dependency Fallback
# ===========================================================================
def test_feature_13_html_scraper_robustness():
    print("--> Testing Feature 13: HTML Scraper Robustness & Dependency Fallback...")

    dummy_html = """
    <html><body>
      <article>
        <h2><a href="/releases/berserk-v41">Berserk Vol. 41 [Digital]</a></h2>
        <a class="download" href="magnet:?xt=urn:btih:dummyhash">Download</a>
      </article>
    </body></html>
    """

    class DummyResponse:
        status = 200
        def read(self):
            return dummy_html.encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    # Test 1: Execute when httpx is None (simulating Calibre Python runtime)
    orig_httpx = hs_module.httpx
    try:
        hs_module.httpx = None
        provider = HtmlScraperProvider(name="Test Scraper", url="https://manga-scrapes.internal/releases")
        with patch("urllib.request.urlopen", return_value=DummyResponse()):
            releases = provider.fetch_releases()
            _check(len(releases) == 1)
            _check(releases[0].series_name == "Berserk")
            _check(releases[0].volume_start == 41.0)
    finally:
        hs_module.httpx = orig_httpx

    # Test 2: Network failure resilience (both httpx and urllib fail)
    provider_fail = HtmlScraperProvider(name="Failing Scraper", url="https://unreachable.invalid/releases")
    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        # Must NOT raise exception; must return empty list safely
        res_fail = provider_fail.fetch_releases()
        _check(res_fail == [])

    print("    [PASSED] Feature 13: HTML scraper is resilient to missing httpx and network failures.")


# ===========================================================================
# Runner
# ===========================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Running MangaTrax Milestone 1 Defect Fixes Suite")
    print("=" * 60)
    test_feature_6_release_download_lookup_and_bundle_volumes()
    test_feature_7_upsert_release_status_transitions_and_ignore_preservation()
    test_feature_8_auto_download_transitions_and_deduplication()
    test_feature_9_cache_invalidation_operations()
    test_feature_10_parser_loop_bounds()
    test_feature_11_calibre_sync_startup_deduplication()
    test_feature_12_nyaa_crawl_delay()
    test_feature_13_html_scraper_robustness()
    print("=" * 60)
    print("ALL MILESTONE 1 DEFECT FIXES VERIFIED CLEANLY!")
    print("=" * 60)
