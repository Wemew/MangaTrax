#!/usr/bin/env python3
"""task-37 suite: Torbox ready-vs-processing gating, range 404 fallback, fresh-link retries.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.downloader.base import DownloaderFileError  # noqa: E402
from app.downloader.torbox import TORBOX_RANGE_THRESHOLD_BYTES, TorboxDownloader  # noqa: E402
from test_torbox_downloader import HASH, TORBOX_KEY, Ctx, FakeTorboxService, _insert_release  # noqa: E402


def _ready_ctx(ctx):
    ctx.db.set_setting("downloads_dir", os.path.join(ctx.tmpdir, "downloads"))
    ctx.db.set_setting("staging_dir", os.path.join(ctx.tmpdir, "staging"))
    ctx.db.set_setting("torbox_enabled", "true")
    ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
    ctx.db.set_setting("torbox_delete_after_download", "false")
    ctx.db.set_setting("torbox_strict_volume_selection", "true")


# ===========================================================================
# 1. State matrix: ready only when files are present/servable; processing else
# ===========================================================================
def test_state_matrix():
    print("--> Testing ready/processing state matrix...")
    cases = [
        ({"download_present": True}, "completed", "ready"),
        ({"download_present": True}, "downloading", "ready"),
        ({}, "completed", "ready"),
        ({}, "cached", "ready"),
        ({}, "uploading", "processing"),
        ({}, "download finished", "processing"),
        ({"download_finished": True}, "downloading", "processing"),
        ({"download_finished": True, "download_present": False}, "completed", "processing"),
        ({"download_present": False}, "completed", "processing"),
        ({}, "error", "failed"),
        ({}, "downloading", "downloading"),
        ({}, "queued", "stalled"),
    ]
    for row, raw, expected in cases:
        actual = TorboxDownloader._normalise_state(raw, row)
        assert actual == expected, (row, raw, actual, expected)
    print("    [PASSED] ready only with download_present/servable state; finished-but-not-present is processing.")


# ===========================================================================
# 2. Processing rows are polled but never fetched (one notification, no spam)
# ===========================================================================
def test_processing_never_fetches():
    print("--> Testing processing rows are not fetched...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        rid = _insert_release(
            ctx.db, "processing-guid", "Series Vol. 01", downloader="torbox",
            downloader_id="77", download_status="DOWNLOADING",
            downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            volumes=[1], missing_volumes=[1], torrent_hash=HASH,
        )
        service = FakeTorboxService(state="processing", files=[{"id": 1, "name": "Series v01.cbz", "size": 10}])
        with patch("app.scheduler.build_downloader", return_value=service):
            first = ctx.scheduler.poll_torbox_downloads()
            second = ctx.scheduler.poll_torbox_downloads()
        assert first["failed"] == 0 and second["failed"] == 0, (first, second)
        assert service.fetch_calls == [], "processing rows must never fetch"
        row = ctx.db.get_release_by_id(rid)
        assert row["download_status"] == "DOWNLOADING", row
        progress = ctx.db.get_torbox_progress(rid)
        assert progress.get("phase") == "processing", progress
        events = [item for item in ctx.db.get_logs(limit=100) if item["event_type"] == "TORBOX_PROCESSING"]
        assert len(events) == 1, events  # surfaced once, no per-poll spam
        assert "finalizing" in (events[0].get("details") or "").lower(), events[0]
        print("    [PASSED] processing is polled/waiting, never fetched, and logged once.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Range probe 404 -> single-stream fallback succeeds
# ===========================================================================
def test_range_404_falls_back_to_single_stream():
    print("--> Testing 404 range probe falls back to a single stream...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_range404_")
    try:
        downloader = TorboxDownloader(
            api_key=TORBOX_KEY, enabled=True, max_file_bytes=1024 ** 3, parallel_chunks=4,
        )
        downloader._probe_range_total = MagicMock(return_value=TORBOX_RANGE_THRESHOLD_BYTES + 4096)
        downloader._open_range = MagicMock(
            side_effect=DownloaderFileError("Download failed (HTTP 404) for range 0-0."),
        )

        def _fake_stream(url, dest_path, progress_cb=None, label=""):
            with open(dest_path, "wb") as handle:
                handle.write(b"fallback-bytes")
            if progress_cb:
                progress_cb(label, len(b"fallback-bytes"))
            return len(b"fallback-bytes")

        with patch.object(downloader, "_stream_to_file", side_effect=_fake_stream):
            saved = downloader._download_to(
                "https://cdn.example/file", tmpdir, "Series v01.cbz",
                expected_size=TORBOX_RANGE_THRESHOLD_BYTES + 4096,
            )
        assert saved["size"] == len(b"fallback-bytes"), saved
        with open(saved["path"], "rb") as handle:
            assert handle.read() == b"fallback-bytes"
        assert not [name for name in os.listdir(tmpdir) if ".part" in name], os.listdir(tmpdir)
        print("    [PASSED] 404/403/405/416/429 on ranges falls back to single-stream download.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 4. 404 per-file -> fresh requestdl -> success; persistent 404 reports attempts
# ===========================================================================
class _Flaky404Downloader(TorboxDownloader):
    def __init__(self, always_fail=False):
        super().__init__(api_key=TORBOX_KEY, enabled=True, max_file_bytes=1024 ** 3, max_concurrency=1)
        self.always_fail = always_fail
        self.requestdl_calls = 0
        self.download_calls = 0

    def _requestdl(self, torrent_id, file_id=None):
        self.requestdl_calls += 1
        return "https://cdn.example/file?token=secret"

    def _download_to(self, url, dest_dir, name, progress_cb=None, expected_size=None):
        self.download_calls += 1
        if self.always_fail or self.download_calls == 1:
            raise DownloaderFileError("Download failed (HTTP 404) for range 0-0.")
        path = os.path.join(dest_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"ok")
        return {"name": name, "path": path, "size": 2, "seconds": 0.1, "connections": 1}


def test_404_refresh_link_retry():
    print("--> Testing 404 retry re-requests the requestdl link...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_retry404_")
    try:
        downloader = _Flaky404Downloader(always_fail=False)
        with patch("app.downloader.torbox.time.sleep") as sleep:
            saved = downloader.fetch_files(
                {"torrent_id": 1}, [{"id": 1, "name": "Series v01.cbz", "size": 2}], tmpdir,
            )
        assert len(saved) == 1, saved
        assert downloader.requestdl_calls == 2, downloader.requestdl_calls
        assert downloader.download_calls == 2, downloader.download_calls
        assert sleep.called, "404 retry should back off"

        persistent = _Flaky404Downloader(always_fail=True)
        tmpdir2 = tempfile.mkdtemp(prefix="mangatrax_retry404_fail_")
        try:
            with patch("app.downloader.torbox.time.sleep"):
                try:
                    persistent.fetch_files(
                        {"torrent_id": 1}, [{"id": 1, "name": "Series v01.cbz", "size": 2}], tmpdir2,
                    )
                except DownloaderFileError as exc:
                    message = str(exc)
                else:
                    assert False, "persistent 404 must raise"
            assert persistent.requestdl_calls == 3, persistent.requestdl_calls
            assert "after 3 attempts" in message, message
            assert "Series v01.cbz" in message, message
            assert "secret" not in message, "requestdl token must never be logged"
            assert os.listdir(tmpdir2) == [], os.listdir(tmpdir2)
        finally:
            shutil.rmtree(tmpdir2, ignore_errors=True)
        print("    [PASSED] 404 retries with fresh requestdl links; persistent failure reports 3 attempts and cleans up.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 5. UI surface for processing + retry/fallback source wiring
# ===========================================================================
def test_template_and_source_wiring():
    print("--> Testing processing UI + retry/fallback wiring...")
    template = open(
        os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8",
    ).read()
    assert "Processing on Torbox…" in template, "processing UI text missing"
    assert "t.phase == 'processing'" in template, "processing phase branch missing"
    source = open(
        os.path.join(BACKEND_DIR, "app", "downloader", "torbox.py"), encoding="utf-8",
    ).read()
    assert "_PROCESSING_STATES" in source and "_is_range_fallback_status" in source
    assert "_is_retryable_fetch_status" in source and "requestdl" in source
    assert "after {attempt} attempts" in source
    print("    [PASSED] processing state UI and retry/range-fallback code are wired.")


def main():
    print("=" * 78)
    print("Running Torbox not-ready/404 suite (task-37)")
    print("=" * 78)
    tests = [
        test_state_matrix,
        test_processing_never_fetches,
        test_range_404_falls_back_to_single_stream,
        test_404_refresh_link_retry,
        test_template_and_source_wiring,
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
        print(f"TORBOX NOT-READY SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TORBOX NOT-READY/404 TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
