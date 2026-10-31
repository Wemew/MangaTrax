import os
import sys
import tempfile
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.database import MangaDatabase
from app.scheduler import MangaScheduler
from app.covers import CoverCache
from app.providers.base import ReleaseItem

import shutil

def test_ignore_and_unignore_release_lifecycle():
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "test_ignore.db")
        db = MangaDatabase(db_path)
        db.init_db()

        # Add 3 releases
        _, r1_id = db.upsert_release({
            "guid": "guid-op-01",
            "title": "One Piece v01 [Digital]",
            "series_name": "One Piece",
            "volume_start": 1.0,
            "volume_end": 1.0,
            "status": "REQUESTED"
        })
        _, r2_id = db.upsert_release({
            "guid": "guid-op-02",
            "title": "One Piece v02 [Digital]",
            "series_name": "One Piece",
            "volume_start": 2.0,
            "volume_end": 2.0,
            "status": "REQUESTED"
        })
        _, r3_id = db.upsert_release({
            "guid": "guid-nr-01",
            "title": "Naruto v01 [Digital]",
            "series_name": "Naruto",
            "volume_start": 1.0,
            "volume_end": 1.0,
            "status": "REQUESTED"
        })

        # By default, all 3 are returned
        all_releases = db.get_releases()
        assert len(all_releases) == 3

        # Ignore release 2
        db.update_release_status(r2_id, "IGNORED")
        rel_2 = db.get_release_by_id(r2_id)
        assert rel_2["status"] == "IGNORED"

        # Default get_releases should now only return r1 and r3 (status NOT IN ('BLACKLISTED', 'IGNORED'))
        active_releases = db.get_releases()
        active_ids = [r["id"] for r in active_releases]
        assert r2_id not in active_ids
        assert len(active_releases) == 2

        # Grouped view should also omit r2
        grouped = db.get_releases_grouped_by_series()
        op_releases = [r for g in grouped if g["series_name"] == "One Piece" for r in g["releases"]]
        assert len(op_releases) == 1
        assert op_releases[0]["id"] == r1_id

        # Querying status="IGNORED" specifically should return r2
        ignored_releases = db.get_releases(status="IGNORED")
        assert len(ignored_releases) == 1
        assert ignored_releases[0]["id"] == r2_id

        # Blacklist series "Naruto"
        db.add_to_blacklist("Naruto", reason="Testing blacklist")
        
        # Now default get_releases should exclude Naruto entirely (r3 excluded)
        active_releases_after_bl = db.get_releases()
        assert len(active_releases_after_bl) == 1
        assert active_releases_after_bl[0]["id"] == r1_id

        # Unignore release 2 -> restores to REQUESTED
        db.update_release_status(r2_id, "REQUESTED")
        active_after_unignore = db.get_releases()
        assert len(active_after_unignore) == 2
        active_ids_after = [r["id"] for r in active_after_unignore]
        assert r1_id in active_ids_after
        assert r2_id in active_ids_after

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print(" [PASSED] Ignore and Unignore release database lifecycle verified.")

def test_backfill_start_page_and_resume():
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "test_sched.db")
        db = MangaDatabase(db_path)
        db.init_db()

        scheduler = MangaScheduler(db=db, cover_cache=CoverCache(tmpdir))

        # Mock NyaaMangaProvider crawl_pages
        crawled_pages = []
        def mock_crawl_pages(self, max_pages=15, start_page=1, delay=0, stop_check=None):
            # Record which pages were requested
            crawled_pages.append((start_page, max_pages))
            return [ReleaseItem(
                guid=f"nyaa-page-{start_page}",
                title=f"Manga Release P{start_page} v01",
                link=f"https://nyaa.si/view/{start_page}",
                pub_date="2026-10-31"
            )]

        with patch("app.scheduler.NyaaMangaProvider.crawl_pages", mock_crawl_pages):
            # Start backfill with start_page=5, max_pages=3
            res = scheduler.run_polite_backfill(max_pages=3, start_page=5, delay_seconds=0)
            assert res["status"] == "started"

            # Let worker thread execute
            import time
            for _ in range(40):
                status = scheduler.get_backfill_status()
                if not status["running"]:
                    break
                time.sleep(0.05)

        # Verify pages were requested starting from 5
        assert len(crawled_pages) == 3
        requested_start_pages = [call[0] for call in crawled_pages]
        assert requested_start_pages == [5, 6, 7]

        # Verify last_page was tracked in settings
        last_page = db.get_setting("backfill_last_page")
        assert last_page == 7

        # Verify get_backfill_status returns last_page
        final_status = scheduler.get_backfill_status()
        assert final_status["last_page"] == 7
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(" [PASSED] Polite backfill start-page and resumption verified.")

