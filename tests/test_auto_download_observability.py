#!/usr/bin/env python3
"""task-56 suite: auto-download gates, diagnostics, dedupe incl. staging-only volumes."""

import json
import os
import sys
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402


def _release(ctx, guid, series, download_status, wanted, status="REQUESTED", routed=False):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, download_status, "
            "download_wanted_json, volumes_json, missing_vols_json, routed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?);",
            (guid, f"{series} v01", series, status, download_status,
             json.dumps(wanted), json.dumps(wanted), "2026-01-01 00:00:00" if routed else ""),
        )
        conn.commit()
        return cur.lastrowid


def _rel(title, series, guid):
    return SimpleNamespace(title=title, series_name=series, guid=guid, volumes=[1, 2, 3], is_bundle=True)


def test_dedupe_matrix():
    print("--> Testing in-flight/staging dedupe matrix...")
    ctx = E2ETestContext(prefix="mangatrax_t56_dedupe_")
    try:
        _release(ctx, "a1", "Dedupe Series", "DOWNLOADING", [1, 2, 3])
        a = _rel("Dedupe Series v01-03", "Dedupe Series", "b1")
        wanted, reserved = ctx.scheduler._auto_download_wanted(a, "Dedupe Series", [1, 2, 3])
        assert wanted == [] and reserved >= {1, 2, 3}, (wanted, reserved)
        logs = [row for row in ctx.db.get_logs(limit=20) if row["event_type"] == "IN_FLIGHT_DEDUPE"]
        assert logs and "no duplicate submitted" in logs[0]["message"], logs

        # Routed/COMPLETED (staging) rows reserve too.
        _release(ctx, "a2", "Staged Series", "COMPLETED", [5], routed=True)
        staged_rel = _rel("Staged Series v05", "Staged Series", "b2")
        wanted, _res = ctx.scheduler._auto_download_wanted(staged_rel, "Staged Series", [5])
        assert wanted == [], wanted

        # IN_CALIBRE releases reservation; only missing volume survives.
        _release(ctx, "a3", "Partial Series", "COMPLETED", [1, 2, 3], routed=True)
        partial = _rel("Partial Series v01-04", "Partial Series", "b3")
        wanted, _res = ctx.scheduler._auto_download_wanted(partial, "Partial Series", [1, 2, 3, 4])
        assert wanted == [4], wanted
        with ctx.db.get_connection() as conn:
            conn.execute("UPDATE releases SET status = 'IN_CALIBRE' WHERE guid = 'a3';")
            conn.commit()
        wanted, _res = ctx.scheduler._auto_download_wanted(partial, "Partial Series", [1, 2, 3, 4])
        assert wanted == [1, 2, 3, 4], wanted

        # Effective wanted subset (bundle fetched only 2,3) must not reserve 1.
        _release(ctx, "a4", "Subset Series", "DOWNLOADING", [2, 3])
        subset = _rel("Subset Series v01-03", "Subset Series", "b4")
        wanted, _res = ctx.scheduler._auto_download_wanted(subset, "Subset Series", [1, 2, 3])
        assert wanted == [1], wanted

        # Staging-only files: no tracked row, bounded scan must suppress.
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("staging_dir", staging)
        with open(os.path.join(staging, "Ghost Series v02.cbz"), "wb") as handle:
            handle.write(b"x")
        ctx.scheduler._staging_volume_cache = None
        ghost = _rel("Ghost Series v01-03", "Ghost Series", "b5")
        wanted, _res = ctx.scheduler._auto_download_wanted(ghost, "Ghost Series", [1, 2, 3])
        assert wanted == [1, 3], wanted
        print("    [PASSED] all-overlap suppressed, partial keeps missing, routed/effective subset reserved, IN_CALIBRE released, staging-only volume detected.")
    finally:
        ctx.close()


def test_decisions_and_diagnostics():
    print("--> Testing decision audit + diagnostics payload...")
    ctx = E2ETestContext(prefix="mangatrax_t56_diag_")
    try:
        rel = _rel("Decision Series v01", "Decision Series", "dec-1")
        ctx.scheduler._log_auto_download_decision(True, "submitted", "Feed A", rel, "Decision Series", [1])
        ctx.scheduler._log_auto_download_decision(False, "global_disabled", "Feed A", rel, "Decision Series", [2])
        events = {row["event_type"] for row in ctx.db.get_logs(limit=20)}
        assert {"AUTO_DOWNLOAD_SUBMITTED", "AUTO_DOWNLOAD_SKIPPED"} <= events, events
        diagnostics = ctx.scheduler.get_auto_download_diagnostics()
        for key in ("global_enabled", "engine", "engine_ready", "feeds", "monitored_series",
                    "last_poll", "recent_decisions", "checklist", "prerequisites"):
            assert key in diagnostics, key
        assert diagnostics["recent_decisions"], diagnostics
        assert diagnostics["checklist"], diagnostics
        assert any(item["event"] == "AUTO_DOWNLOAD_SUBMITTED" for item in diagnostics["recent_decisions"])
        # Endpoint returns same payload.
        response = ctx.client.get("/api/downloads/auto-download/diagnostics")
        assert response.status_code == 200, response.text
        assert response.json()["engine_ready"] == diagnostics["engine_ready"]
        print("    [PASSED] submitted/skipped activity entries and diagnostics endpoint/payload/checklist present.")
    finally:
        ctx.close()


def test_engine_and_ui_wiring():
    print("--> Testing engine readiness + poll gate + UI wiring...")
    ctx = E2ETestContext(prefix="mangatrax_t56_engine_")
    try:
        ctx.db.set_setting("downloader_engine", "torbox")
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", "key")
        ready, reason = ctx.scheduler._auto_download_engine_ready()
        assert ready is True, reason
        ctx.db.set_setting("torbox_api_key", "")
        ready, reason = ctx.scheduler._auto_download_engine_ready()
        assert ready is False and "api_key" in reason, (ready, reason)
        source = open(os.path.join(BACKEND_DIR, "app", "scheduler.py"), encoding="utf-8").read()
        for needle in ("AUTO_DOWNLOAD_SUBMITTED", "AUTO_DOWNLOAD_SKIPPED", "global_disabled",
                       "feed_disabled", "engine_unavailable", "no_missing_volumes", "no_download_url",
                       "all_volumes_inflight", "strict_skip",
                       "submit_error", "get_auto_download_diagnostics"):
            assert needle in source, f"missing gate/observability: {needle}"
        page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
        for needle in ("Auto-download diagnostics", "refreshAutoDownloadDiagnostics",
                       "/api/downloads/auto-download/diagnostics", "auto-download-checklist",
                       "auto-download-decisions"):
            assert needle in page, f"missing diagnostics UI: {needle}"
        settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
        assert "Auto-download new releases" in settings
        assert "Auto-download new releases</label>" not in settings or "Prerequisites:" in settings
        print("    [PASSED] engine readiness, all decision reasons, diagnostics card/endpoint and wording wired.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running auto-download observability suite (task-56)")
    print("=" * 78)
    tests = [test_dedupe_matrix, test_decisions_and_diagnostics, test_engine_and_ui_wiring]
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
        print(f"AUTO-DOWNLOAD SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL AUTO-DOWNLOAD OBSERVABILITY TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
