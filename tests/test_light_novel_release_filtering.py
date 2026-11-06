#!/usr/bin/env python3
"""task-70 suite: persisted light-novel classification + filtering/cleanup."""

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
from app.database import MangaDatabase  # noqa: E402
from app import ln_releases  # noqa: E402
from app.parser import is_light_novel  # noqa: E402

LN_TITLE = ("I Parry Everything: What Do You Mean I’m the Strongest? I’m Not Even an Adventurer Yet! "
            "- Volume 09 [J-Novel Club]")
LN_ASCII = ("I Parry Everything: What Do You Mean I'm the Strongest? I'm Not Even an Adventurer Yet! "
            "- Volume 09 [J-Novel Club]")


def _insert(ctx, guid, title, series, status="DISCOVERY", volume=1.0, ln=0):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json, "
            "volume_start, is_light_novel) VALUES (?, ?, ?, ?, ?, '[]', ?, ?);",
            (guid, title, series, status, json.dumps([volume]), volume, ln),
        )
        conn.commit()
        return cur.lastrowid


def test_detection_and_persistence():
    print("--> Testing detection (curly/ASCII J-Novel) + upsert persistence/backfill...")
    assert is_light_novel(LN_TITLE) is True
    assert is_light_novel(LN_ASCII) is True
    assert is_light_novel("Blue Lock - Episode Nagi (Digital) (Stick)") is False
    ctx = E2ETestContext(prefix="mangatrax_t70_detect_")
    try:
        _, ln_id = ctx.db.upsert_release({
            "guid": "ln-upsert", "title": LN_ASCII, "series_name": "I Parry Everything",
            "volumes": [9.0], "missing_volumes": [9.0], "status": "DISCOVERY",
        })
        assert ctx.db.get_release_by_id(ln_id)["is_light_novel"] == 1
        _, manga_id = ctx.db.upsert_release({
            "guid": "manga-upsert", "title": "Blue Lock v01", "series_name": "Blue Lock",
            "volumes": [1.0], "missing_volumes": [1.0], "status": "DISCOVERY",
        })
        assert ctx.db.get_release_by_id(manga_id)["is_light_novel"] == 0
        ctx.db.upsert_release({
            "guid": "manga-upsert", "title": LN_ASCII, "series_name": "I Parry Everything",
            "volumes": [9.0], "missing_volumes": [9.0], "status": "DISCOVERY",
        })
        assert ctx.db.get_release_by_id(manga_id)["is_light_novel"] == 1
        # One-time legacy backfill: unclassified row + cleared marker re-runs migration.
        legacy = _insert(ctx, "ln-legacy", LN_TITLE, "I Parry Everything", ln=0)
        with ctx.db.get_connection() as conn:
            conn.execute("DELETE FROM app_settings WHERE key = 'ln_release_backfill_done';")
            conn.commit()
        MangaDatabase(ctx.db_path).init_db()
        assert ctx.db.get_release_by_id(legacy)["is_light_novel"] == 1
        print("    [PASSED] exact/ASCII LN detected; upsert insert/update persists; legacy backfill flags old row.")
    finally:
        ctx.close()


def test_filters():
    print("--> Testing releases/discovery/count/search/pending filters...")
    ctx = E2ETestContext(prefix="mangatrax_t70_filter_")
    try:
        regular = _insert(ctx, "r-regular", "Regular Series v01", "Regular Series", ln=0)
        legacy = _insert(ctx, "r-legacy", LN_TITLE, "I Parry Everything", ln=1)
        flagged = _insert(ctx, "r-flagged", LN_ASCII, "I Parry Everything", status="REQUESTED", volume=9.0, ln=1)
        rows = ctx.db.get_releases(limit=100)
        ids = {row["id"] for row in rows}
        assert regular in ids and legacy not in ids and flagged not in ids, ids
        assert ctx.db.count_releases() == 1
        groups = ctx.db.get_releases_grouped_by_series(limit=100)
        group_ids = {rel["id"] for group in groups for rel in (group.get("releases") or [])}
        assert regular in group_ids and legacy not in group_ids, group_ids
        discovery = ctx.db.get_discovery_releases(limit=100)
        assert regular in {row["id"] for row in discovery} and legacy not in {row["id"] for row in discovery}
        assert ctx.db.count_discovery_releases() == 1
        assert legacy not in {row["id"] for row in ctx.db.search_releases("Parry")}
        assert flagged not in {row["id"] for row in ctx.db.get_pending_requests(limit=100)}
        # Search/status filters stay consistent.
        assert legacy not in {row["id"] for row in ctx.db.get_releases(search="Parry", limit=100)}
        assert flagged not in {row["id"] for row in ctx.db.get_releases(status="REQUESTED", limit=100)}
        print("    [PASSED] legacy/flagged LN hidden from lists, groups, discovery/count, search and pending requests.")
    finally:
        ctx.close()


def test_cleanup_and_ui():
    print("--> Testing maintenance audit + ignore/delete + UI wiring...")
    ctx = E2ETestContext(prefix="mangatrax_t70_cleanup_")
    try:
        ln1 = _insert(ctx, "ln-1", LN_ASCII, "I Parry Everything", status="REQUESTED", ln=1)
        ln2 = _insert(ctx, "ln-2", "[Yen Press] Some Novel v01", "Some Novel", status="REQUESTED", ln=1)
        regular = _insert(ctx, "r-1", "Regular v01", "Regular", ln=0)
        audit = ln_releases.audit_ln_releases(ctx.db)
        assert audit["count"] == 2 and {row["id"] for row in audit["releases"]} == {ln1, ln2}, audit
        result = ln_releases.cleanup_ln_releases(ctx.db, [ln1, regular], "ignore")
        assert result["changed"] == 1 and result["skipped"] == 1, result
        assert ctx.db.get_release_by_id(ln1)["status"] == "IGNORED"
        response = ctx.client.post("/api/maintenance/light-novels/cleanup",
                                   json_data={"release_ids": [ln2], "action": "delete", "confirm": True})
        assert response.status_code == 200, response.text
        assert response.json()["changed"] == 1, response.json()
        assert ctx.db.get_release_by_id(ln2) is None and ctx.db.get_release_by_id(regular) is not None
        assert ctx.client.post("/api/maintenance/light-novels/cleanup",
                               json_data={"release_ids": [regular], "action": "delete"}).status_code == 400
        settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
        for needle in ("light-novel-card", "auditLightNovels", "cleanupLightNovels",
                       "Ignore selected", "Delete selected", "/api/maintenance/light-novels/audit",
                       "/api/maintenance/light-novels/cleanup", "second click"):
            assert needle in settings, needle
        print("    [PASSED] audit rows/count; ignore preserves history, delete removes row only; UI/card wired.")
    finally:
        ctx.close()


def test_source_wiring():
    print("--> Testing upsert/backfill/query wiring...")
    db = open(os.path.join(BACKEND_DIR, "app", "database.py"), encoding="utf-8").read()
    assert "is_light_novel" in db and "ln_release_backfill_done" in db
    assert db.count("COALESCE(is_light_novel, 0) = 0") >= 8
    main = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    assert "/api/maintenance/light-novels/audit" in main
    print("    [PASSED] migration/upsert/backfill and all list/discovery query guards present.")


def main():
    print("=" * 78)
    print("Running light-novel release filtering suite (task-70)")
    print("=" * 78)
    tests = [test_detection_and_persistence, test_filters, test_cleanup_and_ui, test_source_wiring]
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
        print(f"LIGHT-NOVEL FILTER SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL LIGHT-NOVEL FILTER TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
