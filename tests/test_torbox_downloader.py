#!/usr/bin/env python3
"""task-20 suite: Torbox download engine + selectable default downloader.

Covers the abstraction (qBittorrent adapter unchanged), the stdlib Torbox client
(submit → checkcached → createtorrent → mylist poll → requestdl fetch → delete),
volume-scoped selection with unmappable-file fallback, scheduler polling with
timeout/concurrency, settings masking/validation, engine-aware downloads status and
zip-slip/path-traversal safety.  All HTTP is mocked; no network access.

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
import time
import traceback
import urllib.error
import zipfile
from datetime import datetime, timedelta
from email.message import Message
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import schemas  # noqa: E402
from app.covers import CoverCache  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from app.downloader import (  # noqa: E402
    build_downloader,
    resolve_downloader_engine,
)
from app.downloader.base import (  # noqa: E402
    DownloaderAuthError,
    DownloaderError,
    DownloaderFileError,
    DownloaderNotFoundError,
    DownloaderRateLimitError,
    DownloaderSlotError,
    extract_torrent_hash,
)
from app.downloader.qbit_adapter import QBitDownloader  # noqa: E402
from app.downloader.torbox import TorboxDownloader  # noqa: E402
from app.scheduler import MangaScheduler  # noqa: E402

HASH = "0123456789abcdef0123456789abcdef01234567"
TORBOX_KEY = "tb-secret-key-do-not-leak"


# ---------------------------------------------------------------------------
# Mock HTTP layer
# ---------------------------------------------------------------------------
class _Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.headers = headers or {}
        self._cursor = 0

    def read(self, size=None):
        if size is None:
            chunk = self._body[self._cursor:]
            self._cursor = len(self._body)
            return chunk
        chunk = self._body[self._cursor:self._cursor + size]
        self._cursor += len(chunk)
        return chunk

    def getcode(self):
        return self.status

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _headers(retry_after=None):
    message = Message()
    if retry_after is not None:
        message["Retry-After"] = str(retry_after)
    return message


class FakeApi:
    """Scripted Torbox API double keyed by path substring (first match wins)."""

    def __init__(self):
        self.routes = []
        self.calls = []
        self.requests = []

    def add(self, path_part, data=None, status=200, headers=None, body=None):
        self.routes.append((path_part, data, status, headers, body))
        return self

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.calls.append(url)
        self.requests.append(request)
        for path_part, data, status, headers, body in self.routes:
            if path_part in url:
                if status >= 400:
                    raise urllib.error.HTTPError(url, status, f"HTTP {status}", _headers(headers), None)
                if body is not None:
                    return _Response(status, body)
                return _Response(status, json.dumps({"success": True, "data": data}))
        raise AssertionError(f"Unexpected Torbox request: {url}")

    def urls_for(self, path_part):
        return [url for url in self.calls if path_part in url]

    def requests_for(self, path_part):
        return [
            request for request in self.requests
            if path_part in (request.full_url if hasattr(request, "full_url") else str(request))
        ]

    @staticmethod
    def body_of(request):
        return request.data or b""

    @staticmethod
    def content_type(request):
        headers = getattr(request, "headers", {}) or {}
        for key, value in headers.items():
            if key.lower() == "content-type":
                return value
        return ""


class FakeStream:
    """Patch target for torbox._open_no_redirect (streamed CDN downloads)."""

    def __init__(self, body=b"CBZCONTENT", status=200):
        self.body = body
        self.status = status
        self.urls = []

    def __call__(self, request, timeout):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.urls.append(url)
        return _Response(self.status, self.body)


class ScriptedStream:
    """Stream double with scripted responses (bytes | (status,) | Exception)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    def __call__(self, request, timeout):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.urls.append(url)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple):
            status = outcome[0]
            raise urllib.error.HTTPError(url, status, f"HTTP {status}", _headers(), None)
        return _Response(200, outcome)


BENCODED_TORRENT = b"d8:announce4:http4:infod4:name4:testee"

def _zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Test context
# ---------------------------------------------------------------------------
def _insert_release(db, guid, title, **overrides):
    row = {
        "guid": guid,
        "title": title,
        "series_name": overrides.get("series_name", "Torbox Test"),
        "link": overrides.get("link", "https://nyaa.si/view/1"),
        "download_url": overrides.get("download_url", f"magnet:?xt=urn:btih:{HASH}"),
        "status": overrides.get("status", "REQUESTED"),
        "download_status": overrides.get("download_status", "IDLE"),
        "downloader": overrides.get("downloader", ""),
        "downloader_id": overrides.get("downloader_id", ""),
        "downloader_started_at": overrides.get("downloader_started_at", ""),
        "torrent_hash": overrides.get("torrent_hash", ""),
        "is_bundle": overrides.get("is_bundle", 0),
        "volumes_json": json.dumps(overrides.get("volumes", [])),
        "missing_vols_json": json.dumps(overrides.get("missing_volumes", [])),
    }
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO releases
                (guid, title, series_name, link, download_url, status, download_status,
                 downloader, downloader_id, downloader_started_at, torrent_hash, is_bundle,
                 volumes_json, missing_vols_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                row["guid"], row["title"], row["series_name"], row["link"], row["download_url"],
                row["status"], row["download_status"], row["downloader"], row["downloader_id"],
                row["downloader_started_at"], row["torrent_hash"], row["is_bundle"],
                row["volumes_json"], row["missing_vols_json"],
            ),
        )
        conn.commit()
        return cur.lastrowid


class FakeTorboxService:
    """Scheduler-level fake for build_downloader (no HTTP)."""

    name = "torbox"
    enabled = True

    def __init__(self, state="downloading", files=None):
        self.delete_after_download = True
        self.state = state
        self.files = files if files is not None else [{"id": 1, "name": "Series Vol. 01.cbz", "size": 10}]
        self.status_calls = []
        self.fetch_calls = []
        self.cancelled = []
        self.submit_calls = []

    def test_connection(self):
        return {"success": True, "engine": "torbox", "plan": "Pro", "active_slots": 1, "message": "ok"}

    def submit(self, release, savepath, wanted_volumes=None):
        self.submit_calls.append((release, savepath, wanted_volumes))
        return {
            "handle": {"engine": "torbox", "torrent_id": 55, "hash": HASH, "savepath": savepath},
            "state": "queued",
            "cached": False,
        }

    def status(self, handle):
        self.status_calls.append(handle)
        return {
            "state": self.state,
            "raw_state": self.state,
            "progress": 0.5,
            "size": 100,
            "download_speed": 10,
            "files": self.files,
            "cached": self.state == "ready",
        }

    def fetch_files(self, handle, files, dest_dir, progress_cb=None):
        self.fetch_calls.append((handle, files, dest_dir))
        return [{"name": "Series Vol. 01.cbz", "path": os.path.join(dest_dir, "Series Vol. 01.cbz"), "size": 10}]

    def cancel(self, handle, delete_remote=True):
        self.cancelled.append(handle)
        return True


