#!/usr/bin/env python3
"""task-54 suite: reconcile vanished release files / stale staging state."""

import json
import os
import sys
import time
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app import downloads_reconcile  # noqa: E402


def _insert(ctx, guid, title, series, status="DISCOVERY", download_status="COMPLETED",
            files=None, routed=False, volume=1.0):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, download_status, "
            "download_files_json, volumes_json, missing_vols_json, routed_at, downloaded_at, "
            "downloader_id, torrent_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (guid, title, series, status, download_status, json.dumps(files or []),
             json.dumps([volume]), json.dumps([volume]),
             "2026-01-01 00:00:00" if routed else "",
             "2026-01-01 00:00:00" if not routed else "",
             "handle-1", "hash-1"),
        )
        conn.commit()
        return cur.lastrowid


def test_reset_and_keep_cases():
    print("--> Testing reset routed/unrouted + raw/staging keeps...")
    ctx = E2ETestContext(prefix="mangatrax_t54_reconcile_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        # Routed COMPLETED, missing files -> reset.
        routed = _insert(ctx, "rec-routed", "Routed Series v01", "Routed Series", files=["Routed Series v01.cbz"], routed=True)
        # Unrouted COMPLETED, missing -> reset.
        unrouted = _insert(ctx, "rec-unrouted", "Unrouted Series v01", "Unrouted Series", files=["Unrouted Series v01.cbz"], routed=False)
        # Raw file still present -> keep.
        raw_path = os.path.join(downloads, "Raw Series v01.cbz")
        with open(raw_path, "wb") as handle:
            handle.write(b"cbz")
        raw = _insert(ctx, "rec-raw", "Raw Series v01", "Raw Series", files=[raw_path])
        # Staging file present (basename match after relocation) -> keep/relink.
        stage_path = os.path.join(staging, "Stage Series v01.cbz")
        with open(stage_path, "wb") as handle:
            handle.write(b"cbz")
        staged = _insert(ctx, "rec-stage", "Stage Series v01", "Stage Series", files=["/old/path/Stage Series v01.cbz"])
        # Ignored / active untouched.
        ignored = _insert(ctx, "rec-ignored", "Ignored Series v01", "Ignored Series", status="IGNORED", files=["x.cbz"])
        active = _insert(ctx, "rec-active", "Active Series v01", "Active Series", download_status="DOWNLOADING", files=["x.cbz"])
        result = downloads_reconcile.reconcile_missing_release_files(ctx.db, downloads, staging, calibre_series=[])
        assert result["reset"] == 2 and result["checked"] == 4, result
        assert set(result["reset_releases"]) == {routed, unrouted}, result
        for release_id in (routed, unrouted):
            row = ctx.db.get_release_by_id(release_id)
            assert row["download_status"] == "IDLE", row
            assert not row["routed_at"] and not row["downloaded_at"], row
            assert row["download_files_json"] == "[]", row
            assert row["downloader_id"] is None and row["torrent_hash"] is None, row
            assert row["status"] == "DISCOVERY", row
            assert "Tracked files disappeared" in row["download_error"], row
            from app.main import release_download_state
            assert release_download_state(row)["state"] == "not_downloaded"
            assert release_download_state(row)["can_download"] is True
        assert ctx.db.get_release_by_id(raw)["download_status"] == "COMPLETED"
        assert ctx.db.get_release_by_id(staged)["download_status"] == "COMPLETED"
        assert stage_path in json.loads(ctx.db.get_release_by_id(staged)["download_files_json"])
        assert ctx.db.get_release_by_id(ignored)["download_status"] == "COMPLETED"
        assert ctx.db.get_release_by_id(active)["download_status"] == "DOWNLOADING"
        events = [row for row in ctx.db.get_logs(limit=100) if row["event_type"] == "DOWNLOAD_FILES_MISSING"]
        assert len(events) == 2, events
        # Idempotent: nothing left to reset.
        again = downloads_reconcile.reconcile_missing_release_files(ctx.db, downloads, staging, calibre_series=[])
        assert again["reset"] == 0, again
        print("    [PASSED] routed/unrouted reset with cleared fields; raw/staging kept; ignored/active untouched; idempotent.")
    finally:
        ctx.close()


def test_calibre_fallback_and_in_calibre_rows():
    print("--> Testing Calibre found => IN_CALIBRE; IN_CALIBRE rows untouched...")
    ctx = E2ETestContext(prefix="mangatrax_t54_calibre_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        found = _insert(ctx, "cal-found", "Calibre Found v01", "Calibre Found", files=["Calibre Found v01.cbz"], routed=True)
        already = _insert(ctx, "cal-in", "Already In Calibre v01", "Already In Calibre", status="IN_CALIBRE", files=["x.cbz"])
        series = [{"name": "Calibre Found", "book_count": 3}]
        result = downloads_reconcile.reconcile_missing_release_files(ctx.db, downloads, staging, calibre_series=series)
        assert result["reset"] == 0 and result["in_calibre"] == 1, result
        row = ctx.db.get_release_by_id(found)
        assert row["status"] == "IN_CALIBRE" and row["download_status"] == "COMPLETED", row
        assert ctx.db.get_release_by_id(already)["status"] == "IN_CALIBRE"
        # Dry run never writes.
        missing = _insert(ctx, "cal-dry", "Dry Run v01", "Dry Run", files=["Dry Run v01.cbz"])
        dry = downloads_reconcile.reconcile_missing_release_files(ctx.db, downloads, staging, calibre_series=[], dry_run=True)
        assert dry["reset"] == 1, dry
        assert ctx.db.get_release_by_id(missing)["download_status"] == "COMPLETED"
        assert "files disappeared" not in (ctx.db.get_release_by_id(missing)["download_error"] or "")
        print("    [PASSED] Calibre snapshot match marks IN_CALIBRE; existing IN_CALIBRE untouched; dry_run writes nothing.")
    finally:
        ctx.close()


def test_rescan_endpoint_and_wiring():
    print("--> Testing rescan endpoint counts/task + scheduler/UI wiring...")
    ctx = E2ETestContext(prefix="mangatrax_t54_scan_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        release_id = _insert(ctx, "scan-1", "Scan Missing v01", "Scan Missing", files=["Scan Missing v01.cbz"], routed=True)
        with patch("app.main._calibre_series_snapshot", return_value=None):
            response = ctx.client.post("/api/downloads/scan")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reconciled"]["reset"] == 1, body
        assert body["reconciled"]["reset_releases"] == [release_id], body
        assert body["task"]["status"] == "success" and body["task"]["items_done"] >= 1, body
        assert "Checked" in (body["task"]["message"] or ""), body
        assert ctx.db.get_release_by_id(release_id)["download_status"] == "IDLE"
        assert "stale release(s) reset" in open(
            os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8",
        ).read()
        scheduler_source = open(os.path.join(BACKEND_DIR, "app", "scheduler.py"), encoding="utf-8").read()
        assert "reconcile_stale_release_files" in scheduler_source
        assert "last_stale_reconcile" in scheduler_source
        module_source = open(os.path.join(BACKEND_DIR, "app", "downloads_reconcile.py"), encoding="utf-8").read()
        assert "download_status = 'IDLE'" in module_source and "DOWNLOAD_FILES_MISSING" in module_source
        print("    [PASSED] scan returns reconciled counts/task progress, resets state; scheduler and UI wiring present.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running stale staging reconciliation suite (task-54)")
    print("=" * 78)
    tests = [test_reset_and_keep_cases, test_calibre_fallback_and_in_calibre_rows, test_rescan_endpoint_and_wiring]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception:
            failures += 1
            print(f"  [FAILED] {test.__name__}")
            traceback.print_exc()
    print("=" * 78)
    if failures:
        print(f"STALE RECONCILE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STALE RECONCILE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
