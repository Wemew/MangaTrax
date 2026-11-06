#!/usr/bin/env python3
"""task-69 suite: inherit previous-volume filename/title + manual intervention naming."""

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
from app import staging_groups, volume_naming  # noqa: E402
from app.tagger import pipeline, cbz_tagger  # noqa: E402

TEMPLATE = {"series": {"series_name": "Series", "authors": "A", "publisher": "P", "tags": "Manga"}}


def _cbz(path, entries=None, title="", series="", volume=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in (entries or {"001.jpg": b"image"}).items():
            archive.writestr(name, payload)
        if series:
            archive.writestr("ComicInfo.xml",
                             f"<ComicInfo><Series>{series}</Series><Volume>{volume}</Volume><Title>{title}</Title></ComicInfo>")


def test_replacement_rules():
    print("--> Testing volume-token replacement variants/zero-padding/ambiguity...")
    assert volume_naming.inherit_previous_name("Series v01.cbz", 1, 2) == "Series v02.cbz"
    assert volume_naming.inherit_previous_name("Series Vol. 01 - Subtitle.cbz", 1, 2) == "Series Vol. 02 - Subtitle.cbz"
    assert volume_naming.inherit_previous_name("Series v001.cbz", 1, 12) == "Series v012.cbz"
    assert volume_naming.inherit_previous_name("Series v09.cbz", 9, 10) == "Series v10.cbz"
    assert volume_naming.inherit_previous_title("Series Vol. 01 - Subtitle", 1, 2) == "Series Vol. 02 - Subtitle"
    assert volume_naming.inherit_previous_name("Series.cbz", 1, 2) is None
    assert volume_naming.inherit_previous_name("Series v01 v02.cbz", 1, 2) is None
    assert volume_naming.inherit_previous_name("Series v01.cbz", 5, 2) is None
    # Middle markers are replaced only when unique.
    assert volume_naming.inherit_previous_name("Series v01 - Subtitle v02.cbz", 1, 3) is None
    print("    [PASSED] v/vol/subtitle/zero-padding variants; missing/ambiguous/mismatched markers return None.")


def test_assessment_and_groups():
    print("--> Testing intervention assessment + staging group flags/suggestions...")
    ctx = E2ETestContext(prefix="mangatrax_t69_groups_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        _cbz(os.path.join(staging, "Series v01 - Subtitle.cbz"), title="Series Vol. 01 - Subtitle", series="Series", volume="1")
        _cbz(os.path.join(staging, "Series v02 - Subtitle.cbz"), title="Series Vol. 02 - Subtitle", series="Series", volume="2")
        _cbz(os.path.join(staging, "Mystery - Subtitle.cbz"))
        groups = staging_groups.build_staging_groups(staging)
        series = next(group for group in groups if group["series"] == "Series")
        by_name = {item["name"]: item for item in series["files"]}
        assert by_name["Series v02 - Subtitle.cbz"]["needs_intervention"] is False, by_name
        assert by_name["Series v02 - Subtitle.cbz"]["suggested_filename"] == "Series v02 - Subtitle.cbz", by_name
        mystery = next(group for group in groups if "Mystery" in group["series"])
        assert mystery["needs_intervention"] == 1, mystery
        assert mystery["files"][0]["intervention_reason"], mystery["files"][0]
        assert series["needs_intervention"] == 0
        flags = volume_naming.assess_volume_naming("Series v01 v02.cbz", {"series_name": "Series", "volumes": [1.0], "volume_start": 1.0})
        assert flags["needs_intervention"] and "Multiple volume markers" in flags["reason"], flags
        flags = volume_naming.assess_volume_naming("NoVolume.cbz", {"series_name": "NoVolume", "volumes": []})
        assert flags["needs_intervention"] and "Volume could not be parsed" in flags["reason"], flags
        print("    [PASSED] groups expose per-file parsed/flag/reason/suggestions; counters aggregate; bad parses flagged.")
    finally:
        ctx.close()


def test_pipeline_inheritance_and_fallback():
    print("--> Testing pipeline inheritance → flag/fallback → staged naming...")
    ctx = E2ETestContext(prefix="mangatrax_t69_pipe_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        _cbz(os.path.join(staging, "Series v01 - Subtitle.cbz"), title="Series Vol. 01 - Subtitle", series="Series", volume="1")
        _cbz(os.path.join(downloads, "Series v02.cbz"))
        result = pipeline.process_downloads_to_staging(downloads, staging, TEMPLATE, ctx.db)
        assert result["tagged_and_moved"] == 1, result
        assert os.path.isfile(os.path.join(staging, "Series v02 - Subtitle.cbz")), os.listdir(staging)
        info = cbz_tagger.read_comicinfo(os.path.join(staging, "Series v02 - Subtitle.cbz"))
        assert info.get("Title") == "Series Vol. 02 - Subtitle", info
        events = [row for row in ctx.db.get_logs(limit=30) if row["event_type"] == "VOLUME_NAME_INHERITED"]
        assert events, events
        # No previous volume: canonical fallback, not a failure.
        _cbz(os.path.join(downloads, "Fresh v03.cbz"))
        fresh_template = {"fresh": {"series_name": "Fresh", "authors": "A", "publisher": "P", "tags": "Manga"}}
        result = pipeline.process_downloads_to_staging(downloads, staging, fresh_template, ctx.db)
        assert result["tagged_and_moved"] == 1, result
        assert os.path.isfile(os.path.join(staging, "Fresh v03.cbz")) or any(name.startswith("Fresh") for name in os.listdir(staging)), os.listdir(staging)
        # Previous template with no volume token: fallback + intervention flag, not guessed.
        _cbz(os.path.join(staging, "NoToken.cbz"), title="NoToken", series="NoToken", volume="1")
        _cbz(os.path.join(downloads, "NoToken v02.cbz"))
        no_token_template = {"notoken": {"series_name": "NoToken", "authors": "A", "publisher": "P", "tags": "Manga"}}
        result = pipeline.process_downloads_to_staging(downloads, staging, no_token_template, ctx.db)
        assert result["tagged_and_moved"] == 1, result
        processed = result["processed"][0]
        assert processed["needs_intervention"] is True, processed
        assert os.path.isfile(os.path.join(staging, "NoToken v02.cbz")), os.listdir(staging)
        print("    [PASSED] inherited filename/title + activity; no previous uses fallback; tokenless template flags without guessing.")
    finally:
        ctx.close()


def test_manual_fix_endpoint():
    print("--> Testing manual filename fix/rename safety...")
    ctx = E2ETestContext(prefix="mangatrax_t69_manual_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("staging_dir", staging)
        _cbz(os.path.join(staging, "Fix Me v02.cbz"), series="Fix Me", volume="2")
        response = ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "Fix Me v02.cbz", "new_filename": "Fix Me v02 - Subtitle.cbz",
            "series": "Fix Me", "volume": "2", "title": "Fix Me Vol. 02 - Subtitle",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["renamed_from"] == "Fix Me v02.cbz" and data["file"] == "Fix Me v02 - Subtitle.cbz", data
        assert not os.path.exists(os.path.join(staging, "Fix Me v02.cbz"))
        assert os.path.isfile(os.path.join(staging, "Fix Me v02 - Subtitle.cbz"))
        # Collision is rejected without overwrite; traversal rejected.
        _cbz(os.path.join(staging, "Other v01.cbz"), series="Other", volume="1")
        _cbz(os.path.join(staging, "Game v02.cbz"), series="Game", volume="2")
        response = ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "Game v02.cbz", "new_filename": "Other v01.cbz", "series": "Game", "volume": "2",
        })
        assert response.status_code == 409, response.text
        assert os.path.isfile(os.path.join(staging, "Game v02.cbz"))
        response = ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "Game v02.cbz", "new_filename": "../evil.cbz", "series": "Game", "volume": "2",
        })
        assert response.status_code == 400, response.text
        print("    [PASSED] filename save renames atomically, rejects collisions/traversal, updates metadata.")
    finally:
        ctx.close()


