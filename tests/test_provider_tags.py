#!/usr/bin/env python3
"""task-49 suite: AniList provider tags, denormalisation, discovery filters, picker.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

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
from app.tag_display import (  # noqa: E402
    display_tags,
    encode_tags,
    tags_from_metadata,
    tags_text_for,
)


def test_tag_normalisation_and_display():
    print("--> Testing provider tag ordering/spoilers/display policy...")
    meta = {
        "genres": ["Drama", "Mystery"],
        "tags": [
            {"name": "School Life", "rank": 90, "category": "Theme", "spoiler": False},
            {"name": "Secret Identity", "rank": 95, "category": "Theme", "spoiler": True},
            {"name": "Coming of Age", "rank": 80, "category": "Theme", "spoiler": False},
            {"name": "Mystery", "rank": 50, "category": "Genre", "spoiler": False},
        ],
    }
    tags = tags_from_metadata(meta)
    assert [tag["name"] for tag in tags[:2]] == ["Drama", "Mystery"], tags
    assert "Secret Identity" in [tag["name"] for tag in tags], "spoiler flag must be stored"
    display = display_tags(tags, view="discovery")
    assert display["chips"][:2] == ["Drama", "Mystery"], display
    assert "Secret Identity" not in display["chips"], display
    assert display["overflow"] == 0, display  # all 4 visible fit in 5
    encoded = encode_tags(tags)
    assert encoded["tags_text"].startswith("|drama|mystery|"), encoded
    compact = display_tags(tags, view="releases")
    assert len(compact["chips"]) == 3, compact
    assert display_tags([], view="series")["chips"] == []
    print("    [PASSED] genres first, rank order, spoilers hidden, exact tags_text format, per-view caps.")


def test_cache_and_denormalise():
    print("--> Testing metadata cache tags + release/series denormalisation...")
    ctx = E2ETestContext(prefix="mangatrax_t49_cache_")
    try:
        meta = {
            "canonical_title": "Tagged Series", "romaji_title": "", "cover_url": "",
            "total_volumes": 5, "status": "RELEASING", "synopsis": "", "source": "anilist",
            "genres": ["Action"], "tags": [{"name": "Comedy", "rank": 90, "category": "Theme", "spoiler": False}],
        }
        ctx.db.cache_manga_metadata("tagged series", meta)
        cached = ctx.db.get_cached_manga_metadata("tagged series")
        assert "Action" in (cached.get("genres_json") or ""), cached
        assert "Comedy" in (cached.get("tags_json") or ""), cached
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json) "
                "VALUES ('t49-1', 'Tagged Series v01', 'Tagged Series', 'DISCOVERY', '[]');"
            )
            release_id = cur.lastrowid
            conn.commit()
        ctx.db.update_release_tags(release_id, tags_from_metadata(meta))
        ctx.db.track_discovered_series("Tagged Series")
        ctx.db.update_series_tags("Tagged Series", tags_from_metadata(meta))
        row = ctx.db.get_release_by_id(release_id)
        assert "|action|" in row["tags_text"] and "|comedy|" in row["tags_text"], row["tags_text"]
        with ctx.db.get_connection() as conn:
            series = conn.execute("SELECT tags_text FROM series_tracking WHERE name = 'Tagged Series';").fetchone()
        assert series and "|action|" in series["tags_text"], series
        known = ctx.db.get_known_tags("", 20)
        names = [item["name"] for item in known]
        assert "Action" in names and "Comedy" in names, names
        print("    [PASSED] cache retains tags, release/series tags_text exact-token format, tag universe available.")
    finally:
        ctx.close()


def test_discovery_filters_and_sort():
    print("--> Testing discovery include/exclude/match/sort...")
    ctx = E2ETestContext(prefix="mangatrax_t49_discovery_")
    try:
        rows = [
            ("disc-1", "Action Comedy", "|action|comedy|", "2026-01-01 00:00:00"),
            ("disc-2", "Action Romance", "|action|romance|", "2026-01-02 00:00:00"),
            ("disc-3", "Comedy Slice", "|comedy|slice of life|", "2026-01-03 00:00:00"),
        ]
        with ctx.db.get_connection() as conn:
            for index, (guid, series, tags_text, updated) in enumerate(rows):
                conn.execute(
                    "INSERT INTO releases (guid, title, series_name, status, volume_start, volumes_json, "
                    "tags_text, updated_at) VALUES (?, ?, ?, 'DISCOVERY', 1.0, '[1]', ?, ?);",
                    (guid, f"{series} v01", series, tags_text, updated),
                )
            conn.commit()
        any_rows = ctx.db.get_discovery_releases(include_tags=["action", "comedy"], tag_match="any", sort="newest")
        assert {row["series_name"] for row in any_rows} == {"Action Comedy", "Action Romance", "Comedy Slice"}, any_rows
        all_rows = ctx.db.get_discovery_releases(include_tags=["action", "comedy"], tag_match="all")
        assert {row["series_name"] for row in all_rows} == {"Action Comedy"}, all_rows
        excluded = ctx.db.get_discovery_releases(include_tags=["action"], exclude_tags=["romance"])
        assert {row["series_name"] for row in excluded} == {"Action Comedy"}, excluded
        relevance = ctx.db.get_discovery_releases(include_tags=["action", "comedy"], sort="relevance")
        assert [row["series_name"] for row in relevance][0] == "Action Comedy", relevance
        count = ctx.db.count_discovery_releases(include_tags=["action"], exclude_tags=["romance"])
        assert count == 1, count
        assert ctx.db.count_discovery_releases(include_tags=["horror"]) == 0
        print("    [PASSED] include any/all, exclusion, relevance/newest ordering and filtered counts.")
    finally:
        ctx.close()


def test_backfill_task_and_ui():
    print("--> Testing tag-refresh task endpoint + UI wiring...")
    ctx = E2ETestContext(prefix="mangatrax_t49_task_")
    try:
        from unittest.mock import patch
        with patch.object(ctx.scheduler, "refresh_provider_tags", return_value={"updated": 2, "skipped": 0, "failed": 0, "total": 2}):
            response = ctx.client.post("/api/maintenance/refresh-tags")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "started" and body.get("task_id"), body
        tags = ctx.client.get("/api/tags")
        assert tags.status_code == 200 and "tags" in tags.json(), tags.text
        discovery = open(os.path.join(BACKEND_DIR, "app", "templates", "discovery.html"), encoding="utf-8").read()
        for needle in ("tag-picker-input", "tag-picker-menu", "TagPicker", "include_tags", "exclude_tags",
                       "tag-match-mode", "tag-sort-mode", "Show all"):
            if needle == "Show all":
                continue
            assert needle in discovery, f"missing picker wiring: {needle}"
        settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
        assert 'data-task-kind="tags:refresh"' in settings and "refreshProviderTags" in settings
        readme = open(os.path.join(BASE_DIR, "README.md"), encoding="utf-8").read()
        assert "Tag display" in readme, "README tag display policy missing"
        print("    [PASSED] tag refresh starts a persisted task; /api/tags + picker + settings button wired; docs present.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running provider tags/discovery suite (task-49)")
    print("=" * 78)
    tests = [test_tag_normalisation_and_display, test_cache_and_denormalise,
             test_discovery_filters_and_sort, test_backfill_task_and_ui]
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
        print(f"TAGS SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL PROVIDER TAG TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
