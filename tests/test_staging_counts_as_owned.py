#!/usr/bin/env python3
"""task-61 suite: staged volumes count as owned (matcher/status/discovery/state)."""

import json
import os
import sys
import traceback
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app.calibre.matcher import MangaCalibreMatcher  # noqa: E402
from app.database import resolve_status  # noqa: E402
from app.staged_ownership import (  # noqa: E402
    build_staged_volumes_map,
    get_staged_volumes_map,
    invalidate_staged_ownership_cache,
)


def _write(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("page.jpg", b"image")


def _matcher(staged=None):
    calibre = [{"name": "Owned Series", "series_name": "Owned Series", "book_count": 2,
                "owned_volumes_json": "[1,2]"}]
    return MangaCalibreMatcher(calibre, {"owned series": {1.0, 2.0}}, staged_volumes_map=staged or {})


def test_matcher_matrix():
    print("--> Testing matcher Calibre∪staged ownership matrix...")
    matcher = _matcher({"owned series": {3.0}})
    cases = [
        ([1, 2], "IN_CALIBRE"),
        ([3], "IN_STAGING"),
        ([1, 3], "IN_STAGING"),
        ([2, 3], "IN_STAGING"),
        ([1, 4], "REQUESTED"),
        ([1, 2, 4], "REQUESTED"),
    ]
    for volumes, expected in cases:
        result = matcher.check_release({"series_name": "Owned Series", "volumes": volumes, "title": "x"})
        assert result["status"] == expected, (volumes, result)
    partial = matcher.check_release({"series_name": "Owned Series", "volumes": [1, 2, 4], "title": "x"})
    assert partial["missing_volumes"] == [4], partial
    assert matcher.get_owned_volumes({"series_name": "Owned Series"}) == {1.0, 2.0}
    assert matcher.get_combined_owned_volumes({"series_name": "Owned Series"}) == {1.0, 2.0, 3.0}
    from app.calibre.matcher import MangaCalibreMatcher as M
    plain = M([{"name": "Other", "series_name": "Other"}], {}, staged_volumes_map={"other": {2.0}})
    r = plain.check_release({"series_name": "Other", "volumes": [2], "title": "x"})
    assert r["status"] == "IN_STAGING", r
    print("    [PASSED] Calibre-only → IN_CALIBRE, staged-only → IN_STAGING, mixed union correct, partial wanted excludes staged.")


def test_staged_map_scan_fallback_cache():
    print("--> Testing staged map scan/nested/aliases/fallback/temp filtering/cache...")
    ctx = E2ETestContext(prefix="mangatrax_t61_map_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(os.path.join(staging, "nested"), exist_ok=True)
        _write(os.path.join(staging, "Map Series v01.cbz"))
        _write(os.path.join(staging, "nested", "Map Series v02.cbz"))
        _write(os.path.join(staging, "Map Series v03.cbz.part"))
        _write(os.path.join(staging, ".hidden v04.cbz"))
        # DB fallback for an unparseable staged name.
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, download_status, routed_at, "
                "download_wanted_json, volumes_json) VALUES ('map-fb', 'Fallback', 'Fallback Series', "
                "'REQUESTED', 'COMPLETED', '2026-01-01', '[7]', '[9]');"
            )
            conn.commit()
        mapping = build_staged_volumes_map(ctx.db, staging)
        assert mapping.get("map series") == {1.0, 2.0}, mapping
        assert "map series" in mapping and 3.0 not in mapping["map series"], mapping
        assert mapping.get("fallback series") == {7.0}, mapping
        assert get_staged_volumes_map(ctx.db, staging) == mapping
        os.remove(os.path.join(staging, "Map Series v01.cbz"))
        invalidate_staged_ownership_cache()
        refreshed = get_staged_volumes_map(ctx.db, staging)
        assert refreshed.get("map series") == {2.0}, refreshed
        print("    [PASSED] file scan nested/aliases, temp/hidden skipped, DB fallback used, cache invalidated after deletion.")
    finally:
        ctx.close()


def test_status_precedence_and_state():
    print("--> Testing IN_STAGING precedence/transition/terminal state/discovery...")
    assert resolve_status("REQUESTED", "IN_STAGING") == "IN_STAGING"
    assert resolve_status("IN_STAGING", "IN_CALIBRE") == "IN_CALIBRE"
    assert resolve_status("IN_STAGING", "REQUESTED") == "IN_STAGING"
    assert resolve_status("IN_STAGING", "DISCOVERY") == "IN_STAGING"
    assert resolve_status("IN_CALIBRE", "IN_STAGING") == "IN_CALIBRE"
    from app.main import release_download_state
    state = release_download_state({"id": 1, "status": "IN_STAGING", "download_status": "IDLE"})
    assert state["state"] == "in_staging" and state["terminal"] is True, state
    assert state["can_download"] is False and state["can_retry"] is False, state
    ctx = E2ETestContext(prefix="mangatrax_t61_status_")
    try:
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volume_start, volumes_json) "
                "VALUES ('staged-disc', 'Staged Series v01', 'Staged Series', 'IN_STAGING', 1.0, '[1]');"
            )
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volume_start, volumes_json) "
                "VALUES ('wanted-disc', 'Wanted Series v01', 'Wanted Series', 'REQUESTED', 1.0, '[1]');"
            )
            conn.commit()
        rows = ctx.db.get_discovery_releases(limit=10, offset=0)
        names = {row["series_name"] for row in rows}
        assert "Staged Series" not in names, names
        assert "Wanted Series" in names, names
        assert ctx.db.count_discovery_releases() == len([row for row in rows if row["series_name"] == "Wanted Series"])
        # Manual download guard 409 for IN_STAGING.
        with ctx.db.get_connection() as conn:
            cur = conn.execute("SELECT id FROM releases WHERE guid = 'staged-disc';")
            staged_id = cur.fetchone()["id"]
        response = ctx.client.post(f"/api/releases/{staged_id}/download")
        assert response.status_code == 409, response.text
        print("    [PASSED] sticky IN_STAGING, upgrade to IN_CALIBRE, no downgrade, terminal state, discovery excluded, manual retry blocked.")
    finally:
        ctx.close()


def test_ui_and_wiring():
    print("--> Testing staging-aware matcher wiring + badges/docs...")
    scheduler = open(os.path.join(BACKEND_DIR, "app", "scheduler.py"), encoding="utf-8").read()
    assert "staged_volumes_map=self._staged_volumes_map()" in scheduler
    assert scheduler.count("staged_volumes_map=self._staged_volumes_map()") >= 5, "all matcher constructions must be staging-aware"
    matcher = open(os.path.join(BACKEND_DIR, "app", "calibre", "matcher.py"), encoding="utf-8").read()
    assert "IN_STAGING" in matcher and "get_staged_volumes" in matcher
    flat = open(os.path.join(BACKEND_DIR, "app", "templates", "partials", "flat_release_row_chunk.html"), encoding="utf-8").read()
    card = open(os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_card_chunk.html"), encoding="utf-8").read()
    assert "IN_STAGING" in flat and "In staging" in flat
    assert "IN_STAGING" in card and "In staging" in card
    print("    [PASSED] matcher wiring in scheduler, status state/badges and module symbols present.")


def main():
    print("=" * 78)
    print("Running staging-as-owned suite (task-61)")
    print("=" * 78)
    tests = [test_matcher_matrix, test_staged_map_scan_fallback_cache, test_status_precedence_and_state, test_ui_and_wiring]
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
        print(f"STAGING OWNERSHIP SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STAGING OWNERSHIP TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