class SequenceTorboxService(FakeTorboxService):
    """Fake returning scripted status payloads (e.g. queued -> promoted -> ready)."""

    def __init__(self, responses, files=None):
        super().__init__(state="downloading", files=files)
        self.responses = list(responses)
        self.last_info = None

    def status(self, handle):
        self.status_calls.append(handle)
        if len(self.responses) > 1:
            self.last_info = self.responses.pop(0)
        else:
            self.last_info = self.responses[0]
        return self.last_info


class Ctx:
    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_torbox_")
        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.db.set_setting("calibre_path", os.path.join(self.tmpdir, "calibre"))
        self.db.set_setting("staging_dir", os.path.join(self.tmpdir, "staging"))
        self.db.set_setting("qbit_enabled", "false")
        self.db.set_setting("auto_download", "false")
        self.scheduler = MangaScheduler(self.db, CoverCache(self.tmpdir))

    def events(self, event_type):
        return [row for row in self.db.get_logs(limit=200) if row["event_type"] == event_type]

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# 1. Engine selection + qBittorrent adapter (zero behaviour change)
# ===========================================================================
def test_engine_selection_and_qbit_adapter():
    print("--> Testing engine selection + qBittorrent adapter...")
    ctx = Ctx()
    try:
        assert resolve_downloader_engine(ctx.db) == "qbit"
        ctx.db.set_setting("downloader_engine", "torbox")
        assert resolve_downloader_engine(ctx.db) == "torbox"
        ctx.db.set_setting("downloader_engine", "bogus")
        assert resolve_downloader_engine(ctx.db) == "qbit"

        # Adapter delegates to the client exactly like the old direct call.
        client = MagicMock()
        client.enabled = True
        client.add_torrent.return_value = True
        client.get_torrents.return_value = [{
            "hash": HASH, "name": "Series Vol. 01", "state": "downloading", "progress": 0.5,
        }]
        client.get_torrent_files.return_value = [{"name": "Series Vol. 01.cbz", "size": 10}]
        adapter = QBitDownloader(client)
        submit = adapter.submit({"download_url": f"magnet:?xt=urn:btih:{HASH}"}, "/staging", [1])
        client.add_torrent.assert_called_once_with(
            url_or_magnet=f"magnet:?xt=urn:btih:{HASH}", savepath="/staging",
            category="manga", tags="mangatrax",
        )
        assert submit["handle"]["hash"] == HASH, submit
        status = adapter.status({"hash": HASH})
        assert status["state"] == "downloading" and status["progress"] == 0.5, status
        assert adapter.fetch_files({"hash": HASH}, None, "/staging") == []
        assert adapter.cancel({"hash": HASH}, delete_remote=False) is True
        assert extract_torrent_hash(f"magnet:?xt=urn:btih:{HASH}") == HASH
        # build_downloader picks the configured engine and keeps qbit the default.
        ctx.db.set_setting("downloader_engine", "qbit")
        assert isinstance(build_downloader(ctx.db, qbit_client=client), QBitDownloader)
        ctx.db.set_setting("downloader_engine", "torbox")
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        assert isinstance(build_downloader(ctx.db, engine="torbox"), TorboxDownloader)
        print("    [PASSED] qbit adapter delegates unchanged; engine selection works.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Torbox submit (cached + uncached) and status normalisation
# ===========================================================================
def test_torbox_submit_and_status():
    print("--> Testing Torbox submit (cached/uncached) and status...")
    api = FakeApi()
    api.add("/torrents/checkcached", data=[HASH])
    api.add("/torrents/createtorrent", data={"torrent_id": 55, "hash": HASH})
    api.add("/torrents/mylist", data=[{
        "id": 55, "hash": HASH, "name": "Series", "download_state": "completed",
        "download_finished": True, "download_present": True, "size": 200, "progress": 1.0,
        "files": [{"id": 7, "name": "Series Vol. 01.cbz", "size": 200}],
    }])
    api.add("/user/me", data={"plan": "Pro", "active_slot_count": 2})
    downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)
    with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
        result = downloader.submit({"download_url": f"magnet:?xt=urn:btih:{HASH}"}, "/staging", [1])
        assert result["cached"] is True, result
        assert result["handle"]["torrent_id"] == 55 and result["handle"]["hash"] == HASH, result
        cached_calls = api.urls_for("/torrents/checkcached")
        assert cached_calls and HASH in cached_calls[0], cached_calls
        assert "format=list" in cached_calls[0] and "list_files=true" in cached_calls[0]
        assert api.urls_for("/torrents/createtorrent")

        # Wire format regression (task-23): Torbox createtorrent is multipart/form-data
        # with a `magnet` field — never a query parameter (that caused live HTTP 400).
        create_requests = api.requests_for("/torrents/createtorrent")
        create_request = create_requests[0]
        assert "?" not in create_request.full_url, create_request.full_url
        assert create_request.method == "POST", create_request.method
        content_type = FakeApi.content_type(create_request)
        assert content_type.startswith("multipart/form-data; boundary="), content_type
        create_body = FakeApi.body_of(create_request)
        assert b'name="magnet"' in create_body, create_body[:400]
        assert HASH.encode() in create_body, create_body[:400]
        assert b'name="file"' not in create_body, create_body[:400]
        assert b"application/json" not in content_type.encode(), content_type

        status = downloader.status({"torrent_id": 55})
        assert status["state"] == "ready", status
        assert status["files"][0]["id"] == 7, status
        mylist_calls = api.urls_for("/torrents/mylist")
        assert mylist_calls and "bypass_cache=true" in mylist_calls[0], mylist_calls

        connection = downloader.test_connection()
        assert connection["success"] is True and connection["plan"] == "Pro", connection
        assert connection["active_slots"] == 2, connection
        assert TORBOX_KEY not in json.dumps(connection), connection

    # Uncached submission uses the magnet form (no file upload needed).
    api2 = FakeApi()
    api2.add("/torrents/checkcached", data=[])
    api2.add("/torrents/createtorrent", data={"torrent_id": 56, "hash": HASH})
    downloader2 = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)
    with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api2):
        result2 = downloader2.submit({"download_url": f"magnet:?xt=urn:btih:{HASH}"}, "/staging", None)
    assert result2["cached"] is False and result2["handle"]["torrent_id"] == 56, result2
    assert api2.urls_for("/torrents/createtorrent"), api2.calls
    print("    [PASSED] checkcached -> createtorrent -> mylist(bypass_cache) with typed states.")


