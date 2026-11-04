#!/usr/bin/env python3
"""task-41 suite: maintenance purge of cached release rows + preview + UI.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import sys
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from test_torbox_downloader import Ctx  # noqa: E402


def _insert(ctx, guid, series, status, title=None):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, ?);",
            (guid, title or f"{series} v01", series, status),
        )
        conn.commit()
        return cur.lastrowid


def _insert_with_download_status(ctx, guid, series, status, download_status, title=None):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, download_status) "
            "VALUES (?, ?, ?, ?, ?);",
            (guid, title or f"{series} v01", series, status, download_status),
        )
        conn.commit()
        return cur.lastrowid


def _release_count(ctx):
    with ctx.db.get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM releases;").fetchone()[0])


# ===========================================================================
# 1. Purge all: active rows skipped, everything else untouched
# ===========================================================================
def test_purge_all_safety():
    print("--> Testing clear_cached_releases(all) safety...")
    ctx = Ctx()
    try:
        ctx.db.track_discovered_series("Series A")
        _insert(ctx, "a1", "Series A", "DISCOVERY")
        _insert(ctx, "a2", "Series A", "NEW")
        _insert(ctx, "b1", "Series B", "REQUESTED")
        _insert(ctx, "a3", "Series A", "DOWNLOADING")
        _insert(ctx, "b2", "Series B", "QUEUED")
        tracked_before = len(ctx.db.get_all_series() or [])
        activity_before = len(ctx.db.get_logs(limit=200) or [])
        with patch.object(ctx.db, "_invalidate_grouped_cache") as invalidate:
            result = ctx.db.clear_cached_releases(actor="tester")
        invalidate.assert_called_once()
        assert result["deleted"] == 3, result
        assert result["skipped_active"] == 2, result
        assert result["series_name"] is None and result["statuses"] is None, result
        assert _release_count(ctx) == 2, _release_count(ctx)
        with ctx.db.get_connection() as conn:
            active = {row["guid"] for row in conn.execute("SELECT guid FROM releases;").fetchall()}
        assert active == {"a3", "b2"}, active
        assert len(ctx.db.get_all_series() or []) == tracked_before, "series_tracking must be untouched"
        events = [row for row in ctx.db.get_logs(limit=50) if row["event_type"] == "RELEASES_CLEARED"]
        assert len(events) == 1, events
        assert events[0]["actor"] == "tester", events[0]
        assert len(ctx.db.get_logs(limit=200) or []) >= activity_before, "activity log must not be deleted"
        print("    [PASSED] purge deletes only cached rows, skips active, preserves tracking/activity, audits.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Per-series and status filters
# ===========================================================================
def test_filters():
    print("--> Testing per-series and status filters...")
    ctx = Ctx()
    try:
        _insert(ctx, "a1", "Series A", "DISCOVERY")
        _insert(ctx, "a2", "Series A", "REQUESTED")
        _insert(ctx, "b1", "Series B", "DISCOVERY")
        _insert(ctx, "b2", "Series B", "REQUESTED")
        assert ctx.db.count_cached_releases("Series A")["count"] == 2
        result = ctx.db.clear_cached_releases("Series A", actor="tester")
        assert result["deleted"] == 2, result
        with ctx.db.get_connection() as conn:
            remaining = {row["guid"] for row in conn.execute("SELECT guid FROM releases;").fetchall()}
        assert remaining == {"b1", "b2"}, remaining

        result = ctx.db.clear_cached_releases(statuses=["DISCOVERY"], actor="tester")
        assert result["deleted"] == 1, result
        assert ctx.db.count_cached_releases()["count"] == 1
        result = ctx.db.clear_cached_releases(statuses=["REQUESTED"], actor="tester")
        assert result["deleted"] == 1, result
        print("    [PASSED] series filter and status filters delete exactly the matching cached rows.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Exclude filter (Except IN_CALIBRE) + preview
# ===========================================================================
def test_exclude_filter_and_preview():
    print("--> Testing exclude-status filter + preview count...")
    ctx = Ctx()
    try:
        _insert(ctx, "a1", "Series A", "DISCOVERY")
        _insert(ctx, "a2", "Series A", "IN_CALIBRE")
        _insert(ctx, "a3", "Series A", "NEW")
        preview = ctx.db.count_cached_releases("Series A", exclude_statuses=["IN_CALIBRE"])
        assert preview["count"] == 2, preview
        result = ctx.db.clear_cached_releases("Series A", exclude_statuses=["IN_CALIBRE"], actor="tester")
        assert result["deleted"] == 2, result
        assert ctx.db.count_cached_releases("Series A")["count"] == 1
        print("    [PASSED] exclude-status filter preserves IN_CALIBRE rows and preview matches.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Endpoints: confirm required, counts, audit
# ===========================================================================
def test_endpoint_confirm_and_counts():
    print("--> Testing clear-releases endpoint confirm/preview...")
    ctx = E2ETestContext(prefix="mangatrax_release_cache_")
    try:
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES "
                "('x1', 'X v01', 'X', 'DISCOVERY'), ('x2', 'X v02', 'X', 'DOWNLOADING');"
            )
            conn.commit()
        preview = ctx.client.get("/api/maintenance/releases-cache")
        assert preview.status_code == 200 and preview.json()["count"] == 1, preview.text
        rejected = ctx.client.post("/api/maintenance/clear-releases", json_data={})
        assert rejected.status_code == 400, rejected.status_code
        assert "confirm" in rejected.json()["detail"].lower(), rejected.json()
        cleared = ctx.client.post("/api/maintenance/clear-releases", json_data={"confirm": True})
        assert cleared.status_code == 200, cleared.text
        body = cleared.json()
        assert body["deleted"] == 1 and body["skipped_active"] == 1, body
        events = [row for row in ctx.db.get_logs(limit=50) if row["event_type"] == "RELEASES_CLEARED"]
        assert len(events) == 1, events
        template = open(
            os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8",
        ).read()
        for needle in ("release-cache-card", "clearReleaseCache", "updateReleaseCacheCount",
                       "Clear cached releases", "/api/maintenance/clear-releases",
                       "release-cache-statuses"):
            assert needle in template, f"missing release-cache UI wiring: {needle}"
        print("    [PASSED] endpoint requires confirm, returns deleted/skipped counts, audits, UI wired.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Active guard uses download_status (authoritative), not release status
# ===========================================================================
def test_active_guard_uses_download_status():
    print("--> Testing download_status active guard (status=REQUESTED)...")
    ctx = Ctx()
    try:
        _insert(ctx, "cached1", "Series A", "REQUESTED")
        _insert(ctx, "cached2", "Series A", "DISCOVERY")
        active_id = _insert_with_download_status(
            ctx, "active1", "Series A", "REQUESTED", "DOWNLOADING",
        )
        queued_id = _insert_with_download_status(
            ctx, "active2", "Series A", "NEW", "QUEUED",
        )
        preview = ctx.db.count_cached_releases()
        assert preview["count"] == 2, preview
        with patch.object(ctx.db, "_invalidate_grouped_cache") as invalidate:
            result = ctx.db.clear_cached_releases(actor="tester")
        invalidate.assert_called_once()
        assert result["deleted"] == 2, result
        assert result["skipped_active"] == 2, result
        with ctx.db.get_connection() as conn:
            remaining = {row["guid"] for row in conn.execute("SELECT guid FROM releases;").fetchall()}
        assert remaining == {"active1", "active2"}, remaining
        assert active_id and queued_id  # rows still present (engine owns them)

        # Once the downloader releases ownership, the rows become eligible.
        with ctx.db.get_connection() as conn:
            conn.execute("UPDATE releases SET download_status = 'FAILED' WHERE guid = 'active1';")
            conn.execute("UPDATE releases SET download_status = 'IDLE' WHERE guid = 'active2';")
            conn.commit()
        second = ctx.db.clear_cached_releases(actor="tester")
        assert second["deleted"] == 2 and second["skipped_active"] == 0, second
        assert _release_count(ctx) == 0, _release_count(ctx)
        print("    [PASSED] download_status DOWNLOADING/QUEUED rows excluded/reported/preserved, then eligible after reset.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Second run purge: empty (idempotent), active remains untouched
# ===========================================================================
def test_second_purge_noop():
    print("--> Testing second purge is a no-op...")
    ctx = Ctx()
    try:
        _insert(ctx, "a1", "Series A", "DISCOVERY")
        first = ctx.db.clear_cached_releases(actor="tester")
        second = ctx.db.clear_cached_releases(actor="tester")
        assert first["deleted"] == 1, first
        assert second["deleted"] == 0 and second["skipped_active"] == 0, second
        print("    [PASSED] repeated purge deletes nothing (idempotent).")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running release cache purge suite (task-41)")
    print("=" * 78)
    tests = [
        test_purge_all_safety,
        test_filters,
        test_exclude_filter_and_preview,
        test_endpoint_confirm_and_counts,
        test_active_guard_uses_download_status,
        test_second_purge_noop,
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
        print(f"RELEASE CACHE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL RELEASE CACHE PURGE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
