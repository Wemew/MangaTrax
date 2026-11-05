#!/usr/bin/env python3
"""Auto-download diagnostics: last-poll detail and the "why found 0" hint.

The card previously showed only counters (and read fields that never existed for
new/transitions), so "Last poll: 0 found" gave no clue whether the feed failed, had
no items, or everything was filtered. The payload now carries the poll timestamp,
result message/errors and an explicit hint; the poll result also tracks new/transition
counts.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "tests"))

import e2e.harness as H  # noqa: E402 - installs FastAPI/Jinja shims + temp DATA_DIR
from app import main as M  # noqa: E402


def _seed_poll_result(result, finished_at="2026-01-02T03:04:05", running=False):
    with M.scheduler._feed_check_state_lock:
        M.scheduler._feed_check_state.update({
            "running": running,
            "started_at": finished_at,
            "finished_at": finished_at if not running else None,
            "result": result,
        })


def _diagnostics():
    return M.scheduler.get_auto_download_diagnostics()


def test_found_zero_hint_without_errors():
    _seed_poll_result({
        "success": True, "message": "Feeds checked: 0 releases found, 0 requested, 0 sent.",
        "releases_found": 0, "new_releases": 0, "transitions": 0, "feeds_failed": 0, "errors": [],
    })
    data = _diagnostics()
    assert data["last_poll_at"] == "2026-01-02T03:04:05", data["last_poll_at"]
    assert data["last_poll_success"] is True, data["last_poll_success"]
    assert "no parseable manga releases" in data["last_poll_hint"], data["last_poll_hint"]
    assert "activity log" in data["last_poll_hint"], data["last_poll_hint"]
    assert data["last_poll_message"], "poll message missing"
    print("    [PASSED] found=0 without errors explains empty/filtered feed and where to look.")


def test_found_zero_hint_with_feed_failures():
    _seed_poll_result({
        "success": False,
        "message": "Feeds checked: 0 releases found... 1 feed(s) failed: Nyaa: HTTP 429",
        "releases_found": 0, "new_releases": 0, "transitions": 0,
        "feeds_failed": 1, "errors": [{"feed": "Nyaa", "error": "HTTP 429"}],
    })
    data = _diagnostics()
    assert data["last_poll_success"] is False, data["last_poll_success"]
    assert "failed for 1 feed" in data["last_poll_hint"], data["last_poll_hint"]
    assert "HTTP 429" in data["last_poll_hint"], data["last_poll_hint"]
    assert data["last_poll_errors"] == [{"feed": "Nyaa", "error": "HTTP 429"}], data["last_poll_errors"]
    print("    [PASSED] failed poll surfaces the provider error in the hint.")


def test_found_hint_cleared_when_poll_has_results():
    _seed_poll_result({
        "success": True, "message": "Feeds checked: 7 releases found (3 new, 1 transitions), 2 requested, 1 sent.",
        "releases_found": 7, "new_releases": 3, "transitions": 1,
        "feeds_failed": 0, "errors": [],
    })
    data = _diagnostics()
    assert data["last_poll_hint"] == "", data["last_poll_hint"]
    assert data["last_poll"]["new_releases"] == 3, data["last_poll"]
    assert data["last_poll"]["transitions"] == 1, data["last_poll"]
    print("    [PASSED] found>0 shows real new/transition counts and clears the hint.")


def test_no_result_and_running_states():
    _seed_poll_result(None, finished_at=None)
    assert "No feed poll result" in _diagnostics()["last_poll_hint"], _diagnostics()["last_poll_hint"]
    _seed_poll_result(None, finished_at=None, running=True)
    data = _diagnostics()
    assert data["feed_poll_running"] is True, data
    assert "running now" in data["last_poll_hint"], data["last_poll_hint"]
    print("    [PASSED] no-result and running states explain themselves.")


def test_payload_carries_new_and_transition_counts():
    payload = M.scheduler._feed_check_payload(
        True, "ok", releases_found=5, new_releases=2, transitions=1,
        requested=3, sent_to_downloader=1,
    )
    assert payload["new_releases"] == 2 and payload["transitions"] == 1, payload
    print("    [PASSED] feed-poll payload exposes new/transition counters (previously bogus zeros).")


def test_ui_renders_poll_detail_and_hint():
    downloads = open(os.path.join(BASE_DIR, "backend", "app", "templates", "downloads.html"), encoding="utf-8").read()
    for marker in ("last_poll_hint", "Poll result:", "last_poll_at", "new_releases", "transitions"):
        assert marker in downloads, f"{marker} missing from diagnostics UI"
    print("    [PASSED] diagnostics card renders poll result, timestamp and hint.")


def run():
    test_found_zero_hint_without_errors()
    test_found_zero_hint_with_feed_failures()
    test_found_hint_cleared_when_poll_has_results()
    test_no_result_and_running_states()
    test_payload_carries_new_and_transition_counts()
    test_ui_renders_poll_detail_and_hint()
    print("ALL AUTO-DOWNLOAD DIAGNOSTICS TESTS PASSED")


if __name__ == "__main__":
    run()
