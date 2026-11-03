#!/usr/bin/env python3
"""task-29 suite: Torbox latency — off-loop polling/fetch, local progress, timings.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import io
import json
import logging
import os
import shutil
import sys
import tempfile
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

from app.downloader.torbox import TorboxDownloader  # noqa: E402
from test_torbox_downloader import (  # noqa: E402
    HASH,
    TORBOX_KEY,
    Ctx,
    FakeApi,
    FakeTorboxService,
    ScriptedStream,
    _insert_release,
)


def _ready_ctx(ctx, max_gb=64):
    ctx.db.set_setting("downloads_dir", os.path.join(ctx.tmpdir, "downloads"))
    ctx.db.set_setting("staging_dir", os.path.join(ctx.tmpdir, "staging"))
    ctx.db.set_setting("torbox_enabled", "true")
    ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
    ctx.db.set_setting("torbox_delete_after_download", "false")
    ctx.db.set_setting("torbox_max_download_gb", str(max_gb))
    ctx.db.set_setting("torbox_strict_volume_selection", "true")
    ctx.db.set_setting("torbox_timeout_hours", "6")


def _fresh_release(ctx, guid, title, torrent_id="55"):
    rid = _insert_release(
        ctx.db, guid, title, downloader="torbox", downloader_id=str(torrent_id),
        download_status="DOWNLOADING",
        downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        volumes=[1], missing_volumes=[1], torrent_hash=HASH,
    )
    return rid


class _SlowFetchService(FakeTorboxService):
    """Status ready immediately; fetch_files blocks until released."""

    def __init__(self):
        super().__init__(state="ready", files=[{"id": 1, "name": "Series v01.cbz", "size": 10}])
        self.block = threading.Event()
        self.started = threading.Event()

    def fetch_files(self, handle, files, dest_dir, progress_cb=None):
        self.fetch_calls.append((handle, files, dest_dir))
        self.started.set()
        self.block.wait(3.0)
        if progress_cb:
            progress_cb("Series v01.cbz", 10)
        return [{"name": "Series v01.cbz", "path": os.path.join(dest_dir, "Series v01.cbz"), "size": 10}]


# ===========================================================================
# 1. Throttled progress writes + status payload fields
# ===========================================================================
def test_progress_throttling_and_status_fields():
    print("--> Testing throttled local fetch progress + status fields...")
    from e2e.harness import E2ETestContext

    import app.main as main_module

    ctx = E2ETestContext(prefix="mangatrax_latency_progress_")
    try:
        rid = _insert_release(
            ctx.db, "progress-guid", "Series Vol. 01", downloader="torbox",
            downloader_id="55", download_status="DOWNLOADING",
            downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            volumes=[1], missing_volumes=[1], torrent_hash=HASH,
        )
        cb = ctx.scheduler._torbox_progress_callback(rid, 1_000_000)
        cb("Series v01.cbz", 100)
        first = ctx.db.get_torbox_progress(rid)
        assert first["phase"] == "fetching" and first["downloaded_bytes"] == 100, first
        assert first["total_bytes"] == 1_000_000 and first["progress_pct"] == 0.0, first
        cb("Series v01.cbz", 200)  # <1% and <2s -> throttled away
        second = ctx.db.get_torbox_progress(rid)
        assert second["downloaded_bytes"] == 100, second
        cb("Series v01.cbz", 1_000_000)  # final always persists
        final = ctx.db.get_torbox_progress(rid)
        assert final["downloaded_bytes"] == 1_000_000 and final["progress_pct"] == 100.0, final

        ctx.db.update_torbox_progress(
            rid, phase="fetching", downloaded_bytes=512_000, total_bytes=1_200_000,
            speed_bps=8_400_000,
        )
        row = ctx.db.get_release_by_id(rid)
        view = main_module._torbox_release_view(row, {"state": "ready", "size": 1_200_000, "files": []})
        assert view["phase"] == "fetching", view
        assert view["state"] == "fetching", view
        assert abs(view["progress_pct"] - 42.67) < 0.1, view
        assert view["downloaded_bytes"] == 512_000 and view["total_bytes"] == 1_200_000, view
        assert view["speed_bps"] == 8_400_000, view
        assert view["last_checked_at"] is not None
        print("    [PASSED] progress writes are throttled and status exposes fetch pct/speed/bytes.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Async fetch does not block the poller (second release still detected)
# ===========================================================================
def test_async_fetch_does_not_block_poll():
    print("--> Testing async fetch keeps the poll pass fast...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        first = _fresh_release(ctx, "async-guid-1", "Series Vol. 01", torrent_id="61")
        second = _fresh_release(ctx, "async-guid-2", "Series Vol. 02", torrent_id="62")
        service = _SlowFetchService()
        started = time.monotonic()
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.poll_torbox_downloads(async_fetch=True)
        elapsed = time.monotonic() - started
        assert result["queued_fetch"] == 2, result
        assert result["processed"] == 2, result
        assert elapsed < 0.5, f"poll pass blocked for {elapsed:.2f}s by a slow fetch"
        assert service.started.wait(1.0), "fetch threads did not start"
        for rid in (first, second):
            progress = ctx.db.get_torbox_progress(rid)
            assert progress.get("phase") in ("ready", "fetching", "routing"), (rid, progress)
        service.block.set()
        ctx.scheduler.wait_for_torbox_fetches(timeout=5.0)
        assert ctx.db.get_release_by_id(first)["download_status"] == "COMPLETED"
        assert ctx.db.get_release_by_id(second)["download_status"] == "COMPLETED"
        print("    [PASSED] slow fetch runs off-loop; both ready releases are detected and complete.")
    finally:
        try:
            ctx.scheduler.wait_for_torbox_fetches(timeout=3.0)
        except Exception:
            pass
        ctx.close()


# ===========================================================================
# 3. Immediate post-submit poll signal
# ===========================================================================
def test_immediate_poll_after_submit():
    print("--> Testing submit signals an immediate Torbox poll...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        rid = _insert_release(ctx.db, "signal-guid", "Series Vol. 01", volumes=[1], missing_volumes=[1])
        service = FakeTorboxService(state="downloading")
        with patch("app.scheduler.build_downloader", return_value=service):
            with patch.object(ctx.scheduler, "request_torbox_poll") as request:
                result = ctx.scheduler.submit_torbox_download(
                    rid, "Series Vol. 01", f"magnet:?xt=urn:btih:{HASH}", missing_volumes=[1],
                )
        assert result["success"] is True, result
        request.assert_called()
        print("    [PASSED] successful submit wakes the Torbox poller immediately.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Timing log line + persisted timestamps + audit details
# ===========================================================================
def test_timing_log_and_audit():
    print("--> Testing ready→fetch→route timings are logged and persisted...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        rid = _fresh_release(ctx, "timing-guid", "Series Vol. 01")
        service = FakeTorboxService(state="ready", files=[{"id": 1, "name": "Series v01.cbz", "size": 10}])
        records = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Handler(logging.INFO)
        logger = logging.getLogger("mangatrax.scheduler")
        logger.addHandler(handler)
        try:
            with patch("app.scheduler.build_downloader", return_value=service):
                result = ctx.scheduler.poll_torbox_downloads()
        finally:
            logger.removeHandler(handler)
        assert result["completed"] == 1, result
        messages = [r.getMessage() for r in records if "Torbox latency for" in r.getMessage()]
        assert messages, [r.getMessage() for r in records]
        text = messages[-1]
        assert "ready→fetch" in text and "fetch" in text and "route" in text, text
        assert "GiB at" in text and "MiB/s" in text, text
        progress = ctx.db.get_torbox_progress(rid)
        for key in ("ready_detected_at", "fetch_started_at", "fetch_finished_at", "routed_at"):
            assert progress.get(key), (key, progress)
        assert progress.get("phase") == "done", progress
        events = [row for row in ctx.db.get_logs(limit=100) if row["event_type"] == "TORBOX_DOWNLOADED"]
        assert events, "TORBOX_DOWNLOADED audit missing"
        details = json.loads(events[-1]["details"])
        assert "latency" in details and details["latency"]["fetch_seconds"] >= 0, details
        assert "ready_to_fetch_seconds" in details["latency"], details
        assert "Torbox latency" in events[-1]["details"] or "latency:" in events[-1].get("details", "")
        print("    [PASSED] latency INFO line, timestamps and audit details are populated.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Adaptive poll interval
# ===========================================================================
def test_adaptive_poll_interval():
    print("--> Testing adaptive Torbox poll interval...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        ctx.db.set_setting("torbox_poll_seconds", "5")
        assert ctx.scheduler._torbox_next_poll_interval() >= 15.0, "idle should back off"
        rid = _fresh_release(ctx, "adaptive-guid", "Series Vol. 01")
        ctx.db.update_torbox_progress(rid, phase="fetching")
        assert ctx.scheduler._torbox_next_poll_interval() == 5.0, "busy should poll at the floor"
        ctx.db.update_torbox_progress(rid, phase="done")
        assert ctx.scheduler._torbox_next_poll_interval() >= 15.0
        print("    [PASSED] adaptive interval: floor while fetching, backed off when idle.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Check-now endpoint
# ===========================================================================
def test_check_now_endpoint():
    print("--> Testing POST /api/downloads/torbox/poll (Check now)...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_latency_checknow_")
    try:
        ctx.db.set_setting("downloader_engine", "torbox")
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        fake_result = {
            "status": "ok", "processed": 3, "completed": 1, "failed": 0,
            "queued_fetch": 2, "fetching": 1, "last_checked_at": "2026-01-01T00:00:00Z",
        }
        with patch.object(ctx.scheduler, "torbox_poll_now", return_value=fake_result) as poll_now:
            with patch("app.main._build_downloader_status", return_value={"torrents": [], "downloader": {}}):
                response = ctx.client.post("/api/downloads/torbox/poll")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["checked"] == 3 and body["queued_fetch"] == 2 and body["fetching"] == 1, body
        assert body["last_checked_at"] == "2026-01-01T00:00:00Z", body
        poll_now.assert_called_once()
        template = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
        assert "Check now" in template and "checkTorboxNow" in template
        assert "torbox-progress-text" in template and "Ready — fetch queued" in template
        print("    [PASSED] Check-now runs a poll and returns fresh counters; UI controls are wired.")
    finally:
        ctx.close()


# ===========================================================================
# 7. Downloader reports granular stream progress (not just once per file)
# ===========================================================================
def test_downloader_stream_progress_is_granular():
    print("--> Testing granular progress callbacks from the streamed download...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        dest = tempfile.mkdtemp(prefix="mangatrax_latency_stream_")
        reports = []
        try:
            api = FakeApi().add("/torrents/requestdl", data={"url": "https://cdn.example/file"})
            payload = b"x" * (2 * 1024 * 1024 + 123)
            stream = ScriptedStream([payload])
            downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True, max_file_bytes=64 * 1024 ** 3)
            with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
                with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                    saved = downloader.fetch_files(
                        {"torrent_id": 70},
                        [{"id": 1, "name": "Series v01.cbz", "size": len(payload)}],
                        dest,
                        progress_cb=lambda label, written: reports.append((label, written)),
                    )
            assert len(saved) == 1, saved
            assert len(reports) >= 2, reports  # 1 MiB step + final, not only once per file
            assert reports[-1][1] == len(payload), reports
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        print("    [PASSED] stream progress fires during the download (throttled), not only at the end.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running Torbox latency suite (task-29)")
    print("=" * 78)
    tests = [
        test_progress_throttling_and_status_fields,
        test_async_fetch_does_not_block_poll,
        test_immediate_poll_after_submit,
        test_timing_log_and_audit,
        test_adaptive_poll_interval,
        test_check_now_endpoint,
        test_downloader_stream_progress_is_granular,
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
        print(f"TORBOX LATENCY SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TORBOX LATENCY TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
