#!/usr/bin/env python3
"""task-53 suite: per-file pickup progress, shared series modal, inline errors."""

import json
import os
import sys
import time
import traceback
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402


def _hold(ctx, guid, title):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, download_status, "
            "downloader, downloader_id, volumes_json, missing_vols_json) "
            "VALUES (?, ?, 'Progress Series', 'REQUESTED', 'DOWNLOADING', 'torbox', '77', '[1,2]', '[1,2]');",
            (guid, title),
        )
        conn.commit()
        release_id = cur.lastrowid
    ctx.scheduler._hold_torbox_for_manual_pickup(
        release_id, title, "mapping failed",
        info={"torrent_id": 77, "files": [
            {"id": 1, "name": f"{title} v01.cbz", "size": 100},
            {"id": 2, "name": f"{title} v02.cbz", "size": 200},
        ]},
    )
    return release_id, ctx.db.get_manual_pickup_for_release(release_id)


def test_per_file_progress():
    print("--> Testing per-file fetch progress transitions...")
    ctx = E2ETestContext(prefix="mangatrax_t53_progress_")
    try:
        release_id, pickup = _hold(ctx, "per-file-1", "Progress Series")
        calls = []
        fake = MagicMock()
        fake.fetch_files.side_effect = lambda handle, files, dest, progress_cb=None: [
            {"path": os.path.join(dest, files[0]["name"]), "size": files[0]["size"]}
        ]

        def reporter(done, total, message, **kwargs):
            calls.append((done, total, message))

        with patch("app.scheduler.build_downloader", return_value=fake), \
             patch.object(ctx.scheduler, "process_downloads_to_staging", return_value={}):
            result = ctx.scheduler._fetch_manual_pickup_files(
                pickup["id"], None, progress=reporter,
            )
        assert result["fetched"] == 2 and result["failed"] == 0, result
        assert calls[0][0] == 0 and calls[0][1] == 2, calls
        assert any(done == 1 and total == 2 and "v01" in message for done, total, message in calls), calls
        assert any(done == 2 and total == 2 and "v02" in message for done, total, message in calls), calls
        assert calls[-1][0] == 2 and calls[-1][1] == 2, calls
        refreshed = ctx.db.get_torbox_manual_pickup(pickup["id"])
        assert refreshed["status"] == "completed", refreshed
        print("    [PASSED] fetch reports done/total/current file and completes pickup after per-file loop.")
    finally:
        ctx.close()


def test_task_endpoint_progress():
    print("--> Testing pickup fetch task items/message...")
    ctx = E2ETestContext(prefix="mangatrax_t53_task_")
    try:
        release_id, pickup = _hold(ctx, "task-1", "Task Progress")
        fake = MagicMock()
        fake.fetch_files.side_effect = lambda handle, files, dest, progress_cb=None: [
            {"path": os.path.join(dest, files[0]["name"]), "size": files[0]["size"]}
        ]
        with patch("app.scheduler.build_downloader", return_value=fake), \
             patch.object(ctx.scheduler, "process_downloads_to_staging", return_value={}):
            response = ctx.client.post(
                f"/api/torbox/manual-pickups/{pickup['id']}/fetch", json_data={"all": True},
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
        assert task["status"] == "success", task
        assert task["items_total"] == 2 and task["items_done"] == 2, task
        assert "failed 0" in (task["message"] or ""), task
        print("    [PASSED] pickup fetch task persists done/total 2/2 and final fetched/failed message.")
    finally:
        ctx.close()


def test_ui_modal_and_errors():
    print("--> Testing shared modal + inline error wiring...")
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "manual_pickup.html"), encoding="utf-8").read()
    assert "prompt(" not in page, "manual pickup must not use browser prompts"
    for needle in ("pickup-match-modal", "openPickupMatch", "submitPickupMatch", "pickup-match-search",
                   "pickup-match-new-title", "pickup-match-list", "/api/downloads/awaiting-series/match",
                   "pickup-error", "task.error", "mapping kept"):
        assert needle in page, f"missing modal/error wiring: {needle}"
    assert "data-task-kind=\"torbox:pickup:retry\"" in page
    print("    [PASSED] page uses the shared match modal (no prompts) and surfaces task errors inline while keeping mapping.")


def main():
    print("=" * 78)
    print("Running manual pickup polish suite (task-53)")
    print("=" * 78)
    tests = [test_per_file_progress, test_task_endpoint_progress, test_ui_modal_and_errors]
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
        print(f"MANUAL PICKUP POLISH SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL MANUAL PICKUP POLISH TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