# ===========================================================================
# 2b. Submission forms: infohash magnet, /view-derived torrent, retries, HTML reject
# ===========================================================================
def test_torbox_submission_forms_and_fallbacks():
    print("--> Testing Torbox submission forms and robust torrent fallback...")
    downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)

    # 1. Infohash-only release -> synthesised magnet on the same multipart path.
    api = FakeApi()
    api.add("/torrents/checkcached", data=[])
    api.add("/torrents/createtorrent", data={"torrent_id": 61, "hash": HASH})
    with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
        result = downloader.submit(
            {"title": "Hash Only Release", "download_url": "", "torrent_hash": HASH}, "/downloads",
        )
    assert result["handle"]["torrent_id"] == 61, result
    request = api.requests_for("/torrents/createtorrent")[0]
    assert "?" not in request.full_url, request.full_url
    body = FakeApi.body_of(request)
    assert b'name="magnet"' in body, body[:400]
    assert f"urn:btih:{HASH}".encode() in body, body[:400]

    # 2. /view/<id> release -> derive <base>/download/<id>.torrent and upload file part.
    api2 = FakeApi()
    api2.add("/torrents/checkcached", data=[])
    api2.add("/torrents/createtorrent", data={"torrent_id": 62, "hash": HASH})
    stream = ScriptedStream([BENCODED_TORRENT])
    logged = []

    class _Capture(logging.Handler):
        def emit(self, record):
            logged.append(record.getMessage())

    logger = logging.getLogger("mangatrax.downloader.torbox")
    handler = _Capture()
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api2):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                result2 = downloader.submit({
                    "title": "View Release",
                    "download_url": "https://mirror.example/nyaa/view/123",
                }, "/downloads")
        assert result2["handle"]["torrent_id"] == 62, result2
        assert any("nyaa/download/123.torrent" in url for url in stream.urls), stream.urls
        request2 = api2.requests_for("/torrents/createtorrent")[0]
        assert "?" not in request2.full_url, request2.full_url
        body2 = FakeApi.body_of(request2)
        assert b'name="file"' in body2 and b'filename="release.torrent"' in body2, body2[:500]
        assert BENCODED_TORRENT in body2, "the fetched bencoded torrent must be uploaded"
        assert any("derived-torrent-url" in message for message in logged), logged

        # 3. A view URL returning HTML must be rejected before any upload.
        api3 = FakeApi()
        api3.add("/torrents/checkcached", data=[])
        stream_html = ScriptedStream([b"<!DOCTYPE html><html><body>not a torrent</body></html>"])
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api3):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=stream_html):
                try:
                    downloader.submit({
                        "title": "HTML Release",
                        "download_url": "https://mirror.example/view/9",
                    }, "/downloads")
                except DownloaderError as exc:
                    html_error = str(exc)
                else:
                    assert False, "HTML payload must be rejected"
        assert "HTML Release" in html_error, html_error
        assert "bencoded" in html_error or "HTML" in html_error, html_error
        assert api3.urls_for("/torrents/createtorrent") == [], api3.calls

        # 4. 504 then 200 -> one retry, succeeds.
        api4 = FakeApi()
        api4.add("/torrents/checkcached", data=[])
        api4.add("/torrents/createtorrent", data={"torrent_id": 63, "hash": HASH})
        stream_retry = ScriptedStream([(504,), BENCODED_TORRENT])
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api4):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=stream_retry):
                with patch("app.downloader.torbox.time.sleep", side_effect=lambda _s: None):
                    result4 = downloader.submit({
                        "title": "Retry Release",
                        "download_url": "https://mirror.example/view/77",
                    }, "/downloads")
        assert len(stream_retry.urls) == 2, stream_retry.urls
        assert result4["handle"]["torrent_id"] == 63, result4

        # 5. Persistent 504 -> actionable error naming the release.
        api5 = FakeApi()
        api5.add("/torrents/checkcached", data=[])
        stream_fail = ScriptedStream([(504,), (504,)])
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api5):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=stream_fail):
                with patch("app.downloader.torbox.time.sleep", side_effect=lambda _s: None):
                    try:
                        downloader.submit({
                            "title": "Youjo Senki Vol.35",
                            "download_url": "https://mirror.example/view/35",
                        }, "/downloads")
                    except DownloaderError as exc:
                        gateway_error = str(exc)
                    else:
                        assert False, "persistent 504 must raise DownloaderError"
        assert "Youjo Senki Vol.35" in gateway_error, gateway_error
        assert "504" in gateway_error, gateway_error
        assert api5.urls_for("/torrents/createtorrent") == [], api5.calls
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    # 6. Scheduler passes DB guid/torrent_hash candidates to the engine.
    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        release_id = _insert_release(
            ctx.db, "candidate-guid", "Candidate Release", torrent_hash=HASH,
        )
        fake = FakeTorboxService(state="downloading")
        with patch("app.scheduler.build_downloader", return_value=fake):
            submitted = ctx.scheduler.submit_torbox_download(
                release_id, "Candidate Release", "", missing_volumes=[1],
            )
        assert submitted["success"] is True, submitted
        release_payload = fake.submit_calls[0][0]
        assert release_payload["guid"] == "candidate-guid", release_payload
        assert release_payload["torrent_hash"] == HASH, release_payload
        assert release_payload["title"] == "Candidate Release", release_payload
    finally:
        ctx.close()
    print("    [PASSED] magnet multipart, infohash magnet, /view-derived torrent, HTML reject, 504 retry, scheduler candidates.")


