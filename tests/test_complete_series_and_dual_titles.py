import sys
import os
import tempfile
import json
import gc
from unittest.mock import patch

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

    # Hermetic: _process_release() enriches DISCOVERY releases with MangaUpdates synopsis
    # over HTTP. Stub that HTTP call -- otherwise this test hits the live API (slow, and it
    # fails/flakes on an offline machine). The canonical title/volume count come from the
    # seeded local AniList cache above, which is what this test is actually about.
    from app.metadata.mangaupdates import MangaUpdatesService
    with patch.object(MangaUpdatesService, "_fetch_from_api", return_value=None):
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

    titles = None
    # detect_series_titles() queries AniList for each candidate; stub the HTTP call so this
    # test only exercises the delimiter-based title detection (and stays offline/deterministic).
    from app.metadata.anilist import AniListMetadataService
    with patch.object(AniListMetadataService, "_fetch_from_anilist", return_value=None):
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

def test_reconciliation_23_of_24_volumes():
    import sqlite3
    from app.calibre.scanner import CalibreMangaScanner

    calibre_tmp = tempfile.mkdtemp()
    calibre_db_path = os.path.join(calibre_tmp, "metadata.db")

    conn = sqlite3.connect(calibre_db_path)
    cur = conn.cursor()
    # Complete, realistic Calibre metadata.db schema. The scanner joins ALL of these
    # link tables; the previous mock omitted the authors/publishers/tags tables, so
    # CalibreMangaScanner.get_all_series() raised (sqlite3.OperationalError: no such
    # table: books_authors_link), logged the error, returned [] -- and the DB sync it
    # feeds silently did nothing, so no Calibre ownership was ever recorded and the
    # reconciliation this test is about never ran. That was a test-fixture bug.
    cur.executescript("""
        CREATE TABLE books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            series_index REAL DEFAULT 1.0
        );
        CREATE TABLE series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE books_series_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            series INTEGER NOT NULL
        );
        CREATE TABLE authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE books_authors_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            author INTEGER NOT NULL
        );
        CREATE TABLE publishers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE books_publishers_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            publisher INTEGER NOT NULL
        );
        CREATE TABLE tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE books_tags_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            tag INTEGER NOT NULL
        );
    """)

    cur.execute("INSERT INTO series (id, name) VALUES (1, 'The Devil is a Part-Timer!');")
    cur.execute("INSERT INTO authors (id, name) VALUES (1, 'Satoshi Wagahara');")
    cur.execute("INSERT INTO publishers (id, name) VALUES (1, 'Yen Press');")
    cur.execute("INSERT INTO tags (id, name) VALUES (1, 'Manga');")
    cur.execute("INSERT INTO tags (id, name) VALUES (2, 'Comedy');")
    # Insert 23 volumes into Calibre (volumes 1 to 23)
    for v in range(1, 24):
        cur.execute("INSERT INTO books (id, title, series_index) VALUES (?, ?, ?);",
                    (v, f"The Devil is a Part-Timer!, Vol. {v}", float(v)))
        cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, 1);", (v,))
        cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, 1);", (v,))
        cur.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, 1);", (v,))
        cur.execute("INSERT INTO books_tags_link (book, tag) VALUES (?, 1);", (v,))
    conn.commit()
    conn.close()

    scanner = CalibreMangaScanner(calibre_tmp)
    volumes_map = scanner.get_all_volumes_per_series()

    # Verify volumes_map contains all 23 volumes under multiple lookup keys
    assert "the devil is a part-timer!" in volumes_map
    assert len(volumes_map["the devil is a part-timer!"]) == 23
    assert 24.0 not in volumes_map["the devil is a part-timer!"]

    # Initialize MangaTrax DB
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tf.close()
    db = MangaDatabase(tf.name)
    db.init_db()

    # Sync Calibre library series and volumes_map to database
    series_list = scanner.get_all_series()
    db.sync_calibre_series(series_list, volumes_map=volumes_map)

    # Insert 24-volume complete release (simulate previously saved release with empty or un-reconciled missing_volumes)
    db.upsert_release({
        "guid": "devil_part_timer_complete_24",
        "title": "Hataraku Maou-sama! | The Devil is a Part-Timer! (2015-2026) (Digital) (1r0n, Ushi) (complete)",
        "series_name": "Hataraku Maou sama! | The Devil is a Part Timer!",
        "status": "REQUESTED",
        "release_group": "1r0n, Ushi",
        "is_complete": 1,
        "volume_start": 1.0,
        "volume_end": 24.0,
        "volumes": [float(v) for v in range(1, 25)],
        "missing_volumes": [float(v) for v in range(1, 25)]  # Initially all 24 were set missing
    })

    # Query grouped releases (web UI call)
    groups = db.get_releases_grouped_by_series()
    assert len(groups) == 1
    g = groups[0]

    # Verify all 24 volumes are in all_volumes, but ONLY Volume 24 is in missing_volumes
    assert len(g["all_volumes"]) == 24
    assert g["all_volumes"][0] == 1.0
    assert g["all_volumes"][-1] == 24.0
    assert g["missing_volumes"] == [24.0], f"Expected missing_volumes == [24.0], got {g['missing_volumes']}"

    rel = g["releases"][0]
    assert rel["missing_volumes"] == [24.0]
    assert rel["status"] == "REQUESTED"

    # Verify that Volumes 1 through 23 are recognized as owned
    owned_in_group = [v for v in g["all_volumes"] if v not in g["missing_volumes"]]
    assert len(owned_in_group) == 23
    assert 24.0 not in owned_in_group

    del db
    gc.collect()
    try:
        os.unlink(tf.name)
        for f in os.listdir(calibre_tmp):
            os.unlink(os.path.join(calibre_tmp, f))
        os.rmdir(calibre_tmp)
    except Exception:
        pass
    print(" [PASSED] Reconciliation of 23 of 24 volumes against Calibre library verified.")

