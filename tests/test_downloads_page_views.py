#!/usr/bin/env python3
"""task-36 hotfix suite: downloads/index templates must never receive None numerics.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

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

from e2e.harness import E2ETestContext  # installs FastAPI/Jinja shims in Calibre
import app.main as main_module  # noqa: E402
from test_torbox_downloader import HASH, TORBOX_KEY  # noqa: E402

NUMERIC_KEYS = (
    "progress", "progress_pct", "size", "size_bytes", "eta", "eta_seconds",
    "dlspeed", "download_speed", "speed_bps", "downloaded_bytes", "total_bytes",
    "upload_speed", "ratio",
)


def _assert_numeric_row(row):
    for key in NUMERIC_KEYS:
        assert key in row, (key, row)
        value = row[key]
        assert value is not None, (key, row)
        assert isinstance(value, (int, float)), (key, type(value), row)


# ===========================================================================
# 1. Torbox view: no live info -> zero defaults (the live 500 case)
# ===========================================================================
def test_no_live_info_zero_defaults():
    print("--> Testing Torbox view with no live info (zero defaults)...")
    ctx = E2ETestContext(prefix="mangatrax_view_none_")
    try:
        row = {
            "id": 1, "title": "Series Vol. 01", "download_status": "DOWNLOADING",
            "downloader": "torbox", "downloader_id": "55", "torrent_hash": HASH,
            "torbox_progress_json": "{}", "volumes_json": "[1]", "missing_vols_json": "[1]",
        }
        view = main_module._torbox_release_view(row, None)
        _assert_numeric_row(view)
        assert view["eta"] == 0 and view["eta_seconds"] == 0, view
        assert view["dlspeed"] == 0 and view["speed_bps"] == 0, view
        assert view["progress"] == 0.0 and view["size"] == 0, view
        assert view["downloaded_bytes"] == 0 and view["total_bytes"] == 0, view
        print("    [PASSED] every numeric field is 0/0.0 with no live info (no None arithmetic).")
    finally:
        ctx.close()


# ===========================================================================
# 2. Fetching: local fetch values win over stale live Ready/None values
# ===========================================================================
def test_fetching_local_values_win():
    print("--> Testing fetching phase uses local values...")
    ctx = E2ETestContext(prefix="mangatrax_view_fetching_")
    try:
        progress = {
            "phase": "fetching", "downloaded_bytes": 420, "total_bytes": 1000,
            "speed_bps": 100, "progress_pct": 42.0,
        }
        row = {
            "id": 2, "title": "Series Vol. 02", "download_status": "DOWNLOADING",
            "downloader": "torbox", "downloader_id": "56", "torrent_hash": HASH,
            "torbox_progress_json": json.dumps(progress), "volumes_json": "[2]",
            "missing_vols_json": "[2]",
        }
        view = main_module._torbox_release_view(row, {"state": "ready", "eta": None, "download_speed": 0})
        _assert_numeric_row(view)
        assert view["phase"] == "fetching" and view["state"] == "fetching", view
        assert view["dlspeed"] == 100 and view["speed_bps"] == 100, view
        assert 0.41 <= view["progress"] <= 0.43, view
        assert view["eta"] > 0 and view["eta_seconds"] == view["eta"], view
        assert view["downloaded_bytes"] == 420 and view["total_bytes"] == 1000, view
        assert view["is_active"] is True, view
        print("    [PASSED] local fetch progress/speed/ETA are used instead of live Ready/None.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Live eta/speed used when not fetching
# ===========================================================================
def test_live_eta_speed_used():
    print("--> Testing valid live eta/speed are preserved...")
    ctx = E2ETestContext(prefix="mangatrax_view_live_")
    try:
        row = {
            "id": 3, "title": "Series Vol. 03", "download_status": "DOWNLOADING",
            "downloader": "torbox", "downloader_id": "57", "torrent_hash": HASH,
            "torbox_progress_json": "{}", "volumes_json": "[3]", "missing_vols_json": "[3]",
        }
        view = main_module._torbox_release_view(row, {
            "state": "downloading", "eta": 120, "download_speed": 5000,
            "progress": 0.5, "size": 1000, "seeds": 3,
        })
        _assert_numeric_row(view)
        assert view["eta"] == 120 and view["eta_seconds"] == 120, view
        assert view["dlspeed"] == 5000 and view["download_speed"] == 5000, view
        assert view["progress"] == 0.5 and view["size"] == 1000, view
        assert view["seeds"] == 3, view
        print("    [PASSED] live eta/speed/progress are normalised and preserved when valid.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Failed row: state failed, all numerics default, error preserved
# ===========================================================================
def test_failed_row_defaults():
    print("--> Testing failed Torbox row defaults...")
    ctx = E2ETestContext(prefix="mangatrax_view_failed_")
    try:
        row = {
            "id": 4, "title": "Series Vol. 04", "download_status": "FAILED",
            "downloader": "torbox", "downloader_id": "58", "torrent_hash": HASH,
            "torbox_progress_json": "{}", "download_error": "boom", "download_attempts": 2,
            "volumes_json": "[4]", "missing_vols_json": "[4]",
        }
        view = main_module._torbox_release_view(row, None)
        _assert_numeric_row(view)
        assert view["failed"] is True and view["state"] == "failed", view
        assert view["error"] == "boom" and view["attempts"] == 2, view
        assert view["eta"] == 0 and view["speed_bps"] == 0, view
        assert view["is_active"] is False, view
        print("    [PASSED] failed row is failed with 0 defaults and no None values.")
    finally:
        ctx.close()


# ===========================================================================
# 5. qBittorrent row builder also normalises missing fields
# ===========================================================================
def test_qbit_view_defaults():
    print("--> Testing qBittorrent row builder normalisation...")
    view = main_module._torrent_status_view({})
    for key in ("progress", "size_bytes", "downloaded_bytes", "download_speed",
                "upload_speed", "eta_seconds", "ratio", "seeds"):
        assert key in view and view[key] is not None, (key, view)
        assert isinstance(view[key], (int, float)), (key, type(view[key]))
    assert view["eta"] == 0 and view["eta_seconds"] == 0, view
    assert view["dlspeed"] == 0 and view["size"] == 0 and view["progress"] == 0.0, view
    print("    [PASSED] qBittorrent rows expose 0 defaults for missing fields too.")


# ===========================================================================
# 6. Template source guards
# ===========================================================================
def test_templates_hardened():
    print("--> Testing downloads/index template None guards...")
    downloads = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    index = open(os.path.join(BACKEND_DIR, "app", "templates", "index.html"), encoding="utf-8").read()
    assert "t.eta > 0" not in downloads, "bare t.eta > 0 must be guarded"
    for needle in (
        "(t.eta or 0)", "(t.progress or 0)", "(t.size or 0)",
        "(t.speed_bps or 0)", "(t.dlspeed or 0)",
        "(t.downloaded_bytes or 0)", "(t.total_bytes or 0)",
    ):
        assert needle in downloads, f"missing downloads guard: {needle}"
    for needle in ("(t.progress or 0)", "(t.dlspeed or 0)", "(t.size or 0)"):
        assert needle in index, f"missing index guard: {needle}"
    assert "(t.progress * 100)" not in downloads and "(t.progress * 100)" not in index
    print("    [PASSED] all template numeric arithmetic is None-guarded (ETA included).")


# ===========================================================================
# 7. Downloads page route stays 200 with a Torbox row lacking live eta
# ===========================================================================
def test_downloads_page_route_200():
    print("--> Testing /downloads route with a Torbox row without live eta...")
    ctx = E2ETestContext(prefix="mangatrax_view_page_")
    try:
        ctx.db.set_setting("downloader_engine", "torbox")
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        row = {
            "id": 7, "title": "Series Vol. 07", "download_status": "DOWNLOADING",
            "downloader": "torbox", "downloader_id": "59", "torrent_hash": HASH,
            "torbox_progress_json": "{}", "volumes_json": "[7]", "missing_vols_json": "[7]",
        }
        view = main_module._torbox_release_view(row, None)
        payload = {
            "status": "success", "engine": "torbox", "degraded": False,
            "downloader": {"enabled": True, "connected": True, "message": "ok", "version": "torbox", "last_checked_seconds_ago": 2},
            "torrents": [view], "active": [view], "count": 1,
            "totals": {"download_speed": 0, "upload_speed": 0, "progress": 0.0, "eta_seconds": 0},
        }
        with patch("app.main._build_downloader_status", return_value=payload):
            response = ctx.client.get("/downloads")
        # The CALIBRE runner shims Jinja (opaque client), so this asserts the route and
        # view data path; the template-source tests above cover the removed arithmetic.
        assert response.status_code == 200, response.text
        assert view["eta"] == 0 and view["eta_seconds"] == 0, view
        print("    [PASSED] downloads route returns 200 with an eta-less Torbox row (shimmed template).")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running downloads page 500 hotfix suite (task-36)")
    print("=" * 78)
    tests = [
        test_no_live_info_zero_defaults,
        test_fetching_local_values_win,
        test_live_eta_speed_used,
        test_failed_row_defaults,
        test_qbit_view_defaults,
        test_templates_hardened,
        test_downloads_page_route_200,
    ]
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
        print(f"DOWNLOADS HOTFIX SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL DOWNLOADS PAGE HOTFIX TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
