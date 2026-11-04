#!/usr/bin/env python3
"""task-25 suite: Torbox strict exact-missing-volume selection.

Default-on setting ``torbox_strict_volume_selection`` makes Torbox download ONLY the
files for the missing volumes; unmappable file names or absent wanted volumes are hard
failures instead of silently downloading the whole torrent.  ``false`` restores the old
permissive whole-torrent fallback.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import io
import json
import os
import shutil
import sys
import tempfile
import traceback
import zipfile
from datetime import datetime
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import schemas  # noqa: E402
from app.downloader.base import DownloaderFileError  # noqa: E402
from app.downloader.torbox import TorboxDownloader  # noqa: E402
from test_torbox_downloader import (  # noqa: E402
    BENCODED_TORRENT,
    HASH,
    TORBOX_KEY,
    Ctx,
    FakeApi,
    FakeTorboxService,
    ScriptedStream,
    _insert_release,
)


def _files(volumes, prefix="Series"):
    return [{"id": volume, "name": f"{prefix} v{volume:02d}.cbz", "size": 10} for volume in volumes]


def _zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _ready_ctx(ctx):
    ctx.db.set_setting("downloads_dir", os.path.join(ctx.tmpdir, "downloads"))
    ctx.db.set_setting("staging_dir", os.path.join(ctx.tmpdir, "staging"))
    ctx.db.set_setting("torbox_enabled", "true")
    ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
    ctx.db.set_setting("torbox_delete_after_download", "false")
    ctx.db.set_setting("torbox_strict_volume_selection", "true")


# ===========================================================================
# 1. Bundle v01..v05 with missing [3,4] -> exactly two per-file requestdl calls
# ===========================================================================
def test_strict_subset_exact_files():
    print("--> Testing strict subset selects exactly the missing volumes...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        files = _files([1, 2, 3, 4, 5])
        release = {"id": 1, "title": "Strict Bundle", "missing_vols_json": json.dumps([3.0, 4.0])}
        plan = ctx.scheduler._plan_torbox_selection(release, files, [3, 4])
        assert plan["action"] == "files", plan
        assert [item["id"] for item in plan["files"]] == [3, 4], plan["files"]
        assert plan["skipped_volumes"] == [1, 2, 5], plan
        assert plan["selected_volumes"] == [3, 4], plan
        mapping = {entry["name"]: entry["volumes"] for entry in plan["mapping"]}
        assert mapping["Series v03.cbz"] == [3.0] and mapping["Series v01.cbz"] == [1.0], mapping

        # Real fetch: exactly two requestdl calls (file_id=3,4), no zip_link.
        dest = tempfile.mkdtemp(prefix="mangatrax_strict_dl_")
        try:
            api = FakeApi().add("/torrents/requestdl", data={"url": "https://cdn.example/file"})
            stream = ScriptedStream([b"CBZ3", b"CBZ4"])
            downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)
            with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
                with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                    saved = downloader.fetch_files({"torrent_id": 55}, plan["files"], dest)
            assert len(saved) == 2, saved
            request_urls = api.urls_for("/torrents/requestdl")
            assert len(request_urls) == 2, request_urls
            assert any("file_id=3" in url for url in request_urls), request_urls
            assert any("file_id=4" in url for url in request_urls), request_urls
            assert not any("zip_link=true" in url for url in request_urls), request_urls
            assert sorted(os.listdir(dest)) == ["Series v03.cbz", "Series v04.cbz"], os.listdir(dest)
        finally:
            shutil.rmtree(dest, ignore_errors=True)

        # End-to-end poll: the same selection reaches downloader.fetch_files.
        ctx.db.set_setting("torbox_strict_volume_selection", "true")
        rid = _insert_release(
            ctx.db, "strict-subset-guid", "Strict Bundle", downloader="torbox",
            downloader_id="55", download_status="DOWNLOADING",
            downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            volumes=[1, 2, 3, 4, 5], missing_volumes=[3, 4],
        )
        service = FakeTorboxService(state="ready", files=files)
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.poll_torbox_downloads()
        assert result["completed"] == 1, result
        _handle, selection, _dest = service.fetch_calls[0]
        assert [item["id"] for item in selection] == [3, 4], selection
        events = ctx.events("SELECTIVE_DOWNLOAD")
        assert events and "2/5 file(s) selected" in events[0]["message"], events
        assert "3 owned volume(s) skipped" in events[0]["message"], events
        assert ctx.db.get_release_by_id(rid)["download_status"] == "COMPLETED"
        print("    [PASSED] v01..v05 missing 3,4 -> only file ids 3 and 4 requested, no zip_link.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Single missing volume -> only that file
# ===========================================================================
def test_strict_single_volume():
    print("--> Testing strict single missing volume...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        files = _files([1, 2, 3, 4, 5])
        plan = ctx.scheduler._plan_torbox_selection({"title": "Single"}, files, [3])
        assert plan["action"] == "files", plan
        assert [item["id"] for item in plan["files"]] == [3], plan["files"]
        print("    [PASSED] only the v03 file is selected.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Unmappable file names -> hard failure, zero requestdl calls, no output
# ===========================================================================
def test_strict_unmappable_hard_fail():
    print("--> Testing strict unmappable file names fail hard...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        files = [{"id": 1, "name": "Series v01.cbz"}, {"id": 2, "name": "extras.nfo"}]
        try:
            ctx.scheduler._plan_torbox_selection({"title": "Mystery Bundle"}, files, [1])
        except DownloaderFileError as exc:
            message = str(exc)
        else:
            assert False, "unmappable file must raise DownloaderFileError"
        assert "Mystery Bundle" in message, message
        assert "extras.nfo" in message, message
        assert "cannot be mapped" in message, message

        rid = _insert_release(
            ctx.db, "strict-unmapped-guid", "Mystery Bundle", downloader="torbox",
            downloader_id="60", download_status="DOWNLOADING",
            downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            volumes=[1], missing_volumes=[1],
        )
        service = FakeTorboxService(state="ready", files=files)
        dest = os.path.join(ctx.tmpdir, "downloads", ctx.scheduler._safe_torrent_dirname("Mystery Bundle"))
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "leftover.cbz.part"), "wb") as handle:
            handle.write(b"partial")
        # task-52: this suite covers the legacy FAILED fallback; pickup is covered separately.
        ctx.db.set_setting("torbox_manual_pickup_enabled", "false")
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.poll_torbox_downloads()
        assert result["failed"] == 1, result
        assert service.fetch_calls == [], "no files may be requested after a strict mapping failure"
        assert ctx.db.get_release_by_id(rid)["download_status"] == "FAILED"
        failures = ctx.events("TORBOX_FAILED")
        assert failures and "cannot be mapped" in (failures[0].get("details") or ""), failures
        assert not os.path.exists(os.path.join(dest, "leftover.cbz.part")), "partial output must be cleaned"
        print("    [PASSED] unmappable names fail the release with no requestdl calls and no partial output.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Wanted volume absent from the torrent -> typed error naming it
# ===========================================================================
def test_strict_absent_volume():
    print("--> Testing a wanted volume absent from the torrent...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        files = _files([1, 2])
        try:
            ctx.scheduler._plan_torbox_selection({"title": "Missing Volume Torrent"}, files, [3])
        except DownloaderFileError as exc:
            message = str(exc)
        else:
            assert False, "absent wanted volume must raise DownloaderFileError"
        assert "Missing Volume Torrent" in message, message
        assert "[3.0]" in message or "3" in message, message
        assert "absent from the torrent" in message, message
        print("    [PASSED] absent volume fails with an actionable message.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Empty wanted set -> no createtorrent/submit at all (no slot consumed)
# ===========================================================================
def test_empty_wanted_skips_submit():
    print("--> Testing empty wanted set skips submission entirely...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        rid = _insert_release(ctx.db, "nothing-missing-guid", "Already Complete", volumes=[1, 2])
        service = FakeTorboxService(state="ready")
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.submit_torbox_download(
                rid, "Already Complete", f"magnet:?xt=urn:btih:{HASH}", missing_volumes=[],
            )
        assert result["success"] is True and result["skipped"] is True, result
        assert service.submit_calls == [], "no torrent may be created when nothing is missing"
        assert "nothing missing" in result["message"].lower(), result
        events = ctx.events("SELECTIVE_DOWNLOAD")
        assert events and "nothing missing" in events[0]["message"].lower(), events
        row = ctx.db.get_release_by_id(rid)
        assert row["download_status"] != "DOWNLOADING", row
        print("    [PASSED] empty wanted -> submit skipped, no createtorrent call.")
    finally:
        ctx.close()


# ===========================================================================
# 6. All files needed -> whole-torrent zip is equivalent and allowed
# ===========================================================================
def test_all_needed_zip():
    print("--> Testing all-files-needed zip selection...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        files = _files([1, 2])
        plan = ctx.scheduler._plan_torbox_selection({"title": "All Needed"}, files, [1, 2])
        assert plan["action"] == "zip" and plan["files"] is None, plan
        assert plan["selected_volumes"] == [1, 2], plan

        rid = _insert_release(
            ctx.db, "all-needed-guid", "All Needed", downloader="torbox",
            downloader_id="70", download_status="DOWNLOADING",
            downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            volumes=[1, 2], missing_volumes=[1, 2],
        )
        service = FakeTorboxService(state="ready", files=files)
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.poll_torbox_downloads()
        assert result["completed"] == 1, result
        assert service.fetch_calls[0][1] is None, service.fetch_calls

        dest = tempfile.mkdtemp(prefix="mangatrax_all_needed_")
        try:
            api = FakeApi().add("/torrents/requestdl", data={"url": "https://cdn.example/torrent.zip"})
            zip_bytes = _zip_bytes({"Series v01.cbz": b"v1", "Series v02.cbz": b"v2"})
            stream = ScriptedStream([zip_bytes])
            downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True)
            with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
                with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                    saved = downloader.fetch_files({"torrent_id": 70}, None, dest)
            assert len(saved) == 2, saved
            request_urls = api.urls_for("/torrents/requestdl")
            assert request_urls and "zip_link=true" in request_urls[0], request_urls
            assert rid
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        print("    [PASSED] all volumes needed -> single zip_link download (equivalent).")
    finally:
        ctx.close()


# ===========================================================================
# 7. Setting off restores the legacy whole-torrent fallback
# ===========================================================================
def test_strict_off_legacy_fallback():
    print("--> Testing torbox_strict_volume_selection=false restores legacy behaviour...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        ctx.db.set_setting("torbox_strict_volume_selection", "false")
        files = [{"id": 1, "name": "Series v01.cbz"}, {"id": 2, "name": "extras.nfo"}]
        plan = ctx.scheduler._plan_torbox_selection({"title": "Legacy Bundle"}, files, [1])
        assert plan["action"] == "zip" and plan["strict"] is False, plan
        assert "unmappable file name" in plan["reason"], plan["reason"]
        print("    [PASSED] legacy permissive fallback still available behind the setting.")
    finally:
        ctx.close()


# ===========================================================================
# 8. Mapping robustness: v01-03, Vol. 4, [Vol 5-6], (v7-v8), Volume 9, short_name
# ===========================================================================
def test_mapping_robustness():
    print("--> Testing file -> volume mapping robustness...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        files = [
            {"id": 1, "name": "Series v01-03.cbz"},
            {"id": 2, "name": "Series Vol. 4.cbz"},
            {"id": 3, "name": "[Vol 5-6] Series.cbz"},
            {"id": 4, "name": "(v7-v8) Series.cbz"},
            {"id": 5, "name": "Series Volume 9.cbz"},
            {"id": 6, "name": "", "short_name": "Series v10.cbz"},
        ]
        plan = ctx.scheduler._plan_torbox_selection(
            {"title": "Mapping Bundle"}, files, [2, 6, 8, 9, 10],
        )
        assert plan["action"] == "files", plan
        assert sorted(item["id"] for item in plan["files"]) == [1, 3, 4, 5, 6], plan["files"]
        mapping = {entry["id"]: entry["volumes"] for entry in plan["mapping"]}
        assert mapping[1] == [1.0, 2.0, 3.0], mapping
        assert mapping[3] == [5.0, 6.0], mapping
        assert mapping[4] == [7.0, 8.0], mapping
        assert mapping[5] == [9.0], mapping
        assert mapping[6] == [10.0], mapping  # short_name fallback used
        print("    [PASSED] ranges/Vol/Volume/bracket/paren forms and short_name all map correctly.")
    finally:
        ctx.close()


# ===========================================================================
# 9. Manual download endpoint computes accurate wanted volumes (matcher fallback)
# ===========================================================================
def test_manual_endpoint_computes_missing():
    print("--> Testing manual download passes accurate wanted volumes...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_strict_manual_")
    try:
        ctx.db.set_setting("downloader_engine", "torbox")
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, link, download_url, status, volumes_json, missing_vols_json) "
                "VALUES (?, ?, ?, ?, 'REQUESTED', ?, '[]');",
                ("strict-manual", "Strict Manual", "https://nyaa.si/view/1",
                 f"magnet:?xt=urn:btih:{HASH}", json.dumps([1, 2, 3])),
            )
            conn.commit()
        release_id = [row["id"] for row in ctx.db.get_releases() if row["guid"] == "strict-manual"][0]

        class _Matcher:
            def check_release(self, release, auto_request_monitored=False):
                return {"missing_volumes": [2.0, 5.0], "status": "REQUESTED"}

        captured = {}

        def fake_submit(release_id_arg, title, download_url, **kwargs):
            captured.update(kwargs)
            return {"success": True, "engine": "torbox", "cached": False, "skipped": False}

        with patch.object(ctx.scheduler, "_series_refresh_matcher", return_value=_Matcher()):
            with patch.object(ctx.scheduler, "submit_torbox_download", side_effect=fake_submit):
                response = ctx.client.post(f"/api/releases/{release_id}/download")
        assert response.status_code == 200, response.json()
        assert captured.get("missing_volumes") == [2.0, 5.0], captured
        print("    [PASSED] matcher-derived missing volumes are passed into the Torbox submission.")
    finally:
        ctx.close()


# ===========================================================================
# 10. Status payload shows the active selection; settings surface
# ===========================================================================
def test_status_selection_and_settings():
    print("--> Testing status selection field + settings surface...")
    from e2e.harness import E2ETestContext

    assert "torbox_strict_volume_selection" in schemas.SETTINGS_KEYS
    model = schemas.validate(
        schemas.SettingsUpdateRequest, {"torbox_strict_volume_selection": "false"},
    )
    assert model.torbox_strict_volume_selection == "false"

    ctx = E2ETestContext(prefix="mangatrax_strict_status_")
    try:
        assert ctx.db.get_setting("torbox_strict_volume_selection") == "true"
        ctx.db.set_setting("downloader_engine", "torbox")
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, link, download_url, status, download_status, "
                "downloader, downloader_id, missing_vols_json, volumes_json) "
                "VALUES (?, ?, ?, ?, 'REQUESTED', 'DOWNLOADING', 'torbox', '88', ?, ?);",
                ("strict-status", "Strict Status", "https://nyaa.si/view/1",
                 f"magnet:?xt=urn:btih:{HASH}", json.dumps([3.0, 4.0]), json.dumps([1, 2, 3, 4])),
            )
            conn.commit()
        fake = FakeTorboxService(state="downloading")
        with patch("app.main.build_downloader", return_value=fake):
            payload = ctx.client.get("/api/downloads/status").json()
        item = payload["torrents"][0]
        assert item["engine"] == "torbox", item
        assert item["wanted_volumes"] == [3.0, 4.0], item
        assert "3, 4" in item["selection"], item
        assert item["strict_volume_selection"] is True, item

        settings_html = open(
            os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8"
        ).read()
        assert 'id="setting-torbox-strict-selection"' in settings_html
        assert "torbox_strict_volume_selection: document.getElementById('setting-torbox-strict-selection')" in settings_html
        assert "old permissive whole-torrent fallback" in settings_html
        print("    [PASSED] status exposes wanted volumes/selection; setting documented in the UI.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running Torbox strict-volume suite (task-25)")
    print("=" * 78)
    tests = [
        test_strict_subset_exact_files,
        test_strict_single_volume,
        test_strict_unmappable_hard_fail,
        test_strict_absent_volume,
        test_empty_wanted_skips_submit,
        test_all_needed_zip,
        test_strict_off_legacy_fallback,
        test_mapping_robustness,
        test_manual_endpoint_computes_missing,
        test_status_selection_and_settings,
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
        print(f"STRICT VOLUME SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STRICT VOLUME TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
