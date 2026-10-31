import sys
import os
import tempfile
import json
import gc

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.parser import (
    is_complete_series_release,
    is_chapter_only,
    extract_manga_bundle_and_volumes
)
from app.database import MangaDatabase
from app.covers import CoverCache
from app.scheduler import MangaScheduler
from app.providers.base import ReleaseItem
from app.calibre.matcher import MangaCalibreMatcher

def test_complete_series_title_detection():
    test_cases = [
        ("Hataraku Maou-sama! | The Devil is a Part-Timer! (2015-2026) (Digital) (1r0n, Ushi) (complete)", True),
        ("Bleach [Complete Series] [Digital]", True),
        ("Fullmetal Alchemist (Completed) (CBZ)", True),
        ("Naruto (All Volumes) [Digital]", True),
        ("One Piece - 105", False),
        ("Chainsaw Man v01", False),
        ("Dandadan [01-02]", False),
    ]
    for title, expected in test_cases:
        assert is_complete_series_release(title) == expected, f"Failed for {title}"
        if expected:
            assert is_chapter_only(title) is False, f"Complete series {title} should never be chapter-only"
    print(" [PASSED] Complete series title detection and chapter bypass verified.")

def test_complete_series_bundle_extraction():
    raw_title = "Hataraku Maou-sama! | The Devil is a Part-Timer! (2015-2026) (Digital) (1r0n, Ushi) (complete)"
    parsed = extract_manga_bundle_and_volumes(raw_title)
    assert parsed["is_complete"] is True
    assert parsed["is_bundle"] is True
    assert parsed["volume_start"] == 1.0
    assert parsed["volume_end"] is None
    assert parsed["volumes"] == []
    assert parsed["release_group"] == "1r0n, Ushi"
    print(" [PASSED] Complete series bundle extraction and group isolation verified.")

def test_external_metadata_volume_resolution():
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tf.close()
    db = MangaDatabase(tf.name)
    db.init_db()

    # Seed AniList cache with 24 volumes for The Devil is a Part-Timer!
    db.cache_manga_metadata("hataraku maou-sama!", {
        "canonical_title": "The Devil is a Part-Timer!",
        "romaji_title": "Hataraku Maou-sama!",
        "total_volumes": 24,
        "status": "FINISHED"
    })

    covers_dir = tempfile.mkdtemp()
    cover_cache = CoverCache(covers_dir)
    scheduler = MangaScheduler(db, cover_cache)

    matcher = MangaCalibreMatcher([], {})
    rel = ReleaseItem(
        title="Hataraku Maou-sama! | The Devil is a Part-Timer! (2015-2026) (Digital) (1r0n, Ushi) (complete)",
        link="https://nyaa.si/view/2170324",
        series_name="Hataraku Maou-sama! | The Devil is a Part-Timer!",
        release_group="1r0n, Ushi",
        is_bundle=True,
        is_complete=True
    )

    is_new, status, missing, rel_id, canon = scheduler._process_release(rel, "Nyaa", matcher)

    assert status == "DISCOVERY"
    assert canon == "The Devil is a Part-Timer!"
    assert rel.volume_start == 1.0
    assert rel.volume_end == 24.0
    assert len(rel.volumes) == 24
    assert len(missing) == 24

    # Check DB record
    db_rel = db.get_release_by_id(rel_id)
    assert db_rel["is_complete"] == 1
    assert len(db_rel["volumes"]) == 24

    del db
    gc.collect()
    try:
        os.unlink(tf.name)
    except Exception:
        pass
    print(" [PASSED] External metadata volume expansion and DB persistence verified.")

