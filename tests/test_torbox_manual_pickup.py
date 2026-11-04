#!/usr/bin/env python3
"""task-52 suite: Torbox manual pickup hold/cap/expiry/API/page wiring."""

import json
import os
import sys
import time
import traceback
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402


def _release(ctx, guid, title):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, download_status, "
            "downloader, downloader_id, volumes_json, missing_vols_json) "
            "VALUES (?, ?, 'Pickup Series', 'REQUESTED', 'DOWNLOADING', 'torbox', '123', '[1,2]', '[1,2]');",
            (guid, title),
        )
        conn.commit()
        return cur.lastrowid


def test_hold_snapshot_and_state():
    print("--> Testing hold instead of delete + file snapshot...")
    ctx = E2ETestContext(prefix="mangatrax_t52_hold_")
    try:
        release_id = _release(ctx, "pickup-1", "Pickup Series v01-02")
        info = {"torrent_id": 123, "files": [
            {"id": 1, "name": "Pickup Series v01.cbz", "size": 100},
            {"id": 2, "name": "Pickup Series v02.cbz", "size": 200},
        ]}
        held = ctx.scheduler._hold_torbox_for_manual_pickup(
            release_id, "Pickup Series v01-02", "wanted volumes absent from torrent",
            downloader=MagicMock(), handle={"torrent_id": 123}, info=info,
        )
        assert held is True
        pickups = ctx.db.list_torbox_manual_pickups()
        assert len(pickups) == 1, pickups
        pickup = pickups[0]
        files = json.loads(pickup["files_json"])
        assert len(files) == 2 and files[0]["suggested_volume"] == 1.0, files
        assert files[1]["suggested_volume"] == 2.0, files
        release = ctx.db.get_release_by_id(release_id)
        assert release["download_status"] == "MANUAL_PICKUP", release
        assert "Held for manual pickup" in (release["download_error"] or ""), release
        assert ctx.db.get_active_torbox_downloads() == [], "MANUAL_PICKUP must leave the normal queue"
        print("    [PASSED] hold keeps the torrent, snapshots files/suggested volumes, sets MANUAL_PICKUP and leaves queue.")
    finally:
        ctx.close()


def test_cap_and_expiry():
    print("--> Testing cap finite/0=unlimited and expiry/0=never...")
    ctx = E2ETestContext(prefix="mangatrax_t52_cap_")
    try:
        info = {"torrent_id": 1, "files": [{"id": 1, "name": "Cap Series v01.cbz", "size": 10}]}
        ctx.db.set_setting("torbox_manual_pickup_max_items", "1")
        first = _release(ctx, "cap-1", "Cap Series v01")
        assert ctx.scheduler._hold_torbox_for_manual_pickup(first, "Cap Series v01", "mapping failed", info=info) is True
        second = _release(ctx, "cap-2", "Cap Series v02")
        assert ctx.scheduler._hold_torbox_for_manual_pickup(second, "Cap Series v02", "mapping failed", info=info) is False
        assert ctx.db.count_active_manual_pickups() == 1
        ctx.db.set_setting("torbox_manual_pickup_max_items", "0")
        third = _release(ctx, "cap-3", "Cap Series v03")
        assert ctx.scheduler._hold_torbox_for_manual_pickup(third, "Cap Series v03", "mapping failed", info=info) is True
        assert ctx.db.count_active_manual_pickups() == 2

        # Expiry: old record expires, never-expire record stays.
        ctx.db.set_setting("torbox_manual_pickup_expiry_days", "1")
        expiring = _release(ctx, "exp-1", "Exp Series v01")
        assert ctx.scheduler._hold_torbox_for_manual_pickup(expiring, "Exp Series v01", "mapping failed", info=info) is True
        pickup = ctx.db.get_manual_pickup_for_release(expiring)
        with ctx.db.get_connection() as conn:
            conn.execute(
                "UPDATE torbox_manual_pickups SET expires_at = datetime('now','-1 day') WHERE id = ?;",
                (pickup["id"],),
            )
            conn.commit()
        ctx.db.set_setting("torbox_manual_pickup_expiry_days", "0")
        never = _release(ctx, "never-1", "Never Series v01")
        assert ctx.scheduler._hold_torbox_for_manual_pickup(never, "Never Series v01", "mapping failed", info=info) is True
        never_pickup = ctx.db.get_manual_pickup_for_release(never)
        assert never_pickup["expires_at"] is None, never_pickup
        downloader = MagicMock()
        downloader.cancel.return_value = True
        expired_count = ctx.scheduler.sweep_torbox_manual_pickups(downloader=downloader)
        assert expired_count == 1, expired_count
        assert ctx.db.get_torbox_manual_pickup(pickup["id"])["status"] == "expired"
        assert ctx.db.get_torbox_manual_pickup(never_pickup["id"])["status"] == "pending"
        assert ctx.db.get_release_by_id(expiring)["download_status"] == "FAILED"
        print("    [PASSED] finite cap blocks Nth hold; 0 unlimited; past-due expires (remote cancel), 0=never.")
    finally:
        ctx.close()


