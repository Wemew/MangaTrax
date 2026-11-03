#!/usr/bin/env python3
"""task-26 suite: in-flight auto-download dedupe + JPEG XL throw-away filter.

In-flight dedupe: a second release (another group/quality/source) for a volume that is
already DOWNLOADING or COMPLETED-but-not-yet-in-Calibre is never auto-downloaded again;
manual downloads remain an explicit override.  JPEG XL releases are discarded by the
feed filters, typed ``jpegxl`` (hidden from manga views, marked ``[JPEGXL]`` when a
stored row is shown) and never auto-downloaded.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.database import MEDIA_TYPE_JPEGXL, MangaDatabase  # noqa: E402
from app.parser import is_jpegxl_release  # noqa: E402
from app.parser import extract_manga_bundle_and_volumes  # noqa: E402
from app.providers.base import ReleaseItem  # noqa: E402
from test_chapter_guard_and_description_cleanup import _IngestContext  # noqa: E402


def _release(title, guid):
    """Provider-shaped release WITH parsed volumes/bundle fields (feeds rely on them)."""
    parsed = extract_manga_bundle_and_volumes(title)
    return ReleaseItem(
        title=title,
        link=f"https://nyaa.si/view/{guid}",
        download_url=f"magnet:?xt=urn:btih:{guid}",
        series_name=parsed["series_name"],
        is_bundle=parsed["is_bundle"],
        volume_start=parsed["volume_start"],
        volume_end=parsed["volume_end"],
        volumes=parsed["volumes"],
        guid=guid,
    )
from test_torbox_downloader import HASH, TORBOX_KEY, Ctx, FakeTorboxService, _insert_release  # noqa: E402


def _fake_qbit(selective=None):
    qbit = MagicMock()
    qbit.enabled = True
    qbit.add_torrent.return_value = True
    qbit.extract_torrent_hash.return_value = "inflighthash"
    qbit.apply_selective_volume_download.return_value = selective or {
        "status": "filtered", "skipped_volumes": [], "kept_volumes": [],
    }
    return qbit


def _enable_feed_auto_download(ctx):
    with ctx.db.get_connection() as conn:
        conn.execute("UPDATE feeds SET auto_download = 1;")
        conn.commit()


def _seed_release(ctx, guid, title, series, volumes, download_status, status="REQUESTED", **extra):
    with ctx.db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO releases
                (guid, title, series_name, link, download_url, status, download_status,
                 downloader, downloader_id, volumes_json, missing_vols_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                guid, title, series, f"https://nyaa.si/view/{guid}",
                f"magnet:?xt=urn:btih:{guid}",
                status, download_status, extra.get("downloader", ""),
                extra.get("downloader_id", ""),
                json.dumps(volumes), json.dumps(volumes),
            ),
        )
        conn.commit()
        return cur.lastrowid


def _events(ctx, event_type):
    return [row for row in ctx.db.get_logs(limit=300) if row["event_type"] == event_type]


# ===========================================================================
# 1. JPEG XL detection + feed discard
# ===========================================================================
def test_jpegxl_detection_and_feed_discard():
    print("--> Testing JPEG XL detection and feed discard...")
    assert is_jpegxl_release("[JPEGXL] Known Series v03")
    assert is_jpegxl_release("Known Series v01 (JPEG XL)")
    assert is_jpegxl_release("Known Series v02 JXL")
    assert is_jpegxl_release("Known Series v01 jpeg-xL")
    assert is_jpegxl_release("Known Series v01 .jxl")
    assert not is_jpegxl_release("Known Series v01")
    assert not is_jpegxl_release("JXLover v01")
    assert not is_jpegxl_release("")

    ctx = _IngestContext(calibre_series={"Known Series": [1, 2]}, settings={"auto_download": "true"})
    try:
        _enable_feed_auto_download(ctx)
        qbit = _fake_qbit()
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            result = ctx.poll(_release("[JPEGXL] Known Series v03", "jxl-guid"))
        assert result["releases_found"] == 0, result
        assert ctx.releases() == [], "JPEG XL releases must not be ingested"
        qbit.add_torrent.assert_not_called()
        logs = ctx.db.get_logs(limit=100)
        assert not [row for row in logs if row["event_type"] in ("AUTO_DOWNLOAD", "IN_FLIGHT_DEDUPE")], logs
        discards = _events(ctx, "JPEGXL_DISCARDED")
        assert len(discards) == 1 and "[JPEGXL]" in discards[0]["message"], discards
        # Re-polling the same release must not spam the audit log.
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            ctx.poll(_release("[JPEGXL] Known Series v03", "jxl-guid"))
        assert len(_events(ctx, "JPEGXL_DISCARDED")) == 1, _events(ctx, "JPEGXL_DISCARDED")
        print("    [PASSED] JPEG XL tags/formats are recognised, discarded and marked [JPEGXL] once.")
    finally:
        ctx.close()


# ===========================================================================
# 2. JPEG XL media typing (+ UI marker)
# ===========================================================================
def test_jpegxl_media_typing_and_ui():
    print("--> Testing JPEG XL media typing and [JPEGXL] marker...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_jpegxl_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, 'DISCOVERY');",
                ("jxl-stored", "[JPEGXL] Old Release v01", "Old Release"),
            )
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status) VALUES (?, ?, ?, 'DISCOVERY');",
                ("normal-stored", "Normal Release v01", "Normal Release"),
            )
            conn.commit()
        db.set_setting("jpegxl_filter_applied", "")
        typed = db._run_jpegxl_filter_once()
        assert typed == 1, typed
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT media_type FROM releases WHERE guid = 'jxl-stored';")
            assert cur.fetchone()["media_type"] == MEDIA_TYPE_JPEGXL
            cur.execute("SELECT media_type FROM releases WHERE guid = 'normal-stored';")
            assert cur.fetchone()["media_type"] != MEDIA_TYPE_JPEGXL
        visible = {row["guid"] for row in db.get_releases()}
        assert "jxl-stored" not in visible, visible
        assert "normal-stored" in visible, visible
        assert db._run_jpegxl_filter_once() == 0, "typing pass must be idempotent"
        template = open(
            os.path.join(BACKEND_DIR, "app", "templates", "partials", "flat_release_row_chunk.html"),
            encoding="utf-8",
        ).read()
        assert "[JPEGXL]" in template and "media_type == 'jpegxl'" in template
        print("    [PASSED] stored JPEG XL rows are typed, hidden from views and marked [JPEGXL].")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 3. In-flight dedupe: DOWNLOADING suppresses the second release
# ===========================================================================
def test_inflight_suppresses_auto_download():
    print("--> Testing in-flight dedupe suppresses a duplicate auto-download...")
    ctx = _IngestContext(calibre_series={"Known Series": [1, 2]}, settings={"auto_download": "true"})
    try:
        _enable_feed_auto_download(ctx)
        _seed_release(ctx, "old-group-guid", "Known Series Vol. 03 (OldGroup)",
                      "Known Series", [3], "DOWNLOADING")
        qbit = _fake_qbit()
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            result = ctx.poll(_release("Known Series Vol. 03 (NewGroup)", "new-group-guid"))
        assert result["sent_to_downloader"] == 0, result
        qbit.add_torrent.assert_not_called()
        rows = {row["title"]: row for row in ctx.releases()}
        assert "Known Series Vol. 03 (NewGroup)" in rows, rows
        new_row = rows["Known Series Vol. 03 (NewGroup)"]
        assert new_row["status"] == "REQUESTED", new_row
        dedupe_events = _events(ctx, "IN_FLIGHT_DEDUPE")
        assert len(dedupe_events) == 1, dedupe_events
        assert "already in flight" in dedupe_events[0]["message"], dedupe_events[0]
        assert "no duplicate submitted" in dedupe_events[0]["message"], dedupe_events[0]
        print("    [PASSED] second release for a DOWNLOADING volume is not auto-downloaded.")
    finally:
        ctx.close()


# ===========================================================================
# 4. COMPLETED-but-not-in-Calibre reserves; FAILED/IDLE does not
# ===========================================================================
def test_completed_reserves_failed_does_not():
    print("--> Testing reservation statuses (COMPLETED reserves, FAILED does not)...")
    ctx = _IngestContext(calibre_series={"Known Series": [1, 2]}, settings={"auto_download": "true"})
    try:
        _enable_feed_auto_download(ctx)
        _seed_release(ctx, "completed-guid", "Known Series Vol. 03 (Done)",
                      "Known Series", [3], "COMPLETED")
        qbit = _fake_qbit()
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            result = ctx.poll(_release("Known Series Vol. 03 (Alt)", "alt-guid"))
        assert result["sent_to_downloader"] == 0, result
        qbit.add_torrent.assert_not_called()

        # A FAILED row releases its reservation: the alt release may download.
        _seed_release(ctx, "failed-guid", "Known Series Vol. 04 (Failed)",
                      "Known Series", [4], "FAILED")
        qbit2 = _fake_qbit()
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit2):
            result2 = ctx.poll(_release("Known Series Vol. 04 (Alt)", "alt-vol4-guid"))
        assert result2["sent_to_downloader"] == 1, result2
        qbit2.add_torrent.assert_called_once()
        print("    [PASSED] COMPLETED (awaiting Calibre) reserves; FAILED releases the volume.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Partial bundle: only the unreserved volumes are requested
# ===========================================================================
def test_partial_bundle_filters_wanted():
    print("--> Testing a partially-reserved bundle downloads only the remaining volume...")
    ctx = _IngestContext(calibre_series={"Known Series": [1, 2]}, settings={"auto_download": "true"})
    try:
        _enable_feed_auto_download(ctx)
        _seed_release(ctx, "inflight-vol3", "Known Series Vol. 03 (OldGroup)",
                      "Known Series", [3], "DOWNLOADING")
        qbit = _fake_qbit()
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            result = ctx.poll(_release("Known Series Vol. 03-04", "bundle-guid"))
        assert result["sent_to_downloader"] == 1, result
        qbit.add_torrent.assert_called_once()
        qbit.apply_selective_volume_download.assert_called_once()
        _hash, wanted = qbit.apply_selective_volume_download.call_args[0]
        assert wanted == [4.0], wanted
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT download_wanted_json FROM releases WHERE guid = 'bundle-guid';")
            stored = json.loads(cur.fetchone()["download_wanted_json"])
        assert stored == [4.0], stored
        dedupe_events = _events(ctx, "IN_FLIGHT_DEDUPE")
        assert dedupe_events and "downloading only [4.0]" in dedupe_events[0]["message"], dedupe_events
        print("    [PASSED] vol 3 in flight -> bundle requests only vol 4 and persists the wanted set.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Manual downloads bypass the in-flight filter (explicit override)
# ===========================================================================
def test_manual_download_bypasses_dedupe():
    print("--> Testing manual download bypasses in-flight dedupe...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_inflight_manual_")
    try:
        ctx.db.set_setting("downloader_engine", "torbox")
        _seed_release(ctx, "manual-old-guid", "Known Series Vol. 03 (Old)",
                      "Known Series", [3], "DOWNLOADING")
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, link, download_url, status, "
                "volumes_json, missing_vols_json) "
                "VALUES (?, ?, ?, ?, ?, 'REQUESTED', ?, ?);",
                ("manual-new-guid", "Known Series Vol. 03 (Manual)", "Known Series",
                 "https://nyaa.si/view/manual-new-guid", f"magnet:?xt=urn:btih:{HASH}",
                 json.dumps([3]), json.dumps([3])),
            )
            conn.commit()
        release_id = [row["id"] for row in ctx.db.get_releases() if row["guid"] == "manual-new-guid"][0]
        captured = {}

        def fake_submit(release_id_arg, title, download_url, **kwargs):
            captured.update(kwargs)
            return {"success": True, "engine": "torbox", "cached": False, "skipped": False}

        with patch.object(ctx.scheduler, "submit_torbox_download", side_effect=fake_submit):
            response = ctx.client.post(f"/api/releases/{release_id}/download")
        assert response.status_code == 200, response.json()
        assert captured.get("missing_volumes") == [3.0], captured
        assert not _events(ctx, "IN_FLIGHT_DEDUPE"), "manual override must not be filtered"
        print("    [PASSED] manual download keeps the full missing set (explicit override).")
    finally:
        ctx.close()


# ===========================================================================
# 7. Torbox poll uses the persisted effective wanted set (not the full missing set)
# ===========================================================================
def test_torbox_poll_uses_persisted_wanted():
    print("--> Testing Torbox poll uses the effective (deduped) wanted set...")
    ctx = Ctx()
    try:
        ctx.db.set_setting("downloads_dir", os.path.join(ctx.tmpdir, "downloads"))
        ctx.db.set_setting("staging_dir", os.path.join(ctx.tmpdir, "staging"))
        ctx.db.set_setting("torbox_enabled", "true")
        ctx.db.set_setting("torbox_api_key", TORBOX_KEY)
        ctx.db.set_setting("torbox_delete_after_download", "false")
        rid = _insert_release(
            ctx.db, "poll-wanted-guid", "Known Series Vol. 03-04", downloader="torbox",
            downloader_id="77", download_status="DOWNLOADING",
            downloader_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            volumes=[3, 4], missing_volumes=[3, 4],
        )
        with ctx.db.get_connection() as conn:
            conn.execute(
                "UPDATE releases SET download_wanted_json = ? WHERE id = ?;",
                (json.dumps([4.0]), rid),
            )
            conn.commit()
        files = [{"id": 3, "name": "Series v03.cbz", "size": 10},
                 {"id": 4, "name": "Series v04.cbz", "size": 10}]
        service = FakeTorboxService(state="ready", files=files)
        with patch("app.scheduler.build_downloader", return_value=service):
            result = ctx.scheduler.poll_torbox_downloads()
        assert result["completed"] == 1, result
        _handle, selection, _dest = service.fetch_calls[0]
        assert [item["id"] for item in selection] == [4], selection
        print("    [PASSED] only the effective wanted volume is fetched even though 3 is missing.")
    finally:
        ctx.close()


# ===========================================================================
# 8. Reservation helper semantics
# ===========================================================================
def test_reservation_helper():
    print("--> Testing the in-flight reservation helper...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_reserved_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        def insert(guid, status, download_status, volumes):
            with db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO releases (guid, title, series_name, status, download_status, volumes_json) "
                    "VALUES (?, ?, 'Known Series', ?, ?, ?);",
                    (guid, f"Known Series {guid}", status, download_status, json.dumps(volumes)),
                )
                conn.commit()
        insert("downloading", "REQUESTED", "DOWNLOADING", [3.0])
        insert("completed", "REQUESTED", "COMPLETED", [4.0])
        insert("failed", "REQUESTED", "FAILED", [5.0])
        insert("idle", "REQUESTED", "IDLE", [6.0])
        insert("in-calibre", "IN_CALIBRE", "COMPLETED", [7.0])
        insert("ignored", "IGNORED", "COMPLETED", [8.0])
        reserved = db.get_inflight_reserved_volumes(["Known Series"])
        assert reserved == {3.0, 4.0}, reserved
        assert db.get_inflight_reserved_volumes([]) == set()
        assert db.get_inflight_reserved_volumes(["Known Series"], exclude_guid="downloading") == {4.0}
        print("    [PASSED] DOWNLOADING/COMPLETED reserve; FAILED/IDLE/IN_CALIBRE/IGNORED do not.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 78)
    print("Running in-flight dedupe + JPEG XL suite (task-26)")
    print("=" * 78)
    tests = [
        test_jpegxl_detection_and_feed_discard,
        test_jpegxl_media_typing_and_ui,
        test_inflight_suppresses_auto_download,
        test_completed_reserves_failed_does_not,
        test_partial_bundle_filters_wanted,
        test_manual_download_bypasses_dedupe,
        test_torbox_poll_uses_persisted_wanted,
        test_reservation_helper,
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
        print(f"IN-FLIGHT/JPEGXL SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL IN-FLIGHT DEDUPE + JPEG XL TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