def test_calibre_partial_ownership_for_complete_release():
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tf.close()
    db = MangaDatabase(tf.name)
    db.init_db()

    db.cache_manga_metadata("hataraku maou-sama!", {
        "canonical_title": "The Devil is a Part-Timer!",
        "romaji_title": "Hataraku Maou-sama!",
        "total_volumes": 24,
        "status": "FINISHED"
    })

    covers_dir = tempfile.mkdtemp()
    cover_cache = CoverCache(covers_dir)
    scheduler = MangaScheduler(db, cover_cache)

    owned = {float(v) for v in range(1, 11)}
    matcher = MangaCalibreMatcher(
        [{"name": "The Devil is a Part-Timer!", "book_count": 10}],
        {"the devil is a part-timer!": owned}
    )

    rel = ReleaseItem(
        title="Hataraku Maou-sama! | The Devil is a Part-Timer! (2015-2026) (Digital) (1r0n, Ushi) (complete)",
        link="https://nyaa.si/view/2170324",
        series_name="Hataraku Maou-sama! | The Devil is a Part-Timer!",
        release_group="1r0n, Ushi",
        is_bundle=True,
        is_complete=True
    )

    is_new, status, missing, rel_id, canon = scheduler._process_release(rel, "Nyaa", matcher)

    assert status == "REQUESTED"
    assert len(missing) == 14
    assert missing[0] == 11.0
    assert missing[-1] == 24.0

    del db
    gc.collect()
    try:
        os.unlink(tf.name)
    except Exception:
        pass
    print(" [PASSED] Partial Calibre ownership with selective complete release requesting verified.")

def test_completed_category_filter():
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tf.close()
    db = MangaDatabase(tf.name)
    db.init_db()

    db.upsert_release({
        "guid": "g1",
        "title": "Hataraku Maou-sama! | The Devil is a Part-Timer! (complete)",
        "series_name": "The Devil is a Part-Timer!",
        "is_complete": 1,
        "is_bundle": 1,
        "volumes": [float(v) for v in range(1, 25)],
        "status": "DISCOVERY"
    })
    db.upsert_release({
        "guid": "g2",
        "title": "Chainsaw Man v01",
        "series_name": "Chainsaw Man",
        "is_complete": 0,
        "is_bundle": 0,
        "volumes": [1.0],
        "status": "DISCOVERY"
    })

    completed_releases = db.get_releases(status="COMPLETED")
    assert len(completed_releases) == 1
    assert completed_releases[0]["guid"] == "g1"

    grouped = db.get_releases_grouped_by_series(status="COMPLETED")
    assert len(grouped) == 1
    assert grouped[0]["series_name"] == "The Devil is a Part-Timer!"
    assert grouped[0]["is_complete"] is True

    del db
    gc.collect()
    try:
        os.unlink(tf.name)
    except Exception:
        pass
    print(" [PASSED] Completed series category filter and grouping verified.")

def test_dual_titles_detection_during_backfill():
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tf.close()
    db = MangaDatabase(tf.name)
    db.init_db()
    covers_dir = tempfile.mkdtemp()
    scheduler = MangaScheduler(db, CoverCache(covers_dir))

    titles = scheduler.detect_series_titles("Class de 2 Banme ni Kawaii Onna no Ko to Tomodachi ni Natta | I Made Friends with the Second Prettiest Girl in My Class")
    assert len(titles) >= 2
    has_romaji = any("Class de 2 Banme" in t for t in titles)
    has_english = any("Second Prettiest Girl" in t for t in titles)
    assert has_romaji and has_english, f"Expected both titles in {titles}"

    del db
    gc.collect()
    try:
        os.unlink(tf.name)
    except Exception:
        pass
    print(" [PASSED] Dual title detection and backfill title isolation verified.")