def test_ui_wiring():
    print("--> Testing Staging intervention badge/filter/Fix + Filename UI...")
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    groups = open(os.path.join(BACKEND_DIR, "app", "staging_groups.py"), encoding="utf-8").read()
    pipeline_src = open(os.path.join(BACKEND_DIR, "app", "tagger", "pipeline.py"), encoding="utf-8").read()
    for needle in ("staging-intervention-filter", "Needs naming intervention", "need naming intervention",
                   "Fix naming", "data-suggested-filename", "tag-field-filename", "new_filename",
                   "filterStagingIntervention"):
        assert needle in page, needle
    assert "needs_intervention" in groups and "suggested_filename" in groups
    assert "VOLUME_NAME_INHERITED" in pipeline_src and "build_previous_volume_index" in pipeline_src
    print("    [PASSED] group badge/filter/Fix, filename modal/save, pipeline inheritance wiring present.")


def main():
    print("=" * 78)
    print("Running previous-volume naming suite (task-69)")
    print("=" * 78)
    tests = [test_replacement_rules, test_assessment_and_groups, test_pipeline_inheritance_and_fallback,
             test_manual_fix_endpoint, test_ui_wiring]
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
        print(f"PREVIOUS-VOLUME NAMING SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL PREVIOUS-VOLUME NAMING TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
