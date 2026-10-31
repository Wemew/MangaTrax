import os
import sys
import tempfile
import zipfile
import shutil
from unittest import mock

# Ensure app is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

from app.parser import extract_manga_bundle_and_volumes, string_similarity, clean_manga_title
from app.calibre.scanner import CalibreMangaScanner
from app.calibre.matcher import MangaCalibreMatcher
from app.tagger.cbz_tagger import generate_comicinfo_xml, inject_comicinfo, auto_tag_staging_cbz_files, has_comicinfo
from app.database import MangaDatabase
from mock_calibre_manga import create_mock_calibre_manga_db

def test_manga_bundle_parsing():
    print("--> Testing Manga Bundle & Volume Parsing...")
    # Bundle Range
    r1 = extract_manga_bundle_and_volumes("[ReleaseGroup] Chainsaw Man Vol. 01-05 [CBZ] [Digital]")
    assert r1["is_bundle"] is True
    assert r1["series_name"] == "Chainsaw Man"
    assert r1["volume_start"] == 1.0
    assert r1["volume_end"] == 5.0
    assert r1["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0]

    # Full batch volume extraction V1-V7 -> [1, 2, 3, 4, 5, 6, 7]
    r_v7 = extract_manga_bundle_and_volumes("[Group] Bleach (v01-v07) [Digital] [CBZ]")
    assert r_v7["is_bundle"] is True
    assert r_v7["series_name"] == "Bleach"
    assert r_v7["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]

    # Vol. with dot and spaces
    r_dot = extract_manga_bundle_and_volumes("Bleach Vol. 1-7")
    assert r_dot["is_bundle"] is True
    assert r_dot["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]

    # Another bundle syntax
    r2 = extract_manga_bundle_and_volumes("Frieren - Beyond Journey's End v04-v06 (Digital) (1080p)")
    assert r2["is_bundle"] is True
    assert r2["volume_start"] == 4.0
    assert r2["volume_end"] == 6.0
    assert r2["volumes"] == [4.0, 5.0, 6.0]

    # Comma separated bundle
    r_comma = extract_manga_bundle_and_volumes("Chainsaw Man Vol. 1, 2, 3")
    assert r_comma["is_bundle"] is True
    assert r_comma["volumes"] == [1.0, 2.0, 3.0]

    # Single Volume
    r3 = extract_manga_bundle_and_volumes("[Group] One Piece Vol. 105 [CBZ]")
    assert r3["is_bundle"] is False
    assert r3["series_name"] == "One Piece"
    assert r3["volume_start"] == 105.0
    assert r3["volumes"] == [105.0]

    # Volume 1 Standalone
    r4 = extract_manga_bundle_and_volumes("Dandadan Vol 1")
    assert r4["is_bundle"] is False
    assert r4["series_name"] == "Dandadan"
    assert r4["volume_start"] == 1.0

    print("    [PASSED] All bundle and single volume patterns correctly parsed.")

def test_calibre_scanner_and_matcher():
    print("--> Testing Zero-Lock Calibre Scanner & Bundle Matcher...")
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_calibre_")
    db_path = os.path.join(temp_dir, "metadata.db")
    create_mock_calibre_manga_db(db_path)

    try:
        scanner = CalibreMangaScanner(temp_dir)
        assert scanner.is_available() is True

        summary = scanner.get_library_summary()
        assert summary["total_series"] == 3
        assert summary["total_books"] == 110

        series_list = scanner.get_all_series()
        assert len(series_list) == 3

        # Check Chainsaw Man metadata inheritance
        csm = next(s for s in series_list if s["series_name"] == "Chainsaw Man")
        assert csm["authors"] == "Tatsuki Fujimoto"
        assert csm["publisher"] == "VIZ Media"
        assert "Action" in csm["tags"]
        assert csm["latest_index"] == 3.0

        volumes_map = scanner.get_all_volumes_per_series()
        assert volumes_map["chainsaw man"] == {1.0, 2.0, 3.0}
        assert len(volumes_map["one piece"]) == 104

        matcher = MangaCalibreMatcher(series_list, volumes_map)

        # Case A: Bundle with missing volumes (Chainsaw Man Vol 1-5, Calibre has 1-3)
        rel_bundle = {
            "title": "Chainsaw Man Vol. 01-05",
            "series_name": "Chainsaw Man",
            "is_bundle": True,
            "volumes": [1.0, 2.0, 3.0, 4.0, 5.0]
        }
        res_bundle = matcher.check_release(rel_bundle)
        assert res_bundle["status"] == "REQUESTED"
        assert res_bundle["missing_volumes"] == [4.0, 5.0]

        # Case B: Bundle fully owned (Frieren Vol 1-3)
        rel_owned = {
            "title": "Frieren Vol. 01-03",
            "series_name": "Frieren: Beyond Journey's End",
            "is_bundle": True,
            "volumes": [1.0, 2.0, 3.0]
        }
        res_owned = matcher.check_release(rel_owned)
        assert res_owned["status"] == "IN_CALIBRE"
        assert res_owned["missing_volumes"] == []

        # Case C: Vol 1 of untracked series -> DISCOVERY
        rel_disc = {
            "title": "Dandadan Vol 1",
            "series_name": "Dandadan",
            "is_bundle": False,
            "volumes": [1.0]
        }
        res_disc = matcher.check_release(rel_disc)
        assert res_disc["status"] == "DISCOVERY"

        print("    [PASSED] Scanner and Matcher successfully resolved bundle ranges.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_cbz_tagger_and_comicinfo():
    print("--> Testing ComicInfo.xml Generation & CBZ Zip Injection...")
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_cbz_")
    staging_dir = os.path.join(temp_dir, "staging")
    os.makedirs(staging_dir, exist_ok=True)

    try:
        # Create a mock .cbz archive (ZIP format with 3 image dummy files)
        cbz_name = "Chainsaw Man Vol. 04.cbz"
        cbz_path = os.path.join(staging_dir, cbz_name)

        with zipfile.ZipFile(cbz_path, "w") as z:
            z.writestr("001.jpg", b"fake-jpeg-data-p1")
            z.writestr("002.jpg", b"fake-jpeg-data-p2")
            z.writestr("003.jpg", b"fake-jpeg-data-p3")

        assert has_comicinfo(cbz_path) is False

        # Series templates map as returned by db.get_series_templates_map()
        templates_map = {
            "chainsaw man": {
                "series_name": "Chainsaw Man",
                "authors": "Tatsuki Fujimoto",
                "publisher": "VIZ Media",
                "tags": "Action, Shounen, Supernatural"
            }
        }

        # Run auto-tagging on staging directory
        tagged = auto_tag_staging_cbz_files(staging_dir, templates_map)
        assert len(tagged) == 1
        assert tagged[0]["filename"] == cbz_name
        assert tagged[0]["series_name"] == "Chainsaw Man"
        assert tagged[0]["volume"] == 4.0

        # Verify CBZ file now contains ComicInfo.xml and images are unchanged
        assert has_comicinfo(cbz_path) is True
        with zipfile.ZipFile(cbz_path, "r") as z:
            names = z.namelist()
            assert "001.jpg" in names
            assert "ComicInfo.xml" in names
            assert z.read("001.jpg") == b"fake-jpeg-data-p1"

            xml_data = z.read("ComicInfo.xml").decode("utf-8")
            assert "<Series>Chainsaw Man</Series>" in xml_data
            assert "<Volume>4</Volume>" in xml_data
            assert "<Writer>Tatsuki Fujimoto</Writer>" in xml_data
            assert "<Publisher>VIZ Media</Publisher>" in xml_data
            assert "<Genre>Action, Shounen, Supernatural</Genre>" in xml_data

        print("    [PASSED] CBZ ComicInfo.xml successfully injected with inherited metadata.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_database_and_blacklist_workflow():
    print("--> Testing Manga Database, Requests & Blacklist Lifecycle...")
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_db_")
    db_file = os.path.join(temp_dir, "mangatrax_test.db")

    try:
        db = MangaDatabase(db_file)

        # Sync series
        calibre_series = [{
            "series_id": 1,
            "series_name": "Chainsaw Man",
            "book_count": 3,
            "latest_index": 3.0,
            "authors": "Tatsuki Fujimoto",
            "publisher": "VIZ Media",
            "tags": "Action, Shounen"
        }]
        db.sync_calibre_series(calibre_series)
        series = db.get_all_series()
        assert len(series) == 1
        assert series[0]["name"] == "Chainsaw Man"

        # Upsert release
        rel = {
            "guid": "guid-csm-vol-4-5",
            "title": "Chainsaw Man Vol. 04-05",
            "series_name": "Chainsaw Man",
            "is_bundle": True,
            "volume_start": 4.0,
            "volume_end": 5.0,
            "volumes": [4.0, 5.0],
            "missing_volumes": [4.0, 5.0],
            "download_url": "magnet:?xt=urn:btih:fakehash",
            "status": "REQUESTED"
        }
        is_new, rel_id = db.upsert_release(rel)
        assert is_new is True
        assert rel_id > 0

        pending = db.get_pending_requests()
        assert len(pending) == 1
        assert pending[0]["missing_volumes"] == [4.0, 5.0]

        # Blacklist test
        db.add_to_blacklist("Chainsaw Man", "Dropped series")
        assert db.is_series_blacklisted("Chainsaw Man") is True

        # Releases for blacklisted series are transitioned out of REQUESTED
        pending_after = db.get_pending_requests()
        assert len(pending_after) == 0

        # Unblacklist
        bl = db.get_blacklisted_series()
        assert len(bl) == 1
        db.remove_from_blacklist(bl[0]["id"])
        assert db.is_series_blacklisted("Chainsaw Man") is False

        # Discovery & link preservation test
        disc_rel = {
            "guid": "guid-dandadan-1",
            "title": "Dandadan Vol 1",
            "series_name": "Dandadan",
            "is_bundle": False,
            "volume_start": 1.0,
            "link": "https://mangaupdates.com/series/dandadan",
            "status": "DISCOVERY"
        }
        _, d_id = db.upsert_release(disc_rel)
        db.update_release_details(d_id, cover_url="https://example.com/cover.jpg")
        db.track_discovered_series("Dandadan", release_id=d_id)

        all_tracked = db.get_all_series()
        dandadan = next(s for s in all_tracked if s["name"] == "Dandadan")
        assert dandadan["website_url"] == "https://mangaupdates.com/series/dandadan"
        assert dandadan["is_monitored"] == 1

        # Test clear_discovered_series
        disc_rel2 = {
            "guid": "guid-unwanted-1",
            "title": "Unwanted Vol 1",
            "series_name": "Unwanted",
            "is_bundle": False,
            "volume_start": 1.0,
            "status": "DISCOVERY"
        }
        db.upsert_release(disc_rel2)
        assert len(db.get_discovery_releases()) >= 1
        cleared_disc = db.clear_discovered_series()
        assert cleared_disc >= 1
        assert len(db.get_discovery_releases()) == 0

        # Test CoverCache clear_cache
        from app.covers import CoverCache
        c_cache = CoverCache(temp_dir)
        dummy_file = os.path.join(c_cache.covers_dir, "test_cover.jpg")
        with open(dummy_file, "wb") as f:
            f.write(b"dummy image data")
        assert os.path.isfile(dummy_file)
        removed_files = c_cache.clear_cache()
        assert removed_files == 1
        assert not os.path.exists(dummy_file)

        print("    [PASSED] Database, request queue, and blacklist lifecycle verified.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_series_grouping_and_metadata():
    print("--> Testing Series Grouping and AniList Metadata Caching...")
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_grouping_")
    db_file = os.path.join(temp_dir, "mangatrax_group_test.db")

    try:
        db = MangaDatabase(db_file)
        from app.metadata.anilist import AniListMetadataService

        # 1. Test Metadata Cache storage & retrieval
        anilist = AniListMetadataService(db=db)
        db.cache_manga_metadata("bleach", {
            "canonical_title": "Bleach",
            "romaji_title": "BLEACH",
            "cover_url": "https://example.com/bleach.jpg",
            "total_volumes": 74,
            "status": "FINISHED",
            "synopsis": "Ichigo Kurosaki is a high school student...",
            "source": "anilist"
        })

        cached = db.get_cached_manga_metadata("bleach")
        assert cached is not None
        assert cached["canonical_title"] == "Bleach"
        assert cached["total_volumes"] == 74

        # 2. Add multiple releases of the same series with different names and bundle ranges
        rel1 = {
            "guid": "guid-bleach-batch-1",
            "title": "[danke-Empire] Bleach v01-v07 [Digital] [1080p]",
            "series_name": "Bleach",
            "is_bundle": True,
            "volume_start": 1.0,
            "volume_end": 7.0,
            "volumes": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
            "missing_volumes": [4.0, 5.0, 6.0, 7.0],
            "status": "REQUESTED"
        }
        rel2 = {
            "guid": "guid-bleach-single-8",
            "title": "Bleach Vol. 8",
            "series_name": "Bleach",
            "is_bundle": False,
            "volume_start": 8.0,
            "volume_end": 8.0,
            "volumes": [8.0],
            "missing_volumes": [8.0],
            "status": "REQUESTED"
        }
        rel3 = {
            "guid": "guid-csm-1",
            "title": "Chainsaw Man Vol. 1",
            "series_name": "Chainsaw Man",
            "is_bundle": False,
            "volume_start": 1.0,
            "volume_end": 1.0,
            "volumes": [1.0],
            "missing_volumes": [],
            "status": "IN_CALIBRE"
        }

        db.upsert_release(rel1)
        db.upsert_release(rel2)
        db.upsert_release(rel3)

        # 3. Test get_releases_grouped_by_series
        groups = db.get_releases_grouped_by_series()
        assert len(groups) == 2

        # Bleach group should have wanted releases and aggregated volumes 1..8
        bleach_group = next(g for g in groups if g["series_name"] == "Bleach")
        assert bleach_group["releases_count"] == 2
        assert bleach_group["wanted_count"] == 2
        assert bleach_group["all_volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        assert bleach_group["missing_volumes"] == [4.0, 5.0, 6.0, 7.0, 8.0]
        assert bleach_group["total_volumes"] == 74
        assert bleach_group["cover_url"] == "https://example.com/bleach.jpg"

        # Chainsaw Man group
        csm_group = next(g for g in groups if g["series_name"] == "Chainsaw Man")
        assert csm_group["releases_count"] == 1
        assert csm_group["all_volumes"] == [1.0]

        # 4. Test Romaji alias & English canonical series deduplication in releases view
        db.cache_manga_metadata("shingeki no kyojin", {
            "canonical_title": "Attack on Titan",
            "romaji_title": "Shingeki no Kyojin",
            "cover_url": "https://example.com/aot.jpg"
        })
        rel_aot1 = {
            "guid": "guid-snk-romaji-1",
            "title": "[Group] Shingeki no Kyojin Vol. 01 [Digital]",
            "series_name": "Shingeki no Kyojin",
            "volumes": [1.0],
            "missing_volumes": [],
            "status": "REQUESTED"
        }
        rel_aot2 = {
            "guid": "guid-aot-english-2",
            "title": "[Group] Attack on Titan Vol. 02 [Digital]",
            "series_name": "Attack on Titan",
            "volumes": [2.0],
            "missing_volumes": [],
            "status": "REQUESTED"
        }
        db.upsert_release(rel_aot1)
        db.upsert_release(rel_aot2)

        # Both releases should collapse under single canonical "Attack on Titan" card
        updated_groups = db.get_releases_grouped_by_series()
        aot_group = next((g for g in updated_groups if g["series_name"] == "Attack on Titan"), None)
        assert aot_group is not None
        assert aot_group["romaji_name"] == "Shingeki no Kyojin"
        assert aot_group["all_volumes"] == [1.0, 2.0]
        # Ensure no orphan "Shingeki no Kyojin" series card remains
        assert not any(g["series_name"] == "Shingeki no Kyojin" for g in updated_groups)

        print("    [PASSED] Series grouping and metadata cache properly aggregate all bundle volumes.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_chapters_and_mixed_updates():
    print("--> Testing Chapter-Only Discard & Mixed Volume/Chapter Parsing...")
    from app.parser import is_chapter_only, extract_manga_bundle_and_volumes, extract_release_group

    # 1. Pure chapters must be flagged as chapter-only (to be discarded)
    assert is_chapter_only("One Piece Chapter 1110 [Digital]") is True
    assert is_chapter_only("[TCB] One Piece Ch. 1110") is True
    assert is_chapter_only("Chainsaw Man c150") is True
    assert is_chapter_only("Jujutsu Kaisen Chapter 250-255") is True
    assert is_chapter_only("[Group] Bleach Ch. 01 - 10") is True
    assert is_chapter_only("Oshi no Ko c140 [English]") is True

    # Numbers without 'v' or 'vol' in front indicate chapter releases, not volumes
    assert is_chapter_only("[Group] 7 dai Tatarimasu node Hayaku Kekkon Shitekudasai! | Get Married So I Can Curse Your Firstborn and Finally Be Free! - 01-02") is True
    assert is_chapter_only("7 dai Tatarimasu node Hayaku Kekkon Shitekudasai! | Get Married So I Can Curse Your Firstborn and Finally Be Free! 01-02") is True
    assert is_chapter_only("[ScanGroup] Manga Title - 01 [English]") is True
    assert is_chapter_only("[ScanGroup] Manga Title 01-02 [English]") is True
    assert is_chapter_only("[ScanGroup] Manga Title - 15 [English]") is True
    assert is_chapter_only("[ScanGroup] Manga Title #01-02") is True
    assert is_chapter_only("[ScanGroup] Manga Title [01-02]") is True
    assert is_chapter_only("Mob Psycho 100 - 01") is True

    # 2. Volumes must NOT be flagged as chapter-only
    assert is_chapter_only("One Piece Vol. 108") is False
    assert is_chapter_only("Bleach v01-v07") is False
    assert is_chapter_only("[danke-Empire] Bleach v01-v20 [Digital]") is False
    assert is_chapter_only("Look Back [Digital] [CBZ]") is False
    assert is_chapter_only("Goodbye, Eri (2022) [Digital]") is False
    assert is_chapter_only("Mob Psycho 100 [Digital]") is False

    # 3. Mixed releases must NOT be flagged as chapter-only, and only volume info extracted
    assert is_chapter_only("Bleach Vol. 1 (Ch. 1-7)") is False
    r_mixed1 = extract_manga_bundle_and_volumes("Bleach Vol. 1 (Ch. 1-7)")
    assert r_mixed1["series_name"] == "Bleach"
    assert r_mixed1["volume_start"] == 1.0
    assert r_mixed1["volumes"] == [1.0]
    assert r_mixed1["is_bundle"] is False

    r_mixed2 = extract_manga_bundle_and_volumes("One Piece v108 (c1080-c1090)")
    assert r_mixed2["series_name"] == "One Piece"
    assert r_mixed2["volume_start"] == 108.0
    assert r_mixed2["volumes"] == [108.0]

    r_mixed3 = extract_manga_bundle_and_volumes("[Group] Chainsaw Man Vol. 01-03 (Ch. 1-26)")
    assert r_mixed3["series_name"] == "Chainsaw Man"
    assert r_mixed3["is_bundle"] is True
    assert r_mixed3["volumes"] == [1.0, 2.0, 3.0]

    print("    [PASSED] Chapter-only releases correctly discarded; mixed releases isolate volume data.")

def test_prioritized_release_groups():
    print("--> Testing Prioritized Release Groups Management & Filtering...")
    from app.parser import extract_release_group
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_prioritized_")
    db_file = os.path.join(temp_dir, "mangatrax_prioritized_test.db")

    try:
        # 1. Group extraction & ignoring [Completed]/[Complete] tags regardless of position
        assert extract_release_group("[danke-Empire] Bleach v01-v20 [Digital] [1080p] [CBZ]") == "danke-Empire"
        assert extract_release_group("[Lu自主规制] Chainsaw Man Vol. 01-11 [Digital] [HD]") == "Lu自主规制"
        assert extract_release_group("[1R0N] One Piece Vol. 108") == "1R0N"
        assert extract_release_group("Bleach v01-v20 (danke-Empire)") == "danke-Empire"
        assert extract_release_group("One Piece Vol 108 [Digital] [CBZ]") is None
        # Releases where [Completed] is after or before group
        assert extract_release_group("[Manga] Frieren Vol. 01-10 [danke-Empire] [Completed]") == "danke-Empire"
        assert extract_release_group("Frieren Vol. 01-10 [Completed] [danke-Empire]") == "danke-Empire"
        assert extract_release_group("[danke-Empire] Bleach v01-v20 [Completed]") == "danke-Empire"
        assert extract_release_group("Bleach v01-v20 (danke-Empire) [Complete]") == "danke-Empire"
        assert extract_release_group("[Seven Seas] [Stick] The Tale of a Little Alchemist v01-04 [Digital] [Completed]") == "Stick"
        # Releases with [Completed] or [Complete] but NO release group
        assert extract_release_group("Ore ga Ojousama Gakkou ni Shomin Sample Toshite Rachirareta Ken v01-15 [Completed]") is None
        assert extract_release_group("[Manga] Ore ga Ojousama Gakkou ni Shomin Sample Toshite Rachirareta Ken v01-15 (2012-2018) [Complete] | Shomin Sample cbz ver") is None
        assert extract_release_group("Attack on Titan v01-v34 [Completed Series] [CBZ ver]") is None
        assert extract_release_group("Dandadan Vol. 01-05 [Completed]") is None
        # Scene releases with unbracketed groups
        assert extract_release_group("Manga.Million.SiteRip.1600p.AVIF.2026-TenmaLand (Multi) (All languages) (CBZ) (Digital)") == "TenmaLand"
        assert extract_release_group("Manga.Million.SiteRip.1600p.AVIF.2026 TenmaLand (Multi) (All languages) (CBZ) (Digital)") == "TenmaLand"

        db = MangaDatabase(db_file)

        # 2. Database prioritized groups
        groups = db.get_prioritized_groups()
        assert len(groups) >= 4  # Default seeded groups
        assert db.is_group_prioritized("danke-Empire") is True
        assert db.is_group_prioritized("unknown-group") is False

        # Add custom group
        db.add_prioritized_group("CustomGroup", "High quality scanlations", priority=2)
        assert db.is_group_prioritized("CustomGroup") is True

        # Insert releases: one from prioritized group, one from regular group
        rel1 = {
            "guid": "guid-csm-danke-1",
            "title": "[danke-Empire] Chainsaw Man Vol. 01 [Digital]",
            "series_name": "Chainsaw Man",
            "release_group": "danke-Empire",
            "is_bundle": False,
            "volume_start": 1.0,
            "volume_end": 1.0,
            "volumes": [1.0],
            "missing_volumes": [],
            "status": "REQUESTED"
        }
        rel2 = {
            "guid": "guid-csm-random-1",
            "title": "[RandomGroup] Chainsaw Man Vol. 01 [Scan]",
            "series_name": "Chainsaw Man",
            "release_group": "RandomGroup",
            "is_bundle": False,
            "volume_start": 1.0,
            "volume_end": 1.0,
            "volumes": [1.0],
            "missing_volumes": [],
            "status": "REQUESTED"
        }
        db.upsert_release(rel1)
        db.upsert_release(rel2)

        # 3. Query all vs prioritized
        all_releases = db.get_releases()
        assert len(all_releases) == 2
        danke_rel = next(r for r in all_releases if r["guid"] == "guid-csm-danke-1")
        random_rel = next(r for r in all_releases if r["guid"] == "guid-csm-random-1")
        assert danke_rel["is_prioritized"] is True
        assert random_rel["is_prioritized"] is False

        prioritized_releases = db.get_prioritized_releases()
        assert len(prioritized_releases) == 1
        assert prioritized_releases[0]["guid"] == "guid-csm-danke-1"

        # 4. Grouped by series prioritized_only
        p_series_groups = db.get_releases_grouped_by_series(prioritized_only=True)
        assert len(p_series_groups) == 1
        assert len(p_series_groups[0]["releases"]) == 1
        assert p_series_groups[0]["releases"][0]["release_group"] == "danke-Empire"

        # 3. Test automatic database sanitization of legacy erroneous "Completed" release groups
        with db.get_connection() as conn:
            conn.execute("INSERT INTO releases (guid, title, series_name, release_group) VALUES ('guid-legacy-completed-1', '[Manga] Frieren Vol. 01-10 [danke-Empire] [Completed]', 'Frieren', 'Completed');")
            conn.execute("INSERT INTO releases (guid, title, series_name, release_group) VALUES ('guid-legacy-completed-2', 'Ore ga Ojousama v01-15 [Completed]', 'Ore ga Ojousama', 'Completed');")
            conn.commit()

        # Run init_db which triggers the migration
        db.init_db()

        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT release_group FROM releases WHERE guid = 'guid-legacy-completed-1';")
            assert cur.fetchone()[0] == "danke-Empire"
            cur.execute("SELECT release_group FROM releases WHERE guid = 'guid-legacy-completed-2';")
            assert cur.fetchone()[0] == ""

        print("    [PASSED] Prioritized groups extracted, stored, and filtered with complete accuracy.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_mangaupdates_and_rss_links():
    print("--> Testing MangaUpdates Service & RSS Feed Link Integration...")
    from app.metadata.mangaupdates import MangaUpdatesService, clean_description_text

    # 1. BBCode, HTML, Markdown links, and publisher prefix cleaning
    raw_desc = "[b]From Viz:[/b] In a world where <p>demons lurk</p>, young [url=https://example.com]Denji[/url] must survive."
    cleaned = clean_description_text(raw_desc)
    assert "[b]" not in cleaned
    assert "[/b]" not in cleaned
    assert "From Viz:" not in cleaned
    assert "<p>" not in cleaned
    assert "In a world where demons lurk, young Denji must survive." == cleaned

    raw_markdown = "**Original Webtoon:** [Kakao Webtoon R19](https://webtoon.kakao.com/content/12345)"
    cleaned_md = clean_description_text(raw_markdown)
    assert cleaned_md == "Original Webtoon: Kakao Webtoon R19"
    assert "https://" not in cleaned_md

    # Multiline markdown link with newline between ] and ( (exact user issue from screenshot)
    raw_multiline = """**Original Webtoon:**
[Kakao Webtoon R19]
(https://webtoon.kakao.com/content/%EC%9B%94%EC%95%BC%ED%99%98%EB%8B%B4-19%EC%84%B8-%EC%99%84%EC%A0%84%ED%8C%90/4764)
[Kakao Page R19](https://page.kakao.com/content/68152126)"""
    cleaned_multiline = clean_description_text(raw_multiline)
    assert "https://" not in cleaned_multiline
    assert "%EC%9B%94" not in cleaned_multiline
    assert "Original Webtoon: Kakao Webtoon R19 Kakao Page R19" in cleaned_multiline

    # HTML entity unescaping
    raw_entities = "Kimito Kagurazaka is a commoner with a fetish for men&#039;s muscles or at least that&#039;s the lie..."
    cleaned_entities = clean_description_text(raw_entities)
    assert "men's muscles" in cleaned_entities
    assert "&#039;" not in cleaned_entities

    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_mu_")
    db_file = os.path.join(temp_dir, "mangatrax_mu_test.db")

    try:
        db = MangaDatabase(db_file)
        mu_service = MangaUpdatesService(db=db)

        # 2. Cache seeding and retrieval
        db.cache_manga_metadata("mu:dandadan", {
            "canonical_title": "Dandadan",
            "romaji_title": "https://www.mangaupdates.com/series/test-dandadan",
            "synopsis": "A nerdy high school boy and an occult-obsessed girl meet bizarre spirits and extraterrestrials.",
            "cover_url": "https://example.com/dandadan.jpg",
            "source": "mangaupdates"
        })

        info = mu_service.get_series_info("Dandadan")
        assert info["title"] == "Dandadan"
        assert "extraterrestrials" in info["description"]
        assert info["url"] == "https://www.mangaupdates.com/series/test-dandadan"
        assert info["source"] == "cache"

        # 3. Discovery release with RSS link, MangaUpdates synopsis and URL
        rss_permalink = "https://nyaa.si/view/1234567"
        rel = {
            "guid": "guid-dandadan-vol1",
            "title": "[Group] Dandadan Vol. 01 [Digital]",
            "series_name": "Dandadan",
            "release_group": "Group",
            "is_bundle": False,
            "volume_start": 1.0,
            "volume_end": 1.0,
            "volumes": [1.0],
            "missing_volumes": [],
            "download_url": "magnet:?xt=urn:btih:mock",
            "link": rss_permalink,
            "cover_url": info["cover_url"],
            "description": info["description"],
            "mangaupdates_url": info["url"],
            "source_feed_name": "Nyaa Manga",
            "status": "DISCOVERY"
        }
        db.upsert_release(rel)

        discovery_items = db.get_discovery_releases()
        assert len(discovery_items) == 1
        d_item = discovery_items[0]
        assert d_item["link"] == rss_permalink
        assert d_item["mangaupdates_url"] == "https://www.mangaupdates.com/series/test-dandadan"
        assert "extraterrestrials" in d_item["description"]

        # 4. Track discovered series inherits MangaUpdates link and description
        db.track_discovered_series("Dandadan", release_id=d_item["id"])
        tracked_list = db.get_all_series()
        assert len(tracked_list) == 1
        tracked_dandadan = tracked_list[0]
        assert tracked_dandadan["website_url"] == "https://www.mangaupdates.com/series/test-dandadan"
        assert "extraterrestrials" in tracked_dandadan["description"]

        # 5. Test RSS Provider web post permalink vs torrent download URL separation
        from unittest.mock import patch, MagicMock
        from app.providers.rss import RSSFeedProvider
        mock_feed = MagicMock()
        mock_feed.bozo = 0
        mock_feed.entries = [{
            "title": "[Group] Chainsaw Man Vol. 12 [Digital]",
            "link": "https://nyaa.si/download/2180362.torrent",
            "id": "https://nyaa.si/view/2180362",
            "summary": '<a href="https://nyaa.si/view/2180362">#2180362</a> | 250 MiB',
            "nyaa_size": "250 MiB",
            "nyaa_seeders": 45,
            "nyaa_infohash": "6506A47A152E85AAECA04865557873AF38F895F3"
        }]
        with patch("feedparser.parse", return_value=mock_feed):
            provider = RSSFeedProvider(name="Nyaa", url="https://nyaa.si/?page=rss")
            rss_items = provider.fetch_releases()
            assert len(rss_items) == 1
            assert rss_items[0].link == "https://nyaa.si/view/2180362"
            assert rss_items[0].download_url == "https://nyaa.si/download/2180362.torrent"
            assert rss_items[0].raw_data["post_size"] == "250 MiB"
            assert rss_items[0].raw_data["seeders"] == 45

        print("    [PASSED] MangaUpdates metadata and specific RSS links verified end-to-end.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_romaji_to_english_titles_conversion():
    print("--> Testing Romaji to English Titles Normalization & Cross-Matching...")
    from app.metadata.anilist import AniListMetadataService

    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_english_")
    db_file = os.path.join(temp_dir, "mangatrax_eng_test.db")

    try:
        db = MangaDatabase(db_file)
        anilist = AniListMetadataService(db=db)

        # 1. Seed or resolve English vs Romaji mapping in cache
        db.cache_manga_metadata("shingeki no kyojin", {
            "canonical_title": "Attack on Titan",
            "romaji_title": "Shingeki no Kyojin",
            "synopsis": "Humanity fights giant man-eating humanoids.",
            "source": "anilist"
        })
        db.cache_manga_metadata("attack on titan", {
            "canonical_title": "Attack on Titan",
            "romaji_title": "Shingeki no Kyojin",
            "synopsis": "Humanity fights giant man-eating humanoids.",
            "source": "anilist"
        })

        db.cache_manga_metadata("kimetsu no yaiba", {
            "canonical_title": "Demon Slayer: Kimetsu no Yaiba",
            "romaji_title": "Kimetsu no Yaiba",
            "synopsis": "Tanjiro fights demons to save his sister.",
            "source": "anilist"
        })

        # Test resolve_english_title
        assert anilist.resolve_english_title("Shingeki no Kyojin") == "Attack on Titan"
        assert anilist.resolve_english_title("Kimetsu no Yaiba") == "Demon Slayer: Kimetsu no Yaiba"
        assert anilist.resolve_english_title("Attack on Titan") == "Attack on Titan"

        # 2. Test Calibre Cross-Matching (Calibre has English, release has Romaji)
        calibre_library = [
            {"series_id": 1, "series_name": "Attack on Titan", "book_count": 34, "latest_index": 34.0},
            {"series_id": 2, "series_name": "Demon Slayer: Kimetsu no Yaiba", "book_count": 23, "latest_index": 23.0}
        ]
        volumes_map = {
            "attack on titan": {1.0, 2.0, 3.0},
            "demon slayer: kimetsu no yaiba": {1.0, 2.0}
        }
        meta_cache = db.get_all_manga_metadata_cache()
        matcher = MangaCalibreMatcher(calibre_library, volumes_map, meta_cache)

        # Release with Romaji name "Shingeki no Kyojin"
        match_snk = matcher.find_series("Shingeki no Kyojin")
        assert match_snk is not None
        assert match_snk["series_name"] == "Attack on Titan"

        # Check release evaluation with Romaji title against English Calibre library
        rel_snk = {
            "title": "[Group] Shingeki no Kyojin Vol. 04 [Digital]",
            "series_name": "Shingeki no Kyojin",
            "volumes": [4.0],
            "is_bundle": False
        }
        res = matcher.check_release(rel_snk, auto_request_monitored=True)
        assert res["status"] == "REQUESTED"
        assert res["matched_series"]["series_name"] == "Attack on Titan"
        assert 4.0 in res["missing_volumes"]

        # 3. Test Database Title Normalization (normalize_series_titles_to_english)
        rel_db = {
            "guid": "guid-test-snk-vol1",
            "title": "[Group] Shingeki no Kyojin Vol. 01",
            "series_name": "Shingeki no Kyojin",
            "volume_start": 1.0,
            "volume_end": 1.0,
            "volumes": [1.0],
            "missing_volumes": [],
            "status": "DISCOVERY"
        }
        db.upsert_release(rel_db)

        # Before normalization, discovery sees English via cache
        disc_releases = db.get_discovery_releases()
        assert len(disc_releases) == 1
        assert disc_releases[0]["series_name"] == "Attack on Titan"
        assert disc_releases[0]["romaji_name"] == "Shingeki no Kyojin"

        # Run database normalization
        updated = db.normalize_series_titles_to_english(anilist)
        assert updated >= 1

        all_rel = db.get_releases()
        snk_in_db = next(r for r in all_rel if r["guid"] == "guid-test-snk-vol1")
        assert snk_in_db["series_name"] == "Attack on Titan"

        print("    [PASSED] Romaji titles successfully converted to English and cross-matched against Calibre.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_nyaa_historical_crawl_and_series_backfill():
    print("--> Testing Nyaa Historical Paginated Crawl & Targeted Series Backfill...")
    from app.providers.nyaa import NyaaMangaProvider
    from app.scheduler import MangaScheduler
    from app.covers import CoverCache
    from unittest.mock import patch, MagicMock
    import asyncio

    sample_nyaa_html = """
    <html>
    <body>
    <table class="torrent-list">
    <tbody>
        <tr>
            <td><a href="/c/3_1"><img src="/static/img/icons/nyaa/3_1.png"></a></td>
            <td>
                <a href="/view/2180362#comments" class="comments"><i class="fa fa-comments"></i> 2</a>
                <a href="/view/2180362" title="Chainsaw Man 180">Chainsaw Man 180</a>
            </td>
            <td>
                <a href="/download/2180362.torrent"><i class="fa fa-download"></i></a>
                <a href="magnet:?xt=urn:btih:mockhash1"><i class="fa fa-magnet"></i></a>
            </td>
            <td class="text-center">8.0 MiB</td>
            <td class="text-center" data-timestamp="1729794900">2026-10-24 18:35</td>
            <td class="text-center">50</td>
            <td class="text-center">2</td>
            <td class="text-center">100</td>
        </tr>
        <tr>
            <td><a href="/c/3_1"><img src="/static/img/icons/nyaa/3_1.png"></a></td>
            <td>
                <a href="/view/2180200" title="[danke-Empire] Chainsaw Man Vol. 04-06 [Digital]">
                    [danke-Empire] Chainsaw Man Vol. 04-06 [Digital]
                </a>
            </td>
            <td>
                <a href="/download/2180200.torrent"><i class="fa fa-download"></i></a>
                <a href="magnet:?xt=urn:btih:mockhash2"><i class="fa fa-magnet"></i></a>
            </td>
            <td class="text-center">450.0 MiB</td>
            <td class="text-center" data-timestamp="1729700000">2026-10-23 15:00</td>
            <td class="text-center">75</td>
            <td class="text-center">1</td>
            <td class="text-center">250</td>
        </tr>
    </tbody>
    </table>
    </body>
    </html>
    """

    # 1. Test Nyaa HTML table parsing & chapter-only discard
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = sample_nyaa_html.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        nyaa = NyaaMangaProvider()
        releases = nyaa.crawl_pages(max_pages=1)
        # Chainsaw Man 180 is pure chapter update -> must be discarded
        # Only Chainsaw Man Vol. 04-06 bundle must remain!
        assert len(releases) == 1
        bundle = releases[0]
        assert bundle.series_name == "Chainsaw Man"
        assert bundle.release_group == "danke-Empire"
        assert bundle.is_bundle is True
        assert bundle.volumes == [4.0, 5.0, 6.0]
        assert bundle.link == "https://nyaa.si/view/2180200"
        assert bundle.download_url == "magnet:?xt=urn:btih:mockhash2"
        assert bundle.raw_data["post_size"] == "450.0 MiB"
        assert bundle.raw_data["seeders"] == 75

    # 2. Test targeted series backfill in Scheduler
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_sched_backfill_")
    db_file = os.path.join(temp_dir, "mangatrax_sb_test.db")

    try:
        db = MangaDatabase(db_file)
        covers = CoverCache(temp_dir)
        sched = MangaScheduler(db, covers)

        # Mock Calibre having Chainsaw Man Vol 1-3
        calibre_library = [
            {"series_id": 10, "series_name": "Chainsaw Man", "book_count": 3, "latest_index": 3.0}
        ]
        db.sync_calibre_series(calibre_library)

        # Mock AniList, MangaUpdates, and qBittorrent to prevent live network calls during tests
        sched.anilist.resolve_manga_series = MagicMock(return_value={
            "canonical_title": "Chainsaw Man",
            "romaji_title": "Chainsaw Man",
            "english_title": "Chainsaw Man",
            "total_volumes": 20
        })
        sched.mangaupdates.get_series_info = MagicMock(return_value={
            "title": "Chainsaw Man",
            "description": "Mock description",
            "url": "https://www.mangaupdates.com/series/mock"
        })
        mock_qbit = MagicMock()
        mock_qbit.add_torrent.return_value = True
        sched.get_qbit_client = MagicMock(return_value=mock_qbit)

        # Mock Nyaa search returning the bundle
        with patch.object(NyaaMangaProvider, "search_series", return_value=[bundle]):
            res = sched.backfill_series("Chainsaw Man", max_pages=1)

            assert res["status"] == "success"
            assert res["releases_found"] == 1
            assert res["requested"] == 1

            # Verify saved release in DB
            db_releases = db.get_releases(status="REQUESTED")
            assert len(db_releases) == 1
            assert db_releases[0]["series_name"] == "Chainsaw Man"
            assert db_releases[0]["missing_volumes"] == [4.0, 5.0, 6.0]

            # 3. Test async background series backfill
            async_res = sched.run_series_backfill_async("Chainsaw Man", max_pages=1)
            assert async_res["status"] == "started"
            import time
            time.sleep(0.3)
            status_info = sched.get_backfill_status()
            assert status_info["status"] in ("running", "idle")

        print("    [PASSED] Nyaa historical paginated crawl and series backfill verified 100%.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_in_app_bittorrent_downloader_setup():
    print("--> Testing In-App BitTorrent Downloader Configuration & Lifecycle...")
    from unittest.mock import MagicMock, patch
    from app.downloader.qbit import QBitClient
    from app.scheduler import MangaScheduler
    from app.covers import CoverCache

    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_qbit_inapp_")
    db_file = os.path.join(temp_dir, "mangatrax_qbit.db")

    try:
        db = MangaDatabase(db_file)
        covers = CoverCache(temp_dir)
        sched = MangaScheduler(db, covers)

        # 1. Verify default in-app downloader settings are initialized in database
        settings = db.get_all_settings()
        assert "qbit_url" in settings
        assert "qbit_username" in settings
        assert "qbit_password" in settings
        assert "qbit_enabled" in settings
        assert "qbit_category" in settings
        assert "qbit_tags" in settings

        # 2. Test QBitClient disabled state
        disabled_client = QBitClient(enabled=False)
        assert disabled_client.login() is False
        assert disabled_client.add_torrent("magnet:?xt=test") is False
        test_res = disabled_client.test_connection()
        assert test_res["success"] is False
        assert "disabled" in test_res["message"].lower()

        # 3. Test dynamic in-app reconfiguration without compose variables
        db.set_setting("qbit_url", "http://192.168.1.150:9091")
        db.set_setting("qbit_username", "mangauser")
        db.set_setting("qbit_password", "secretpass")
        db.set_setting("qbit_enabled", "true")
        db.set_setting("qbit_category", "manga-custom")
        db.set_setting("qbit_tags", "tag1,tag2")

        client = sched.get_qbit_client()
        assert client.base_url == "http://192.168.1.150:9091"
        assert client.username == "mangauser"
        assert client.password == "secretpass"
        assert client.enabled is True

        # 4. Mock successful authentication and connection test
        mock_login_resp = MagicMock()
        mock_login_resp.status = 200
        mock_login_resp.read.return_value = b"Ok."

        mock_version_resp = MagicMock()
        mock_version_resp.status = 200
        mock_version_resp.read.return_value = b"v4.6.5"

        with patch.object(client.opener, "open", side_effect=[mock_login_resp, mock_version_resp]):
            conn_res = client.test_connection()
            assert conn_res["success"] is True
            assert conn_res["version"] == "v4.6.5"
            assert "Connected to qBittorrent v4.6.5" in conn_res["message"]

        # 5. Mock add_torrent call
        mock_add_resp = MagicMock()
        mock_add_resp.status = 200
        mock_add_resp.read.return_value = b"Ok."

        with patch.object(client.opener, "open", return_value=mock_add_resp):
            success = client.add_torrent(
                url_or_magnet="magnet:?xt=urn:btih:mockhash123",
                savepath="/custom/staging",
                category="manga-custom",
                tags="tag1,tag2"
            )
            assert success is True

        print("    [PASSED] In-app BitTorrent downloader setup, dynamic reload, and API operations verified 100%.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_light_novel_filtering():
    print("--> Testing Light Novel Detection, Filtering & Auto-Purging...")
    from app.parser import is_light_novel
    from app.providers.base import ReleaseItem
    from app.scheduler import MangaScheduler
    from app.covers import CoverCache
    from app.calibre.matcher import MangaCalibreMatcher

    # 1. Pattern matching and classification
    assert is_light_novel("The Tale of a Little Alchemist Blessed by the Spirits v01-04 [Seven Seas] [Stick]") is True
    assert is_light_novel("Reincarnated as a Sword v01-12 [Seven Seas] [Stick]") is True
    assert is_light_novel("Overlord v01-14 [Yen On] [Lu自主规制]") is True
    assert is_light_novel("Ascendance of a Bookworm Part 1-5 [J-Novel Club] [Stick]") is True
    assert is_light_novel("Mushoku Tensei - Jobless Reincarnation (Novel) v01-26") is True
    assert is_light_novel("Sword Art Online [LN] Vol. 01-25") is True
    assert is_light_novel("Classroom of the Elite [Light Novel] v01-11") is True
    assert is_light_novel("That Time I Got Reincarnated as a Slime v01-18 [EPUB]") is True
    assert is_light_novel("Toradora! v01-10 [Airship] [Stick]") is True
    assert is_light_novel("Solo Leveling [Web Novel] Complete") is True
    assert is_light_novel("The Tale of a Little Alchemist Blessed by the Spirits v01-02 [Audiobook] [Seven Seas Siren] [Stick & Oak]") is True

    # 2. Legitimate manga must NOT be classified as light novels
    assert is_light_novel("[danke-Empire] Bleach v01-v20 [Digital] [CBZ]") is False
    assert is_light_novel("[Lu自主规制] Chainsaw Man Vol. 01-11") is False
    assert is_light_novel("[Manga] Ore ga Ojousama Gakkou ni Shomin Sample Toshite Rachirareta Ken v01-15") is False
    assert is_light_novel("The Tale of a Little Alchemist Blessed by the Spirits (Manga) v01-03 [Seven Seas] [Digital]") is False
    assert is_light_novel("The Tale of a Little Alchemist Blessed by the Spirits v01 [CBZ]") is False
    assert is_light_novel("Frieren - Beyond Journey's End v01-10 (Digital) (1080p)") is False
    assert is_light_novel("One Piece Vol. 108 [1R0N]") is False
    assert is_light_novel("Dandadan Vol. 1-8 [Viz Media] [CBZ]") is False
    assert is_light_novel("Chainsaw Man Vol. 1 [Manga] [Seven Seas] [Stick]") is False

    temp_dir = tempfile.mkdtemp(prefix="mangatrax_test_ln_")
    db_file = os.path.join(temp_dir, "mangatrax_ln_test.db")

    try:
        db = MangaDatabase(db_file)
        cover_cache = CoverCache(temp_dir)
        scheduler = MangaScheduler(db, cover_cache)
        matcher = MangaCalibreMatcher([], {})

        # 3. Scheduler processing rejects Light Novels
        ln_rel = ReleaseItem(
            title="The Tale of a Little Alchemist Blessed by the Spirits v01-04 [Seven Seas] [Stick]",
            link="https://nyaa.si/view/999999",
            series_name="The Tale of a Little Alchemist Blessed by the Spirits",
            release_group="Stick",
            is_bundle=True,
            volume_start=1.0,
            volume_end=4.0,
            volumes=[1.0, 2.0, 3.0, 4.0]
        )
        is_new, status, missing, rel_id, canonical = scheduler._process_release(ln_rel, "Nyaa Manga", matcher)
        assert status == "LIGHT_NOVEL"
        assert is_new is False
        assert rel_id is None

        # 4. Database auto-purge on startup
        # Manually force-insert a light novel into releases table
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO releases (guid, title, series_name, status)
                VALUES ('ln-manual-1', 'The Tale of a Little Alchemist Blessed by the Spirits v01-04 [Seven Seas] [Stick]', 'The Tale of a Little Alchemist Blessed by the Spirits', 'DISCOVERY');
            """)
            conn.commit()

        # Verify it exists
        releases_before = db.get_discovery_releases()
        assert any(r["guid"] == "ln-manual-1" for r in releases_before)

        # Trigger database init/purge
        db.init_db()

        # Verify it was purged from the database
        releases_after = db.get_discovery_releases()
        assert not any(r["guid"] == "ln-manual-1" for r in releases_after)

        print("    [PASSED] Light novels detected, rejected by scheduler, and purged from database.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_paginated_discovery_and_deduplication():
    print("--> Testing Paginated Discovery & Series Deduplication...")
    temp_dir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(temp_dir, "test_discovery.db")
        db = MangaDatabase(db_path)

        # Insert 15 discovery releases across 5 distinct series (3 releases each)
        series_names = ["Spy x Family", "Chainsaw Man", "Dandadan", "Frieren", "Jujutsu Kaisen"]
        for i, s_name in enumerate(series_names):
            for v in [1, 2, 3]:
                db.upsert_release({
                    "guid": f"disc-{i}-{v}",
                    "title": f"[Manga] {s_name} Vol. {v} [Digital]",
                    "series_name": s_name,
                    "volume_start": float(v),
                    "volume_end": float(v),
                    "is_bundle": False,
                    "volumes": [float(v)],
                    "download_url": f"magnet:?xt=urn:btih:mock{i}{v}",
                    "link": f"https://example.com/post/{i}/{v}",
                    "cover_url": f"https://example.com/covers/{i}.jpg" if v == 1 else "",
                    "description": f"Synopsis for {s_name}" if v == 1 else "",
                    "source_feed_name": "Test Manga Feed",
                    "status": "DISCOVERY"
                })

        # 1. Total discovery count should collapse by series (exactly 5 series)
        total_series = db.count_discovery_releases()
        assert total_series == 5, f"Expected 5 deduplicated series, got {total_series}"

        # 2. Paginated fetching
        # Page 1: limit 2, offset 0 -> 2 items
        page1 = db.get_discovery_releases(limit=2, offset=0)
        assert len(page1) == 2, f"Expected 2 items on page 1, got {len(page1)}"
        p1_series = {r["series_name"] for r in page1}
        assert len(p1_series) == 2

        # Page 2: limit 2, offset 2 -> 2 items
        page2 = db.get_discovery_releases(limit=2, offset=2)
        assert len(page2) == 2, f"Expected 2 items on page 2, got {len(page2)}"
        p2_series = {r["series_name"] for r in page2}
        assert len(p2_series) == 2
        assert not (p1_series & p2_series), "Pages should not overlap series"

        # Page 3: limit 2, offset 4 -> 1 item remaining
        page3 = db.get_discovery_releases(limit=2, offset=4)
        assert len(page3) == 1, f"Expected 1 item on page 3, got {len(page3)}"

        # 3. Canonical selection should prefer release with synopsis / cover
        all_disc = db.get_discovery_releases(limit=10, offset=0)
        for r in all_disc:
            assert r["description"].startswith("Synopsis for"), f"Should pick release with description for {r['series_name']}"

        # 4. Search filtering
        frieren_count = db.count_discovery_releases(search="frieren")
        assert frieren_count == 1, f"Expected 1 matching series for 'frieren', got {frieren_count}"
        frieren_disc = db.get_discovery_releases(search="frieren")
        assert len(frieren_disc) == 1
        assert frieren_disc[0]["series_name"] == "Frieren"

        # 5. get_release_by_id
        target_id = frieren_disc[0]["id"]
        rel_by_id = db.get_release_by_id(target_id)
        assert rel_by_id is not None
        assert rel_by_id["series_name"] == "Frieren"

        print("    [PASSED] Paginated discovery, series deduplication, and search filtering verified.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_webhook_notifications_service():
    print("--> Testing Webhook Notification System (Discord & Generic)...")
    from app.notifier.webhook import NotificationService, SimpleHttpResponse

    posted_payloads = []

    def mock_post_json(url, json_data, timeout=10.0):
        posted_payloads.append({"url": url, "data": json_data})
        return SimpleHttpResponse(204, "")

    with mock.patch("app.notifier.webhook._post_json", side_effect=mock_post_json):
        service = NotificationService(
            discord_webhook="https://discord.com/api/webhooks/test/123",
            generic_webhook="https://notify.home.arpa/webhook"
        )

        sample_release = {
            "title": "[Manga] Chainsaw Man Vol. 12 [VIZ]",
            "series_name": "Chainsaw Man",
            "volume_start": 12.0,
            "volume_end": 12.0,
            "is_bundle": False,
            "link": "https://nyaa.si/view/123456",
            "cover_url": "https://example.com/csm.jpg",
            "source_feed_name": "Nyaa Manga",
            "release_group": "VIZ"
        }

        # 1. Test notify_new_request
        service.notify_new_request(sample_release)
        assert len(posted_payloads) == 2, f"Expected 2 dispatches (discord + generic), got {len(posted_payloads)}"
        
        discord_call = next(p for p in posted_payloads if "discord" in p["url"])
        generic_call = next(p for p in posted_payloads if "notify.home.arpa" in p["url"])

        # Verify Discord embed
        d_embed = discord_call["data"]["embeds"][0]
        assert "Chainsaw Man" in d_embed["title"]
        assert d_embed["color"] == 0xf59e0b # Amber
        assert d_embed["thumbnail"]["url"] == "https://example.com/csm.jpg"
        assert any(f["name"] == "Series" and f["value"] == "Chainsaw Man" for f in d_embed["fields"])

        # Verify Generic payload
        assert generic_call["data"]["event"] == "NEW_REQUEST"
        assert generic_call["data"]["release"]["series_name"] == "Chainsaw Man"

        posted_payloads.clear()

        # 2. Test notify_acquired
        service.notify_acquired(sample_release)
        assert len(posted_payloads) == 2
        d_embed_acq = next(p for p in posted_payloads if "discord" in p["url"])["data"]["embeds"][0]
        assert "Added to Calibre" in d_embed_acq["title"]
        assert d_embed_acq["color"] == 0x10b981 # Emerald

        posted_payloads.clear()

        # 3. Test test_webhook diagnostics
        test_result = service.test_webhook()
        assert test_result["status"] == "success"
        assert "discord" in test_result["results"]
        assert test_result["results"]["discord"]["success"] is True
        assert "generic" in test_result["results"]
        assert test_result["results"]["generic"]["success"] is True

        print("    [PASSED] Webhook notifications for requests, acquisitions, and test diagnostics verified.")

def test_selective_volume_download():
    print("--> Testing Selective Volume Download for Bundle Torrents...")
    from app.downloader.qbit import QBitClient

    qbit = QBitClient(enabled=True)

    # 1. Test hash extraction from magnet links (both 40-char hex and 32-char base32)
    hex_magnet = "magnet:?xt=urn:btih:3b53f6396e952934cdcfd93ad987085c8e3efcfd&dn=Test"
    h1 = qbit.extract_torrent_hash(hex_magnet)
    assert h1 == "3b53f6396e952934cdcfd93ad987085c8e3efcfd"

    b32_magnet = "magnet:?xt=urn:btih:4NRM4NZK5WUVL2XGXZZPXG6J2BTRUUBB&dn=Test"
    h2 = qbit.extract_torrent_hash(b32_magnet)
    assert h2 == "e362ce372aeda955eae6be72fb9bc9d0671a5021"

    # 2. Test selective download on a multi-file bundle torrent (v01 through v05)
    mock_files = [
        {"index": 0, "name": "Chainsaw Man/Chainsaw Man v01 (Digital).cbz", "size": 1000, "priority": 1},
        {"index": 1, "name": "Chainsaw Man/Chainsaw Man v02 (Digital).cbz", "size": 1000, "priority": 1},
        {"index": 2, "name": "Chainsaw Man/Chainsaw Man v03 (Digital).cbz", "size": 1000, "priority": 1},
        {"index": 3, "name": "Chainsaw Man/Chainsaw Man v04 (Digital).cbz", "size": 1000, "priority": 1},
        {"index": 4, "name": "Chainsaw Man/Chainsaw Man v05 (Digital).cbz", "size": 1000, "priority": 1},
        {"index": 5, "name": "Chainsaw Man/release_info.txt", "size": 50, "priority": 1}
    ]

    set_priority_calls = []

    with mock.patch.object(qbit, "get_torrent_files", return_value=mock_files):
        with mock.patch.object(qbit, "set_file_priorities", side_effect=lambda h, idxs, priority: set_priority_calls.append((h, idxs, priority)) or True):
            # User already has Volumes 1 & 2 in Calibre; only wants 3, 4, 5
            wanted_vols = [3.0, 4.0, 5.0]
            res = qbit.apply_selective_volume_download(h1, wanted_vols)

            assert res["status"] == "filtered"
            assert res["skipped_volumes"] == [1.0, 2.0]
            assert res["kept_volumes"] == [3.0, 4.0, 5.0]
            assert res["skipped_files"] == 3  # Vol 1, Vol 2, and release_info.txt

            # Verify set_file_priorities was invoked with priority=0 for unneeded files
            assert len(set_priority_calls) == 1
            call_hash, skipped_indices, prio = set_priority_calls[0]
            assert call_hash == h1
            assert set(skipped_indices) == {0, 1, 5}
            assert prio == 0

    # 3. Test single archive torrent (e.g. single 1GB .zip file)
    single_archive_files = [
        {"index": 0, "name": "Chainsaw_Man_Complete_v01-05.zip", "size": 5000000, "priority": 1}
    ]
    with mock.patch.object(qbit, "get_torrent_files", return_value=single_archive_files):
        res_single = qbit.apply_selective_volume_download(h1, [3.0, 4.0, 5.0])
        assert res_single["status"] == "single_archive"

    print("    [PASSED] Selective bundle volume filtering, priority deselecting, and hash extraction verified.")

def test_timezone_conversion_and_formatting():
    print("--> Testing Timezone Conversion (Europe/Copenhagen Winter & Summer DST)...")
    from app.timezone_util import format_localtime, format_localdate

    # Database stores UTC timestamps like '2026-10-31 10:53:44'
    # In winter time (CET, UTC+1), Copenhagen is 1 hour ahead of UTC:
    winter_utc = "2026-10-31 10:53:44"
    winter_local = format_localtime(winter_utc)
    assert winter_local == "2026-10-31 11:53", f"Expected '2026-10-31 11:53', got '{winter_local}'"

    # In summer time (CEST, UTC+2), Copenhagen is 2 hours ahead of UTC:
    summer_utc = "2026-06-15 10:53:44"
    summer_local = format_localtime(summer_utc)
    assert summer_local == "2026-06-15 12:53", f"Expected '2026-06-15 12:53', got '{summer_local}'"

    # Test format_localdate
    date_formatted = format_localdate(winter_utc)
    assert date_formatted == "2026-10-31"

    print("    [PASSED] Timezone conversion and automatic daylight saving time handling verified 100%.")

def test_discovery_vip_groups_and_full_data_reset():
    print("--> Testing Discovery VIP Group Matching & Full Data Reset...")
    temp_dir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(temp_dir, "test_reset.db")
        db = MangaDatabase(db_path)

        # 1. Insert discovery items: one with default VIP group, one unknown
        db.upsert_release({
            "guid": "vip-disc-1",
            "title": "[danke-Empire] Frieren Vol. 1",
            "series_name": "Frieren",
            "release_group": "danke-Empire",
            "volume_start": 1.0,
            "status": "DISCOVERY"
        })
        db.upsert_release({
            "guid": "vip-disc-2",
            "title": "[NewScanGroup] Dandadan Vol. 1",
            "series_name": "Dandadan",
            "release_group": "NewScanGroup",
            "volume_start": 1.0,
            "status": "DISCOVERY"
        })

        disc = db.get_discovery_releases()
        frieren = next(r for r in disc if r["series_name"] == "Frieren")
        dandadan = next(r for r in disc if r["series_name"] == "Dandadan")

        assert frieren["is_prioritized"] is True, "danke-Empire should be recognized as prioritized"
        assert dandadan["is_prioritized"] is False, "NewScanGroup is not yet prioritized"

        # Dynamically prioritize NewScanGroup (as if user clicked VIP button on Discovery)
        db.add_prioritized_group("NewScanGroup")
        disc_after = db.get_discovery_releases()
        dandadan_after = next(r for r in disc_after if r["series_name"] == "Dandadan")
        assert dandadan_after["is_prioritized"] is True, "NewScanGroup should now be prioritized"

        # 2. Add tracked series, blacklist, activity log, cache
        db.track_discovered_series("Frieren", 1)
        db.add_to_blacklist("Bad Manga", "Testing blacklist")
        db.log_activity("TEST_EVENT", "Sample message")
        db.cache_manga_metadata("sample_key", {"title": "Sample"})

        assert len(db.get_all_series()) > 0
        assert len(db.get_blacklisted_series()) > 0
        assert len(db.get_logs(limit=10)) > 0

        # 3. Trigger full application reset
        res = db.reset_all_data()
        assert res["status"] == "success"

        # Verify all data was completely wiped
        assert len(db.get_releases()) == 0
        assert len(db.get_all_series()) == 0
        assert len(db.get_blacklisted_series()) == 0
        assert len(db.get_cached_manga_metadata("sample_key") or {}) == 0
        
        # Verify default feeds and groups were cleanly restored
        feeds = db.get_all_feeds()
        assert len(feeds) == 1
        assert "Manga Releases" in feeds[0]["name"]

        groups = db.get_prioritized_groups()
        assert any(g["name"] == "danke-Empire" for g in groups)

        logs = db.get_logs(limit=10)
        assert len(logs) == 1
        assert logs[0]["event_type"] == "SYSTEM_RESET"

        print("    [PASSED] Discovery VIP group recognition and full application data wipe verified 100%.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    print("==================================================")
    print("Running MangaTrax Comprehensive Integration Suite")
    print("==================================================")
    test_manga_bundle_parsing()
    test_calibre_scanner_and_matcher()
    test_cbz_tagger_and_comicinfo()
    test_database_and_blacklist_workflow()
    test_series_grouping_and_metadata()
    test_chapters_and_mixed_updates()
    test_prioritized_release_groups()
    test_mangaupdates_and_rss_links()
    test_romaji_to_english_titles_conversion()
    test_nyaa_historical_crawl_and_series_backfill()
    test_in_app_bittorrent_downloader_setup()
    test_light_novel_filtering()
    test_paginated_discovery_and_deduplication()
    test_webhook_notifications_service()
    test_selective_volume_download()
    test_timezone_conversion_and_formatting()
    test_discovery_vip_groups_and_full_data_reset()
    print("==================================================")
    print("ALL TESTS PASSED SUCCESSFULLY! (100% VERIFIED)")
    print("==================================================")
