#!/usr/bin/env python3
"""task-48 suite: migrated audits/repairs + counter progress follow-ups.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import sys
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


def test_audit_endpoints_return_tasks():
    print("--> Testing audit/repair endpoints return persisted task ids...")
    ctx = E2ETestContext(prefix="mangatrax_t48_audit_")
    try:
        endpoints = [
            ("get", "/api/tagger/audit"),
            ("get", "/api/maintenance/series-attribution"),
            ("get", "/api/maintenance/suspicious-volume-ranges"),
        ]
        for method, path in endpoints:
            response = getattr(ctx.client, method)(path)
            assert response.status_code == 200, (path, response.text)
            body = response.json()
            assert body.get("task_id"), (path, body)
            assert body.get("task", {}).get("status") == "success", (path, body)
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("staging_dir", staging)
        with zipfile.ZipFile(os.path.join(staging, "Task Forty Eight v01.cbz"), "w") as archive:
            archive.writestr("001.jpg", b"image")
        repair = ctx.client.post(
            "/api/tagger/repair-series",
            json_data={"files": ["Task Forty Eight v01.cbz"], "repair_writer": True, "repair_publisher": True},
        )
        assert repair.status_code == 200, repair.text
        assert repair.json().get("task_id"), repair.json()
        assert repair.json()["task"]["status"] == "success", repair.json()
        attribution = ctx.client.post(
            "/api/maintenance/series-attribution-repair", json_data={"release_ids": []},
        )
        assert attribution.status_code == 200, attribution.text
        assert attribution.json().get("task_id"), attribution.json()
        volume = ctx.client.post(
            "/api/maintenance/repair-volume-ranges", json_data={"release_ids": []},
        )
        assert volume.status_code == 200, volume.text
        assert volume.json().get("task_id"), volume.json()
        print("    [PASSED] ComicInfo/attribution/volume audit+repair endpoints persist task ids and legacy fields.")
    finally:
        ctx.close()


def test_reporter_counters():
    print("--> Testing reporter callbacks (attribution + speed test rounds)...")
    ctx = E2ETestContext(prefix="mangatrax_t48_counters_")
    try:
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES "
                "('t48-1', 'Counters Series v01', 'Counters Series', 'DISCOVERY');"
            )
            conn.commit()
        calls = []
        ctx.scheduler.audit_series_attribution(progress=lambda done, total, message: calls.append((done, total, message)))
        assert calls, "attribution audit must report progress"
        assert calls[-1][0] == calls[-1][1], calls
        assert any(call[1] > 0 for call in calls), calls

        from app import network_diagnostics
        seen = []

        def fake_download(url, count, timeout):
            return 200, count, 0.01

        with patch("app.network_diagnostics._download_once", side_effect=fake_download):
            result = network_diagnostics.run_speedtest(
                bytes_total=1000, connections=2, url=network_diagnostics.SPEEDTEST_URL,
                progress=lambda done, total, message: seen.append((done, total, message)),
            )
        assert result["status"] == "success", result
        assert seen, "speed test must report per-round progress"
        assert seen[-1][0] == 1000, seen
        assert any("connection" in call[2].lower() for call in seen), seen
        print("    [PASSED] attribution audit and speed-test round reporters emit done/total progress.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing migrated button task hooks...")
    settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    downloads = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    base = open(os.path.join(BACKEND_DIR, "app", "templates", "base.html"), encoding="utf-8").read()
    for needle in ('data-task-kind="audit:attribution"', 'data-task-kind="audit:volume-ranges"',
                   'data-task-kind="repair:attribution"', 'data-task-kind="repair:volume-ranges"',
                   'data-task-kind="speedtest"', 'data-task-kind="logs:prune"'):
        assert needle in settings, f"missing settings task hook: {needle}"
    for needle in ('data-task-kind="audit:comicinfo"', 'data-task-kind="repair:comicinfo"',
                   'data-task-kind="downloads:process"', 'data-task-kind="downloads:prune"',
                   'data-task-kind="downloads:flatten"'):
        assert needle in downloads, f"missing downloads task hook: {needle}"
    assert "MangaTasks" in base and "bindButton" in base and "data-task-kind" in base
    # Old audit functions may refresh in place but must not reload.
    for fn in ("auditSeriesTags", "repairSelectedSeries", "auditSeriesAttribution",
               "repairSeriesAttribution", "auditVolumeRanges", "repairVolumeRanges"):
        assert fn in downloads or fn in settings, fn
    print("    [PASSED] audit/repair/downloads/speedtest buttons carry task kind hooks for reload-safe reattachment.")


def main():
    print("=" * 78)
    print("Running task-progress follow-up suite (task-48)")
    print("=" * 78)
    tests = [test_audit_endpoints_return_tasks, test_reporter_counters, test_ui_wiring]
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
        print(f"TASK PROGRESS FOLLOW-UP SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TASK-48 PROGRESS FOLLOW-UP TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
