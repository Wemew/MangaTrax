#!/usr/bin/env python3
"""task-58 suite: failed document detection, matching, retry/delete, UI."""

import io
import json
import os
import sys
import time
import zipfile
import traceback
from unittest.mock import AsyncMock, MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app import download_failures  # noqa: E402
from app.tagger import pipeline as downloads_pipeline  # noqa: E402


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _valid_cbz(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("page.jpg", b"image")


def test_detection_scan():
    print("--> Testing magic-byte detection + scan exclusions...")
    ctx = E2ETestContext(prefix="mangatrax_t58_scan_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        os.makedirs(root, exist_ok=True)
        _write(os.path.join(root, "Rar Series v01.cbz"), b"Rar!\x1a\x07\x00data")
        _write(os.path.join(root, "Pdf Series v01.cbz"), b"%PDF-1.4 sample")
        _write(os.path.join(root, "Seven Series v01.cbz"), b"7z\xbc\xaf\x27\x1cdata")
        _write(os.path.join(root, "Html Series v01.cbz"), b"<!DOCTYPE html><html>error</html>")
        _valid_cbz(os.path.join(root, "Valid Series v01.cbz"))
        stats = download_failures.scan_failures(ctx.db, root)
        assert stats["checked"] == 5 and stats["failed"] == 4, stats
        rows = download_failures.list_failures(ctx.db, root)
        types = {row["name"]: row["detected_type"] for row in rows}
        assert types["Rar Series v01.cbz"] == "rar", types
        assert types["Pdf Series v01.cbz"] == "pdf", types
        assert types["Seven Series v01.cbz"] == "7z", types
        assert types["Html Series v01.cbz"] == "html", types
        assert "Valid Series v01.cbz" not in types, types
        assert all("not a zip" in row["reason"] for row in rows), rows
        print("    [PASSED] RAR/PDF/7z/HTML signatures detected, valid CBZ excluded, reasons recorded.")
    finally:
        ctx.close()


def test_matching_and_pipeline_persistence():
    print("--> Testing release matching + pipeline failure persistence/clearing...")
    ctx = E2ETestContext(prefix="mangatrax_t58_match_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(root, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, download_url, volumes_json) "
                "VALUES ('match-1', 'Match Series v01', 'Match Series', 'REQUESTED', 'magnet:?xt=abc', '[1]');"
            )
            conn.commit()
            release_id = cur.lastrowid
        bad = os.path.join(root, "Match Series v01.cbz")
        _write(bad, b"Rar!broken")
        ctx.db.set_setting("downloads_dir", root)
        ctx.db.set_setting("staging_dir", staging)
        stats = download_failures.scan_failures(ctx.db, root)
        rows = download_failures.list_failures(ctx.db, root)
        assert rows and rows[0]["release_id"] == release_id, rows
        assert rows[0]["can_retry"] is True and rows[0]["release_title"] == "Match Series v01", rows
        # No-template pipeline path records too.
        with ctx.db.get_connection() as conn:
            conn.execute("DELETE FROM download_failures;")
            conn.commit()
        unmatched = os.path.join(root, "NoMatch Series v01.cbz")
        _write(unmatched, b"%PDF-bad")
        downloads_pipeline.process_downloads_to_staging(
            root, staging, {"nomatch series": {"series_name": "NoMatch Series"}}, ctx.db,
        )
        failures = download_failures.list_failures(ctx.db, root)
        assert any(row["name"] == "NoMatch Series v01.cbz" for row in failures), failures
        # Valid archive succeeds and clears the prior failure.
        with zipfile.ZipFile(unmatched, "w") as archive:
            archive.writestr("page.jpg", b"image")
        # Force a template so the pipeline moves/clears it.
        downloads_pipeline.process_downloads_to_staging(
            root, staging, {"nomatch series": {"series_name": "NoMatch Series"}}, ctx.db,
        )
        assert not [row for row in download_failures.list_failures(ctx.db, root) if row["name"] == "NoMatch Series v01.cbz"]
        print("    [PASSED] release matched by series/volume with retryable URL; pipeline failure persisted; later success cleared.")
    finally:
        ctx.close()


def test_retry_and_delete():
    print("--> Testing retry 409/submit + delete safety/bulk...")
    ctx = E2ETestContext(prefix="mangatrax_t58_retry_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(root, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", root)
        ctx.db.set_setting("staging_dir", staging)
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, download_url, volumes_json) "
                "VALUES ('retry-1', 'Retry Series v01', 'Retry Series', 'REQUESTED', 'magnet:?xt=retry', '[1]');"
            )
            conn.commit()
        _write(os.path.join(root, "Retry Series v01.cbz"), b"Rar!broken")
        download_failures.scan_failures(ctx.db, root)
        row = download_failures.list_failures(ctx.db, root)[0]
        with patch("app.main.api_release_download", new=AsyncMock(return_value=MagicMock(
            status_code=200, body=json.dumps({"status": "success", "engine": "torbox"}),
        ))):
            retried = ctx.client.post(f"/api/downloads/failures/{row['id']}/retry")
        assert retried.status_code == 200, retried.text
        assert retried.json()["download"]["engine"] == "torbox", retried.json()
        assert ctx.db.get_download_failure(row["id"])["status"] == "retried"
        # No matching release -> 409.
        _write(os.path.join(root, "Unmatched Series v99.cbz"), b"%PDF-bad")
        download_failures.scan_failures(ctx.db, root)
        unmatched = next(item for item in download_failures.list_failures(ctx.db, root) if item["name"] == "Unmatched Series v99.cbz")
        assert ctx.client.post(f"/api/downloads/failures/{unmatched['id']}/retry").status_code == 409
        # Delete only recorded failures; traversal record rejected.
        with patch("app.main.downloads_reconcile.reconcile_missing_release_files", MagicMock(return_value={})) as reconcile:
            deleted = ctx.client.post(f"/api/downloads/failures/{unmatched['id']}/delete")
            assert deleted.status_code == 200, deleted.text
            assert deleted.json()["deleted"] == 1, deleted.json()
            assert reconcile.called
        assert not os.path.exists(os.path.join(root, "Unmatched Series v99.cbz"))
        assert ctx.db.get_download_failure(unmatched["id"])["status"] == "deleted"
        # Traversal in DB row is rejected, file outside untouched.
        outside = os.path.join(ctx.temp_dir, "outside.cbz")
        _write(outside)
        bad_id = ctx.db.record_download_failure("../outside.cbz", "bad", "rar")
        rejected = ctx.client.post(f"/api/downloads/failures/{bad_id}/delete")
        assert rejected.status_code in (400, 403), rejected.status_code
        assert os.path.exists(outside)
        # Bulk requires confirm and deletes remaining recorded failures.
        _write(os.path.join(root, "Bulk Series v01.cbz"), b"7z\xbc\xaf\x27\x1c")
        download_failures.scan_failures(ctx.db, root)
        before = len(download_failures.list_failures(ctx.db, root))
        assert before >= 1
        assert ctx.client.post("/api/downloads/failures/delete-all", json_data={}).status_code == 400
        bulk = ctx.client.post("/api/downloads/failures/delete-all", json_data={"confirm": True})
        assert bulk.status_code == 200, bulk.json()
        assert bulk.json()["deleted"] + bulk.json()["skipped"] == before, bulk.json()
        remaining = download_failures.list_failures(ctx.db, root)
        assert all(item["file_relative"] == "../outside.cbz" for item in remaining), remaining
        print("    [PASSED] retry submits via shared path/marks retried, 409 paths, delete containment + reconciliation + bulk confirm.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing failed documents UI wiring...")
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    for needle in ("Failed documents", "loadFailedDocuments", "scanFailedDocuments",
                   "retryFailedDocument", "deleteFailedDocument", "armDeleteAllFailures",
                   "executeDeleteAllFailures", "/api/downloads/failures", "failed-documents-empty",
                   "No failed documents found."):
        assert needle in page, f"missing UI wiring: {needle}"
    section = page.split("// --- Failed documents (task-58) ---")[1]
    assert "window.confirm" not in section
    assert "Click Delete file again within 4s" in section
    print("    [PASSED] card/table/retry/delete/bulk-scan wiring, two-step confirm, no window.confirm.")


def main():
    print("=" * 78)
    print("Running failed documents suite (task-58)")
    print("=" * 78)
    tests = [test_detection_scan, test_matching_and_pipeline_persistence, test_retry_and_delete, test_ui_wiring]
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
        print(f"FAILED DOCUMENTS SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL FAILED DOCUMENT TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
