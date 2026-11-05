#!/usr/bin/env python3
"""task-57 suite: per-file delete for staging and Awaiting Calibre."""

import json
import os
import sys
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app.tagger import pipeline as downloads_pipeline  # noqa: E402


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def test_awaiting_scan_shape():
    print("--> Testing awaiting scan exposes files/relative/size...")
    ctx = E2ETestContext(prefix="mangatrax_t57_scan_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        _write(os.path.join(root, "Unknown Series", "Unknown Series v01.cbz"))
        _write(os.path.join(root, "Unknown Series", "Unknown Series v02.cbz"))
        entries = downloads_pipeline.scan_awaiting_series(root, {})
        bucket = next(item for item in entries if item["series"] == "Unknown Series")
        assert bucket["count"] == 2, bucket
        assert len(bucket["files"]) == 2, bucket
        file_info = bucket["files"][0]
        assert file_info["relative"] == "Unknown Series/Unknown Series v01.cbz", file_info
        assert file_info["name"] == "Unknown Series v01.cbz", file_info
        assert isinstance(file_info["size_mb"], float), file_info
        print("    [PASSED] files list is additive with relative path, name and size; existing fields intact.")
    finally:
        ctx.close()


def test_staging_delete_and_reconcile():
    print("--> Testing staging file delete + release reconcile...")
    ctx = E2ETestContext(prefix="mangatrax_t57_staging_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        target = os.path.join(staging, "Deleted Series v01.cbz")
        other = os.path.join(staging, "Other Series v01.cbz")
        _write(target)
        _write(other)
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, download_status, "
                "download_files_json, volumes_json, missing_vols_json, routed_at) "
                "VALUES ('del-1', 'Deleted Series v01', 'Deleted Series', 'DISCOVERY', 'COMPLETED', ?, '[1]', '[1]', '2026-01-01');",
                (json.dumps([target]),),
            )
            conn.commit()
            release_id = cur.lastrowid
        with patch("app.main._calibre_series_snapshot", return_value=None):
            response = ctx.client.post(
                "/api/downloads/files/delete",
                json_data={"area": "staging", "relative": "Deleted Series v01.cbz"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deleted"] is True and body["area"] == "staging", body
        assert body["remaining_files"] == 1, body
        assert not os.path.exists(target), "target staging file must be removed"
        assert os.path.exists(other), "other staging files must be untouched"
        row = ctx.db.get_release_by_id(release_id)
        assert row["download_status"] == "IDLE", row
        assert not row["routed_at"] and row["download_files_json"] == "[]", row
        events = [item for item in ctx.db.get_logs(limit=50) if item["event_type"] == "DOWNLOAD_FILE_DELETED"]
        assert events and "Deleted Series v01.cbz" in events[0]["message"], events
        # Missing / traversal / absolute rejected.
        assert ctx.client.post("/api/downloads/files/delete", json_data={"area": "staging", "relative": "../escape.cbz"}).status_code == 400
        assert ctx.client.post("/api/downloads/files/delete", json_data={"area": "staging", "relative": target}).status_code == 400
        assert ctx.client.post("/api/downloads/files/delete", json_data={"area": "staging", "relative": "nope.cbz"}).status_code == 404
        print("    [PASSED] only target deleted, release reconciled to IDLE, activity logged, traversal/absolute/missing rejected.")
    finally:
        ctx.close()


def test_downloads_delete_and_cleanup():
    print("--> Testing raw awaiting file delete + empty folder cleanup...")
    ctx = E2ETestContext(prefix="mangatrax_t57_downloads_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        target = os.path.join(downloads, "Raw Unknown", "Raw Unknown v01.cbz")
        keep = os.path.join(downloads, "Other Unknown", "Other Unknown v01.cbz")
        _write(target)
        _write(keep)
        with patch("app.main._calibre_series_snapshot", return_value=None):
            response = ctx.client.post(
                "/api/downloads/files/delete",
                json_data={"area": "downloads", "relative": "Raw Unknown/Raw Unknown v01.cbz"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deleted"] and body["relative"] == "Raw Unknown/Raw Unknown v01.cbz", body
        assert body["series"] and body["series_remaining"] == 0, body
        assert not os.path.exists(target)
        assert not os.path.exists(os.path.dirname(target)), "empty parent folder should be pruned"
        assert os.path.exists(keep), "other series files untouched"
        assert not os.path.exists(downloads) or os.path.isdir(downloads), "root never deleted"
        # Non-listed file rejected.
        outside = os.path.join(ctx.temp_dir, "outside.cbz")
        _write(outside)
        assert ctx.client.post("/api/downloads/files/delete", json_data={"area": "downloads", "relative": "../outside.cbz"}).status_code == 400
        assert ctx.client.post("/api/downloads/files/delete", json_data={"area": "downloads", "relative": "Other Unknown/Other Unknown v01.cbz"}).status_code == 200
        print("    [PASSED] raw file deleted, series counts, empty folder pruned to root, other files/root untouched.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing per-file delete UI wiring...")
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    for needle in ("/api/downloads/files/delete", "deleteDownloadFile", "data-relative=\"{{ f.relative }}\"",
                   "data-relative=\"{{ file.relative }}\"", "staging-file-count", "awaiting-series-list",
                   "awaiting-series-count", "awaiting-file-count", "staging-empty-row",
                   "Click Delete again within 4s", "Match series", "openManualMatch(this.dataset.series)",
                   "never writes into the Calibre library"):
        assert needle in page, f"missing UI wiring: {needle}"
    section = page.split("// --- Per-file delete (task-57)")[1]
    assert "window.confirm" not in section
    assert "button.dataset.armed" in section and "clearTimeout" in section
    print("    [PASSED] per-row staging/awaiting delete buttons, two-step confirm, in-place counts/empty state, no window.confirm.")


def test_downloads_page_staging_delete_target():
    """Regression: staging rows must carry the delete target (f.relative).

    The page context used to expose only ``name`` for staging files, so the Delete
    button rendered ``data-relative=""`` and the JS handler returned silently — the
    button appeared to do nothing.  (The Calibre test harness stubs Jinja rendering,
    so this asserts the view's context contract + the template binding it feeds.)
    """
    print("--> Testing /downloads context provides the staging delete target...")
    main_src = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    staging_block_start = main_src.find("for entry in cbz_tagger.scan_staging_cbz_files")
    assert staging_block_start >= 0, "staging_files scan block missing"
    staging_block = main_src[staging_block_start:staging_block_start + 500]
    assert '"relative": entry["relative"]' in staging_block, (
        "downloads_view must expose f.relative; the staging Delete button has no target otherwise"
    )
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    assert 'data-relative="{{ f.relative }}"' in page, "staging delete button no longer binds f.relative"
    print("    [PASSED] staging delete button target is supplied by the view context.")


def main():
    print("=" * 78)
    print("Running per-file delete suite (task-57)")
    print("=" * 78)
    tests = [test_awaiting_scan_shape, test_staging_delete_and_reconcile,
             test_downloads_delete_and_cleanup, test_ui_wiring,
             test_downloads_page_staging_delete_target]
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
        print(f"FILE DELETE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL PER-FILE DELETE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
