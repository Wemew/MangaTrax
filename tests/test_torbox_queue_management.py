#!/usr/bin/env python3
"""Torbox queue management: definitive Remove + Clear failed + actionable strict errors.

Covers the regression where a removed row could stay in the queue (stale status cache
and/or a stale handle made the endpoint look failed), the bulk cleanup, and the
chapter-range repair hint attached to strict-volume-selection failures.
"""

import json
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "tests"))

import e2e.harness as H  # noqa: E402 - installs FastAPI/Jinja shims + temp DATA_DIR
from app import main as M  # noqa: E402


def _call(coro):
    resp = H.run_coroutine_sync(coro)
    if hasattr(resp, "json"):
        try:
            return resp.json()
        except Exception:
            pass
    return json.loads(getattr(resp, "content", b"{}").decode())


def _seed(guid, title, status="FAILED", downloader_id=None, error="", volumes=None, torrent_hash=None):
    con = sqlite3.connect(M.db.db_path)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO releases (guid,title,series_name,download_url,link,status,download_status,downloader,"
        "downloader_id,torrent_hash,download_error,volumes_json,missing_vols_json,download_wanted_json,"
        "torbox_progress_json,download_files_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            guid, title, title.rsplit(" v", 1)[0], "https://example.invalid/a.torrent", "https://example.invalid/post",
            "REQUESTED", status, "torbox", downloader_id, torrent_hash, error,
            json.dumps(volumes or [1.0]), json.dumps(volumes or [1.0]), json.dumps(volumes or [1.0]),
            json.dumps({"phase": "fetching"}), json.dumps(["partial.cbz"]),
        ),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def test_remove_is_definitive():
    rid = _seed("torbox-remove-1", "Destroy All Humans v01-07", status="FAILED",
                error="Torbox strict volume selection failed: wanted volume(s) [8.0] are absent from the torrent.")
    response = _call(M.api_torbox_remove(rid))
    assert response.get("removed") is True, response
    assert response.get("status") == "success", response
    row = M.db.get_release_by_id(rid)
    assert row["download_status"] == "IDLE", row["download_status"]
    for field in ("downloader_id", "torrent_hash", "downloader_started_at", "downloaded_at", "routed_at"):
        assert not row.get(field), f"{field} should be cleared, got {row.get(field)!r}"
    assert not row.get("download_error"), row.get("download_error")
    assert int(row.get("download_attempts") or 0) == 0, row.get("download_attempts")
    assert json.loads(row.get("download_wanted_json") or "[]") == [], row.get("download_wanted_json")
    assert json.loads(row.get("download_files_json") or "[]") == [], row.get("download_files_json")
    queue_ids = [int(r["id"]) for r in M.db.get_downloader_releases("torbox", 50)]
    assert rid not in queue_ids, queue_ids
    print("    [PASSED] Remove clears handle/error/progress and drops the row from the queue.")


def test_clear_failed_only_touches_failed():
    failed_a = _seed("torbox-clear-1", "Honzuki no Gekokujou v01", status="FAILED",
                     error="Torbox strict volume selection for 'Honzuki' failed: wanted volume(s) [3.0] are absent.")
    failed_b = _seed("torbox-clear-2", "A Couple of Cuckoos 205-215", status="FAILED",
                     error="Torbox strict volume selection failed: wanted volume(s) [205.0] are absent from the torrent.",
                     volumes=[205.0, 206.0])
    active = _seed("torbox-clear-3", "Active download", status="DOWNLOADING", downloader_id="torrent-123")
    response = _call(M.api_torbox_clear_failed())
    assert response.get("status") == "success", response
    assert response.get("removed") == 2, response
    assert response.get("failed_entries") == 2, response
    assert response.get("task_id"), response
    task = M.db.get_background_task(int(response["task_id"]))
    assert task and str(task.get("status")) == "success", task
    assert str(task.get("kind")) == "downloads:torbox-clear-failed", task
    for rid in (failed_a, failed_b):
        row = M.db.get_release_by_id(rid)
        assert row["download_status"] == "IDLE", (rid, row["download_status"])
        assert not row.get("download_error"), (rid, row.get("download_error"))
    kept = M.db.get_release_by_id(active)
    assert kept["download_status"] == "DOWNLOADING", kept["download_status"]
    assert kept.get("downloader_id") == "torrent-123", kept.get("downloader_id")
    print("    [PASSED] Clear failed removes only FAILED rows (tasks/counts recorded) and keeps active downloads.")


