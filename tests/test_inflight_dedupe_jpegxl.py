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
                 downloader, downloader_id, volumes_json, missing_vols_json,
                 download_wanted_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                guid, title, series, f"https://nyaa.si/view/{guid}",
                f"magnet:?xt=urn:btih:{guid}",
                status, download_status, extra.get("downloader", ""),
                extra.get("downloader_id", ""),
                json.dumps(volumes), json.dumps(volumes),
                json.dumps(extra.get("wanted", [])),
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
    real_world = (
        "Reading Our Way to a Happy New World 001-009 as v01-02 "
        "(Digital-Compilation) (Oak-JXL) [JPEG-XL]"
    )
    assert is_jpegxl_release(real_world), real_world
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
            result = ctx.poll(_release(real_world, "jxl-guid"))
        assert result["releases_found"] == 0, result
        assert ctx.releases() == [], "JPEG XL releases must not be ingested"
        qbit.add_torrent.assert_not_called()
        logs = ctx.db.get_logs(limit=100)
        assert not [row for row in logs if row["event_type"] in ("AUTO_DOWNLOAD", "IN_FLIGHT_DEDUPE")], logs
        discards = _events(ctx, "JPEGXL_DISCARDED")
        assert len(discards) == 1 and "[JPEGXL]" in discards[0]["message"], discards
        # Re-polling the same release must not spam the audit log.
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            ctx.poll(_release(real_world, "jxl-guid"))
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
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT download_status FROM releases WHERE guid = 'new-group-guid';")
            assert cur.fetchone()["download_status"] == "PENDING_DEDUPE", "suppressed duplicate must be marked"
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
        def insert(guid, status, download_status, volumes, wanted=None):
            with db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO releases (guid, title, series_name, status, download_status, "
                    "volumes_json, download_wanted_json) "
                    "VALUES (?, ?, 'Known Series', ?, ?, ?, ?);",
                    (guid, f"Known Series {guid}", status, download_status,
                     json.dumps(volumes), json.dumps(wanted or [])),
                )
                conn.commit()
        # Bug 1: a v01-05 bundle fetching only [3,4] reserves only [3,4].
        insert("bundle", "REQUESTED", "DOWNLOADING", [1.0, 2.0, 3.0, 4.0, 5.0], wanted=[3.0, 4.0])
        # Legacy row without a wanted set falls back to the bundle's volumes.
        insert("legacy", "REQUESTED", "DOWNLOADING", [6.0])
        # COMPLETED reserves only what it actually fetched.
        insert("completed", "REQUESTED", "COMPLETED", [7.0, 8.0, 9.0], wanted=[8.0])
        insert("failed", "REQUESTED", "FAILED", [10.0], wanted=[10.0])
        insert("idle", "REQUESTED", "IDLE", [11.0], wanted=[11.0])
        insert("in-calibre", "IN_CALIBRE", "COMPLETED", [12.0], wanted=[12.0])
        insert("ignored", "IGNORED", "COMPLETED", [13.0], wanted=[13.0])
        insert("blacklisted", "BLACKLISTED", "COMPLETED", [14.0], wanted=[14.0])
        reserved = db.get_inflight_reserved_volumes(["Known Series"])
        assert reserved == {3.0, 4.0, 6.0, 8.0}, reserved
        assert db.get_inflight_reserved_volumes([]) == set()
        assert db.get_inflight_reserved_volumes(["Known Series"], exclude_guid="bundle") == {6.0, 8.0}
        print("    [PASSED] reservations prefer download_wanted_json (legacy falls back to volumes); "
              "IN_CALIBRE/IGNORED/BLACKLISTED never reserve.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 9. Bug 1: partial-bundle reservation must not over-reserve
# ===========================================================================
def test_partial_bundle_reservation_does_not_overreserve():
    print("--> Testing a v01-05 bundle fetching [3,4] does not reserve volumes 1,2,5...")
    ctx = _IngestContext(calibre_series={"Known Series": [1, 2]}, settings={"auto_download": "true"})
    try:
        _enable_feed_auto_download(ctx)
        _seed_release(ctx, "bundle-inflight", "Known Series Vol. 01-05",
                      "Known Series", [1, 2, 3, 4, 5], "DOWNLOADING", wanted=[3, 4])
        assert ctx.db.get_inflight_reserved_volumes(["Known Series"]) == {3.0, 4.0}
        qbit = _fake_qbit()
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            result = ctx.poll(_release("Known Series Vol. 05 (Alt)", "vol5-alt-guid"))
        assert result["sent_to_downloader"] == 1, result
        qbit.add_torrent.assert_called_once()
        print("    [PASSED] a second release for volume 5 is not suppressed by the [3,4] bundle.")
    finally:
        ctx.close()


# ===========================================================================
# 10. Bug 2: suppressed duplicate retried after the in-flight release fails
# ===========================================================================
def test_sweep_retries_after_failure():
    print("--> Testing PENDING_DEDUPE sweep retries after the first release fails...")
    ctx = _IngestContext(calibre_series={"Known Series": [1, 2]}, settings={"auto_download": "true"})
    try:
        _enable_feed_auto_download(ctx)
        _seed_release(ctx, "first-inflight", "Known Series Vol. 03 (Old)",
                      "Known Series", [3], "DOWNLOADING")
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=_fake_qbit()):
            ctx.poll(_release("Known Series Vol. 03 (Alt)", "retry-guid"))
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT download_status FROM releases WHERE guid = 'retry-guid';")
            assert cur.fetchone()["download_status"] == "PENDING_DEDUPE"
            conn.execute(
                "UPDATE releases SET download_status = 'FAILED' WHERE guid = 'first-inflight';"
            )
            conn.commit()
        qbit = _fake_qbit()
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            summary = ctx.scheduler.sweep_pending_dedupe()
        assert summary == {"checked": 1, "submitted": 1, "still_reserved": 0, "cleared": 0}, summary
        qbit.add_torrent.assert_called_once()
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT download_status FROM releases WHERE guid = 'retry-guid';")
            assert cur.fetchone()["download_status"] == "DOWNLOADING", "PENDING_DEDUPE must be cleared"
        retries = [row for row in _events(ctx, "AUTO_DOWNLOAD") if "Dedupe retry submitted" in row["message"]]
        assert retries and "Missing: [3.0]" in retries[-1]["message"], retries
        print("    [PASSED] FAILED first release -> sweep submits the duplicate and clears the state.")
    finally:
        ctx.close()


