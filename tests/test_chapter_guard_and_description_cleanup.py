#!/usr/bin/env python3
"""Product-defect regression suite: description punctuation + marker-less chapters.

Covers the two defects found by the audit and previously hidden by stripped
assertions (calibre-debug -e compiles every bare ``assert`` away, so only the
sanctioned runner below proves anything):

  * P4 -- ``clean_description_text`` left an orphan space in front of punctuation
    after stripping an inline HTML tag: ``<p>demons lurk</p>, young`` cleaned to
    ``demons lurk , young``.  It now collapses horizontal whitespace before
    ``, . ; : ! ? ) ]`` without touching the markdown / BBCode / entity paths.

  * P3 -- releases titled ``Chainsaw Man 180`` carry no ``v``/``vol`` marker, so
    ``is_chapter_only()`` cannot classify them and they were ingested as junk
    volume-less releases.  The parser stays pure and context-free; the scheduler
    decides at ingestion time, and only when the title *without* the trailing
    bare number names a series we already know (Calibre library or a tracked
    row).  That is what keeps numbered series names safe:
    ``Mob Psycho 100`` -> stripped "Mob Psycho" is NOT a known series (the known
    series is ``Mob Psycho 100`` itself), so the number is part of the name and
    the release is never discarded.

Run through the assert-aware runner (assertions compiled live, optimize=0):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import re
import sqlite3
import sys
import tempfile
import shutil
import traceback
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
for _path in (BACKEND_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.covers import CoverCache
from app.database import MangaDatabase
from app.metadata.mangaupdates import clean_description_text
from app.parser import extract_manga_bundle_and_volumes, is_chapter_only
from app.providers.base import ReleaseItem
from app.scheduler import MangaScheduler


# ===========================================================================
# Helpers
# ===========================================================================
def _write_calibre_library(db_path, series_map):
    """Writes a minimal, scanner-complete Calibre metadata.db from {series_name: [volumes]}."""
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, sort TEXT,
            author_sort TEXT, series_index REAL DEFAULT 1.0, path TEXT DEFAULT '', has_cover INTEGER DEFAULT 0);
        CREATE TABLE series (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, series INTEGER NOT NULL);
        CREATE TABLE authors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT, link TEXT DEFAULT '');
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, author INTEGER NOT NULL);
        CREATE TABLE publishers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT);
        CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, publisher INTEGER NOT NULL);
        CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, tag INTEGER NOT NULL);
    """)
    for series_id, (name, volumes) in enumerate(series_map.items(), start=1):
        cur.execute("INSERT INTO series (id, name) VALUES (?, ?);", (series_id, name))
        cur.execute("INSERT OR IGNORE INTO authors (id, name) VALUES (?, ?);", (series_id, "Author"))
        cur.execute("INSERT OR IGNORE INTO publishers (id, name) VALUES (?, ?);", (series_id, "Publisher"))
        for volume in volumes:
            cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"{name} Vol. {int(volume)}", float(volume)))
            book_id = cur.lastrowid
            cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?);", (book_id, series_id))
            cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, ?);", (book_id, series_id))
            cur.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, ?);", (book_id, series_id))
    conn.commit()
    conn.close()


