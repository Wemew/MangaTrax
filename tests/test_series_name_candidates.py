#!/usr/bin/env python3
"""task-43 suite: parser recovery + candidate matching for bracketed series names.

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
from app.parser import (  # noqa: E402
    extract_manga_bundle_and_volumes,
    normalize_text,
    series_name_candidates,
)
from app import downloads_inventory  # noqa: E402
from app.tagger import cbz_tagger, pipeline as downloads_pipeline  # noqa: E402
from test_downloads_routing import _make_cbz  # noqa: E402

OSHI = "[Oshi No Ko]"
OSHI_TEMPLATE = {
    normalize_text(OSHI): {"series_name": OSHI, "authors": "Aka Akasaka", "publisher": "", "tags": ""},
}


def test_parser_matrix():
    print("--> Testing parser recovery matrix...")
    cases = [
        ("[OSHI NO KO] {Plus} v12 (Digital).cbz", "[OSHI NO KO]", [12.0]),
        ("[OSHI NO KO] {Plus} v12-13 (Digital).cbz", "[OSHI NO KO]", [12.0, 13.0]),
        ("[OSHI NO KO] v12 (Digital).cbz", "[OSHI NO KO]", [12.0]),
        ("_OSHI NO KO_ 001-130 as v01-13 (Digital-Compilation) v12.cbz", "OSHI NO KO", list(range(1, 14))),
        ("OSHI NO KO v12 (Digital) (Plus).cbz", "OSHI NO KO", [12.0]),
        ("[Group] Series v01.cbz", "Series", [1.0]),
    ]
    for filename, expected_series, expected_volumes in cases:
        parsed = extract_manga_bundle_and_volumes(filename)
        assert parsed["series_name"] == expected_series, (filename, parsed["series_name"])
        assert parsed["volumes"] == expected_volumes, (filename, parsed["volumes"])
        assert parsed["series_name"] != "{Plus}", filename
    print("    [PASSED] five Oshi filename shapes recover the bracket/underscore series; [Group] Series unchanged.")


def test_candidates_and_resolution():
    print("--> Testing series_name_candidates + template resolution...")
    forms = [
        "[OSHI NO KO] {Plus} v12 (Digital).cbz",
        "[OSHI NO KO] {Plus} v12-13 (Digital).cbz",
        "[OSHI NO KO] v12 (Digital).cbz",
        "_OSHI NO KO_ 001-130 as v01-13 (Digital-Compilation) v12.cbz",
        "OSHI NO KO v12 (Digital) (Plus).cbz",
    ]
    for form in forms:
        candidates = series_name_candidates(form)
        assert candidates, (form, candidates)
        resolved = downloads_pipeline.resolve_series_template(extract_manga_bundle_and_volumes(form)["series_name"], OSHI_TEMPLATE)
        assert resolved and resolved["series_name"] == OSHI, (form, resolved)
        direct = None
        for candidate in candidates:
            direct = downloads_pipeline.resolve_series_template(candidate, OSHI_TEMPLATE)
            if direct:
                break
        assert direct and direct["series_name"] == OSHI, (form, candidates)
    # A genuinely different series must not auto-map via candidates.
    assert downloads_pipeline.resolve_series_template("Another Manga Complete", OSHI_TEMPLATE) is None
    print("    [PASSED] bracket/underscore/cleaned candidates resolve all forms to [Oshi No Ko]; different series stays unresolved.")


def test_routing_end_to_end():
    print("--> Testing end-to-end routing of bracket-name files (no manual match)...")
    ctx = E2ETestContext(prefix="mangatrax_candidates_route_")
    try:
        ctx.db.sync_calibre_series([{
            "series_name": OSHI, "series_id": 7, "book_count": 13, "latest_index": 13,
            "authors": "Aka Akasaka", "publisher": "", "tags": "",
        }])
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        _make_cbz(os.path.join(downloads, "[OSHI NO KO] {Plus} v12 (Digital).cbz"))
        _make_cbz(os.path.join(downloads, "_OSHI NO KO_ 001-130 as v01-13 (Digital-Compilation) v12.cbz"))
        templates = ctx.db.get_series_templates_map()
        awaiting = downloads_pipeline.scan_awaiting_series(downloads, templates)
        assert awaiting == [], awaiting
        inventory = downloads_inventory.scan_downloads(downloads, series_templates=templates)
        states = {entry["name"]: entry["state"] for entry in inventory["entries"]}
        assert all(state == "ready" for state in states.values()), states
        result = downloads_pipeline.process_downloads_to_staging(downloads, staging, templates, ctx.db, actor="tester")
        assert result["tagged_and_moved"] == 2, result
        staged = sorted(os.listdir(staging))
        assert "_OSHI NO KO_ 001-130 as v01-13 (Digital-Compilation) v12.cbz" in staged, staged
        assert "[OSHI NO KO] {Plus} v12 (Digital).cbz" in staged, staged
        for filename in staged:
            fields = cbz_tagger.read_comicinfo(os.path.join(staging, filename))
            assert fields["Series"] == OSHI, (filename, fields)
            assert fields["Title"].startswith(OSHI), (filename, fields)
        assert sorted(os.listdir(downloads)) == [], "all files moved from downloads"
        print("    [PASSED] bracket/underscore files tag with [Oshi No Ko] and move flat to staging without manual match.")
    finally:
        ctx.close()


def test_manual_match_still_works():
    print("--> Testing manual match for genuinely unknown series + override across variants...")
    ctx = E2ETestContext(prefix="mangatrax_candidates_manual_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        source = "Totally Unknown Thing {Plus} v01.cbz"
        _make_cbz(os.path.join(downloads, source))
        response = ctx.client.post("/api/downloads/awaiting-series/match", json_data={
            "source_series": "Totally Unknown Thing", "new_series_title": "Totally Unknown Thing",
        })
        assert response.status_code == 200, response.text
        assert response.json()["moved_files"] >= 1, response.json()
        assert os.path.isfile(os.path.join(staging, source)), os.listdir(staging)
        overrides = ctx.db.get_series_alias_overrides()
        assert overrides and overrides[0]["target_name"] == "Totally Unknown Thing", overrides
        # A different parse shape for the same source resolves through the candidates.
        variant = os.path.join(downloads, "Totally Unknown Thing v02.cbz")
        _make_cbz(variant)
        templates = ctx.db.get_series_templates_map()
        resolved = downloads_pipeline.resolve_series_template("Totally Unknown Thing", templates)
        assert resolved and resolved["series_name"] == "Totally Unknown Thing", resolved
        result = downloads_pipeline.process_downloads_to_staging(downloads, staging, templates, ctx.db, actor="tester")
        assert result["tagged_and_moved"] == 1, result
        assert any(name.startswith("Totally Unknown Thing") for name in os.listdir(staging)), os.listdir(staging)
        print("    [PASSED] manual match still works and its override covers variant parse shapes.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing candidate/manual-match UI remains wired...")
    template = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    for needle in ("Manual match", "manual-match-modal", "openManualMatch", "/api/downloads/awaiting-series/match"):
        assert needle in template, f"missing UI wiring: {needle}"
    source = open(os.path.join(BACKEND_DIR, "app", "parser.py"), encoding="utf-8").read()
    assert "def series_name_candidates" in source and "_recover_series_from_result" in source
    print("    [PASSED] parser candidates and manual-match UI are wired.")


def main():
    print("=" * 78)
    print("Running bracketed series-name recovery suite (task-43)")
    print("=" * 78)
    tests = [
        test_parser_matrix,
        test_candidates_and_resolution,
        test_routing_end_to_end,
        test_manual_match_still_works,
        test_ui_wiring,
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
        print(f"SERIES CANDIDATES SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL SERIES-NAME CANDIDATE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
