#!/usr/bin/env python3
"""task-46 suite: in-place release download state, chips/polling, no page reload.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import sys
import traceback
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
import app.main as main_module  # noqa: E402


def test_state_matrix():
    print("--> Testing release download-state matrix...")
    cases = [
        ({}, "not_downloaded"),
        ({"download_status": "DOWNLOADING", "torbox_progress_json": '{"progress_pct": 42, "speed_bps": 1000}'}, "downloading"),
        ({"download_status": "QUEUED"}, "downloading"),
        ({"download_status": "COMPLETED", "downloaded_at": "2026-01-01"}, "downloaded"),
        ({"download_status": "COMPLETED", "routed_at": "2026-01-01"}, "in_staging"),
        ({"status": "IN_CALIBRE"}, "in_calibre"),
        ({"download_status": "FAILED", "download_error": "boom"}, "failed"),
        ({"download_status": "PENDING_DEDUPE"}, "waiting_inflight"),
    ]
    for release, expected in cases:
        state = main_module.release_download_state(release)
        assert state["state"] == expected, (release, state)
        assert state["can_download"] is (expected in ("not_downloaded", "failed")), state
    downloading = main_module.release_download_state(cases[1][0])
    assert downloading["progress_pct"] == 42.0 and downloading["speed_bps"] == 1000, downloading
    failed = main_module.release_download_state(cases[6][0])
    assert failed["error"] == "boom" and failed["can_retry"] is True, failed
    staging = main_module.release_download_state(cases[4][0])
    assert staging["can_download"] is False and staging["terminal"] is True, staging
    print("    [PASSED] downloading/downloaded/in_staging/in_calibre/failed/waiting/not_downloaded precedence and flags.")


def test_state_endpoint_and_post():
    print("--> Testing state endpoint + POST state payload...")
    ctx = E2ETestContext(prefix="mangatrax_release_state_")
    try:
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, download_url) "
                "VALUES ('state-1', 'State Series v01', 'State Series', 'DISCOVERY', ?);",
                ("magnet:?xt=urn:btih:state",),
            )
            release_id = cur.lastrowid
            conn.commit()
        missing = ctx.client.get("/api/releases/999999/state")
        assert missing.status_code == 404, missing.status_code
        state = ctx.client.get(f"/api/releases/{release_id}/state")
        assert state.status_code == 200, state.text
        assert state.json()["state"] == "not_downloaded", state.json()
        qbit = MagicMock()
        qbit.add_torrent.return_value = True
        qbit.extract_torrent_hash.return_value = "hash123"
        with patch("app.main.resolve_downloader_engine", return_value="qbit"), \
             patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit), \
             patch("app.main.scheduler.get_qbit_client", return_value=qbit), \
             patch.object(main_module, "scheduler", ctx.scheduler), \
             patch("app.main.scheduler.process_downloads_to_staging", return_value={}):
            response = ctx.client.post(f"/api/releases/{release_id}/download")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success" and "state" in body, body
        assert body["state"]["state"] in ("downloading", "downloaded", "not_downloaded"), body
        print("    [PASSED] endpoint returns derived state (404 when missing); POST includes state.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing chips/button/poll/delegation wiring...")
    releases = open(os.path.join(BACKEND_DIR, "app", "templates", "releases.html"), encoding="utf-8").read()
    card = open(
        os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_card_chunk.html"), encoding="utf-8",
    ).read()
    flat = open(
        os.path.join(BACKEND_DIR, "app", "templates", "partials", "flat_release_row_chunk.html"), encoding="utf-8",
    ).read()
    for source in (card, flat):
        assert "release-state-chip" in source, "missing state chip"
    for source in (card, flat):
        assert "data-release-id=" in source, "missing data-release-id row hook"
        assert "download-release-btn" in source, "missing stable download button class"
        assert "can_download" in source, "download button not gated by state"
    assert "location.reload()" not in releases.split("async function downloadRelease")[1].split("</script>")[0]
    for needle in (
        "/api/releases/${releaseId}/state", "setTimeout(poll, 1500)", "updateReleaseStateDom",
        "addEventListener('click'", "download-release-btn", "Retry", "updateAllCardSummaries",
        "release-card-summary",
    ):
        assert needle in releases, f"missing JS wiring: {needle}"
    prioritized = open(os.path.join(BACKEND_DIR, "app", "templates", "prioritized.html"), encoding="utf-8").read()
    assert "/state" in prioritized and "location.reload()" not in prioritized.split("async function downloadRelease")[1].split("</script>")[0]
    print("    [PASSED] chips, state-gated button, delegated click, polling, Retry and per-card summary wired; no reload.")


def main():
    print("=" * 78)
    print("Running release download state suite (task-46)")
    print("=" * 78)
    tests = [test_state_matrix, test_state_endpoint_and_post, test_ui_wiring]
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
        print(f"RELEASE DOWNLOAD STATE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL RELEASE DOWNLOAD STATE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
