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

        # Verify last_page was tracked in settings.
        # app_settings stores every value as TEXT (settings are strings by design),
        # so get_setting() returns the string "7" -- coerce before comparing to int.
        last_page = db.get_setting("backfill_last_page")
        assert last_page is not None, "backfill_last_page was not persisted"
        assert int(last_page) == 7, f"Expected backfill_last_page == 7, got {last_page!r}"

        # Verify get_backfill_status returns last_page
        final_status = scheduler.get_backfill_status()
        assert final_status["last_page"] == 7
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(" [PASSED] Polite backfill start-page and resumption verified.")

def test_template_features_and_modal():
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "templates"))

    def read_template(relative_path):
        with open(os.path.join(template_dir, relative_path), "r", encoding="utf-8") as f:
            return f.read()

    base_html = read_template("base.html")
    releases_html = read_template("releases.html")
    prioritized_html = read_template("prioritized.html")
    discovery_html = read_template("discovery.html")

    # The lazy-loading refactor (commit e6e1cf2) moved the per-release / per-series
    # action buttons out of releases.html / prioritized.html into shared Jinja partials
    # that those pages include. These assertions follow the markup to where it actually
    # lives; the previous version only read releases.html and had been stale since that
    # refactor (it never failed because asserts were stripped by optimize=2). Test bug.
    partial_names = [
        "series_card_chunk.html",
        "series_table_row_chunk.html",
        "flat_release_row_chunk.html",
    ]
    action_markup = "\n".join(read_template(os.path.join("partials", name)) for name in partial_names)

    # Base HTML should have modal-backfill-setup and universal functions
    assert 'id="modal-backfill-setup"' in base_html
    assert 'backfill-input-start-page' in base_html
    assert 'backfill-input-max-pages' in base_html
    assert 'openBackfillModal' in base_html
    assert 'blacklistSeriesGlobal' in base_html
    assert 'ignoreReleaseGlobal' in base_html
    assert 'unignoreReleaseGlobal' in base_html
    assert 'backfillSeriesGlobal' in base_html

    # Releases page must include the partials that render blacklist/backfill/track/ignore
    assert 'partials/series_card_chunk.html' in releases_html
    assert 'partials/flat_release_row_chunk.html' in releases_html
    assert 'ignoreReleaseGlobal' in action_markup
    assert 'unignoreReleaseGlobal' in action_markup
    assert 'blacklistSeriesGlobal' in action_markup
    assert 'backfillSeriesGlobal' in action_markup
    assert 'handleTrackSeries' in releases_html
    assert 'status=IGNORED' in releases_html

    # Prioritized HTML should also feature parity
    assert 'partials/series_card_chunk.html' in prioritized_html
    assert 'partials/flat_release_row_chunk.html' in prioritized_html
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

        # The selective-volume download flow now lives in
        # QBitClient.apply_selective_volume_download(), which lists the torrent's files via
        # get_torrent_files() and deselects owned volumes via set_file_priorities(). The old
        # filter_torrent_bundle_files() helper it used to mock no longer exists, so the mock
        # was inert and the real code path never ran (filtered == 0). Mock at the *current*
        # boundary and let the real selective-download logic execute -- stale test mock.
        from app.downloader.qbit import QBitClient

        torrent_hash = "abcdef1234567890abcdef1234567890abcdef12"
        mock_qbit = QBitClient(base_url="http://mock-qbit:8080", username="admin", password="adminadmin", enabled=True)
        mock_qbit._ensure_auth = MagicMock(return_value=True)
        mock_qbit.get_torrent_files = MagicMock(return_value=[
            {"index": 0, "name": "Bleach Vol. 01.cbz"},
            {"index": 1, "name": "Bleach Vol. 02.cbz"},
            {"index": 2, "name": "Bleach Vol. 03.cbz"},
            {"index": 3, "name": "Bleach Vol. 04.cbz"},
            {"index": 4, "name": "Bleach Vol. 05.cbz"},
        ])
        mock_qbit.set_file_priorities = MagicMock(return_value=True)
        scheduler.get_qbit_client = MagicMock(return_value=mock_qbit)

        # Test check_and_filter_active_downloads executes query and filtering cleanly
        filtered = scheduler.check_and_filter_active_downloads()
        assert filtered == 1, f"expected 1 filtered bundle, got {filtered}"
        # Volumes 1-3 are already owned (present in Calibre) -> priority 0 (skip);
        # missing volumes 4-5 keep downloading.
        mock_qbit.set_file_priorities.assert_called_once_with(torrent_hash, [0, 1, 2], priority=0)
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