def test_anilist_rate_limiter():
    import time
    from app.metadata.anilist import AniListRateLimiter

    # 1. Test sliding window rate limit
    limiter = AniListRateLimiter(max_requests=3, time_window=0.4)
    start_time = time.time()
    for _ in range(4):
        limiter.wait_if_needed()
    elapsed = time.time() - start_time
    assert elapsed >= 0.35, f"Limiter did not pause for rolling window capacity, elapsed: {elapsed:.3f}s"

    # 2. Test 429 Retry-After handling
    limiter_429 = AniListRateLimiter(max_requests=10, time_window=1.0)
    limiter_429.handle_rate_limit_error({"Retry-After": "0.3"})
    assert limiter_429.retry_until > time.time()
    start_cooldown = time.time()
    limiter_429.wait_if_needed()
    cooldown_elapsed = time.time() - start_cooldown
    assert cooldown_elapsed >= 0.25, f"Cooldown failed to pause execution, elapsed: {cooldown_elapsed:.3f}s"

    # 3. Test remaining quota header inspection
    limiter_pacing = AniListRateLimiter(max_requests=90, time_window=60.0)
    start_pacing = time.time()
    limiter_pacing.handle_response_headers({"X-RateLimit-Remaining": "1"})
    pacing_elapsed = time.time() - start_pacing
    assert pacing_elapsed >= 0.9, f"Pacing failed to throttle when remaining quota <= 2, elapsed: {pacing_elapsed:.3f}s"

    print(" [PASSED] AniList thread-safe sliding-window rate limiter & 429 backoff verified.")

def test_dual_title_delimiter_preservation():
    from app.metadata.anilist import AniListMetadataService
    from app.parser import extract_manga_bundle_and_volumes

    # Verify parser extracts series name with delimiters intact
    raw = "[nao] Class de 2 Banme ni Kawaii Onna no Ko to Tomodachi ni Natta / I Made Friends with the Second Prettiest Girl in My Class - Vol 3"
    info = extract_manga_bundle_and_volumes(raw)
    assert "/" in info["series_name"]
    assert " / " in info["series_name"]

    # Verify resolve_manga_series fallback preserves original delimiters without clobbering into concatenated string.
    # Hermetic contract: metadata HTTP must be stubbed, else the result depends on whether
    # the machine has network access. Without this stub the live AniList query returns the
    # English title with source="anilist", and the assertion only "passed" offline.
    service = AniListMetadataService()
    with patch.object(AniListMetadataService, "_fetch_from_anilist", return_value=None):
        fallback_res = service.resolve_manga_series(info["series_name"])
    assert "/" in fallback_res["canonical_title"], f"Expected slash delimiter preserved, got: {fallback_res['canonical_title']}"
    assert fallback_res["source"] == "fallback"

    print(" [PASSED] Dual title delimiter preservation in parser & metadata fallback verified.")

def test_exact_match_priority_over_substring():
    from app.parser import match_manga_series_in_calibre

    calibre_library = [
        {"series_id": 1, "name": "Devil May Cry", "series_name": "Devil May Cry", "book_count": 69},
        {"series_id": 2, "name": "The Devil Is a Part-Timer!", "series_name": "The Devil Is a Part-Timer!", "book_count": 23}
    ]

    query_title = "Hataraku Maou-sama! | The Devil is a Part-Timer!"
    matched = match_manga_series_in_calibre(query_title, calibre_library)

    assert matched is not None, "Expected match in Calibre library"
    assert matched["name"] == "The Devil Is a Part-Timer!", f"Wrong series matched: {matched['name']}"
    assert matched["book_count"] == 23, f"Expected 23 book count, got: {matched['book_count']}"

    print(" [PASSED] Calibre matching exact candidate part priority over false substring verified.")

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
    test_reconciliation_23_of_24_volumes()
    test_anilist_rate_limiter()
    test_dual_title_delimiter_preservation()
    test_exact_match_priority_over_substring()
    print("==================================================")
    print("ALL COMPLETE SERIES AND DUAL TITLE TESTS PASSED!")
    print("==================================================\n")
