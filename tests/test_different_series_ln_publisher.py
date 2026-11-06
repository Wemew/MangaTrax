#!/usr/bin/env python3
"""task-66 suite: per-release different-series + single-publisher light-novel rule."""

import json
import os
import sys
import traceback
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app.parser import is_light_novel  # noqa: E402


def test_light_novel_matrix():
    print("--> Testing single-publisher LN rule + paren semantics...")
    positives = [
        "[Yen Press] Some Novel Title v01",
        "[Seven Seas] Some Novel Title v01-02",
        "[J-Novel Club] Some Novel Title v01",
        "[Yen On] Some Novel Title v01",
        "[Airship] Some Novel Title v01",
        "[Seven Seas Siren] Some Novel Title v01",
        "[Cross Infinite World] Some Novel Title v01",
        "[Seven Seas] [Stick] Some Novel Title v01",
    ]
    negatives = [
        "Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)",
        "(Yen Press) Some Manga Title v01",
        "[Yen Press] [Digital] Some Manga Title v01",
        "[CBZ] Some Manga Title v01",
        "Some Manga Title [Yen Press] v01 [Manga]",
        "[Yen Press] [Vol 1-3] Some Manga Title",
    ]
    for title in positives:
        assert is_light_novel(title) is True, f"expected LN: {title}"
    for title in negatives:
        assert is_light_novel(title) is False, f"expected manga: {title}"
    print("    [PASSED] single [imprint] LN positives, two-bracket case, paren-only/digital/manga negatives.")


def _release(ctx, guid, series, volumes, status="REQUESTED"):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (guid, f"{series} v01", series, status, json.dumps(volumes), json.dumps(volumes)),
        )
        conn.commit()
        return cur.lastrowid


def test_different_series_endpoint():
    print("--> Testing per-release different-series endpoint semantics...")
    ctx = E2ETestContext(prefix="mangatrax_t66_diff_")
    try:
        first = _release(ctx, "diff-1", "Original Series", [2.0])
        sibling = _release(ctx, "diff-2", "Original Series", [3.0])
        response = ctx.client.post(
            f"/api/releases/{first}/different-series", json_data={"new_series_title": "New Series"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["series_name"] == "New Series" and body["release_status"] == "REQUESTED", body  # sticky precedence
        assert ctx.db.get_release_by_id(first)["series_name"] == "New Series"
        assert ctx.db.get_release_by_id(sibling)["series_name"] == "Original Series", "sibling must be untouched"
        assert not any(row["target_name"] == "New Series" for row in ctx.db.get_series_alias_overrides()), "no global alias"
        events = [row for row in ctx.db.get_logs(limit=30) if row["event_type"] == "RELEASE_REKEYED_DIFFERENT_SERIES"]
        assert events, events
        # Veto preserved.
        veto = _release(ctx, "diff-3", "Original Series", [1.0], status="IGNORED")
        res = ctx.client.post(f"/api/releases/{veto}/different-series", json_data={"detach": True})
        assert res.status_code == 200 and res.json()["release_status"] == "IGNORED", res.text
        # Detach volume 1 → DISCOVERY.
        det = _release(ctx, "diff-4", "Original Series", [1.0])
        res = ctx.client.post(f"/api/releases/{det}/different-series", json_data={"detach": True})
        assert res.status_code == 200 and res.json()["release_status"] == "DISCOVERY", res.text
        # Calibre target branch via patched matcher.
        target_release = _release(ctx, "diff-5", "Original Series", [3.0])
        snapshot = [{"series_name": "Target Series", "series_id": 9, "book_count": 5}]
        fake_matcher = MagicMock()
        fake_matcher.check_release.return_value = {"status": "REQUESTED", "missing_volumes": [3.0]}
        with patch("app.main._calibre_series_snapshot", return_value=snapshot), \
             patch.object(ctx.scheduler, "_series_refresh_matcher", return_value=fake_matcher):
            res = ctx.client.post(
                f"/api/releases/{target_release}/different-series",
                json_data={"calibre_series_id": 9},
            )
        assert res.status_code == 200, res.text
        assert res.json()["series_name"] == "Target Series" and res.json()["release_status"] == "REQUESTED", res.text
        assert ctx.db.get_release_by_id(target_release)["missing_volumes"] == [3.0]
        # Invalid / missing.
        assert ctx.client.post("/api/releases/999999/different-series", json_data={"detach": True}).status_code == 404
        assert ctx.client.post(f"/api/releases/{first}/different-series", json_data={}).status_code == 400
        print("    [PASSED] only target re-keyed, no alias, veto preserved, detach→Discovery, Calibre target status/missing, 404/400.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing Different-series buttons/modal + LN matrix source wiring...")
    flat = open(os.path.join(BACKEND_DIR, "app", "templates", "partials", "flat_release_row_chunk.html"), encoding="utf-8").read()
    card = open(os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_card_chunk.html"), encoding="utf-8").read()
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "releases.html"), encoding="utf-8").read()
    main = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    parser = open(os.path.join(BACKEND_DIR, "app", "parser.py"), encoding="utf-8").read()
    assert "openDifferentSeriesModal" in flat and "openDifferentSeriesModal" in card
    for needle in ("different-series-modal", "searchDifferentSeries", "submitDifferentSeries",
                   "Detach → Discovery", "/api/releases/${_differentRelease.id}/different-series"):
        assert needle in page, needle
    assert "/api/releases/{release_id}/different-series" in main
    assert "Single square-bracketed publisher" in parser
    print("    [PASSED] grouped/flat buttons, shared modal/detach/apply, endpoint and LN rule present.")


def main():
    print("=" * 78)
    print("Running different-series + LN publisher suite (task-66)")
    print("=" * 78)
    tests = [test_light_novel_matrix, test_different_series_endpoint, test_ui_wiring]
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
        print(f"DIFFERENT SERIES/LN SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL DIFFERENT SERIES/LN TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
