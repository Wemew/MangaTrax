#!/usr/bin/env python3
"""task-34 suite: prune emptied download folders (safe, bottom-up, busy-aware).

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # installs FastAPI/Jinja shims in Calibre
import app.main as main_module  # noqa: E402
from app import downloads_inventory  # noqa: E402
from app.tagger import pipeline as downloads_pipeline  # noqa: E402
from test_downloads_routing import KNOWN_TEMPLATE, Ctx, _make_cbz  # noqa: E402
from test_torbox_downloader import HASH  # noqa: E402


def _capture_pipeline_logs():
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Handler(logging.INFO)
    logger = logging.getLogger("mangatrax.tagger.pipeline")
    logger.addHandler(handler)
    return records, handler, logger


# ===========================================================================
# 1. Routing moves the file and prunes the emptied torrent directory
# ===========================================================================
def test_routing_prunes_emptied_dir():
    print("--> Testing routing prunes the emptied download folder...")
    ctx = Ctx()
    try:
        source_dir = os.path.join(ctx.downloads, "Known Series Vol. 01")
        _make_cbz(os.path.join(source_dir, "Known Series v01.cbz"))
        result = ctx.run(templates=KNOWN_TEMPLATE)
        assert result["tagged_and_moved"] == 1, result
        assert os.path.isfile(os.path.join(ctx.staging, "Known Series Vol. 01", "Known Series v01.cbz")), os.listdir(ctx.staging)
        assert not os.path.exists(source_dir), "emptied torrent dir must be pruned"
        assert result["removed"] == 1, result
        assert result["pruned_dirs"] == ["Known Series Vol. 01"], result
        print("    [PASSED] file moved to staging and the empty source dir is removed (removed=1).")
    finally:
        ctx.close()


# ===========================================================================
# 2. Leftover .part/junk keeps the directory; nested empties prune bottom-up
# ===========================================================================
def test_keeps_non_empty_and_prunes_nested():
    print("--> Testing non-empty dir kept + nested empty dirs pruned bottom-up...")
    ctx = Ctx()
    try:
        source_dir = os.path.join(ctx.downloads, "Known Series Vol. 02")
        _make_cbz(os.path.join(source_dir, "Known Series v02.cbz"))
        with open(os.path.join(source_dir, "leftover.cbz.part"), "wb") as handle:
            handle.write(b"partial")
        result = ctx.run(templates=KNOWN_TEMPLATE)
        assert result["tagged_and_moved"] == 1, result
        assert os.path.isdir(source_dir), "a dir with a leftover .part must be kept"
        assert os.sep + "Known Series Vol. 02" not in json.dumps(result["pruned_dirs"]), result

        nested = os.path.join(ctx.downloads, "a", "b", "c")
        os.makedirs(nested)
        prune = downloads_pipeline.prune_empty_dirs(ctx.downloads, db=ctx.db)
        assert prune["removed"] == 3, prune
        assert not os.path.exists(os.path.join(ctx.downloads, "a")), prune
        assert os.path.isdir(ctx.downloads), "the downloads root itself is never removed"
        print("    [PASSED] .part keeps its dir; empty nested dirs are removed bottom-up.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Symlinks are never traversed/deleted; containment guard holds
# ===========================================================================
def test_symlink_outside_untouched():
    print("--> Testing symlink outside the downloads tree is untouched...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_prune_symlink_")
    try:
        downloads = os.path.join(tmpdir, "downloads")
        outside = os.path.join(tmpdir, "outside")
        os.makedirs(downloads)
        os.makedirs(outside)
        link = os.path.join(downloads, "outside-link")
        try:
            os.symlink(outside, link, target_is_directory=True)
            real_link = True
        except (OSError, NotImplementedError):
            # Windows without developer mode cannot create symlinks: simulate the
            # islink guard with an empty directory that the pruner must treat as a
            # symlink and therefore never traverse/delete.
            os.makedirs(link, exist_ok=True)
            real_link = False
            print("    [SKIP] real symlink creation not permitted; using simulated islink guard")
        if real_link:
            result = downloads_pipeline.prune_empty_dirs(downloads, busy_names=set())
            assert result["removed"] == 0, result
            assert os.path.isdir(outside), "symlink target outside the root must survive"
            assert os.path.islink(link), "symlink itself must not be deleted"
        else:
            with patch("app.tagger.pipeline.os.path.islink", side_effect=lambda candidate: candidate == link):
                result = downloads_pipeline.prune_empty_dirs(downloads, busy_names=set())
            assert result["removed"] == 0, result
            assert os.path.isdir(link), "symlink-guarded dir must not be removed"
        print("    [PASSED] symlinked directory outside the tree is neither followed nor removed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 4. Busy guard: a dir belonging to a DOWNLOADING release is kept
# ===========================================================================
def test_busy_download_dir_kept():
    print("--> Testing the DOWNLOADING-release busy guard...")
    ctx = Ctx()
    try:
        busy_title = "Busy Torrent v01"
        busy_dir = os.path.join(ctx.downloads, busy_title)
        os.makedirs(busy_dir)
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, download_status, downloader) "
                "VALUES (?, ?, 'Busy Series', 'REQUESTED', 'DOWNLOADING', 'torbox');",
                ("busy-guid", busy_title),
            )
            conn.commit()
        result = downloads_pipeline.prune_empty_dirs(ctx.downloads, db=ctx.db)
        assert result["removed"] == 0 and result["skipped_busy"] >= 1, result
        assert os.path.isdir(busy_dir), "busy download dir must be kept"
        with ctx.db.get_connection() as conn:
            conn.execute("UPDATE releases SET download_status = 'FAILED' WHERE guid = 'busy-guid';")
            conn.commit()
        result = downloads_pipeline.prune_empty_dirs(ctx.downloads, db=ctx.db)
        assert result["removed"] == 1, result
        assert not os.path.exists(busy_dir), "no-longer-busy empty dir can be pruned"
        print("    [PASSED] busy dir skipped while DOWNLOADING, pruned after it stops.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Scanner hides empty/junk dirs, keeps loose media (task-30 unchanged)
# ===========================================================================
def test_scanner_hides_empty_and_junk_dirs():
    print("--> Testing scanner hides empty/junk-only dirs...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_prune_scan_")
    try:
        downloads = os.path.join(tmpdir, "downloads")
        os.makedirs(os.path.join(downloads, "empty-dir"))
        junk = os.path.join(downloads, "junk-dir")
        os.makedirs(junk)
        with open(os.path.join(junk, "readme.nfo"), "w", encoding="utf-8") as handle:
            handle.write("junk")
        partial = os.path.join(downloads, "partial-dir")
        os.makedirs(partial)
        with open(os.path.join(partial, "x.cbz.part"), "wb") as handle:
            handle.write(b"part")
        _make_cbz(os.path.join(downloads, "Loose Series v01.cbz"))
        inventory = downloads_inventory.scan_downloads(downloads, series_templates={})
        names = {entry["name"] for entry in inventory["entries"]}
        assert "empty-dir" not in names, names
        assert "junk-dir" not in names, names
        assert "partial-dir" in names, names
        assert "Loose Series v01.cbz" in names, names
        states = {entry["name"]: entry["state"] for entry in inventory["entries"]}
        assert states["partial-dir"] == "partial", states
        print("    [PASSED] empty/junk dirs are hidden; partial dirs and loose media remain.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 6. Manual endpoint: downloads pruned, staging only when asked, counts returned
# ===========================================================================
def test_prune_endpoint():
    print("--> Testing POST /api/downloads/prune-empty...")
    ctx = E2ETestContext(prefix="mangatrax_prune_api_")
    try:
        downloads = os.path.join(ctx.temp_dir, "dl")
        staging = os.path.join(ctx.temp_dir, "stg")
        os.makedirs(os.path.join(downloads, "empty", "nested"))
        os.makedirs(os.path.join(staging, "empty-staging"))
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        first = ctx.client.post("/api/downloads/prune-empty", json_data={})
        body = first.json()
        assert first.status_code == 200 and body["status"] == "success", body
        assert body["removed"] == 2, body
        assert not os.path.exists(os.path.join(downloads, "empty")), body
        assert os.path.isdir(os.path.join(staging, "empty-staging")), "staging is not pruned automatically"
        second = ctx.client.post("/api/downloads/prune-empty", json_data={"include_staging": True})
        body = second.json()
        assert body["staging"]["removed"] == 1, body
        assert body["removed"] == 1, body
        assert not os.path.exists(os.path.join(staging, "empty-staging")), body
        print("    [PASSED] endpoint prunes downloads (and staging only when asked) with counts.")
    finally:
        ctx.close()


# ===========================================================================
# 7. One audit/log line per prune pass, none when nothing was removed
# ===========================================================================
def test_single_activity_line_per_pass():
    print("--> Testing one prune log/activity line per pass...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_prune_log_")
    try:
        downloads = os.path.join(tmpdir, "downloads")
        os.makedirs(os.path.join(downloads, "one"))
        os.makedirs(os.path.join(downloads, "two"))
        db = None
        from app.database import MangaDatabase
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        records, handler, logger = _capture_pipeline_logs()
        try:
            result = downloads_pipeline.prune_empty_dirs(downloads, db=db)
        finally:
            logger.removeHandler(handler)
        assert result["removed"] == 2, result
        messages = [record.getMessage() for record in records if "Downloads prune" in record.getMessage()]
        assert len(messages) == 1, messages
        assert "removed 2 empty folder" in messages[0], messages
        events = [row for row in db.get_logs(limit=50) if row["event_type"] == "DOWNLOADS_PRUNED"]
        assert len(events) == 1, events
        assert json.loads(events[0]["details"])["removed"] == 2, events[0]
        records, handler, logger = _capture_pipeline_logs()
        try:
            again = downloads_pipeline.prune_empty_dirs(downloads, db=db)
        finally:
            logger.removeHandler(handler)
        assert again["removed"] == 0, again
        assert not [record for record in records if "Downloads prune" in record.getMessage()]
        assert len([row for row in db.get_logs(limit=50) if row["event_type"] == "DOWNLOADS_PRUNED"]) == 1
        print("    [PASSED] one log/audit line per pass with removed dirs; silent when nothing removed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 8. Template wiring: prune button + JS + endpoint
# ===========================================================================
def test_template_wiring():
    print("--> Testing Prune empty folders button wiring...")
    template = open(
        os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8",
    ).read()
    for needle in ("Prune empty folders", "pruneEmptyFolders()", "/api/downloads/prune-empty",
                   "btn-prune-empty"):
        assert needle in template, f"missing prune UI wiring: {needle}"
    print("    [PASSED] raw-downloads card exposes the Prune empty folders action.")


def main():
    print("=" * 78)
    print("Running prune-empty-download-dirs suite (task-34)")
    print("=" * 78)
    tests = [
        test_routing_prunes_emptied_dir,
        test_keeps_non_empty_and_prunes_nested,
        test_symlink_outside_untouched,
        test_busy_download_dir_kept,
        test_scanner_hides_empty_and_junk_dirs,
        test_prune_endpoint,
        test_single_activity_line_per_pass,
        test_template_wiring,
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
        print(f"PRUNE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL PRUNE-EMPTY-DIR TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