# ===========================================================================
# 11. Sweep: no-op while reserved, batch cap, stale non-REQUESTED cleared
# ===========================================================================
def test_sweep_noop_while_reserved_and_capped():
    print("--> Testing sweep no-op while reserved, batch cap and stale-state clearing...")
    ctx = _IngestContext(calibre_series={"Known Series": [1, 2]}, settings={"auto_download": "true"})
    try:
        _enable_feed_auto_download(ctx)
        _seed_release(ctx, "reserved-first", "Known Series Vol. 06 (Old)",
                      "Known Series", [6], "DOWNLOADING")
        _seed_release(ctx, "pending-a", "Known Series Vol. 06 (A)", "Known Series",
                      [6], "PENDING_DEDUPE")
        _seed_release(ctx, "pending-b", "Known Series Vol. 06 (B)", "Known Series",
                      [6], "PENDING_DEDUPE")
        _seed_release(ctx, "pending-calibre", "Known Series Vol. 07 (Owned)", "Known Series",
                      [7], "PENDING_DEDUPE", status="IN_CALIBRE")
        qbit = _fake_qbit()
        before_dedupe = len(_events(ctx, "IN_FLIGHT_DEDUPE"))
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            noop = ctx.scheduler.sweep_pending_dedupe(limit=1)
        assert noop == {"checked": 1, "submitted": 0, "still_reserved": 1, "cleared": 0}, noop
        qbit.add_torrent.assert_not_called()
        assert len(_events(ctx, "IN_FLIGHT_DEDUPE")) == before_dedupe, "no event spam while reserved"
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT download_status FROM releases WHERE guid = 'pending-a';")
            assert cur.fetchone()["download_status"] == "PENDING_DEDUPE"

        with ctx.db.get_connection() as conn:
            conn.execute("UPDATE releases SET download_status = 'FAILED' WHERE guid = 'reserved-first';")
            conn.commit()
        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            one = ctx.scheduler.sweep_pending_dedupe(limit=1)
        assert one == {"checked": 1, "submitted": 1, "still_reserved": 0, "cleared": 0}, one
        assert qbit.add_torrent.call_count == 1
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT download_status FROM releases WHERE guid = 'pending-b';")
            assert cur.fetchone()["download_status"] == "PENDING_DEDUPE", "batch cap must leave the rest"

        with patch.object(ctx.scheduler, "get_qbit_client", return_value=qbit):
            rest = ctx.scheduler.sweep_pending_dedupe(limit=10)
        # pending-b is still reserved by the just-submitted pending-a; the IN_CALIBRE
        # PENDING row is cleared and nothing is submitted again.
        assert rest == {"checked": 2, "submitted": 0, "still_reserved": 1, "cleared": 1}, rest
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT download_status FROM releases WHERE guid = 'pending-calibre';")
            assert cur.fetchone()["download_status"] == "IDLE", "IN_CALIBRE PENDING rows are cleared"
            cur.execute("SELECT download_status FROM releases WHERE guid = 'pending-b';")
            assert cur.fetchone()["download_status"] == "PENDING_DEDUPE", "still reserved -> stay pending"
        print("    [PASSED] sweep is silent while reserved, capped per cycle, clears stale states.")
    finally:
        ctx.close()


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
        test_partial_bundle_reservation_does_not_overreserve,
        test_sweep_retries_after_failure,
        test_sweep_noop_while_reserved_and_capped,
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
