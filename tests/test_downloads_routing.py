#!/usr/bin/env python3
"""task-22 suite: downloads routing (DOWNLOADS_DIR -> tag -> STAGING_DIR).

Model: raw downloads from both engines land in ``downloads_dir`` and stay untouched
unless their series already exists in Calibre, in which case the .cbz is tagged and
moved to ``staging_dir`` (relative subpath preserved).  Unknown-series downloads are
left alone and summarised as awaiting a Calibre scan.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import logging
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
from app.covers import CoverCache  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from app.scheduler import MangaScheduler  # noqa: E402
from app.tagger import cbz_tagger  # noqa: E402
from app.tagger import pipeline as downloads_pipeline  # noqa: E402
from app.tagger.pipeline import process_downloads_to_staging, scan_awaiting_series  # noqa: E402

KNOWN_TEMPLATE = {
    "known series": {
        "series_name": "Known Series",
        "authors": "Test Author",
        "publisher": "Test Publisher",
        "tags": "Manga",
    },
}


def _make_cbz(path, entries=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in (entries or {"001.jpg": b"image-bytes"}).items():
            archive.writestr(name, payload)


class Ctx:
    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_routing_")
        self.downloads = os.path.join(self.tmpdir, "downloads")
        self.staging = os.path.join(self.tmpdir, "staging")
        os.makedirs(self.downloads, exist_ok=True)
        os.makedirs(self.staging, exist_ok=True)
        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.db.set_setting("downloads_dir", self.downloads)
        self.db.set_setting("staging_dir", self.staging)
        self.db.set_setting("calibre_path", os.path.join(self.tmpdir, "calibre"))
        self.db.set_setting("qbit_enabled", "false")
        self.scheduler = MangaScheduler(self.db, CoverCache(self.tmpdir))

    def run(self, templates=None, actor="scheduler"):
        return process_downloads_to_staging(
            self.downloads, self.staging, templates or {}, self.db, actor=actor,
        )

    def events(self, event_type):
        return [row for row in self.db.get_logs(limit=200) if row["event_type"] == event_type]

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# 1. Unknown series: whole series stays in downloads, summarised as awaiting
# ===========================================================================
def test_series_unknown_stays():
    print("--> Testing unknown series stays in downloads...")
    ctx = Ctx()
    try:
        early = os.path.join(ctx.downloads, "Unreleased Saga Vol. 01.cbz")
        later = os.path.join(ctx.downloads, "sub", "Unreleased Saga Vol. 02.cbz")
        _make_cbz(early)
        _make_cbz(later)
        result = ctx.run(templates={})
        assert result["scanned"] == 2, result
        assert result["tagged_and_moved"] == 0, result
        assert result["awaiting_calibre"] == 2, result
        assert result["failed"] == 0 and result["skipped_existing"] == 0, result
        assert os.path.isfile(early) and os.path.isfile(later), "unknown series must not move"
        assert not os.path.exists(os.path.join(ctx.staging, "Unreleased Saga Vol. 01.cbz"))
        assert not cbz_tagger.has_comicinfo(early), "unknown series must not be tagged"
        assert result["series_awaiting"] and result["series_awaiting"][0]["series"] == "Unreleased Saga", result
        assert result["series_awaiting"][0]["count"] == 2, result
        awaiting_events = ctx.events("AWAITING_CALIBRE_SERIES")
        assert len(awaiting_events) == 1, awaiting_events  # one summary, not per-file spam
        details = awaiting_events[0].get("details") or ""
        assert "Unreleased Saga" in details and "2" in details, details
        assert ctx.events("TAGGED_AND_MOVED_TO_STAGING") == []
        print("    [PASSED] all volumes kept; one awaiting summary event; nothing tagged/moved.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Known series: tag + move, subpath preserved, audit event
# ===========================================================================
def test_series_known_tags_and_moves():
    print("--> Testing known series is tagged and moved to staging...")
    ctx = Ctx()
    try:
        source = os.path.join(ctx.downloads, "nested", "Known Series Vol. 02.cbz")
        _make_cbz(source)
        result = ctx.run(templates=KNOWN_TEMPLATE)
        assert result == {
            "scanned": 1, "tagged_and_moved": 1, "awaiting_calibre": 0,
            "failed": 0, "skipped_existing": 0, "series_awaiting": [],
            "removed": 1, "pruned_count": 1, "pruned_dirs": ["nested"], "skipped_busy": 0,
        }, result
        destination = os.path.join(ctx.staging, "nested", "Known Series Vol. 02.cbz")
        assert os.path.isfile(destination), destination
        assert not os.path.exists(source), "source must be removed after the move"
        fields = cbz_tagger.read_comicinfo(destination)
        assert fields and fields.get("Series") == "Known Series", fields
        assert fields.get("Volume") == "2", fields
        moved = ctx.events("TAGGED_AND_MOVED_TO_STAGING")
        assert len(moved) == 1, moved
        details = moved[0].get("details") or ""
        assert "rename" in details, details  # same-device preference
        assert moved[0].get("actor") == "scheduler", moved[0]
        print("    [PASSED] tagged with ComicInfo, moved with subpath, source removed, audited as rename.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Series becomes known between passes -> waiting files unlocked
# ===========================================================================
def test_series_appears_between_passes():
    print("--> Testing a series appearing in Calibre unlocks waiting files...")
    ctx = Ctx()
    try:
        source = os.path.join(ctx.downloads, "Late Bloomer Vol. 03.cbz")
        _make_cbz(source)
        first = ctx.run(templates={})
        assert first["awaiting_calibre"] == 1 and first["tagged_and_moved"] == 0, first
        assert os.path.isfile(source)
        templates = {"late bloomer": {"series_name": "Late Bloomer", "authors": "", "publisher": "", "tags": ""}}
        second = ctx.run(templates=templates)
        assert second["tagged_and_moved"] == 1 and second["awaiting_calibre"] == 0, second
        destination = os.path.join(ctx.staging, "Late Bloomer Vol. 03.cbz")
        assert os.path.isfile(destination) and not os.path.exists(source)
        assert cbz_tagger.has_comicinfo(destination)
        print("    [PASSED] second pass tags and moves the previously waiting file.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Untaggable/corrupt archive stays in downloads and is audited
# ===========================================================================
def test_failed_archive_stays():
    print("--> Testing an untaggable archive stays in downloads...")
    ctx = Ctx()
    try:
        broken = os.path.join(ctx.downloads, "Known Series Vol. 04.cbz")
        with open(broken, "wb") as handle:
            handle.write(b"this is not a zip archive")
        result = ctx.run(templates=KNOWN_TEMPLATE)
        assert result["failed"] == 1, result
        assert result["tagged_and_moved"] == 0, result
        assert os.path.isfile(broken), "failed archives must never move"
        failures = ctx.events("DOWNLOADS_ROUTING_FAILED")
        assert failures, ctx.events("DOWNLOADS_ROUTING_FAILED")
        assert "Known Series Vol. 04.cbz" in (failures[0].get("details") or ""), failures[0]
        print("    [PASSED] corrupt archive stays in downloads; failure count + audit recorded.")
    finally:
        ctx.close()


# ===========================================================================
# 5. No overwrite: existing destination is skipped and counted
# ===========================================================================
def test_no_overwrite_skips_existing():
    print("--> Testing no-overwrite on existing staging destination...")
    ctx = Ctx()
    try:
        source = os.path.join(ctx.downloads, "Known Series Vol. 05.cbz")
        destination = os.path.join(ctx.staging, "Known Series Vol. 05.cbz")
        _make_cbz(source)
        _make_cbz(destination, entries={"existing.jpg": b"existing"})
        before = open(destination, "rb").read()
        result = ctx.run(templates=KNOWN_TEMPLATE)
        assert result["skipped_existing"] == 1, result
        assert result["tagged_and_moved"] == 0, result
        assert os.path.isfile(source), "skipped source is left in downloads"
        assert open(destination, "rb").read() == before, "destination must never be overwritten"
        print("    [PASSED] existing destination skipped; source kept; destination bytes untouched.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Same-device rename preferred; cross-device copy+delete fallback
# ===========================================================================
def test_move_preference():
    print("--> Testing same-device rename vs cross-device copy fallback...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_move_")
    try:
        source = os.path.join(tmpdir, "a.cbz")
        destination = os.path.join(tmpdir, "sub", "a.cbz")
        with open(source, "wb") as handle:
            handle.write(b"data")
        replaced = []
        real_replace = os.replace

        def record_replace(src, dst):
            replaced.append((src, dst))
            real_replace(src, dst)

        with patch("app.tagger.pipeline.os.replace", side_effect=record_replace):
            kind = downloads_pipeline._move_with_preference(source, destination)
        assert kind == "rename", kind
        assert replaced == [(source, destination)], replaced
        assert os.path.isfile(destination) and not os.path.exists(source)

        # Cross-device: _device_of 1 vs 2 -> copy2 + remove.
        source2 = os.path.join(tmpdir, "b.cbz")
        destination2 = os.path.join(tmpdir, "staging2", "b.cbz")
        with open(source2, "wb") as handle:
            handle.write(b"cross-device")
        copied = []
        removed = []
        real_copy2 = shutil.copy2
        real_remove = os.remove

        def record_copy(src, dst, *args, **kwargs):
            copied.append((src, dst))
            return real_copy2(src, dst, *args, **kwargs)

        def record_remove(path, *args, **kwargs):
            removed.append(path)
            return real_remove(path, *args, **kwargs)

        with patch("app.tagger.pipeline._device_of", side_effect=[1, 2]):
            with patch("app.tagger.pipeline.shutil.copy2", side_effect=record_copy):
                with patch("app.tagger.pipeline.os.remove", side_effect=record_remove):
                    kind2 = downloads_pipeline._move_with_preference(source2, destination2)
        assert kind2 == "copy", kind2
        assert copied and copied[0][0] == source2, copied
        assert removed == [source2], removed
        assert os.path.isfile(destination2) and not os.path.exists(source2)
        print("    [PASSED] same-device uses os.replace only; cross-device copies, renames then deletes.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 7. Path validation + same-as-staging refusal
# ===========================================================================
def test_path_validation_and_settings():
    print("--> Testing downloads_dir validation and settings wiring...")
    bad_values = ["downloads", "/srv/dl\x00x", "/srv/dl/../etc", ""]
    for bad in bad_values:
        try:
            schemas.validate(schemas.SettingsUpdateRequest, {"downloads_dir": bad})
        except schemas.ValidationError:
            continue
        assert False, f"downloads_dir must be rejected: {bad!r}"
    good = schemas.validate(schemas.SettingsUpdateRequest, {"downloads_dir": "/srv/downloads"})
    assert good.downloads_dir == "/srv/downloads", good.downloads_dir
    assert "downloads_dir" in schemas.SETTINGS_KEYS

    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_routing_settings_")
    try:
        ctx.db.set_setting("staging_dir", os.path.join(ctx.temp_dir, "staging_dir"))
        same = ctx.client.post("/api/settings", json_data={"downloads_dir": os.path.join(ctx.temp_dir, "staging_dir")})
        assert same.status_code == 400, (same.status_code, same.json())
        assert "staging_dir" in (same.json().get("detail") or ""), same.json()
        distinct = os.path.join(ctx.temp_dir, "downloads_dir")
        ok = ctx.client.post("/api/settings", json_data={"downloads_dir": distinct})
        assert ok.status_code == 200, ok.json()
        assert ctx.db.get_setting("downloads_dir") == distinct
        payload = ctx.client.get("/api/settings").json()
        assert payload["settings"]["downloads_dir"] == distinct, payload
    finally:
        ctx.close()
    print("    [PASSED] relative/NUL/traversal/empty rejected; same-as-staging 400; valid value stored.")


# ===========================================================================
# 8. Both engines save to downloads_dir (qbit savepath + torbox submit/fetch)
# ===========================================================================
def test_engine_savepaths_use_downloads_dir():
    print("--> Testing both engines save into downloads_dir...")
    ctx = Ctx()
    try:
        # Torbox submit: savepath must be under downloads_dir.
        fake = MagicMock()
        fake.submit.return_value = {
            "handle": {"engine": "torbox", "torrent_id": 9, "hash": "0" * 40},
            "state": "queued", "cached": False,
        }
        release_id = ctx.db.upsert_release({
            "guid": "savepath-guid", "title": "Savepath Vol. 01",
            "download_url": "magnet:?xt=urn:btih:" + "0" * 40,
            "status": "REQUESTED", "download_status": "IDLE",
            "volumes": [1], "missing_volumes": [1],
        })[1]
        with patch("app.scheduler.build_downloader", return_value=fake):
            result = ctx.scheduler.submit_torbox_download(
                release_id, "Savepath Vol. 01", "magnet:?xt=urn:btih:" + "0" * 40, missing_volumes=[1],
            )
        assert result["success"] is True, result
        savepath = fake.submit.call_args[0][1]
        assert os.path.realpath(savepath).startswith(os.path.realpath(ctx.downloads)), savepath

        # Torbox poll fetch destination must be under downloads_dir too.
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", "fake-key")
        poll_fake = MagicMock()
        poll_fake.enabled = True
        poll_fake.status.return_value = {
            "state": "ready", "raw_state": "completed", "progress": 1.0,
            "files": [{"id": 1, "name": "Savepath Vol. 01.cbz", "size": 1}],
        }
        poll_fake.fetch_files.return_value = [{"name": "Savepath Vol. 01.cbz", "path": "x", "size": 1}]
        poll_fake.cancel.return_value = True
        ctx.db.update_download_handle(release_id, "torbox", downloader_id="9", torrent_hash="0" * 40)
        with patch("app.scheduler.build_downloader", return_value=poll_fake):
            ctx.scheduler.poll_torbox_downloads()
        _handle, _selection, dest_dir = poll_fake.fetch_files.call_args[0]
        assert os.path.realpath(dest_dir).startswith(os.path.realpath(ctx.downloads)), dest_dir
    finally:
        ctx.close()

    # qBittorrent manual download route saves into downloads_dir.
    from e2e.harness import E2ETestContext

    ectx = E2ETestContext(prefix="mangatrax_routing_qbit_")
    try:
        downloads_dir = os.path.join(ectx.temp_dir, "downloads")
        ectx.db.set_setting("downloads_dir", downloads_dir)
        with ectx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, link, download_url, status) VALUES (?, ?, ?, ?, 'REQUESTED');",
                ("qbit-savepath", "Qbit Savepath", "https://nyaa.si/view/1",
                 "magnet:?xt=urn:btih:" + "1" * 40),
            )
            conn.commit()
        target_id = [row["id"] for row in ectx.db.get_releases() if row["guid"] == "qbit-savepath"][0]
        qbit = MagicMock()
        qbit.enabled = True
        qbit.add_torrent.return_value = True
        qbit.extract_torrent_hash.return_value = "1" * 40
        qbit.apply_selective_volume_download.return_value = {"status": "all_needed"}
        with patch.object(ectx.scheduler, "get_qbit_client", return_value=qbit):
            with patch.object(ectx.scheduler, "process_downloads_to_staging", return_value={}):
                response = ectx.client.post(f"/api/releases/{target_id}/download")
        assert response.status_code == 200, response.json()
        assert qbit.add_torrent.call_args.kwargs["savepath"] == downloads_dir, qbit.add_torrent.call_args
    finally:
        ectx.close()
    print("    [PASSED] qBittorrent savepath, Torbox submit savepath and Torbox fetch destination use downloads_dir.")


# ===========================================================================
# 9. Idempotency + legacy staging files untouched
# ===========================================================================
def test_idempotent_and_staging_untouched():
    print("--> Testing idempotency and untouched legacy staging files...")
    ctx = Ctx()
    try:
        _make_cbz(os.path.join(ctx.downloads, "Known Series Vol. 06.cbz"))
        legacy = os.path.join(ctx.staging, "Legacy In Staging Vol. 01.cbz")
        _make_cbz(legacy)
        first = ctx.run(templates=KNOWN_TEMPLATE)
        assert first["scanned"] == 1 and first["tagged_and_moved"] == 1, first
        second = ctx.run(templates=KNOWN_TEMPLATE)
        assert second == {
            "scanned": 0, "tagged_and_moved": 0, "awaiting_calibre": 0,
            "failed": 0, "skipped_existing": 0, "series_awaiting": [],
            "removed": 0, "pruned_count": 0, "pruned_dirs": [], "skipped_busy": 0,
        }, second
        assert os.path.isfile(legacy), "files already in staging must be untouched by the routing pass"
        print("    [PASSED] second pass is a no-op; pre-existing staging files untouched.")
    finally:
        ctx.close()


# ===========================================================================
# 10. Manual Process Downloads endpoint runs the pipeline
# ===========================================================================
def test_manual_process_endpoint_and_ui():
    print("--> Testing manual Process Downloads endpoint + UI surface...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_routing_manual_")
    try:
        downloads_dir = os.path.join(ctx.temp_dir, "downloads")
        staging_dir = os.path.join(ctx.temp_dir, "staging")
        os.makedirs(downloads_dir, exist_ok=True)
        os.makedirs(staging_dir, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads_dir)
        ctx.db.set_setting("staging_dir", staging_dir)
        ctx.db.sync_calibre_series([
            {"series_name": "Known Series", "series_id": 1, "book_count": 1, "latest_index": 1,
             "authors": "Author", "publisher": "", "tags": ""},
        ])
        _make_cbz(os.path.join(downloads_dir, "Known Series Vol. 01.cbz"))
        _make_cbz(os.path.join(downloads_dir, "Unknown Series Vol. 01.cbz"))
        response = ctx.client.post("/api/staging/tag")
        payload = response.json()
        assert response.status_code == 200, payload
        assert payload.get("tagged_and_moved") == 1, payload
        assert payload.get("awaiting_calibre") == 1, payload
        assert os.path.isfile(os.path.join(staging_dir, "Known Series Vol. 01.cbz"))
        assert os.path.isfile(os.path.join(downloads_dir, "Unknown Series Vol. 01.cbz"))
        events = {row["event_type"] for row in ctx.db.get_logs(limit=200)}
        assert "TAGGED_AND_MOVED_TO_STAGING" in events, events

        # UI/docs surface.
        downloads_html = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
        assert "Awaiting Calibre Series" in downloads_html
        assert "Process Downloads" in downloads_html
        settings_html = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
        assert 'id="setting-downloads-dir"' in settings_html
        assert "same drive" in settings_html.lower()
        assert "downloads_dir: document.getElementById('setting-downloads-dir')" in settings_html
        plan_doc = open(os.path.join(BASE_DIR, "TORBOX_INTEGRATION_PLAN.md"), encoding="utf-8").read()
        assert "DOWNLOADS_DIR" in plan_doc and "STAGING_DIR" in plan_doc
        assert "import_dir" not in plan_doc, "plan doc still contradicts the routing model"
        print("    [PASSED] manual endpoint tags/moves known series, leaves unknown, UI+plan docs updated.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running downloads routing suite (task-22)")
    print("=" * 78)
    tests = [
        test_series_unknown_stays,
        test_series_known_tags_and_moves,
        test_series_appears_between_passes,
        test_failed_archive_stays,
        test_no_overwrite_skips_existing,
        test_move_preference,
        test_path_validation_and_settings,
        test_engine_savepaths_use_downloads_dir,
        test_idempotent_and_staging_untouched,
        test_manual_process_endpoint_and_ui,
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
        print(f"ROUTING SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL DOWNLOADS ROUTING TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
