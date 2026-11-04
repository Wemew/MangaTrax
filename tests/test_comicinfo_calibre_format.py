#!/usr/bin/env python3
"""task-38 suite: Calibre-format ComicInfo parity (title/authors/tags/volume + audit).

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.parser import extract_manga_bundle_and_volumes, normalize_text  # noqa: E402
from app.tagger import cbz_tagger, pipeline as downloads_pipeline, series_audit  # noqa: E402
from app.tagger.naming import (  # noqa: E402
    format_authors,
    format_comicinfo_title,
    format_publisher,
    format_tags,
    format_volume,
)
from test_downloads_routing import KNOWN_TEMPLATE, Ctx, _make_cbz  # noqa: E402

MATRIX_TEMPLATE = {
    "known series": {
        "series_name": "Known Series",
        "authors": "Reki Kawahara,Isll",
        "publisher": "Yen Press",
        "tags": "Action,Comedy",
    },
}


def _audit_reference():
    return {
        "names": {normalize_text("Known Series"): "Known Series"},
        "templates": {"known series": MATRIX_TEMPLATE["known series"]},
        "aliases": {},
    }


def _make_xml_cbz(path, extra=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Title>Known Series Vol. 28</Title>
  <Series>Known Series</Series>
  <Number>28.0</Number>
  <Volume>28.0</Volume>
  <Writer>Reki Kawahara,Isll</Writer>
  <Penciller>Reki Kawahara,Isll</Penciller>
  <Publisher>Yen Press</Publisher>
  <Genre>Action,Comedy</Genre>
  <Manga>YesAndRightToLeft</Manga>
  <Pages>42</Pages>
  {extra}
</ComicInfo>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("001.jpg", b"image")
        archive.writestr("ComicInfo.xml", xml)


# ===========================================================================
# 1. Title matrix
# ===========================================================================
def test_title_matrix():
    print("--> Testing title formatter matrix...")
    assert format_comicinfo_title("A Couple of Cuckoos", volume_start=28) == "A Couple of Cuckoos v28"
    assert format_comicinfo_title("Series", volume_start=1) == "Series v01"
    assert format_comicinfo_title("Series", volume_start=9) == "Series v09"
    assert format_comicinfo_title("Series", volume_start=10) == "Series v10"
    assert format_comicinfo_title("Series", volume_start=100) == "Series v100"
    assert format_comicinfo_title("Series", volumes=[1, 2, 3]) == "Series v01-03"
    assert format_comicinfo_title("Series", volumes=[1, 3]) == "Series v01, v03"
    assert format_comicinfo_title("Series", volume=1.5) == "Series v1.5"
    assert format_comicinfo_title("Series") == "Series"
    # Missing series: parsed series name, then filename stem; never date/group/size.
    fallback = format_comicinfo_title(
        "", volume_start=10,
        fallback_name="Wistoria - Wand & Sword v10 (2026) (Digital) [Group] (120MB).cbz",
    )
    assert fallback.startswith("Wistoria - Wand & Sword v10"), fallback
    for token in ("2026", "Digital", "Group", "120MB", "Vol."):
        assert token not in fallback, (token, fallback)
    stem_fallback = format_comicinfo_title("", fallback_name="just-a-file.cbz")
    assert stem_fallback == "just-a-file", stem_fallback
    print("    [PASSED] v01/v09/v10/v28/v100, bundles, gaps, decimals, fallbacks; no stray tokens.")


# ===========================================================================
# 2. Authors / tags / volume / publisher formatters
# ===========================================================================
def test_element_formatters():
    print("--> Testing author/tag/volume/publisher formatters...")
    cases = {
        "Reki Kawahara,Isll": "Reki Kawahara & Isll",
        "Reki Kawahara; Isll": "Reki Kawahara & Isll",
        "Reki Kawahara & Isll": "Reki Kawahara & Isll",
        "  Reki   Kawahara ,  Isll , Reki Kawahara ": "Reki Kawahara & Isll",
        "Solo Author": "Solo Author",
        "": "",
    }
    for raw, expected in cases.items():
        actual = format_authors(raw)
        assert actual == expected, (raw, actual, expected)
    assert format_tags("Action,Comedy; Action , Romance ") == "Action, Comedy, Romance"
    assert format_tags("Action") == "Action"
    assert format_tags("") == ""
    assert format_volume("28.0") == "28"
    assert format_volume("1.50") == "1.5"
    assert format_volume("1-3") == "1-3"
    assert format_volume("01-03") == "1-3"
    assert format_volume(28) == "28"
    assert format_volume("") == ""
    assert format_publisher("  Yen Press  ") == "Yen Press"
    print("    [PASSED] authors use ' & ', tags use ', ', volumes Calibre-style, publisher exact.")


# ===========================================================================
# 3. XML output carries Calibre formats and keeps non-Calibre extras
# ===========================================================================
def test_xml_output():
    print("--> Testing generated ComicInfo.xml elements...")
    xml = cbz_tagger.generate_comicinfo_xml({
        "title": "Known Series v28", "series_name": "Known Series", "volume": "28.0",
        "authors": "Reki Kawahara,Isll", "publisher": "Yen Press", "tags": "Action,Comedy",
    })
    for needle in (
        "<Title>Known Series v28</Title>",
        "<Series>Known Series</Series>",
        "<Number>28</Number>",
        "<Volume>28</Volume>",
        "<Writer>Reki Kawahara &amp; Isll</Writer>",
        "<Penciller>Reki Kawahara &amp; Isll</Penciller>",
        "<Publisher>Yen Press</Publisher>",
        "<Genre>Action, Comedy</Genre>",
        "<Manga>YesAndRightToLeft</Manga>",
    ):
        assert needle in xml, (needle, xml)
    assert "Vol." not in xml, xml
    print("    [PASSED] XML has Calibre title/authors/tags/volume and preserves Manga extra.")


# ===========================================================================
# 4. Tagger and pipeline produce identical metadata for the same tree
# ===========================================================================
def test_tagger_and_pipeline_identical():
    print("--> Testing tagger/pipeline parity on the same CBZ...")
    ctx = Ctx()
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_parity_")
    try:
        source = os.path.join(tmpdir, "Known Series v03.cbz")
        _make_cbz(source)
        routed_input = os.path.join(ctx.downloads, "Known Series v03.cbz")
        shutil.copyfile(source, routed_input)
        auto_dir = os.path.join(tmpdir, "auto")
        os.makedirs(auto_dir)
        shutil.copyfile(source, os.path.join(auto_dir, "Known Series v03.cbz"))
        ctx.run(templates=MATRIX_TEMPLATE)
        auto_tagged = cbz_tagger.auto_tag_staging_cbz_files(auto_dir, MATRIX_TEMPLATE)
        assert auto_tagged, auto_tagged
        routed_fields = cbz_tagger.read_comicinfo(
            os.path.join(ctx.staging, "Known Series v03.cbz"),
        )
        auto_fields = cbz_tagger.read_comicinfo(os.path.join(auto_dir, "Known Series v03.cbz"))
        for key in ("Title", "Series", "Number", "Volume", "Writer", "Penciller", "Publisher", "Genre"):
            assert routed_fields.get(key) == auto_fields.get(key), (key, routed_fields, auto_fields)
        assert routed_fields["Title"] == "Known Series v03", routed_fields
        assert routed_fields["Writer"] == "Reki Kawahara & Isll", routed_fields
        assert routed_fields["Genre"] == "Action, Comedy", routed_fields
        print("    [PASSED] pipeline and tagger write identical Calibre-formatted ComicInfo.xml.")
    finally:
        ctx.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 5. Audit detects/repairs every overlapping element; extras preserved
# ===========================================================================
def test_audit_and_repair():
    print("--> Testing audit/repair for title/authors/tags/volume...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_audit38_")
    try:
        path = os.path.join(tmpdir, "Known Series v28.cbz")
        _make_xml_cbz(path)
        entry = {"path": path, "name": "Known Series v28.cbz", "relative": "Known Series v28.cbz"}
        record = series_audit.audit_entry(entry, _audit_reference())
        assert record["status"] == series_audit.STATUS_REPAIRABLE, record
        assert record["changes"] == ["Title", "Writer", "Penciller", "Genre", "Number", "Volume"], record
        assert record["suggested_title"] == "Known Series v28", record
        assert record["suggested_writer"] == "Reki Kawahara & Isll", record
        assert record["suggested_penciller"] == "Reki Kawahara & Isll", record
        assert record["suggested_genre"] == "Action, Comedy", record
        assert record["suggested_number"] == "28" and record["suggested_volume"] == "28", record
        for note in ("Calibre title", "' & '", "comma-space", "series-index"):
            assert note in record["reason"], (note, record["reason"])

        plan = series_audit.plan_repair(record)
        assert plan["updates"] == {
            "Title": "Known Series v28", "Writer": "Reki Kawahara & Isll",
            "Penciller": "Reki Kawahara & Isll", "Genre": "Action, Comedy",
            "Number": "28", "Volume": "28",
        }, plan
        existing = cbz_tagger.read_comicinfo_xml(path)
        new_xml = cbz_tagger.build_comicinfo_xml(existing, plan["updates"])
        assert cbz_tagger.rewrite_comicinfo(path, new_xml), "rewrite failed"
        fields = cbz_tagger.read_comicinfo(path)
        assert fields["Title"] == "Known Series v28", fields
        assert fields["Writer"] == "Reki Kawahara & Isll", fields
        assert fields["Genre"] == "Action, Comedy", fields
        assert fields["Manga"] == "YesAndRightToLeft", fields
        assert fields["Pages"] == "42", fields

        # Correct file stays ok.
        _make_xml_cbz(path)
        correct_xml = cbz_tagger.build_comicinfo_xml(
            cbz_tagger.read_comicinfo_xml(path),
            {"Title": "Known Series v28", "Writer": "Reki Kawahara & Isll",
             "Penciller": "Reki Kawahara & Isll", "Genre": "Action, Comedy",
             "Number": "28", "Volume": "28"},
        )
        cbz_tagger.rewrite_comicinfo(path, correct_xml)
        okay = series_audit.audit_entry(entry, _audit_reference())
        assert okay["status"] == series_audit.STATUS_OK and okay["changes"] == [], okay

        # Unparseable/ambiguous volume: report-only, never guessed.
        _make_xml_cbz(path, extra="")
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("ComicInfo.xml", cbz_tagger.build_comicinfo_xml(
                cbz_tagger.read_comicinfo_xml(path), {"Volume": "TBD", "Number": "TBD"},
            ))
        weird = series_audit.audit_entry(entry, _audit_reference())
        assert weird.get("suggested_volume") in (None, "TBD"), weird
        assert "Volume" not in (weird.get("changes") or []), weird
        print("    [PASSED] audit flags/repairs all overlapping elements, preserves extras, never guesses volume.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 6. Audit UI wiring (current → suggested, per-element repair counts)
# ===========================================================================
def test_ui_wiring():
    print("--> Testing audit UI current → suggested + repair counts...")
    template = open(
        os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8",
    ).read()
    for needle in ("auditFieldDiff", "current_title", "suggested_title",
                   "Calibre fields (current", "field_counts"):
        assert needle in template, f"missing audit UI wiring: {needle}"
    main_source = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    assert '"field_counts": field_counts' in main_source
    print("    [PASSED] audit rows show current → suggested and repair toast reports per-element counts.")


def main():
    print("=" * 78)
    print("Running ComicInfo Calibre-format parity suite (task-38)")
    print("=" * 78)
    tests = [
        test_title_matrix,
        test_element_formatters,
        test_xml_output,
        test_tagger_and_pipeline_identical,
        test_audit_and_repair,
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
        print(f"COMICINFO FORMAT SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL COMICINFO CALIBRE-FORMAT TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
