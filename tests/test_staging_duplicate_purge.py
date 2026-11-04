#!/usr/bin/env python3
"""task-55 suite: staging duplicate audit/purge with path safety and bookkeeping."""

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
from app import staging_duplicates  # noqa: E402

CALIBRE = [{"name": "Calibre Series", "book_count": 2, "volumes": [1, 2]}]


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _release(ctx, files):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, download_status, "
            "download_files_json, volumes_json, missing_vols_json, routed_at) "
            "VALUES ('dup-1', 'Calibre Series v01-02', 'Calibre Series', 'DISCOVERY', "
            "'COMPLETED', ?, '[1,2]', '[]', '2026-01-01 00:00:00');",
            (json.dumps(files),),
        )
        conn.commit()
        return cur.lastrowid


def test_detection_matrix():
    print("--> Testing detection: duplicates only when series+all volumes in Calibre...")
    ctx = E2ETestContext(prefix="mangatrax_t55_audit_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(os.path.join(staging, "legacy", "nested"), exist_ok=True)
        _write(os.path.join(staging, "Calibre Series v01.cbz"))
        _write(os.path.join(staging, "legacy", "nested", "Calibre Series v02.cbz"))
        _write(os.path.join(staging, "Calibre Series v03.cbz"))  # volume not in Calibre
        _write(os.path.join(staging, "Unknown Series v01.cbz"))
        _write(os.path.join(staging, "Calibre Series v01.part"))
        _write(os.path.join(staging, ".hidden v01.cbz"))
        _write(os.path.join(staging, "zero.cbz"), b"")
        result = staging_duplicates.audit_staging_duplicates(ctx.db, staging, CALIBRE)
        names = {item["filename"] for item in result["files"]}
        assert result["duplicates"] == 2, result
        assert names == {"Calibre Series v01.cbz", "Calibre Series v02.cbz"}, names
        assert result["total_bytes"] == 2, result
        assert os.path.exists(os.path.join(staging, "Calibre Series v01.cbz")), "audit must be read-only"
        assert "v03" not in "".join(names), names
        # No Calibre snapshot -> no duplicates.
        empty = staging_duplicates.audit_staging_duplicates(ctx.db, staging, [])
        assert empty["duplicates"] == 0, empty
        print("    [PASSED] nested dupes found, missing-volume/unknown/partial/hidden/zero-byte excluded, no writes.")
    finally:
        ctx.close()


def test_purge_and_bookkeeping():
    print("--> Testing purge selected + release IN_CALIBRE bookkeeping...")
    ctx = E2ETestContext(prefix="mangatrax_t55_purge_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        first = os.path.join(staging, "Calibre Series v01.cbz")
        second = os.path.join(staging, "Calibre Series v02.cbz")
        _write(first)
        _write(second)
        release_id = _release(ctx, [first, second, "/elsewhere/Calibre Series v03.cbz"])
        audit = staging_duplicates.audit_staging_duplicates(ctx.db, staging, CALIBRE)
        assert all(item["release_id"] == release_id for item in audit["files"]), audit
        # Partial purge keeps release state.
        partial = staging_duplicates.purge_staging_duplicates(
            ctx.db, staging, filenames=["Calibre Series v01.cbz"], calibre_series=CALIBRE,
        )
        assert partial["purged"] == 1 and partial["bytes_freed"] == 1, partial
        row = ctx.db.get_release_by_id(release_id)
        assert row["status"] == "DISCOVERY" and row["routed_at"], row
        assert not os.path.exists(first) and os.path.exists(second)
        # Purge remaining -> IN_CALIBRE, bookkeeping cleared, row kept.
        full = staging_duplicates.purge_staging_duplicates(
            ctx.db, staging, filenames=["Calibre Series v02.cbz"], calibre_series=CALIBRE,
        )
        assert full["purged"] == 1, full
        row = ctx.db.get_release_by_id(release_id)
        assert row is not None and row["status"] == "IN_CALIBRE", row
        assert not row["routed_at"], row
        kept_files = json.loads(row["download_files_json"])
        assert "/elsewhere/Calibre Series v03.cbz" in kept_files, row
        assert all("Calibre Series v01.cbz" not in str(path) and "Calibre Series v02.cbz" not in str(path) for path in kept_files), row
        events = [item for item in ctx.db.get_logs(limit=50) if item["event_type"] == "STAGING_DUPLICATE_PURGED"]
        assert len(events) == 2, events
        # Idempotent: nothing left.
        again = staging_duplicates.purge_staging_duplicates(ctx.db, staging, all_files=True, confirm=True, calibre_series=CALIBRE)
        assert again["purged"] == 0, again
        print("    [PASSED] partial keeps state; fully purged release -> IN_CALIBRE with cleared routed/staging bookkeeping; activity logged.")
    finally:
        ctx.close()


def test_path_safety_and_task():
    print("--> Testing traversal/symlink safety + purge task endpoint/UI...")
    ctx = E2ETestContext(prefix="mangatrax_t55_safety_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        _write(os.path.join(staging, "Calibre Series v01.cbz"))
        ctx.db.set_setting("staging_dir", staging)
        outside = os.path.join(ctx.temp_dir, "outside.cbz")
        _write(outside)
        safe = staging_duplicates.purge_staging_duplicates(
            ctx.db, staging, filenames=["../outside.cbz", "missing.cbz"], calibre_series=CALIBRE,
        )
        assert safe["purged"] == 0 and safe["skipped"] >= 1, safe
        assert os.path.exists(outside), "path traversal must never delete outside staging"
        # Symlink to outside is skipped by audit (if the platform can create one).
        link = os.path.join(staging, "Linked Series v01.cbz")
        try:
            os.symlink(outside, link)
            audited = staging_duplicates.audit_staging_duplicates(ctx.db, staging, CALIBRE)
            assert "Linked Series v01.cbz" not in {item["filename"] for item in audited["files"]}, audited
        except (OSError, NotImplementedError):
            pass
        # Endpoint: all duplicate purge runs a background task and removes only verified files.
        with patch("app.main._staging_calibre_snapshot", return_value=CALIBRE):
            response = ctx.client.post(
                "/api/downloads/staging-duplicates/purge",
                json_data={"all": True, "confirm": True},
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task_id"]
            deadline = time.time() + 5
            task = {}
            while time.time() < deadline:
                task = ctx.client.get(f"/api/tasks/{task_id}").json()["task"]
                if not task["active"]:
                    break
                time.sleep(0.05)
        assert task["status"] == "success" and task["items_done"] == 1, task
        assert task["result"]["purged"] == 1, task
        assert not os.path.exists(os.path.join(staging, "Calibre Series v01.cbz"))
        assert os.path.exists(outside)
        page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
        for needle in ("Staging cleanup / duplicates", "auditStagingDuplicates", "purgeSelectedDuplicates",
                       "purgeAllDuplicates", "staging-duplicates-confirm", "data-task-kind=\"downloads:staging-purge\"",
                       "No staging files duplicated in Calibre"):
            assert needle in page, f"missing UI wiring: {needle}"
        assert "window.confirm" not in page.split("// --- Staging duplicates audit/purge")[1]
        print("    [PASSED] traversal rejected, symlink skipped, purge task progress 1/1, UI two-step confirm/no window.confirm wired.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running staging duplicate purge suite (task-55)")
    print("=" * 78)
    tests = [test_detection_matrix, test_purge_and_bookkeeping, test_path_safety_and_task]
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
        print(f"STAGING DUPLICATE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STAGING DUPLICATE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