class _IngestContext:
    """Real scheduler + real SQLite DB + a scanner-readable mock Calibre library.

    The only stubs are the two external metadata services (AniList /
    MangaUpdates) so the suite is hermetic and fast; every write still goes
    through ``MangaScheduler.check_all_feeds()``, the real ingestion path.
    """

    def __init__(self, calibre_series=None, settings=None):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_chapter_guard_")
        self.calibre_dir = os.path.join(self.tmpdir, "calibre")
        os.makedirs(self.calibre_dir, exist_ok=True)
        _write_calibre_library(os.path.join(self.calibre_dir, "metadata.db"), calibre_series or {})

        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.db.set_setting("calibre_path", self.calibre_dir)
        self.db.set_setting("qbit_enabled", "false")
        self.db.set_setting("auto_download", "false")
        self.db.set_setting("staging_dir", os.path.join(self.tmpdir, "staging"))
        for key, value in (settings or {}).items():
            self.db.set_setting(key, value)

        self.cover_cache = CoverCache(os.path.join(self.tmpdir, "covers"))
        self.scheduler = MangaScheduler(self.db, self.cover_cache)
        self.scheduler.anilist.resolve_manga_series = MagicMock(
            side_effect=lambda raw: {"canonical_title": raw, "romaji_title": raw, "english_title": raw}
        )
        self.scheduler.mangaupdates.get_series_info = MagicMock(return_value=None)

        for seeded_feed in self.db.get_all_feeds():
            self.db.delete_feed(seeded_feed["id"])
        self.db.add_feed(
            name="Nyaa Manga Feed",
            feed_type="nyaa",
            url="https://nyaa.si/?page=rss",
            interval=60,
            auto_download=False,
        )

    def poll(self, *releases):
        """Runs one real feed poll over *releases* and returns its summary dict."""
        provider = MagicMock()
        provider.fetch_releases.return_value = list(releases)
        with patch("app.scheduler.create_provider", return_value=provider):
            return self.scheduler.check_all_feeds()

    def releases(self):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT title, series_name, status, volumes_json FROM releases ORDER BY id;")
            return [dict(row) for row in cur.fetchall()]

    def discards(self):
        return [row for row in self.db.get_logs(limit=200) if row["event_type"] == "CHAPTER_DISCARDED"]

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _release(title, guid, series_name=None):
    """Builds a release exactly the way a provider does: series_name is the parsed title."""
    parsed = extract_manga_bundle_and_volumes(title) if series_name is None else None
    return ReleaseItem(
        title=title,
        link=f"https://nyaa.si/view/{guid}",
        download_url=f"magnet:?xt=urn:btih:{guid}",
        series_name=series_name if series_name is not None else parsed["series_name"],
        guid=guid,
    )


# ===========================================================================
# (a) Description cleaning: no orphan space before punctuation
# ===========================================================================
def test_description_cleaning_collapses_space_before_punctuation():
    print("--> Testing clean_description_text(): tag-bounded punctuation (P4)...")

    # The exact reported defect: an inline tag just before punctuation used to leave
    # "demons lurk , young" (space inserted by tag stripping).
    cleaned = clean_description_text("In a world where <p>demons lurk</p>, young Denji must survive.")
    assert cleaned == "In a world where demons lurk, young Denji must survive.", cleaned
    assert " ," not in cleaned

    # Every punctuation character in the contract, each preceded by a stripped tag.
    cases = [
        ("A <b>bold</b>.", "A bold."),
        ("B <i>ital</i>!", "B ital!"),
        ("C <span>x</span>?", "C x?"),
        ("D <em>y</em>:", "D y:"),
        ("E <u>z</u>;", "E z;"),
        ("F <b>closing</b>)", "F closing)"),
        ("G <b>closing</b>]", "G closing]"),
        ("H <p>two spaces</p>   ,", "H two spaces,"),
    ]
    for raw, expected in cases:
        assert clean_description_text(raw) == expected, (raw, clean_description_text(raw), expected)
        assert not re.search(r"[ \t]+[,.;:!?)\]]", clean_description_text(raw)), raw

    # No regression in the markdown / BBCode / entity paths.
    assert clean_description_text(
        "**Original Webtoon:** [Kakao Webtoon R19](https://webtoon.kakao.com/content/12345)"
    ) == "Original Webtoon: Kakao Webtoon R19"
    assert clean_description_text(
        "[b]From Viz:[/b] In a world where <p>demons lurk</p>, young "
        "[url=https://example.com]Denji[/url] must survive."
    ) == "In a world where demons lurk, young Denji must survive."
    assert clean_description_text(
        "Kimito Kagurazaka is a commoner with a fetish for men&#039;s muscles or at least that&#039;s the lie..."
    ) == "Kimito Kagurazaka is a commoner with a fetish for men's muscles or at least that's the lie..."
    assert clean_description_text("") == ""
    print("    [PASSED] tag stripping no longer orphans a space before punctuation.")


