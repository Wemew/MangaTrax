#!/usr/bin/env python3
"""Staging duplicate purge path-selection regression.

Legacy nested staging and flat staging can hold files with the SAME basename.
The purge lookup previously keyed both the relative path and the bare filename in one
dict, so a bare filename overwrote the root entry and purging "Calibre Series v01.cbz"
could delete the nested copy instead of the requested root file. Bare filenames are now
only accepted when unique; exact relative paths always win.
"""

import json
import os
import sqlite3
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

from app import staging_duplicates as SD  # noqa: E402
from app.database import MangaDatabase  # noqa: E402


def _write(staging, rel, data=b"x" * 10):
    path = os.path.join(staging, rel)
    os.makedirs(os.path.dirname(path) or staging, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _seed_release(db, staging_root, recorded_path):
    con = sqlite3.connect(db.db_path)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO releases (guid,title,series_name,download_url,link,status,download_status,downloader,"
        "volumes_json,missing_vols_json,download_files_json,routed_at,downloaded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "dup-path-rel-1", "Calibre Series v01", "Calibre Series",
            "https://example.invalid/t.torrent", "https://example.invalid/p",
            "REQUESTED", "COMPLETED", "torbox", "[1]", "[1]",
            json.dumps([recorded_path]), "2026-01-01 00:00:00", "2026-01-01 00:00:00",
        ),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def _snapshot():
    return [{"name": "Calibre Series", "volumes": [1.0]}]


def test_relative_path_wins_over_nested_same_basename():
    tmp = tempfile.mkdtemp(prefix="dup-path-")
    staging = os.path.join(tmp, "staging")
    root = _write(staging, "Calibre Series v01.cbz")
    nested = _write(staging, "legacy/nested/Calibre Series v01.cbz")
    db = MangaDatabase(os.path.join(tmp, "t.db"))
    rid = _seed_release(db, staging, root)

    audit = SD.audit_staging_duplicates(db, staging, calibre_series=_snapshot())
    assert {item["relative"] for item in audit["files"]} == {
        "Calibre Series v01.cbz", "legacy/nested/Calibre Series v01.cbz",
    }, audit["files"]

    result = SD.purge_staging_duplicates(
        db, staging, filenames=["Calibre Series v01.cbz"], confirm=True, calibre_series=_snapshot(),
    )
    assert result["purged"] == 1, result
    assert not os.path.exists(root), "requested root duplicate was not deleted"
    assert os.path.exists(nested), "nested duplicate was deleted instead of the requested root file"
    assert os.path.basename(result["purged_files"][0]) == "Calibre Series v01.cbz", result

    release = db.get_release_by_id(rid)
    assert release["status"] == "IN_CALIBRE", release["status"]
    assert not release.get("routed_at"), release.get("routed_at")
    assert json.loads(release.get("download_files_json") or "[]") == [], release.get("download_files_json")
    print("    [PASSED] exact relative path purges the intended copy and completes release bookkeeping.")


def test_ambiguous_bare_filename_is_skipped():
    tmp = tempfile.mkdtemp(prefix="dup-amb-")
    staging = os.path.join(tmp, "staging")
    nested_a = _write(staging, "legacy/a/Calibre Series v01.cbz")
    nested_b = _write(staging, "legacy/b/Calibre Series v01.cbz")
    db = MangaDatabase(os.path.join(tmp, "t.db"))

    result = SD.purge_staging_duplicates(
        db, staging, filenames=["Calibre Series v01.cbz"], confirm=True, calibre_series=_snapshot(),
    )
    # Bare filename matches two verified copies -> unmatched/skipped, nothing deleted.
    assert result["purged"] == 0, result
    assert result["skipped"] == 1, result
    assert os.path.exists(nested_a) and os.path.exists(nested_b), "ambiguous bare filename deleted a file"
    print("    [PASSED] ambiguous bare filename is skipped instead of guessing.")



def test_unique_bare_filename_still_works():
    tmp = tempfile.mkdtemp(prefix="dup-unique-")
    staging = os.path.join(tmp, "staging")
    nested = _write(staging, "legacy/nested/Calibre Series v01.cbz")
    db = MangaDatabase(os.path.join(tmp, "t.db"))
    result = SD.purge_staging_duplicates(
        db, staging, filenames=["Calibre Series v01.cbz"], confirm=True, calibre_series=_snapshot(),
    )
    assert result["purged"] == 1, result
    assert not os.path.exists(nested), "unique bare filename was not purged"
    print("    [PASSED] unique bare filename still resolves.")


def test_relative_nested_path_works():
    tmp = tempfile.mkdtemp(prefix="dup-relnested-")
    staging = os.path.join(tmp, "staging")
    root = _write(staging, "Calibre Series v01.cbz")
    nested = _write(staging, "legacy/nested/Calibre Series v01.cbz")
    db = MangaDatabase(os.path.join(tmp, "t.db"))
    result = SD.purge_staging_duplicates(
        db, staging, filenames=["legacy/nested/Calibre Series v01.cbz"], confirm=True, calibre_series=_snapshot(),
    )
    assert result["purged"] == 1, result
    assert os.path.exists(root) and not os.path.exists(nested), "relative nested path selected the wrong file"
    print("    [PASSED] explicit nested relative path purges exactly that file.")


def run():
    test_relative_path_wins_over_nested_same_basename()
    test_ambiguous_bare_filename_is_skipped()
    test_unique_bare_filename_still_works()
    test_relative_nested_path_works()
    print("ALL STAGING DUPLICATE PATH TESTS PASSED")


if __name__ == "__main__":
    run()
