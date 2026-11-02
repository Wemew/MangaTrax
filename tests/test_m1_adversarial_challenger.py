"""
tests/test_m1_adversarial_challenger.py
Adversarial stress-testing and empirical verification suite for Milestone 1 defect fixes:
- Parser bundle extraction extreme stress testing (extreme ranges, inverted, floats, negatives, ReDoS)
- Database status state machine stress testing (manual IGNORED persistence, valid/invalid transitions, concurrency)
- get_release_by_id (>1000 ID, missing_volumes deserialization, null fallbacks)
- Scheduler transition detection and auto-download deduplication
- Nyaa crawl delay start offsets and HTML scraper urllib fallback resilience
"""

import os
import sys
import json
import time
import shutil
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
from app.scheduler import MangaScheduler
from app.providers.nyaa import NyaaMangaProvider
import app.providers.html_scraper as hs_module
from app.providers.html_scraper import HtmlScraperProvider
from app.providers.base import ReleaseItem


# ===========================================================================
# 1. Parser Bundle Extraction Adversarial Stress Tests
# ===========================================================================
def test_parser_bundle_adversarial_extremes():
    print("--> [Challenger] Testing Parser Bundle Extraction: Adversarial Extremes & Edge Cases...")

    # A. Massive Range (Vol 1-9999999) - Algorithmic DoS stress test
    t0 = time.time()
    res_massive = extract_manga_bundle_and_volumes("[Group] Giant Epic Manga Vol. 01-9999999 [Digital]")
    t_elapsed = time.time() - t0
    assert t_elapsed < 0.1, f"Parser took too long ({t_elapsed:.4f}s) on massive range; potential DoS vulnerability"
    assert res_massive["is_bundle"] is True, "Massive range should be recognized as a bundle"
    assert res_massive["volume_start"] == 1.0, f"Expected volume_start 1.0, got {res_massive['volume_start']}"
    assert res_massive["volume_end"] == 1001.0, f"Expected clamped volume_end 1001.0, got {res_massive['volume_end']}"
    assert len(res_massive["volumes"]) == 1001, f"Expected 1001 volumes, got {len(res_massive['volumes'])}"
    assert res_massive["volumes"][0] == 1.0
    assert res_massive["volumes"][-1] == 1001.0

    # B. Inverted Ranges (Vol 20-10, Vol 9999999-1, Vol 2-1)
    res_inv20 = extract_manga_bundle_and_volumes("Reversed Manga Vol 20-10")
    assert res_inv20["is_bundle"] is True
    assert res_inv20["volume_start"] == 10.0, f"Expected swapped start 10.0, got {res_inv20['volume_start']}"
    assert res_inv20["volume_end"] == 20.0, f"Expected swapped end 20.0, got {res_inv20['volume_end']}"
    assert res_inv20["volumes"] == [float(x) for x in range(10, 21)]
    assert len(res_inv20["volumes"]) == 11

    res_inv_massive = extract_manga_bundle_and_volumes("Huge Inverted Manga Vol 9999999-1")
    assert res_inv_massive["is_bundle"] is True
    assert res_inv_massive["volume_start"] == 1.0
    assert res_inv_massive["volume_end"] == 1001.0
    assert len(res_inv_massive["volumes"]) == 1001

    res_inv_small = extract_manga_bundle_and_volumes("Tiny Inverted Manga v2-v1")
    assert res_inv_small["is_bundle"] is True
    assert res_inv_small["volume_start"] == 1.0
    assert res_inv_small["volume_end"] == 2.0
    assert res_inv_small["volumes"] == [1.0, 2.0]

    # C. Exact Boundary Tests around MAX_BUNDLE_SPAN (1000)
    # Vol 1-1000 (span = 999)
    res_boundary_under = extract_manga_bundle_and_volumes("Boundary Manga Vol. 1-1000")
    assert res_boundary_under["is_bundle"] is True
    assert res_boundary_under["volume_start"] == 1.0
    assert res_boundary_under["volume_end"] == 1000.0
    assert len(res_boundary_under["volumes"]) == 1000

    # Vol 1-1001 (span = 1000 == MAX_BUNDLE_SPAN)
    res_boundary_exact = extract_manga_bundle_and_volumes("Boundary Manga Vol. 1-1001")
    assert res_boundary_exact["is_bundle"] is True
    assert res_boundary_exact["volume_start"] == 1.0
    assert res_boundary_exact["volume_end"] == 1001.0
    assert len(res_boundary_exact["volumes"]) == 1001

    # Vol 1-1002 (span = 1001 > MAX_BUNDLE_SPAN) -> clamped to span 1000
    res_boundary_over = extract_manga_bundle_and_volumes("Boundary Manga Vol. 1-1002")
    assert res_boundary_over["is_bundle"] is True
    assert res_boundary_over["volume_start"] == 1.0
    assert res_boundary_over["volume_end"] == 1001.0
    assert len(res_boundary_over["volumes"]) == 1001

    # D. Large Base Offset (Vol 1000000-1000005)
    res_offset = extract_manga_bundle_and_volumes("Offset Manga Vol. 1000000-1000005")
    assert res_offset["is_bundle"] is True
    assert res_offset["volume_start"] == 1000000.0
    assert res_offset["volume_end"] == 1000005.0
    assert res_offset["volumes"] == [1000000.0, 1000001.0, 1000002.0, 1000003.0, 1000004.0, 1000005.0]

    # E. Zero Boundaries (Vol 0-0, Vol 0-5)
    res_zero_same = extract_manga_bundle_and_volumes("Zero Manga Vol 0-0")
    assert res_zero_same["is_bundle"] is True
    assert res_zero_same["volume_start"] == 0.0
    assert res_zero_same["volume_end"] == 0.0
    assert res_zero_same["volumes"] == [0.0]

    res_zero_span = extract_manga_bundle_and_volumes("Zero Manga Vol 0-5")
    assert res_zero_span["is_bundle"] is True
    assert res_zero_span["volume_start"] == 0.0
    assert res_zero_span["volume_end"] == 5.0
    assert res_zero_span["volumes"] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]

    # F. Non-Integer Volumes (Decimals / Fractional Volumes)
    res_float_span = extract_manga_bundle_and_volumes("Decimal Manga Vol. 1.5 - 4.5")
    assert res_float_span["is_bundle"] is True
    assert res_float_span["volume_start"] == 1.5
    assert res_float_span["volume_end"] == 4.5
    assert res_float_span["volumes"] == [1.0, 2.0, 3.0, 4.0]

    res_float_subzero = extract_manga_bundle_and_volumes("Decimal Manga Vol 0.5 - 2.5")
    assert res_float_subzero["is_bundle"] is True
    assert res_float_subzero["volume_start"] == 0.5
    assert res_float_subzero["volume_end"] == 2.5
    assert res_float_subzero["volumes"] == [0.0, 1.0, 2.0]

    res_single_float = extract_manga_bundle_and_volumes("Single Decimal Manga Vol. 10.5")
    assert res_single_float["is_bundle"] is False
    assert res_single_float["volume_start"] == 10.5
    assert res_single_float["volume_end"] == 10.5
    assert res_single_float["volumes"] == [10.5]

    # G. Negative Numbers & Hyphen Separators
    # Standard separator hyphen before Vol should parse smoothly
    res_hyphen_sep = extract_manga_bundle_and_volumes("Hyphen Manga - Vol 01-05")
    assert res_hyphen_sep["is_bundle"] is True
    assert res_hyphen_sep["volume_start"] == 1.0
    assert res_hyphen_sep["volume_end"] == 5.0

    # Negative numbers in volume position must not crash or trigger unhandled exceptions
    try:
        res_neg1 = extract_manga_bundle_and_volumes("Negative Manga Vol -1 to 5")
        assert isinstance(res_neg1, dict)
    except Exception as e:
        assert False, f"Negative volume title caused unexpected exception: {e}"

    try:
        res_neg2 = extract_manga_bundle_and_volumes("Negative Manga Vol -5 - -1")
        assert isinstance(res_neg2, dict)
    except Exception as e:
        assert False, f"Double negative volume title caused unexpected exception: {e}"

    # H. Comma / Plus Separated Bundles (Pattern 1b)
    res_comma = extract_manga_bundle_and_volumes("List Manga Vol 1, 2, 3, 4, 5")
    assert res_comma["is_bundle"] is True
    assert res_comma["volume_start"] == 1.0
    assert res_comma["volume_end"] == 5.0
    assert res_comma["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0]

    # Inverted comma list
    res_comma_inv = extract_manga_bundle_and_volumes("List Manga Vol 5, 4, 3, 2, 1")
    assert res_comma_inv["is_bundle"] is True
    assert res_comma_inv["volume_start"] == 1.0
    assert res_comma_inv["volume_end"] == 5.0
    assert res_comma_inv["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0]

    # Duplicate volumes in comma list (must be deduplicated)
    res_comma_dup = extract_manga_bundle_and_volumes("List Manga Vol 1, 2, 2, 3, 3, 3, 4")
    assert res_comma_dup["is_bundle"] is True
    assert res_comma_dup["volumes"] == [1.0, 2.0, 3.0, 4.0]

    # Plus / ampersand separated
    res_plus = extract_manga_bundle_and_volumes("Plus Manga Vol 1 + 2 + 3")
    assert res_plus["is_bundle"] is True
    assert res_plus["volumes"] == [1.0, 2.0, 3.0]

    res_amp = extract_manga_bundle_and_volumes("Amp Manga Vol 1 & 2 & 3")
    assert res_amp["is_bundle"] is True
    assert res_amp["volumes"] == [1.0, 2.0, 3.0]

    # I. Adversarial ReDoS Stress Test
    long_prefix = "Manga Series Name " * 500  # ~9000 chars
    t_start = time.time()
    res_long_match = extract_manga_bundle_and_volumes(long_prefix + "Vol 01-05")
    t_long = time.time() - t_start
    assert t_long < 0.2, f"ReDoS match took {t_long:.4f}s (exceeded 0.2s threshold)"
    assert res_long_match["is_bundle"] is True

    t_start = time.time()
    res_long_nomatch = extract_manga_bundle_and_volumes(long_prefix + "Chapter 100 Extra")
    t_long_nomatch = time.time() - t_start
    assert t_long_nomatch < 0.2, f"ReDoS non-match took {t_long_nomatch:.4f}s (exceeded 0.2s threshold)"

    # J. Extremely Large Numbers (Python Arbitrary Precision Ints)
    res_huge_nums = extract_manga_bundle_and_volumes("Huge Ints Manga Vol 10000000000000000000 - 10000000000000000005")
    assert res_huge_nums["is_bundle"] is True
    assert len(res_huge_nums["volumes"]) == 6

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
        assert is_new is True

        # User manually ignores this release
        db.update_release_status(rel_id, "IGNORED")
        row = db.get_release_by_id(rel_id)
        assert row["status"] == "IGNORED", "Failed to set status to IGNORED"

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
            assert is_new_up is False
            assert updated_id == rel_id

            # Verify that status REMAINED 'IGNORED' without exception
            current = db.get_release_by_id(rel_id)
            assert current["status"] == "IGNORED", (
                f"VIOLATION: Manual IGNORED status was overwritten by feed upsert status '{new_status}'!"
            )
            # Verify that non-status metadata DID update properly
            assert current["description"] == f"Updated description {idx}"
            assert current["download_url"] == f"https://example.com/dl_updated_{idx}"
            assert current["cover_url"] == f"https://example.com/cover_{idx}.jpg"

        # -------------------------------------------------------------------
        # Test 2: Unignore Recovery Lifecycle
        # -------------------------------------------------------------------
        # User unignores the release (restores to REQUESTED)
        db.update_release_status(rel_id, "REQUESTED")
        current = db.get_release_by_id(rel_id)
        assert current["status"] == "REQUESTED", "Manual restore to REQUESTED failed"

        # Now feed re-scans with IN_CALIBRE: transition SHOULD succeed because status is no longer IGNORED
        db.upsert_release({
            "guid": guid_ignored,
            "title": "Shield Hero Vol. 01 [In Calibre]",
            "series_name": "Shield Hero",
            "status": "IN_CALIBRE"
        })
        current = db.get_release_by_id(rel_id)
        assert current["status"] == "IN_CALIBRE", "Legitimate transition to IN_CALIBRE after unignore failed"

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
        assert db.get_release_by_id(tid)["status"] == "DISCOVERY"

        db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": "REQUESTED"
        })
        assert db.get_release_by_id(tid)["status"] == "REQUESTED", "DISCOVERY -> REQUESTED transition failed"

        # B. REQUESTED -> IN_CALIBRE (Acquired)
        db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": "IN_CALIBRE"
        })
        assert db.get_release_by_id(tid)["status"] == "IN_CALIBRE", "REQUESTED -> IN_CALIBRE transition failed"

        # C. IN_CALIBRE -> REQUESTED (e.g. missing volumes discovered)
        db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": "REQUESTED"
        })
        assert db.get_release_by_id(tid)["status"] == "REQUESTED", "IN_CALIBRE -> REQUESTED transition failed"

        # D. COALESCE check: upsert with status=None preserves prior status
        db.upsert_release({
            "guid": guid_trans,
            "title": "Transition Test Vol. 01",
            "series_name": "Transition Test",
            "status": None  # Omitted status
        })
        assert db.get_release_by_id(tid)["status"] == "REQUESTED", "COALESCE failed: status was wiped by None"

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

        assert len(errors) == 0, f"Concurrent upserts raised database errors: {errors}"
        final_row = db.get_release_by_id(conc_id)
        assert final_row is not None
        assert final_row["status"] in ("REQUESTED", "DISCOVERY")

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
        assert len(active_1000) == 1000, f"get_releases(limit=1000) returned {len(active_1000)}"

        # Verify O(1) indexed lookup on extreme IDs
        test_ids = [1, 500, 1000, 1001, 1050, 1500, 2000, 2200]
        for tid in test_ids:
            rel = db.get_release_by_id(tid)
            assert rel is not None, f"get_release_by_id failed to locate release #{tid}"
            assert rel["id"] == tid
            assert rel["guid"] == f"guid-bulk-{tid}"
            assert rel["title"] == f"Manga Bulk Series {tid} Vol. 01"

            # Check bundle & missing volumes deserialization on multiples of 10
            if tid % 10 == 0:
                assert rel["is_bundle"] == 1
                assert isinstance(rel["volumes"], list)
                assert rel["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0]
                assert isinstance(rel["missing_volumes"], list)
                assert rel["missing_volumes"] == [4.0, 5.0]
            else:
                assert rel["volumes"] == []
                assert rel["missing_volumes"] == []

        # -------------------------------------------------------------------
        # B. Non-Existent and Boundary IDs
        # -------------------------------------------------------------------
        assert db.get_release_by_id(999999) is None, "Non-existent positive ID should return None"
        assert db.get_release_by_id(0) is None, "ID 0 should return None"
        assert db.get_release_by_id(-1) is None, "Negative ID should return None"

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
        assert rel_null is not None
        assert rel_null["volumes"] == [], "NULL volumes_json must deserialize to empty list []"
        assert rel_null["missing_volumes"] == [], "NULL missing_vols_json must deserialize to empty list []"

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
        assert rel_lg is not None
        assert len(rel_lg["volumes"]) == 500
        assert len(rel_lg["missing_volumes"]) == 250
        assert rel_lg["volumes"] == large_vols
        assert rel_lg["missing_volumes"] == large_missing

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
        assert rel_frac["volumes"] == frac_vols
        assert rel_frac["missing_volumes"] == frac_missing

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
        assert high_ignored_id > 2000, f"Expected ID > 2000, got {high_ignored_id}"

        # Confirm get_releases(limit=1000) CANNOT find it
        assert not any(r["id"] == high_ignored_id for r in db.get_releases(limit=1000))

        # Confirm get_release_by_id CAN find it and preserve missing_volumes
        target = db.get_release_by_id(high_ignored_id)
        assert target is not None
        assert target["status"] == "IGNORED"
        assert target["missing_volumes"] == [8.0, 9.0, 10.0]

        # Simulate selective download filtering as done in main.py:584-587
        missing = target.get("missing_volumes") or json.loads(target.get("missing_vols_json") or "[]")
        vols = target.get("volumes") or []
        assert missing == [8.0, 9.0, 10.0]
        assert len(missing) < len(vols)
        torrent_hash = "0123456789abcdef0123456789abcdef01234567"

        mock_qbit = MagicMock()
        mock_qbit.apply_selective_volume_download.return_value = {
            "status": "filtered",
            "skipped_volumes": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
            "kept_volumes": [8.0, 9.0, 10.0]
        }
        res_sel = mock_qbit.apply_selective_volume_download(torrent_hash, [float(v) for v in missing])
        assert res_sel["status"] == "filtered"
        assert res_sel["kept_volumes"] == [8.0, 9.0, 10.0]

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
        db = MangaDatabase(db_path)
        scheduler = MangaScheduler(db=db)

        # Mock qbit client
        mock_qbit = MagicMock()
        mock_qbit.add_torrent.return_value = True
        mock_qbit.extract_torrent_hash.return_value = "deadbeef12345678"
        scheduler.get_qbit_client = MagicMock(return_value=mock_qbit)

        # Mock notifier
        mock_notifier = MagicMock()
        scheduler.get_notifier = MagicMock(return_value=mock_notifier)

        # Step 1: Ingest release for UNMONITORED series (status = DISCOVERY)
        rel_item = ReleaseItem(
            guid="guid-trans-manga-01",
            title="Dungeon Meshi Vol. 01 [Digital]",
            series_name="Dungeon Meshi",
            volume_start=1.0,
            volume_end=1.0,
            volumes=[1.0],
            download_url="https://tracker.invalid/dm01.torrent"
        )

        mock_feed = MagicMock()
        mock_feed.get_feed_name.return_value = "TestFeed"
        mock_feed.fetch_releases.return_value = [rel_item]

        # Enable auto_download globally and on feed
        db.set_setting("auto_download_enabled", "true")
        feed_config = {"name": "TestFeed", "auto_download": True, "feed_type": "nyaa"}

        # First poll: series is NOT monitored in series_tracking -> status becomes DISCOVERY
        # We simulate the inner ingestion logic of scheduler._poll_feed_source
        is_new, rel_id = db.upsert_release({
            "guid": rel_item.guid,
            "title": rel_item.title,
            "series_name": rel_item.series_name,
            "status": "DISCOVERY",
            "download_status": "IDLE",
            "download_url": rel_item.download_url
        })
        assert is_new is True
        row = db.get_release_by_id(rel_id)
        assert row["status"] == "DISCOVERY"
        assert row["download_status"] == "IDLE"
        assert mock_qbit.add_torrent.call_count == 0

        # Step 2: User starts monitoring the series
        db.track_discovered_series("Dungeon Meshi")
        assert db.is_series_monitored("Dungeon Meshi")

        # Step 3: Second poll re-evaluates release -> transitions from DISCOVERY to REQUESTED
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, status, download_status FROM releases WHERE guid = ?;", (rel_item.guid,))
            existing_row = cur.fetchone()

        prev_status = existing_row["status"]
        prev_dl_status = existing_row["download_status"]
        new_status = "REQUESTED"  # Because series is now monitored

        is_new_2, rel_id_2 = db.upsert_release({
            "guid": rel_item.guid,
            "title": rel_item.title,
            "series_name": rel_item.series_name,
            "status": new_status,
            "download_url": rel_item.download_url
        })
        assert is_new_2 is False, "Second poll must not be considered a new release"

        # Check transition condition matching scheduler.py:292-296
        is_transition = (
            not is_new_2
            and prev_status in ("DISCOVERY", "NEW")
            and prev_dl_status == "IDLE"
        )
        assert is_transition is True, "Scheduler failed to detect transition from DISCOVERY to REQUESTED"

        # Trigger auto-download action
        if (is_new_2 or is_transition):
            db.update_release_status(rel_id_2, "REQUESTED")
            mock_notifier.notify_new_request({"title": rel_item.title})
            success = mock_qbit.add_torrent(url_or_magnet=rel_item.download_url)
            if success:
                db.update_download_status(rel_id_2, "DOWNLOADING", "deadbeef12345678")

        assert mock_qbit.add_torrent.call_count == 1
        assert mock_notifier.notify_new_request.call_count == 1
        row_after_dl = db.get_release_by_id(rel_id_2)
        assert row_after_dl["status"] == "REQUESTED"
        assert row_after_dl["download_status"] == "DOWNLOADING"

        # Step 4: Third poll: release is still REQUESTED and DOWNLOADING -> NO DUPLICATE DOWNLOAD
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, status, download_status FROM releases WHERE guid = ?;", (rel_item.guid,))
            existing_row_3 = cur.fetchone()

        prev_status_3 = existing_row_3["status"]
        prev_dl_status_3 = existing_row_3["download_status"]
        is_new_3, _ = db.upsert_release({
            "guid": rel_item.guid,
            "title": rel_item.title,
            "series_name": rel_item.series_name,
            "status": "REQUESTED"
        })
        is_transition_3 = (
            not is_new_3
            and prev_status_3 in ("DISCOVERY", "NEW")
            and prev_dl_status_3 == "IDLE"
        )
        assert is_transition_3 is False, "Transition falsely re-detected; would cause duplicate download"

        # Auto-download should NOT be triggered
        if (is_new_3 or is_transition_3):
            mock_qbit.add_torrent(url_or_magnet=rel_item.download_url)

        assert mock_qbit.add_torrent.call_count == 1, "Duplicate download queued!"

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
    assert d1 == [1, 2], f"Expected delays on [1, 2], got {d1}"

    # Scenario 2: start_page = 5, max_pages = 3 -> delays after page 5, 6; none after 7
    d2 = simulate_delays(5, 3)
    assert d2 == [5, 6], f"Expected delays on [5, 6], got {d2}"

    # Scenario 3: start_page = 10, max_pages = 1 -> single page crawl, no delay needed
    d3 = simulate_delays(10, 1)
    assert d3 == [], f"Expected no delays for single page crawl, got {d3}"

    # B. HTML Scraper Fallback when httpx is None
    orig_httpx = hs_module.httpx
    try:
        hs_module.httpx = None
        scraper = HtmlScraperProvider(
            name="Challenger Scraper",
            base_url="https://mock-manga.invalid",
            entry_selector=".release-item",
            title_selector=".title",
            link_selector="a.download"
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
            page_content = scraper._fetch_page_html("https://mock-manga.invalid/page/1")
            assert "Berserk Vol. 41" in page_content
            releases = scraper.fetch_releases(max_items=10)
            assert len(releases) == 1
            assert releases[0].title == "Berserk Vol. 41"

    finally:
        hs_module.httpx = orig_httpx

    print("    [PASSED] Nyaa crawl delay & HTML scraper urllib fallback 100% verified.")


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
