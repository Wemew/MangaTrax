#!/usr/bin/env python3
"""task-47 suite: persisted background-task framework + global task UI.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

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
from app import background_tasks  # noqa: E402


def _wait_task(db, task_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = db.get_background_task(task_id)
        if task and task.get("status") != "running":
            return task
        time.sleep(0.05)
    return db.get_background_task(task_id)


def test_framework_lifecycle():
    print("--> Testing background task lifecycle/persistence/failure...")
    ctx = E2ETestContext(prefix="mangatrax_tasks_fw_")
    try:
        seen = []

        def worker(report):
            report(1, 4, "first", force=True)
            seen.append("first")
            report(2, 4, "half")
            return {"answer": 42}

        info = background_tasks.run_in_background(
            ctx.db, "test:lifecycle", "Lifecycle test", worker, actor="tester", scope="scope-1",
        )
        task = _wait_task(ctx.db, info["task_id"])
        assert task["status"] == "success", task
        assert task["progress_pct"] == 100.0, task
        assert '"answer": 42' in (task["result_json"] or ""), task
        assert task["actor"] == "tester" and task["scope"] == "scope-1", task
        assert task["finished_at"], task
        assert seen == ["first"], seen

        def failing(report):
            report(1, 2, "about to fail", force=True)
            raise RuntimeError("boom")

        info = background_tasks.run_in_background(ctx.db, "test:fail", "Failing test", failing)
        failed = _wait_task(ctx.db, info["task_id"])
        assert failed["status"] == "failed" and "boom" in failed["error"], failed
        events = [row for row in ctx.db.get_logs(limit=100)
                  if row["event_type"] in ("BACKGROUND_TASK_STARTED", "BACKGROUND_TASK_FINISHED")]
        assert events, events
        print("    [PASSED] start/progress/finish persisted; failure captured; activity logged.")
    finally:
        ctx.close()


def test_cancel_and_prune():
    print("--> Testing cooperative cancel + prune safety...")
    ctx = E2ETestContext(prefix="mangatrax_tasks_cancel_")
    try:
        def cancellable(report):
            for index in range(100):
                if report.cancelled:
                    report.raise_if_cancelled()
                report(index, 100, "working", force=True)
                time.sleep(0.02)
            return {"done": True}

        info = background_tasks.run_in_background(
            ctx.db, "test:cancel", "Cancellable", cancellable, cancellable=True,
        )
        time.sleep(0.1)
        assert background_tasks.cancel_supported(info["task_id"]) is True
        assert background_tasks.request_cancel(ctx.db, info["task_id"]) is True
        task = _wait_task(ctx.db, info["task_id"])
        assert task["status"] == "cancelled", task

        def quick(report):
            return {}

        info2 = background_tasks.run_in_background(ctx.db, "test:nocancel", "Not cancellable", quick)
        _wait_task(ctx.db, info2["task_id"])
        assert background_tasks.request_cancel(ctx.db, info2["task_id"]) is False
        # Prune: old finished removed; running kept.
        with ctx.db.get_connection() as conn:
            conn.execute(
                "UPDATE background_tasks SET finished_at = datetime('now','-30 days') "
                "WHERE id = ?;", (info2["task_id"],),
            )
            conn.commit()
        running_id = ctx.db.create_background_task("test:running", "Running")
        removed = ctx.db.prune_background_tasks(days=7)
        assert removed >= 1, removed
        assert ctx.db.get_background_task(info2["task_id"]) is None
        assert ctx.db.get_background_task(running_id) is not None
        print("    [PASSED] cooperative cancel works; non-cancellable refuses; prune removes old finished only.")
    finally:
        ctx.close()


def test_api_and_endpoints():
    print("--> Testing task API + migrated endpoint task ids...")
    ctx = E2ETestContext(prefix="mangatrax_tasks_api_")
    try:
        with ctx.db.get_connection() as conn:
            task_id = ctx.db.create_background_task("test:api", "API task")
            ctx.db.update_background_task(task_id, done=1, total=2, message="half", progress_pct=50)
            conn.commit()
        listing = ctx.client.get("/api/tasks")
        assert listing.status_code == 200, listing.text
        data = listing.json()
        assert data["tasks"] and any(task["id"] == task_id for task in data["tasks"]), data
        detail = ctx.client.get(f"/api/tasks/{task_id}")
        assert detail.status_code == 200 and detail.json()["task"]["progress_pct"] == 50.0, detail.text
        missing = ctx.client.get("/api/tasks/999999")
        assert missing.status_code == 404
        refused = ctx.client.post(f"/api/tasks/{task_id}/cancel")
        assert refused.status_code == 409, refused.status_code
        ctx.db.finish_background_task(task_id, "success")

        # Quick migrated endpoint: flatten returns a completed task id alongside legacy fields.
        flatten = ctx.client.post("/api/downloads/flatten-staging")
        assert flatten.status_code == 200, flatten.text
        body = flatten.json()
        assert body.get("task_id") and body.get("task", {}).get("status") == "success", body
        assert "flattened" in body
        # Speed test: rate-limit cleared; returns task id + legacy speed result.
        main_module = __import__("app.main", fromlist=["main"])
        with patch.object(
            main_module.network_diagnostics, "run_speedtest",
            return_value={"status": "success", "aggregate_mbps": 10.0, "bytes_transferred": 100},
        ):
            main_module._NETWORK_SPEEDTEST_LAST.clear()
            speed = ctx.client.post("/api/diagnostics/speedtest", json_data={"bytes": 1000, "connections": 1})
        assert speed.status_code == 200, speed.text
        assert speed.json().get("task_id") and speed.json().get("aggregate_mbps") == 10.0, speed.json()
        print("    [PASSED] list/detail/404/409/API fields; quick + speedtest endpoints return task ids with legacy fields.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing global task UI wiring + docs...")
    base = open(os.path.join(BACKEND_DIR, "app", "templates", "base.html"), encoding="utf-8").read()
    for needle in ("global-tasks", "tasks-panel", "tasks-active-count", "MangaTasks",
                   "bindButton", "watch", "/api/tasks?active=0", "setInterval(poll, 1500)",
                   "aria-live", "tasks-toast"):
        assert needle in base, f"missing global task UI wiring: {needle}"
    assert "location.reload" not in base.split("const MangaTasks")[1].split("})();")[0]
    readme = open(os.path.join(BASE_DIR, "README.md"), encoding="utf-8").read()
    assert "Long-running actions" in readme, "missing documentation convention"
    print("    [PASSED] base.html has persisted tasks panel/polling/reattachment helpers; README documents the convention.")


def main():
    print("=" * 78)
    print("Running background task progress suite (task-47)")
    print("=" * 78)
    tests = [test_framework_lifecycle, test_cancel_and_prune, test_api_and_endpoints, test_ui_wiring]
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
        print(f"BACKGROUND TASK SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL BACKGROUND TASK TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
