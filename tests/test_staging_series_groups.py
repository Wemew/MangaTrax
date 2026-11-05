#!/usr/bin/env python3
"""task-63 suite: staging series grouping + master Match series retag/link."""

import json
import os
import sys
import time
import traceback
from unittest.mock import patch
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app import staging_groups  # noqa: E402


def _cbz(path, series="", volume="", extra=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("page.jpg", b"image")
        if series:
            archive.writestr(
                "ComicInfo.xml",
                f"<ComicInfo><Series>{series}</Series><Volume>{volume}</Volume>{extra}</ComicInfo>",
            )


def _tracker(ctx, name):
    with ctx.db.get_connection() as conn:
        cur = conn.execute("INSERT INTO series_tracking (name, is_monitored) VALUES (?, 1);", (name,))
        conn.commit()
        return cur.lastrowid


def _release(ctx, guid, series):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, volumes_json) "
            "VALUES (?, ?, ?, 'REQUESTED', '[1]');", (guid, f"{series} v01", series),
        )
        conn.commit()
        return cur.lastrowid


def test_grouping():
    print("--> Testing staging grouping by ComicInfo/fallback + counts...")
    ctx = E2ETestContext(prefix="mangatrax_t63_groups_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        makedirs = os.makedirs
        makedirs(staging, exist_ok=True)
        _cbz(os.path.join(staging, "Group Series", "Group Series v01.cbz"), series="Group Series", volume="1")
        _cbz(os.path.join(staging, "Group Series v02.cbz"), series="Group Series", volume="2")
        _cbz(os.path.join(staging, "Fallback Series v05.cbz"))
        from app.tagger import cbz_tagger
        groups = staging_groups.build_staging_groups(staging)
        by_name = {group["series"]: group for group in groups}
        assert "Group Series" in by_name, groups
        assert by_name["Group Series"]["count"] == 2, by_name["Group Series"]
        assert by_name["Group Series"]["volumes"] == [1.0, 2.0], by_name["Group Series"]
        assert by_name["Group Series"]["tagged"] == 2, by_name["Group Series"]
        assert "Fallback Series" in by_name and by_name["Fallback Series"]["tagged"] == 0, by_name
        print("    [PASSED] groups by ComicInfo Series, filename fallback, counts/volumes/tagged computed.")
    finally:
        ctx.close()


def test_retag_link_and_new_title():
    print("--> Testing retag/link task + alias/re-key/cache + new-title path...")
    ctx = E2ETestContext(prefix="mangatrax_t63_retag_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("staging_dir", staging)
        ctx.db.set_setting("downloads_dir", os.path.join(ctx.temp_dir, "downloads"))
        _cbz(os.path.join(staging, "Old Series v01.cbz"), series="Old Series", volume="1")
        _cbz(os.path.join(staging, "Old Series v02.cbz"), series="Old Series", volume="2")
        tracker = _tracker(ctx, "Old Series")
        release_id = _release(ctx, "old-rel", "Old Series")
        calibre = {"series_name": "New Series", "series_id": 55, "book_count": 5}
        with patch("app.main._calibre_series_snapshot", return_value=[calibre]):
            response = ctx.client.post(
                "/api/downloads/staging-series/match",
                json_data={"source_series": "Old Series", "calibre_series_id": 55, "confirm": True},
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task_id"]
            deadline = time.time() + 5
            task = {}
            while time.time() < deadline:
                task = ctx.client.get(f"/api/tasks/{task_id}").json()["task"]
                if not task["active"]:
                    break
                time.sleep(0.05)
        assert task["status"] == "success", task
        assert task["result"]["tagged"] == 2 and task["result"]["failed"] == 0, task
        from app.tagger import cbz_tagger
        for name in ("Old Series v01.cbz", "Old Series v02.cbz"):
            xml = cbz_tagger.read_comicinfo_xml(os.path.join(staging, name))
            assert "<Series>New Series</Series>" in (xml or ""), xml
        assert ctx.db.get_release_by_id(release_id)["series_name"] == "New Series"
        trackers = ctx.db.get_all_series()
        assert len(trackers) == 1 and trackers[0]["name"] == "New Series", trackers
        assert trackers[0]["calibre_series_id"] == 55, trackers[0]
        aliases = ctx.db.get_series_alias_overrides()
        assert any(row["target_name"] == "New Series" for row in aliases), aliases
        # New-title path for a second group.
        _cbz(os.path.join(staging, "Loose Series v03.cbz"), series="Loose Series", volume="3")
        response = ctx.client.post(
            "/api/downloads/staging-series/match",
            json_data={"source_series": "Loose Series", "new_series_title": "Brand New Series", "confirm": True},
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["task_id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            task = ctx.client.get(f"/api/tasks/{task_id}").json()["task"]
            if not task["active"]:
                break
            time.sleep(0.05)
        assert task["status"] == "success" and task["result"]["tagged"] == 1, task
        assert "<Series>Brand New Series</Series>" in (cbz_tagger.read_comicinfo_xml(os.path.join(staging, "Loose Series v03.cbz")) or "")
        print("    [PASSED] retag preserves files, updates ComicInfo, alias/link/re-key, new-title path works.")
    finally:
        ctx.close()


def test_failure_isolation_and_validation():
    print("--> Testing per-file failure isolation + invalid requests...")
    ctx = E2ETestContext(prefix="mangatrax_t63_fail_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("staging_dir", staging)
        _cbz(os.path.join(staging, "Broken Group v01.cbz"), series="Broken Group", volume="1")
        with open(os.path.join(staging, "Broken Group v02.cbz"), "wb") as handle:
            handle.write(b"not a zip")
        with patch("app.main._calibre_series_snapshot", return_value=[]):
            response = ctx.client.post(
                "/api/downloads/staging-series/match",
                json_data={"source_series": "Broken Group", "new_series_title": "Fixed Group", "confirm": True},
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task_id"]
            deadline = time.time() + 5
            while time.time() < deadline:
                task = ctx.client.get(f"/api/tasks/{task_id}").json()["task"]
                if not task["active"]:
                    break
                time.sleep(0.05)
        assert task["status"] == "success", task
        assert task["result"]["tagged"] == 1 and task["result"]["failed"] == 1, task
        assert ctx.client.post("/api/downloads/staging-series/match", json_data={"source_series": "Missing", "new_series_title": "X", "confirm": True}).status_code in (400, 404)
        assert ctx.client.post("/api/downloads/staging-series/match", json_data={"source_series": "Broken Group"}).status_code == 400
        print("    [PASSED] one corrupt file fails while the good file is retagged; missing source/target rejected.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing grouped collapsible UI/master match wiring...")
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    for needle in ("staging-series-groups", "staging-series-group", "staging-group-count", "Match series",
                   "openStagingSeriesMatch", "staging-series-match-modal", "submitStagingSeriesMatch",
                   "/api/downloads/staging-series/match", "localStorage.setItem(key"):
        assert needle in page, f"missing UI wiring: {needle}"
    assert page.count("Preview / Tag") >= 1 and page.count("Delete") >= 1
    module = open(os.path.join(BACKEND_DIR, "app", "staging_groups.py"), encoding="utf-8").read()
    assert "downloads:staging-series-match" not in module  # task kind lives in main
    main = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    assert "downloads:staging-series-match" in main and "staging_groups.build_staging_groups" in main
    print("    [PASSED] collapsible groups, master Match buttons/modal/task endpoint, per-file actions preserved.")


def main():
    print("=" * 78)
    print("Running staging series groups suite (task-63)")
    print("=" * 78)
    tests = [test_grouping, test_retag_link_and_new_title, test_failure_isolation_and_validation, test_ui_wiring]
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
        print(f"STAGING GROUPS SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STAGING GROUP TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
