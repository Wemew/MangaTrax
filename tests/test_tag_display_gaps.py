#!/usr/bin/env python3
"""task-51 suite: series/dashboard tag chips + known-tag universe union."""

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
from app.tag_display import display_tags  # noqa: E402


def test_known_tags_union():
    print("--> Testing known-tag union across cache/releases/series...")
    ctx = E2ETestContext(prefix="mangatrax_t51_union_")
    try:
        # No metadata cache rows: only denormalised tables.
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, tags_json, tags_text) "
                "VALUES ('t51-1', 'Union v01', 'Union', 'DISCOVERY', ?, ?);",
                (json.dumps([{"name": "Action", "category": "Genre", "rank": 100000, "spoiler": False},
                             {"name": "Comedy", "category": "Theme", "rank": 80, "spoiler": False}]),
                 "|action|comedy|"),
            )
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, tags_json, tags_text) "
                "VALUES ('t51-2', 'Union v02', 'Union', 'DISCOVERY', ?, ?);",
                (json.dumps([{"name": "Comedy", "category": "Theme", "rank": 50, "spoiler": False},
                             {"name": "Spoiler Tag", "category": "Theme", "rank": 999, "spoiler": True}]),
                 "|comedy|spoiler tag|"),
            )
            conn.execute(
                "INSERT INTO series_tracking (name, is_monitored, tags_json, tags_text) VALUES "
                "('Union Series', 1, ?, ?);",
                (json.dumps([{"name": "Action", "category": "Genre", "rank": 100000, "spoiler": False}]),
                 "|action|"),
            )
            conn.commit()
        tags = ctx.db.get_known_tags("", 20)
        by_name = {item["name"].lower(): item for item in tags}
        assert "action" in by_name and "comedy" in by_name, tags
        assert "spoiler tag" not in by_name, tags
        assert by_name["action"]["category"] == "Genre", by_name["action"]
        assert by_name["comedy"]["count"] == 2, by_name["comedy"]
        exact = ctx.db.get_known_tags("action", 5)
        assert exact and exact[0]["name"] == "Action", exact
        assert len(ctx.db.get_known_tags("", 1)) == 1
        print("    [PASSED] union counts/dedupe, genre preference, spoiler exclusion, exact-first ordering and limit.")
    finally:
        ctx.close()


def test_view_markup():
    print("--> Testing series/dashboard chip markup + policy...")
    row = open(os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_table_row_chunk.html"), encoding="utf-8").read()
    index = open(os.path.join(BACKEND_DIR, "app", "templates", "index.html"), encoding="utf-8").read()
    for needle in ("tag_display(s.tags_json, view='series')", "tag_info.chips", "tag_info.overflow", "tag_info.tooltip"):
        assert needle in row, f"missing series chip wiring: {needle}"
    for needle in ("tag_display(r.tags_json, view='dashboard')", "tag_info.chips", "tag_info.overflow"):
        assert needle in index, f"missing dashboard chip wiring: {needle}"
    tags = [{"name": f"Tag {index}", "category": "Theme", "rank": 100 - index, "spoiler": False} for index in range(6)]
    assert len(display_tags(tags, view="series")["chips"]) == 4
    assert display_tags(tags, view="series")["overflow"] == 2
    assert len(display_tags(tags, view="dashboard")["chips"]) == 3
    assert display_tags([], view="series")["chips"] == []
    readme = open(os.path.join(BASE_DIR, "README.md"), encoding="utf-8").read()
    assert "union of the metadata cache, release tags and" in readme
    print("    [PASSED] series=4/dashboard=3 chips, overflow/tooltip wiring, empty state, README sources.")


def main():
    print("=" * 78)
    print("Running tag display gaps suite (task-51)")
    print("=" * 78)
    tests = [test_known_tags_union, test_view_markup]
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
        print(f"TAG GAPS SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TAG GAP TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
