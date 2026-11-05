#!/usr/bin/env python3
"""task-62 suite: tracked-series → Calibre link/merge (Failure Frame case)."""

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
from app.parser import CalibreSeriesIndex  # noqa: E402

LONG = "Failure Frame: I Became the Strongest and Annihilated Everything With Low-Level Spells"
CALIBRE_ENTRY = {"series_name": "Failure Frame", "name": "Failure Frame", "series_id": 42,
                 "book_count": 12, "latest_index": 12.0, "authors": "Author", "publisher": "Pub"}


def _volumes_map():
    return {"42": {float(v) for v in range(1, 13)}, "failure frame": {float(v) for v in range(1, 13)}}


def _tracker(ctx, name, monitored=1):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO series_tracking (name, is_monitored) VALUES (?, ?);", (name, monitored),
        )
        conn.commit()
        return cur.lastrowid


def _release(ctx, guid, series, volumes, status="REQUESTED"):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (guid, f"{series} v01-13", series, status, json.dumps(volumes), json.dumps(volumes)),
        )
        conn.commit()
        return cur.lastrowid


def test_root_cause_reproduction():
    print("--> Testing Failure Frame root cause + link + wanted semantics...")
    index = CalibreSeriesIndex([CALIBRE_ENTRY])
    assert index.match(LONG, threshold=0.90)["series_name"] == "Failure Frame", "colon subtitle must match"
    ctx = E2ETestContext(prefix="mangatrax_t62_repro_")
    try:
        tracked = _tracker(ctx, LONG)
        rel_id = _release(ctx, "ff-1", LONG, list(range(1, 14)))
        linked = ctx.db.auto_link_tracked_series([CALIBRE_ENTRY], volumes_map=_volumes_map())
        assert linked and linked[0]["target"] == "Failure Frame", linked
        trackers = ctx.db.get_all_series()
        assert len(trackers) == 1 and trackers[0]["name"] == "Failure Frame", trackers
        assert trackers[0]["calibre_series_id"] == 42 and trackers[0]["book_count"] == 12, trackers[0]
        assert json.loads(trackers[0]["owned_volumes_json"]) == [float(v) for v in range(1, 13)]
        release = ctx.db.get_release_by_id(rel_id)
        assert release["series_name"] == "Failure Frame", release
        # 12 Calibre-owned volumes → release covering 1-13 wants only 13.
        matcher = MangaCalibreMatcher(
            [CALIBRE_ENTRY], _volumes_map(), staged_volumes_map={},
        )
        result = matcher.check_release({"series_name": "Failure Frame", "volumes": [float(v) for v in range(1, 14)]})
        assert result["status"] == "REQUESTED" and result["missing_volumes"] == [13.0], result
        # Alias preserved for the old long name.
        aliases = ctx.db.get_series_alias_overrides()
        from app.parser import normalize_text
        assert any(row["source_norm"] == normalize_text(LONG) and row["target_name"] == "Failure Frame" for row in aliases), aliases
        print("    [PASSED] colon subtitle matches, tracker linked/merged, 12 owned, only v13 wanted, alias registered.")
    finally:
        ctx.close()


def test_sync_and_idempotency():
    print("--> Testing automatic sync link + idempotency...")
    ctx = E2ETestContext(prefix="mangatrax_t62_sync_")
    try:
        _tracker(ctx, LONG)
        _release(ctx, "ff-sync", LONG, [1.0, 2.0, 13.0])
        ctx.db.sync_calibre_series([CALIBRE_ENTRY], volumes_map=_volumes_map())
        trackers = ctx.db.get_all_series()
        assert len(trackers) == 1 and trackers[0]["name"] == "Failure Frame", trackers
        assert trackers[0]["calibre_series_id"] == 42, trackers[0]
        # Repeated sync/link must not duplicate or rename again.
        ctx.db.sync_calibre_series([CALIBRE_ENTRY], volumes_map=_volumes_map())
        ctx.db.auto_link_tracked_series([CALIBRE_ENTRY], volumes_map=_volumes_map())
        trackers = ctx.db.get_all_series()
        assert len(trackers) == 1 and trackers[0]["name"] == "Failure Frame", trackers
        assert ctx.db.get_release_by_id(1)["series_name"] == "Failure Frame"
        print("    [PASSED] sync auto-links the long tracker; repeated sync merge is idempotent.")
    finally:
        ctx.close()


