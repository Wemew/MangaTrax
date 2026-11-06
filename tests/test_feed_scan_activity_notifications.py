#!/usr/bin/env python3
"""task-68 suite: automatic feed-scan webhook only on actual activity."""

import os
import sys
import traceback
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402


def test_suppression_and_activity():
    print("--> Testing zero-activity suppression + each activity kind sends once...")
    ctx = E2ETestContext(prefix="mangatrax_t68_notify_")
    try:
        notifier = MagicMock()
        quiet = [
            {"success": True, "releases_found": 0, "new_releases": 0, "transitions": 0,
             "requested": 0, "sent_to_downloader": 0},
            # Known releases only: found > 0 must still be silent.
            {"success": True, "releases_found": 7, "new_releases": 0, "transitions": 0,
             "requested": 0, "sent_to_downloader": 0},
            # Provider errors with no entries.
            {"success": False, "releases_found": 0, "feeds_failed": 2, "errors": [{"feed": "Nyaa", "error": "HTTP 500"}],
             "new_releases": 0, "transitions": 0, "requested": 0, "sent_to_downloader": 0},
            # Skipped concurrent poll.
            {"success": True, "skipped": True, "releases_found": 0},
        ]
        for payload in quiet:
            notifier.reset_mock()
            with patch.object(ctx.scheduler, "get_notifier", return_value=notifier), \
                 patch.object(ctx.scheduler, "_check_all_feeds_locked", return_value=payload):
                ctx.scheduler.check_all_feeds(trigger="auto")
            notifier.notify_feed_scan.assert_not_called()
        active = [
            {"new_releases": 1},
            {"transitions": 2},
            {"requested": 1},
            {"sent_to_downloader": 1},
            {"new_releases": 0, "transitions": 0, "requested": 0, "sent_to_downloader": 0,
             "processed": [{"filename": "a.cbz"}]},
            # Mixed activity still sends exactly once with the original payload.
            {"releases_found": 3, "new_releases": 1, "transitions": 1, "requested": 1,
             "sent_to_downloader": 1, "feeds_failed": 0},
        ]
        for payload in active:
            notifier.reset_mock()
            with patch.object(ctx.scheduler, "get_notifier", return_value=notifier), \
                 patch.object(ctx.scheduler, "_check_all_feeds_locked", return_value=payload):
                ctx.scheduler.check_all_feeds(trigger="auto")
            notifier.notify_feed_scan.assert_called_once()
            assert notifier.notify_feed_scan.call_args[0][0] == payload, notifier.notify_feed_scan.call_args
            assert notifier.notify_feed_scan.call_args[1].get("trigger") == "auto"
        # Setting off and manual trigger stay silent.
        ctx.db.set_setting("webhook_feed_scan_notifications", "false")
        notifier.reset_mock()
        with patch.object(ctx.scheduler, "get_notifier", return_value=notifier), \
             patch.object(ctx.scheduler, "_check_all_feeds_locked", return_value={"new_releases": 5}):
            ctx.scheduler.check_all_feeds(trigger="auto")
        notifier.notify_feed_scan.assert_not_called()
        ctx.db.set_setting("webhook_feed_scan_notifications", "true")
        notifier.reset_mock()
        with patch.object(ctx.scheduler, "get_notifier", return_value=notifier), \
             patch.object(ctx.scheduler, "_check_all_feeds_locked", return_value={"new_releases": 5}):
            ctx.scheduler.check_all_feeds(trigger="manual")
        notifier.notify_feed_scan.assert_not_called()
        print("    [PASSED] quiet/known/error/skipped scans silent; each activity kind sends once; settings/manual respected.")
    finally:
        ctx.close()


def test_ui_hint():
    print("--> Testing settings hint reflects activity-only notifications...")
    settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    assert "only when an automatic (scheduled) feed poll actually finds new releases" in settings
    assert "Empty/no-change scans" in settings and "Manual Poll Feeds stays silent" in settings
    scheduler = open(os.path.join(BACKEND_DIR, "app", "scheduler.py"), encoding="utf-8").read()
    assert "_feed_scan_has_activity" in scheduler and "Suppressed automatic feed-scan notification" in scheduler
    print("    [PASSED] hint explains activity gate; scheduler suppression debug log present.")


def main():
    print("=" * 78)
    print("Running feed-scan activity notification suite (task-68)")
    print("=" * 78)
    tests = [test_suppression_and_activity, test_ui_hint]
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
        print(f"FEED-SCAN NOTIFICATION SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL FEED-SCAN NOTIFICATION TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
