#!/usr/bin/env python3
"""task-42 suite: auto-cleanup/fuzzy matching + manual Awaiting-Calibre match.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

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
import app.main as main_module  # noqa: E402
from app.parser import clean_series_for_matching, extract_manga_bundle_and_volumes, normalize_text  # noqa: E402
from app import downloads_inventory  # noqa: E402
from app.tagger import cbz_tagger, pipeline as downloads_pipeline  # noqa: E402
from test_downloads_routing import _make_cbz  # noqa: E402

OSHI = "[Oshi No Ko]"
OSHI_TEMPLATE = {
    normalize_text(OSHI): {
        "series_name": OSHI, "authors": "Aka Akasaka", "publisher": "", "tags": "",
    },
}


def _seed_oshi(ctx):
    ctx.db.sync_calibre_series([{
        "series_name": OSHI, "series_id": 7, "book_count": 13, "latest_index": 13,
        "authors": "Aka Akasaka", "publisher": "", "tags": "",
    }])


def _cbz(path):
    _make_cbz(path)
    return path


# ===========================================================================
# 1. Parser cleanup + template fallback chain (Oshi case)
# ===========================================================================
def test_cleanup_and_resolution():
    print("--> Testing parser cleanup + cleaned/fuzzy template resolution...")
    assert clean_series_for_matching("[OSHI NO KO] {Plus}") == "[OSHI NO KO]"
    assert clean_series_for_matching("[OSHI NO KO] 001-130 as v01-13") == "[OSHI NO KO]"
    assert clean_series_for_matching("Steins;Gate") == "Steins;Gate"
    parsed = extract_manga_bundle_and_volumes("_OSHI NO KO_ 001-130 as v01-13 (Digital-Compilation) [Plus].cbz")
    assert "plus" not in normalize_text(parsed["series_name"]), parsed
    resolved = downloads_pipeline.resolve_series_template("[OSHI NO KO] {Plus}", OSHI_TEMPLATE)
    assert resolved and resolved["series_name"] == OSHI, resolved
    # Cleaned-key fallback also handles the range clause.
    resolved = downloads_pipeline.resolve_series_template("[OSHI NO KO] 001-130 as v01-13", OSHI_TEMPLATE)
    assert resolved and resolved["series_name"] == OSHI, resolved
    # Ambiguous fuzzy matches are never guessed.
    templates = {
        normalize_text("Test Series A"): {"series_name": "Test Series A"},
        normalize_text("Test Series B"): {"series_name": "Test Series B"},
    }
    assert downloads_pipeline.resolve_series_template("Test Series", templates) is None
    # Unknown/far names stay unresolved.
    assert downloads_pipeline.resolve_series_template("Totally Different", OSHI_TEMPLATE) is None
    print("    [PASSED] braces/range clauses cleaned; Oshi resolves; ambiguous fuzzy stays unresolved.")


# ===========================================================================
# 2. Awaiting scan + inventory hide auto-resolved series
# ===========================================================================
def test_awaiting_and_inventory_resolution():
    print("--> Testing awaiting scan/inventory use the shared resolution...")
    ctx = E2ETestContext(prefix="mangatrax_match_scan_")
    try:
        _seed_oshi(ctx)
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        _cbz(os.path.join(downloads, "OSHI NO KO v12.cbz"))
        templates = ctx.db.get_series_templates_map()
        awaiting = downloads_pipeline.scan_awaiting_series(downloads, templates)
        assert awaiting == [], awaiting
        inventory = downloads_inventory.scan_downloads(downloads, series_templates=templates)
        states = {entry["name"]: entry["state"] for entry in inventory["entries"]}
        assert states.get("OSHI NO KO v12.cbz") == "ready", states
        print("    [PASSED] resolved Oshi release is neither awaiting nor marked awaiting in inventory.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Manual Calibre match: override, tag/move, re-key, audit, future auto-match
# ===========================================================================
def test_manual_calibre_match():
    print("--> Testing manual match to a Calibre series...")
    ctx = E2ETestContext(prefix="mangatrax_match_calibre_")
    try:
        _seed_oshi(ctx)
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        source = "[OSHI NO KO] {Plus}"
        good = _cbz(os.path.join(downloads, "OSHI NO KO v12.cbz"))
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, 'DISCOVERY');",
                ("oshi-1", "_OSHI NO KO_ 001-130 as v01-13 (Digital-Compilation) v12.cbz", source),
            )
            release_id = cur.lastrowid
            conn.commit()
        snapshot = [{"id": 7, "series_name": OSHI, "name": OSHI, "authors": "Aka Akasaka", "book_count": 13}]
        with patch("app.main._calibre_series_snapshot", return_value=snapshot):
            response = ctx.client.post("/api/downloads/awaiting-series/match", json_data={
                "source_series": source, "calibre_series_id": 7,
            })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["target"]["name"] == OSHI, body
        assert release_id in body["release_ids"], body
        assert body["moved_files"] >= 1, body
        staged = os.path.join(staging, "OSHI NO KO v12.cbz")
        assert os.path.isfile(staged), os.listdir(staging)
        fields = cbz_tagger.read_comicinfo(staged)
        assert fields["Series"] == OSHI, fields
        assert fields["Title"].startswith(OSHI), fields
        with ctx.db.get_connection() as conn:
            row = conn.execute("SELECT series_name FROM releases WHERE id = ?;", (release_id,)).fetchone()
        assert row["series_name"] == OSHI, row
        overrides = ctx.db.get_series_alias_overrides()
        assert overrides and all(item["target_name"] == OSHI for item in overrides), overrides
        events = [item for item in ctx.db.get_logs(limit=50) if item["event_type"] == "SERIES_MANUALLY_MATCHED"]
        assert len(events) == 1, events
        # Future ingest with the same parsed source resolves automatically via override.
        with ctx.db.get_connection() as conn:
            override_sources = {row["source_norm"] for row in conn.execute(
                "SELECT source_norm FROM series_alias_overrides;"
            ).fetchall()}
        assert normalize_text(source) in override_sources, override_sources
        assert downloads_pipeline.resolve_series_template(source, ctx.db.get_series_templates_map())["series_name"] == OSHI
        assert os.path.isfile(good) is False, "original moved to staging"
        print("    [PASSED] Calibre match persists override, tags/moves flat, re-keys release, audits, future auto-match.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Manual new-title target + override removal
# ===========================================================================
def test_new_title_and_removal():
    print("--> Testing manual new-title target + override removal...")
    ctx = E2ETestContext(prefix="mangatrax_match_new_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        source = "Custom Braces Series {Plus}"
        _cbz(os.path.join(downloads, "Custom Braces Series v01.cbz"))
        response = ctx.client.post("/api/downloads/awaiting-series/match", json_data={
            "source_series": source, "new_series_title": "Custom Braces Series",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["target"]["calibre_series_id"] is None, body
        assert body["moved_files"] >= 1, body
        assert os.path.isfile(os.path.join(staging, "Custom Braces Series v01.cbz"))
        tracked = {row["name"]: row for row in ctx.db.get_all_series()}
        assert "Custom Braces Series" in tracked, tracked
        assert tracked["Custom Braces Series"]["calibre_series_id"] is None, tracked["Custom Braces Series"]
        aliases = ctx.db.get_series_alias_overrides()
        assert aliases and all(item["target_name"] == "Custom Braces Series" for item in aliases), aliases
        for alias in aliases:
            deleted = ctx.client.delete(f"/api/maintenance/series-aliases/{alias['id']}")
            assert deleted.status_code == 200, deleted.text
        assert ctx.db.get_series_alias_overrides() == []
        print("    [PASSED] new typed series created/routed; override listed and removable via endpoint.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Errors + missing files + ranking endpoint
# ===========================================================================
def test_errors_and_ranking():
    print("--> Testing 4xx errors, missing-file re-key, series ranking...")
    ctx = E2ETestContext(prefix="mangatrax_match_errors_")
    try:
        missing = ctx.client.post("/api/downloads/awaiting-series/match", json_data={
            "source_series": "Some Source",
        })
        assert missing.status_code == 400, missing.text
        bad = ctx.client.post("/api/downloads/awaiting-series/match", json_data={
            "source_series": "Some Source", "calibre_series_id": 99999,
        })
        assert bad.status_code in (400, 404), bad.status_code
        # No files present but releases re-keyed; response reports zero moves.
        with ctx.db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES "
                "('missing-1', '_OSHI NO KO_ v12.cbz', '[OSHI NO KO] {Plus}', 'DISCOVERY');"
            )
            conn.commit()
        with patch("app.main._calibre_series_snapshot", return_value=[
            {"id": 7, "series_name": OSHI, "name": OSHI, "authors": "Aka Akasaka", "book_count": 13},
        ]):
            ok = ctx.client.post("/api/downloads/awaiting-series/match", json_data={
                "source_series": "[OSHI NO KO] {Plus}", "calibre_series_id": 7,
            })
            ranked = ctx.client.get("/api/calibre/series")
            ranked_items = main_module._calibre_series_match_items(None, "OSHI NO KO")
        assert ok.status_code == 200, ok.text
        assert ok.json()["moved_files"] == 0, ok.json()
        assert ok.json()["release_ids"], ok.json()
        assert ranked.status_code == 200, ranked.text
        assert ranked_items and ranked_items[0]["name"] == OSHI, ranked_items
        template = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
        for needle in ("Manual match", "manual-match-modal", "openManualMatch", "submitManualMatch",
                       "/api/downloads/awaiting-series/match", "manual-match-search"):
            assert needle in template, f"missing manual-match UI wiring: {needle}"
        assert "location.reload()" not in template.split("async function submitManualMatch")[1].split("async function refreshDownloadsNow")[0]
        print("    [PASSED] 4xx validation, missing-file re-key (0 moved), ranked Calibre picker + UI wiring.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running manual series match suite (task-42)")
    print("=" * 78)
    tests = [
        test_cleanup_and_resolution,
        test_awaiting_and_inventory_resolution,
        test_manual_calibre_match,
        test_new_title_and_removal,
        test_errors_and_ranking,
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
        print(f"MANUAL SERIES MATCH SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL MANUAL SERIES MATCH TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