def test_template_features_and_modal():
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "templates"))
    
    with open(os.path.join(template_dir, "base.html"), "r", encoding="utf-8") as f:
        base_html = f.read()
    with open(os.path.join(template_dir, "releases.html"), "r", encoding="utf-8") as f:
        releases_html = f.read()
    with open(os.path.join(template_dir, "prioritized.html"), "r", encoding="utf-8") as f:
        prioritized_html = f.read()
    with open(os.path.join(template_dir, "discovery.html"), "r", encoding="utf-8") as f:
        discovery_html = f.read()

    # Base HTML should have modal-backfill-setup and universal functions
    assert 'id="modal-backfill-setup"' in base_html
    assert 'backfill-input-start-page' in base_html
    assert 'backfill-input-max-pages' in base_html
    assert 'openBackfillModal' in base_html
    assert 'blacklistSeriesGlobal' in base_html
    assert 'ignoreReleaseGlobal' in base_html
    assert 'unignoreReleaseGlobal' in base_html
    assert 'backfillSeriesGlobal' in base_html

    # Releases HTML should feature blacklist, backfill, track and ignore
    assert 'ignoreReleaseGlobal' in releases_html
    assert 'unignoreReleaseGlobal' in releases_html
    assert 'blacklistSeriesGlobal' in releases_html
    assert 'backfillSeriesGlobal' in releases_html
    assert 'handleTrackSeries' in releases_html
    assert 'status=IGNORED' in releases_html

    # Prioritized HTML should also feature parity
    assert 'ignoreReleaseGlobal' in prioritized_html
    assert 'unignoreReleaseGlobal' in prioritized_html
    assert 'blacklistSeriesGlobal' in prioritized_html
    assert 'backfillSeriesGlobal' in prioritized_html
    assert 'status=IGNORED' in prioritized_html

    # Discovery should trigger openBackfillModal
    assert 'openBackfillModal()' in discovery_html

    print(" [PASSED] Template feature parity and backfill modal verified.")

def test_all_python_files_syntax():
    import glob
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
    for py_file in glob.glob(os.path.join(backend_dir, "**", "*.py"), recursive=True):
        with open(py_file, "r", encoding="utf-8") as f:
            code = f.read()
        compile(code, py_file, "exec")
    print(" [PASSED] All backend Python files compile cleanly with zero syntax errors.")

def test_pending_requests_and_bundle_download_filter():
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "test_reqs.db")
        db = MangaDatabase(db_path)
        db.init_db()

        # Add requested releases
        for i in range(15):
            db.upsert_release({
                "guid": f"guid-req-{i}",
                "title": f"Manga Item {i} v01",
                "series_name": f"Series {i}",
                "status": "REQUESTED"
            })

        # Test get_pending_requests with and without limit
        p_10 = db.get_pending_requests(limit=10)
        assert len(p_10) == 10

        p_all = db.get_pending_requests()
        assert len(p_all) == 15

        # Add a downloading bundle release
        db.upsert_release({
            "guid": "guid-bundle-dl",
            "title": "Bleach v01-05 [Digital]",
            "series_name": "Bleach",
            "is_bundle": 1,
            "volume_start": 1.0,
            "volume_end": 5.0,
            "volumes": [1.0, 2.0, 3.0, 4.0, 5.0],
            "missing_volumes": [4.0, 5.0],
            "download_status": "DOWNLOADING",
            "torrent_hash": "abcdef1234567890abcdef1234567890abcdef12"
        })

        scheduler = MangaScheduler(db=db, cover_cache=CoverCache(tmpdir))

        # Enable mock qbit to exercise the SQL query
        mock_qbit = MagicMock()
        mock_qbit.enabled = True
        mock_qbit.extract_torrent_hash = MagicMock(return_value="abcdef1234567890abcdef1234567890abcdef12")
        mock_qbit.filter_torrent_bundle_files = MagicMock(return_value={"kept_volumes": [4.0, 5.0], "skipped_volumes": [1.0, 2.0, 3.0]})
        scheduler.get_qbit_client = MagicMock(return_value=mock_qbit)

        # Test check_and_filter_active_downloads executes query and filtering cleanly
        filtered = scheduler.check_and_filter_active_downloads()
        assert filtered == 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(" [PASSED] get_pending_requests(limit) and bundle download filter query verified.")

