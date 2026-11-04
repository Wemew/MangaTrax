#!/usr/bin/env python3
"""task-44 suite: bare chapter-range vs volume-bundle classification + repair + labels.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app.parser import extract_manga_bundle_and_volumes, is_chapter_only  # noqa: E402
from test_torbox_downloader import Ctx  # noqa: E402


def test_parser_matrix():
    print("--> Testing chapter-vs-volume classification matrix...")
    cases = [
        ("A Couple of Cuckoos 205-215 (2024) (Digital) (Rillant)", "A Couple of Cuckoos", [], [[205.0, 215.0]], True),
        ("A Couple of Cuckoos 205 (2024) (Digital)", "A Couple of Cuckoos", [], [], True),
        ("A Couple of Cuckoos Ch.205-215 (2024) (Digital)", "A Couple of Cuckoos", [], [[205.0, 215.0]], True),
        ("Kakkou no Iinazuke | A Couple of Cuckoos v01-28 [Color Chapter 1-215] (Digital)", "Kakkou no Iinazuke | A Couple of Cuckoos", list(range(1, 29)), [[1.0, 215.0]], False),
        ("A Couple of Cuckoos Vol. 205-215 (2024)", "A Couple of Cuckoos", list(range(205, 216)), [], False),
        ("Cuckoos Vols 100-105 (Digital)", "Cuckoos", [100.0, 101.0, 102.0, 103.0, 104.0, 105.0], [], False),
        ("Some Manga 01-05 (Digital)", "Some", [], [[1.0, 5.0]], True),
        ("Some Manga 1-5 (Digital)", "Some", [1.0, 2.0, 3.0, 4.0, 5.0], [], False),
        ("A Couple of Cuckoos 205 (2024) (Digital) (Rillant)", "A Couple of Cuckoos", [], [], True),
        ("That Time I Got Reincarnated as a Slime 119", "That Time I Got Reincarnated as a Slime", [], [], True),
        ("Series 100", "Series", [], [], True),
        ("Series (2024)", "Series", [], [], False),
        ("Series 5", "Series", [], [], True),
        ("Series v205", "Series", [205.0], [], False),
    ]
    for title in (
        "A Couple of Cuckoos 205 (2024) (Digital) (Rillant)",
        "That Time I Got Reincarnated as a Slime 119",
        "Series 100",
    ):
        parsed = extract_manga_bundle_and_volumes(title)
        assert parsed["chapter_start"] == parsed["chapter_end"], (title, parsed)
        assert parsed["chapter_start"] >= 100, (title, parsed)
        assert is_chapter_only(title) is True, title
    for title in ("Series (2024)", "Series v205"):
        assert is_chapter_only(title) is False, title
    for title, expected_series, expected_volumes, expected_ranges, expected_chapter in cases:
        parsed = extract_manga_bundle_and_volumes(title)
        assert parsed["series_name"] == expected_series, (title, parsed["series_name"])
        assert parsed["volumes"] == expected_volumes, (title, parsed["volumes"])
        assert parsed["chapter_ranges"] == expected_ranges, (title, parsed["chapter_ranges"])
        assert parsed["is_chapter"] is expected_chapter, (title, parsed["is_chapter"])
    assert is_chapter_only("A Couple of Cuckoos 205-215 (2024) (Digital)") is True
    assert is_chapter_only("A Couple of Cuckoos Vol. 205-215 (2024)") is False
    print("    [PASSED] 205-215 single/chapter markers -> chapters; v/Vol/Vols markers -> volumes; 01-05 chapter, 1-5 volumes.")


def test_process_release_chapter_only():
    print("--> Testing _process_release returns CHAPTER_ONLY for 205-215...")
    ctx = Ctx()
    try:
        rel = SimpleNamespace(
            title="A Couple of Cuckoos 205-215 (2024) (Digital) (Rillant)",
            series_name="A Couple of Cuckoos", guid="cuckoo-bad-range",
            release_group="Rillant", is_complete=False, is_bundle=False,
            volumes=[], volume_start=None, volume_end=None,
        )
        result = ctx.scheduler._process_release(rel, "test", SimpleNamespace(), set())
        assert result[1] == "CHAPTER_ONLY", result
        with ctx.db.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM releases WHERE guid = 'cuckoo-bad-range';",
            ).fetchone()[0]
        assert count == 0, "chapter release must not be stored/requested/downloaded"
        print("    [PASSED] chapter-range release is skipped as CHAPTER_ONLY, nothing stored or auto-downloaded.")
    finally:
        ctx.close()


def test_label_wiring():
    print("--> Testing Complete series label wiring...")
    partial = open(
        os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_card_chunk.html"),
        encoding="utf-8",
    ).read()
    flat = open(
        os.path.join(BACKEND_DIR, "app", "templates", "partials", "flat_release_row_chunk.html"),
        encoding="utf-8",
    ).read()
    for text in (partial, flat):
        assert "Complete series (v" in text, "missing complete-series range label"
        assert "volumes|length == 0" in text, "missing empty-volumes guard"
    assert "Bundle (" in partial, "normal bundle wording should remain available for non-empty bundles"
    print("    [PASSED] empty-volumes range labels render 'Complete series (vNN-NN)'; normal bundles unchanged.")


def test_repair_flow():
    print("--> Testing suspicious volume-range audit/repair...")
    ctx = E2ETestContext(prefix="mangatrax_volrange_")
    try:
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json, "
                "missing_vols_json, volume_start, volume_end, is_bundle) "
                "VALUES (?, ?, ?, 'REQUESTED', ?, ?, 205, 215, 1);",
                ("bad-range", "A Couple of Cuckoos 205-215 (2024) (Digital) (Rillant)",
                 "A Couple of Cuckoos", json.dumps(list(range(205, 216))), "[]"),
            )
            bad_id = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json, "
                "missing_vols_json, volume_start, volume_end, is_bundle) "
                "VALUES (?, ?, ?, 'REQUESTED', ?, ?, 205, 215, 1);",
                ("good-range", "A Couple of Cuckoos Vol. 205-215 (2024)",
                 "A Couple of Cuckoos", json.dumps(list(range(205, 216))), "[]"),
            )
            good_id = cur.lastrowid
            conn.commit()
        audit = ctx.client.get("/api/maintenance/suspicious-volume-ranges")
        assert audit.status_code == 200, audit.text
        body = audit.json()
        ids = {item["id"] for item in body["files"]}
        assert bad_id in ids and good_id not in ids, ids
        bad = next(item for item in body["files"] if item["id"] == bad_id)
        assert bad["safe_to_repair"] is True and bad["suggestion"] == "chapter-only", bad
        repaired = ctx.client.post(
            "/api/maintenance/repair-volume-ranges", json_data={"release_ids": [bad_id]},
        )
        assert repaired.status_code == 200, repaired.text
        assert repaired.json()["repaired"] == 1, repaired.json()
        with ctx.db.get_connection() as conn:
            row = conn.execute(
                "SELECT volumes_json, volume_start, volume_end, status, is_bundle "
                "FROM releases WHERE id = ?;", (bad_id,),
            ).fetchone()
            good = conn.execute("SELECT volumes_json, status FROM releases WHERE id = ?;", (good_id,)).fetchone()
        assert row["volumes_json"] == "[]" and row["volume_start"] is None, dict(row)
        assert row["status"] == "IGNORED" and row["is_bundle"] == 0, dict(row)
        assert json.loads(good["volumes_json"]) and good["status"] == "REQUESTED", dict(good)
        events = [item for item in ctx.db.get_logs(limit=50) if item["event_type"] == "CHAPTER_RANGE_REPAIRED"]
        assert len(events) == 1, events
        second = ctx.client.get("/api/maintenance/suspicious-volume-ranges").json()
        assert second["files"] == [], second
        template = open(
            os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8",
        ).read()
        for needle in ("volume-range-card", "auditVolumeRanges", "repairVolumeRanges",
                       "/api/maintenance/suspicious-volume-ranges"):
            assert needle in template, f"missing repair UI wiring: {needle}"
        print("    [PASSED] audit flags only invalid ranges; repair clears/marks IGNORED, logs once, idempotent; UI wired.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running chapter-vs-volume range suite (task-44)")
    print("=" * 78)
    tests = [
        test_parser_matrix,
        test_process_release_chapter_only,
        test_label_wiring,
        test_repair_flow,
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
        print(f"CHAPTER/VOLUME RANGE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL CHAPTER-VS-VOLUME RANGE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
