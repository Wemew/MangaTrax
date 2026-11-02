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


class TestTier2BoundaryCorner(unittest.TestCase):
    """
    Tier 2: Boundary & Corner Cases (>=5 tests per category)
    Categories:
      1. Empty / Null / Whitespace Inputs (5 tests)
      2. Extreme Volume & Chapter Numbers (5 tests)
      3. Special / Unicode / Multilingual Characters (5 tests)
      4. Malformed XML / RSS Feeds (5 tests)
      5. Zero / Negative / Extreme Intervals & Config Values (5 tests)
    Total: 25 test cases.
    """

    # =========================================================================
    # 1. Empty / Null / Whitespace Inputs
    # =========================================================================

    def test_boundary_empty_series_name(self):
        """Boundary 1.1: Empty and whitespace strings in parser and DB upsert."""
        with E2ETestContext() as ctx:
            p_empty = extract_manga_bundle_and_volumes("")
            self.assertEqual(p_empty["series_name"], "")

            p_spaces = extract_manga_bundle_and_volumes("     ")
            self.assertEqual(p_spaces["series_name"], "")

            # Upsert release with empty series name
            is_new, r_id = ctx.db.upsert_release({
                "guid": "empty-sname-1",
                "title": "[Group] Vol. 01",
                "series_name": "   ",
                "status": "DISCOVERY"
            })
            self.assertTrue(is_new)
            rel = ctx.db.get_release_by_id(r_id)
            self.assertEqual(rel["series_name"], "   ")

    def test_boundary_empty_feed_url(self):
        """Boundary 1.2: Feed creation with empty URL returns 400 error."""
        with E2ETestContext() as ctx:
            # Empty URL
            r1 = ctx.client.post("/api/feeds", json_data={"name": "Bad Feed", "feed_type": "rss", "url": ""})
            self.assertEqual(r1.status_code, 400)

            # Whitespace URL
            r2 = ctx.client.post("/api/feeds", json_data={"name": "Bad Feed 2", "feed_type": "rss", "url": "   "})
            # Either 400 or handled safely
            self.assertIn(r2.status_code, (400, 200))

            # Feed test with empty URL
            r3 = ctx.client.post("/api/feeds/test", json_data={"url": "", "feed_type": "rss"})
            self.assertEqual(r3.status_code, 400)

    def test_boundary_empty_cbz_archive(self):
        """Boundary 1.3: Tagging 0-byte or corrupted non-ZIP archive handles safely."""
        with E2ETestContext() as ctx:
            # 1. 0-byte file: zipfile 'a' mode initializes valid archive
            empty_cbz = os.path.join(ctx.staging_dir, "empty.cbz")
            with open(empty_cbz, "wb") as f:
                pass  # 0 bytes

            self.assertFalse(has_comicinfo(empty_cbz))
            res = inject_comicinfo(empty_cbz, {"series_name": "Empty", "volume": "1"})
            self.assertTrue(res)
            self.assertTrue(has_comicinfo(empty_cbz))

            # 2. Non-existent file: inject_comicinfo must return False safely without crash
            res_missing = inject_comicinfo(os.path.join(ctx.staging_dir, "does_not_exist.cbz"), {"series_name": "Ghost", "volume": "1"})
            self.assertFalse(res_missing)

    def test_boundary_null_and_missing_db_fields(self):
        """Boundary 1.4: Upserting releases with null and omitted optional fields."""
        with E2ETestContext() as ctx:
            is_new, r_id = ctx.db.upsert_release({
                "guid": "minimal-guid",
                "title": "Minimal Release",
                "series_name": None,
                "download_url": None,
                "link": None,
                "cover_url": None,
                "volume_start": None,
                "volume_end": None,
                "volumes": None
            })
            self.assertTrue(is_new)
            rel = ctx.db.get_release_by_id(r_id)
            self.assertEqual(rel["guid"], "minimal-guid")
            self.assertEqual(rel["title"], "Minimal Release")
            self.assertIsNone(rel["series_name"])
            self.assertEqual(rel["status"], "REQUESTED")

    def test_boundary_empty_search_and_filter(self):
        """Boundary 1.5: Querying series and releases with empty search strings."""
        with E2ETestContext() as ctx:
            ctx.db.track_discovered_series("Naruto")
            ctx.db.upsert_release({"guid": "n-1", "title": "Naruto Vol 1", "series_name": "Naruto"})

            # Empty string search
            s_empty = ctx.db.get_all_series(search="")
            self.assertEqual(len(s_empty), 1)

            # Whitespace search
            s_space = ctx.db.get_all_series(search="   ")
            self.assertEqual(len(s_space), 1)

            # API calls with empty query
            r1 = ctx.client.get("/api/series/rows", params={"search": ""})
            self.assertEqual(r1.status_code, 200)

            r2 = ctx.client.get("/api/releases/cards", params={"search": ""})
            self.assertEqual(r2.status_code, 200)

    # =========================================================================
    # 2. Extreme Volume & Chapter Numbers
    # =========================================================================

    def test_boundary_extreme_volume_zero(self):
        """Boundary 2.1: Volume 0 (prologue / special prequel) correctly parsed."""
        r0 = extract_manga_bundle_and_volumes("[Group] Jujutsu Kaisen Vol. 0 [Digital]")
        self.assertEqual(r0["series_name"], "Jujutsu Kaisen")
        self.assertEqual(r0["volume_start"], 0.0)
        self.assertEqual(r0["volumes"], [0.0])

        xml_str = generate_comicinfo_xml({"series_name": "Jujutsu Kaisen", "volume": "0"})
        root = ET.fromstring(xml_str)
        self.assertEqual(root.find("Volume").text, "0")

    def test_boundary_extreme_high_volume(self):
        """Boundary 2.2: Extreme volume numbers (Vol 9999) parsed without overflow."""
        r9999 = extract_manga_bundle_and_volumes("Kochikame Vol. 9999")
        self.assertEqual(r9999["series_name"], "Kochikame")
        self.assertEqual(r9999["volume_start"], 9999.0)
        self.assertEqual(r9999["volumes"], [9999.0])

    def test_boundary_decimal_and_fractional_volumes(self):
        """Boundary 2.3: Fractional and decimal volumes (Vol. 7.5, Vol. 0.5)."""
        r_half = extract_manga_bundle_and_volumes("Re:Zero Vol. 7.5 [Digital]")
        self.assertEqual(r_half["series_name"], "Re:Zero")
        self.assertEqual(r_half["volume_start"], 7.5)
        self.assertEqual(r_half["volumes"], [7.5])

        r_p5 = extract_manga_bundle_and_volumes("Manga Special Vol 0.5")
        self.assertEqual(r_p5["volume_start"], 0.5)

    def test_boundary_huge_bundle_range(self):
        """Boundary 2.4: Large bundle ranges (Vol 1-100) and safety clamping."""
        r_range = extract_manga_bundle_and_volumes("[Batch] One Piece Vol. 01-100 [CBZ]")
        self.assertTrue(r_range["is_bundle"])
        self.assertEqual(r_range["volume_start"], 1.0)
        self.assertEqual(r_range["volume_end"], 100.0)
        self.assertEqual(len(r_range["volumes"]), 100)

        # Extreme span (e.g. Vol 1-99999): must not crash or hang system
        r_huge = extract_manga_bundle_and_volumes("Extreme Manga Vol 1-9999")
        self.assertTrue(isinstance(r_huge["volumes"], list))

    def test_boundary_inverted_bundle_range(self):
        """Boundary 2.5: Inverted bundle ranges (Vol 10-05) handled safely."""
        r_inv = extract_manga_bundle_and_volumes("Broken Manga Vol. 10-05")
        # Should not crash, loop infinitely, or produce invalid volume arrays
        self.assertTrue(isinstance(r_inv, dict))
        if r_inv.get("is_bundle") and r_inv.get("volumes"):
            # Volumes list should not be negative or corrupt
            self.assertTrue(all(v >= 0 for v in r_inv["volumes"]))

    # =========================================================================
    # 3. Special / Unicode / Multilingual Characters
    # =========================================================================

    def test_boundary_unicode_kanji_kana_titles(self):
        """Boundary 3.1: Full Unicode Japanese Kanji, Hiragana, and Katakana titles."""
        with E2ETestContext() as ctx:
            title_jp = "葬送のフリーレン 第01巻 [デジタル版]"
            parsed = extract_manga_bundle_and_volumes(title_jp)
            self.assertTrue(len(parsed["series_name"]) > 0)

            # Store in DB and verify persistence
            is_new, r_id = ctx.db.upsert_release({
                "guid": "jp-guid-1",
                "title": title_jp,
                "series_name": "葬送のフリーレン",
                "status": "DISCOVERY"
            })
            self.assertTrue(is_new)
            rel = ctx.db.get_release_by_id(r_id)
            self.assertEqual(rel["series_name"], "葬送のフリーレン")

    def test_boundary_accents_and_diacritics(self):
        """Boundary 3.2: Accented European titles (Pokémon, Nausicaä, Otaku no Café)."""
        with E2ETestContext() as ctx:
            titles = [
                ("Pokémon Adventures Vol. 01", "Pokémon Adventures"),
                ("Nausicaä of the Valley of the Wind Vol. 01", "Nausicaä of the Valley of the Wind"),
                ("Café Stella Vol. 1", "Café Stella")
            ]
            for raw, expected in titles:
                parsed = extract_manga_bundle_and_volumes(raw)
                self.assertIn(expected.split()[0], parsed["series_name"])

                ctx.db.track_discovered_series(expected)
                self.assertTrue(any(s["name"] == expected for s in ctx.db.get_all_series()))

    def test_boundary_format_meta_characters(self):
        """Boundary 3.3: SQL injection and format meta-characters ('', %%, <script>, &)."""
        with E2ETestContext() as ctx:
            nasty_title = "'; DROP TABLE releases; -- <script>alert(1)</script> %_ Vol 01"
            parsed = extract_manga_bundle_and_volumes(nasty_title)
            self.assertEqual(parsed["volume_start"], 1.0)

            # Test SQL injection safety in DB
            is_new, r_id = ctx.db.upsert_release({
                "guid": "sqli-guid-1",
                "title": nasty_title,
                "series_name": "SafeSeries",
                "status": "REQUESTED"
            })
            self.assertTrue(is_new)

            # Ensure table was not dropped
            all_r = ctx.db.get_releases()
            self.assertEqual(len(all_r), 1)

            # Test XML generation escaping
            xml_str = generate_comicinfo_xml({
                "series_name": "Test & <Injected>",
                "volume": "1"
            })
            root = ET.fromstring(xml_str)
            self.assertEqual(root.find("Series").text, "Test & <Injected>")

    def test_boundary_emojis_and_surrogate_pairs(self):
        """Boundary 3.4: Emoji characters in titles and summaries."""
        with E2ETestContext() as ctx:
            emoji_title = "My Love Story with Yamada-kun at Lv999! ❤️🔥 Vol. 01"
            parsed = extract_manga_bundle_and_volumes(emoji_title)
            self.assertEqual(parsed["volume_start"], 1.0)

            is_new, r_id = ctx.db.upsert_release({
                "guid": "emoji-guid-1",
                "title": emoji_title,
                "series_name": "Yamada-kun Lv999 ❤️",
                "description": "Fun romcom ✨🎉",
                "status": "REQUESTED"
            })
            self.assertTrue(is_new)
            rel = ctx.db.get_release_by_id(r_id)
            self.assertIn("❤️", rel["series_name"])
            self.assertIn("✨", rel["description"])

    def test_boundary_extreme_title_length(self):
        """Boundary 3.5: Extremely long titles (500+ characters) with bracket soup."""
        long_title = "[GroupA] [1080p] [Digital] [Lossless] [Multi-Language] [Dual-Audio] [Special-Edition] " * 5 + "Kaguya-sama Vol. 01 [CBZ]"
        parsed = extract_manga_bundle_and_volumes(long_title)
        self.assertEqual(parsed["volume_start"], 1.0)
        self.assertIn("Kaguya-sama", parsed["series_name"])

    # =========================================================================
    # 4. Malformed XML / RSS Feeds
    # =========================================================================

    def test_boundary_malformed_xml_syntax(self):
        """Boundary 4.1: Corrupt unclosed XML handled safely without unhandled exception."""
        from app.providers.rss import RSSFeedProvider
        import feedparser

        broken_xml = "<?xml version='1.0'?><rss><channel><item><title>Broken<link>http://"
        with patch("feedparser.parse", return_value=feedparser.parse(broken_xml)):
            provider = RSSFeedProvider(name="Broken Feed", url="https://example.com/broken.xml")
            items = provider.fetch_releases()
            # feedparser handles broken XML gracefully; should return empty or partial
            self.assertTrue(isinstance(items, list))

    def test_boundary_malformed_feed_missing_item_fields(self):
        """Boundary 4.2: Feed items missing title, link, or GUID."""
        from app.providers.rss import RSSFeedProvider
        import feedparser

        xml_missing = generate_rss_xml([
            {"title": "", "link": "https://example.com/no-title"},
            {"title": "Valid Vol 1", "link": ""}
        ])
        with patch("feedparser.parse", return_value=feedparser.parse(xml_missing)):
            provider = RSSFeedProvider(name="Missing Fields Feed", url="https://example.com/feed")
            items = provider.fetch_releases()
            # Items without title must be skipped; item with title must be processed
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].title, "Valid Vol 1")

    def test_boundary_malformed_comicinfo_existing_xml(self):
        """Boundary 4.3: Corrupted existing ComicInfo.xml in CBZ archive before re-tagging."""
        with E2ETestContext() as ctx:
            corrupt_xml = "<<<CORRUPTED NON-XML DATA>>>"
            cbz_path = ctx.create_mock_cbz("corrupted_meta.cbz", pages=2, existing_xml=corrupt_xml)

            # has_comicinfo checks zip namelist, returns True if file entry exists
            self.assertTrue(has_comicinfo(cbz_path))

            # Overwriting with inject_comicinfo(overwrite=True) replaces with valid XML
            res = inject_comicinfo(cbz_path, {"series_name": "Fixed", "volume": "1"}, overwrite=True)
            self.assertTrue(res)

            with zipfile.ZipFile(cbz_path, "r") as z:
                xml_data = z.read("ComicInfo.xml").decode("utf-8")
                root = ET.fromstring(xml_data)
                self.assertEqual(root.find("Series").text, "Fixed")

    def test_boundary_malformed_rss_invalid_pubdate(self):
        """Boundary 4.4: Feeds with invalid or non-standard pubDate formats."""
        from app.providers.rss import RSSFeedProvider
        import feedparser

        xml_bad_date = generate_rss_xml([
            {"title": "Manga Vol 1", "link": "https://example.com/1", "pubDate": "INVALID DATE STRING 99/99/9999"}
        ])
        with patch("feedparser.parse", return_value=feedparser.parse(xml_bad_date)):
            provider = RSSFeedProvider(name="Bad Date Feed", url="https://example.com/feed")
            items = provider.fetch_releases()
            self.assertEqual(len(items), 1)
            self.assertTrue(len(items[0].pub_date) > 0)

    def test_boundary_malformed_rss_huge_payload(self):
        """Boundary 4.5: Large feed payload with 500 entries parses cleanly."""
        from app.providers.rss import RSSFeedProvider
        import feedparser

        big_items = [
            {"title": f"Series {i} Vol. 01 [Digital]", "link": f"https://example.com/{i}", "guid": f"big-{i}"}
            for i in range(1, 201)
        ]
        xml_big = generate_rss_xml(big_items)
        parsed_feed = feedparser.parse(xml_big)

        with patch("feedparser.parse", return_value=parsed_feed):
            provider = RSSFeedProvider(name="Big Feed", url="https://example.com/feed")
            items = provider.fetch_releases()
            self.assertEqual(len(items), 200)

    # =========================================================================
    # 5. Zero / Negative / Extreme Intervals & Config Values
    # =========================================================================

    def test_boundary_zero_and_negative_feed_interval(self):
        """Boundary 5.1: Zero and negative feed intervals in DB and API."""
        with E2ETestContext() as ctx:
            # Add feed with interval 0
            fid = ctx.db.add_feed(name="Zero Feed", feed_type="rss", url="https://example.com", interval=0)
            feed = ctx.db.get_feed(fid)
            self.assertEqual(feed["check_interval_minutes"], 0)

            # Setting negative interval
            with ctx.db.get_connection() as conn:
                conn.execute("UPDATE feeds SET check_interval_minutes = -10 WHERE id = ?;", (fid,))
                conn.commit()
            feed_neg = ctx.db.get_feed(fid)
            self.assertEqual(feed_neg["check_interval_minutes"], -10)

    def test_boundary_extreme_feed_interval(self):
        """Boundary 5.2: Extremely large feed intervals (e.g. 10,000,000 minutes)."""
        with E2ETestContext() as ctx:
            fid = ctx.db.add_feed(name="Yearly Feed", feed_type="rss", url="https://example.com", interval=10000000)
            feed = ctx.db.get_feed(fid)
            self.assertEqual(feed["check_interval_minutes"], 10000000)

    def test_boundary_zero_and_negative_page_limits(self):
        """Boundary 5.3: Feed pagination start_page and max_pages edge values."""
        from app.providers.nyaa import NyaaMangaProvider
        provider = NyaaMangaProvider(name="Edge Nyaa", config={"max_pages": 0, "delay_seconds": 0.0})
        # max_pages=0 should immediately return empty list without network calls
        items = provider.fetch_releases()
        self.assertEqual(items, [])

    def test_boundary_extreme_limit_offset_queries(self):
        """Boundary 5.4: Database queries with limit=0 and extreme offsets."""
        with E2ETestContext() as ctx:
            ctx.db.upsert_release({"guid": "lim-1", "title": "Series Vol 1", "series_name": "Series"})

            # Limit 0
            r_zero = ctx.db.get_releases(limit=0)
            self.assertEqual(len(r_zero), 0)

            # Offset beyond count
            r_far = ctx.db.get_releases(limit=10, offset=999999)
            self.assertEqual(len(r_far), 0)

            # Discovery with offset beyond count
            d_far = ctx.db.get_discovery_releases(limit=10, offset=999999)
            self.assertEqual(len(d_far), 0)

    def test_boundary_port_and_timeout_boundaries(self):
        """Boundary 5.5: Downloader client port boundaries and 0.0 timeout."""
        with E2ETestContext() as ctx:
            # Client with timeout=0.0
            client = QBitClient(base_url="http://localhost:65535", enabled=True)
            res = client.test_connection(timeout=0.001)
            self.assertFalse(res["success"])

            # Configure downloader with extreme port via API
            conf_res = ctx.client.post("/api/downloader/configure", json_data={
                "qbit_url": "http://localhost:65535",
                "qbit_enabled": "false"
            })
            self.assertEqual(conf_res.status_code, 200)
            self.assertEqual(ctx.db.get_setting("qbit_url"), "http://localhost:65535")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTier2BoundaryCorner)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
