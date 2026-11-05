#!/usr/bin/env python3
"""Staging-duplicate audit must match ACTUAL owned volumes, never book_count.

Bug (reported against the UI): the audit fell back to Calibre's `book_count` and
treated a series with N books as owning volumes 1..N.  On sparse/part-based libraries
this flagged staging files whose volumes Calibre did NOT own, so "Purge" could delete
staging copies that were not duplicates at all.
"""

import json
import os
import sqlite3
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

from app import staging_duplicates as SD  # noqa: E402
from app.calibre.scanner import CalibreMangaScanner  # noqa: E402
from app.database import MangaDatabase  # noqa: E402


def _write(staging, rel, data=b"x" * 10):
    path = os.path.join(staging, rel)
    os.makedirs(os.path.dirname(path) or staging, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _snapshot(extra):
    entry = {"series_id": 1, "series_name": "Calibre Series", "name": "Calibre Series", "latest_index": 1.0}
    entry.update(extra)
    return [entry]


def test_book_count_alone_never_marks_duplicates():
    tmp = tempfile.mkdtemp(prefix="dupmatch-")
    staging = os.path.join(tmp, "staging")
    for vol in (8, 32, 40):
        _write(staging, f"Calibre Series v{vol:02d}.cbz")
    db = MangaDatabase(os.path.join(tmp, "t.db"))

    # book_count=40 previously implied volumes 1..40 -> all three files were "duplicates".
    audit = SD.audit_staging_duplicates(db, staging, calibre_series=_snapshot({"book_count": 40}))
    assert audit["duplicates"] == 0, f"book_count must not imply volume ownership: {audit['files']}"
    assert SD._calibre_volumes(_snapshot({"book_count": 40})[0]) == set(), "book_count fallback still present"
    print("    [PASSED] book_count alone produces no duplicates (destructive purge path closed).")


def test_exact_owned_volumes_control_matching():
    tmp = tempfile.mkdtemp(prefix="dupvol-")
    staging = os.path.join(tmp, "staging")
    v8 = _write(staging, "Calibre Series v08.cbz")
    v9 = _write(staging, "Calibre Series v09.cbz")
    v32 = _write(staging, "Calibre Series v32.cbz")
    db = MangaDatabase(os.path.join(tmp, "t.db"))

    audit = SD.audit_staging_duplicates(db, staging, calibre_series=_snapshot({"volumes": [8.0]}))
    names = {item["filename"] for item in audit["files"]}
    assert names == {"Calibre Series v08.cbz"}, f"expected only v08 duplicate, got {names}"
    entry = audit["files"][0]
    assert entry["calibre_volumes"] == [8.0], entry["calibre_volumes"]
    assert 9.0 not in entry["calibre_volumes"] and 32.0 not in entry["calibre_volumes"], entry

    # Purge must refuse the non-duplicates even if someone posts their filenames.
    result = SD.purge_staging_duplicates(
        db, staging, filenames=["Calibre Series v09.cbz", "Calibre Series v32.cbz"],
        confirm=True, calibre_series=_snapshot({"volumes": [8.0]}),
    )
    assert result["purged"] == 0, result
    assert os.path.exists(v9) and os.path.exists(v32), "non-duplicate staging files were deleted"
    assert os.path.exists(v8), "purge without selecting v08 should leave it"
    print("    [PASSED] only volumes actually owned by Calibre are duplicates; others are skipped.")


def test_scanner_exposes_owned_volumes():
    class FakeCursor:
        def __init__(self):
            self._first = True

        def execute(self, sql, *args):
            self._first = "GROUP BY s.id, s.name" in sql

        def fetchall(self):
            if self._first:
                return [{
                    "series_id": 1, "series_name": "Calibre Series", "book_count": 13,
                    "latest_index": 13.0, "authors": "A", "publisher": "", "tags": "",
                }]
            return []

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    scanner = object.__new__(CalibreMangaScanner)
    scanner.is_available = lambda: True
    scanner._get_connection = lambda: FakeConn()
    scanner.get_series_volumes_map = lambda: {"calibre series": {8.0, 9.0}}
    series = scanner.get_all_series()
    assert series and series[0].get("volumes") == [8.0, 9.0], series
    assert series[0]["book_count"] == 13, series[0]
    print("    [PASSED] scanner snapshot carries explicit owned volumes next to book_count.")


def test_ui_shows_owned_volumes_and_wording():
    downloads = open(os.path.join(BASE_DIR, "backend", "app", "templates", "downloads.html"), encoding="utf-8").read()
    assert "calibre_volumes" in downloads, "UI does not display the matched owned volumes"
    assert "owned v" in downloads, "UI does not label matched owned volumes"
    assert "never by series name or book count alone" in downloads, "card wording not corrected"
    print("    [PASSED] card shows the matched owned volumes and documents exact-volume matching.")


def run():
    test_book_count_alone_never_marks_duplicates()
    test_exact_owned_volumes_control_matching()
    test_scanner_exposes_owned_volumes()
    test_ui_shows_owned_volumes_and_wording()
    print("ALL STAGING DUPLICATE VOLUME-MATCHING TESTS PASSED")


if __name__ == "__main__":
    run()
