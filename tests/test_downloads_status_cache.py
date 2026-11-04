#!/usr/bin/env python3
"""task-32 suite: Torbox status last-good cache/single-flight + live UI wiring.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # installs the FastAPI/Jinja shims in Calibre
import app.main as main_module  # noqa: E402
from test_torbox_downloader import HASH, TORBOX_KEY, FakeTorboxService  # noqa: E402


def _reset_cache():
    with main_module._TORBOX_STATUS_LOCK:
        main_module._TORBOX_STATUS_CACHE.update({"payload": None, "at": 0.0, "building": False})
        main_module._TORBOX_SPEED_HISTORY.clear()


def _row(release_id=1, speed=0):
    return {
        "release_id": release_id, "hash": f"hash{release_id}", "name": f"Series v{release_id:02d}",
        "state": "downloading", "phase": "fetching", "progress": 0.5, "progress_pct": 50.0,
        "size_bytes": 100, "size": 100, "downloaded_bytes": 50, "total_bytes": 100,
        "download_speed": speed, "speed_bps": speed, "dlspeed": speed,
        "is_active": True, "failed": False, "error": "", "can_retry": False,
        "eta": 10, "eta_seconds": 10, "last_checked_at": "", "last_checked_seconds_ago": 1,
        "downloaded_at": "", "routed_at": "", "download_files": [],
    }


def _payload(torrents=None, connected=True):
    torrents = torrents or []
    return {
        "status": "success" if connected else "degraded",
        "degraded": not connected, "engine": "torbox", "generated_at": "",
        "downloader": {"enabled": True, "connected": connected, "message": "ok" if connected else "down"},
        "torrents": torrents,
        "active": [t for t in torrents if t.get("is_active")],
        "count": len(torrents),
        "totals": {"download_speed": 0, "upload_speed": 0, "progress": 0, "eta_seconds": None},
        "live_state_available": connected,
    }


# ===========================================================================
# 1. TTL cache: repeated calls within ~1 s reuse one build
# ===========================================================================
def test_cache_ttl_reuses_build():
    print("--> Testing Torbox status TTL cache...")
    _reset_cache()
    build = MagicMock(return_value=_payload([_row(1, 1000)]))
    with patch.object(main_module, "_build_torbox_downloader_status", build):
        first = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
        second = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
    assert build.call_count == 1, build.call_count
    assert second["torrents"] and len(second["torrents"]) == 1, second
    assert second["stale"] is False, second
    assert second["cache_age_seconds"] >= 0, second
    assert second["generated_at"], second
    print("    [PASSED] a second call inside the TTL reuses the cached payload (one build).")


# ===========================================================================
# 2. Build failure serves last-good payload with stale metadata
# ===========================================================================
def test_failure_serves_stale_last_good():
    print("--> Testing last-good payload served on transient failure...")
    _reset_cache()
    rows = [_row(1, 5000), _row(2, 0)]
    with patch.object(main_module, "_build_torbox_downloader_status", return_value=_payload(rows)):
        good = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
    assert [t["release_id"] for t in good["torrents"]] == [1, 2], good["torrents"]
    with main_module._TORBOX_STATUS_LOCK:
        main_module._TORBOX_STATUS_CACHE["at"] = time.time() - 5.0
    with patch.object(main_module, "_build_torbox_downloader_status", return_value=_payload([], False)):
        stale = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
    assert stale["stale"] is True, stale
    assert stale["stale_age_seconds"] >= 4, stale
    assert stale["degraded"] is True, stale
    assert stale["live_state_available"] is False, stale
    assert [t["release_id"] for t in stale["torrents"]] == [1, 2], stale["torrents"]
    print("    [PASSED] transient failure keeps the last rows and flags them stale.")
    _reset_cache()


# ===========================================================================
# 3. No cache + live API down -> DB-derived rows, live_state_available false
# ===========================================================================
def test_db_rows_when_live_down():
    print("--> Testing DB rows are served while the live API is down...")
    ctx = E2ETestContext(prefix="mangatrax_status_down_")
    try:
        ctx.db.set_setting("downloader_engine", "torbox")
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, link, download_url, status, "
                "download_status, downloader, downloader_id, torrent_hash, volumes_json, "
                "missing_vols_json) VALUES (?, ?, ?, ?, ?, 'REQUESTED', 'DOWNLOADING', "
                "'torbox', '55', ?, '[1]', '[1]');",
                ("down-guid", "Series Vol. 01", "Series", "https://nyaa.si/view/1",
                 f"magnet:?xt=urn:btih:{HASH}", HASH),
            )
            conn.commit()
        fake = FakeTorboxService()
        fake.test_connection = MagicMock(return_value={"success": False, "message": "timeout"})
        _reset_cache()
        with patch("app.main.build_downloader", return_value=fake):
            payload = main_module._build_downloader_status(1.0)
        assert payload["live_state_available"] is False, payload.keys()
        assert payload["torrents"], "DB-derived rows must be present when live state is down"
        assert payload["torrents"][0]["name"] == "Series Vol. 01", payload["torrents"][0]
        assert payload["degraded"] is True, payload
        print("    [PASSED] live API down with no cache still shows DB rows (live_state_available false).")
    finally:
        _reset_cache()
        ctx.close()


# ===========================================================================
# 4. Genuine empty: connected live API + no DB rows
# ===========================================================================
def test_genuine_empty_payload():
    print("--> Testing a genuinely empty queue stays empty (not stale)...")
    _reset_cache()
    with patch.object(main_module, "_build_torbox_downloader_status", return_value=_payload([])):
        payload = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
    assert payload["torrents"] == [], payload
    assert payload["stale"] is False, payload
    assert payload["live_state_available"] is True, payload
    print("    [PASSED] connected + no DB rows = genuine empty, not a stale artifact.")
    _reset_cache()


# ===========================================================================
# 5. Single-flight: a concurrent call never starts a second build
# ===========================================================================
def test_single_flight():
    print("--> Testing single-flight status builds...")
    _reset_cache()
    entered = threading.Event()
    release = threading.Event()
    calls = {"count": 0}

    def slow_build(payload, timeout):
        calls["count"] += 1
        entered.set()
        release.wait(2.0)
        return _payload([_row(1)])

    result = {}

    def worker():
        result["payload"] = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)

    with patch.object(main_module, "_build_torbox_downloader_status", slow_build):
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(1.0), "builder did not start"
        started = time.monotonic()
        concurrent = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
        elapsed = time.monotonic() - started
        release.set()
        thread.join(3.0)
    assert calls["count"] == 1, calls
    assert elapsed < 0.5, f"concurrent call blocked for {elapsed:.2f}s"
    assert concurrent["live_state_available"] is False, concurrent
    assert concurrent["degraded"] is True, concurrent
    assert result["payload"]["torrents"], result["payload"]
    print("    [PASSED] concurrent status calls share one build and return promptly.")
    _reset_cache()


# ===========================================================================
# 6. Speed history + rolling average on rows
# ===========================================================================
def test_speed_history():
    print("--> Testing capped speed history + rolling average...")
    _reset_cache()
    speeds = iter([1000, 2000, 3000])
    with patch.object(main_module, "_build_torbox_downloader_status",
                      side_effect=lambda payload, timeout: _payload([_row(1, next(speeds))])):
        first = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
        with main_module._TORBOX_STATUS_LOCK:
            main_module._TORBOX_STATUS_CACHE["at"] = 0.0
        second = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
        with main_module._TORBOX_STATUS_LOCK:
            main_module._TORBOX_STATUS_CACHE["at"] = 0.0
        third = main_module._torbox_status_cached({"engine": "torbox"}, 1.0)
    history = third["torrents"][0]["speed_history"]
    assert history == [1000, 2000, 3000], history
    assert third["torrents"][0]["speed_avg_bps"] == 2000, third["torrents"][0]
    print("    [PASSED] rows carry capped speed_history and a rolling speed_average.")
    _reset_cache()


# ===========================================================================
# 7. Template wiring: in-place renderers, 1 s/5 s poll, no eager reloads
# ===========================================================================
def test_template_live_ui_wiring():
    print("--> Testing live UI template wiring...")
    template = open(
        os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8",
    ).read()
    for needle in (
        "renderTorboxQueue", "renderInventory", "refreshDownloadsNow",
        "active ? 1000 : 5000", "_sparklineSvg", "speed_history",
        "torbox-reconnecting", "Reconnecting", "torbox-updated", "Updated ",
        "torbox-queue-body", "inventory-body",
    ):
        assert needle in template, f"missing live-UI wiring: {needle}"
    assert "autoRefreshDownloadsInventory" not in template, "inventory reloader must be gone"
    assert "refreshTorboxProgress" not in template, "old progress-only poller must be gone"
    # Only the qBittorrent/tag-modal actions may reload now; Torbox UI flow must not.
    reloads = template.count("location.reload()")
    assert reloads <= 4, f"too many full-page reloads remain: {reloads}"
    print("    [PASSED] in-place renderers, adaptive 1s/5s polling, sparkline and stale chip wired.")


def main():
    print("=" * 78)
    print("Running Torbox status cache + live UI suite (task-32)")
    print("=" * 78)
    tests = [
        test_cache_ttl_reuses_build,
        test_failure_serves_stale_last_good,
        test_db_rows_when_live_down,
        test_genuine_empty_payload,
        test_single_flight,
        test_speed_history,
        test_template_live_ui_wiring,
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
        print(f"STATUS CACHE/LIVE UI SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STATUS CACHE + LIVE UI TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
