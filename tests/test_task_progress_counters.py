#!/usr/bin/env python3
"""task-50 suite: real counters for Calibre sync/backfill + quick action counts."""

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


def test_sync_counter_reporter():
    print("--> Testing Calibre sync done/total reporter...")
    ctx = E2ETestContext(prefix="mangatrax_t50_sync_")
    try:
        calls = []

        def fake_sync(progress=None):
            for index in range(1, 4):
                if progress:
                    progress(index, 3, f"Series {index}/3")
            return {"success": True, "synced_series": 3, "resolved_requests": 1}

        with patch.object(ctx.scheduler, "sync_calibre_library", side_effect=fake_sync):
            response = ctx.client.post("/api/calibre/sync")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("task_id") and body.get("synced_series") == 3, body
        assert body["task"]["status"] == "success", body["task"]
        assert body["task"]["progress_pct"] == 100.0, body["task"]
        assert body["task"]["items_total"] == 3, body["task"]
        print("    [PASSED] Calibre sync task records done/total series and persists legacy result.")
    finally:
        ctx.close()


def test_backfill_counts():
    print("--> Testing backfill task page/release counters...")
    ctx = E2ETestContext(prefix="mangatrax_t50_backfill_")
    try:
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO series_tracking (name, is_monitored) VALUES ('Counters Backfill', 1);"
            )
            conn.commit()
        series = ctx.db.get_all_series()[0]

        def fake_backfill(series_name, max_pages=3, actor=None, progress=None):
            if progress:
                progress(0, 4, "metadata")
                progress(1, max_pages, "Nyaa page 1/2")
                progress(2, max_pages, "Nyaa page 2/2")
                progress(2, 2, "done")
            return {"status": "started", "message": "Series refresh started"}

        with patch.object(ctx.scheduler, "run_series_backfill_async", side_effect=fake_backfill):
            response = ctx.client.post(
                f"/api/series/{series['id']}/backfill", json_data={"max_pages": 2},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "started" and body.get("task_id"), body
        assert body["task"]["status"] == "success", body["task"]
        assert body["task"]["items_total"] == 2, body["task"]
        assert response.json()["task"]["progress_pct"] == 100.0
        print("    [PASSED] backfill task carries pages/max counters and completes with legacy fields.")
    finally:
        ctx.close()


def test_quick_action_counts():
    print("--> Testing quick downloads/logs task counts...")
    ctx = E2ETestContext(prefix="mangatrax_t50_quick_")
    try:
        flatten = ctx.client.post("/api/downloads/flatten-staging")
        assert flatten.status_code == 200, flatten.text
        body = flatten.json()
        assert body.get("task_id"), body
        assert body["task"]["status"] == "success", body["task"]
        assert body["task"]["items_done"] == 1 and body["task"]["items_total"] == 1, body["task"]
        assert "Completed" in (body["task"].get("message") or ""), body["task"]
        prune = ctx.client.post("/api/logs/prune")
        assert prune.status_code == 200, prune.text
        assert prune.json().get("task_id"), prune.json()
        print("    [PASSED] quick actions report a completed counter summary in their task row.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running task-progress counter suite (task-50)")
    print("=" * 78)
    tests = [test_sync_counter_reporter, test_backfill_counts, test_quick_action_counts]
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
        print(f"COUNTER SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TASK-50 COUNTER TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
