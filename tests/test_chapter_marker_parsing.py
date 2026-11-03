#!/usr/bin/env python3
"""task-17 suite: chapter markers without a separator (Ch1-50), chapter ranges,
trailing format-word series strip, and format-word release groups.

Reported titles:
    Solo Leveling Manga Ch1-50 (Webtoon, CBZ)
    Solo Leveling Manga Ch51-100 (Webtoon, CBZ)
Before the fix these parsed as volume BUNDLES of 50 phantom volumes with the series
key "Solo Leveling Ch".

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.parser import (  # noqa: E402
    extract_manga_bundle_and_volumes as parse_title,
    extract_release_groups,
    is_chapter_only,
)
from test_chapter_guard_and_description_cleanup import _IngestContext, _release  # noqa: E402

SOLO = "Solo Leveling Manga {} (Webtoon, CBZ)"


def _p(title):
    return parse_title(title)


# ===========================================================================
# A. No-separator chapter markers are chapters, not volume bundles
# ===========================================================================
def test_no_separator_chapter_markers():
    print("--> Testing no-separator chapter markers (Ch1-50, Chapter1-50, ...)...")
    expected = [
        ("Ch1-50", 1.0, 50.0),
        ("Ch51-100", 51.0, 100.0),
        ("Ch.1-50", 1.0, 50.0),
        ("Ch. 1-50", 1.0, 50.0),
        ("Chapter1-50", 1.0, 50.0),
        ("Chapter 1-50", 1.0, 50.0),
        ("c1-50", 1.0, 50.0),
        ("#1-50", 1.0, 50.0),
    ]
    for marker, start, end in expected:
        parsed = _p(SOLO.format(marker))
        assert parsed["series_name"] == "Solo Leveling", (marker, parsed["series_name"])
        assert parsed["volumes"] == [], (marker, parsed["volumes"])
        assert parsed["is_bundle"] is False, (marker, parsed["is_bundle"])
        assert parsed["is_chapter"] is True, (marker, parsed["is_chapter"])
        assert parsed["chapter_ranges"] == [[start, end]], (marker, parsed["chapter_ranges"])
        assert parsed["chapter_start"] == start and parsed["chapter_end"] == end, marker
        assert parsed["release_groups"] == [], (marker, parsed["release_groups"])
        assert is_chapter_only(SOLO.format(marker)) is True, marker
    print("    [PASSED] every no-separator marker form is a chapter range, 0 volumes.")


# ===========================================================================
# B. Spaced markers now record chapter_ranges too
# ===========================================================================
def test_spaced_marker_ranges_recorded():
    print("--> Testing spaced chapter markers record their range (defect B)...")
    for marker in ("Ch 1-50", "Ch. 1-50", "Chapter 1-50", "c1-50", "#1-50"):
        parsed = _p(SOLO.format(marker))
        assert parsed["chapter_ranges"] == [[1.0, 50.0]], (marker, parsed["chapter_ranges"])
        assert parsed["chapter_start"] == 1.0 and parsed["chapter_end"] == 50.0, marker
        assert parsed["chapter_number"] == 1.0, (marker, parsed["chapter_number"])
    # A single chapter keeps its number and has no range.
    single = _p("Chainsaw Man Ch. 180")
    assert single["chapter_number"] == 180.0, single["chapter_number"]
    assert single["chapter_ranges"] == [], single["chapter_ranges"]
    print("    [PASSED] ranges captured for spaced forms; single chapter unchanged.")


# ===========================================================================
# C. Volume regressions (the bundle path must still work)
# ===========================================================================
def test_volume_regressions():
    print("--> Testing volume bundle regressions...")
    for title in ("Some Series Vol 1-50", "Some Series Volume 1-50",
                  "Some Series Vols 1-50", "Some Series v01-50"):
        parsed = _p(title)
        assert parsed["series_name"] == "Some Series", (title, parsed["series_name"])
        assert parsed["volumes"] == [float(v) for v in range(1, 51)], (title, len(parsed["volumes"]))
        assert parsed["is_bundle"] is True, (title, parsed["is_bundle"])
        assert parsed["is_chapter"] is False, (title, parsed["is_chapter"])
    danmachi = _p("DanMachi 1-4")
    assert danmachi["series_name"] == "DanMachi", danmachi["series_name"]
    assert danmachi["volumes"] == [1.0, 2.0, 3.0, 4.0], danmachi["volumes"]
    assert danmachi["is_bundle"] is True and danmachi["is_chapter"] is False, danmachi
    # A chapter marker after a real volume keyword still keeps the volume reading.
    mixed = _p("Some Series Vol 1-4 Ch 1-10")
    assert mixed["volumes"] == [1.0, 2.0, 3.0, 4.0], mixed["volumes"]
    print("    [PASSED] Vol/Volume/Vols/v01 and bare-range bundles unchanged.")


# ===========================================================================
# D1. Trailing format descriptor is stripped from the series key
# ===========================================================================
def test_series_format_suffix_stripped():
    print("--> Testing trailing format-word series strip (defect C)...")
    cases = [
        ("Solo Leveling Manga Ch1-50 (Webtoon, CBZ)", "Solo Leveling"),
        ("Solo Leveling Webtoon Ch1-50", "Solo Leveling"),
        ("Solo Leveling Manhwa Vol 1-5", "Solo Leveling"),
        ("Solo Leveling Comic 1-4", "Solo Leveling"),
    ]
    for title, expected in cases:
        parsed = _p(title)
        assert parsed["series_name"] == expected, (title, parsed["series_name"])
    # "Novel" is deliberately NOT a format descriptor (spec §9.2 keeps it).
    novel = _p("Some Brand New Novel")
    assert novel["series_name"] == "Some Brand New Novel", novel["series_name"]
    novel_bundle = _p("Some Brand New Novel Vol 1-2")
    assert novel_bundle["series_name"] == "Some Brand New Novel", novel_bundle["series_name"]
    assert novel_bundle["volumes"] == [1.0, 2.0], novel_bundle["volumes"]
    print("    [PASSED] trailing Manga/Webtoon/Manhwa/Comic stripped; Novel preserved.")


# ===========================================================================
# D2. Format-word blocks are never release groups
# ===========================================================================
def test_format_word_groups_filtered():
    print("--> Testing format-word release-group filtering (defect D)...")
    for title in (
        "Solo Leveling Manga Ch1-50 (Webtoon, CBZ)",
        "Solo Leveling Ch1-50 (Manhwa, CBZ)",
        "Solo Leveling Ch1-50 (Webtoon & CBZ)",
        "Solo Leveling Ch1-50 (Digital, CBZ)",
        "Solo Leveling Ch1-50 (Webtoon)",
    ):
        parsed = _p(title)
        assert parsed["release_groups"] == [], (title, parsed["release_groups"])
        assert extract_release_groups(title) == [], (title, extract_release_groups(title))
    # Real joint tags must keep working.
    assert extract_release_groups("X v01 (1r0n, Ushi)") == ["1r0n", "Ushi"]
    assert extract_release_groups("X v01 (Aku-Tenshi & SnoopyCool)") == ["Aku-Tenshi", "SnoopyCool"]
    assert extract_release_groups("X v01 (Rillant + Aquila + DigitalMangaFan)") == [
        "Rillant", "Aquila", "DigitalMangaFan",
    ]
    assert extract_release_groups("X v01 (K-MANGA) (Webtoon, CBZ) (Rillant, Aquila)") == [
        "Rillant", "Aquila",
    ]
    print("    [PASSED] all-format blocks yield no group; real joint tags still split.")


# ===========================================================================
# E. Scheduler discard path: known series -> discarded + audited
# ===========================================================================
def test_scheduler_discards_known_series_ranges():
    print("--> Testing scheduler discard + audit for a known series' chapter range...")
    ctx = _IngestContext({"Solo Leveling": [1.0, 2.0, 3.0]})
    try:
        first = ctx.poll(_release(SOLO.format("Ch1-50"), "chapter-range-1"))
        second = ctx.poll(_release(SOLO.format("Ch51-100"), "chapter-range-2"))
        assert first["releases_found"] == 0, first
        assert second["releases_found"] == 0, second
        assert ctx.releases() == [], ctx.releases()
        discards = ctx.discards()
        titles = " ".join(row.get("message", "") for row in discards)
        assert "Ch1-50" in titles, discards
        assert "Ch51-100" in titles, discards
        details = " ".join(row.get("details", "") or "" for row in discards)
        assert "explicit chapter range" in details, details
        assert "Solo Leveling" in details, details
        # Attributed to the background scheduler, not a user.
        assert all(row.get("actor") == "scheduler" for row in discards), discards
        print("    [PASSED] known-series chapter batches discarded with CHAPTER_DISCARDED audit.")
    finally:
        ctx.close()


# ===========================================================================
# F. Unknown series: still silently discarded (pre-existing explicit policy)
# ===========================================================================
def test_scheduler_unknown_series_keeps_silent_policy():
    print("--> Testing unknown-series explicit chapters keep the silent policy...")
    ctx = _IngestContext({})
    try:
        result = ctx.poll(_release(SOLO.format("Ch1-50"), "chapter-range-unknown"))
        assert result["releases_found"] == 0, result
        assert ctx.releases() == [], ctx.releases()
        # Single explicit chapters / unknown series never produced this event before;
        # only the known-series range batch is audited.
        assert ctx.discards() == [], ctx.discards()
        print("    [PASSED] unknown series: discarded as before, no new audit event.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running chapter-marker parsing suite (task-17)")
    print("=" * 78)
    tests = [
        test_no_separator_chapter_markers,
        test_spaced_marker_ranges_recorded,
        test_volume_regressions,
        test_series_format_suffix_stripped,
        test_format_word_groups_filtered,
        test_scheduler_discards_known_series_ranges,
        test_scheduler_unknown_series_keeps_silent_policy,
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
        print(f"CHAPTER-MARKER SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL CHAPTER-MARKER TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
