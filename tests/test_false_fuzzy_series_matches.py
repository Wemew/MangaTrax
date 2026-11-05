#!/usr/bin/env python3
"""task-64 suite: distinctive-head fuzzy guard + false-match audit/repair."""

import json
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
from app.calibre.matcher import MangaCalibreMatcher  # noqa: E402
from app import false_series_matches  # noqa: E402
from app.parser import CalibreSeriesIndex, match_manga_series_in_calibre  # noqa: E402

RED = "The Red Ranger Becomes an Adventurer in Another World"
LEVEL0 = "Level 0 Demon King Becomes an Adventurer in Another World"


def test_fuzzy_guard():
    print("--> Testing distinctive-head fuzzy guard...")
    index = CalibreSeriesIndex([{"series_name": LEVEL0, "id": 1}])
    assert index.match(RED, threshold=0.80) is None, "generic-suffix fuzzy match must be rejected"
    both = [{"series_name": LEVEL0, "id": 1}, {"series_name": RED, "id": 2}]
    matched = match_manga_series_in_calibre(RED, both)
    assert (matched or {}).get("id") == 2, matched
    assert match_manga_series_in_calibre("Solo Levelling", [{"series_name": "Solo Leveling", "id": 3}])
    assert match_manga_series_in_calibre("Solo Leveling: Ragnarok", [{"series_name": "Solo Leveling", "id": 3}])
    matcher = MangaCalibreMatcher([{"series_name": LEVEL0, "id": 1}], {})
    assert matcher.find_series(RED) is None, "matcher.find_series must reject suffix-only fuzzy"
    print("    [PASSED] Red Ranger→Level 0 rejected; correct series wins when both; typo/subtitle positives preserved.")


def test_audit_and_repair():
    print("--> Testing false-match audit + repair preserving veto...")
    ctx = E2ETestContext(prefix="mangatrax_t64_repair_")
    try:
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json) "
                "VALUES ('red-1', 'The Red Ranger Becomes an Adventurer in Another World v01', ?, 'REQUESTED', '[1]', '[1]');",
                (LEVEL0,),
            )
            first = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json) "
                "VALUES ('red-2', 'The Red Ranger Becomes an Adventurer in Another World v02', ?, 'IGNORED', '[2]', '[2]');",
                (LEVEL0,),
            )
            second = cur.lastrowid
            conn.commit()
        audit = false_series_matches.audit_false_series_matches(
            ctx.db, [{"series_name": LEVEL0, "id": 1}],
        )
        ids = {item["release_id"] for item in audit["candidates"]}
        assert first in ids and second in ids, audit
        assert all(item["suggested_series"] == "The Red Ranger Becomes an Adventurer in Another World" for item in audit["candidates"]), audit
        result = false_series_matches.repair_false_series_matches(
            ctx.db, [first, second], [{"series_name": LEVEL0, "id": 1}],
        )
        assert result["repaired"] == 2, result
        row1 = ctx.db.get_release_by_id(first)
        row2 = ctx.db.get_release_by_id(second)
        assert row1["series_name"] == "The Red Ranger Becomes an Adventurer in Another World", row1
        assert row1["status"] == "DISCOVERY", row1
        assert row2["status"] == "IGNORED", "user veto must be preserved"
        assert row2["series_name"] == "The Red Ranger Becomes an Adventurer in Another World"
        # Endpoint audit + repair.
        with patch("app.main._calibre_series_snapshot", return_value=[{"series_name": LEVEL0, "id": 1}]):
            audit_resp = ctx.client.post("/api/maintenance/false-series/audit")
        assert audit_resp.status_code == 200, audit_resp.text
        # New false row to exercise the repair endpoint.
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json) "
                "VALUES ('red-3', 'The Red Ranger Becomes an Adventurer in Another World v03', ?, 'REQUESTED', '[3]', '[3]');",
                (LEVEL0,),
            )
            conn.commit()
            rid = conn.execute("SELECT id FROM releases WHERE guid='red-3';").fetchone()["id"]
        with patch("app.main._calibre_series_snapshot", return_value=[{"series_name": LEVEL0, "id": 1}]):
            repair_resp = ctx.client.post(
                "/api/maintenance/false-series/repair", json_data={"release_ids": [rid], "confirm": True},
            )
        assert repair_resp.status_code == 200, repair_resp.text
        assert repair_resp.json()["repaired"] == 1, repair_resp.json()
        assert repair_resp.json().get("task_id"), repair_resp.json()
        print("    [PASSED] audit finds both Red Ranger rows; repair rekeys, preserves IGNORED, endpoint task works.")
    finally:
        ctx.close()


def test_ui_and_wiring():
    print("--> Testing matcher logs/UI wiring...")
    parser = open(os.path.join(BACKEND_DIR, "app", "parser.py"), encoding="utf-8").read()
    assert "_fuzzy_title_head_agrees" in parser and "Rejected fuzzy series match" in parser
    settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    for needle in ("false-series-card", "auditFalseSeriesMatches", "repairFalseSeriesMatches",
                   "/api/maintenance/false-series/audit", "/api/maintenance/false-series/repair",
                   "false-series-selected"):
        assert needle in settings, needle
    main = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    assert "false_series_matches.audit_false_series_matches" in main and "false-series" in main
    print("    [PASSED] guard logging, audit/repair endpoints and Settings maintenance card wired.")


def main():
    print("=" * 78)
    print("Running false fuzzy series match suite (task-64)")
    print("=" * 78)
    tests = [test_fuzzy_guard, test_audit_and_repair, test_ui_and_wiring]
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
        print(f"FALSE FUZZY MATCH SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL FALSE FUZZY MATCH TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