# ===========================================================================
# The parser stays pure: is_chapter_only() is deliberately NOT changed
# ===========================================================================
def test_parser_is_chapter_only_is_left_context_free():
    print("--> Testing is_chapter_only() is unchanged and still context-free...")

    # Bare trailing numbers are NOT chapter-only for the parser: without library
    # context the number may be part of the series name.
    # task-44: standalone bare 3-digit trailing numbers are chapter-only, while
    # 2-digit bare numbers / non-trailing numbers are unchanged.
    for title in ("Chainsaw Man 180", "One Piece 108", "Mob Psycho 100"):
        assert is_chapter_only(title) is True, title
    for title in ("20th Century Boys", "7th Time Loop", "Isekai Ojisan 2"):
        assert is_chapter_only(title) is False, title

    # The parser's own pure classifications still work (existing machinery).
    assert is_chapter_only("Chainsaw Man Ch. 180") is True
    assert is_chapter_only("Chainsaw Man c180") is True
    assert is_chapter_only("Chainsaw Man #180") is True
    assert is_chapter_only("Chainsaw Man Vol. 180") is False
    assert is_chapter_only("Chainsaw Man v180") is False
    assert is_chapter_only("Chainsaw Man (complete)") is False

    # The defect path: "Chainsaw Man 180" parses as a volume-less chapter release.
    # The chapter number is exposed as data and stripped from the series key (so
    # chapters group under the series); the scheduler's context guard then decides
    # whether to discard it.
    parsed = extract_manga_bundle_and_volumes("Chainsaw Man 180")
    assert parsed["series_name"] == "Chainsaw Man", parsed
    assert parsed["numbered_series_name"] == "Chainsaw Man 180", parsed
    assert parsed["is_chapter"] is True, parsed
    assert parsed["chapter_number"] == 180.0, parsed
    assert parsed["volumes"] == [], parsed
    assert parsed["volume_start"] is None, parsed
    print("    [PASSED] parser classification unchanged; bare-number titles stay unclassified there.")


# ===========================================================================
# (b1) Marker-less chapter of a Calibre-owned series is discarded + audited
# ===========================================================================
def test_unmarked_chapter_discarded_when_series_in_calibre():
    print("--> Testing 'Chainsaw Man 180' with Chainsaw Man in Calibre -> discarded...")
    ctx = _IngestContext({"Chainsaw Man": [1.0, 2.0, 3.0]})
    try:
        # The setting ships enabled by default.
        assert ctx.db.get_setting("discard_unmarked_chapters") == "true", ctx.db.get_all_settings()

        result = ctx.poll(_release("Chainsaw Man 180", "guard-1001"))

        assert result["releases_found"] == 0, result
        assert ctx.releases() == [], ctx.releases()

        discards = ctx.discards()
        assert len(discards) == 1, discards
        event = discards[0]
        assert event["actor"] == "scheduler", event
        assert "Chainsaw Man 180" in event["message"], event
        assert "Chainsaw Man" in event["details"], event
        assert "known series" in event["details"], event
        print("    [PASSED] discarded, audit-logged (actor='scheduler'), and never stored.")
    finally:
        ctx.close()


# ===========================================================================
# (b1b) Marker-less chapter of an already-tracked (user-typed) series
# ===========================================================================
def test_unmarked_chapter_discarded_when_series_only_tracked_in_db():
    print("--> Testing marker-less chapter of an already-tracked series -> discarded...")
    # Calibre library empty: the "known series" comes from series_tracking only.
    ctx = _IngestContext({})
    try:
        assert ctx.db.track_discovered_series("Dandadan") is True
        result = ctx.poll(_release("Dandadan 42", "guard-1002"))

        assert result["releases_found"] == 0, result
        assert ctx.releases() == [], ctx.releases()
        discards = ctx.discards()
        assert len(discards) == 1, discards
        assert discards[0]["actor"] == "scheduler", discards[0]
        assert "Dandadan" in discards[0]["details"], discards[0]
        print("    [PASSED] tracked rows are enough library context to prove a chapter.")
    finally:
        ctx.close()