def test_dynamic_expansion_of_unexpanded_releases():
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tf.close()
    db = MangaDatabase(tf.name)
    db.init_db()

    # Seed metadata for Hatamou Maou-sama
    db.cache_manga_metadata("hataraku maou sama!", {
        "canonical_title": "The Devil is a Part-Timer!",
        "romaji_title": "Hataraku Maou-sama!",
        "total_volumes": 24,
        "status": "FINISHED"
    })

    # Simulate release that was stored with empty volumes prior to complete compilation logic
    db.upsert_release({
        "guid": "test_unexpanded",
        "title": "Hataraku Maou-sama! | The Devil is a Part-Timer! (2015-2026) (Digital) (1r0n, Ushi) (complete)",
        "series_name": "Hataraku Maou sama! | The Devil is a Part Timer!",
        "status": "REQUESTED",
        "release_group": "1r0n, Ushi",
        "is_complete": 1,
        "volumes": []
    })

    # Query grouped releases
    groups = db.get_releases_grouped_by_series()
    assert len(groups) == 1
    g = groups[0]
    assert g["series_name"] == "The Devil is a Part-Timer!"
    assert len(g["all_volumes"]) == 24
    assert len(g["releases"][0]["volumes"]) == 24
    assert g["all_volumes"][0] == 1.0
    assert g["all_volumes"][-1] == 24.0

    # Ensure it was persisted to DB
    persisted = db.get_release_by_id(g["releases"][0]["id"])
    assert len(persisted["volumes"]) == 24
    assert persisted["volume_start"] == 1.0
    assert persisted["volume_end"] == 24.0

    del db
    gc.collect()
    try:
        os.unlink(tf.name)
    except Exception:
        pass
    print(" [PASSED] Dynamic expansion and DB persistence of legacy unexpanded complete releases verified.")

def test_fast_init_db_and_background_scheduler_expansion():
    import time
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tf.close()
    db = MangaDatabase(tf.name)
    db.init_db()

    # Pre-populate database with an unexpanded complete release
    db.upsert_release({
        "guid": "test_bg_expand",
        "title": "Hataraku Maou-sama! | The Devil is a Part-Timer! (Complete)",
        "series_name": "Hataraku Maou-sama! | The Devil is a Part-Timer!",
        "status": "DISCOVERY",
        "is_complete": 1,
        "volumes": []
    })

    # Cache metadata for The Devil is a Part-Timer!
    db.cache_manga_metadata("hataraku maou-sama!", {
        "canonical_title": "The Devil is a Part-Timer!",
        "romaji_title": "Hataraku Maou-sama!",
        "total_volumes": 24,
        "status": "FINISHED"
    })

    # Verify that init_db() runs virtually instantly with zero blocking network calls
    t0 = time.time()
    db.init_db()
    init_duration = time.time() - t0
    assert init_duration < 1.0, f"init_db took too long ({init_duration:.3f}s), should be instantaneous without network calls!"

    # Run background scheduler volume expansion
    covers_dir = tempfile.mkdtemp()
    cover_cache = CoverCache(covers_dir)
    scheduler = MangaScheduler(db, cover_cache)
    matcher = MangaCalibreMatcher([], {})

    expanded = scheduler.expand_unresolved_complete_releases(matcher=matcher)
    assert expanded >= 1, "Scheduler should have expanded the unexpanded complete release"

    # Verify that release in DB now has 24 volumes
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT volumes_json, volume_start, volume_end FROM releases WHERE guid = 'test_bg_expand';")
        row = cur.fetchone()
        vols = json.loads(row["volumes_json"])
        assert len(vols) == 24
        assert row["volume_start"] == 1.0
        assert row["volume_end"] == 24.0

    del db
    gc.collect()
    try:
        os.unlink(tf.name)
    except Exception:
        pass
    print(" [PASSED] Instant init_db and background scheduler expansion verified.")

if __name__ == "__main__":
    print("\n==================================================")
    print("Running MangaTrax Complete Series & Dual Title Suite")
    print("==================================================")
    test_complete_series_title_detection()
    test_complete_series_bundle_extraction()
    test_external_metadata_volume_resolution()
    test_calibre_partial_ownership_for_complete_release()
    test_completed_category_filter()
    test_dual_titles_detection_during_backfill()
    test_dynamic_expansion_of_unexpanded_releases()
    test_fast_init_db_and_background_scheduler_expansion()
    print("==================================================")
    print("ALL COMPLETE SERIES AND DUAL TITLE TESTS PASSED!")
    print("==================================================\n")
