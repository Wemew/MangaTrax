#!/usr/bin/env python3
"""Feed-poll UX suite: non-blocking "Poll Feeds" + no silent provider failures.

The topbar "Poll Feeds" button called ``POST /api/feeds/check``, whose async handler
ran ``scheduler.check_all_feeds()`` inline.  That blocked the uvicorn event loop for
the whole network poll, so the entire web UI froze until the poll finished.  Worse,
per-feed provider exceptions (e.g. a dead Nyaa lookup) were only logged: the HTTP
response still said ``{"success": true}`` with a summary that omitted the failures.

The poll now runs on a daemon thread; the endpoint returns immediately and the UI
polls ``GET /api/feeds/check/status``.  Per-feed failures are counted and reported
in the result (``success`` false, ``feeds_failed``, ``errors``, message detail).

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import sys
import tempfile
import shutil
import time
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.covers import CoverCache
from app.database import MangaDatabase
from app.scheduler import MangaScheduler

MAIN_SOURCE = os.path.join(BACKEND_DIR, "app", "main.py")
SCHEDULER_SOURCE = os.path.join(BACKEND_DIR, "app", "scheduler.py")
BASE_TEMPLATE = os.path.join(BACKEND_DIR, "app", "templates", "base.html")


def _source(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class _Ctx:
    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_feedpoll_")
        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.db.set_setting("calibre_path", os.path.join(self.tmpdir, "calibre"))
        # Hermetic: remove the default seeded RSS feed so tests never hit the network.
        for feed in self.db.get_all_feeds():
            self.db.delete_feed(feed["id"])
        self.scheduler = MangaScheduler(self.db, CoverCache(self.tmpdir))

    def add_feed(self, name, feed_type="rss", url="https://example.invalid/feed.xml", auto_download=False):
        feed_id = self.db.add_feed(name, feed_type, url, auto_download=auto_download)
        return feed_id

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ===========================================================================
# 1. Endpoint/route source guard: the poll is not awaited inline
# ===========================================================================
def test_endpoint_source_guard():
    print("--> Testing feed-check route wiring (source guard)...")
    main_source = _source(MAIN_SOURCE)
    start = main_source.index('@app.post("/api/feeds/check")')
    end = main_source.index('@app.get("/api/feeds/check/status")')
    handler = main_source[start:end]
    assert "async def api_feeds_check" in handler, handler
    assert "start_feed_check()" in handler, handler
    # The blocking call must NOT be inside the async handler (it blocked the loop).
    assert "check_all_feeds(" not in handler, handler
    status_endpoint = main_source[end:end + 400]
    assert "get_feed_check_status()" in status_endpoint, status_endpoint
    # No async route may call the blocking poll directly.
    assert "scheduler.check_all_feeds(" not in main_source, "blocking feed poll still called from a route"

    scheduler_source = _source(SCHEDULER_SOURCE)
    assert "def start_feed_check" in scheduler_source
    assert "def get_feed_check_status" in scheduler_source
    wrapper_start = scheduler_source.index("    def check_all_feeds(self)")
    wrapper = scheduler_source[wrapper_start:wrapper_start + 700]
    assert "_feed_check_lock.acquire(blocking=False)" in wrapper, wrapper
    print("    [PASSED] poll runs on a daemon thread; routes only start/query it.")


# ===========================================================================
# 2. start_feed_check returns immediately; status reports progress/result
# ===========================================================================
def test_start_feed_check_is_async():
    print("--> Testing background feed check start/status...")
    ctx = _Ctx()
    try:
        payload = {
            "success": True,
            "message": "Feeds checked: 0 releases found, 0 requested, 0 sent to downloader.",
            "releases_found": 0,
            "requested": 0,
            "sent_to_downloader": 0,
            "feeds_failed": 0,
            "errors": [],
            "skipped": False,
        }

        def slow_poll():
            time.sleep(0.5)
            return payload

        ctx.scheduler.check_all_feeds = slow_poll
        began = time.perf_counter()
        started = ctx.scheduler.start_feed_check()
        elapsed = time.perf_counter() - began
        assert elapsed < 0.2, f"start_feed_check() blocked for {elapsed:.2f}s"
        assert started["success"] is True and started["started"] is True, started

        # A second trigger while the first is still running reports "already in progress".
        again = ctx.scheduler.start_feed_check()
        assert again["success"] is True and again["started"] is False, again
        assert "already in progress" in again["message"].lower(), again

        status = ctx.scheduler.get_feed_check_status()
        assert status["running"] is True, status
        assert status["started_at"], status
        assert status["result"] is None, status
        assert _wait_for(lambda: not ctx.scheduler.get_feed_check_status()["running"]), "poll did not finish"
        finished = ctx.scheduler.get_feed_check_status()
        assert finished["running"] is False, finished
        assert finished["finished_at"], finished
        assert finished["result"]["success"] is True, finished
        assert finished["result"]["message"] == payload["message"], finished
        print("    [PASSED] POST returns immediately; status exposes progress and the result.")
    finally:
        ctx.close()


# ===========================================================================
# 3. A crashed poll is surfaced, never kills the app
# ===========================================================================
def test_feed_check_crash_surfaced():
    print("--> Testing background poll crash is surfaced...")
    ctx = _Ctx()
    try:
        def broken_poll():
            raise RuntimeError("nyaa exploded")

        ctx.scheduler.check_all_feeds = broken_poll
        started = ctx.scheduler.start_feed_check()
        assert started["started"] is True, started
        assert _wait_for(lambda: not ctx.scheduler.get_feed_check_status()["running"]), "poll thread did not finish"
        result = ctx.scheduler.get_feed_check_status()["result"]
        assert result["success"] is False, result
        assert "nyaa exploded" in result["message"], result
        print("    [PASSED] crash reported in the status result; worker/process unaffected.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Provider failures are counted and reported (no silent success)
# ===========================================================================
def test_provider_failures_not_silent():
    print("--> Testing per-feed failures are reported, not a silent success...")
    ctx = _Ctx()
    try:
        ctx.add_feed("Good Feed")
        ctx.add_feed("Broken Feed")
        ctx.add_feed("Unsupported Feed")

        class _Provider:
            def __init__(self, name):
                self.name = name

            def fetch_releases(self):
                if self.name == "Broken Feed":
                    raise RuntimeError("nyaa lookup failed: connection reset")
                return []

        def fake_create_provider(feed, **_kwargs):
            if feed.get("name") == "Unsupported Feed":
                return None
            return _Provider(feed.get("name"))

        with patch("app.scheduler.create_provider", side_effect=fake_create_provider):
            result = ctx.scheduler.check_all_feeds()

        assert result["success"] is False, result
        assert result["feeds_failed"] == 2, result
        failed_names = {item["feed"] for item in result["errors"]}
        assert failed_names == {"Broken Feed", "Unsupported Feed"}, result["errors"]
        assert "2 feed(s) failed" in result["message"], result["message"]
        assert "nyaa lookup failed" in result["message"], result["message"]
        assert "Broken Feed" in result["message"], result["message"]
        assert result["releases_found"] == 0, result

        # The failing feeds are marked ERROR; the good one is OK.
        feeds = {feed["name"]: feed for feed in ctx.db.get_all_feeds()}
        assert feeds["Broken Feed"]["last_status"] == "ERROR", feeds["Broken Feed"]
        assert feeds["Unsupported Feed"]["last_status"] == "ERROR", feeds["Unsupported Feed"]
        assert feeds["Good Feed"]["last_status"] == "OK", feeds["Good Feed"]

        # And the audit trail records the failure count, not just "completed".
        rows = [row for row in ctx.db.get_logs(limit=100) if row.get("event_type") == "FEED_POLL_COMPLETED"]
        assert rows, "FEED_POLL_COMPLETED missing"
        assert "feeds_failed=2" in (rows[0].get("details") or ""), rows[0]
        print("    [PASSED] success=false + feeds_failed/errors + message detail; audit records it.")
    finally:
        ctx.close()


# ===========================================================================
# 5. All-OK polls still report success (regression)
# ===========================================================================
def test_all_ok_polls_report_success():
    print("--> Testing all-OK feed poll still reports success...")
    ctx = _Ctx()
    try:
        ctx.add_feed("Good Feed")

        class _Provider:
            def fetch_releases(self):
                return []

        with patch("app.scheduler.create_provider", return_value=_Provider()):
            result = ctx.scheduler.check_all_feeds()
        assert result["success"] is True, result
        assert result["feeds_failed"] == 0, result
        assert result["errors"] == [], result
        assert result["skipped"] is False, result
        print("    [PASSED] unchanged success payload when every feed works.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Concurrent poll is skipped, not run twice
# ===========================================================================
def test_concurrent_poll_skipped():
    print("--> Testing concurrent feed-poll deduplication...")
    ctx = _Ctx()
    try:
        ctx.add_feed("Good Feed")
        assert ctx.scheduler._feed_check_lock.acquire(blocking=False) is True
        try:
            result = ctx.scheduler.check_all_feeds()
        finally:
            ctx.scheduler._feed_check_lock.release()
        assert result["success"] is True, result
        assert result["skipped"] is True, result
        assert "already in progress" in result["message"].lower(), result
        print("    [PASSED] second poll is skipped with an explicit message.")
    finally:
        ctx.close()


# ===========================================================================
# 7. Route + status end-to-end through the app
# ===========================================================================
def test_routes_end_to_end():
    print("--> Testing feed-check routes through the app...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_feedpoll_http_")
    try:
        def failing_poll():
            time.sleep(0.2)
            return {
                "success": False,
                "message": "Feeds checked: 0 releases found, 0 requested, 0 sent to downloader. 1 feed(s) failed: Broken Feed: nyaa lookup failed",
                "releases_found": 0,
                "requested": 0,
                "sent_to_downloader": 0,
                "feeds_failed": 1,
                "errors": [{"feed": "Broken Feed", "error": "nyaa lookup failed"}],
                "skipped": False,
            }

        ctx.scheduler.check_all_feeds = failing_poll
        response = ctx.client.post("/api/feeds/check")
        assert response.status_code == 200, (response.status_code, response.json())
        body = response.json()
        assert body["success"] is True and body["started"] is True, body

        def _done():
            status = ctx.client.get("/api/feeds/check/status").json()
            return status if not status.get("running") else None

        deadline = time.time() + 5.0
        status = None
        while time.time() < deadline:
            status = _done()
            if status is not None:
                break
            time.sleep(0.05)
        assert status is not None, "status never reported completion"
        result = status["result"]
        assert result["success"] is False, result
        assert result["feeds_failed"] == 1, result
        assert "Broken Feed" in result["message"], result
        print("    [PASSED] POST returns immediately; status carries the failure detail.")
    finally:
        ctx.close()


# ===========================================================================
# 8. Provider-level failures (Nyaa/RSS/HTML) are typed, not silent []
# ===========================================================================
def test_provider_network_failures_are_typed():
    print("--> Testing provider network failures are typed, not silent...")
    import urllib.error
    from app.providers.base import ProviderFetchError
    from app.providers.html_scraper import HtmlScraperProvider
    from app.providers.nyaa import NyaaMangaProvider
    from app.providers.rss import RSSFeedProvider
    from app.providers import rss as rss_module

    # Nyaa: first page unreachable -> ProviderFetchError (not [] + "success").
    nyaa = NyaaMangaProvider(name="Nyaa", config={"max_pages": 1, "delay_seconds": 0})
    with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=urllib.error.URLError("dns failure")):
        try:
            nyaa.fetch_releases()
        except ProviderFetchError as exc:
            assert "Failed to fetch Nyaa page 1" in str(exc), str(exc)
        else:
            assert False, "dead Nyaa lookup must raise ProviderFetchError"

    # RSS: parser returns None on network failure -> ProviderFetchError.
    with patch.object(rss_module, "_parse_feed_with_timeout", return_value=None):
        try:
            RSSFeedProvider(name="rss", url="https://example.invalid/feed").fetch_releases()
        except ProviderFetchError as exc:
            assert "Failed to fetch RSS feed" in str(exc), str(exc)
        else:
            assert False, "dead RSS feed must raise ProviderFetchError"

    # HTML scraper: first page unreachable -> ProviderFetchError.
    scraper = HtmlScraperProvider(name="html", url="https://example.invalid/releases")
    with patch.object(HtmlScraperProvider, "_fetch_page_html", return_value=None):
        try:
            scraper.fetch_releases()
        except ProviderFetchError as exc:
            assert "Failed to fetch HTML page 1" in str(exc), str(exc)
        else:
            assert False, "dead HTML source must raise ProviderFetchError"
    print("    [PASSED] all three providers surface unreachable sources as typed errors.")


# ===========================================================================
# 9. The poll reports those provider failures (end-to-end through the scheduler)
# ===========================================================================
def test_poll_reports_provider_failures():
    print("--> Testing poll reports real provider failures...")
    import urllib.error
    ctx = _Ctx()
    try:
        ctx.add_feed("Nyaa Feed", feed_type="nyaa", url="https://nyaa.si/?f=0&c=3_1")
        with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=urllib.error.URLError("nyaa down")):
            result = ctx.scheduler.check_all_feeds()
        assert result["success"] is False, result
        assert result["feeds_failed"] == 1, result
        assert result["errors"][0]["feed"] == "Nyaa Feed", result["errors"]
        assert "nyaa down" in result["errors"][0]["error"], result["errors"]
        assert "1 feed(s) failed" in result["message"], result["message"]
        feeds = {feed["name"]: feed for feed in ctx.db.get_all_feeds()}
        assert feeds["Nyaa Feed"]["last_status"] == "ERROR", feeds["Nyaa Feed"]
        print("    [PASSED] dead Nyaa lookup fails the feed and the poll result.")

        # A provider that explodes during construction is a per-feed failure too.
        ctx2 = _Ctx()
        try:
            ctx2.add_feed("Bad Config")
            ctx2.add_feed("Good Feed")

            class _Provider:
                def fetch_releases(self):
                    return []

            def fake_factory(feed, **_kwargs):
                if feed.get("name") == "Bad Config":
                    raise RuntimeError("invalid feed configuration")
                return _Provider()

            with patch("app.scheduler.create_provider", side_effect=fake_factory):
                result2 = ctx2.scheduler.check_all_feeds()
            assert result2["success"] is False, result2
            assert result2["feeds_failed"] == 1, result2
            assert result2["errors"][0]["feed"] == "Bad Config", result2["errors"]
            assert "configuration error" in result2["errors"][0]["error"], result2["errors"]
        finally:
            ctx2.close()
    finally:
        ctx.close()


# ===========================================================================
# 10. A legitimately empty source is still a success (no false alarms)
# ===========================================================================
def test_empty_source_is_not_a_failure():
    print("--> Testing an empty (but reachable) source is still a success...")
    ctx = _Ctx()
    try:
        ctx.add_feed("Nyaa Feed", feed_type="nyaa", url="https://nyaa.si/?f=0&c=3_1")

        class _EmptyResponse:
            status = 200

            def read(self):
                return b"<html><body><table class='torrent-list'><tbody></tbody></table></body></html>"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with patch("app.providers.nyaa.urllib.request.urlopen", return_value=_EmptyResponse()):
            result = ctx.scheduler.check_all_feeds()
        assert result["success"] is True, result
        assert result["feeds_failed"] == 0, result
        assert result["releases_found"] == 0, result
        feeds = {feed["name"]: feed for feed in ctx.db.get_all_feeds()}
        assert feeds["Nyaa Feed"]["last_status"] == "OK", feeds["Nyaa Feed"]
        print("    [PASSED] reachable-but-empty feeds stay successful; no false alarms.")
    finally:
        ctx.close()


# ===========================================================================
# 11. UI source guard: waits via status polling, shows failure toasts
# ===========================================================================
def test_ui_polls_status():
    print("--> Testing UI polling source guard...")
    source = _source(BASE_TEMPLATE)
    assert "async function waitForFeedCheck" in source, "missing waitForFeedCheck helper"
    assert "/api/feeds/check/status" in source, "UI must poll the status endpoint"
    start = source.index("async function triggerFeedCheck")
    end = source.index("async function waitForFeedCheck")
    handler = source[start:end]
    assert "waitForFeedCheck()" in handler, handler
    # Result is surfaced as success OR error (no silent success).
    assert "result.success" in handler, handler
    assert "showToast(result.message, 'success')" in handler, handler
    assert "showToast(result.message || 'Feed check failed', 'error')" in handler, handler
    # Reload only happens after the background poll completes.
    assert handler.index("waitForFeedCheck()") < handler.index("location.reload()"), handler
    print("    [PASSED] UI starts the poll, polls status, toasts the real result.")


def main():
    print("=" * 78)
    print("Running feed-poll availability/failure-reporting suite")
    print("=" * 78)
    tests = [
        test_endpoint_source_guard,
        test_start_feed_check_is_async,
        test_feed_check_crash_surfaced,
        test_provider_failures_not_silent,
        test_all_ok_polls_report_success,
        test_concurrent_poll_skipped,
        test_routes_end_to_end,
        test_provider_network_failures_are_typed,
        test_poll_reports_provider_failures,
        test_empty_source_is_not_a_failure,
        test_ui_polls_status,
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
        print(f"FEED-POLL SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL FEED-POLL TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