# ===========================================================================
# (b2) Numbered series names are NOT discarded ("Mob Psycho 100")
# ===========================================================================
def test_numbered_series_title_is_never_discarded():
    print("--> Testing 'Mob Psycho 100' (number part of the name) is not discarded...")
    ctx = _IngestContext({"Mob Psycho 100": [1.0, 2.0], "Chainsaw Man": [1.0]})
    try:
        # "Mob Psycho 100" is a series name, not a chapter of "Mob Psycho": stripping
        # the number does not name any known series, so it must be ingested.
        result = ctx.poll(_release("Mob Psycho 100", "guard-2001"))
        assert result["releases_found"] == 1, result
        stored = ctx.releases()
        assert len(stored) == 1, stored
        assert stored[0]["series_name"] == "Mob Psycho 100", stored
        assert ctx.discards() == [], ctx.discards()

        # A chapter OF the numbered series is still detected: stripping 180 leaves
        # "Mob Psycho 100", which *is* a known series.
        result_chapter = ctx.poll(_release("Mob Psycho 100 180", "guard-2002"))
        assert result_chapter["releases_found"] == 0, result_chapter
        assert len(ctx.releases()) == 1, ctx.releases()
        discards = ctx.discards()
        assert len(discards) == 1, discards
        assert "Mob Psycho 100 180" in discards[0]["message"], discards[0]
        print("    [PASSED] 'Mob Psycho 100' survives; 'Mob Psycho 100 180' is a chapter of it.")
    finally:
        ctx.close()


# ===========================================================================
# (b2b) Without library context a bare trailing number is left alone
# ===========================================================================
def test_bare_number_title_not_discarded_without_library_context():
    print("--> Testing guard is context-aware, not a blind regex...")
    ctx = _IngestContext({})
    try:
        # Nothing known: the guard cannot prove "Chainsaw Man 180" is a chapter, so
        # the release is retained -- and (task-9 third item) it is grouped under the
        # clean series key "Chainsaw Man", not one bogus series per chapter.
        result = ctx.poll(
            _release("Chainsaw Man 180", "guard-3001"),
            _release("Chainsaw Man 181", "guard-3002"),
        )
        # task-44: 3-digit bare chapter numbers are chapter-only regardless of context.
        assert result["releases_found"] == 0, result
        assert ctx.releases() == [], ctx.releases()
        assert len(ctx.discards()) == 2, ctx.discards()
        print("    [PASSED] 3-digit bare chapter numbers are chapter-only, not phantom volumes.")
    finally:
        ctx.close()


# ===========================================================================
# (b3) Accepted trade-off: "One Piece 108" is discarded as a chapter
# ===========================================================================
def test_accepted_trade_off_markerless_volume_of_known_series():
    print("--> Testing accepted trade-off: 'One Piece 108' with One Piece tracked...")
    ctx = _IngestContext({"One Piece": [float(v) for v in range(1, 105)]})
    try:
        result = ctx.poll(_release("One Piece 108", "guard-4001"))

        # Trade-off: a release that drops the "Vol." marker cannot be distinguished
        # from a chapter, so it is discarded.  The audit log names the release so the
        # user can still act on it.
        assert result["releases_found"] == 0, result
        assert ctx.releases() == [], ctx.releases()
        discards = ctx.discards()
        assert len(discards) == 1, discards
        assert "One Piece 108" in discards[0]["message"], discards[0]
        assert "One Piece" in discards[0]["details"], discards[0]
        print("    [PASSED] undocumented volume releases of a known series are discarded (documented trade-off).")
    finally:
        ctx.close()


# ===========================================================================
# (b4) Setting turns the guard off without a code change
# ===========================================================================
def test_setting_disables_unmarked_chapter_guard():
    print("--> Testing discard_unmarked_chapters=false restores ingestion...")
    ctx = _IngestContext({"Chainsaw Man": [1.0]}, settings={"discard_unmarked_chapters": "false"})
    try:
        result = ctx.poll(_release("Chainsaw Man 180", "guard-5001"))
        assert result["releases_found"] == 1, result
        stored = ctx.releases()
        assert len(stored) == 1, stored
        # Ingested as before the fix (the fuzzy Calibre match may normalise the series
        # key to "Chainsaw Man"; what matters is that the release is stored, not dropped).
        assert stored[0]["title"] == "Chainsaw Man 180", stored
        assert ctx.discards() == [], ctx.discards()

        # Default is true: a fresh context without the override discards the same release.
        ctx.db.set_setting("discard_unmarked_chapters", "true")
        result_on = ctx.poll(_release("Chainsaw Man 181", "guard-5002"))
        assert result_on["releases_found"] == 0, result_on
        assert len(ctx.discards()) == 1, ctx.discards()
        print("    [PASSED] guard is gated by the discard_unmarked_chapters setting (default true).")
    finally:
        ctx.close()


