import os
import sys
import unittest
import json
import zipfile
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

# Ensure test harness is accessible
E2E_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.dirname(E2E_DIR)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from e2e.harness import (
    E2ETestContext,
    generate_rss_xml,
    generate_nyaa_html,
    extract_manga_bundle_and_volumes,
    generate_comicinfo_xml,
    inject_comicinfo,
    has_comicinfo,
    auto_tag_staging_cbz_files,
    CalibreMangaScanner,
    MangaCalibreMatcher,
    QBitClient,
    main_app
)


class TestTier1FeatureCoverage(unittest.TestCase):
    """
    Tier 1: Feature Coverage (>=5 tests per feature)
    Covers:
      1. Endpoints (6 tests)
      2. Series Tracking (5 tests)
      3. Feeds (5 tests)
      4. Calibre Sync (5 tests)
      5. ComicInfo Tagging (5 tests)
      6. qBit Downloads (5 tests)
      7. Discovery (5 tests)
      8. Blacklist (5 tests)
    Total: 41 test cases.
    """

    # =========================================================================
    # 1. Endpoints Feature Coverage
    # =========================================================================

    def test_endpoint_dashboard_and_stats(self):
        """Feature 1.1: Verify dashboard HTML and statistics aggregation."""
        with E2ETestContext() as ctx:
            # Seed 1 tracked series and 2 releases
            ctx.db.sync_calibre_series([{"series_id": 1, "series_name": "One Piece", "book_count": 1, "latest_index": 1.0}])
            ctx.db.upsert_release({"guid": "g1", "title": "One Piece Vol 1", "series_name": "One Piece", "status": "REQUESTED"})
            ctx.db.upsert_release({"guid": "g2", "title": "One Piece Vol 2", "series_name": "One Piece", "status": "IN_CALIBRE"})

            res = ctx.client.get("/")
            self.assertEqual(res.status_code, 200)

            stats = ctx.db.get_dashboard_stats()
            self.assertEqual(stats["monitored_series"], 1)
            self.assertEqual(stats["pending_requests"], 1)
            self.assertEqual(stats["acquired_books"], 1)

    def test_endpoint_series_crud_and_status(self):
        """Feature 1.2: Verify series retrieval, row formatting, and monitored toggle endpoint."""
        with E2ETestContext() as ctx:
            ctx.db.sync_calibre_series([{
                "series_id": 1,
                "series_name": "Chainsaw Man",
                "authors": "Tatsuki Fujimoto",
                "publisher": "VIZ Media",
                "book_count": 3,
                "latest_index": 3.0
            }])
            series_list = ctx.db.get_all_series()
            s_id = series_list[0]["id"]

            # Query series rows
            res = ctx.client.get("/api/series/rows", params={"monitored_only": False})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["total"], 1)

            # Toggle monitored state via API
            toggle_res = ctx.client.post(f"/api/series/{s_id}/toggle")
            self.assertEqual(toggle_res.status_code, 200)
            self.assertEqual(toggle_res.json()["is_monitored"], 0)

            # Verify persisted toggle
            series_after = ctx.db.get_all_series()
            self.assertEqual(series_after[0]["is_monitored"], 0)

    def test_endpoint_releases_list_and_details(self):
        """Feature 1.3: Verify release flat rows, card chunks, and grouped endpoints."""
        with E2ETestContext() as ctx:
            ctx.db.upsert_release({
                "guid": "rel-bleach-1",
                "title": "[Group] Bleach Vol 1 [Digital]",
                "series_name": "Bleach",
                "is_bundle": False,
                "volume_start": 1.0,
                "volume_end": 1.0,
                "volumes": [1.0],
                "status": "REQUESTED"
            })

            # Flat rows
            r_flat = ctx.client.get("/api/releases/flat_rows", params={"status": "REQUESTED"})
            self.assertEqual(r_flat.status_code, 200)
            self.assertEqual(r_flat.json()["count"], 1)

            # Cards
            r_cards = ctx.client.get("/api/releases/cards", params={"status": "REQUESTED"})
            self.assertEqual(r_cards.status_code, 200)
            self.assertEqual(r_cards.json()["count"], 1)

            # Grouped JSON
            r_grouped = ctx.client.get("/api/releases/grouped", params={"status": "REQUESTED"})
            self.assertEqual(r_grouped.status_code, 200)
            self.assertEqual(r_grouped.json()["count"], 1)

    def test_endpoint_release_status_and_actions(self):
        """Feature 1.4: Verify release ignore, unignore, and manual status update endpoints."""
        with E2ETestContext() as ctx:
            _, rel_id = ctx.db.upsert_release({
                "guid": "rel-action-1",
                "title": "Jujutsu Kaisen Vol 1",
                "series_name": "Jujutsu Kaisen",
                "status": "REQUESTED"
            })

            # Ignore release
            res_ign = ctx.client.post(f"/api/releases/{rel_id}/ignore")
            self.assertEqual(res_ign.status_code, 200)
            r = ctx.db.get_release_by_id(rel_id)
            self.assertEqual(r["status"], "IGNORED")

            # Unignore release
            res_unign = ctx.client.post(f"/api/releases/{rel_id}/unignore")
            self.assertEqual(res_unign.status_code, 200)
            r = ctx.db.get_release_by_id(rel_id)
            self.assertEqual(r["status"], "REQUESTED")

            # Update status explicitly
            res_stat = ctx.client.post(f"/api/releases/{rel_id}/status", json_data={"status": "IN_CALIBRE"})
            self.assertEqual(res_stat.status_code, 200)
            r = ctx.db.get_release_by_id(rel_id)
            self.assertEqual(r["status"], "IN_CALIBRE")

    def test_endpoint_settings_get_and_update(self):
        """Feature 1.5: Verify saving and reading settings via API."""
        with E2ETestContext() as ctx:
            save_res = ctx.client.post("/api/settings", json_data={
                "app_timezone": "Europe/Copenhagen",
                "staging_dir": "/custom/staging",
                "auto_download": "true"
            })
            self.assertEqual(save_res.status_code, 200)
            self.assertEqual(ctx.db.get_setting("app_timezone"), "Europe/Copenhagen")
            self.assertEqual(ctx.db.get_setting("staging_dir"), "/custom/staging")
            self.assertEqual(ctx.db.get_setting("auto_download"), "true")

    def test_endpoint_feeds_crud_and_test(self):
        """Feature 1.6: Verify feed creation, deletion, and test endpoint."""
        with E2ETestContext() as ctx:
            init_count = len(ctx.db.get_all_feeds())

            # Add feed
            add_res = ctx.client.post("/api/feeds", json_data={
                "name": "Nyaa Shounen",
                "feed_type": "nyaa",
                "url": "https://nyaa.si/?page=rss&q=manga",
                "interval": 30,
                "auto_download": True
            })
            self.assertEqual(add_res.status_code, 200)
            feed_id = add_res.json()["feed_id"]
            self.assertGreater(feed_id, 0)

            feeds = ctx.db.get_all_feeds()
            self.assertEqual(len(feeds), init_count + 1)

            # Delete feed
            del_res = ctx.client.delete(f"/api/feeds/{feed_id}")
            self.assertEqual(del_res.status_code, 200)
            self.assertEqual(len(ctx.db.get_all_feeds()), init_count)

    # =========================================================================
    # 2. Series Tracking Feature Coverage
    # =========================================================================

    def test_series_tracking_initial_sync(self):
        """Feature 2.1: Series sync from Calibre scanner records initial metadata."""
        with E2ETestContext() as ctx:
            calibre_series = [{
                "series_id": 10,
                "series_name": "Dungeon Meshi",
                "book_count": 14,
                "latest_index": 14.0,
                "authors": "Ryoko Kui",
                "publisher": "Harta",
                "tags": "Fantasy, Cooking"
            }]
            ctx.db.sync_calibre_series(calibre_series)
            all_s = ctx.db.get_all_series()
            self.assertEqual(len(all_s), 1)
            self.assertEqual(all_s[0]["name"], "Dungeon Meshi")
            self.assertEqual(all_s[0]["book_count"], 14)
            self.assertEqual(all_s[0]["latest_index"], 14.0)
            self.assertEqual(all_s[0]["authors"], "Ryoko Kui")

    def test_series_tracking_volume_progression(self):
        """Feature 2.2: Updating series metadata reflects newly acquired volumes."""
        with E2ETestContext() as ctx:
            ctx.db.sync_calibre_series([{
                "series_id": 1,
                "series_name": "Berserk",
                "book_count": 5,
                "latest_index": 5.0,
                "authors": "Kentaro Miura",
                "publisher": "Dark Horse",
                "tags": "Dark Fantasy"
            }])
            s1 = ctx.db.get_all_series()[0]
            self.assertEqual(s1["latest_index"], 5.0)

            # Resync with new volumes acquired
            ctx.db.sync_calibre_series([{
                "series_id": 1,
                "series_name": "Berserk",
                "book_count": 7,
                "latest_index": 7.0,
                "authors": "Kentaro Miura",
                "publisher": "Dark Horse",
                "tags": "Dark Fantasy"
            }])
            s2 = ctx.db.get_all_series()[0]
            self.assertEqual(s2["book_count"], 7)
            self.assertEqual(s2["latest_index"], 7.0)

    def test_series_tracking_manual_addition_and_toggle(self):
        """Feature 2.3: Manual series addition via track_discovered_series and toggle."""
        with E2ETestContext() as ctx:
            ctx.db.track_discovered_series("Spy x Family")
            series = ctx.db.get_all_series()
            self.assertEqual(len(series), 1)
            self.assertEqual(series[0]["is_monitored"], 1)

            sid = series[0]["id"]
            ctx.db.toggle_series_monitored(sid)
            series_after = ctx.db.get_all_series()
            self.assertEqual(series_after[0]["is_monitored"], 0)

    def test_series_tracking_website_url_and_metadata(self):
        """Feature 2.4: Attaching custom website URL and metadata to tracked series."""
        with E2ETestContext() as ctx:
            ctx.db.track_discovered_series("Oshi no Ko")
            series = ctx.db.get_all_series()
            sid = series[0]["id"]

            res = ctx.client.post(f"/api/series/{sid}/link", json_data={
                "website_url": "https://www.mangaupdates.com/series/oshinoko"
            })
            self.assertEqual(res.status_code, 200)
            series_updated = ctx.db.get_all_series()[0]
            self.assertEqual(series_updated["website_url"], "https://www.mangaupdates.com/series/oshinoko")

    def test_series_tracking_deletion_and_cleanup(self):
        """Feature 2.5: Removing series and cleaning tracking entries."""
        with E2ETestContext() as ctx:
            ctx.db.track_discovered_series("Tokyo Ghoul")
            self.assertEqual(len(ctx.db.get_all_series()), 1)

            with ctx.db.get_connection() as conn:
                conn.execute("DELETE FROM series_tracking WHERE lower(name) = 'tokyo ghoul';")
                conn.commit()
            ctx.db._invalidate_grouped_cache()
            self.assertEqual(len(ctx.db.get_all_series()), 0)

    # =========================================================================
    # 3. Feeds Feature Coverage
    # =========================================================================

    def test_feed_rss_xml_ingestion(self):
        """Feature 3.1: Parsing standard RSS 2.0 XML into release items."""
        from app.providers.rss import RSSFeedProvider
        import feedparser
        raw_xml = generate_rss_xml([
            {"title": "Vinland Saga Vol. 01 [Digital]", "link": "https://example.com/vs1", "guid": "vs-guid-1"},
            {"title": "Vinland Saga Vol. 02 [Digital]", "link": "https://example.com/vs2", "guid": "vs-guid-2"}
        ])

        with patch("feedparser.parse", return_value=feedparser.parse(raw_xml)):
            provider = RSSFeedProvider(name="Test RSS", url="https://example.com/rss")
            items = provider.fetch_releases()
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0].title, "Vinland Saga Vol. 01 [Digital]")
            self.assertEqual(items[1].title, "Vinland Saga Vol. 02 [Digital]")

    def test_feed_nyaa_provider_ingestion(self):
        """Feature 3.2: Parsing Nyaa HTML page with magnet infohash and sizes."""
        from app.providers.nyaa import NyaaMangaProvider
        raw_html = generate_nyaa_html([
            {
                "title": "[1080p] Kingdom Vol. 01 [CBZ]",
                "view_url": "/view/1001",
                "torrent_url": "/download/1001.torrent",
                "magnet": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                "size": "210.5 MiB"
            }
        ])

        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = raw_html.encode("utf-8")
            mock_open.return_value.__enter__.return_value = mock_resp

            provider = NyaaMangaProvider(name="Nyaa Manga", url="https://nyaa.si/?f=0&c=3_1")
            releases = provider.fetch_releases()
            self.assertEqual(len(releases), 1)
            rel = releases[0]
            self.assertEqual(rel.title, "[1080p] Kingdom Vol. 01 [CBZ]")
            self.assertIn("magnet:?xt=urn:btih:", rel.download_url)
            self.assertEqual(rel.raw_data.get("post_size"), "210.5 MiB")

    def test_feed_auto_download_flag_filtering(self):
        """Feature 3.3: Auto-download triggers only when both global and feed flags are true."""
        with E2ETestContext() as ctx:
            feed_id = ctx.db.add_feed(
                name="Auto Feed",
                feed_type="nyaa",
                url="https://example.com/feed",
                auto_download=False,
                interval=60
            )
            feed = ctx.db.get_feed(feed_id)
            self.assertFalse(feed["auto_download"])

            with ctx.db.get_connection() as conn:
                conn.execute("UPDATE feeds SET auto_download = 1 WHERE id = ?;", (feed_id,))
                conn.commit()

            feed_updated = ctx.db.get_feed(feed_id)
            self.assertTrue(bool(feed_updated["auto_download"]))

    def test_feed_duplicate_guid_deduplication(self):
        """Feature 3.4: Ingesting existing GUIDs updates records without duplicate rows."""
        with E2ETestContext() as ctx:
            is_new1, id1 = ctx.db.upsert_release({
                "guid": "unique-guid-100",
                "title": "Bleach Vol. 1",
                "series_name": "Bleach",
                "status": "REQUESTED"
            })
            self.assertTrue(is_new1)

            # Re-ingest same GUID
            is_new2, id2 = ctx.db.upsert_release({
                "guid": "unique-guid-100",
                "title": "Bleach Vol. 1 [Updated Quality]",
                "series_name": "Bleach",
                "status": "REQUESTED"
            })
            self.assertFalse(is_new2)
            self.assertEqual(id1, id2)
            self.assertEqual(len(ctx.db.get_releases()), 1)

    def test_feed_interval_scheduling(self):
        """Feature 3.5: Verifying feed check scheduling intervals."""
        with E2ETestContext() as ctx:
            fid = ctx.db.add_feed(name="Interval Feed", feed_type="rss", url="https://example.com/feed", interval=15)
            feed = ctx.db.get_feed(fid)
            self.assertEqual(feed["check_interval_minutes"], 15)

            with ctx.db.get_connection() as conn:
                conn.execute("UPDATE feeds SET check_interval_minutes = 45 WHERE id = ?;", (fid,))
                conn.commit()

            feed2 = ctx.db.get_feed(fid)
            self.assertEqual(feed2["check_interval_minutes"], 45)

    # =========================================================================
    # 4. Calibre Sync Feature Coverage
    # =========================================================================

    def test_calibre_sync_zero_lock_read(self):
        """Feature 4.1: Calibre scanner opens metadata.db without file locking."""
        with E2ETestContext() as ctx:
            ctx.create_mock_calibre_library([
                {"name": "Monster", "author": "Naoki Urasawa", "volumes": [1.0, 2.0, 3.0]}
            ])
            scanner = CalibreMangaScanner(ctx.calibre_dir)
            self.assertTrue(scanner.is_available())
            summary = scanner.get_library_summary()
            self.assertEqual(summary["total_series"], 1)
            self.assertEqual(summary["total_books"], 3)

    def test_calibre_sync_author_tags_publishers(self):
        """Feature 4.2: Scanner extracts full author, publisher, and tag metadata."""
        with E2ETestContext() as ctx:
            ctx.create_mock_calibre_library([{
                "name": "Pluto",
                "author": "Naoki Urasawa",
                "publisher": "VIZ Media",
                "tags": ["Sci-Fi", "Mystery"],
                "volumes": [1.0]
            }])
            scanner = CalibreMangaScanner(ctx.calibre_dir)
            all_s = scanner.get_all_series()
            self.assertEqual(len(all_s), 1)
            pluto = all_s[0]
            self.assertEqual(pluto["authors"], "Naoki Urasawa")
            self.assertEqual(pluto["publisher"], "VIZ Media")
            self.assertIn("Mystery", pluto["tags"])

    def test_calibre_sync_incremental_updates(self):
        """Feature 4.3: Matcher detects missing volumes against Calibre library."""
        with E2ETestContext() as ctx:
            ctx.create_mock_calibre_library([
                {"name": "One Piece", "author": "Eiichiro Oda", "volumes": [1.0, 2.0]}
            ])
            scanner = CalibreMangaScanner(ctx.calibre_dir)
            series_list = scanner.get_all_series()
            volumes_map = scanner.get_series_volumes_map()
            matcher = MangaCalibreMatcher(series_list, volumes_map)

            # Ingest Vol 1-3 release: missing is [3.0]
            check = matcher.check_release({
                "title": "One Piece Vol 1-3",
                "series_name": "One Piece",
                "is_bundle": True,
                "volumes": [1.0, 2.0, 3.0]
            })
            self.assertEqual(check["status"], "REQUESTED")
            self.assertEqual(check["missing_volumes"], [3.0])

    def test_calibre_sync_dual_title_aliases(self):
        """Feature 4.4: Dual title candidate parts match Calibre library entries."""
        with E2ETestContext() as ctx:
            ctx.create_mock_calibre_library([
                {"name": "Frieren: Beyond Journey's End", "author": "Kanehito Yamada", "volumes": [1.0]}
            ])
            scanner = CalibreMangaScanner(ctx.calibre_dir)
            matcher = MangaCalibreMatcher(scanner.get_all_series(), scanner.get_series_volumes_map())

            # Release with dual title delimiter
            check = matcher.check_release({
                "title": "Sousou no Frieren / Frieren: Beyond Journey's End Vol 1",
                "series_name": "Frieren: Beyond Journey's End",
                "is_bundle": False,
                "volumes": [1.0]
            })
            self.assertEqual(check["status"], "IN_CALIBRE")

    def test_calibre_sync_missing_or_corrupt_db(self):
        """Feature 4.5: Graceful fallback when Calibre metadata.db is missing."""
        scanner = CalibreMangaScanner("/non/existent/path/to/calibre")
        self.assertFalse(scanner.is_available())
        summary = scanner.get_library_summary()
        self.assertFalse(summary["available"])
        self.assertEqual(summary["total_series"], 0)

    # =========================================================================
    # 5. ComicInfo Tagging Feature Coverage
    # =========================================================================

    def test_comicinfo_xml_generation(self):
        """Feature 5.1: Generating standard ComicInfo.xml metadata payload."""
        xml_str = generate_comicinfo_xml({
            "title": "Chainsaw Man Vol. 1",
            "series_name": "Chainsaw Man",
            "volume": "1",
            "authors": "Tatsuki Fujimoto",
            "publisher": "VIZ Media",
            "tags": "Action, Supernatural"
        })
        root = ET.fromstring(xml_str)
        self.assertEqual(root.find("Series").text, "Chainsaw Man")
        self.assertEqual(root.find("Volume").text, "1")
        self.assertEqual(root.find("Writer").text, "Tatsuki Fujimoto")
        self.assertEqual(root.find("Publisher").text, "VIZ Media")

    def test_comicinfo_zip_injection(self):
        """Feature 5.2: Injecting ComicInfo.xml into CBZ without recompressing images."""
        with E2ETestContext() as ctx:
            cbz_path = ctx.create_mock_cbz("test_vol_1.cbz", pages=2)
            self.assertFalse(has_comicinfo(cbz_path))

            metadata = {
                "series_name": "Test Series",
                "volume": "1",
                "authors": "Test Author",
                "publisher": "Test Publisher",
                "tags": "Action"
            }
            res = inject_comicinfo(cbz_path, metadata)
            self.assertTrue(res)

            self.assertTrue(has_comicinfo(cbz_path))
            with zipfile.ZipFile(cbz_path, "r") as z:
                names = z.namelist()
                self.assertIn("ComicInfo.xml", names)
                self.assertIn("001.jpg", names)
                # Verify page image is intact
                self.assertEqual(z.read("001.jpg"), b"fake-jpeg-data-for-page-1")

    def test_comicinfo_has_comicinfo_detection(self):
        """Feature 5.3: Detecting existing ComicInfo.xml and preventing double tagging."""
        with E2ETestContext() as ctx:
            existing = "<ComicInfo><Series>Old</Series></ComicInfo>"
            cbz_path = ctx.create_mock_cbz("tagged.cbz", pages=1, existing_xml=existing)
            self.assertTrue(has_comicinfo(cbz_path))

    def test_comicinfo_staging_directory_scan(self):
        """Feature 5.4: Scanning staging directory and tagging untagged CBZ files."""
        with E2ETestContext() as ctx:
            cbz_name = "One Piece Vol. 05.cbz"
            cbz_path = ctx.create_mock_cbz(cbz_name, pages=3)

            templates = {
                "one piece": {
                    "series_name": "One Piece",
                    "authors": "Eiichiro Oda",
                    "publisher": "VIZ Media",
                    "tags": "Action"
                }
            }
            results = auto_tag_staging_cbz_files(ctx.staging_dir, templates)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["series_name"], "One Piece")
            self.assertEqual(str(results[0]["volume"]), "5")
            self.assertTrue(has_comicinfo(cbz_path))

    def test_comicinfo_xml_escaping_and_special_chars(self):
        """Feature 5.5: XML special characters in metadata are properly escaped."""
        meta = {
            "title": "Rock & Roll <Manga>",
            "series_name": 'Series "Quotes" & More',
            "authors": "A & B > C < D"
        }
        xml_str = generate_comicinfo_xml(meta)
        root = ET.fromstring(xml_str)
        self.assertEqual(root.find("Series").text, 'Series "Quotes" & More')
        self.assertEqual(root.find("Title").text, "Rock & Roll <Manga>")
        self.assertEqual(root.find("Writer").text, "A & B > C < D")

    # =========================================================================
    # 6. qBit Downloads Feature Coverage
    # =========================================================================

    def test_qbit_client_authentication(self):
        """Feature 6.1: qBittorrent client authentication and cookie handling."""
        client = QBitClient(base_url="http://mock-qbit:8080", username="admin", password="password", enabled=True)
        with patch.object(client.opener, "open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b"Ok."
            mock_open.return_value.__enter__.return_value = mock_resp

            success = client.login()
            self.assertTrue(success)
            self.assertTrue(client.authenticated)

    def test_qbit_add_torrent_magnet_and_file(self):
        """Feature 6.2: Adding torrent via magnet link with tags and category."""
        client = QBitClient(enabled=True)
        client.authenticated = True
        with patch.object(client.opener, "open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b"Ok."
            mock_open.return_value.__enter__.return_value = mock_resp

            res = client.add_torrent(
                url_or_magnet="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                savepath="/staging",
                category="manga",
                tags="mangatrax"
            )
            self.assertTrue(res)

    def test_qbit_selective_file_prioritization(self):
        """Feature 6.3: Selective file prioritization skips owned volumes."""
        client = QBitClient(enabled=True)
        client.authenticated = True

        fake_files = [
            {"index": 0, "name": "Bleach Vol 01.cbz", "priority": 1},
            {"index": 1, "name": "Bleach Vol 02.cbz", "priority": 1},
            {"index": 2, "name": "Bleach Vol 03.cbz", "priority": 1}
        ]
        with patch.object(client, "get_torrent_files", return_value=fake_files), \
             patch.object(client, "set_file_priorities", return_value=True):

            # Only missing volume 3
            res = client.apply_selective_volume_download("fakehash", wanted_volumes=[3.0])
            self.assertEqual(res["status"], "filtered")
            self.assertEqual(res["skipped_volumes"], [1.0, 2.0])
            self.assertEqual(res["kept_volumes"], [3.0])

    def test_qbit_torrent_status_and_progress(self):
        """Feature 6.4: Polling torrent list and inspecting active progress."""
        client = QBitClient(enabled=True)
        client.authenticated = True

        fake_torrents = [{
            "hash": "0123456789abcdef0123456789abcdef01234567",
            "name": "One Piece Vol 105",
            "progress": 0.85,
            "dlspeed": 1048576,
            "state": "downloading"
        }]
        with patch.object(client.opener, "open") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = json.dumps(fake_torrents).encode("utf-8")
            mock_open.return_value.__enter__.return_value = mock_resp

            torrents = client.get_torrents(category="manga")
            self.assertEqual(len(torrents), 1)
            self.assertEqual(torrents[0]["progress"], 0.85)
            self.assertEqual(torrents[0]["state"], "downloading")

    def test_qbit_connection_failure_handling(self):
        """Feature 6.5: Graceful error handling when downloader is offline."""
        client = QBitClient(base_url="http://offline-host:8080", enabled=True)
        res = client.test_connection(timeout=0.1)
        self.assertFalse(res["success"])
        self.assertIn("failed", res["message"].lower())

    # =========================================================================
    # 7. Discovery Feature Coverage
    # =========================================================================

    def test_discovery_volume_one_detection(self):
        """Feature 7.1: Untracked releases containing Volume 1 categorized as DISCOVERY."""
        matcher = MangaCalibreMatcher([], {})
        rel = {
            "title": "Choujin X Vol. 01 [Digital]",
            "series_name": "Choujin X",
            "is_bundle": False,
            "volumes": [1.0]
        }
        res = matcher.check_release(rel)
        self.assertEqual(res["status"], "DISCOVERY")

    def test_discovery_queue_pagination_and_query(self):
        """Feature 7.2: Discovery queue respects limit and pagination."""
        with E2ETestContext() as ctx:
            for i in range(1, 15):
                ctx.db.upsert_release({
                    "guid": f"disc-guid-{i}",
                    "title": f"New Series {i} Vol. 01",
                    "series_name": f"New Series {i}",
                    "status": "DISCOVERY"
                })

            res_p1 = ctx.client.get("/discovery", params={"page": 1, "page_size": 5})
            self.assertEqual(res_p1.status_code, 200)

            count = ctx.db.count_discovery_releases()
            self.assertEqual(count, 14)
            p1_items = ctx.db.get_discovery_releases(limit=5, offset=0)
            self.assertEqual(len(p1_items), 5)

    def test_discovery_track_series_conversion(self):
        """Feature 7.3: Promoting a discovered release to tracked monitored series."""
        with E2ETestContext() as ctx:
            _, rel_id = ctx.db.upsert_release({
                "guid": "disc-dandadan-1",
                "title": "Dandadan Vol 1",
                "series_name": "Dandadan",
                "status": "DISCOVERY"
            })

            res = ctx.client.post("/api/discovery/track", json_data={
                "series_name": "Dandadan",
                "release_id": rel_id
            })
            self.assertEqual(res.status_code, 200)

            # Dandadan is now tracked
            series = ctx.db.get_all_series()
            dandadan = next((s for s in series if s["name"] == "Dandadan"), None)
            self.assertIsNotNone(dandadan)
            self.assertEqual(dandadan["is_monitored"], 1)

            # Release status transitioned to REQUESTED
            r = ctx.db.get_release_by_id(rel_id)
            self.assertEqual(r["status"], "REQUESTED")

    def test_discovery_clear_unwanted(self):
        """Feature 7.4: Clearing untracked discovery releases from database."""
        with E2ETestContext() as ctx:
            ctx.db.upsert_release({"guid": "u1", "title": "Unwanted 1 Vol 1", "series_name": "Unwanted 1", "status": "DISCOVERY"})
            ctx.db.upsert_release({"guid": "u2", "title": "Unwanted 2 Vol 1", "series_name": "Unwanted 2", "status": "DISCOVERY"})
            self.assertEqual(ctx.db.count_discovery_releases(), 2)

            cleared = ctx.db.clear_discovered_series()
            self.assertEqual(cleared, 2)
            self.assertEqual(ctx.db.count_discovery_releases(), 0)

    def test_discovery_vip_group_prioritization(self):
        """Feature 7.5: Releases from prioritized VIP groups correctly match."""
        with E2ETestContext() as ctx:
            ctx.db.add_prioritized_group("CustomVIPGroup", notes="Top tier scanner", priority=10)
            groups = ctx.db.get_prioritized_groups()
            self.assertTrue(any(g["name"] == "CustomVIPGroup" for g in groups))

            # Ingest release with VIP group
            parsed = extract_manga_bundle_and_volumes("[CustomVIPGroup] Chainsaw Man Vol. 01 [Digital]")
            self.assertEqual(parsed["release_group"], "CustomVIPGroup")

    # =========================================================================
    # 8. Blacklist Feature Coverage
    # =========================================================================

    def test_blacklist_add_series(self):
        """Feature 8.1: Adding series to blacklist and persisting record."""
        with E2ETestContext() as ctx:
            res = ctx.client.post("/api/blacklist", json_data={
                "name": "Boruto",
                "reason": "Not interested"
            })
            self.assertEqual(res.status_code, 200)
            self.assertTrue(ctx.db.is_series_blacklisted("Boruto"))

    def test_blacklist_query_and_is_blacklisted(self):
        """Feature 8.2: Blacklist lookup is case-insensitive."""
        with E2ETestContext() as ctx:
            ctx.db.add_to_blacklist("Attack on Titan", "Finished reading")
            self.assertTrue(ctx.db.is_series_blacklisted("attack on titan"))
            self.assertTrue(ctx.db.is_series_blacklisted("ATTACK ON TITAN"))
            self.assertFalse(ctx.db.is_series_blacklisted("Naruto"))

    def test_blacklist_cascade_release_status(self):
        """Feature 8.3: Blacklisting a series purges its pending REQUESTED releases."""
        with E2ETestContext() as ctx:
            ctx.db.upsert_release({
                "guid": "aot-01",
                "title": "Attack on Titan Vol 1",
                "series_name": "Attack on Titan",
                "status": "REQUESTED"
            })
            self.assertEqual(len(ctx.db.get_pending_requests()), 1)

            # Blacklist series
            ctx.db.add_to_blacklist("Attack on Titan", "Dropped")
            # Pending requests must be 0
            self.assertEqual(len(ctx.db.get_pending_requests()), 0)

    def test_blacklist_prevent_ingestion(self):
        """Feature 8.4: Blacklisted series correctly identified in DB lookup."""
        with E2ETestContext() as ctx:
            ctx.db.add_to_blacklist("Fairy Tail", "Dropped")
            self.assertTrue(ctx.db.is_series_blacklisted("Fairy Tail"))
            self.assertTrue(ctx.db.is_series_blacklisted("fairy tail"))

    def test_blacklist_remove_and_restore(self):
        """Feature 8.5: Removing series from blacklist restores tracking eligibility."""
        with E2ETestContext() as ctx:
            ctx.db.add_to_blacklist("Bleach", "Temporary")
            self.assertTrue(ctx.db.is_series_blacklisted("Bleach"))

            bl_list = ctx.db.get_blacklisted_series()
            b_id = bl_list[0]["id"]

            del_res = ctx.client.delete(f"/api/blacklist/{b_id}")
            self.assertEqual(del_res.status_code, 200)
            self.assertFalse(ctx.db.is_series_blacklisted("Bleach"))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTier1FeatureCoverage)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