def test_strict_volume_hints():
    chapter_like = M._torbox_release_view(
        {"id": 1, "title": "A Couple of Cuckoos 205-215", "download_status": "FAILED", "downloader": "torbox",
         "download_error": "Torbox strict volume selection failed: wanted volume(s) [205.0] are absent from the torrent.",
         "missing_vols_json": "[205.0, 206.0]", "volumes_json": "[205.0, 206.0]", "status": "REQUESTED"},
        None,
    )
    assert "chapter numbers stored as volumes" in chapter_like.get("strict_hint", ""), chapter_like.get("strict_hint")
    assert "Suspicious volume ranges" in chapter_like["strict_hint"], chapter_like["strict_hint"]

    unmappable = M._torbox_release_view(
        {"id": 2, "title": "Bookworm", "download_status": "FAILED", "downloader": "torbox",
         "download_error": "Torbox strict volume selection for 'Bookworm' failed: 2 file name(s) cannot be mapped to volumes (x, y); refusing.",
         "missing_vols_json": "[1.0, 2.0]", "status": "REQUESTED"},
        None,
    )
    assert "could not be matched to volumes" in unmappable.get("strict_hint", ""), unmappable.get("strict_hint")
    assert "strict volume selection" in unmappable["strict_hint"], unmappable["strict_hint"]

    healthy = M._torbox_release_view(
        {"id": 3, "title": "Downloading", "download_status": "DOWNLOADING", "downloader": "torbox",
         "missing_vols_json": "[1.0]", "status": "REQUESTED"},
        {"state": "downloading", "progress": 0.4, "size": 100},
    )
    assert not healthy.get("strict_hint"), healthy.get("strict_hint")
    print("    [PASSED] strict failures carry actionable hints (chapter-range repair / naming / absent volumes).")


def test_cache_invalidation_and_wiring():
    M._TORBOX_STATUS_CACHE["payload"] = {"torrents": [{"release_id": 99}]}
    M._TORBOX_STATUS_CACHE["at"] = 123.0
    M._invalidate_torbox_status_cache()
    assert M._TORBOX_STATUS_CACHE["payload"] is None, M._TORBOX_STATUS_CACHE
    assert float(M._TORBOX_STATUS_CACHE["at"] or 0) == 0.0, M._TORBOX_STATUS_CACHE

    main_src = open(os.path.join(BASE_DIR, "backend", "app", "main.py"), encoding="utf-8").read()
    remove_start = main_src.find("async def api_torbox_remove")
    remove_block = main_src[remove_start:remove_start + 2600]
    assert "clear_downloader_submission" in remove_block, "remove must clear the full submission"
    assert "_invalidate_torbox_status_cache()" in remove_block, "remove must invalidate the status cache"
    assert "is_failed" in remove_block and "handle = {} if is_failed" in remove_block, (
        "failed rows must not re-cancel the remote job (can block and look dead)"
    )
    assert "/api/downloads/torbox/clear-failed" in main_src, "bulk clear endpoint missing"
    assert "downloads:torbox-clear-failed" in main_src, "bulk clear task kind missing"
    clear_start = main_src.find("async def api_torbox_clear_failed")
    clear_block = main_src[clear_start:clear_start + 2200]
    assert "build_downloader" not in clear_block and ".cancel(" not in clear_block, (
        "clear-failed must be local-only so it cannot hang on Torbox timeouts"
    )

    downloads = open(os.path.join(BASE_DIR, "backend", "app", "templates", "downloads.html"), encoding="utf-8").read()
    for marker in ("_removedTorboxIds", "_dropTorboxRow", "clearFailedTorbox", "btn-clear-failed-torbox", "strict_hint",
                   "_queueConfirm", "_setButtonBusy", "_visibleFailedTorboxIds", "_ensureTorboxEmptyState"):
        assert marker in downloads, f"{marker} missing from downloads.html"
    assert "res.status !== 404" in downloads, "remove must tolerate a stale/404 row"
    # No blocking window.confirm() in queue handlers: suppressed dialogs silently abort.
    for fn in ("async function retryTorbox", "async function removeTorbox", "async function clearFailedTorbox"):
        start = downloads.find(fn)
        block = downloads[start:start + 2200]
        assert "confirm(" not in block, f"{fn} must not rely on window.confirm()"
        assert "_queueConfirm(" in block, f"{fn} must use the inline two-step confirmation"
    print("    [PASSED] cache invalidation + UI wiring (instant row drop, dialog-free confirm, Clear failed button).")


def run():
    test_remove_is_definitive()
    test_clear_failed_only_touches_failed()
    test_strict_volume_hints()
    test_cache_invalidation_and_wiring()
    print("ALL TORBOX QUEUE MANAGEMENT TESTS PASSED")


if __name__ == "__main__":
    run()
