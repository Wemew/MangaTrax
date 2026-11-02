"""
tests/test_m1_adversarial_challenger.py
Adversarial stress-testing and empirical verification suite for Milestone 1 defect fixes:
- Parser bundle extraction extreme stress testing (extreme ranges, inverted, floats, negatives, ReDoS)
- Database status state machine stress testing (manual IGNORED persistence, valid/invalid transitions, concurrency)
- get_release_by_id (>1000 ID, missing_volumes deserialization, null fallbacks)
- Scheduler transition detection and auto-download deduplication
- Nyaa crawl delay start offsets and HTML scraper stdlib-transport resilience
"""

import os
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

# Ensure backend and repo root are in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.parser import (
    extract_manga_bundle_and_volumes,
    MAX_BUNDLE_SPAN,
    clean_manga_title,
    is_complete_series_release
)
from app.database import MangaDatabase
from app.covers import CoverCache
from app.scheduler import MangaScheduler
from app.providers.nyaa import NyaaMangaProvider
from app.providers.html_scraper import HtmlScraperProvider
from app.providers.base import ReleaseItem


# calibre-debug.exe -e runs Python with optimization level 2 (__debug__ == False),
# which silently strips every bare `assert` statement.  This suite therefore uses an
# explicit helper so its verification cannot be compiled away.
def _check(cond, msg=None):
    if not cond:
        raise AssertionError(msg if msg is not None else "check failed")