# ===========================================================================
# (b5) Explicit marker path is unchanged
# ===========================================================================
def test_explicit_chapter_marker_path_is_unchanged():
    print("--> Testing 'Chainsaw Man Ch. 180' still uses the existing explicit path...")
    ctx = _IngestContext({"Chainsaw Man": [1.0]})
    try:
        result = ctx.poll(_release("Chainsaw Man Ch. 180", "guard-6001"))
        assert result["releases_found"] == 0, result
        assert ctx.releases() == [], ctx.releases()
        # No new audit event: this release never reaches the marker-less guard, it is
        # discarded by the pre-existing is_chapter_only() check exactly as before.
        assert ctx.discards() == [], ctx.discards()
        print("    [PASSED] explicit chapter markers take the original path, no behaviour change.")
    finally:
        ctx.close()


# ===========================================================================
# Volume releases are never touched by the guard
# ===========================================================================
def test_volume_release_with_marker_is_not_discarded():
    print("--> Testing volume-marked releases are untouched by the guard...")
    ctx = _IngestContext({"Chainsaw Man": [1.0, 2.0, 3.0]})
    try:
        result = ctx.poll(_release("Chainsaw Man Vol. 180", "guard-7001"))
        assert result["releases_found"] == 1, result
        stored = ctx.releases()
        assert len(stored) == 1, stored
        assert stored[0]["series_name"] == "Chainsaw Man", stored
        assert ctx.discards() == [], ctx.discards()
        print("    [PASSED] 'Chainsaw Man Vol. 180' is a volume release, not a chapter.")
    finally:
        ctx.close()


# ===========================================================================
# Ingestion also happens through the backfill path (_process_release)
# ===========================================================================
def test_process_release_path_discards_unmarked_chapter():
    print("--> Testing the backfill ingestion path (_process_release) uses the same guard...")
    ctx = _IngestContext({"Chainsaw Man": [1.0, 2.0, 3.0]})
    try:
        _, matcher = ctx.scheduler._build_calibre_context()
        release = _release("Chainsaw Man 180", "guard-1003")
        is_new, status, missing, rel_id, series_key = ctx.scheduler._process_release(
            release, "Nyaa Manga", matcher
        )

        assert status == "CHAPTER_ONLY", (status, series_key)
        assert is_new is False and rel_id is None, (is_new, rel_id)
        assert ctx.releases() == [], ctx.releases()
        discards = ctx.discards()
        assert len(discards) == 1, discards
        assert discards[0]["actor"] == "scheduler", discards[0]
        print("    [PASSED] backfill ingestion discards marker-less chapters identically.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running description-cleanup + marker-less chapter guard suite")
    print("=" * 78)
    tests = [
        test_description_cleaning_collapses_space_before_punctuation,
        test_parser_is_chapter_only_is_left_context_free,
        test_unmarked_chapter_discarded_when_series_in_calibre,
        test_unmarked_chapter_discarded_when_series_only_tracked_in_db,
        test_numbered_series_title_is_never_discarded,
        test_bare_number_title_not_discarded_without_library_context,
        test_accepted_trade_off_markerless_volume_of_known_series,
        test_setting_disables_unmarked_chapter_guard,
        test_explicit_chapter_marker_path_is_unchanged,
        test_volume_release_with_marker_is_not_discarded,
        test_process_release_path_discards_unmarked_chapter,
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
        print(f"DESCRIPTION CLEANUP + CHAPTER GUARD SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL DESCRIPTION CLEANUP + CHAPTER GUARD TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
