#!/usr/bin/env python3
"""task-60 suite: feed-scan and processed-series webhook notifications."""

import json
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
from app.notifier.webhook import NotificationService  # noqa: E402


def test_payload_shapes():
    print("--> Testing feed-scan/processed Discord+generic payload shapes...")
    service = NotificationService(discord_webhook="https://example.com/discord", generic_webhook="https://example.com/generic")
    calls = []
    with patch("app.notifier.webhook._post_json", side_effect=lambda url, payload, timeout=10.0: calls.append((url, payload))):
        service.notify_feed_scan({
            "success": True, "releases_found": 7, "requested": 2, "sent_to_downloader": 1,
            "new_releases": 1, "transitions": 1, "feeds_failed": 1,
            "errors": [{"feed": "Nyaa", "error": "HTTP 500"}],
        }, trigger="auto", finished_at="2026-01-01 00:00:00 UTC")
        service.notify_download_processed({
            "series_name": "Processed Series", "volumes": [1, 2], "files": ["a.cbz", "b.cbz"],
            "destination": "/staging", "release_id": 42, "title": "Processed Series v01-02",
        })
    assert len(calls) == 4, calls
    discord = [payload for url, payload in calls if "discord" in url]
    generic = [payload for url, payload in calls if "generic" in url]
    assert "Feed scan complete" in discord[0]["embeds"][0]["title"], discord[0]
    assert any(field["name"] == "First errors" for field in discord[0]["embeds"][0]["fields"])
    assert "Downloaded & processed" in discord[1]["embeds"][0]["title"], discord[1]
    assert generic[0]["event"] == "FEED_SCAN" and generic[0]["trigger"] == "auto", generic[0]
    assert generic[0]["scan"]["releases_found"] == 7, generic[0]
    assert generic[1]["event"] == "DOWNLOAD_PROCESSED", generic[1]
    assert generic[1]["processed"]["volumes"] == [1, 2], generic[1]
    # Delivery failures never raise.
    with patch("app.notifier.webhook._post_json", side_effect=RuntimeError("boom")):
        service.notify_feed_scan({"releases_found": 0}, trigger="auto")
        service.notify_download_processed({"series_name": "X", "volumes": [1], "files": []})
    print("    [PASSED] Discord embeds/generic FEED_SCAN+DOWNLOAD_PROCESSED payloads and delivery-failure containment.")


def test_auto_vs_manual_and_processed_grouping():
    print("--> Testing auto-only feed notify + processed grouping/settings...")
    ctx = E2ETestContext(prefix="mangatrax_t60_sched_")
    try:
        notifier = MagicMock()
        payload = {"success": True, "releases_found": 2, "requested": 1, "sent_to_downloader": 1}
        with patch.object(ctx.scheduler, "get_notifier", return_value=notifier), \
             patch.object(ctx.scheduler, "_check_all_feeds_locked", return_value=payload):
            ctx.scheduler.check_all_feeds(trigger="manual")
            notifier.notify_feed_scan.assert_not_called()
            ctx.scheduler.check_all_feeds(trigger="auto")
        notifier.notify_feed_scan.assert_called_once()
        assert notifier.notify_feed_scan.call_args[1].get("trigger") == "auto", notifier.notify_feed_scan.call_args
        ctx.db.set_setting("webhook_feed_scan_notifications", "false")
        notifier2 = MagicMock()
        with patch.object(ctx.scheduler, "get_notifier", return_value=notifier2), \
             patch.object(ctx.scheduler, "_check_all_feeds_locked", return_value=payload):
            ctx.scheduler.check_all_feeds(trigger="auto")
        notifier2.notify_feed_scan.assert_not_called()

        # Processed grouping: one notification per series+volumes group, files/destination.
        notifier3 = MagicMock()
        result = {"processed": [
            {"series_name": "Group Series", "volumes": [1, 2], "filename": "a.cbz", "destination": "/staging/a.cbz"},
            {"series_name": "Group Series", "volumes": [1, 2], "filename": "b.cbz", "destination": "/staging/b.cbz"},
            {"series_name": "Other Series", "volumes": [3], "filename": "c.cbz", "destination": "/staging/c.cbz"},
        ], "tagged_and_moved": 3}
        with patch.object(ctx.scheduler, "get_notifier", return_value=notifier3):
            ctx.scheduler.notify_processed_batches(result)
        assert notifier3.notify_download_processed.call_count == 2, notifier3.call_args_list
        first = notifier3.notify_download_processed.call_args_list[0][0][0]
        assert first["series_name"] == "Group Series" and first["volumes"] == [1, 2], first
        assert len(first["files"]) == 2, first
        ctx.db.set_setting("webhook_processed_notifications", "false")
        notifier4 = MagicMock()
        with patch.object(ctx.scheduler, "get_notifier", return_value=notifier4):
            ctx.scheduler.notify_processed_batches(result)
        notifier4.notify_download_processed.assert_not_called()
        notifier5 = MagicMock()
        with patch.object(ctx.scheduler, "get_notifier", return_value=notifier5):
            ctx.scheduler.notify_processed_batches({"processed": []})
        notifier5.notify_download_processed.assert_not_called()
        print("    [PASSED] manual silent, auto notifies, settings disable, grouping by series+volumes, no-op when nothing moved.")
    finally:
        ctx.close()


def test_settings_and_pipeline_wiring():
    print("--> Testing settings/schema/UI and pipeline processed wiring...")
    ctx = E2ETestContext(prefix="mangatrax_t60_settings_")
    try:
        response = ctx.client.post("/api/settings", json_data={
            "webhook_feed_scan_notifications": "false",
            "webhook_processed_notifications": "false",
        })
        assert response.status_code == 200, response.text
        assert ctx.db.get_setting("webhook_feed_scan_notifications") == "false"
        assert ctx.db.get_setting("webhook_processed_notifications") == "false"
        settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
        for needle in ("setting-webhook-feed-scan", "setting-webhook-processed",
                       "Notify on automatic feed scans", "Notify when a downloaded series is processed"):
            assert needle in settings, needle
        pipeline = open(os.path.join(BACKEND_DIR, "app", "tagger", "pipeline.py"), encoding="utf-8").read()
        assert '"processed": []' in pipeline and '"destination": dest_path' in pipeline
        scheduler = open(os.path.join(BACKEND_DIR, "app", "scheduler.py"), encoding="utf-8").read()
        assert "notify_processed_batches" in scheduler and "notify_feed_scan" in scheduler
        main = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
        assert "scheduler.notify_processed_batches(route_result)" in main
        print("    [PASSED] settings save/defaults/UI, pipeline processed entries, scheduler/main notification wiring.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running webhook scan/processed notifications suite (task-60)")
    print("=" * 78)
    tests = [test_payload_shapes, test_auto_vs_manual_and_processed_grouping, test_settings_and_pipeline_wiring]
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
        print(f"WEBHOOK NOTIFICATIONS SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL WEBHOOK NOTIFICATION TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
