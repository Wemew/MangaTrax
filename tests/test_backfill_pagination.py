#!/usr/bin/env python3
"""task-19 suite: backfill pagination must not treat transient fetch errors as
"end of results", must report WHY it stopped, and must be gentle on mirrors.

Symptom: one 429/timeout on page 2 made crawl_pages return [] -> the backfill loop
broke and printed a successful completion summary after 1-2 pages.

Covers: retry on 429 (honouring Retry-After), persistent 500 -> hard failure
(backfill_status=error, BACKFILL_ERROR, no BACKFILL_COMPLETED, last_page preserved),
404/empty page -> normal end, user stop, exact page budget, stop reasons in the
summary, and the 1.0 s minimum between HTML page fetches even when delay is 0.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import re
import sys
import tempfile
import shutil
import traceback
import urllib.error
from email.message import Message
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.covers import CoverCache  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from app.providers.base import ProviderFetchError  # noqa: E402
from app.providers.nyaa import (  # noqa: E402
    NYAA_BACKOFF_SECONDS,
    NYAA_FETCH_ATTEMPTS,
    NYAA_MIN_PAGE_INTERVAL_SECONDS,
    NyaaMangaProvider,
)
from app.scheduler import MangaScheduler  # noqa: E402


def _page_html(page, rows=1):
    body = []
    for index in range(rows):
        ident = page * 1000 + index
        body.append(
            f"""
            <tr>
              <td>Category</td>
              <td><a href="/view/{ident}">Test Manga {ident} Vol. 01</a></td>
              <td><a href="/download/{ident}.torrent">dl</a></td>
              <td>500 MiB</td>
              <td>2026-10-31</td>
              <td>10</td>
            </tr>
            """
        )
    return (
        "<html><body><table class='torrent-list'><tbody>"
        + "".join(body)
        + "</tbody></table></body></html>"
    )


EMPTY_PAGE_HTML = "<html><body><table class='torrent-list'><tbody></tbody></table></body></html>"


class _Response:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _headers(retry_after=None):
    message = Message()
    if retry_after is not None:
        message["Retry-After"] = str(retry_after)
    return message


class _FetchPlan:
    """Scripted urllib.request.urlopen double for Nyaa HTML pages."""

    def __init__(self, plan, default_rows=1, on_page=None):
        self.plan = {page: list(entries) for page, entries in plan.items()}
        self.default_rows = default_rows
        self.on_page = on_page
        self.calls = []
        self.sleeps = []

    @staticmethod
    def _page_of(url):
        match = re.search(r"[?&]p=(\d+)", url)
        assert match, url
        return int(match.group(1))

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        page = self._page_of(url)
        self.calls.append((page, url))
        if self.on_page:
            self.on_page(page)
        entries = self.plan.get(page)
        if entries is None:
            return _Response(200, _page_html(page, self.default_rows))
        status, body, retry_after = entries.pop(0) if len(entries) > 1 else entries[0]
        if status == 200:
            return _Response(200, body if body is not None else _page_html(page, self.default_rows))
        raise urllib.error.HTTPError(url, status, f"HTTP {status}", _headers(retry_after), None)


class _Ctx:
    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_backfill_")
        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.db.set_setting("calibre_path", os.path.join(self.tmpdir, "calibre"))
        self.db.set_setting("qbit_enabled", "false")
        self.db.set_setting("auto_download", "false")
        self.db.set_setting("backfill_delay_seconds", "0")
        for seeded in self.db.get_all_feeds():
            self.db.delete_feed(seeded["id"])
        self.scheduler = MangaScheduler(self.db, CoverCache(self.tmpdir))
        self.processed = []
        self.scheduler._process_release = self._fake_process

    def _fake_process(self, rel, feed, matcher, known_series_keys=None):
        self.processed.append(rel.title)
        return True, "DISCOVERY", [], None, None

    def run(self, max_pages=3, delay=0.0, start_page=1):
        self.scheduler._run_polite_backfill_worker(
            max_pages=max_pages, delay_seconds=delay, start_page=start_page,
        )

    def run_with_fetch(self, plan, max_pages=3, delay=0.0, default_rows=1, on_page=None):
        fetch = _FetchPlan(plan, default_rows=default_rows, on_page=on_page)
        with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=fetch):
            with patch("app.providers.nyaa.time.sleep", side_effect=fetch.sleeps.append):
                with patch("app.providers.nyaa.random.uniform", return_value=0.0):
                    self.run(max_pages=max_pages, delay=delay)
        return fetch

    def events(self, event_type):
        return [row for row in self.db.get_logs(limit=200) if row["event_type"] == event_type]

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# 1. 429 then 200 on page 2 -> retry continues and the crawl completes
# ===========================================================================
def test_429_then_200_continues():
    print("--> Testing 429-then-200 retry on page 2...")
    ctx = _Ctx()
    try:
        fetch = ctx.run_with_fetch({
            1: [(200, None, None)],
            2: [(429, None, "2"), (200, None, None)],
            3: [(200, None, None)],
        }, max_pages=3)
        assert ctx.db.get_setting("backfill_status") == "idle", ctx.db.get_setting("backfill_status")
        summary = ctx.db.get_setting("backfill_progress")
        assert "max pages reached (3)" in summary, summary
        assert len(ctx.events("BACKFILL_COMPLETED")) == 1
        assert ctx.events("BACKFILL_ERROR") == []
        # page 2 was fetched twice (429 then 200).
        assert [page for page, _url in fetch.calls].count(2) == 2, fetch.calls
        # Retry-After: 2 was honoured (not the ~1 s exponential backoff).
        assert 2.0 in fetch.sleeps, fetch.sleeps
        assert all(sleep > 0 for sleep in fetch.sleeps), fetch.sleeps
        print("    [PASSED] transient 429 retried with Retry-After; crawl completed normally.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Persistent 500 -> hard failure, BACKFILL_ERROR (never COMPLETED)
# ===========================================================================
def test_persistent_500_fails_backfill():
    print("--> Testing persistent 500 fails the backfill correctly...")
    ctx = _Ctx()
    try:
        fetch = ctx.run_with_fetch({
            1: [(200, None, None)],
            2: [(200, None, None)],
            3: [(500, None, None)],
        }, max_pages=5)
        assert ctx.db.get_setting("backfill_status") == "error", ctx.db.get_setting("backfill_status")
        assert ctx.events("BACKFILL_COMPLETED") == [], ctx.events("BACKFILL_COMPLETED")
        errors = ctx.events("BACKFILL_ERROR")
        assert len(errors) == 1, errors
        message = f"{errors[0].get('message', '')} {errors[0].get('details', '')}"
        assert "page 3" in message and "HTTP 500" in message, message
        assert ctx.db.get_setting("backfill_last_page") == "3", ctx.db.get_setting("backfill_last_page")
        # Exhausted the bounded retry budget: page 3 fetched NYAA_FETCH_ATTEMPTS times.
        assert [page for page, _url in fetch.calls].count(3) == NYAA_FETCH_ATTEMPTS, fetch.calls
        assert len(NYAA_BACKOFF_SECONDS) == NYAA_FETCH_ATTEMPTS - 1, NYAA_BACKOFF_SECONDS
        print("    [PASSED] exhausted retries -> error status, audited, resume page preserved.")
    finally:
        ctx.close()


# ===========================================================================
# 3. 404 ends normally with an explicit reason
# ===========================================================================
def test_404_ends_normally():
    print("--> Testing a 404 page ends the crawl normally...")
    ctx = _Ctx()
    try:
        ctx.run_with_fetch({
            1: [(200, None, None)],
            2: [(200, None, None)],
            3: [(200, None, None)],
            4: [(404, None, None)],
        }, max_pages=6)
        assert ctx.db.get_setting("backfill_status") == "idle", ctx.db.get_setting("backfill_status")
        summary = ctx.db.get_setting("backfill_progress")
        assert "reached end of results (page 4: HTTP 404)" in summary, summary
        assert len(ctx.events("BACKFILL_COMPLETED")) == 1
        assert ctx.db.get_setting("backfill_last_page") == "4"
        print("    [PASSED] 404 is a genuine end with the reason in the summary.")
    finally:
        ctx.close()


# ===========================================================================
# 4. A parsed page with no rows ends normally (and is NOT a failure)
# ===========================================================================
def test_empty_page_ends_normally():
    print("--> Testing an empty page ends the crawl normally...")
    ctx = _Ctx()
    try:
        ctx.run_with_fetch({
            1: [(200, None, None)],
            2: [(200, EMPTY_PAGE_HTML, None)],
        }, max_pages=5)
        assert ctx.db.get_setting("backfill_status") == "idle", ctx.db.get_setting("backfill_status")
        summary = ctx.db.get_setting("backfill_progress")
        assert "reached end of results (page 2: no rows)" in summary, summary
        assert ctx.events("BACKFILL_ERROR") == []
        print("    [PASSED] 200-with-no-rows ends normally with a 'no rows' reason.")
    finally:
        ctx.close()


# ===========================================================================
# 5. User stop mid-run -> 'stopped by user' reason, last page recorded
# ===========================================================================
def test_user_stop_reports_reason():
    print("--> Testing user stop reports 'stopped by user'...")
    ctx = _Ctx()
    try:
        def stop_after_first(page):
            if page == 1:
                ctx.scheduler.stop_backfill_requested = True

        ctx.run_with_fetch({1: [(200, None, None)]}, max_pages=5, on_page=stop_after_first)
        summary = ctx.db.get_setting("backfill_progress")
        assert "stopped by user (page 1)" in summary, summary
        assert ctx.db.get_setting("backfill_status") == "idle", ctx.db.get_setting("backfill_status")
        assert ctx.db.get_setting("backfill_last_page") == "1", ctx.db.get_setting("backfill_last_page")
        assert len(ctx.events("BACKFILL_COMPLETED")) == 1
        print("    [PASSED] user stop reported explicitly; resume page recorded.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Requested page budget respected exactly
# ===========================================================================
def test_page_budget_respected():
    print("--> Testing the requested page budget is respected exactly...")
    ctx = _Ctx()
    try:
        fetch = ctx.run_with_fetch({}, max_pages=15, default_rows=1)
        assert [page for page, _url in fetch.calls] == list(range(1, 16)), fetch.calls
        summary = ctx.db.get_setting("backfill_progress")
        assert "max pages reached (15)" in summary, summary
        assert "processed 15 releases" in summary, summary
        assert ctx.db.get_setting("backfill_last_page") == "15"
        assert len(ctx.events("BACKFILL_COMPLETED")) == 1
        print("    [PASSED] exactly 15 pages fetched and the summary says so.")
    finally:
        ctx.close()


# ===========================================================================
# 7. Minimum inter-page delay even when the configured delay is 0
# ===========================================================================
def test_minimum_page_interval():
    print("--> Testing the 1.0 s minimum between HTML page fetches...")
    provider = NyaaMangaProvider(name="interval", config={"delay_seconds": 0, "base_url": "https://mirror.example"})
    fetch = _FetchPlan({}, default_rows=1)
    sleeps = []
    with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=fetch):
        with patch("app.providers.nyaa.time.sleep", side_effect=sleeps.append):
            provider.crawl_pages(max_pages=3, delay=0)
    assert [page for page, _url in fetch.calls] == [1, 2, 3], fetch.calls
    # Two inter-page waits; every one must respect the mirror-safe floor.
    assert len(sleeps) == 2, sleeps
    assert all(sleep >= NYAA_MIN_PAGE_INTERVAL_SECONDS for sleep in sleeps), sleeps
    assert provider.last_crawl_reason == "max pages reached (3)", provider.last_crawl_reason

    # The user setting is an override but never below the floor.
    fetch_zero = _FetchPlan({}, default_rows=1)
    sleeps_zero = []
    provider2 = NyaaMangaProvider(name="interval2", config={"delay_seconds": 0})
    with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=fetch_zero):
        with patch("app.providers.nyaa.time.sleep", side_effect=sleeps_zero.append):
            provider2.crawl_pages(max_pages=2, delay=0)
    assert sleeps_zero and all(sleep >= NYAA_MIN_PAGE_INTERVAL_SECONDS for sleep in sleeps_zero), sleeps_zero
    print("    [PASSED] 1.0 s floor enforced between pages even with delay=0.")


# ===========================================================================
# 8. Provider-level stop reasons and hard-fail semantics
# ===========================================================================
def test_provider_stop_reasons_and_hard_failure():
    print("--> Testing provider stop reasons and hard-fail semantics...")
    provider = NyaaMangaProvider(name="reasons", config={"delay_seconds": 0})

    # 404 -> normal end.
    fetch = _FetchPlan({1: [(404, None, None)]})
    with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=fetch):
        assert provider.crawl_pages(max_pages=2) == []
    assert "reached end of results (page 1: HTTP 404)" in provider.last_crawl_reason, provider.last_crawl_reason
    assert provider.last_crawl_pages[0]["kind"] == "end_of_results", provider.last_crawl_pages

    # Empty page -> normal end.
    fetch = _FetchPlan({1: [(200, EMPTY_PAGE_HTML, None)]})
    with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=fetch):
        assert provider.crawl_pages(max_pages=2) == []
    assert "no rows" in provider.last_crawl_reason, provider.last_crawl_reason

    # Page 1 persistent failure -> ProviderFetchError by default.
    fetch = _FetchPlan({1: [(503, None, None)]})
    with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=fetch):
        with patch("app.providers.nyaa.time.sleep", side_effect=lambda _s: None):
            try:
                provider.crawl_pages(max_pages=2)
            except ProviderFetchError as exc:
                assert "Failed to fetch Nyaa page 1" in str(exc), str(exc)
                assert "HTTP 503" in str(exc), str(exc)
            else:
                assert False, "persistent page-1 failure must raise ProviderFetchError"

    # Pages > 1: raise_on_fetch_error=False keeps partial results but sets the reason.
    fetch = _FetchPlan({1: [(200, None, None)], 2: [(500, None, None)]})
    with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=fetch):
        with patch("app.providers.nyaa.time.sleep", side_effect=lambda _s: None):
            partial = provider.crawl_pages(max_pages=3, raise_on_fetch_error=False)
    assert len(partial) == 1, partial
    assert provider.last_crawl_reason.startswith("failed on page 2"), provider.last_crawl_reason
    print("    [PASSED] 404/empty end normally; persistent failures raise (or keep partials when allowed).")


def main():
    print("=" * 78)
    print("Running backfill pagination/retry suite (task-19)")
    print("=" * 78)
    tests = [
        test_429_then_200_continues,
        test_persistent_500_fails_backfill,
        test_404_ends_normally,
        test_empty_page_ends_normally,
        test_user_stop_reports_reason,
        test_page_budget_respected,
        test_minimum_page_interval,
        test_provider_stop_reasons_and_hard_failure,
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
        print(f"BACKFILL SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL BACKFILL TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
