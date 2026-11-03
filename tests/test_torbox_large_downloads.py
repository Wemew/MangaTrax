#!/usr/bin/env python3
"""task-27 suite: Torbox large downloads — configurable cap, per-file fallback,
visible failures with retry/remove, delete-on-failure and log hygiene.

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
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import schemas  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from app.downloader import build_downloader  # noqa: E402
from app.downloader.base import DownloaderFileError  # noqa: E402
from app.downloader.torbox import TorboxDownloader  # noqa: E402
from test_torbox_downloader import (  # noqa: E402
    HASH,
    TORBOX_KEY,
    Ctx,
    FakeApi,
    FakeTorboxService,
    ScriptedStream,
    _insert_release,
)

GIB = 1024 ** 3
BIG_FILE_BYTES = 19_693_900_000  # ~18.3 GiB / 19.7 GB, the user's real torrent size


def _files(count, size=10, start=1):
    return [
        {"id": 0 if index == 30 else index, "name": f"Series v{index:02d}.cbz", "size": size}
        for index in range(start, start + count)
    ]


def _zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _ready_ctx(ctx, max_gb=64):
    ctx.db.set_setting("downloads_dir", os.path.join(ctx.tmpdir, "downloads"))
    ctx.db.set_setting("staging_dir", os.path.join(ctx.tmpdir, "staging"))
    ctx.db.set_setting("torbox_enabled", "true")
    ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
    ctx.db.set_setting("torbox_delete_after_download", "false")
    ctx.db.set_setting("torbox_max_download_gb", str(max_gb))
    ctx.db.set_setting("torbox_strict_volume_selection", "true")


class _FailingFetchService(FakeTorboxService):
    def fetch_files(self, handle, files, dest_dir, progress_cb=None):
        self.fetch_calls.append((handle, files, dest_dir))
        raise DownloaderFileError("requestdl failed (HTTP 503) for Series v01.cbz")




class _FirstFetchOsErrorService(FakeTorboxService):
    """First fetch_files call raises a raw OSError; later calls succeed."""

    def __init__(self, files=None):
        super().__init__(state="ready", files=files or [{"id": 1, "name": "Series v01.cbz", "size": 10}])
        self.fetch_count = 0

    def fetch_files(self, handle, files, dest_dir, progress_cb=None):
        self.fetch_calls.append((handle, files, dest_dir))
        self.fetch_count += 1
        if self.fetch_count == 1:
            raise PermissionError(13, "Permission denied", dest_dir)
        return [{"name": "Series v01.cbz", "path": os.path.join(dest_dir, "Series v01.cbz"), "size": 10}]


# ===========================================================================
# 1. Configurable cap: setting -> factory -> downloader pre-check (zero reads)
# ===========================================================================
def test_configurable_cap_and_precheck():
    print("--> Testing the configurable cap and pre-stream rejection...")
    ctx = Ctx()
    try:
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        default_client = build_downloader(ctx.db, engine="torbox")
        assert default_client.max_file_bytes == 64 * GIB, default_client.max_file_bytes
        ctx.db.set_setting("torbox_max_download_gb", "2")
        client = build_downloader(ctx.db, engine="torbox")
        assert client.max_file_bytes == 2 * GIB, client.max_file_bytes

        downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True, max_file_bytes=16 * GIB)
        dest = tempfile.mkdtemp(prefix="mangatrax_cap_")
        try:
            api = FakeApi()
            read_calls = MagicMock(side_effect=AssertionError("transport must not be touched"))
            with patch.object(downloader, "_requestdl", MagicMock()) as requestdl:
                with patch("app.downloader.torbox.urllib.request.urlopen", read_calls):
                    try:
                        downloader.fetch_files(
                            {"torrent_id": 1},
                            [{"id": 7, "name": "Series v01.cbz", "size": BIG_FILE_BYTES}],
                            dest,
                        )
                    except DownloaderFileError as exc:
                        message = str(exc)
                    else:
                        assert False, "a file above the cap must be rejected before streaming"
            assert "Series v01.cbz" in message, message
            assert "GiB" in message and "torbox_max_download_gb" in message, message
            assert "19" in message or "18" in message, message
            requestdl.assert_not_called()
            read_calls.assert_not_called()
            assert api.calls == []
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        print("    [PASSED] cap is configurable; oversized file fails with size+cap in the message, zero reads.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Planner respects the cap both ways
# ===========================================================================
def test_plan_cap_both_ways():
    print("--> Testing planner cap: lowered rejects, raised allows...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx, max_gb=16)
        files = [{"id": 1, "name": "Series v01.cbz", "size": BIG_FILE_BYTES}]
        release = {"id": 1, "title": "Huge Single", "missing_vols_json": json.dumps([1.0])}
        try:
            ctx.scheduler._plan_torbox_selection(release, files, [1])
        except DownloaderFileError as exc:
            message = str(exc)
        else:
            assert False, "16 GiB cap must reject the 19.7 GB file"
        assert "GiB" in message and "cap" in message.lower(), message

        ctx.db.set_setting("torbox_max_download_gb", "64")
        plan = ctx.scheduler._plan_torbox_selection(release, files, [1])
        # A single needed file is an equivalent whole-torrent zip or a per-file fetch;
        # with the raised cap either is fine (no size error).
        assert plan["action"] in ("zip", "files"), plan
        print("    [PASSED] lowered cap rejects; raised cap allows the same file.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Real-world shape: 31-file torrent, strict subset selects 2 (ids incl. 0)
# ===========================================================================
def test_real_world_31_file_subset_per_file():
    print("--> Testing a 2/31 strict subset download per-file (ids incl. 0)...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        files = _files(31)
        assert files[29]["id"] == 0, files[29]  # the user's log had file id 0 for v30
        release = {"id": 1, "title": "Big Bundle 001-009", "missing_vols_json": json.dumps([30.0, 31.0])}
        plan = ctx.scheduler._plan_torbox_selection(release, files, [30, 31])
        assert plan["action"] == "files", plan
        assert sorted(int(item["id"]) for item in plan["files"]) == [0, 31], plan["files"]
        assert plan["selected_count"] == 2 and plan["total_files"] == 31, plan

        dest = tempfile.mkdtemp(prefix="mangatrax_subset_")
        try:
            api = FakeApi().add("/torrents/requestdl", data={"url": "https://cdn.example/file"})
            stream = ScriptedStream([b"VOL30", b"VOL31"])
            downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True, max_file_bytes=64 * GIB)
            with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
                with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                    saved = downloader.fetch_files({"torrent_id": 55, "size": 31 * 10}, plan["files"], dest)
            assert len(saved) == 2, saved
            urls = api.urls_for("/torrents/requestdl")
            assert len(urls) == 2, urls
            assert any("file_id=0" in url for url in urls), urls
            assert any("file_id=31" in url for url in urls), urls
            assert not any("zip_link=true" in url for url in urls), urls
            assert sorted(os.listdir(dest)) == ["Series v30.cbz", "Series v31.cbz"], os.listdir(dest)
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        print("    [PASSED] exactly the 2 selected files are fetched per-file; id=0 works.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Per-file requestdl failure -> TORBOX_FAILED + persisted download_error
# ===========================================================================
def test_fetch_failure_persists_error_and_audit():
    print("--> Testing a per-file fetch failure is surfaced + persisted...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        rid = _insert_release(
            ctx.db, "fetch-fail-guid", "Series Vol. 01", downloader="torbox",
            downloader_id="55", download_status="DOWNLOADING",
            downloader_started_at="2026-01-01 00:00:00",
            volumes=[1], missing_volumes=[1], torrent_hash=HASH,
        )
        # Fix the started_at so the timeout path does not win over the fetch path.
        with ctx.db.get_connection() as conn:
            conn.execute("UPDATE releases SET downloader_started_at = CURRENT_TIMESTAMP WHERE id = ?;", (rid,))
            conn.commit()
        service = _FailingFetchService(state="ready", files=[{"id": 1, "name": "Series v01.cbz", "size": 10}])
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.poll_torbox_downloads()
        assert result["failed"] == 1, result
        row = ctx.db.get_release_by_id(rid)
        assert row["download_status"] == "FAILED", row
        assert "requestdl failed (HTTP 503)" in (row["download_error"] or ""), row["download_error"]
        assert "attempt 1" in row["download_error"], row["download_error"]
        failures = ctx.events("TORBOX_FAILED")
        assert failures and "requestdl failed" in (failures[-1].get("details") or ""), failures
        assert service.cancelled, "delete-on-failure should free the slot by default"
        print("    [PASSED] failure reason persisted (with attempt/timestamp), audited and remote removed.")
    finally:
        ctx.close()


# ===========================================================================
# 5. delete_on_failure=false keeps the remote torrent
# ===========================================================================
def test_delete_on_failure_setting():
    print("--> Testing torbox_delete_on_failure on/off...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        ctx.db.set_setting("torbox_delete_on_failure", "false")
        rid = _insert_release(
            ctx.db, "keep-fail-guid", "Series Vol. 02", downloader="torbox",
            downloader_id="56", download_status="DOWNLOADING",
            downloader_started_at="2026-01-01 00:00:00",
            volumes=[2], missing_volumes=[2], torrent_hash=HASH,
        )
        with ctx.db.get_connection() as conn:
            conn.execute("UPDATE releases SET downloader_started_at = CURRENT_TIMESTAMP WHERE id = ?;", (rid,))
            conn.commit()
        service = _FailingFetchService(state="ready", files=[{"id": 2, "name": "Series v02.cbz", "size": 10}])
        with patch("app.scheduler.build_downloader", return_value=service):
            ctx.scheduler.poll_torbox_downloads()
        assert ctx.db.get_release_by_id(rid)["download_status"] == "FAILED"
        assert service.cancelled == [], "delete_on_failure=false must keep the remote torrent"
        assert "attempt 1" in (ctx.db.get_release_by_id(rid)["download_error"] or "")
        print("    [PASSED] delete_on_failure=false keeps the failed torrent in Torbox.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Zip only for small all-needed bundles; many files -> exact per-file
# ===========================================================================
def test_zip_vs_per_file_all_needed():
    print("--> Testing zip only for small all-needed bundles...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        small = _files(2)
        small_plan = ctx.scheduler._plan_torbox_selection(
            {"title": "Small"}, small, [1, 2], total_size=20,
        )
        assert small_plan["action"] == "zip", small_plan

        many = _files(30, size=3 * GIB)
        many_plan = ctx.scheduler._plan_torbox_selection(
            {"title": "Big Bundle"}, many, list(range(1, 31)), total_size=90 * GIB,
        )
        assert many_plan["action"] == "files", many_plan
        assert len(many_plan["files"]) == 30, many_plan["files"]
        assert "per-file" in many_plan["reason"], many_plan["reason"]

        dest = tempfile.mkdtemp(prefix="mangatrax_many_")
        try:
            api = FakeApi().add("/torrents/requestdl", data={"url": "https://cdn.example/file"})
            stream = ScriptedStream([b"x" * 8 for _ in range(30)])
            downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True, max_file_bytes=64 * GIB)
            with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
                with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                    saved = downloader.fetch_files({"torrent_id": 60, "size": 90 * GIB}, many_plan["files"], dest)
            assert len(saved) == 30, len(saved)
            urls = api.urls_for("/torrents/requestdl")
            assert len(urls) == 30 and not any("zip_link=true" in url for url in urls), urls
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        print("    [PASSED] 30-file/90 GiB all-needed torrent downloads per-file; small bundle still zips.")
    finally:
        ctx.close()


# ===========================================================================
# 7. Status surfaces failure; Retry (rate-limited) and Remove endpoints work
# ===========================================================================
def test_status_retry_remove():
    print("--> Testing failed status payload, retry and remove endpoints...")
    from e2e.harness import E2ETestContext

    import app.main as main_module

    ctx = E2ETestContext(prefix="mangatrax_torbox_retry_")
    try:
        ctx.db.set_setting("downloader_engine", "torbox")
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, link, download_url, status, "
                "download_status, downloader, downloader_id, torrent_hash, volumes_json, "
                "missing_vols_json, download_error, download_attempts) "
                "VALUES (?, ?, ?, ?, ?, 'REQUESTED', 'FAILED', 'torbox', '55', ?, ?, ?, ?, 2);",
                (
                    "torbox-failed-guid", "Series Vol. 01", "Series",
                    "https://nyaa.si/view/1", f"magnet:?xt=urn:btih:{HASH}",
                    HASH, json.dumps([1]), json.dumps([1]),
                    "Torbox file 'Series v01.cbz' is 19.7 GiB, above the configured cap (attempt 2)",
                ),
            )
            conn.commit()
        release_id = [r["id"] for r in ctx.db.get_releases() if r["guid"] == "torbox-failed-guid"][0]
        service = FakeTorboxService(state="downloading")
        with patch("app.main.build_downloader", return_value=service):
            payload = ctx.client.get("/api/downloads/status").json()
        item = next(row for row in payload["torrents"] if row["release_id"] == release_id)
        assert item["failed"] is True and item["state"] == "failed", item
        assert "above the configured cap" in item["error"], item
        assert item["can_retry"] is True and item["attempts"] == 2, item

        main_module._TORBOX_RETRY_LAST.clear()
        submit = MagicMock(return_value={"success": True, "engine": "torbox", "cached": False})
        with patch("app.main.build_downloader", return_value=service):
            with patch.object(ctx.scheduler, "submit_torbox_download", submit):
                response = ctx.client.post(f"/api/downloads/torbox/{release_id}/retry")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "success", response.json()
        submit.assert_called_once()
        kwargs = submit.call_args.kwargs
        assert kwargs.get("missing_volumes") == [1.0], kwargs
        row = ctx.db.get_release_by_id(release_id)
        assert row["download_error"] == "", row["download_error"]
        second = ctx.client.post(f"/api/downloads/torbox/{release_id}/retry")
        assert second.status_code == 429, second.status_code
        assert "rate" in second.json()["detail"].lower(), second.json()

        with ctx.db.get_connection() as conn:
            conn.execute(
                "UPDATE releases SET download_status = 'FAILED', download_error = 'boom' WHERE id = ?;",
                (release_id,),
            )
            conn.commit()
        with patch("app.main.build_downloader", return_value=service):
            removed = ctx.client.post(f"/api/downloads/torbox/{release_id}/remove")
        assert removed.status_code == 200, removed.text
        assert removed.json()["removed"] is True, removed.json()
        row = ctx.db.get_release_by_id(release_id)
        assert row["download_status"] == "IDLE" and row["download_error"] == "", row
        template = open(
            os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8"
        ).read()
        assert "retryTorbox" in template and "removeTorbox" in template and "t.failed" in template
        assert "t.error" in template
        print("    [PASSED] Failed+error shown; retry clears error and re-submits once; remove clears state.")
    finally:
        ctx.close()


# ===========================================================================
# 8. Settings schema validation + defaults
# ===========================================================================
def test_settings_validation_and_defaults():
    print("--> Testing torbox_max_download_gb / torbox_delete_on_failure settings...")
    for bad in ("0", "-1", "abc", "20000"):
        try:
            schemas.validate(schemas.SettingsUpdateRequest, {"torbox_max_download_gb": bad})
        except schemas.ValidationError:
            continue
        assert False, f"torbox_max_download_gb must reject {bad!r}"
    model = schemas.validate(schemas.SettingsUpdateRequest, {"torbox_max_download_gb": "128"})
    assert model.torbox_max_download_gb == "128", model.torbox_max_download_gb
    for bad in ("maybe", "2"):
        try:
            schemas.validate(schemas.SettingsUpdateRequest, {"torbox_delete_on_failure": bad})
        except schemas.ValidationError:
            continue
        assert False, f"torbox_delete_on_failure must reject {bad!r}"
    model = schemas.validate(schemas.SettingsUpdateRequest, {"torbox_delete_on_failure": "off"})
    assert model.torbox_delete_on_failure == "false", model.torbox_delete_on_failure
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_cap_defaults_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        assert db.get_setting("torbox_max_download_gb") == "64", db.get_setting("torbox_max_download_gb")
        assert db.get_setting("torbox_delete_on_failure") == "true"
        settings_html = open(
            os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8"
        ).read()
        assert "setting-torbox-max-download-gb" in settings_html
        assert "setting-torbox-delete-on-failure" in settings_html
        assert "torbox_max_download_gb: document.getElementById('setting-torbox-max-download-gb')" in settings_html
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("    [PASSED] settings validated, default 64 GiB, delete-on-failure default on, UI wired.")




# ===========================================================================
# 7b. A mid-way per-file failure removes files already written by that call
# ===========================================================================
def test_partial_output_cleanup():
    print("--> Testing partial output cleanup on a mid-fetch failure...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        dest = tempfile.mkdtemp(prefix="mangatrax_partial_")
        try:
            api = FakeApi().add("/torrents/requestdl", data={"url": "https://cdn.example/file"})
            stream = ScriptedStream([b"first-file", DownloaderFileError("stream broke")])
            downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True, max_file_bytes=64 * GIB)
            with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
                with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                    try:
                        downloader.fetch_files(
                            {"torrent_id": 70},
                            [{"id": 1, "name": "Series v01.cbz", "size": 10},
                             {"id": 2, "name": "Series v02.cbz", "size": 10}],
                            dest,
                        )
                    except DownloaderFileError as exc:
                        assert "stream broke" in str(exc), exc
                    else:
                        assert False, "the mid-fetch failure must propagate"
            assert os.listdir(dest) == [], os.listdir(dest)
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        print("    [PASSED] already-written files/partials are removed when a later file fails.")
    finally:
        ctx.close()



# ===========================================================================
# 9b. Filesystem errors become actionable DownloaderFileErrors
# ===========================================================================
def test_filesystem_errors_are_actionable():
    print("--> Testing filesystem write errors are converted (errno + path + free space)...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        dest = tempfile.mkdtemp(prefix="mangatrax_fs_")
        try:
            for errno_value, strerror in ((13, "Permission denied"), (28, "No space left on device")):
                downloader = TorboxDownloader(api_key=TORBOX_KEY, enabled=True, max_file_bytes=64 * GIB)
                api = FakeApi().add("/torrents/requestdl", data={"url": "https://cdn.example/file"})
                stream = ScriptedStream([b"data"])
                with patch("app.downloader.torbox.urllib.request.urlopen", side_effect=api):
                    with patch("app.downloader.torbox._open_no_redirect", side_effect=stream):
                        with patch("builtins.open", side_effect=OSError(errno_value, strerror)):
                            try:
                                downloader.fetch_files(
                                    {"torrent_id": 80},
                                    [{"id": 1, "name": "Series v01.cbz", "size": 10}],
                                    dest,
                                )
                            except DownloaderFileError as exc:
                                message = str(exc)
                            else:
                                assert False, f"errno {errno_value} must surface as DownloaderFileError"
                assert "Cannot write" in message, message
                assert f"errno {errno_value}" in message, message
                assert strerror in message, message
                assert "free space on the destination" in message, message
                assert "Series v01.cbz" in message, message
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        print("    [PASSED] PermissionError/ENOSPC become actionable errors; no bare OSError escapes.")
    finally:
        ctx.close()


# ===========================================================================
# 9c. A raw OSError during one release's fetch cannot abort the poll cycle
# ===========================================================================
def test_poll_contains_per_release_oserror_and_continues():
    print("--> Testing poll contains a per-release OSError and continues...")
    ctx = Ctx()
    try:
        _ready_ctx(ctx)
        first = _insert_release(
            ctx.db, "fs-fail-guid", "Series Vol. 01", downloader="torbox",
            downloader_id="81", download_status="DOWNLOADING",
            downloader_started_at="2026-01-01 00:00:00",
            volumes=[1], missing_volumes=[1], torrent_hash=HASH,
        )
        second = _insert_release(
            ctx.db, "fs-ok-guid", "Series Vol. 02", downloader="torbox",
            downloader_id="82", download_status="DOWNLOADING",
            downloader_started_at="2026-01-01 00:00:00",
            volumes=[1], missing_volumes=[1], torrent_hash=HASH,
        )
        with ctx.db.get_connection() as conn:
            conn.execute("UPDATE releases SET downloader_started_at = CURRENT_TIMESTAMP WHERE id IN (?, ?);", (first, second))
            conn.commit()
        service = _FirstFetchOsErrorService()
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.poll_torbox_downloads()  # must NOT raise
        assert result["failed"] == 1 and result["completed"] == 1, result
        failed_row = ctx.db.get_release_by_id(first)
        assert failed_row["download_status"] == "FAILED", failed_row
        error = failed_row["download_error"] or ""
        assert "PermissionError" in error and "Permission denied" in error, error
        assert "attempt 1" in error, error
        assert ctx.db.get_release_by_id(second)["download_status"] == "COMPLETED"
        failures = ctx.events("TORBOX_FAILED")
        assert failures and "PermissionError" in (failures[-1].get("details") or ""), failures
        print("    [PASSED] OSError is contained (persisted/audited) and the next release completes.")
    finally:
        ctx.close()

def main():
    print("=" * 78)
    print("Running Torbox large-download suite (task-27)")
    print("=" * 78)
    tests = [
        test_configurable_cap_and_precheck,
        test_plan_cap_both_ways,
        test_real_world_31_file_subset_per_file,
        test_fetch_failure_persists_error_and_audit,
        test_delete_on_failure_setting,
        test_zip_vs_per_file_all_needed,
        test_partial_output_cleanup,
        test_filesystem_errors_are_actionable,
        test_poll_contains_per_release_oserror_and_continues,
        test_status_retry_remove,
        test_settings_validation_and_defaults,
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
        print(f"TORBOX LARGE-DOWNLOAD SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL TORBOX LARGE-DOWNLOAD TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