def test_ambiguous_and_manual_repair():
    print("--> Testing ambiguous no-merge + manual endpoint + repair/status preservation...")
    ctx = E2ETestContext(prefix="mangatrax_t62_manual_")
    try:
        # Ambiguous-ish long tracker with no exact candidate match must remain unlinked.
        ambiguous = _tracker(ctx, "Other: Completely Different Subtitle")
        other_calibre = [
            {"series_name": "Other Series One", "series_id": 1, "book_count": 2},
            {"series_name": "Other Series Two", "series_id": 2, "book_count": 3},
        ]
        linked = ctx.db.auto_link_tracked_series(other_calibre)
        assert not any(item["source"] == "Other: Completely Different Subtitle" for item in linked), linked
        assert ctx.db.get_all_series()[0]["calibre_series_id"] is None
        # Manual link endpoint.
        canonical = _tracker(ctx, "Failure Frame", monitored=0)
        _tracker(ctx, LONG)  # duplicate tracker created before repair
        rel_id = _release(ctx, "ff-manual", LONG, [13.0], status="IGNORED")
        with patch("app.main._calibre_series_snapshot", return_value=[CALIBRE_ENTRY]):
            response = ctx.client.post(
                f"/api/series/{canonical}/match-calibre", json_data={"calibre_series_id": 42},
            )
        assert response.status_code == 200, response.text
        assert response.json()["merged"] is True, response.json()
        trackers = ctx.db.get_all_series()
        failure_trackers = [row for row in trackers if row["name"] == "Failure Frame"]
        assert len(failure_trackers) == 1, trackers
        assert failure_trackers[0]["calibre_series_id"] == 42, failure_trackers[0]
        assert failure_trackers[0]["is_monitored"] == 1, failure_trackers[0]
        assert ctx.db.get_release_by_id(rel_id)["status"] == "IGNORED", "release user veto must be preserved"
        assert ctx.db.get_release_by_id(rel_id)["series_name"] == "Failure Frame"
        # Repair audit dry-run is read-only; confirm repairs remaining candidates.
        with patch("app.main._calibre_series_snapshot", return_value=[CALIBRE_ENTRY]):
            audit = ctx.client.post("/api/series/link-repair", json_data={"dry_run": True})
        assert audit.status_code == 200 and audit.json()["dry_run"] is True, audit.text
        print("    [PASSED] ambiguous left unlinked, manual merge links + preserves vetoes, dry-run repair read-only.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing series UI/endpoint wiring...")
    row = open(os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_table_row_chunk.html"), encoding="utf-8").read()
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "series.html"), encoding="utf-8").read()
    main = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    assert "Not linked — Match to Calibre series" in row and "openSeriesCalibreMatch" in row
    for needle in ("series-calibre-match-modal", "searchSeriesCalibreMatch", "submitSeriesCalibreMatch",
                   "/api/series/${_seriesCalibreMatch.id}/match-calibre"):
        assert needle in page, needle
    assert "/api/series/{series_id}/match-calibre" in main and "/api/series/link-repair" in main
    print("    [PASSED] unlinked-state label/button, match modal, manual/repair endpoints wired.")


def main():
    print("=" * 78)
    print("Running tracked-series Calibre link suite (task-62)")
    print("=" * 78)
    tests = [test_root_cause_reproduction, test_sync_and_idempotency, test_ambiguous_and_manual_repair, test_ui_wiring]
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
        print(f"TRACKED MATCH SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TRACKED MATCH TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
