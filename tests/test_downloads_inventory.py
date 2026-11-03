#!/usr/bin/env python3
"""task-30 suite: raw downloads inventory, per-release download tracking, frequent routing.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import downloads_inventory  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from test_downloads_routing import KNOWN_TEMPLATE, _make_cbz  # noqa: E402
from test_torbox_downloader import HASH, TORBOX_KEY, Ctx, FakeTorboxService, _insert_release  # noqa: E402


# ===========================================================================
# 1. Inventory scan over a representative temp tree
# ===========================================================================
def test_inventory_scan_states_and_fields():
    print("--> Testing raw downloads inventory states/fields/cap...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_inventory_scan_")
    try:
        downloads = os.path.join(tmpdir, "downloads")
        os.makedirs(downloads)
        known = os.path.join(downloads, "Known Series v01-02")
        os.makedirs(known)
        _make_cbz(os.path.join(known, "Known Series v01.cbz"))
        _make_cbz(os.path.join(known, "Known Series v02.cbz"))
        _make_cbz(os.path.join(downloads, "Unknown Series v01.cbz"))
        _make_cbz(os.path.join(downloads, "rawblob.bin"))
        partial_dir = os.path.join(downloads, "Partial Torrent")
        os.makedirs(partial_dir)
        with open(os.path.join(partial_dir, "Partial Torrent v01.cbz.part"), "wb") as handle:
            handle.write(b"partial")

        tracked_files = [os.path.join(known, "Known Series v01.cbz")]
        releases = [{
            "title": "Known Series v01-02", "download_status": "COMPLETED",
            "download_files_json": json.dumps(tracked_files),
            "download_wanted_json": json.dumps([1, 2]),
            "downloaded_at": "2026-01-01 00:00:00",
        }]
        inventory = downloads_inventory.scan_downloads(
            downloads, series_templates={"known series": {"series_name": "Known Series"}},
            releases=releases,
        )
        assert inventory["exists"] is True, inventory
        entries = {entry["name"]: entry for entry in inventory["entries"]}
        known_entry = entries["Known Series v01-02"]
        assert known_entry["state"] == "ready", known_entry
        assert known_entry["file_count"] == 2 and known_entry["cbz_count"] == 2, known_entry
        assert known_entry["size_bytes"] > 0, known_entry
        assert known_entry["age_seconds"] is not None, known_entry
        assert known_entry["series_name"] == "Known Series", known_entry
        assert sorted(known_entry["volumes"]) == [1.0, 2.0], known_entry
        assert known_entry["release"]["title"] == "Known Series v01-02", known_entry
        assert known_entry["release"]["download_status"] == "COMPLETED", known_entry

        assert entries["Unknown Series v01.cbz"]["state"] == "awaiting_calibre", entries
        assert entries["rawblob.bin"]["state"] == "unmatched", entries
        assert entries["Partial Torrent"]["state"] == "partial", entries
        assert entries["Partial Torrent"]["has_partials"] is True, entries

        capped = downloads_inventory.scan_downloads(downloads, series_templates={}, max_entries=1)
        assert capped["truncated"] is True and len(capped["entries"]) == 1, capped
        print("    [PASSED] states (ready/awaiting/unmatched/partial), fields, matching and cap are correct.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 2. Status payload + POST /api/downloads/scan
# ===========================================================================
def test_status_and_scan_endpoint():
    print("--> Testing status inventory + force rescan endpoint...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_inventory_api_")
    try:
        downloads = os.path.join(ctx.temp_dir, "raw")
        os.makedirs(downloads, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        _make_cbz(os.path.join(downloads, "New Series v01.cbz"))
        response = ctx.client.post("/api/downloads/scan")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success", body
        assert body["last_scanned_at"], body
        assert any(entry["name"] == "New Series v01.cbz" for entry in body["entries"]), body
        stored = json.loads(ctx.db.get_setting("downloads_inventory_json"))
        assert any(entry["name"] == "New Series v01.cbz" for entry in stored["entries"]), stored
        status = ctx.client.get("/api/downloads/status").json()
        assert "inventory" in status and status["last_scanned_at"], status.keys()
        assert any(entry["name"] == "New Series v01.cbz" for entry in status["inventory"]["entries"]), status["inventory"]
        print("    [PASSED] GET status exposes inventory/last_scanned_at; POST scan refreshes and persists.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Per-release download tracking + completion writing it
# ===========================================================================
def test_release_tracking_columns_and_completion():
    print("--> Testing download_files/downloaded_at/routed_at tracking...")
    ctx = Ctx()
    try:
        ctx.db.set_setting("downloads_dir", os.path.join(ctx.tmpdir, "downloads"))
        ctx.db.set_setting("staging_dir", os.path.join(ctx.tmpdir, "staging"))
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        ctx.db.set_setting("torbox_delete_after_download", "false")
        ctx.db.set_setting("torbox_strict_volume_selection", "true")
        rid = _insert_release(
            ctx.db, "track-guid", "Series Vol. 01", downloader="torbox",
            downloader_id="55", download_status="DOWNLOADING",
            downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            volumes=[1], missing_volumes=[1], torrent_hash=HASH,
        )
        service = FakeTorboxService(state="ready", files=[{"id": 1, "name": "Series v01.cbz", "size": 10}])
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.poll_torbox_downloads()
        assert result["completed"] == 1, result
        row = ctx.db.get_release_by_id(rid)
        assert row["download_status"] == "COMPLETED", row
        paths = json.loads(row["download_files_json"] or "[]")
        assert paths and str(paths[0]).lower().endswith(".cbz"), paths
        assert row["downloaded_at"], row
        ctx.db.mark_release_routed(rid)
        assert ctx.db.get_release_by_id(rid)["routed_at"], "routed_at must be set"
        tracked = ctx.db.get_tracked_downloads()
        assert any(item["id"] == rid for item in tracked), tracked
        print("    [PASSED] completion persists files/downloaded_at; routed_at/tracked query work.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Frequent pass: inventory + routing moves a newly appearing file
# ===========================================================================
def test_frequent_pass_routes_new_file():
    print("--> Testing the frequent inventory+route pass moves a new ready file...")
    ctx = Ctx()
    try:
        downloads = os.path.join(ctx.tmpdir, "downloads")
        staging = os.path.join(ctx.tmpdir, "staging")
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        _make_cbz(os.path.join(downloads, "Known Series Vol. 02.cbz"))
        with patch.object(ctx.db, "get_series_templates_map", return_value=KNOWN_TEMPLATE):
            summary = ctx.scheduler.periodic_downloads_inventory_and_route()
        routed = summary["routing"]
        assert routed["tagged_and_moved"] == 1, routed
        assert os.path.isfile(os.path.join(staging, "Known Series Vol. 02.cbz")), os.listdir(staging)
        inventory = summary["inventory"]
        assert inventory["scanned_at"], inventory
        assert ctx.db.get_setting("downloads_inventory_json"), "inventory not persisted"
        print("    [PASSED] one frequent pass scans the folder and routes a newly appearing file.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Settings save triggers immediate inventory/routing
# ===========================================================================
def test_settings_save_triggers_inventory_route():
    print("--> Testing settings save triggers inventory/routing...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_inventory_settings_")
    try:
        downloads = os.path.join(ctx.temp_dir, "dl2")
        staging = os.path.join(ctx.temp_dir, "st2")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        with patch.object(ctx.scheduler, "periodic_downloads_inventory_and_route", return_value={}) as pass_mock:
            response = ctx.client.post(
                "/api/settings", json_data={"downloads_dir": downloads, "staging_dir": staging},
            )
        assert response.status_code == 200, response.text
        pass_mock.assert_called_once()
        assert response.json()["status"] == "success", response.json()
        print("    [PASSED] saving the directories triggers an immediate inventory/routing pass.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Template wiring: raw card, states, controls, release badge
# ===========================================================================
def test_template_wiring():
    print("--> Testing Downloads raw card + Releases waiting badge wiring...")
    downloads = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    for needle in (
        "Downloads Folder (raw)", "raw-downloads-card", "downloads_inventory.entries",
        "Ready to move", "Awaiting Calibre", "Partial / still downloading", "Unmatched",
        "Tag & move now", "Rescan", "rescanDownloads()", "Last scanned",
        "inventory-last-scan", "autoRefreshDownloadsInventory",
    ):
        assert needle in downloads, f"missing Downloads template wiring: {needle}"
    releases = open(
        os.path.join(BACKEND_DIR, "app", "templates", "partials", "flat_release_row_chunk.html"),
        encoding="utf-8",
    ).read()
    assert "Downloaded — waiting for Calibre/route" in releases, "release badge missing"
    assert "r.download_files_json" in releases, "release badge tooltip missing files"
    print("    [PASSED] raw downloads card, state chips, controls and release badge are wired.")


def main():
    print("=" * 78)
    print("Running downloads inventory suite (task-30)")
    print("=" * 78)
    tests = [
        test_inventory_scan_states_and_fields,
        test_status_and_scan_endpoint,
        test_release_tracking_columns_and_completion,
        test_frequent_pass_routes_new_file,
        test_settings_save_triggers_inventory_route,
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
        print(f"DOWNLOADS INVENTORY SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL DOWNLOADS INVENTORY TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
