#!/usr/bin/env python3
"""task-40 suite: spinoff identity scoping, backfill guard, attribution repair.

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
from app.parser import normalize_text, split_series_candidate_parts  # noqa: E402
from test_torbox_downloader import Ctx  # noqa: E402

PARENT_RAW = "Tensei Shitara Slime Datta Ken"
PARENT_EN = "That Time I Got Reincarnated as a Slime"
SPINOFF_RAW = "Tensei Shitara Slime Datta Ken: Trinity in Tempest"
SPINOFF_EN = "Trinity in Tempest"


def _meta(**overrides):
    base = {
        "canonical_title": "", "english_title": "", "romaji_title": "",
        "total_volumes": 10, "status": "RELEASING", "synopsis": "",
        "source": "anilist", "anilist_id": None, "synonyms": [], "matched_term": "",
    }
    base.update(overrides)
    return base


def _resolver(name):
    """AniList stub with distinct parent id=1 and spinoff id=2."""
    norm = normalize_text(name)
    if norm == normalize_text(PARENT_RAW) or norm == normalize_text(PARENT_EN):
        return _meta(
            canonical_title=PARENT_EN, english_title=PARENT_EN,
            romaji_title=PARENT_RAW, anilist_id=1, synonyms=["Tensura"],
            matched_term=name,
        )
    if norm == normalize_text(SPINOFF_RAW) or norm == normalize_text(SPINOFF_EN):
        return _meta(
            canonical_title=SPINOFF_EN, english_title=SPINOFF_EN,
            romaji_title=SPINOFF_RAW, anilist_id=2, synonyms=["Tensura Nikki"],
            matched_term=name,
        )
    if norm == normalize_text(PARENT_EN + " | " + PARENT_RAW):
        # Combined alias string: resolver does not match it directly.
        return _meta(source="fallback", matched_term=name)
    return _meta(source="fallback", matched_term=name)


def _patch_anilist(ctx):
    return patch.object(ctx.scheduler.anilist, "resolve_manga_series", side_effect=_resolver)


# ===========================================================================
# 1. Identity matrix: own-entry aliases only; parent prefix cannot hijack
# ===========================================================================
def test_identity_matrix():
    print("--> Testing spinoff identity scoping...")
    ctx = Ctx()
    try:
        ctx.db.track_discovered_series(PARENT_EN)
        ctx.db.track_discovered_series(SPINOFF_EN)
        with _patch_anilist(ctx):
            parent = ctx.scheduler.resolve_series_identity(PARENT_RAW)
            spinoff = ctx.scheduler.resolve_series_identity(SPINOFF_RAW)
            tracked_spinoff = ctx.scheduler.resolve_series_identity(SPINOFF_EN)
            fallback = ctx.scheduler.resolve_series_identity(SPINOFF_RAW + " v01")
        assert parent["stable_name"] == PARENT_EN, parent
        assert parent["anilist_id"] == 1, parent
        assert spinoff["stable_name"] == SPINOFF_EN, spinoff
        assert spinoff["anilist_id"] == 2, spinoff
        assert spinoff["stable_name"] != parent["stable_name"], (spinoff, parent)
        assert tracked_spinoff["stable_name"] == SPINOFF_EN, tracked_spinoff
        assert fallback["stable_name"] != PARENT_EN, fallback
        print("    [PASSED] Parent: Spinoff resolves to its own AniList id/entry, never the parent.")

        # Delimiter parts are usable only when they agree on the SAME AniList id.
        combined = f"{PARENT_EN} | {PARENT_RAW}"
        with patch.object(ctx.scheduler.anilist, "resolve_manga_series",
                          side_effect=lambda name: (
                              _meta(source="fallback", matched_term=name)
                              if normalize_text(name) == normalize_text(combined) else _resolver(name)
                          )):
            alias_identity = ctx.scheduler.resolve_series_identity(combined)
        assert alias_identity["stable_name"] == PARENT_EN, alias_identity
        assert alias_identity["anilist_id"] == 1, alias_identity
        print("    [PASSED] English | Romaji (same entry id) still resolves to the tracked parent.")
    finally:
        ctx.close()


# ===========================================================================
# 2. detect_series_titles no longer harvests mis-attributed spinoff rows
# ===========================================================================
def test_detect_excludes_db_spinoffs():
    print("--> Testing detect_series_titles excludes DB spinoff rows...")
    ctx = Ctx()
    try:
        ctx.db.track_discovered_series(PARENT_EN)
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, 'DISCOVERY');",
                ("spinoff-guid", SPINOFF_RAW + " v01", PARENT_EN),
            )
            conn.commit()
        with _patch_anilist(ctx):
            titles = ctx.scheduler.detect_series_titles(PARENT_EN)
        norms = {normalize_text(title) for title in titles}
        assert normalize_text(SPINOFF_RAW) not in norms, titles
        assert normalize_text(SPINOFF_EN) not in norms, titles
        assert normalize_text(PARENT_EN) in norms, titles
        print("    [PASSED] titles come only from the requested name/parts/own aliases, not DB release harvesting.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Backfill release processing skips spinoffs with SPINOFF_SKIPPED audit
# ===========================================================================
def test_backfill_skips_spinoff():
    print("--> Testing backfill spinoff skip + auto-download guard...")
    ctx = Ctx()
    try:
        rel = SimpleNamespace(
            title=SPINOFF_RAW + " v01", series_name=SPINOFF_RAW, guid="spinoff-rel",
            release_group="", is_complete=False, is_bundle=False, volumes=[1],
            volume_start=1.0, volume_end=1.0, download_url="magnet:?xt=spinoff",
            link="", cover_url="", description="", meta=None,
            to_dict=lambda: {"title": SPINOFF_RAW + ' v01', "series_name": SPINOFF_RAW},
        )
        target_identity = {
            "primary_name": PARENT_EN,
            "alias_norms": {normalize_text(PARENT_EN), normalize_text(PARENT_RAW)},
            "anilist_id": 1,
        }
        with patch.object(ctx.scheduler, "_discard_unmarked_chapters_enabled", return_value=False), \
             patch.object(ctx.scheduler, "resolve_series_identity", return_value={
                 "stable_name": SPINOFF_RAW, "alt_names": [], "anilist_id": 2, "source": "raw", "meta": {}, "raw_name": SPINOFF_RAW,
             }):
            result = ctx.scheduler._process_release(
                rel, "test", SimpleNamespace(check_release=lambda *a, **k: {"status": "DISCOVERY"}),
                known_series_keys=set(), target_identity=target_identity,
            )
        assert result[1] == "SPINOFF_SKIPPED", result
        events = [row for row in ctx.db.get_logs(limit=50) if row["event_type"] == "SPINOFF_SKIPPED"]
        assert len(events) == 1, events
        assert "Trinity" in (events[0].get("details") or "") or "spinoff" in events[0]["message"].lower(), events[0]
        with ctx.db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM releases WHERE guid = 'spinoff-rel';").fetchone()[0]
        assert count == 0, "skipped spinoff must not be stored/downloaded"
        print("    [PASSED] spinoff release is skipped, audited once, never stored or auto-downloaded.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Attribution audit/repair re-keys mis-stamped rows and is idempotent
# ===========================================================================
def test_attribution_repair():
    print("--> Testing existing-data attribution audit/repair...")
    ctx = Ctx()
    try:
        ctx.db.track_discovered_series(PARENT_EN)
        ctx.db.track_discovered_series(SPINOFF_EN)
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, 'DISCOVERY');",
                ("bad-guid", SPINOFF_RAW + " v01", PARENT_EN),
            )
            bad_id = cur.lastrowid
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, 'DISCOVERY');",
                ("good-guid", PARENT_EN + " v01", PARENT_EN),
            )
            conn.commit()
        with _patch_anilist(ctx):
            audit = ctx.scheduler.audit_series_attribution()
            assert audit["summary"]["repairable"] == 1, audit
            record = audit["files"][0]
            assert record["id"] == bad_id and record["current_series"] == PARENT_EN, record
            assert record["suggested_series"] == SPINOFF_EN, record
            assert record["safe_to_repair"] is True, record
            repaired = ctx.scheduler.repair_series_attribution([bad_id], actor="tester")
            assert repaired["repaired"] == 1, repaired
            second = ctx.scheduler.repair_series_attribution([bad_id], actor="tester")
            assert second["repaired"] == 0 and second["skipped"] == 1, second
            audit_again = ctx.scheduler.audit_series_attribution()
        with ctx.db.get_connection() as conn:
            row = conn.execute("SELECT series_name FROM releases WHERE id = ?;", (bad_id,)).fetchone()
        assert row["series_name"] == SPINOFF_EN, row
        events = [item for item in ctx.db.get_logs(limit=50) if item["event_type"] == "SERIES_ATTRIBUTION_REPAIRED"]
        assert len(events) == 1, events
        assert audit_again["summary"]["repairable"] == 0, audit_again
        print("    [PASSED] mis-attributed row re-keyed once with audit; second run/audit is a no-op.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Endpoints + Settings UI wiring
# ===========================================================================
def test_endpoints_and_ui():
    print("--> Testing attribution endpoints + Settings card...")
    ctx = E2ETestContext(prefix="mangatrax_spinoff_api_")
    try:
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, 'DISCOVERY');",
                ("api-bad", SPINOFF_RAW + " v01", PARENT_EN),
            )
            conn.commit()
        get_response = ctx.client.get("/api/maintenance/series-attribution")
        assert get_response.status_code == 200, get_response.text
        assert "files" in get_response.json(), get_response.json()
        post_response = ctx.client.post(
            "/api/maintenance/series-attribution-repair", json_data={"release_ids": []},
        )
        assert post_response.status_code == 200, post_response.text
        assert post_response.json()["repaired"] == 0, post_response.json()
        template = open(
            os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8",
        ).read()
        for needle in ("series-attribution-card", "auditSeriesAttribution", "repairSeriesAttribution",
                       "/api/maintenance/series-attribution", "Spinoff / series attribution"):
            assert needle in template, f"missing attribution UI wiring: {needle}"
        print("    [PASSED] audit/repair endpoints exist and the Settings maintenance card is wired.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running spinoff identity/attribution suite (task-40)")
    print("=" * 78)
    tests = [
        test_identity_matrix,
        test_detect_excludes_db_spinoffs,
        test_backfill_skips_spinoff,
        test_attribution_repair,
        test_endpoints_and_ui,
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
        print(f"SPINOFF IDENTITY SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL SPINOFF IDENTITY/ATTRIBUTION TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