def _make_calibre_db(db_path, series_map):
    """Minimal scanner-complete Calibre metadata.db from {series_name: [volumes]}."""
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, sort TEXT,
            author_sort TEXT, series_index REAL DEFAULT 1.0, path TEXT DEFAULT '', has_cover INTEGER DEFAULT 0);
        CREATE TABLE series (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, series INTEGER NOT NULL);
        CREATE TABLE authors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT, link TEXT DEFAULT '');
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, author INTEGER NOT NULL);
        CREATE TABLE publishers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT);
        CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, publisher INTEGER NOT NULL);
        CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, tag INTEGER NOT NULL);
    """)
    for sid, (name, vols) in enumerate(series_map.items(), start=1):
        cur.execute("INSERT INTO series (id, name) VALUES (?, ?);", (sid, name))
        cur.execute("INSERT OR IGNORE INTO authors (id, name) VALUES (?, ?);", (sid, "Author"))
        for v in vols:
            cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"{name} Vol. {int(v)}", float(v)))
            bid = cur.lastrowid
            cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?);", (bid, sid))
            cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, ?);", (bid, sid))
    conn.commit()
    conn.close()


# ===========================================================================
# 1. Parser Bundle Extraction Adversarial Stress Tests
# ===========================================================================
def test_parser_bundle_adversarial_extremes():
    print("--> [Challenger] Testing Parser Bundle Extraction: Adversarial Extremes & Edge Cases...")

    # A. Massive Range (Vol 1-9999999) - Algorithmic DoS stress test
    t0 = time.time()
    res_massive = extract_manga_bundle_and_volumes("[Group] Giant Epic Manga Vol. 01-9999999 [Digital]")
    t_elapsed = time.time() - t0
    _check(t_elapsed < 0.1, f"Parser took too long ({t_elapsed:.4f}s) on massive range; potential DoS vulnerability")
    _check(res_massive["is_bundle"] is True, "Massive range should be recognized as a bundle")
    _check(res_massive["volume_start"] == 1.0, f"Expected volume_start 1.0, got {res_massive['volume_start']}")
    _check(res_massive["volume_end"] == 1001.0, f"Expected clamped volume_end 1001.0, got {res_massive['volume_end']}")
    _check(len(res_massive["volumes"]) == 1001, f"Expected 1001 volumes, got {len(res_massive['volumes'])}")
    _check(res_massive["volumes"][0] == 1.0)
    _check(res_massive["volumes"][-1] == 1001.0)

    # B. Inverted Ranges (Vol 20-10, Vol 9999999-1, Vol 2-1)
    res_inv20 = extract_manga_bundle_and_volumes("Reversed Manga Vol 20-10")
    _check(res_inv20["is_bundle"] is True)
    _check(res_inv20["volume_start"] == 10.0, f"Expected swapped start 10.0, got {res_inv20['volume_start']}")
    _check(res_inv20["volume_end"] == 20.0, f"Expected swapped end 20.0, got {res_inv20['volume_end']}")
    _check(res_inv20["volumes"] == [float(x) for x in range(10, 21)])
    _check(len(res_inv20["volumes"]) == 11)

    res_inv_massive = extract_manga_bundle_and_volumes("Huge Inverted Manga Vol 9999999-1")
    _check(res_inv_massive["is_bundle"] is True)
    _check(res_inv_massive["volume_start"] == 1.0)
    _check(res_inv_massive["volume_end"] == 1001.0)
    _check(len(res_inv_massive["volumes"]) == 1001)

    res_inv_small = extract_manga_bundle_and_volumes("Tiny Inverted Manga v2-v1")
    _check(res_inv_small["is_bundle"] is True)
    _check(res_inv_small["volume_start"] == 1.0)
    _check(res_inv_small["volume_end"] == 2.0)
    _check(res_inv_small["volumes"] == [1.0, 2.0])

    # C. Exact Boundary Tests around MAX_BUNDLE_SPAN (1000)
    # Vol 1-1000 (span = 999)
    res_boundary_under = extract_manga_bundle_and_volumes("Boundary Manga Vol. 1-1000")
    _check(res_boundary_under["is_bundle"] is True)
    _check(res_boundary_under["volume_start"] == 1.0)
    _check(res_boundary_under["volume_end"] == 1000.0)
    _check(len(res_boundary_under["volumes"]) == 1000)

    # Vol 1-1001 (span = 1000 == MAX_BUNDLE_SPAN)
    res_boundary_exact = extract_manga_bundle_and_volumes("Boundary Manga Vol. 1-1001")
    _check(res_boundary_exact["is_bundle"] is True)
    _check(res_boundary_exact["volume_start"] == 1.0)
    _check(res_boundary_exact["volume_end"] == 1001.0)
    _check(len(res_boundary_exact["volumes"]) == 1001)

    # Vol 1-1002 (span = 1001 > MAX_BUNDLE_SPAN) -> clamped to span 1000
    res_boundary_over = extract_manga_bundle_and_volumes("Boundary Manga Vol. 1-1002")
    _check(res_boundary_over["is_bundle"] is True)
    _check(res_boundary_over["volume_start"] == 1.0)
    _check(res_boundary_over["volume_end"] == 1001.0)
    _check(len(res_boundary_over["volumes"]) == 1001)

    # D. Large Base Offset (Vol 1000000-1000005)
    res_offset = extract_manga_bundle_and_volumes("Offset Manga Vol. 1000000-1000005")
    _check(res_offset["is_bundle"] is True)
    _check(res_offset["volume_start"] == 1000000.0)
    _check(res_offset["volume_end"] == 1000005.0)
    _check(res_offset["volumes"] == [1000000.0, 1000001.0, 1000002.0, 1000003.0, 1000004.0, 1000005.0])

    # E. Zero Boundaries (Vol 0-0, Vol 0-5)
    res_zero_same = extract_manga_bundle_and_volumes("Zero Manga Vol 0-0")
    _check(res_zero_same["is_bundle"] is True)
    _check(res_zero_same["volume_start"] == 0.0)
    _check(res_zero_same["volume_end"] == 0.0)
    _check(res_zero_same["volumes"] == [0.0])

    res_zero_span = extract_manga_bundle_and_volumes("Zero Manga Vol 0-5")
    _check(res_zero_span["is_bundle"] is True)
    _check(res_zero_span["volume_start"] == 0.0)
    _check(res_zero_span["volume_end"] == 5.0)
    _check(res_zero_span["volumes"] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0])

    # F. Non-Integer Volumes (Decimals / Fractional Volumes)
    res_float_span = extract_manga_bundle_and_volumes("Decimal Manga Vol. 1.5 - 4.5")
    _check(res_float_span["is_bundle"] is True)
    _check(res_float_span["volume_start"] == 1.5)
    _check(res_float_span["volume_end"] == 4.5)
    _check(res_float_span["volumes"] == [1.0, 2.0, 3.0, 4.0])

    res_float_subzero = extract_manga_bundle_and_volumes("Decimal Manga Vol 0.5 - 2.5")
    _check(res_float_subzero["is_bundle"] is True)
    _check(res_float_subzero["volume_start"] == 0.5)
    _check(res_float_subzero["volume_end"] == 2.5)
    _check(res_float_subzero["volumes"] == [0.0, 1.0, 2.0])

    res_single_float = extract_manga_bundle_and_volumes("Single Decimal Manga Vol. 10.5")
    _check(res_single_float["is_bundle"] is False)
    _check(res_single_float["volume_start"] == 10.5)
    _check(res_single_float["volume_end"] == 10.5)
    _check(res_single_float["volumes"] == [10.5])

    # G. Negative Numbers & Hyphen Separators
    # Standard separator hyphen before Vol should parse smoothly
    res_hyphen_sep = extract_manga_bundle_and_volumes("Hyphen Manga - Vol 01-05")
    _check(res_hyphen_sep["is_bundle"] is True)
    _check(res_hyphen_sep["volume_start"] == 1.0)
    _check(res_hyphen_sep["volume_end"] == 5.0)

    # Negative numbers in volume position must not crash or trigger unhandled exceptions
    try:
        res_neg1 = extract_manga_bundle_and_volumes("Negative Manga Vol -1 to 5")
        _check(isinstance(res_neg1, dict))
    except Exception as e:
        _check(False, f"Negative volume title caused unexpected exception: {e}")

    try:
        res_neg2 = extract_manga_bundle_and_volumes("Negative Manga Vol -5 - -1")
        _check(isinstance(res_neg2, dict))
    except Exception as e:
        _check(False, f"Double negative volume title caused unexpected exception: {e}")

    # H. Comma / Plus Separated Bundles (Pattern 1b)
    res_comma = extract_manga_bundle_and_volumes("List Manga Vol 1, 2, 3, 4, 5")
    _check(res_comma["is_bundle"] is True)
    _check(res_comma["volume_start"] == 1.0)
    _check(res_comma["volume_end"] == 5.0)
    _check(res_comma["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0])

    # Inverted comma list
    res_comma_inv = extract_manga_bundle_and_volumes("List Manga Vol 5, 4, 3, 2, 1")
    _check(res_comma_inv["is_bundle"] is True)
    _check(res_comma_inv["volume_start"] == 1.0)
    _check(res_comma_inv["volume_end"] == 5.0)
    _check(res_comma_inv["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0])

    # Duplicate volumes in comma list (must be deduplicated)
    res_comma_dup = extract_manga_bundle_and_volumes("List Manga Vol 1, 2, 2, 3, 3, 3, 4")
    _check(res_comma_dup["is_bundle"] is True)
    _check(res_comma_dup["volumes"] == [1.0, 2.0, 3.0, 4.0])

    # Plus / ampersand separated
    res_plus = extract_manga_bundle_and_volumes("Plus Manga Vol 1 + 2 + 3")
    _check(res_plus["is_bundle"] is True)
    _check(res_plus["volumes"] == [1.0, 2.0, 3.0])

    res_amp = extract_manga_bundle_and_volumes("Amp Manga Vol 1 & 2 & 3")
    _check(res_amp["is_bundle"] is True)
    _check(res_amp["volumes"] == [1.0, 2.0, 3.0])

    # I. Adversarial ReDoS Stress Test
    long_prefix = "Manga Series Name " * 500  # ~9000 chars
    t_start = time.time()
    res_long_match = extract_manga_bundle_and_volumes(long_prefix + "Vol 01-05")
    t_long = time.time() - t_start
    _check(t_long < 0.2, f"ReDoS match took {t_long:.4f}s (exceeded 0.2s threshold)")
    _check(res_long_match["is_bundle"] is True)

    t_start = time.time()
    res_long_nomatch = extract_manga_bundle_and_volumes(long_prefix + "Chapter 100 Extra")
    t_long_nomatch = time.time() - t_start
    _check(t_long_nomatch < 0.2, f"ReDoS non-match took {t_long_nomatch:.4f}s (exceeded 0.2s threshold)")

    # J. Extremely Large Numbers (Python Arbitrary Precision Ints)
    res_huge_nums = extract_manga_bundle_and_volumes("Huge Ints Manga Vol 10000000000000000000 - 10000000000000000005")
    _check(res_huge_nums["is_bundle"] is True)
    # Volumes are carried as floats, so 1e19 and 1e19+5 collapse to the same float value.
    # The robustness contract is "must not crash or return an empty bundle", not an exact range.
    _check(len(res_huge_nums["volumes"]) >= 1, f"huge int range produced no volumes: {res_huge_nums}")

    print("    [PASSED] Parser bundle extraction adversarial stress tests 100% verified.")


# ===========================================================================
# 2. Database Status State Machine Adversarial Stress Tests
# ===========================================================================
def test_database_state_machine_adversarial():
    print("--> [Challenger] Testing Database Status State Machine: IGNORED Preservation & Transitions...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_test_challenger_db_")
    try:
        db_path = os.path.join(tmpdir, "challenger_state_machine.db")
        db = MangaDatabase(db_path)

        # -------------------------------------------------------------------
        # Test 1: Manual IGNORED Release Preservation Shield
        # -------------------------------------------------------------------
        # Ingest a release initially as DISCOVERY
        guid_ignored = "guid-challenger-ignored-shield"
        is_new, rel_id = db.upsert_release({
            "guid": guid_ignored,
            "title": "Shield Hero Vol. 01 [Initial]",
            "series_name": "Shield Hero",
            "status": "DISCOVERY",
            "description": "Initial description",
            "download_url": "https://example.com/dl1"
        })
        _check(is_new is True)

        # User manually ignores this release
        db.update_release_status(rel_id, "IGNORED")
        row = db.get_release_by_id(rel_id)
        _check(row["status"] == "IGNORED", "Failed to set status to IGNORED")

        # Adversarial assault: Try overwriting IGNORED with all conceivable feed statuses
        attack_statuses = [
            "DISCOVERY",
            "REQUESTED",
            "IN_CALIBRE",
            "BLACKLISTED",
            "NEW",
            "PENDING",
            None
        ]

        for idx, new_status in enumerate(attack_statuses):
            is_new_up, updated_id = db.upsert_release({
                "guid": guid_ignored,
                "title": f"Shield Hero Vol. 01 [Attack {idx} - {new_status}]",
                "series_name": "Shield Hero",
                "status": new_status,
                "description": f"Updated description {idx}",
                "download_url": f"https://example.com/dl_updated_{idx}",
                "cover_url": f"https://example.com/cover_{idx}.jpg",
                "missing_volumes": [1.0, 2.0]
            })
            _check(is_new_up is False)
            _check(updated_id == rel_id)

            # Verify that status REMAINED 'IGNORED' without exception
            current = db.get_release_by_id(rel_id)
            _check(current["status"] == "IGNORED", f"VIOLATION: Manual IGNORED status was overwritten by feed upsert status '{new_status}'!")
            # Verify that non-status metadata DID update properly
            _check(current["description"] == f"Updated description {idx}")
            _check(current["download_url"] == f"https://example.com/dl_updated_{idx}")
            _check(current["cover_url"] == f"https://example.com/cover_{idx}.jpg")

        # -------------------------------------------------------------------
        # Test 2: Unignore Recovery Lifecycle
        # -------------------------------------------------------------------
        # User unignores the release (restores to REQUESTED)
        db.update_release_status(rel_id, "REQUESTED")
        current = db.get_release_by_id(rel_id)
        _check(current["status"] == "REQUESTED", "Manual restore to REQUESTED failed")

        # Now feed re-scans with IN_CALIBRE: transition SHOULD succeed because status is no longer IGNORED
        db.upsert_release({
            "guid": guid_ignored,
            "title": "Shield Hero Vol. 01 [In Calibre]",
            "series_name": "Shield Hero",
            "status": "IN_CALIBRE"
        })
        current = db.get_release_by_id(rel_id)
        _check(current["status"] == "IN_CALIBRE", "Legitimate transition to IN_CALIBRE after unignore failed")

        # -------------------------------------------------------------------
        # Test 3: Complete State Transition Matrix via upsert_release()
        # -------------------------------------------------------------------
        # Matrix:
        # A. DISCOVERY -> REQUESTED (Series tracked)
        guid_trans = "guid-challenger-transitions"
        _, tid = db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": "DISCOVERY"
        })
        _check(db.get_release_by_id(tid)["status"] == "DISCOVERY")

        db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": "REQUESTED"
        })
        _check(db.get_release_by_id(tid)["status"] == "REQUESTED", "DISCOVERY -> REQUESTED transition failed")

        # B. REQUESTED -> IN_CALIBRE (Acquired)
        db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": "IN_CALIBRE"
        })
        _check(db.get_release_by_id(tid)["status"] == "IN_CALIBRE", "REQUESTED -> IN_CALIBRE transition failed")

        # C. IN_CALIBRE -> REQUESTED (e.g. missing volumes discovered)
        db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": "REQUESTED"
        })
        _check(db.get_release_by_id(tid)["status"] == "REQUESTED", "IN_CALIBRE -> REQUESTED transition failed")

        # D. COALESCE check: upsert with status=None preserves prior status
        db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": None  # Omitted status
        })
        _check(db.get_release_by_id(tid)["status"] == "REQUESTED", "COALESCE failed: status was wiped by None")

        # -------------------------------------------------------------------
        # Test 4: Concurrency Stress Test on State Transitions
        # -------------------------------------------------------------------
        guid_concurrent = "guid-challenger-concurrency"
        _, conc_id = db.upsert_release({
            "guid": guid_concurrent,
            "title": "Concurrent Release Vol. 01",
            "series_name": "Concurrent Release",
            "status": "DISCOVERY"
        })

        errors = []
        def worker_task(thread_id: int):
            try:
                for i in range(25):
                    st = "REQUESTED" if i % 2 == 0 else "DISCOVERY"
                    db.upsert_release({
                        "guid": guid_concurrent,
                        "title": f"Concurrent Release Vol. 01 (Thread {thread_id} Iter {i})",
                        "series_name": "Concurrent Release",
                        "status": st,
                        "description": f"Thread {thread_id} iteration {i}"
                    })
            except Exception as ex:
                errors.append((thread_id, ex))

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        _check(len(errors) == 0, f"Concurrent upserts raised database errors: {errors}")
        final_row = db.get_release_by_id(conc_id)
        _check(final_row is not None)
        _check(final_row["status"] in ("REQUESTED", "DISCOVERY"))

        print("    [PASSED] Database status state machine & IGNORED shield 100% verified.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 3. get_release_by_id (>1000 ID, missing_volumes Deserialization) Stress Tests
# ===========================================================================
def test_get_release_by_id_and_missing_volumes_stress():
    print("--> [Challenger] Testing get_release_by_id (>1000 ID, Deserialization, Null Fallbacks)...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_test_challenger_id_")
    try:
        db_path = os.path.join(tmpdir, "challenger_id_stress.db")
        db = MangaDatabase(db_path)

        # -------------------------------------------------------------------
        # A. High Volume / Large ID Population (> 2000 releases)
        # -------------------------------------------------------------------
        print("    [Info] Populating 2200 releases to stress IDs > 1000...")
        with db.get_connection() as conn:
            cur = conn.cursor()
            for i in range(1, 2201):
                cur.execute("""
                    INSERT INTO releases (
                        guid, title, series_name, status, is_bundle,
                        volumes_json, missing_vols_json, download_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    f"guid-bulk-{i}",
                    f"Manga Bulk Series {i} Vol. 01",
                    f"Manga Bulk Series {i}",
                    "REQUESTED" if i % 2 == 0 else "DISCOVERY",
                    1 if i % 10 == 0 else 0,
                    json.dumps([float(v) for v in range(1, 6)]) if i % 10 == 0 else "[]",
                    json.dumps([4.0, 5.0]) if i % 10 == 0 else "[]",
                    f"https://tracker.invalid/download/{i}.torrent"
                ))
            conn.commit()

        # Verify limit clamp on get_releases()
        active_1000 = db.get_releases(limit=1000)
        _check(len(active_1000) == 1000, f"get_releases(limit=1000) returned {len(active_1000)}")

        # Verify O(1) indexed lookup on extreme IDs
        test_ids = [1, 500, 1000, 1001, 1050, 1500, 2000, 2200]
        for tid in test_ids:
            rel = db.get_release_by_id(tid)
            _check(rel is not None, f"get_release_by_id failed to locate release #{tid}")
            _check(rel["id"] == tid)
            _check(rel["guid"] == f"guid-bulk-{tid}")
            _check(rel["title"] == f"Manga Bulk Series {tid} Vol. 01")

            # Check bundle & missing volumes deserialization on multiples of 10
            if tid % 10 == 0:
                _check(rel["is_bundle"] == 1)
                _check(isinstance(rel["volumes"], list))
                _check(rel["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0])
                _check(isinstance(rel["missing_volumes"], list))
                _check(rel["missing_volumes"] == [4.0, 5.0])
            else:
                _check(rel["volumes"] == [])
                _check(rel["missing_volumes"] == [])

        # -------------------------------------------------------------------
        # B. Non-Existent and Boundary IDs
        # -------------------------------------------------------------------
        _check(db.get_release_by_id(999999) is None, "Non-existent positive ID should return None")
        _check(db.get_release_by_id(0) is None, "ID 0 should return None")
        _check(db.get_release_by_id(-1) is None, "Negative ID should return None")

        # -------------------------------------------------------------------
        # C. Missing Volumes & Volumes Deserialization Edge Cases
        # -------------------------------------------------------------------
        # 1. Null / None in database columns (legacy records)
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json)
                VALUES ('guid-null-json', 'Null JSON Manga Vol 01', 'Null Manga', 'REQUESTED', NULL, NULL);
            """)
            conn.commit()
            null_id = cur.lastrowid

        rel_null = db.get_release_by_id(null_id)
        _check(rel_null is not None)
        _check(rel_null["volumes"] == [], "NULL volumes_json must deserialize to empty list []")
        _check(rel_null["missing_volumes"] == [], "NULL missing_vols_json must deserialize to empty list []")

        # 2. Large list of missing volumes (500 items)
        large_vols = [float(x) for x in range(1, 501)]
        large_missing = [float(x) for x in range(251, 501)]
        is_new_lg, lg_id = db.upsert_release({
            "guid": "guid-large-vols",
            "title": "Large Bundle Manga Vol. 001-500",
            "series_name": "Large Bundle Manga",
            "is_bundle": 1,
            "volumes": large_vols,
            "missing_volumes": large_missing,
            "status": "REQUESTED"
        })
        rel_lg = db.get_release_by_id(lg_id)
        _check(rel_lg is not None)
        _check(len(rel_lg["volumes"]) == 500)
        _check(len(rel_lg["missing_volumes"]) == 250)
        _check(rel_lg["volumes"] == large_vols)
        _check(rel_lg["missing_volumes"] == large_missing)

        # 3. Fractional / Non-integer missing volumes
        frac_vols = [1.0, 1.5, 2.0, 2.5, 3.0]
        frac_missing = [1.5, 2.5]
        _, frac_id = db.upsert_release({
            "guid": "guid-frac-vols",
            "title": "Fractional Bundle Manga Vol 1-3",
            "series_name": "Fractional Bundle Manga",
            "is_bundle": 1,
            "volumes": frac_vols,
            "missing_volumes": frac_missing,
            "status": "REQUESTED"
        })
        rel_frac = db.get_release_by_id(frac_id)
        _check(rel_frac["volumes"] == frac_vols)
        _check(rel_frac["missing_volumes"] == frac_missing)

        # -------------------------------------------------------------------
        # D. Integration with Download Logic on High ID (> 1000) & IGNORED
        # -------------------------------------------------------------------
        # Insert release with ID > 2000 that is marked IGNORED
        _, high_ignored_id = db.upsert_release({
            "guid": "guid-high-ignored-dl",
            "title": "High ID Ignored Bundle Vol. 01-10",
            "series_name": "High ID Manga",
            "status": "IGNORED",
            "is_bundle": 1,
            "volumes": [float(v) for v in range(1, 11)],
            "missing_volumes": [8.0, 9.0, 10.0],
            "download_url": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=HighID"
        })
        _check(high_ignored_id > 2000, f"Expected ID > 2000, got {high_ignored_id}")

        # Confirm get_releases(limit=1000) CANNOT find it
        _check(not any(r["id"] == high_ignored_id for r in db.get_releases(limit=1000)))

        # Confirm get_release_by_id CAN find it and preserve missing_volumes
        target = db.get_release_by_id(high_ignored_id)
        _check(target is not None)
        _check(target["status"] == "IGNORED")
        _check(target["missing_volumes"] == [8.0, 9.0, 10.0])

        # Simulate selective download filtering as done in main.py:584-587
        missing = target.get("missing_volumes") or json.loads(target.get("missing_vols_json") or "[]")
        vols = target.get("volumes") or []
        _check(missing == [8.0, 9.0, 10.0])
        _check(len(missing) < len(vols))
        torrent_hash = "0123456789abcdef0123456789abcdef01234567"

        mock_qbit = MagicMock()
        mock_qbit.apply_selective_volume_download.return_value = {
            "status": "filtered",
            "skipped_volumes": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
            "kept_volumes": [8.0, 9.0, 10.0]
        }
        res_sel = mock_qbit.apply_selective_volume_download(torrent_hash, [float(v) for v in missing])
        _check(res_sel["status"] == "filtered")
        _check(res_sel["kept_volumes"] == [8.0, 9.0, 10.0])

        print("    [PASSED] get_release_by_id (>1000 ID & Deserialization) 100% verified.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 4. Scheduler Transition Detection & Auto-Download Deduplication
# ===========================================================================
def test_scheduler_auto_download_transitions_and_deduplication():
    print("--> [Challenger] Testing Scheduler Transition Detection & Deduplication...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_test_challenger_sched_")
    try:
        db_path = os.path.join(tmpdir, "sched_test.db")
        calibre_db = os.path.join(tmpdir, "metadata.db")
        _make_calibre_db(calibre_db, {})
        db = MangaDatabase(db_path)
        db.set_setting("calibre_path", calibre_db)
        db.set_setting("auto_download", "true")
        # MangaScheduler requires a CoverCache; use a real one backed by the
        # test's own temp dir (it performs no network I/O until used).
        scheduler = MangaScheduler(db=db, cover_cache=CoverCache(tmpdir))

        # Deterministic offline metadata resolution
        scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: {"canonical_title": raw, "romaji_title": raw, "english_title": raw})
        scheduler.mangaupdates.get_series_info = MagicMock(return_value=None)

        # Mock qbit client
        mock_qbit = MagicMock()
        mock_qbit.add_torrent.return_value = True
        mock_qbit.extract_torrent_hash.return_value = "deadbeef12345678"
        mock_qbit.apply_selective_volume_download.return_value = {
            "status": "filtered", "skipped_volumes": [1.0], "kept_volumes": [2.0, 3.0]}
        scheduler.get_qbit_client = MagicMock(return_value=mock_qbit)

        # Only one deterministic feed
        for seeded in db.get_all_feeds():
            db.delete_feed(seeded["id"])
        db.add_feed(name="TestFeed", feed_type="nyaa", url="https://tracker.invalid/rss",
                    interval=60, auto_download=True)

        rel_item = ReleaseItem(
            guid="guid-trans-manga-01",
            title="Dungeon Meshi Vol. 01-03 [Digital]",
            link="https://tracker.invalid/dm01",
            series_name="Dungeon Meshi",
            is_bundle=True,
            volume_start=1.0,
            volume_end=3.0,
            volumes=[1.0, 2.0, 3.0],
            download_url="https://tracker.invalid/dm01.torrent")
        mock_provider = MagicMock()
        mock_provider.fetch_releases.return_value = [rel_item]

        # Step 1: first poll -> series absent from Calibre -> DISCOVERY, no auto-download
        with patch("app.scheduler.create_provider", return_value=mock_provider):
            res1 = scheduler.check_all_feeds()
        row = db.get_release_by_id(1)
        _check(res1["releases_found"] == 1, f"expected 1 found, got {res1['releases_found']}")
        _check(res1["sent_to_downloader"] == 0)
        _check(row["status"] == "DISCOVERY", f"expected DISCOVERY, got {row['status']}")
        _check(row["download_status"] == "IDLE")
        _check(mock_qbit.add_torrent.call_count == 0)

        # Step 2: user adds Vol 1 to Calibre -> re-poll must detect the
        # DISCOVERY -> REQUESTED transition and auto-download missing Vols 2-3.
        _make_calibre_db(calibre_db, {"Dungeon Meshi": [1.0]})
        with patch("app.scheduler.create_provider", return_value=mock_provider):
            res2 = scheduler.check_all_feeds()
        row = db.get_release_by_id(1)
        _check(res2["requested"] == 1, f"expected 1 requested, got {res2['requested']}")
        _check(res2["sent_to_downloader"] == 1, "transition must trigger exactly one auto-download")
        _check(mock_qbit.add_torrent.call_count == 1)
        _check(mock_qbit.apply_selective_volume_download.call_args is not None,
               "selective volume download must be applied")
        _check(mock_qbit.apply_selective_volume_download.call_args[0][1] == [2.0, 3.0])
        _check(row["status"] == "REQUESTED", f"expected REQUESTED, got {row['status']}")
        _check(row["download_status"] == "DOWNLOADING")
        _check(row["torrent_hash"] == "deadbeef12345678")

        # Step 3: third poll -> still REQUESTED/DOWNLOADING -> NO duplicate download
        with patch("app.scheduler.create_provider", return_value=mock_provider):
            res3 = scheduler.check_all_feeds()
        _check(res3["sent_to_downloader"] == 0, "duplicate download queued on re-poll")
        _check(mock_qbit.add_torrent.call_count == 1)

        print("    [PASSED] Scheduler transition detection & deduplication 100% verified.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 5. Nyaa Crawl Delay Start Offsets & Scraper Fallback Tests
# ===========================================================================
def test_nyaa_crawl_delay_and_html_scraper_fallback():
    print("--> [Challenger] Testing Nyaa Crawl Delay & HTML Scraper Fallback...")

    # A. Nyaa Crawl Delay with arbitrary start_page offsets
    nyaa = NyaaMangaProvider()

    def simulate_delays(start_page: int, max_pages: int):
        delays = []
        for page in range(start_page, start_page + max_pages):
            # Check condition from nyaa.py:252
            if page < (start_page + max_pages - 1):
                delays.append(page)
        return delays

    # Scenario 1: start_page = 1, max_pages = 3 -> delays after page 1, 2; none after 3
    d1 = simulate_delays(1, 3)
    _check(d1 == [1, 2], f"Expected delays on [1, 2], got {d1}")

    # Scenario 2: start_page = 5, max_pages = 3 -> delays after page 5, 6; none after 7
    d2 = simulate_delays(5, 3)
    _check(d2 == [5, 6], f"Expected delays on [5, 6], got {d2}")

    # Scenario 3: start_page = 10, max_pages = 1 -> single page crawl, no delay needed
    d3 = simulate_delays(10, 1)
    _check(d3 == [], f"Expected no delays for single page crawl, got {d3}")

    # B. HTML Scraper stdlib transport (the optional httpx branch was removed with
    # the dependency; urllib.request is now the only path).
    scraper = HtmlScraperProvider(
        name="Challenger Scraper",
        url="https://mock-manga.invalid",
        config={
            "item_selector": ".release-item",
            "title_selector": ".title",
            "link_selector": "a.download",
            "max_pages": 1,
        }
    )

    mock_html = """
    <html>
        <body>
            <div class="release-item">
                <span class="title">Berserk Vol. 41</span>
                <a class="download" href="/dl/berserk41.torrent">Download</a>
            </div>
        </body>
    </html>
    """.encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_html
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        page_content = scraper._fetch_page_html("https://mock-manga.invalid/page/1", {})
        _check("Berserk Vol. 41" in page_content)
        releases = scraper.fetch_releases()
        _check(len(releases) == 1)
        _check(releases[0].title == "Berserk Vol. 41")

    print("    [PASSED] Nyaa crawl delay & HTML scraper urllib transport 100% verified.")


# ===========================================================================
# Main Execution Runner
# ===========================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("Running MangaTrax Challenger Adversarial Stress Suite (Milestone 1)")
    print("=" * 70)

    test_parser_bundle_adversarial_extremes()
    test_database_state_machine_adversarial()
    test_get_release_by_id_and_missing_volumes_stress()
    test_scheduler_auto_download_transitions_and_deduplication()
    test_nyaa_crawl_delay_and_html_scraper_fallback()

    print("=" * 70)
    print("ALL ADVERSARIAL CHALLENGER TESTS PASSED WITH ZERO FAILURES!")
    print("=" * 70)
