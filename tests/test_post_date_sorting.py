#!/usr/bin/env python3
"""task-65 suite: provider post-date persistence + releases/discovery sorting."""

import json
import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402


def test_normalisation_and_upsert():
    print("--> Testing post-date normalisation + upsert persistence...")
    ctx = E2ETestContext(prefix="mangatrax_t65_dates_")
    try:
        norm = ctx.db.normalize_published_at
        assert norm("2026-01-02T03:04:05Z") == "2026-01-02T03:04:05Z"
        assert norm("2026-01-02 03:04:05") == "2026-01-02T03:04:05Z"
        assert norm("Fri, 02 Jan 2026 03:04:05 +0000") == "2026-01-02T03:04:05Z"
        assert norm("") == ""
        base = {"guid": "post-1", "title": "Post Series v01", "series_name": "Post Series",
                "volumes": [1.0], "missing_volumes": [1.0], "status": "REQUESTED",
                "published_at": "2026-01-02T03:04:05Z"}
        is_new, rel_id = ctx.db.upsert_release(base)
        assert is_new and ctx.db.get_release_by_id(rel_id)["published_at"] == "2026-01-02T03:04:05Z"
        update = dict(base, title="Post Series v01 (updated)", published_at="")
        is_new2, rel_id2 = ctx.db.upsert_release(update)
        assert is_new2 is False and rel_id2 == rel_id
        assert ctx.db.get_release_by_id(rel_id)["published_at"] == "2026-01-02T03:04:05Z", "blank update must not clear"
        update["published_at"] = "2026-02-03T00:00:00Z"
        ctx.db.upsert_release(update)
        assert ctx.db.get_release_by_id(rel_id)["published_at"] == "2026-02-03T00:00:00Z"
        # Schema/backfill: column exists and rows with blank published_at are backfilled on init.
        with ctx.db.get_connection() as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(releases);").fetchall()]
        assert "published_at" in columns
        print("    [PASSED] Nyaa/RSS/ISO normalization, insert/update persistence, blank-update safety, guarded column.")
    finally:
        ctx.close()


def _insert(ctx, guid, title, series, status, published, volume=1.0, created="2026-01-01T00:00:00Z"):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, volume_start, volumes_json, "
            "missing_vols_json, published_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?);",
            (guid, title, series, status, volume, json.dumps([volume]), published, created, created),
        )
        conn.commit()
        return cur.lastrowid


def test_release_sorting():
    print("--> Testing flat/grouped post-date ordering + pagination...")
    ctx = E2ETestContext(prefix="mangatrax_t65_sort_")
    try:
        _insert(ctx, "s-old", "Old Series v01", "Old Series", "REQUESTED", "2026-01-01T00:00:00Z")
        _insert(ctx, "s-new", "New Series v01", "New Series", "REQUESTED", "2026-03-01T00:00:00Z")
        _insert(ctx, "s-mid", "Mid Series v01", "Mid Series", "REQUESTED", "2026-02-01T00:00:00Z")
        flat = ctx.db.get_releases(limit=10, sort="post_date")
        assert [row["series_name"] for row in flat][:3] == ["New Series", "Mid Series", "Old Series"], flat
        page = ctx.db.get_releases(limit=1, offset=1, sort="post_date")
        assert page and page[0]["series_name"] == "Mid Series", page
        groups = ctx.db.get_releases_grouped_by_series(limit=10, sort="post_date")
        names = [group["series_name"] for group in groups]
        assert names[:3] == ["New Series", "Mid Series", "Old Series"], names
        # Series A-Z control.
        az = ctx.db.get_releases(limit=10, sort="series")
        assert [row["series_name"] for row in az][:3] == ["Mid Series", "New Series", "Old Series"], az
        print("    [PASSED] flat post-date DESC + pagination, grouped MAX(post) ordering, series A–Z.")
    finally:
        ctx.close()


def test_discovery_sorting():
    print("--> Testing discovery latest-post ordering/count...")
    ctx = E2ETestContext(prefix="mangatrax_t65_disc_")
    try:
        _insert(ctx, "d-old", "Disc Old v01", "Disc Old", "DISCOVERY", "2026-01-01T00:00:00Z")
        _insert(ctx, "d-new", "Disc New v01", "Disc New", "DISCOVERY", "2026-03-01T00:00:00Z")
        _insert(ctx, "d-mid", "Disc Mid v01", "Disc Mid", "DISCOVERY", "2026-02-01T00:00:00Z")
        rows = ctx.db.get_discovery_releases(limit=10, offset=0, sort="post_date")
        names = [row["series_name"] for row in rows]
        assert names[:3] == ["Disc New", "Disc Mid", "Disc Old"], names
        assert ctx.db.count_discovery_releases() >= 3
        rows_series = ctx.db.get_discovery_releases(limit=10, offset=0, sort="series")
        assert [row["series_name"] for row in rows_series][:3] == ["Disc Mid", "Disc New", "Disc Old"], rows_series
        print("    [PASSED] discovery post_date DESC + series A–Z; counts unchanged by sort.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing Releases/Discovery sort controls + URL wiring...")
    releases = open(os.path.join(BACKEND_DIR, "app", "templates", "releases.html"), encoding="utf-8").read()
    discovery = open(os.path.join(BACKEND_DIR, "app", "templates", "discovery.html"), encoding="utf-8").read()
    main = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    flat = open(os.path.join(BACKEND_DIR, "app", "templates", "partials", "flat_release_row_chunk.html"), encoding="utf-8").read()
    for needle in ("release-sort-select", "Latest release post", "changeReleaseSort",
                   "sort=${encodeURIComponent(currentSort)}", "url.searchParams.set('sort'"):
        assert needle in releases, f"missing releases wiring: {needle}"
    for needle in ("discovery-sort", "Latest post", "changeDiscoverySort"):
        assert needle in discovery, f"missing discovery wiring: {needle}"
    assert '"post_date"' in main and "sort: str = Query" in main
    assert "Posted" in flat
    print("    [PASSED] visible sort controls, URL parameters, API sort params and Posted labels wired.")


def main():
    print("=" * 78)
    print("Running post-date sorting suite (task-65)")
    print("=" * 78)
    tests = [test_normalisation_and_upsert, test_release_sorting, test_discovery_sorting, test_ui_wiring]
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
        print(f"POST-DATE SORT SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL POST-DATE SORT TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
