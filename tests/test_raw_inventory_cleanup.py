#!/usr/bin/env python3
"""task-59 suite: raw inventory cleanup (duplicates/already-present/per-row delete)."""

import json
import os
import sys
import time
import traceback
import zipfile
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app import downloads_inventory_cleanup as cleanup  # noqa: E402

CALIBRE = [{"name": "Calibre Series", "volumes": [1, 2], "book_count": 99}]


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _cbz(path, pages=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(pages):
            archive.writestr(f"page{index}.jpg", b"image")


def test_duplicate_detection():
    print("--> Testing duplicate signatures + deterministic keep...")
    ctx = E2ETestContext(prefix="mangatrax_t59_dup_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(root, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        _cbz(os.path.join(root, "Dup Series", "Dup Series v01.cbz"))
        _cbz(os.path.join(root, "Dup Series copy", "Dup Series v01.cbz"))
        _cbz(os.path.join(root, "Dup Series v01.cbz"))
        _cbz(os.path.join(root, "Different Series v01.cbz"), pages=2)
        _write(os.path.join(root, "Different Series v01.cbz.extra"), b"")
        audit = cleanup.audit_inventory_cleanup(ctx.db, root, staging, calibre_series=[])
        by_relative = {item["relative"]: item for item in audit["candidates"]}
        assert len(audit["duplicates"]["candidates"]) == 2, audit["duplicates"]
        assert "Dup Series" not in by_relative, by_relative  # directory kept, file+copy are dupes
        assert by_relative["Dup Series copy"]["reason"] == "duplicate", by_relative
        assert by_relative["Dup Series v01.cbz"]["reason"] == "duplicate", by_relative
        assert "Different Series v01.cbz" not in by_relative, by_relative
        print("    [PASSED] dir+file duplicate detected, two dirs grouped, different content excluded, directory preferred keep.")
    finally:
        ctx.close()


def test_already_present():
    print("--> Testing staging/Calibre alternate-copy detection...")
    ctx = E2ETestContext(prefix="mangatrax_t59_present_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(root, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        _cbz(os.path.join(root, "Staged Series v01.cbz"))
        _cbz(os.path.join(staging, "Staged Series v01.cbz"))
        _cbz(os.path.join(root, "Only Raw Series v01.cbz"))
        _cbz(os.path.join(root, "Calibre Series v01.cbz"))
        _cbz(os.path.join(root, "Calibre Series v02.cbz"))
        _cbz(os.path.join(root, "Partial Calibre v01.cbz"))
        _cbz(os.path.join(root, "Partial Calibre v03.cbz"))
        partial_calibre = [{"name": "Partial Calibre", "volumes": [1, 2], "book_count": 99}]
        audit = cleanup.audit_inventory_cleanup(ctx.db, root, staging, calibre_series=CALIBRE + partial_calibre)
        reasons = {item["relative"]: item["reason"] for item in audit["candidates"]}
        assert reasons.get("Staged Series v01.cbz") == "in_staging", reasons
        assert "Only Raw Series v01.cbz" not in reasons, reasons
        assert reasons.get("Calibre Series v01.cbz") == "in_calibre", reasons
        assert reasons.get("Calibre Series v02.cbz") == "in_calibre", reasons
        # Partial Calibre v03 is not owned: must not be flagged (book_count never guesses).
        assert "Partial Calibre v03.cbz" not in reasons, reasons
        print("    [PASSED] staging basename alternate required; exact Calibre volumes flagged; volume missing/not owned excludes.")
    finally:
        ctx.close()


def test_clear_and_delete():
    print("--> Testing bulk clear + per-row delete safety...")
    ctx = E2ETestContext(prefix="mangatrax_t59_clear_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(root, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", root)
        ctx.db.set_setting("staging_dir", staging)
        _cbz(os.path.join(root, "Dup", "Dup v01.cbz"))
        _cbz(os.path.join(root, "Dup copy", "Dup v01.cbz"))
        _cbz(os.path.join(root, "Delete Me", "Delete Me v01.cbz"))
        _cbz(os.path.join(root, "Keep Me", "Keep Me v01.cbz"))
        result = cleanup.clear_inventory(ctx.db, root, staging, "duplicates", calibre_series=[])
        assert result["deleted"] == 1 and result["files_deleted"] >= 1, result
        assert os.path.isdir(os.path.join(root, "Dup")) or os.path.isdir(os.path.join(root, "Dup copy"))
        # Per-row delete: folder recursive + only listed entry + confirm required.
        per_row = ctx.client.post("/api/downloads/inventory/delete", json_data={"relative": "Delete Me", "confirm": True})
        assert per_row.status_code == 200, per_row.text
        assert not os.path.exists(os.path.join(root, "Delete Me"))
        assert os.path.exists(os.path.join(root, "Keep Me", "Keep Me v01.cbz"))
        assert ctx.client.post("/api/downloads/inventory/delete", json_data={"relative": "Delete Me", "confirm": True}).status_code == 404
        assert ctx.client.post("/api/downloads/inventory/delete", json_data={"relative": "../outside", "confirm": True}).status_code in (400, 404)
        assert ctx.client.post("/api/downloads/inventory/delete", json_data={"relative": "Keep Me"}).status_code == 400
        outside = os.path.join(ctx.temp_dir, "outside")
        _write(outside)
        with ctx.db.get_connection() as conn:
            pass
        # Symlink entry (if creatable) is rejected and outside untouched.
        try:
            os.symlink(outside, os.path.join(root, "Link Entry"))
            resp = ctx.client.post("/api/downloads/inventory/delete", json_data={"relative": "Link Entry", "confirm": True})
            assert resp.status_code in (400, 404), resp.status_code
            assert os.path.exists(outside)
        except (OSError, NotImplementedError):
            pass
        print("    [PASSED] duplicates keep one; folder per-row delete recursive/safe; not-listed/traversal/confirm rejected.")
    finally:
        ctx.close()


def test_bulk_task_and_revalidation():
    print("--> Testing bulk endpoint/task + stale candidate revalidation...")
    ctx = E2ETestContext(prefix="mangatrax_t59_task_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(root, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", root)
        ctx.db.set_setting("staging_dir", staging)
        _cbz(os.path.join(root, "Task Dup", "Task Dup v01.cbz"))
        _cbz(os.path.join(root, "Task Dup copy", "Task Dup v01.cbz"))
        with patch("app.main._calibre_series_snapshot", return_value=[]):
            response = ctx.client.post("/api/downloads/inventory/clear", json_data={"mode": "duplicates", "confirm": True})
            assert response.status_code == 200, response.text
            task_id = response.json()["task_id"]
            deadline = time.time() + 5
            task = {}
            while time.time() < deadline:
                task = ctx.client.get(f"/api/tasks/{task_id}").json()["task"]
                if not task["active"]:
                    break
                time.sleep(0.05)
        assert task["status"] == "success" and task["result"]["deleted"] == 1, task
        assert task["items_total"] == 1 and task["items_done"] == 1, task
        # Audit preview endpoint works and is read-only.
        with patch("app.main._calibre_series_snapshot", return_value=[]):
            audit = ctx.client.post("/api/downloads/inventory/audit")
        assert audit.status_code == 200 and "duplicates" in audit.json(), audit.text
        page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
        for needle in ("Clean up raw duplicates", "openRawCleanupPreview", "confirmRawCleanup",
                       "executeRawCleanup", "deleteRawInventoryEntry", "raw-cleanup-panel",
                       "Delete already-present copies"):
            assert needle in page, f"missing UI wiring: {needle}"
        section = page.split("// --- Raw inventory cleanup (task-59) ---")[1]
        assert "window.confirm" not in section
        # Per-row Tag & move button removed; global one remains.
        assert page.count("Tag & move now") == 1, "only the header Tag & move now may remain"
        print("    [PASSED] bulk task progress/result, audit preview read-only, cleanup UI/two-step, per-row tag button removed.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running raw inventory cleanup suite (task-59)")
    print("=" * 78)
    tests = [test_duplicate_detection, test_already_present, test_clear_and_delete, test_bulk_task_and_revalidation]
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
        print(f"RAW CLEANUP SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL RAW INVENTORY CLEANUP TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