# ===========================================================================
# 3. Torbox typed errors + key never logged
# ===========================================================================
def test_torbox_errors_and_secret_hygiene():
    print("--> Testing Torbox typed errors and key hygiene...")
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("mangatrax.downloader.torbox")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        api = FakeApi().add("/user/me", status=401)
        downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
            try:
                downloader.test_connection()
            except Exception:
                assert False, "test_connection must not raise for connectivity errors"
            result = downloader.test_connection()
            assert result["success"] is False, result
            assert TORBOX_KEY not in json.dumps(result), result

        api_auth = FakeApi().add("/torrents/checkcached", status=401)
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api_auth):
            try:
                downloader.submit({"download_url": f"magnet:?xt=urn:btih:{HASH}"}, "/staging")
            except DownloaderAuthError as exc:
                assert TORBOX_KEY not in str(exc), str(exc)
            else:
                assert False, "401 must raise DownloaderAuthError"

        api429 = FakeApi().add("/torrents/checkcached", status=429, headers="12")
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api429):
            try:
                downloader.submit({"download_url": f"magnet:?xt=urn:btih:{HASH}"}, "/staging")
            except DownloaderRateLimitError as exc:
                assert exc.retry_after == 12.0, exc.retry_after
            else:
                assert False, "429 must raise DownloaderRateLimitError"
        # Retry-After HTTP-date form is parsed too.
        future = (datetime.utcnow() + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        api_date = FakeApi().add("/torrents/checkcached", status=429, headers=future)
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api_date):
            try:
                downloader.submit({"download_url": f"magnet:?xt=urn:btih:{HASH}"}, "/staging")
            except DownloaderRateLimitError as exc:
                assert exc.retry_after is not None and exc.retry_after > 0, exc.retry_after
            else:
                assert False, "429 must raise DownloaderRateLimitError"

        api_slot = FakeApi()
        api_slot.add("/torrents/checkcached", data=[])
        api_slot.add("/torrents/createtorrent", status=409, body=json.dumps(
            {"success": False, "detail": "No active slot available"}
        ).encode())
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api_slot):
            try:
                downloader.submit({"download_url": f"magnet:?xt=urn:btih:{HASH}"}, "/staging")
            except DownloaderSlotError as exc:
                assert "slot" in str(exc).lower(), str(exc)
            else:
                assert False, "slot error must raise DownloaderSlotError"

        api404 = FakeApi().add("/torrents/mylist", status=404)
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api404):
            try:
                downloader.status({"torrent_id": 55})
            except DownloaderNotFoundError:
                pass
            else:
                assert False, "404 must raise DownloaderNotFoundError"

        joined = "\n".join(records)
        assert TORBOX_KEY not in joined, "API key leaked into logs"
    finally:
        logger.removeHandler(handler)
    print("    [PASSED] 401/429(+Retry-After)/slot/404 are typed; key never appears in messages/logs.")


