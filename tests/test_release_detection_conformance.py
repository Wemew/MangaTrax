#!/usr/bin/env python3
"""D3 conformance suite: NovelTrax parser guards ported into MangaTrax.

Reference: ``RELEASE_DETECTION_SPEC.md`` §2 (title normalisation), §5 (series +
volume extraction), §6 (Calibre volume truth), §8 (verified vectors) and the
MangaTrax delta audit §9.6-D3.  Each test below maps to one ported guard and was
failing before the port (see the docstrings for the old behaviour).

Also covered here:
* the new "<chapters A-B> as v<X>-<Y>" chapter-range -> volume mapping (a shared
  NovelTrax/MangaTrax conformance gap flagged by the user);
* the release-group fix for compound format tags ("(Digital-Compilation)" is not
  a group; "(Oak)" is);
* the light-novel paren/bracket convention fix ("(Stick)" is manga-style group
  metadata, "[Stick]" is the LN imprint convention), so a genuine manga release
  such as ``Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)`` is no longer
  dropped as a light novel.

MangaTrax policy is deliberately unchanged: chapter data is exposed as
diagnostics (``is_chapter`` / ``chapter_number`` / ``chapter_ranges``) and the
scheduler's context-guarded chapter discard stays in place.

Run through the assert-aware runner (assertions compiled live, optimize=0):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import sys
import tempfile
import shutil
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
for _path in (BACKEND_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.parser import (
    clean_manga_title,
    extract_manga_bundle_and_volumes,
    extract_release_group,
    is_chapter_only,
    is_light_novel,
    is_valid_group_candidate,
    normalize_text,
    parse_calibre_volumes_and_series,
    strip_site_prefix,
)


def _parsed(title):
    return extract_manga_bundle_and_volumes(title)


# ===========================================================================
# 1. Spec §8 conformance vectors (manga-meaningful rows)
# ===========================================================================
# (title, series_name, volumes, is_bundle, is_chapter)
# Deviating rows are documented inline; deviations are intentional MangaTrax
# choices (chapter numbers stay in the series name so the scheduler's context
# guard can see them, and the v01-09 half of a "+ chapters" title is a bundle).
SPEC_VECTORS = [
    ("DanMachi Volume 19", "DanMachi", [19.0], False, False),
    ("Overlord Vol. 16", "Overlord", [16.0], False, False),
    ("The Eminence in Shadow v6", "The Eminence in Shadow", [6.0], False, False),
    ("Dungeon Crawler Carl Book 7", "Dungeon Crawler Carl", [7.0], False, False),
    ("Avatar Korra Turf Wars Part 1", "Avatar Korra Turf Wars", [1.0], False, False),
    (
        "The Tale of a Little Alchemist Blessed by the Spirits v01-04 [Seven Seas] [Stick]",
        "The Tale of a Little Alchemist Blessed by the Spirits", [1.0, 2.0, 3.0, 4.0], True, False,
    ),
    (
        "Now I'm a Demon Lord! Happily Ever After with Monster Girls in My Dungeon "
        "v01-17 [J-Novel Club] [CleanBookGuy] [Complete]",
        "Now I'm a Demon Lord! Happily Ever After with Monster Girls in My Dungeon",
        [float(v) for v in range(1, 18)], True, False,
    ),
    ("[Yen Press] Overlord, Vol. 16 [Digital] [EPUB]", "Overlord", [16.0], False, False),
    ("Naruto Hiden Series (2015) 1-6 [COMPLETE] (EPUB+PDF)", "Naruto Hiden Series",
     [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], True, False),
    # Chapter-only: no volumes invented; the chapter marker is captured as data.
    ("Cultivation Chat Group [Ch. 0001-3165][WebNovel].epub", "Cultivation Chat Group", [], False, True),
    ("Sugaru Miaki's Light Novel Collection (2013-2018) (vgperson)",
     "Sugaru Miaki's Light Novel Collection", [], False, False),
    ("The Irregular at Magic High School - v01-v19 (Digital) (Light Novel) (Mixed)",
     "The Irregular at Magic High School", [float(v) for v in range(1, 20)], True, False),
    ("Anemone wa Netsu o Obiru | The Anemone Feels the Heat v01-03 (2025-2026) (Digital) (1r0n, Anon)",
     "Anemone wa Netsu o Obiru | The Anemone Feels the Heat", [1.0, 2.0, 3.0], True, False),
    ("Reading Our Way to a Happy New World 001-009 as v01-02 (Digital-Compilation) (Oak) [JPEG-XL]",
     "Reading Our Way to a Happy New World", [1.0, 2.0], True, False),
    # MangaTrax keeps the expanded v01-09 range (the spec's reference build returns
    # only [1]); the "+ 047-051" chapter part is ignored, never a volume.
    ("The Summer Hikaru Died v01-09 + 047-051 (2023-2026) (Digital + Digital-Compilation) "
     "(1r0n, Rillant) [Completed]",
     "The Summer Hikaru Died", [float(v) for v in range(1, 10)], True, False),
    # LN-fix regression: a parenthesised group tag is manga metadata, not LN.
    ("Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)",
     "Blue Lock - Episode Nagi", [], False, False),
    ("Skeleton Knight in Another World v01-09 [Audiobook] [Seven Seas Siren] [Stick & Oak]",
     "Skeleton Knight in Another World", [float(v) for v in range(1, 10)], True, False),
    ("The Apothecary Diaries v01-02 [Audiobook] [Tantor Media]",
     "The Apothecary Diaries", [1.0, 2.0], True, False),
    # A bare single number is chapter data (MangaTrax keeps it in the series name so
    # the scheduler's context guard can see it; the spec's parser strips it).
    ("Dandadan 250", "Dandadan 250", [], False, True),
    ("One Piece 1197", "One Piece 1197", [], False, True),
    ("Kaguya-sama 271", "Kaguya-sama 271", [], False, True),
    ("Dandadan - 251", "Dandadan - 251", [], False, True),
    ("Solo Leveling Ch. 179", "Solo Leveling", [], False, True),
    ("Berserk c374", "Berserk", [], False, True),
    ("DanMachi Vols 01-18", "DanMachi", [float(v) for v in range(1, 19)], True, False),
    ("Overlord Vol 1-14", "Overlord", [float(v) for v in range(1, 15)], True, False),
    ("DanMachi Omnibus 1 (Vols 1-3)", "DanMachi Omnibus", [1.0, 2.0, 3.0], True, False),
    ("Mushoku Tensei Vol 1 & 2", "Mushoku Tensei", [1.0, 2.0], True, False),
    ("Overlord Vol 1, 2, 3", "Overlord", [1.0, 2.0, 3.0], True, False),
    ("DanMachi 1-4", "DanMachi", [1.0, 2.0, 3.0, 4.0], True, False),
    ("86 - 1-4", "86", [1.0, 2.0, 3.0, 4.0], True, False),
    ("Mob Psycho 100 - 1-3", "Mob Psycho 100", [1.0, 2.0, 3.0], True, False),
    ("Classroom of the Elite Vol 11.5", "Classroom of the Elite", [11.5], False, False),
    ("Square Enix Collection", "Square Enix Collection", [], False, False),
    ("81diver", "81diver", [], False, False),
    # Deviation: MangaTrax does not strip the standalone format word "Novel"
    # (spec §8 turns this into "Some Brand New"); alias it instead.
    ("Some Brand New Novel", "Some Brand New Novel", [], False, False),
    ("86 - Eighty Six", "86 - Eighty Six", [], False, False),
]


def test_spec8_conformance_vectors():
    print("--> Testing spec §8 conformance vectors (manga-meaningful rows)...")
    for title, series, volumes, is_bundle, is_chapter in SPEC_VECTORS:
        parsed = _parsed(title)
        assert parsed["series_name"] == series, (title, parsed["series_name"], series)
        assert parsed["volumes"] == volumes, (title, parsed["volumes"], volumes)
        assert parsed["is_bundle"] is is_bundle, (title, parsed["is_bundle"], is_bundle)
        assert parsed["is_chapter"] is is_chapter, (title, parsed["is_chapter"], is_chapter)
    print(f"    [PASSED] {len(SPEC_VECTORS)} spec §8 vectors conform.")


# ===========================================================================
# 2. Guard: bare numeric range -> bundle (old behaviour: no volumes / chapter)
# ===========================================================================
def test_bare_numeric_range_bundle_guard():
    print("--> Testing guard: bare numeric range -> bundle...")
    # Spec §5 Pattern 1b: "a range cannot be a chapter".
    for title, series, expected in [
        ("DanMachi 1-4", "DanMachi", [1.0, 2.0, 3.0, 4.0]),
        ("86 - 1-4", "86", [1.0, 2.0, 3.0, 4.0]),
        ("Mob Psycho 100 - 1-3", "Mob Psycho 100", [1.0, 2.0, 3.0]),
    ]:
        parsed = _parsed(title)
        assert parsed["series_name"] == series, (title, parsed["series_name"])
        assert parsed["volumes"] == expected, (title, parsed["volumes"])
        assert parsed["is_bundle"] is True, title
        assert parsed["is_chapter"] is False, title
        assert is_chapter_only(title) is False, title

    # Guards: series non-empty, 0 < start <= 300, start <= end <= 300, span <= 100.
    # Anything failing them falls through to the existing chapter policy.
    rejected = [
        "DanMachi 0-4",        # start must be > 0
        "DanMachi 1-400",      # end must be <= 300
        "DanMachi 1-200",      # span must be <= 100
        "DanMachi 301-302",    # start must be <= 300
    ]
    for title in rejected:
        parsed = _parsed(title)
        assert parsed["volumes"] == [], (title, parsed["volumes"])
        assert parsed["is_bundle"] is False, title
        assert parsed["is_chapter"] is True, title
        assert is_chapter_only(title) is True, title

    # Zero-padded numbers are chapter notation in manga feeds ("Series 001-009"
    # must not invent a volume mapping); bare SINGLE numbers stay chapters too.
    for title in ["DanMachi 01-04", "Series 001-009", "Mob Psycho 100 - 01", "DanMachi 4"]:
        parsed = _parsed(title)
        assert parsed["volumes"] == [], (title, parsed["volumes"])
        assert parsed["is_chapter"] is True, title
    print("    [PASSED] bare ranges become bundles; guards and chapter notation preserved.")


# ===========================================================================
# 3. Guard: parenthesised omnibus range after an edition number
# ===========================================================================
def test_parenthesised_omnibus_range():
    print("--> Testing guard: parenthesised omnibus range...")
    parsed = _parsed("DanMachi Omnibus 1 (Vols 1-3)")
    # The edition number ("1") stays out of the series name; the range expands.
    assert parsed["series_name"] == "DanMachi Omnibus", parsed["series_name"]
    assert parsed["volumes"] == [1.0, 2.0, 3.0], parsed["volumes"]
    assert parsed["is_bundle"] is True
    # Singular form still works (existing behaviour preserved).
    parsed_single = _parsed("DanMachi Omnibus 2 (Vol 4-6)")
    assert parsed_single["series_name"] == "DanMachi Omnibus", parsed_single["series_name"]
    assert parsed_single["volumes"] == [4.0, 5.0, 6.0], parsed_single["volumes"]
    print("    [PASSED] 'Omnibus 1 (Vols 1-3)' -> series 'Omnibus', volumes 1-3.")


# ===========================================================================
# 4. Guard: plural keywords and Oxford-comma lists
# ===========================================================================
def test_plural_keywords_and_oxford_lists():
    print("--> Testing guard: plural keywords + Oxford-comma lists...")
    for title, expected in [
        ("DanMachi Vols 01-18", [float(v) for v in range(1, 19)]),
        ("Overlord Volumes 1-14", [float(v) for v in range(1, 15)]),
        ("Overlord Vol 1-14", [float(v) for v in range(1, 15)]),  # unaffected singular
    ]:
        parsed = _parsed(title)
        assert parsed["series_name"] == ("DanMachi" if "DanMachi" in title else "Overlord"), title
        assert parsed["volumes"] == expected, (title, parsed["volumes"])
        assert parsed["is_bundle"] is True, title
    # Oxford comma: "Vol 1, 2, and 3" (previously the whole list pattern failed and
    # the title fell through completely).
    for title in ["Overlord Vol 1, 2, and 3", "Overlord Vol 1, 2, 3", "Overlord Vol 1 & 2 & 3"]:
        parsed = _parsed(title)
        assert parsed["series_name"] == "Overlord", (title, parsed["series_name"])
        assert parsed["volumes"] == [1.0, 2.0, 3.0], (title, parsed["volumes"])
        assert parsed["is_bundle"] is True, title
    # "Vols" inside a bracket must survive bracket triage and still parse.
    parsed_bracket = _parsed("DanMachi [Vols 1-3]")
    assert parsed_bracket["volumes"] == [1.0, 2.0, 3.0], parsed_bracket["volumes"]
    print("    [PASSED] plural keywords and Oxford-comma lists parse as bundles.")


# ===========================================================================
# 5. Guard: leading site prefixes (read/get need a separator - the Reading trap)
# ===========================================================================
def test_site_prefix_guard():
    print("--> Testing guard: leading site-prefix stripping...")
    assert clean_manga_title("Download: Overlord Vol. 1") == "Overlord Vol. 1"
    assert clean_manga_title("Free Download Overlord Vol. 1") == "Overlord Vol. 1"
    assert clean_manga_title("Read: Overlord Vol. 2") == "Overlord Vol. 2"
    assert clean_manga_title("Get - Overlord Vol. 3") == "Overlord Vol. 3"
    assert _parsed("Download: Overlord Vol. 1")["series_name"] == "Overlord"

    # The regression this guard exists for: a naive rule turned
    # "Reading Our Way to a Happy New World" into "ing Our Way to a Happy New World".
    assert strip_site_prefix("Reading Our Way to a Happy New World") == "Reading Our Way to a Happy New World"
    assert clean_manga_title("Reading Our Way to a Happy New World Vol. 1") == (
        "Reading Our Way to a Happy New World Vol. 1"
    )
    parsed = _parsed("Reading Our Way to a Happy New World")
    assert parsed["series_name"] == "Reading Our Way to a Happy New World", parsed["series_name"]
    assert parsed["volumes"] == [], parsed["volumes"]
    # Same class of word: "Getting the Boot" keeps its first word.
    assert clean_manga_title("Getting the Boot Vol. 1") == "Getting the Boot Vol. 1"
    # "download" without a separator is still stripped (unlike read/get).
    assert clean_manga_title("Download Overlord Vol. 1") == "Overlord Vol. 1"
    print("    [PASSED] prefixes stripped; 'Reading Our Way...' keeps its first word.")


# ===========================================================================
# 6. NEW contract: "<chapters A-B> as v<X>-<Y> [+ <chapters C-D>]"
# ===========================================================================
def test_chapter_range_as_volume_mapping():
    print("--> Testing chapter-range -> volume mapping ('001-009 as v01-02')...")
    full = (
        "Reading Our Way to a Happy New World 001-009 as v01-02 + 010-012 "
        "(Digital-Compilation) (Oak)"
    )
    parsed = _parsed(full)
    # Rule 1: the whole clause (chapter range + "as" + volume token + trailing
    # chapter clause) is removed from the series name.
    assert parsed["series_name"] == "Reading Our Way to a Happy New World", parsed["series_name"]
    # Rule 2: volumes come from the v half -- chapters 1-9 are volumes 1-2.
    assert parsed["volumes"] == [1.0, 2.0], parsed["volumes"]
    assert parsed["volume_start"] == 1.0 and parsed["volume_end"] == 2.0, parsed
    assert parsed["is_bundle"] is True, parsed
    # Rule 4: chapter ranges retained as data; the release DOES yield volumes.
    assert parsed["is_chapter"] is False, parsed
    assert parsed["chapter_ranges"] == [[1.0, 9.0], [10.0, 12.0]], parsed["chapter_ranges"]
    assert parsed["chapter_start"] == 1.0 and parsed["chapter_end"] == 12.0, parsed
    # Rule 3 + group fix: trailing chapter clause is never volumes; (Oak) is the group.
    assert parsed["release_group"] == "Oak", parsed["release_group"]
    assert "Digital-Compilation" not in (parsed["release_group"] or "")

    single = _parsed("Some Novel 001-009 as v01-02")
    assert single["series_name"] == "Some Novel", single["series_name"]
    assert single["volumes"] == [1.0, 2.0], single["volumes"]
    assert single["chapter_ranges"] == [[1.0, 9.0]], single["chapter_ranges"]
    assert single["release_group"] is None, single["release_group"]

    single_volume = _parsed("Series 010-012 as v03")
    assert single_volume["series_name"] == "Series", single_volume["series_name"]
    assert single_volume["volumes"] == [3.0], single_volume["volumes"]
    assert single_volume["is_bundle"] is False, single_volume
    assert single_volume["chapter_ranges"] == [[10.0, 12.0]], single_volume["chapter_ranges"]

    # Unmapped chapter range: must NOT invent volumes (existing chapter policy).
    unmapped = _parsed("Series 001-009")
    assert unmapped["volumes"] == [], unmapped["volumes"]
    assert unmapped["is_chapter"] is True, unmapped
    assert is_chapter_only("Series 001-009") is True

    # Guard: requires both an explicit "as" and an explicit volume token.  The
    # ordinary title and the plain "Vol 1-2" form are untouched.
    assert _parsed("Reading Our Way to a Happy New World Vol 1-2")["series_name"] == (
        "Reading Our Way to a Happy New World"
    )
    no_chapter_range = _parsed("Some Series as v01")
    assert no_chapter_range["chapter_ranges"] == [], "no chapter range -> not the idiom"
    assert no_chapter_range["is_chapter"] is False, no_chapter_range
    assert no_chapter_range["volumes"] == [1.0], no_chapter_range["volumes"]
    assert _parsed("Some Series 001-009")["series_name"] == "Some Series 001-009"
    print("    [PASSED] chapter ranges map onto the volume range only when explicitly stated.")


# ===========================================================================
# 7. Release-group fix: compound format tags are not groups
# ===========================================================================
def test_compound_format_tag_is_not_a_release_group():
    print("--> Testing release-group fix: (Digital-Compilation) is not a group...")
    title = (
        "Reading Our Way to a Happy New World 001-009 as v01-02 + 010-012 "
        "(Digital-Compilation) (Oak)"
    )
    assert extract_release_group(title) == "Oak", extract_release_group(title)
    assert is_valid_group_candidate("Digital-Compilation") is False
    assert is_valid_group_candidate("Compilation") is False
    assert is_valid_group_candidate("digital compilation") is False
    # Genuine groups/publishers stay valid (no weakening of existing protections).
    for good in ["danke-Empire", "Lu自主规制", "1r0n, Anon", "Stick", "Yen Press", "Oak"]:
        assert is_valid_group_candidate(good) is True, good
    assert extract_release_group("[danke-Empire] Bleach v01-v20 [Digital] [1080p] [CBZ]") == "danke-Empire"
    assert extract_release_group("[Lu自主规制] Chainsaw Man Vol. 01-11 [Hui]") == "Lu自主规制"
    assert extract_release_group("[Manga] Frieren Vol. 01-10 [danke-Empire] [Completed]") == "danke-Empire"
    assert extract_release_group(
        "Anemone wa Netsu o Obiru v01-03 (2025-2026) (Digital) (1r0n, Anon)"
    ) == "1r0n, Anon"
    # A preserved volume range bracket is not a group either.
    assert extract_release_group("DanMachi Omnibus 1 (Vols 1-3)") is None
    print("    [PASSED] 'Oak' wins; Digital-Compilation rejected; real groups intact.")


# ===========================================================================
# 8. Guard: Calibre reversed-range (only with an explicit volume prefix)
# ===========================================================================
def test_calibre_reversed_range_guard():
    print("--> Testing guard: Calibre reversed-range guard...")
    # Explicit prefix: "Vol. 3-1" is a (reversed) range and is normalised.
    series, vols = parse_calibre_volumes_and_series("Bleach Vol. 3-1", "Bleach", None)
    assert series == "Bleach", series
    assert vols == {1.0, 2.0, 3.0}, vols
    # No prefix: a bare reversed pair is NOT a range and adds nothing.
    series, vols = parse_calibre_volumes_and_series("Title 3-1", None, None)
    assert vols == set(), vols
    # Bare ascending range without prefix is still accepted (spec §6 rule 4).
    series, vols = parse_calibre_volumes_and_series("Title 1-4", "Title", None)
    assert vols == {1.0, 2.0, 3.0, 4.0}, vols
    print("    [PASSED] reversed ranges require an explicit volume prefix.")


# ===========================================================================
# 9. Guard: Calibre bracket-delimiter guard ("Title [100 - Vol 1]")
# ===========================================================================
def test_calibre_bracket_delimiter_guard():
    print("--> Testing guard: Calibre bracket-delimiter guard...")
    # Must not become the range 100-1 (or 1-100): only the explicit Vol 1 counts.
    series, vols = parse_calibre_volumes_and_series("Title [100 - Vol 1]", "Title", None)
    assert series == "Title", series
    assert vols == {1.0}, vols
    assert 100.0 not in vols, vols
    # Before the port this bracket pair was read as a swapped range and invented
    # volumes 1..47 from a delimiter that separates a chapter number from a volume
    # tag; now only the explicit volume tag counts.
    series, vols = parse_calibre_volumes_and_series("Title [47 - Vol 1]", "Title", None)
    assert vols == {1.0}, vols
    series, vols = parse_calibre_volumes_and_series("Title [40 - Vol 30]", "Title", None)
    assert vols == {30.0}, vols
    # A real bracketed range still expands.
    series, vols = parse_calibre_volumes_and_series("Title [Vol 1-3]", "Title", None)
    assert vols == {1.0, 2.0, 3.0}, vols
    # Existing omnibus behaviour is unchanged.
    series, vols = parse_calibre_volumes_and_series(
        "Aharen-san is Indecipherable v01-02", "Aharen-san is Indecipherable", 1.0
    )
    assert vols == {1.0, 2.0}, vols
    # Oxford list in a Calibre book title.
    series, vols = parse_calibre_volumes_and_series("Overlord Vol 1, 2, and 3", "Overlord", None)
    assert vols == {1.0, 2.0, 3.0}, vols
    # Trailing bare range must not truncate a series whose name ends in a number.
    series, vols = parse_calibre_volumes_and_series("Mob Psycho 100 - 1-3", None, None)
    assert series == "Mob Psycho 100", series
    assert vols == {1.0, 2.0, 3.0}, vols
    print("    [PASSED] '[100 - Vol 1]' is not a range; real ranges still expand.")


# ===========================================================================
# 10. Chapter diagnostics are exposed as data (policy untouched)
# ===========================================================================
def test_chapter_diagnostics_exposed():
    print("--> Testing chapter diagnostics (is_chapter / chapter_number)...")
    cases = [
        ("Dandadan 250", True, 250.0),
        ("Dandadan - 251", True, 251.0),
        ("Solo Leveling Ch. 179", True, 179.0),
        ("Berserk c374", True, 374.0),
        ("One Piece #1080", True, 1080.0),
        # Spec Pattern 2 would read this as chapter 100; MangaTrax's context guard
        # is what protects the real series name at ingestion time.
        ("Mob Psycho 100", True, 100.0),
        # Volume releases are never chapters.
        ("Chainsaw Man Vol. 180", False, None),
        ("20th Century Boys", False, None),
        ("7th Time Loop", False, None),
    ]
    for title, expected_flag, expected_number in cases:
        parsed = _parsed(title)
        assert parsed["is_chapter"] is expected_flag, (title, parsed["is_chapter"])
        assert parsed["chapter_number"] == expected_number, (title, parsed["chapter_number"])
    # The chapter-as-volume idiom yields volumes -> not a chapter, ranges as data.
    parsed = _parsed("Reading Our Way to a Happy New World 001-009 as v01-02")
    assert parsed["is_chapter"] is False
    assert parsed["chapter_ranges"] == [[1.0, 9.0]]
    # is_chapter_only() itself is unchanged for explicit markers.
    assert is_chapter_only("Chainsaw Man Ch. 180") is True
    assert is_chapter_only("Chainsaw Man Vol. 180") is False
    print("    [PASSED] chapter data exposed without changing the discard policy.")


# ===========================================================================
# 11. Light-novel paren/bracket convention (false-negative drop fixed)
# ===========================================================================
def test_light_novel_paren_bracket_convention():
    print("--> Testing LN convention: parenthesised group tags are manga metadata...")
    # The reported false negative: a genuine manga release was dropped as an LN.
    blue_lock = "Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)"
    assert is_light_novel(blue_lock) is False, "parenthesised (Stick) must not mean light novel"
    parsed = _parsed(blue_lock)
    assert parsed["series_name"] == "Blue Lock - Episode Nagi", parsed["series_name"]
    assert parsed["is_light_novel"] is False, parsed
    # Bracket convention: [Stick] is the LN convention and stays decisive.
    assert is_light_novel("Blue Lock - Episode Nagi [Stick]") is True
    assert is_light_novel("Blue Lock [Stick] (Digital)") is True
    # Imprints: "[Yen On]" decisive, "(Yen On)" is manga-style metadata (not decisive).
    assert is_light_novel("Overlord v01-14 [Yen On]") is True
    assert is_light_novel("Overlord v01-14 (Yen On)") is False
    # Parenthesised publisher+group pairs are the manga convention, not an LN signal.
    assert is_light_novel("Overlord v01-14 (Seven Seas) (Stick)") is False
    assert is_light_novel("Overlord v01-14 [Seven Seas] [Stick]") is True
    # Media-type words state the format and stay decisive in either bracket style.
    for title in [
        "[LN] Overlord Vol. 1",
        "(LN) Overlord Vol. 1",
        "[Light Novel] Overlord Vol. 1",
        "(Light Novel) Overlord Vol. 1",
        "Mushoku Tensei - Jobless Reincarnation (Novel) v01-26",
        "That Time I Got Reincarnated as a Slime v01-18 [EPUB]",
        "The Tale of a Little Alchemist Blessed by the Spirits v01-02 [Audiobook] [Seven Seas Siren]",
        "Reincarnated as a Sword v01-12 [Seven Seas] [Stick]",
        "[Novel] Demon Slayer: Kimetsu no Yaiba - The Flower of Happiness (Light Novel)",
    ]:
        assert is_light_novel(title) is True, title
    # Genuine manga with manga/paren metadata stays manga.
    for title in [
        "Anemone wa Netsu o Obiru v01-03 (2025-2026) (Digital) (1r0n, Anon)",
        "[Lu自主规制] Chainsaw Man Vol. 01-11",
        "Dandadan Vol. 1-8 [Viz Media] [CBZ]",
    ]:
        assert is_light_novel(title) is False, title
    print("    [PASSED] '(Stick)' no longer drops manga; genuine LN markers preserved.")


# ===========================================================================
# 12. The scheduler's context-guarded chapter discard is intact
# ===========================================================================
def test_scheduler_chapter_guard_still_intact():
    print("--> Testing scheduler context-guarded chapter discard is intact...")
    from app.covers import CoverCache
    from app.database import MangaDatabase
    from app.providers.base import ReleaseItem
    from app.scheduler import MangaScheduler

    tmpdir = tempfile.mkdtemp(prefix="mangatrax_d3_sched_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "db.sqlite"))
        scheduler = MangaScheduler(db, CoverCache(os.path.join(tmpdir, "covers")))
        assert scheduler._discard_unmarked_chapters_enabled() is True
        assert db.track_discovered_series("Chainsaw Man") is True
        known = scheduler._known_series_keys(None)

        def _rel(title):
            parsed = _parsed(title)
            return ReleaseItem(
                title=title,
                link="https://nyaa.si/view/1",
                series_name=parsed["series_name"],
                volumes=parsed["volumes"],
                volume_start=parsed["volume_start"],
            )

        # Known base series + bare trailing single number -> discarded as a chapter.
        assert scheduler._unmarked_chapter_reason(_rel("Chainsaw Man 180"), known) != ""
        # Numbered series name is never discarded (the P3b user-confirmed behaviour).
        assert scheduler._unmarked_chapter_reason(_rel("Mob Psycho 100"), known) == ""
        # A bare numeric range is now a volume bundle, so the chapter guard must not
        # fire on it (the D3 port and the context guard compose).
        assert scheduler._unmarked_chapter_reason(_rel("Chainsaw Man 1-4"), known) == ""
        print("    [PASSED] context-guarded discard still only fires for marker-less chapters.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 78)
    print("Running D3 suite: ported NovelTrax parsers + chapter/volume conformance")
    print("=" * 78)
    tests = [
        test_spec8_conformance_vectors,
        test_bare_numeric_range_bundle_guard,
        test_parenthesised_omnibus_range,
        test_plural_keywords_and_oxford_lists,
        test_site_prefix_guard,
        test_chapter_range_as_volume_mapping,
        test_compound_format_tag_is_not_a_release_group,
        test_calibre_reversed_range_guard,
        test_calibre_bracket_delimiter_guard,
        test_chapter_diagnostics_exposed,
        test_light_novel_paren_bracket_convention,
        test_scheduler_chapter_guard_still_intact,
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
        print(f"D3 CONFORMANCE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL D3 CONFORMANCE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
