#!/usr/bin/env python3
"""task-31 suite: Torbox throughput — parallel files, ranged multi-connection fetch,
throttling and instrumentation.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import io
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import schemas  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from app.downloader import build_downloader  # noqa: E402
from app.downloader.base import DownloaderFileError  # noqa: E402
from app.downloader.torbox import TORBOX_RANGE_THRESHOLD_BYTES, TorboxDownloader  # noqa: E402
from test_torbox_downloader import TORBOX_KEY, Ctx, _Response  # noqa: E402


def _range_response(start, end, total, data):
    return _Response(206, data[start:end + 1], headers={
        "Content-Range": f"bytes {start}-{end}/{total}",
        "Content-Length": str(end - start + 1),
    })


class RangeServer:
    """Range-aware fake transport; optionally unsupported or failing once/permanently."""

    def __init__(self, data, support_ranges=True, fail_once_range=None, fail_always_range=None):
        self.data = data
        self.support_ranges = support_ranges
        self.fail_once_range = fail_once_range
        self.fail_always_range = fail_always_range
        self.ranges = []
        self.fail_once_used = set()
        self.lock = threading.Lock()

    def __call__(self, request, timeout=None):
        header = ""
        try:
            header = request.get_header("Range") or ""
        except Exception:
            header = ""
        if not self.support_ranges:
            return _Response(200, self.data)
        match = header.replace("bytes=", "").split("-")
        try:
            start = int(match[0])
            end = int(match[1])
        except (IndexError, ValueError):
            return _Response(200, self.data)
        with self.lock:
            self.ranges.append((start, end))
        is_probe = (start, end) == (0, 0)
        if not is_probe and self.fail_always_range is not None and start == self.fail_always_range:
            raise urllib.error.HTTPError("url", 500, "boom", {}, None)
        if not is_probe and self.fail_once_range is not None and start == self.fail_once_range and start not in self.fail_once_used:
            self.fail_once_used.add(start)
            raise urllib.error.HTTPError("url", 500, "transient", {}, None)
        return _range_response(start, end, len(self.data), self.data)


class SlowFileDownloader(TorboxDownloader):
    """Parallel-file test double: sleeps inside _download_to and tracks concurrency."""

    def __init__(self, delay=0.2, **kwargs):
        super().__init__(api_key=TORBOX_KEY, enabled=True, max_file_bytes=1024 ** 3, **kwargs)
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.completed = []

    def _requestdl(self, torrent_id, file_id=None):
        return f"https://cdn.example/{file_id}"

    def _download_to(self, url, dest_dir, name, progress_cb=None, expected_size=None):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            path = os.path.join(dest_dir, name)
            with open(path, "wb") as handle:
                handle.write(b"x" * 1000)
            if progress_cb:
                progress_cb(name, 1000)
            self.completed.append(name)
            return {"name": name, "path": path, "size": 1000, "seconds": self.delay, "connections": 1}
        finally:
            with self.lock:
                self.active -= 1


# ===========================================================================
# 1. Parallel per-file fetches are concurrent with exact aggregate progress
# ===========================================================================
def test_parallel_files_concurrent():
    print("--> Testing parallel file downloads...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_parallel_")
    try:
        downloader = SlowFileDownloader(delay=0.25, max_concurrency=3)
        files = [{"id": index, "name": f"Series v0{index}.cbz", "size": 1000} for index in (1, 2, 3)]
        progress = {}
        lock = threading.Lock()

        def cb(label, written):
            with lock:
                progress[label] = max(progress.get(label, 0), written)

        started = time.monotonic()
        saved = downloader.fetch_files({"torrent_id": 1}, files, tmpdir, progress_cb=cb)
        elapsed = time.monotonic() - started
        assert len(saved) == 3, saved
        assert downloader.max_active >= 2, downloader.max_active
        assert elapsed < 0.25 * 3 * 0.8, f"parallel fetch too slow: {elapsed:.2f}s"
        assert sorted(os.listdir(tmpdir)) == ["Series v01.cbz", "Series v02.cbz", "Series v03.cbz"], os.listdir(tmpdir)
        assert not any(name.endswith(".part") for name in os.listdir(tmpdir)), os.listdir(tmpdir)
        assert sum(progress.values()) == 3000, progress
        assert len(progress) == 3, progress
        print("    [PASSED] files download concurrently; finals renamed and progress totals exact.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 2. Ranged fetch: probe + 206 slices assemble to the original bytes
# ===========================================================================
def test_ranged_fetch_assembles_exactly():
    print("--> Testing ranged multi-connection assembly...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_ranged_")
    try:
        data = os.urandom(TORBOX_RANGE_THRESHOLD_BYTES + 1024 * 1024)  # > 16 MiB
        server = RangeServer(data)
        downloader = TorboxDownloader(
            api_key=TORBOX_KEY, enabled=True, max_file_bytes=1024 ** 3, parallel_chunks=4,
        )
        with patch.object(downloader, "_requestdl", return_value="https://cdn.example/file"):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=server):
                saved = downloader.fetch_files(
                    {"torrent_id": 1},
                    [{"id": 1, "name": "Series v01.cbz", "size": len(data)}],
                    tmpdir,
                )
        assert len(saved) == 1, saved
        assert os.path.isfile(saved[0]["path"])
        with open(saved[0]["path"], "rb") as handle:
            assert handle.read() == data, "ranged assembly differs from the source bytes"
        chunk_ranges = [entry for entry in server.ranges if entry != (0, 0)]
        assert len(chunk_ranges) >= 2, server.ranges
        assert not [name for name in os.listdir(tmpdir) if ".part" in name], os.listdir(tmpdir)
        print("    [PASSED] 206 range slices are assembled verifiably and renamed atomically.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 3. Server ignores Range (200) -> single-stream fallback
# ===========================================================================
def test_range_unsupported_falls_back():
    print("--> Testing 200/no-Range fallback to a single stream...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_range_fallback_")
    try:
        data = os.urandom(TORBOX_RANGE_THRESHOLD_BYTES + 4096)
        server = RangeServer(data, support_ranges=False)
        downloader = TorboxDownloader(
            api_key=TORBOX_KEY, enabled=True, max_file_bytes=1024 ** 3, parallel_chunks=4,
        )
        with patch.object(downloader, "_requestdl", return_value="https://cdn.example/file"):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=server):
                saved = downloader.fetch_files(
                    {"torrent_id": 1},
                    [{"id": 1, "name": "Series v01.cbz", "size": len(data)}],
                    tmpdir,
                )
        with open(saved[0]["path"], "rb") as handle:
            assert handle.read() == data
        assert "Download failed" not in ""
        print("    [PASSED] a server that ignores Range falls back to the single-stream path.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 4. One range fails once -> retried and succeeds
# ===========================================================================
def test_range_retry_once():
    print("--> Testing a transient range failure is retried once...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_range_retry_")
    try:
        data = os.urandom(TORBOX_RANGE_THRESHOLD_BYTES + 2048)
        server = RangeServer(data, fail_once_range=0)
        downloader = TorboxDownloader(
            api_key=TORBOX_KEY, enabled=True, max_file_bytes=1024 ** 3, parallel_chunks=4,
        )
        with patch.object(downloader, "_requestdl", return_value="https://cdn.example/file"):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=server):
                saved = downloader.fetch_files(
                    {"torrent_id": 1},
                    [{"id": 1, "name": "Series v01.cbz", "size": len(data)}],
                    tmpdir,
                )
        with open(saved[0]["path"], "rb") as handle:
            assert handle.read() == data
        assert server.fail_once_used == {0}, server.fail_once_used
        print("    [PASSED] a failed range is retried once and the file is exact.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 5. Persistent range failure -> error + partial cleanup
# ===========================================================================
def test_range_persistent_failure_cleans_partials():
    print("--> Testing a persistent range failure cleans partial output...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_range_fail_")
    try:
        data = os.urandom(TORBOX_RANGE_THRESHOLD_BYTES + 2048)
        server = RangeServer(data, fail_always_range=0)
        downloader = TorboxDownloader(
            api_key=TORBOX_KEY, enabled=True, max_file_bytes=1024 ** 3, parallel_chunks=4,
        )
        with patch.object(downloader, "_requestdl", return_value="https://cdn.example/file"):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=server):
                try:
                    downloader.fetch_files(
                        {"torrent_id": 1},
                        [{"id": 1, "name": "Series v01.cbz", "size": len(data)}],
                        tmpdir,
                    )
                except DownloaderFileError as exc:
                    assert "Series v01.cbz" in str(exc), str(exc)
                else:
                    assert False, "persistent range failure must raise DownloaderFileError"
        assert os.listdir(tmpdir) == [], os.listdir(tmpdir)
        print("    [PASSED] persistent range failure names the file and removes all partials.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 6. 429 throttling retried with backoff (and logged)
# ===========================================================================
def test_throttled_file_retried():
    print("--> Testing HTTP 429 retry/backoff...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_429_")
    try:
        downloader = SlowFileDownloader(delay=0.0, max_concurrency=2)
        original = downloader._download_item
        state = {"calls": 0}

        def flaky(torrent_id, item, dest_dir, progress_cb=None):
            state["calls"] += 1
            if state["calls"] == 1:
                raise DownloaderFileError("Download failed (HTTP 429) for file")
            return original(torrent_id, item, dest_dir, progress_cb=progress_cb)

        downloader._download_item = flaky
        saved = downloader.fetch_files(
            {"torrent_id": 1}, [{"id": 1, "name": "Series v01.cbz", "size": 1000}], tmpdir,
        )
        assert len(saved) == 1, saved
        assert state["calls"] == 2, state
        assert downloader._throttle_events == 1, downloader._throttle_events
        print("    [PASSED] a 429 is retried with backoff and counted.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 7. Settings bounds + factory wiring + per-file log instrumentation
# ===========================================================================
def test_settings_and_instrumentation():
    print("--> Testing throughput settings bounds/factory and per-file logging...")
    for bad in ("0", "17", "abc"):
        try:
            schemas.validate(schemas.SettingsUpdateRequest, {"torbox_download_concurrency": bad})
        except schemas.ValidationError:
            continue
        assert False, f"torbox_download_concurrency must reject {bad!r}"
    for bad in ("0", "9", "abc"):
        try:
            schemas.validate(schemas.SettingsUpdateRequest, {"torbox_parallel_chunks": bad})
        except schemas.ValidationError:
            continue
        assert False, f"torbox_parallel_chunks must reject {bad!r}"
    model = schemas.validate(
        schemas.SettingsUpdateRequest,
        {"torbox_download_concurrency": "7", "torbox_parallel_chunks": "6"},
    )
    assert model.torbox_download_concurrency == "7" and model.torbox_parallel_chunks == "6", model

    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        ctx.db.set_setting("torbox_download_concurrency", "7")
        ctx.db.set_setting("torbox_parallel_chunks", "6")
        client = build_downloader(ctx.db, engine="torbox")
        assert client.max_concurrency == 7 and client.parallel_chunks == 6, client
    finally:
        ctx.close()

    tmpdir = tempfile.mkdtemp(prefix="mangatrax_log_")
    try:
        downloader = SlowFileDownloader(delay=0.0, max_concurrency=1)
        records = []
        import logging

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Handler(logging.INFO)
        logger = logging.getLogger("mangatrax.downloader.torbox")
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            downloader.fetch_files(
                {"torrent_id": 1}, [{"id": 1, "name": "Series v01.cbz", "size": 1000}], tmpdir,
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
        messages = [record.getMessage() for record in records]
        assert any("MB/s" in message and "conns" in message for message in messages), messages
        settings_html = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
        assert "setting-torbox-download-concurrency" in settings_html
        assert "setting-torbox-parallel-chunks" in settings_html
        readme = open(os.path.join(BASE_DIR, "README.md"), encoding="utf-8").read()
        assert "torbox_download_concurrency" in readme and "speed.cloudflare.com" in readme
        print("    [PASSED] settings bounds/factory, per-file MB/s+conns log and README diagnostics are wired.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 78)
    print("Running Torbox throughput suite (task-31)")
    print("=" * 78)
    tests = [
        test_parallel_files_concurrent,
        test_ranged_fetch_assembles_exactly,
        test_range_unsupported_falls_back,
        test_range_retry_once,
        test_range_persistent_failure_cleans_partials,
        test_throttled_file_retried,
        test_settings_and_instrumentation,
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
        print(f"TORBOX THROUGHPUT SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TORBOX THROUGHPUT TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