# ===========================================================================
# 4. fetch_files: per-file streaming, zip extraction, zip-slip safety
# ===========================================================================
def test_torbox_fetch_files_and_zip_safety():
    print("--> Testing Torbox fetch_files (per-file + zip safety)...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_torbox_fetch_")
    try:
        api = FakeApi()
        api.add("/torrents/requestdl", data={"url": "https://cdn.example/file/1"})
        stream = FakeStream(b"CBZDATA")
        downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                saved = downloader.fetch_files(
                    {"torrent_id": 55},
                    [{"id": 1, "name": "Series Vol. 01.cbz", "size": 7}],
                    tmpdir,
                )
        assert len(saved) == 1 and os.path.isfile(saved[0]["path"]), saved
        assert open(saved[0]["path"], "rb").read() == b"CBZDATA"
        assert not os.path.exists(saved[0]["path"] + ".part")
        request_urls = api.urls_for("/torrents/requestdl")
        assert request_urls and "file_id=1" in request_urls[0], request_urls
        assert f"token={TORBOX_KEY}" in request_urls[0], "requestdl must use token query auth"

        # Whole-torrent zip extraction (zip_link=true) into the staging dir.
        zip_bytes = _zip_bytes({"Series Vol. 01.cbz": b"v1", "Series Vol. 02.cbz": b"v2"})
        api_zip = FakeApi().add("/torrents/requestdl", data={"url": "https://cdn.example/torrent.zip"})
        stream_zip = FakeStream(zip_bytes)
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api_zip):
            with patch("app.downloader.torbox._open_no_redirect", side_effect=stream_zip):
                saved_zip = downloader.fetch_files({"torrent_id": 55}, None, tmpdir)
        assert sorted(item["name"] for item in saved_zip) == ["Series Vol. 01.cbz", "Series Vol. 02.cbz"], saved_zip
        assert api_zip.urls_for("/torrents/requestdl")[0].count("zip_link=true") == 1

        # Unsafe archives: absolute path, '..' traversal and symlinks are refused.
        for evil in (
            _zip_bytes({"/etc/passwd": b"x"}),
            _zip_bytes({"../escape.txt": b"x"}),
        ):
            evil_zip_path = os.path.join(tmpdir, "evil.zip")
            with open(evil_zip_path, "wb") as handle:
                handle.write(evil)
            try:
                downloader._safe_extract_zip(evil_zip_path, tmpdir)
            except DownloaderFileError:
                pass
            else:
                assert False, "unsafe zip entries must be rejected"
            os.remove(evil_zip_path)

        import_stat = zipfile.ZipInfo("link")
        import_stat.external_attr = (0o120777 << 16)
        symlink_buffer = io.BytesIO()
        with zipfile.ZipFile(symlink_buffer, "w") as archive:
            archive.writestr(import_stat, "/etc/passwd")
        symlink_path = os.path.join(tmpdir, "symlink.zip")
        with open(symlink_path, "wb") as handle:
            handle.write(symlink_buffer.getvalue())
        try:
            downloader._safe_extract_zip(symlink_path, tmpdir)
        except DownloaderFileError:
            pass
        else:
            assert False, "symlink zip entries must be rejected"

        # Unsafe per-file names are rejected before any write.
        with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=FakeApi()):
            try:
                downloader.fetch_files({"torrent_id": 55}, [{"id": 9, "name": ".."}], tmpdir)
            except DownloaderFileError:
                pass
            else:
                assert False, "path traversal in file names must be rejected"
        print("    [PASSED] streamed per-file/zip fetches; .part cleanup; zip-slip/symlink/traversal rejected.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 5. Scheduler: submit persists handle, poll -> fetch -> complete -> delete
# ===========================================================================
def test_scheduler_submit_and_poll_happy_path():
    print("--> Testing scheduler submit + poll happy path...")
    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        ctx.db.set_setting("torbox_delete_after_download", "true")
        fake = FakeTorboxService(
            state="ready",
            files=[
                {"id": 1, "name": "Series Vol. 01.cbz", "size": 10},
                {"id": 2, "name": "Series Vol. 02.cbz", "size": 10},
            ],
        )
        release_id = _insert_release(ctx.db, "torbox-guid-1", "Series Vol. 01-02 (Bundle)", volumes=[1, 2], missing_volumes=[2])
        with patch("app.scheduler.build_downloader", return_value=fake):
            submit = ctx.scheduler.submit_torbox_download(
                release_id, "Series Vol. 01-02 (Bundle)", f"magnet:?xt=urn:btih:{HASH}",
                missing_volumes=[2], source="test",
            )
            assert submit["success"] is True, submit
            row = ctx.db.get_release_by_id(release_id)
            assert row["download_status"] == "DOWNLOADING", row["download_status"]
            assert row["downloader"] == "torbox" and row["downloader_id"] == "55", row
            assert len(ctx.events("TORBOX_SUBMITTED")) == 1, ctx.events("TORBOX_SUBMITTED")

            result = ctx.scheduler.poll_torbox_downloads()
            assert result["completed"] == 1, result
            fetch_handle, selection, dest_dir = fake.fetch_calls[0]
            assert [item["id"] for item in selection] == [2], selection  # only missing volume 2
            assert ctx.db.get_release_by_id(release_id)["download_status"] == "COMPLETED"
            assert len(ctx.events("TORBOX_DOWNLOADED")) == 1
            assert len(fake.cancelled) == 1, "delete-after-download default on must delete remotely"

        # Cached submissions emit TORBOX_CACHED instead of TORBOX_SUBMITTED.
        fake_cached = FakeTorboxService(state="ready")
        fake_cached.submit = lambda release, savepath, wanted_volumes=None: {
            "handle": {"engine": "torbox", "torrent_id": 77, "hash": HASH},
            "state": "ready", "cached": True,
        }
        release_id2 = _insert_release(ctx.db, "torbox-guid-2", "Cached Release")
        with patch("app.scheduler.build_downloader", return_value=fake_cached):
            submit2 = ctx.scheduler.submit_torbox_download(
                release_id2, "Cached Release", f"magnet:?xt=urn:btih:{HASH}", missing_volumes=[1],
            )
        assert submit2["cached"] is True and submit2["success"] is True, submit2
        assert len(ctx.events("TORBOX_CACHED")) >= 1, ctx.events("TORBOX_CACHED")
        print("    [PASSED] submit persists handle; poll selects missing volume, fetches, completes and deletes.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Poll edge cases: unmappable fallback, stalled, timeout, failure
# ===========================================================================
def test_poll_fallback_stalled_timeout():
    print("--> Testing poll fallback / stalled / timeout / failure paths...")
    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        ctx.db.set_setting("torbox_timeout_hours", "6")
        # This test covers the legacy permissive fallback path explicitly.
        ctx.db.set_setting("torbox_strict_volume_selection", "false")

        # Unmappable file name -> whole-torrent zip fallback (selection None).
        unmappable = FakeTorboxService(
            state="ready",
            files=[{"id": 1, "name": "random extra.bin", "size": 5},
                   {"id": 2, "name": "Series Vol. 02.cbz", "size": 5}],
        )
        rid = _insert_release(ctx.db, "fallback-guid", "Fallback", downloader="torbox",
                              downloader_id="55", download_status="DOWNLOADING",
                              downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                              volumes=[1, 2], missing_volumes=[2])
        with patch("app.scheduler.build_downloader", return_value=unmappable):
            ctx.scheduler.poll_torbox_downloads()
        assert unmappable.fetch_calls and unmappable.fetch_calls[0][1] is None, unmappable.fetch_calls
        assert ctx.db.get_release_by_id(rid)["download_status"] == "COMPLETED"

        # Stalled -> surfaced, stays DOWNLOADING (not silently retried forever).
        stalled = FakeTorboxService(state="stalled")
        rid2 = _insert_release(ctx.db, "stalled-guid", "Stalled", downloader="torbox",
                               downloader_id="56", download_status="DOWNLOADING",
                               downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        with patch("app.scheduler.build_downloader", return_value=stalled):
            ctx.scheduler.poll_torbox_downloads()
        assert ctx.db.get_release_by_id(rid2)["download_status"] == "DOWNLOADING"
        stalled_events = [e for e in ctx.events("TORBOX_FAILED") if "no seeds" in (e.get("details") or "")]
        assert stalled_events, ctx.events("TORBOX_FAILED")
        with patch("app.scheduler.build_downloader", return_value=stalled):
            ctx.scheduler.poll_torbox_downloads()  # duplicate state is de-duplicated
        assert len([e for e in ctx.events("TORBOX_FAILED") if "no seeds" in (e.get("details") or "")]) == 1

        # Timeout -> FAILED + audit (last page-style resume is the release staying REQUESTED).
        timed_out = FakeTorboxService(state="downloading")
        old = (datetime.utcnow() - timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
        rid3 = _insert_release(ctx.db, "timeout-guid", "Timed Out", downloader="torbox",
                               downloader_id="57", download_status="DOWNLOADING", downloader_started_at=old)
        with patch("app.scheduler.build_downloader", return_value=timed_out):
            result = ctx.scheduler.poll_torbox_downloads()
        assert ctx.db.get_release_by_id(rid3)["download_status"] == "FAILED", result
        timeout_events = [e for e in ctx.events("TORBOX_FAILED") if "Timed out" in (e.get("details") or "")]
        assert timeout_events, ctx.events("TORBOX_FAILED")

        # Engine-reported failure -> FAILED.
        failed = FakeTorboxService(state="failed")
        rid4 = _insert_release(ctx.db, "failed-guid", "Failed Torrent", downloader="torbox",
                               downloader_id="58", download_status="DOWNLOADING",
                               downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        with patch("app.scheduler.build_downloader", return_value=failed):
            ctx.scheduler.poll_torbox_downloads()
        assert ctx.db.get_release_by_id(rid4)["download_status"] == "FAILED"
        print("    [PASSED] unmappable fallback, stalled surfaced once, timeout/failure -> FAILED audited.")
    finally:
        ctx.close()


# ===========================================================================
# 7. Concurrency limit + delete-after toggle
# ===========================================================================
def test_concurrency_and_delete_setting():
    print("--> Testing torbox_max_concurrent and delete-after toggle...")
    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        ctx.db.set_setting("torbox_max_concurrent", "1")
        _insert_release(ctx.db, "active-guid", "Already Active", downloader="torbox",
                        downloader_id="90", download_status="DOWNLOADING",
                        downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        blocked_id = _insert_release(ctx.db, "blocked-guid", "Blocked Release")
        fake = FakeTorboxService(state="ready")
        with patch("app.scheduler.build_downloader", return_value=fake):
            result = ctx.scheduler.submit_torbox_download(
                blocked_id, "Blocked Release", f"magnet:?xt=urn:btih:{HASH}", missing_volumes=[1],
            )
        assert result["success"] is False and result["retryable"] is True, result
        assert fake.submit_calls == [], "concurrency limit must prevent the submit call"
        assert ctx.events("TORBOX_FAILED"), ctx.events("TORBOX_FAILED")
        row = ctx.db.get_release_by_id(blocked_id)
        assert row["download_status"] != "DOWNLOADING", row["download_status"]

        # delete-after false -> cancel() not called on completion.
        ctx.db.set_setting("torbox_max_concurrent", "5")
        ctx.db.set_setting("torbox_delete_after_download", "false")
        fake2 = FakeTorboxService(state="ready", files=[{"id": 1, "name": "Series Vol. 01.cbz", "size": 1}])
        rid = _insert_release(ctx.db, "keep-guid", "Keep Torbox", downloader="torbox",
                              downloader_id="91", download_status="DOWNLOADING",
                              downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                              missing_volumes=[1])
        with patch("app.scheduler.build_downloader", return_value=fake2):
            ctx.scheduler.poll_torbox_downloads()
        assert ctx.db.get_release_by_id(rid)["download_status"] == "COMPLETED"
        # The separately-seeded DOWNLOADING row with nothing missing is skipped (and its
        # remote job released); the delete-after=false keep row must not be cancelled.
        keep_cancels = [h for h in fake2.cancelled if str(h.get("torrent_id")) == "91"]
        assert keep_cancels == [], "delete-after disabled must not cancel the downloaded torrent"
        print("    [PASSED] concurrency gate blocks submission; delete-after respects the setting.")
    finally:
        ctx.close()


# ===========================================================================
# 8. Manual download endpoint routes by engine (e2e, no network)
# ===========================================================================
def test_manual_download_routing():
    print("--> Testing manual /api/releases/{id}/download engine routing...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_torbox_route_")
    try:
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, link, download_url, status) VALUES (?, ?, ?, ?, 'REQUESTED');",
                ("route-guid", "Route Me", "https://nyaa.si/view/1", f"magnet:?xt=urn:btih:{HASH}"),
            )
            conn.commit()
        release_id = ctx.db.get_release_by_id(
            [r["id"] for r in ctx.db.get_releases() if r["guid"] == "route-guid"][0]
        )["id"]

        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("downloader_engine", "torbox")
        with patch.object(ctx.scheduler, "submit_torbox_download", return_value={
            "success": True, "engine": "torbox", "cached": False,
        }) as submit_mock:
            response = ctx.client.post(f"/api/releases/{release_id}/download")
        body = response.json()
        assert response.status_code == 200 and body["engine"] == "torbox", body
        assert submit_mock.called, "torbox engine must route through submit_torbox_download"

        ctx.db.set_setting("downloader_engine", "qbit")
        qbit = MagicMock()
        qbit.enabled = True
        qbit.add_torrent.return_value = True
        qbit.extract_torrent_hash.return_value = HASH
        qbit.apply_selective_volume_download.return_value = {"status": "all_needed"}
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            response2 = ctx.client.post(f"/api/releases/{release_id}/download")
        body2 = response2.json()
        assert response2.status_code == 200 and body2["engine"] == "qbit", body2
        qbit.add_torrent.assert_called_once()
        print("    [PASSED] engine toggle routes to Torbox or qBittorrent accordingly.")
    finally:
        ctx.close()


# ===========================================================================
# 9. Settings masking + validation (key never returned/logged)
# ===========================================================================
def test_settings_masking_and_validation():
    print("--> Testing settings masking/validation...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_torbox_settings_")
    try:
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        response = ctx.client.get("/api/settings")
        payload = response.json()
        assert response.status_code == 200, payload
        assert payload["settings"]["torbox_api_key"] == schemas.TORBOX_API_KEY_MASK, payload
        assert TORBOX_KEY not in json.dumps(payload), "API key leaked through GET /api/settings"

        # Saving the mask back is a no-op; a fresh key replaces the stored one.
        response2 = ctx.client.post("/api/settings", json_data={
            "torbox_api_key": schemas.TORBOX_API_KEY_MASK,
            "downloader_engine": "torbox",
        })
        assert response2.status_code == 200, response2.json()
        assert ctx.db.get_setting("torbox_api_key") == TORBOX_KEY
        assert ctx.db.get_setting("downloader_engine") == "torbox"
        response3 = ctx.client.post("/api/settings", json_data={"torbox_api_key": "new-key-value"})
        assert response3.status_code == 200, response3.json()
        assert ctx.db.get_setting("torbox_api_key") == "new-key-value"

        # Strict validation.
        bad_engine = ctx.client.post("/api/settings", json_data={"downloader_engine": "aria2"})
        assert bad_engine.status_code == 400, bad_engine.json()
        bad_poll = ctx.client.post("/api/settings", json_data={"torbox_poll_seconds": "1"})
        assert bad_poll.status_code == 400, bad_poll.json()

        # Settings page template renders the mask, never the key, and includes the
        # engine selector / Torbox fields (the e2e harness renders pages minimally).
        settings_html = open(
            os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8"
        ).read()
        for marker in (
            'id="setting-torbox-api-key"',
            'id="setting-downloader-engine"',
            'id="setting-torbox-enabled"',
            'id="setting-torbox-delete-after"',
            'id="setting-torbox-poll-seconds"',
            'id="setting-torbox-max-concurrent"',
            'id="setting-torbox-timeout-hours"',
            "{{ settings.torbox_api_key }}",
            "testTorboxConnection()",
            "/api/downloader/torbox/test",
        ):
            assert marker in settings_html, f"settings template missing: {marker}"
        assert "setting-torbox-api-key" in settings_html, "settings card missing"
        for row in ctx.db.get_logs(limit=100):
            assert "new-key-value" not in json.dumps(row), "API key leaked into activity logs"

        # Torbox test endpoint: plan/slots, and an unsaved key is used without echoing.
        fake_client = MagicMock()
        fake_client.test_connection.return_value = {
            "success": True, "plan": "Pro", "active_slots": 3, "message": "ok",
        }
        with patch("app.main.TorboxDownloader", return_value=fake_client) as ctor:
            tested = ctx.client.post("/api/downloader/torbox/test", json_data={"torbox_api_key": "tmp-key"})
        body = tested.json()
        assert tested.status_code == 200 and body["plan"] == "Pro" and body["active_slots"] == 3, body
        ctor.assert_called_once()
        assert "tmp-key" not in json.dumps(body), body
        print("    [PASSED] key masked on GET/UI, mask is a no-op on save, validation strict, test endpoint returns plan/slots.")
    finally:
        ctx.close()


# ===========================================================================
# 10. Engine-aware /api/downloads/status (Torbox healthy + unreachable)
# ===========================================================================
def test_downloads_status_engine_aware():
    print("--> Testing engine-aware /api/downloads/status...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_torbox_status_")
    try:
        assert ctx.client.get("/api/downloads/status").json()["engine"] == "qbit"
        ctx.db.set_setting("downloader_engine", "torbox")
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        _insert_release(ctx.db, "status-guid", "Status Release", downloader="torbox",
                        downloader_id="88", download_status="DOWNLOADING", torrent_hash=HASH,
                        downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        fake = FakeTorboxService(state="downloading", files=[{"id": 1, "name": "Series Vol. 01.cbz", "size": 10}])
        with patch("app.main.build_downloader", return_value=fake):
            payload = ctx.client.get("/api/downloads/status").json()
        assert payload["engine"] == "torbox", payload
        assert payload["status"] == "success" and payload["downloader"]["connected"] is True, payload
        assert payload["count"] >= 1, payload
        item = payload["torrents"][0]
        for key in ("engine", "state", "cached", "files", "progress", "hash", "name"):
            assert key in item, (key, item)
        assert item["engine"] == "torbox" and item["state"] == "downloading", item

        # Unreachable Torbox: still HTTP 200 with a valid degraded payload.
        unreachable = MagicMock()
        unreachable.test_connection.return_value = {"success": False, "message": "connection refused"}
        unreachable.timeout = 1.0
        with patch("app.main.build_downloader", return_value=unreachable):
            response = ctx.client.get("/api/downloads/status")
        body = response.json()
        assert response.status_code == 200, body
        assert body["engine"] == "torbox" and body["downloader"]["connected"] is False, body
        assert "connection refused" in body["downloader"]["message"], body
        print("    [PASSED] status exposes engine/state/cached/files; unreachable Torbox degrades without a 500.")
    finally:
        ctx.close()


# ===========================================================================
# 11. Schema migrations + defaults
# ===========================================================================
def test_schema_and_settings_defaults():
    print("--> Testing schema migrations and settings defaults...")
    ctx = Ctx()
    try:
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(releases);")
            columns = {row["name"] for row in cur.fetchall()}
        for column in ("downloader", "downloader_id", "downloader_started_at"):
            assert column in columns, columns
        settings = ctx.db.get_all_settings()
        assert settings.get("downloader_engine") == "qbit", settings.get("downloader_engine")
        assert settings.get("torbox_enabled") == "false", settings.get("torbox_enabled")
        assert settings.get("torbox_delete_after_download") == "true", settings.get("torbox_delete_after_download")
        assert settings.get("torbox_poll_seconds") == "5", settings.get("torbox_poll_seconds")
        assert settings.get("torbox_max_concurrent") == "2", settings.get("torbox_max_concurrent")
        assert settings.get("torbox_timeout_hours") == "6", settings.get("torbox_timeout_hours")
        assert settings.get("torbox_api_key") == "", settings.get("torbox_api_key")
        # read/write migration columns via the helper and re-init idempotency.
        rid = _insert_release(ctx.db, "migration-guid", "Migration Release")
        ctx.db.update_download_handle(rid, "torbox", downloader_id="123", torrent_hash=HASH)
        row = ctx.db.get_release_by_id(rid)
        assert row["downloader"] == "torbox" and row["downloader_id"] == "123", row
        assert row["downloader_started_at"], row
        ctx.db.init_db()
        row2 = ctx.db.get_release_by_id(rid)
        assert row2["download_status"] == "DOWNLOADING", row2["download_status"]
        print("    [PASSED] guarded migrations + default settings present; init_db re-run is safe.")
    finally:
        ctx.close()


# ===========================================================================
# 12. Queued-slot submissions (task-21): handle, persistence, single surfacing
# ===========================================================================
def test_queued_submit_and_pending_poll():
    print("--> Testing queued-only create -> handle/persist/surface-once...")
    # Client level: a createtorrent response with only queued_id is a success.
    api = FakeApi()
    api.add("/torrents/checkcached", data=[])
    api.add("/torrents/createtorrent", data={"queued_id": 91, "hash": HASH,
                                             "active_limit": 2, "current_active_downloads": 2})
    downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)
    with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
        result = downloader.submit({"download_url": f"magnet:?xt=urn:btih:{HASH}"}, "/staging", [2])
    assert result["state"] == "queued_slot" and result["queued_id"] == 91, result
    assert result["handle"]["queued_id"] == 91, result["handle"]
    assert not result["handle"].get("torrent_id"), result["handle"]
    assert result["cached"] is False, result

    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        queued_service = FakeTorboxService(state="queued_slot", files=[])
        queued_service.submit = lambda release, savepath, wanted_volumes=None: {
            "handle": {"engine": "torbox", "queued_id": 91, "torrent_id": None,
                       "hash": HASH, "savepath": savepath},
            "state": "queued_slot", "cached": False, "queued_id": 91,
        }
        rid = _insert_release(ctx.db, "queued-guid", "Queued Release")
        with patch("app.scheduler.build_downloader", return_value=queued_service):
            submit = ctx.scheduler.submit_torbox_download(
                rid, "Queued Release", f"magnet:?xt=urn:btih:{HASH}", missing_volumes=[2],
            )
        assert submit["success"] is True and submit["queued"] is True, submit
        row = ctx.db.get_release_by_id(rid)
        assert row["download_status"] == "DOWNLOADING", row
        assert row["downloader"] == "torbox" and row["downloader_id"] == "queued:91", row
        submitted_events = ctx.events("TORBOX_SUBMITTED")
        assert len(submitted_events) == 1, submitted_events
        submitted_text = submitted_events[0].get("message", "") + submitted_events[0].get("details", "")
        assert "queued" in submitted_text.lower(), submitted_events[0]
        assert ctx.events("TORBOX_QUEUED") == [], "queued event is the poller's single surfacing"
        assert ctx.events("TORBOX_FAILED") == []

        # Still queued: surfaced once, state stays DOWNLOADING, no error spam.
        with patch("app.scheduler.build_downloader", return_value=queued_service):
            first = ctx.scheduler.poll_torbox_downloads()
            second = ctx.scheduler.poll_torbox_downloads()
        assert first["completed"] == 0 and second["completed"] == 0, (first, second)
        assert ctx.db.get_release_by_id(rid)["download_status"] == "DOWNLOADING"
        assert len(ctx.events("TORBOX_QUEUED")) == 1, ctx.events("TORBOX_QUEUED")
        assert ctx.events("TORBOX_FAILED") == [], ctx.events("TORBOX_FAILED")

        # Queued jobs do not consume torbox_max_concurrent: with max=1 and only a
        # queued row present, a fresh active submission is still allowed.
        ctx.db.set_setting("torbox_max_concurrent", "1")
        active_service = FakeTorboxService(state="downloading")
        rid2 = _insert_release(ctx.db, "active-allowed-guid", "Active Allowed")
        with patch("app.scheduler.build_downloader", return_value=active_service):
            allowed = ctx.scheduler.submit_torbox_download(
                rid2, "Active Allowed", f"magnet:?xt=urn:btih:{HASH}", missing_volumes=[1],
            )
        assert allowed["success"] is True, allowed
        assert len(active_service.submit_calls) == 1, "queued row must not block a new active slot"
        print("    [PASSED] queued-only create accepted; persisted as queued:<id>; surfaced once; slot accounting correct.")
    finally:
        ctx.close()


# ===========================================================================
# 13. Real client: getqueued polling, promotion, masked repr
# ===========================================================================
def test_queued_getqueued_and_promotion():
    print("--> Testing getqueued polling + promotion to torrent_id...")
    downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)
    assert TORBOX_KEY not in repr(downloader), repr(downloader)
    assert TORBOX_KEY not in str(downloader), str(downloader)
    assert "api_key='***'" in repr(downloader), repr(downloader)

    # Still queued: getqueued has the item without a torrent_id.
    api = FakeApi().add("/torrents/getqueued", data=[{
        "id": 91, "hash": HASH, "name": "Queued", "size": 100,
        "queue_position": 1, "active_limit": 2,
    }])
    with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
        info = downloader.status({"engine": "torbox", "queued_id": 91, "hash": HASH})
    assert info["state"] == "queued_slot", info
    assert info["queue_position"] == 1 and info["active_limit"] == 2, info
    assert info["cached"] is False and info["files"] == [], info
    assert api.urls_for("/torrents/getqueued"), api.calls

    # Activated: the queued row now carries the real torrent_id.
    api2 = FakeApi()
    api2.add("/torrents/getqueued", data=[{"id": 91, "hash": HASH, "torrent_id": 555}])
    api2.add("/torrents/mylist", data=[{
        "id": 555, "hash": HASH, "download_state": "downloading", "progress": 0.4,
        "size": 200, "files": [{"id": 7, "name": "Series Vol. 02.cbz", "size": 200}],
    }])
    with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api2):
        promoted = downloader.status({"engine": "torbox", "queued_id": 91, "hash": HASH})
    assert promoted["promoted_torrent_id"] == 555, promoted
    assert promoted["state"] == "downloading" and promoted["handle"]["torrent_id"] == 555, promoted
    assert promoted["files"][0]["id"] == 7, promoted

    # Transition variant: the queued row vanished; the hash is already in mylist.
    api3 = FakeApi()
    api3.add("/torrents/getqueued", data=[])
    api3.add("/torrents/mylist", data=[{"id": 556, "hash": HASH, "download_state": "completed",
                                        "download_finished": True, "progress": 1.0, "files": []}])
    with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api3):
        promoted2 = downloader.status({"engine": "torbox", "queued_id": 91, "hash": HASH})
    assert promoted2["promoted_torrent_id"] == 556, promoted2
    assert promoted2["state"] == "ready", promoted2
    print("    [PASSED] getqueued polled, queue_position/active_limit surfaced, activation captured; repr masked.")


# ===========================================================================
# 14. Queue -> active promotion completes the normal poll/fetch flow
# ===========================================================================
def test_queue_promotion_completes_flow():
    print("--> Testing queue -> active promotion completing the flow...")
    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        ctx.db.set_setting("torbox_delete_after_download", "true")
        rid = _insert_release(
            ctx.db, "promote-guid", "Promote Me", downloader="torbox",
            downloader_id="queued:91", download_status="DOWNLOADING",
            downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            torrent_hash=HASH, volumes=[1, 2], missing_volumes=[2],
        )
        files = [{"id": 1, "name": "Series Vol. 01.cbz", "size": 10},
                 {"id": 2, "name": "Series Vol. 02.cbz", "size": 10}]
        service = SequenceTorboxService([
            {"state": "queued_slot", "raw_state": "queued", "progress": 0.0, "files": [],
             "queued_id": 91, "queue_position": 1, "active_limit": 2},
            {"state": "ready", "raw_state": "completed", "progress": 1.0, "files": files,
             "cached": False, "promoted_torrent_id": 555},
        ], files=files)
        with patch("app.scheduler.build_downloader", return_value=service):
            first = ctx.scheduler.poll_torbox_downloads()
            assert first["completed"] == 0, first
            assert ctx.db.get_release_by_id(rid)["downloader_id"] == "queued:91"

            second = ctx.scheduler.poll_torbox_downloads()
            assert second["completed"] == 1, second
        row = ctx.db.get_release_by_id(rid)
        assert row["download_status"] == "COMPLETED", row
        assert row["downloader_id"] == "555", row
        assert service.fetch_calls and [item["id"] for item in service.fetch_calls[0][1]] == [2], service.fetch_calls
        assert service.cancelled and service.cancelled[0]["torrent_id"] == 555, service.cancelled
        assert ctx.events("TORBOX_DOWNLOADED"), ctx.events("TORBOX_DOWNLOADED")
        assert ctx.events("TORBOX_PROMOTED"), "promotion must be audited"
        assert ctx.events("TORBOX_FAILED") == [], ctx.events("TORBOX_FAILED")
        # The original submission timestamp is preserved across promotion.
        assert row["downloader_started_at"], row
        print("    [PASSED] queued job promoted to torrent 555, fetched missing volume 2, completed and cleaned up.")
    finally:
        ctx.close()


# ===========================================================================
# 15. Queue timeout -> FAILED + actionable audit (no infinite rotation)
# ===========================================================================
def test_queue_timeout_fails():
    print("--> Testing queued-slot timeout -> FAILED + audit...")
    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        ctx.db.set_setting("torbox_timeout_hours", "6")
        old = (datetime.utcnow() - timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
        rid = _insert_release(
            ctx.db, "queue-timeout-guid", "Queue Timeout", downloader="torbox",
            downloader_id="queued:91", download_status="DOWNLOADING",
            downloader_started_at=old, torrent_hash=HASH,
        )
        queued_service = FakeTorboxService(state="queued_slot", files=[])
        with patch("app.scheduler.build_downloader", return_value=queued_service):
            result = ctx.scheduler.poll_torbox_downloads()
        assert result["failed"] == 1, result
        assert ctx.db.get_release_by_id(rid)["download_status"] == "FAILED"
        failures = [e for e in ctx.events("TORBOX_FAILED")
                    if "No free Torbox active slot" in (e.get("details") or "")]
        assert failures, ctx.events("TORBOX_FAILED")
        assert "REQUESTED" in (failures[0].get("details") or ""), failures[0]
        assert queued_service.status_calls == [], "timed-out queued job must not be polled further"
        print("    [PASSED] queued beyond the window -> FAILED with an actionable audit message.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running Torbox/engine suite (task-20 + queued-slot hardening task-21)")
    print("=" * 78)
    tests = [
        test_engine_selection_and_qbit_adapter,
        test_torbox_submit_and_status,
        test_torbox_submission_forms_and_fallbacks,
        test_torbox_errors_and_secret_hygiene,
        test_torbox_fetch_files_and_zip_safety,
        test_scheduler_submit_and_poll_happy_path,
        test_poll_fallback_stalled_timeout,
        test_concurrency_and_delete_setting,
        test_manual_download_routing,
        test_settings_masking_and_validation,
        test_downloads_status_engine_aware,
        test_schema_and_settings_defaults,
        test_queued_submit_and_pending_poll,
        test_queued_getqueued_and_promotion,
        test_queue_promotion_completes_flow,
        test_queue_timeout_fails,
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
        print(f"TORBOX SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TORBOX TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