def test_calibre_volume_bundle_edgecase_handling():
    tmpdir = tempfile.mkdtemp()
    try:
        calibre_dir = os.path.join(tmpdir, "calibre")
        os.makedirs(calibre_dir, exist_ok=True)
        db_path = os.path.join(calibre_dir, "metadata.db")

        # Create Calibre metadata.db
        from mock_calibre_manga import create_mock_calibre_manga_db
        create_mock_calibre_manga_db(db_path)

        # Insert edgecase volume bundle: Aharen-san is Indecipherable v01-02
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("INSERT INTO series (name) VALUES ('Aharen-san is Indecipherable');")
        s_id = cur.lastrowid
        # In Calibre, series_index is single float 1.0 even for v01-02!
        cur.execute("INSERT INTO books (title, series_index) VALUES ('Aharen-san is Indecipherable v01-02', 1.0);")
        b_id = cur.lastrowid
        cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?);", (b_id, s_id))
        conn.commit()
        conn.close()

        from app.calibre.scanner import CalibreMangaScanner
        from app.calibre.matcher import MangaCalibreMatcher

        scanner = CalibreMangaScanner(calibre_dir)
        assert scanner.is_available()

        # 1. Verify get_series_volumes_map resolves BOTH 1.0 and 2.0
        vols_map = scanner.get_series_volumes_map()
        aharen_vols = vols_map.get("aharen-san is indecipherable")
        assert aharen_vols is not None, "Series 'Aharen-san is Indecipherable' not found in volumes map!"
        assert 1.0 in aharen_vols, "Volume 1.0 missing from owned volumes!"
        assert 2.0 in aharen_vols, "Volume 2.0 missing from bundle in owned volumes!"

        # 2. Verify get_all_series reports latest_index = 2.0 (not 1.0)
        all_series = scanner.get_all_series()
        aharen_series = next((s for s in all_series if s["series_name"] == "Aharen-san is Indecipherable"), None)
        assert aharen_series is not None
        assert aharen_series["latest_index"] == 2.0, f"Expected latest_index 2.0, got {aharen_series['latest_index']}"

        # 3. Verify matcher checks
        matcher = MangaCalibreMatcher(all_series, vols_map)

        # Release for v01-02 should be IN_CALIBRE (0 missing)
        rel_bundle = {
            "title": "Aharen-san is Indecipherable v01-02 [Seven Seas]",
            "series_name": "Aharen-san is Indecipherable",
            "is_bundle": True,
            "volumes": [1.0, 2.0]
        }
        res_bundle = matcher.check_release(rel_bundle)
        assert res_bundle["status"] == "IN_CALIBRE", f"Expected IN_CALIBRE, got {res_bundle['status']}"
        assert res_bundle["missing_volumes"] == []

        # Release for single volume v02 should be IN_CALIBRE
        rel_v2 = {
            "title": "Aharen-san is Indecipherable v02 [Digital]",
            "series_name": "Aharen-san is Indecipherable",
            "is_bundle": False,
            "volumes": [2.0]
        }
        res_v2 = matcher.check_release(rel_v2)
        assert res_v2["status"] == "IN_CALIBRE", f"Expected IN_CALIBRE for v02, got {res_v2['status']}"

        # Release for v01-03 should be REQUESTED with ONLY volume 3.0 missing
        rel_v3_bundle = {
            "title": "Aharen-san is Indecipherable v01-03 [Digital]",
            "series_name": "Aharen-san is Indecipherable",
            "is_bundle": True,
            "volumes": [1.0, 2.0, 3.0]
        }
        res_v3 = matcher.check_release(rel_v3_bundle)
        assert res_v3["status"] == "REQUESTED"
        assert res_v3["missing_volumes"] == [3.0], f"Expected missing [3.0], got {res_v3['missing_volumes']}"

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(" [PASSED] Calibre volume bundle edgecase (Aharen-san v01-02) fully verified.")

if __name__ == "__main__":
    test_ignore_and_unignore_release_lifecycle()
    test_backfill_start_page_and_resume()
    test_template_features_and_modal()
    test_pending_requests_and_bundle_download_filter()
    test_calibre_volume_bundle_edgecase_handling()
    test_all_python_files_syntax()
    print("\nALL IGNORE, RESUME, AND PARITY TESTS PASSED!")
