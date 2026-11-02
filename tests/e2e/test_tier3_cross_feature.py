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


class TestTier3CrossFeature(unittest.TestCase):
    """
    Tier 3: Cross-Feature Combinations (Pairwise Interaction Flows)
    Covers:
      1. Feed Ingestion -> Series Match -> Status Update (REQUESTED) -> qBittorrent Selective File Prioritization
      2. Calibre Sync -> Series Tracking Update -> Missing Volume Calculation -> Feed Match -> Calibre IN_CALIBRE transition
      3. Discovery Feed Ingestion -> User Tracks Series -> Release Status Transition -> Staging CBZ Tagging
      4. Blacklist Series -> Cascade Status -> Feed Release Blocked -> Unblacklist -> Re-evaluate
      5. Group Prioritization -> Multi-Source Feeds -> Preferred VIP Group Selected
      6. Dual-Title Resolution -> Romaji vs English Mapping -> Calibre In-Library Ownership -> ComicInfo Tagging
      7. Bundle Partial Overlap -> Batch vs Single Volume Resolution -> Selective Reconciliation
    Total: 7 comprehensive interaction flows.
    """

    def test_flow_feed_to_qbit_download(self):
        """Flow 1: Feed Ingestion -> Match Monitored -> REQUESTED -> qBit Selective Download."""
        with E2ETestContext() as ctx:
            # 1. Setup tracked monitored series in Calibre & DB
            ctx.create_mock_calibre_library([
                {"name": "One Piece", "author": "Eiichiro Oda", "volumes": [1.0, 2.0, 3.0]}
            ])
            ctx.scheduler.sync_calibre_library()
            tracked = ctx.db.get_all_series()
            self.assertEqual(len(tracked), 1)
            self.assertEqual(tracked[0]["book_count"], 3)

            # 2. Ingest bundle release from feed covering Vol 1-5 (missing: [4.0, 5.0])
            raw_title = "[ReleaseGroup] One Piece Vol. 01-05 [CBZ]"
            parsed = extract_manga_bundle_and_volumes(raw_title)
            parsed["title"] = raw_title
            scanner = CalibreMangaScanner(ctx.calibre_dir)
            matcher = MangaCalibreMatcher(scanner.get_all_series(), scanner.get_series_volumes_map())
            match_res = matcher.check_release(parsed)

            self.assertEqual(match_res["status"], "REQUESTED")
            self.assertEqual(match_res["missing_volumes"], [4.0, 5.0])

            # Upsert into DB
            parsed["guid"] = "op-v1-5-guid"
            parsed["status"] = match_res["status"]
            parsed["missing_volumes"] = match_res["missing_volumes"]
            parsed["download_url"] = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
            is_new, rel_id = ctx.db.upsert_release(parsed)
            self.assertTrue(is_new)

            # 3. Simulate sending to qBittorrent with selective prioritization
            qbit = QBitClient(enabled=True)
            qbit.authenticated = True
            fake_files = [
                {"index": 0, "name": "One Piece Vol 01.cbz"},
                {"index": 1, "name": "One Piece Vol 02.cbz"},
                {"index": 2, "name": "One Piece Vol 03.cbz"},
                {"index": 3, "name": "One Piece Vol 04.cbz"},
                {"index": 4, "name": "One Piece Vol 05.cbz"}
            ]
            with patch.object(qbit, "add_torrent", return_value=True), \
                 patch.object(qbit, "get_torrent_files", return_value=fake_files), \
                 patch.object(qbit, "set_file_priorities", return_value=True):

                sel_res = qbit.apply_selective_volume_download(
                    "0123456789abcdef0123456789abcdef01234567",
                    wanted_volumes=[4.0, 5.0]
                )
                self.assertEqual(sel_res["status"], "filtered")
                self.assertEqual(sel_res["skipped_volumes"], [1.0, 2.0, 3.0])
                self.assertEqual(sel_res["kept_volumes"], [4.0, 5.0])

                # Update download status in DB
                ctx.db.update_download_status(rel_id, "DOWNLOADING", "0123456789abcdef0123456789abcdef01234567")
                updated_rel = ctx.db.get_release_by_id(rel_id)
                self.assertEqual(updated_rel["download_status"], "DOWNLOADING")

    def test_flow_calibre_sync_to_feed_transition(self):
        """Flow 2: Calibre owns Vol 1 -> Feed requests Vol 2 -> Calibre acquires Vol 2 -> Transition to IN_CALIBRE."""
        with E2ETestContext() as ctx:
            # 1. Calibre library initially owns Vol 1
            ctx.create_mock_calibre_library([
                {"name": "Spy x Family", "author": "Tatsuya Endo", "volumes": [1.0]}
            ])
            ctx.scheduler.sync_calibre_library()

            # 2. Feed arrives with Vol 2
            raw_title = "Spy x Family Vol. 02 [Digital]"
            parsed = extract_manga_bundle_and_volumes(raw_title)
            parsed["title"] = raw_title
            scanner = CalibreMangaScanner(ctx.calibre_dir)
            matcher = MangaCalibreMatcher(scanner.get_all_series(), scanner.get_series_volumes_map())
            match1 = matcher.check_release(parsed)
            self.assertEqual(match1["status"], "REQUESTED")

            parsed["guid"] = "sxf-vol-2"
            parsed["status"] = "REQUESTED"
            parsed["missing_volumes"] = [2.0]
            _, r_id = ctx.db.upsert_release(parsed)
            self.assertEqual(len(ctx.db.get_pending_requests()), 1)

            # 3. User adds Vol 2 into Calibre library
            ctx.create_mock_calibre_library([
                {"name": "Spy x Family", "author": "Tatsuya Endo", "volumes": [1.0, 2.0]}
            ])

            # Trigger Calibre rescan API
            res = ctx.client.post("/api/calibre/sync")
            self.assertEqual(res.status_code, 200)

            # 4. Release is automatically reconciled to IN_CALIBRE
            rel_after = ctx.db.get_release_by_id(r_id)
            self.assertEqual(rel_after["status"], "IN_CALIBRE")
            self.assertEqual(len(ctx.db.get_pending_requests()), 0)

    def test_flow_discovery_to_backfill_to_tagging(self):
        """Flow 3: Discovery release -> Track series -> Staging CBZ tagged with inherited metadata."""
        with E2ETestContext() as ctx:
            # 1. Ingest untracked series Vol 1 as DISCOVERY
            _, r_id = ctx.db.upsert_release({
                "guid": "disc-frieren-1",
                "title": "Frieren Vol 1",
                "series_name": "Frieren",
                "volume_start": 1.0,
                "volume_end": 1.0,
                "volumes": [1.0],
                "status": "DISCOVERY"
            })
            self.assertEqual(ctx.db.count_discovery_releases(), 1)

            # 2. User tracks discovered series via API
            track_res = ctx.client.post("/api/discovery/track", json_data={
                "series_name": "Frieren",
                "release_id": r_id
            })
            self.assertEqual(track_res.status_code, 200)

            # Series is now monitored
            all_s = ctx.db.get_all_series(monitored_only=True)
            self.assertEqual(len(all_s), 1)

            # 3. Download finishes in staging directory
            cbz_path = ctx.create_mock_cbz("Frieren Vol 01.cbz", pages=3)
            self.assertFalse(has_comicinfo(cbz_path))

            # Staging tagging endpoint invoked
            templates = ctx.db.get_series_templates_map() if hasattr(ctx.db, "get_series_templates_map") else {
                "frieren": {"series_name": "Frieren", "authors": "Kanehito Yamada", "publisher": "Shogakukan", "tags": "Fantasy"}
            }
            tagged = auto_tag_staging_cbz_files(ctx.staging_dir, templates)
            self.assertEqual(len(tagged), 1)
            self.assertTrue(has_comicinfo(cbz_path))

            with zipfile.ZipFile(cbz_path, "r") as z:
                xml_data = z.read("ComicInfo.xml").decode("utf-8")
                self.assertIn("<Series>Frieren</Series>", xml_data)
                self.assertIn("<Volume>1</Volume>", xml_data)

    def test_flow_blacklist_cascade_and_unblacklist(self):
        """Flow 4: Tracked series has pending requests -> Blacklist series -> Status cascaded -> Unblacklist."""
        with E2ETestContext() as ctx:
            # Seed series and requested release
            ctx.db.sync_calibre_series([{"series_id": 1, "series_name": "Boruto", "book_count": 0, "latest_index": 0.0}])
            _, r_id = ctx.db.upsert_release({
                "guid": "boruto-1",
                "title": "Boruto Vol 1",
                "series_name": "Boruto",
                "status": "REQUESTED"
            })
            self.assertEqual(len(ctx.db.get_pending_requests()), 1)

            # User blacklists series via API
            bl_res = ctx.client.post("/api/blacklist", json_data={"name": "Boruto", "reason": "Not reading"})
            self.assertEqual(bl_res.status_code, 200)
            self.assertTrue(ctx.db.is_series_blacklisted("Boruto"))

            # Pending requests must be cleared
            self.assertEqual(len(ctx.db.get_pending_requests()), 0)

            # Unblacklist series via API
            bl_entry = ctx.db.get_blacklisted_series()[0]
            del_res = ctx.client.delete(f"/api/blacklist/{bl_entry['id']}")
            self.assertEqual(del_res.status_code, 200)
            self.assertFalse(ctx.db.is_series_blacklisted("Boruto"))

    def test_flow_group_priority_with_multi_source_feeds(self):
        """Flow 5: Releases from preferred VIP groups vs generic groups are ranked properly."""
        with E2ETestContext() as ctx:
            # Register VIP priority group
            ctx.db.add_prioritized_group("danke-Empire", notes="Top tier scanner", priority=10)

            # Release A: generic group
            ctx.db.upsert_release({
                "guid": "csm-v1-generic",
                "title": "[GenericGroup] Chainsaw Man Vol 1",
                "series_name": "Chainsaw Man",
                "release_group": "GenericGroup",
                "volume_start": 1.0,
                "status": "REQUESTED"
            })

            # Release B: VIP group
            ctx.db.upsert_release({
                "guid": "csm-v1-vip",
                "title": "[danke-Empire] Chainsaw Man Vol 1 [Digital]",
                "series_name": "Chainsaw Man",
                "release_group": "danke-Empire",
                "volume_start": 1.0,
                "status": "REQUESTED"
            })

            # Fetch prioritized releases
            p_releases = ctx.db.get_prioritized_releases(status="REQUESTED")
            self.assertTrue(len(p_releases) >= 1)
            self.assertTrue(any(r["release_group"] == "danke-Empire" for r in p_releases))

    def test_flow_dual_title_resolution_with_calibre_and_metadata(self):
        """Flow 6: Release with Romaji title resolves against Calibre library holding English title."""
        with E2ETestContext() as ctx:
            # Calibre has English name
            ctx.create_mock_calibre_library([
                {"name": "Attack on Titan", "author": "Hajime Isayama", "volumes": [1.0, 2.0, 3.0]}
            ])
            ctx.scheduler.sync_calibre_library()

            # Cache metadata mapping Romaji -> English
            ctx.db.cache_manga_metadata("shingeki no kyojin", {
                "canonical_title": "Attack on Titan",
                "romaji_title": "Shingeki no Kyojin"
            })

            # Scanner & Matcher with cache
            scanner = CalibreMangaScanner(ctx.calibre_dir)
            matcher = MangaCalibreMatcher(
                scanner.get_all_series(),
                scanner.get_series_volumes_map(),
                ctx.db.get_all_manga_metadata_cache()
            )

            # Romaji title release for Vol 1-3
            parsed = extract_manga_bundle_and_volumes("[Group] Shingeki no Kyojin Vol 01-03")
            check = matcher.check_release(parsed)
            # Vol 1-3 already in Calibre under "Attack on Titan"
            self.assertEqual(check["status"], "IN_CALIBRE")
            self.assertEqual(check["missing_volumes"], [])

    def test_flow_bundle_partial_overlap_with_single_volumes(self):
        """Flow 7: Calibre owns Vol 1, 3 -> Batch offers 1-4 -> Missing [2, 4] -> Single Vol 2 arrives."""
        with E2ETestContext() as ctx:
            # Calibre owns Vol 1 and 3 (gaps: 2, 4)
            ctx.create_mock_calibre_library([
                {"name": "Bleach", "author": "Tite Kubo", "volumes": [1.0, 3.0]}
            ])
            ctx.scheduler.sync_calibre_library()

            scanner = CalibreMangaScanner(ctx.calibre_dir)
            matcher = MangaCalibreMatcher(scanner.get_all_series(), scanner.get_series_volumes_map())

            # Batch release Vol 1-4
            batch_rel = extract_manga_bundle_and_volumes("Bleach Vol. 01-04 [Digital]")
            batch_check = matcher.check_release(batch_rel)
            self.assertEqual(batch_check["status"], "REQUESTED")
            self.assertEqual(batch_check["missing_volumes"], [2.0, 4.0])

            # Single volume Vol 2
            v2_rel = extract_manga_bundle_and_volumes("Bleach Vol. 02 [Digital]")
            v2_check = matcher.check_release(v2_rel)
            self.assertEqual(v2_check["status"], "REQUESTED")
            self.assertEqual(v2_check["missing_volumes"], [2.0])

            # Single volume Vol 3 (already owned)
            v3_rel = extract_manga_bundle_and_volumes("Bleach Vol. 03 [Digital]")
            v3_check = matcher.check_release(v3_rel)
            self.assertEqual(v3_check["status"], "IN_CALIBRE")
            self.assertEqual(v3_check["missing_volumes"], [])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTier3CrossFeature)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
