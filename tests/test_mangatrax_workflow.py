import os
import sys
import tempfile
import zipfile
import shutil

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

    # Another bundle syntax
    r2 = extract_manga_bundle_and_volumes("Frieren - Beyond Journey's End v04-v06 (Digital) (1080p)")
    assert r2["is_bundle"] is True
    assert r2["volume_start"] == 4.0
    assert r2["volume_end"] == 6.0
    assert r2["volumes"] == [4.0, 5.0, 6.0]

    # Single Volume
    r3 = extract_manga_bundle_and_volumes("[Group] One Piece Vol. 105 [CBZ]")
    assert r3["is_bundle"] is False
    assert r3["series_name"] == "One Piece"
    assert r3["volume_start"] == 105.0

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

if __name__ == "__main__":
    print("==================================================")
    print("Running MangaTrax Comprehensive Integration Suite")
    print("==================================================")
    test_manga_bundle_parsing()
    test_calibre_scanner_and_matcher()
    test_cbz_tagger_and_comicinfo()
    test_database_and_blacklist_workflow()
    print("==================================================")
    print("ALL TESTS PASSED SUCCESSFULLY! (100% VERIFIED)")
    print("==================================================")
