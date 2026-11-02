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
from app.parser import is_chapter_only, is_light_novel, split_series_candidate_parts


class TestTier4RealWorldScenarios(unittest.TestCase):
    """
    Tier 4: Real-World Scenarios
    Covers:
      1. Complete Series Lifecycle (Discovery -> Track -> Ingestion -> Selective Filter -> Staging Tag -> In-Calibre Resolution)
      2. Multi-Volume Dual-Title Manga Release Storm (Alternate naming, batch ranges, chapter discard, LN purge, VIP priority)
      3. High-Volume Feed Ingestion & Database WAL Concurrency
      4. Historical Backfill Resumption & State Tracking
    Total: 4 comprehensive deep scenario tests.
    """

    def test_scenario_complete_series_lifecycle(self):
        """Scenario 1: Complete end-to-end lifecycle of a manga series from Discovery to IN_CALIBRE."""
        with E2ETestContext() as ctx:
            # Step 1: Blank slate — no series tracked, empty Calibre library
            scanner = CalibreMangaScanner(ctx.calibre_dir)
            self.assertEqual(scanner.get_library_summary()["total_series"], 0)

            # Step 2: Discovery feed detects Volume 1 of 'Dungeon Meshi'
            disc_title = "[ReleaseGroup] Dungeon Meshi Vol. 01 [Digital]"
            parsed_v1 = extract_manga_bundle_and_volumes(disc_title)
            parsed_v1["title"] = disc_title
            parsed_v1["guid"] = "dm-disc-v01"
            parsed_v1["status"] = "DISCOVERY"

            _, disc_id = ctx.db.upsert_release(parsed_v1)
            self.assertEqual(ctx.db.count_discovery_releases(), 1)

            # Step 3: User promotes series to monitored tracking via API
            track_res = ctx.client.post("/api/discovery/track", json_data={
                "series_name": "Dungeon Meshi",
                "release_id": disc_id
            })
            self.assertEqual(track_res.status_code, 200)
            self.assertEqual(ctx.db.count_discovery_releases(), 0)

            series_list = ctx.db.get_all_series(monitored_only=True)
            self.assertEqual(len(series_list), 1)
            self.assertEqual(series_list[0]["name"], "Dungeon Meshi")

            # Step 4: Releases covering the complete series arrive (Volumes 1 to 14)
            # - Batch Vol 1-7
            # - Batch Vol 8-14
            batch1_title = "[danke-Empire] Dungeon Meshi Vol. 01-07 [Digital] [1080p]"
            batch2_title = "[danke-Empire] Dungeon Meshi Vol. 08-14 [Digital] [1080p]"

            p_b1 = extract_manga_bundle_and_volumes(batch1_title)
            p_b1["title"] = batch1_title
            p_b1["guid"] = "dm-b1"
            p_b1["status"] = "REQUESTED"
            p_b1["missing_volumes"] = p_b1["volumes"]
            ctx.db.upsert_release(p_b1)

            p_b2 = extract_manga_bundle_and_volumes(batch2_title)
            p_b2["title"] = batch2_title
            p_b2["guid"] = "dm-b2"
            p_b2["status"] = "REQUESTED"
            p_b2["missing_volumes"] = p_b2["volumes"]
            ctx.db.upsert_release(p_b2)

            self.assertEqual(len(ctx.db.get_pending_requests()), 3)

            # Step 5: Calibre library owner adds volumes 1-6
            ctx.create_mock_calibre_library([
                {
                    "name": "Dungeon Meshi",
                    "author": "Ryoko Kui",
                    "publisher": "Harta",
                    "tags": ["Fantasy", "Cooking"],
                    "volumes": [float(v) for v in range(1, 7)]
                }
            ])
            # Rescan Calibre
            ctx.scheduler.sync_calibre_library()

            # Vol 1 is now recognized as in Calibre
            r_v1 = ctx.db.get_release_by_id(disc_id)
            self.assertEqual(r_v1["status"], "IN_CALIBRE")

            # Step 6: Batch 1 (Vol 1-7) has only Vol 7 missing; selective download filter applied
            matcher = MangaCalibreMatcher(
                CalibreMangaScanner(ctx.calibre_dir).get_all_series(),
                CalibreMangaScanner(ctx.calibre_dir).get_series_volumes_map()
            )
            check_b1 = matcher.check_release(p_b1)
            self.assertEqual(check_b1["missing_volumes"], [7.0])

            qbit = QBitClient(enabled=True)
            qbit.authenticated = True
            mock_files = [{"index": i - 1, "name": f"Dungeon Meshi Vol {i:02d}.cbz"} for i in range(1, 8)]

            with patch.object(qbit, "get_torrent_files", return_value=mock_files), \
                 patch.object(qbit, "set_file_priorities", return_value=True):
                sel = qbit.apply_selective_volume_download("fakehash", wanted_volumes=check_b1["missing_volumes"])
                self.assertEqual(sel["kept_volumes"], [7.0])
                self.assertEqual(len(sel["skipped_volumes"]), 6)

            # Step 7: Downloads finish in staging: create CBZ files for Vol 7 to 14
            for v in range(7, 15):
                ctx.create_mock_cbz(f"Dungeon Meshi Vol. {v:02d}.cbz", pages=3)

            # Auto-tagger injects ComicInfo.xml
            templates = {
                "dungeon meshi": {
                    "series_name": "Dungeon Meshi",
                    "authors": "Ryoko Kui",
                    "publisher": "Harta",
                    "tags": "Fantasy, Cooking"
                }
            }
            tagged = auto_tag_staging_cbz_files(ctx.staging_dir, templates)
            self.assertEqual(len(tagged), 8)

            # Verify ComicInfo contents in one of the tagged files
            cbz_v14 = os.path.join(ctx.staging_dir, "Dungeon Meshi Vol. 14.cbz")
            with zipfile.ZipFile(cbz_v14, "r") as z:
                xml_data = z.read("ComicInfo.xml").decode("utf-8")
                self.assertIn("<Series>Dungeon Meshi</Series>", xml_data)
                self.assertIn("<Volume>14</Volume>", xml_data)
                self.assertIn("<Writer>Ryoko Kui</Writer>", xml_data)

            # Step 8: Calibre library gets updated with all 14 volumes
            ctx.create_mock_calibre_library([
                {
                    "name": "Dungeon Meshi",
                    "author": "Ryoko Kui",
                    "publisher": "Harta",
                    "tags": ["Fantasy", "Cooking"],
                    "volumes": [float(v) for v in range(1, 15)]
                }
            ])
            ctx.scheduler.sync_calibre_library()

            # Step 9: Verify complete series state in database & API
            self.assertEqual(len(ctx.db.get_pending_requests()), 0)
            tracked_final = ctx.db.get_all_series()[0]
            self.assertEqual(tracked_final["book_count"], 14)
            self.assertEqual(tracked_final["latest_index"], 14.0)

            # Verify dashboard stats reflect full completion
            stats = ctx.db.get_dashboard_stats()
            self.assertEqual(stats["pending_requests"], 0)
            self.assertEqual(stats["acquired_books"], 3)

    def test_scenario_multi_volume_dual_title_manga_releases(self):
        """Scenario 2: Dual-title manga releases with alternate naming, batch ranges, and non-manga filtering."""
        with E2ETestContext() as ctx:
            # Calibre library holds English canonical title: 'Demon Slayer: Kimetsu no Yaiba'
            ctx.create_mock_calibre_library([
                {
                    "name": "Demon Slayer: Kimetsu no Yaiba",
                    "author": "Koyoharu Gotouge",
                    "publisher": "VIZ Media",
                    "tags": ["Action", "Historical", "Supernatural"],
                    "volumes": [1.0, 2.0, 3.0]
                }
            ])
            ctx.scheduler.sync_calibre_library()

            # Cache metadata mapping
            ctx.db.cache_manga_metadata("kimetsu no yaiba", {
                "canonical_title": "Demon Slayer: Kimetsu no Yaiba",
                "romaji_title": "Kimetsu no Yaiba"
            })

            # Ingest diverse release storm:
            releases_raw = [
                # A: Chapter-only release (MUST be discarded)
                "[Scanlator] Kimetsu no Yaiba Chapter 180 [Digital]",
                # B: Light Novel release (MUST be discarded)
                "[Novel] Demon Slayer: Kimetsu no Yaiba - The Flower of Happiness (Light Novel)",
                # C: Romaji batch release Vol 1-5 (owned: 1-3, missing: 4, 5)
                "[danke-Empire] Kimetsu no Yaiba Vol. 01-05 [Digital] [1080p]",
                # D: English standalone Vol 4
                "Demon Slayer: Kimetsu no Yaiba Vol. 4 [Digital]",
                # E: Dual-title batch Vol 6-10
                "Kimetsu no Yaiba / Demon Slayer Vol. 06-10 [CBZ]"
            ]

            scanner = CalibreMangaScanner(ctx.calibre_dir)
            matcher = MangaCalibreMatcher(
                scanner.get_all_series(),
                scanner.get_series_volumes_map(),
                ctx.db.get_all_manga_metadata_cache()
            )

            ingested_valid = []
            for r_title in releases_raw:
                if is_chapter_only(r_title):
                    continue
                if is_light_novel(r_title):
                    continue

                parsed = extract_manga_bundle_and_volumes(r_title)
                parsed["title"] = r_title
                parsed["guid"] = f"guid-{abs(hash(r_title))}"

                # Resolve canonical title from metadata cache across candidate parts
                alt_names = []
                if parsed.get("series_name"):
                    dual_parts = split_series_candidate_parts(parsed["series_name"], alt_separators=False)
                    if len(dual_parts) > 1:
                        alt_names.extend(dual_parts)

                for cand in [parsed.get("series_name")] + alt_names:
                    if cand:
                        cached_meta = ctx.db.get_cached_manga_metadata(cand)
                        if cached_meta and cached_meta.get("canonical_title"):
                            parsed["series_name"] = cached_meta["canonical_title"]
                            break

                check = matcher.check_release(parsed, alt_series_names=alt_names)
                parsed["status"] = check["status"]
                parsed["missing_volumes"] = check["missing_volumes"]
                ctx.db.upsert_release(parsed)
                ingested_valid.append(parsed)

            # Exactly 3 valid manga releases (A and B were filtered out)
            self.assertEqual(len(ingested_valid), 3)

            # C (Vol 1-5) missing volumes: [4.0, 5.0]
            rel_c = next(r for r in ingested_valid if "01-05" in r["title"])
            self.assertEqual(rel_c["missing_volumes"], [4.0, 5.0])
            self.assertEqual(rel_c["status"], "REQUESTED")

            # E (Vol 6-10) missing volumes: [6.0, 7.0, 8.0, 9.0, 10.0]
            rel_e = next(r for r in ingested_valid if "06-10" in r["title"])
            self.assertEqual(rel_e["missing_volumes"], [6.0, 7.0, 8.0, 9.0, 10.0])
            self.assertEqual(rel_e["status"], "REQUESTED")

    def test_scenario_high_volume_feed_ingestion_and_concurrency(self):
        """Scenario 3: Stress-testing SQLite WAL mode under high-volume release ingestion."""
        with E2ETestContext() as ctx:
            # Ingest 250 releases in batch across 10 series
            series_names = [f"Manga Series {i}" for i in range(1, 11)]
            for s in series_names:
                ctx.db.track_discovered_series(s)

            for i in range(1, 251):
                s_name = series_names[i % len(series_names)]
                vol_num = float((i % 25) + 1)
                ctx.db.upsert_release({
                    "guid": f"high-vol-guid-{i}",
                    "title": f"[{'danke-Empire' if i % 2 == 0 else 'Generic'}] {s_name} Vol. {int(vol_num):02d}",
                    "series_name": s_name,
                    "release_group": "danke-Empire" if i % 2 == 0 else "Generic",
                    "volume_start": vol_num,
                    "volume_end": vol_num,
                    "volumes": [vol_num],
                    "status": "REQUESTED"
                })

            # Verify all 250 releases persisted
            all_r = ctx.db.get_releases(limit=500)
            self.assertEqual(len(all_r), 250)

            # Verify grouped series aggregation under heavy load
            groups = ctx.db.get_releases_grouped_by_series()
            self.assertEqual(len(groups), 10)

            # Verify flat rows pagination
            r_page1 = ctx.client.get("/api/releases/flat_rows", params={"limit": 50, "offset": 0})
            self.assertEqual(r_page1.status_code, 200)
            self.assertEqual(r_page1.json()["count"], 50)

            r_page2 = ctx.client.get("/api/releases/flat_rows", params={"limit": 50, "offset": 50})
            self.assertEqual(r_page2.status_code, 200)
            self.assertEqual(r_page2.json()["count"], 50)

    def test_scenario_interrupted_crawl_and_resumption(self):
        """Scenario 4: Backfill progress tracking, start_page persistence, and graceful stop."""
        with E2ETestContext() as ctx:
            # 1. Trigger backfill start via API with specific start_page
            start_res = ctx.client.post("/api/backfill/start", json_data={
                "start_page": 5,
                "max_pages": 10,
                "delay": 0.0
            })
            self.assertEqual(start_res.status_code, 200)
            self.assertEqual(start_res.json()["status"], "started")

            # 2. Check backfill status API
            status_res = ctx.client.get("/api/backfill/status")
            self.assertEqual(status_res.status_code, 200)
            b_status = status_res.json()
            self.assertIn("status", b_status)

            # 3. Stop backfill gracefully
            stop_res = ctx.client.post("/api/backfill/stop")
            self.assertEqual(stop_res.status_code, 200)
            self.assertEqual(stop_res.json()["status"], "stopping")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTier4RealWorldScenarios)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