def test_api_fetch_map_discard():
    print("--> Testing list/map/volumes/fetch/discard APIs...")
    ctx = E2ETestContext(prefix="mangatrax_t52_api_")
    try:
        release_id = _release(ctx, "api-1", "API Pickup v01")
        ctx.scheduler._hold_torbox_for_manual_pickup(
            release_id, "API Pickup v01", "mapping failed",
            info={"torrent_id": 9, "files": [
                {"id": 11, "name": "API Pickup v01.cbz", "size": 100},
                {"id": 12, "name": "API Pickup v02.cbz", "size": 200},
            ]},
        )
        pickup = ctx.db.get_manual_pickup_for_release(release_id)
        listing = ctx.client.get("/api/torbox/manual-pickups")
        assert listing.status_code == 200 and listing.json()["count"] == 1, listing.text
        mapped = ctx.client.post(
            f"/api/torbox/manual-pickups/{pickup['id']}/map",
            json_data={"mapping": [{"index": 0, "volume": 1}, {"index": 1, "volume": 2}]},
        )
        assert mapped.status_code == 200, mapped.text
        files = ctx.db.get_torbox_manual_pickup(pickup["id"])["files_json"]
        assert '"suggested_volume": 1.0' in files, files
        volumes = ctx.client.post(
            f"/api/torbox/manual-pickups/{pickup['id']}/volumes", json_data={"volumes": [1]},
        )
        assert volumes.status_code == 200, volumes.text
        assert ctx.db.get_release_by_id(release_id)["volumes_json"] == "[1.0]"

        fake_downloader = MagicMock()
        fake_downloader.fetch_files.return_value = [{"path": "/tmp/API Pickup v01.cbz", "size": 100}]
        with patch("app.scheduler.build_downloader", return_value=fake_downloader), \
             patch.object(ctx.scheduler, "process_downloads_to_staging", return_value={}):
            started = ctx.client.post(
                f"/api/torbox/manual-pickups/{pickup['id']}/fetch",
                json_data={"file_indexes": [0]},
            )
            assert started.status_code == 200, started.text
            task_id = started.json()["task_id"]
            deadline = time.time() + 5
            task = {}
            while time.time() < deadline:
                task = ctx.client.get(f"/api/tasks/{task_id}").json()["task"]
                if not task["active"]:
                    break
                time.sleep(0.05)
        assert task["status"] == "success", task
        assert task["result"]["fetched"] == 1, task["result"]
        refreshed = ctx.db.get_torbox_manual_pickup(pickup["id"])
        assert refreshed["status"] == "partial", refreshed
        assert ctx.db.get_release_by_id(release_id)["download_status"] == "COMPLETED"
        discarded = ctx.client.post(f"/api/torbox/manual-pickups/{pickup['id']}/discard")
        assert discarded.status_code == 200, discarded.text
        assert ctx.db.get_torbox_manual_pickup(pickup["id"])["status"] == "discarded"
        assert ctx.db.get_release_by_id(release_id)["download_status"] == "FAILED"
        print("    [PASSED] list/map/volumes persist; fetch task completes with result; discard clears and fails release.")
    finally:
        ctx.close()


def test_ui_and_settings():
    print("--> Testing page/nav/banner/settings wiring...")
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "manual_pickup.html"), encoding="utf-8").read()
    base = open(os.path.join(BACKEND_DIR, "app", "templates", "base.html"), encoding="utf-8").read()
    downloads = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    for needle in ("Torbox Manual Pickup", "Fetch selected", "Fetch whole torrent", "Retry strict mapping",
                   "Edit series", "Discard", "pickupSaveMapping", "pickupSaveVolumes",
                   "data-task-kind=\"torbox:pickup:fetch\"", "data-task-kind=\"torbox:pickup:retry\""):
        assert needle in page, f"missing page wiring: {needle}"
    assert "/downloads/manual-pickup" in base
    assert "manual_pickup_count" in downloads and "Open Manual Pickup" in downloads
    for needle in ("setting-torbox-manual-pickup-enabled", "setting-torbox-manual-pickup-max-items",
                   "setting-torbox-manual-pickup-expiry-days", "0 = unlimited", "0 = never"):
        assert needle in settings, f"missing settings field: {needle}"
    print("    [PASSED] manual page/actions, nav, downloads banner, settings fields and task hooks present.")


def main():
    print("=" * 78)
    print("Running Torbox manual pickup suite (task-52)")
    print("=" * 78)
    tests = [test_hold_snapshot_and_state, test_cap_and_expiry, test_api_fetch_map_discard, test_ui_and_settings]
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
        print(f"MANUAL PICKUP SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TORBOX MANUAL PICKUP TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
