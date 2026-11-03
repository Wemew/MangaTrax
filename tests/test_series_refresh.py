#!/usr/bin/env python3
"""task-24 suite: per-series Backfill refresh (provider metadata + Calibre + Nyaa).

The Backfill action now runs three exception-shielded phases in one background job:
  1. force-refreshed provider metadata (display/enrichment only; stable key untouched)
  2. a zero-lock Calibre re-read of that one series
  3. the existing targeted Nyaa crawl
then re-reconciles (and, on a total-volume change, expands) that series' releases only.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import shutil
import sys
import tempfile
import traceback
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.covers import CoverCache  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from app.metadata.anilist import AniListMetadataService  # noqa: E402
from app.scheduler import MangaScheduler  # noqa: E402

SERIES_NAME = "Known Series"


class Ctx:
    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_series_refresh_")
        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.db.set_setting("calibre_path", os.path.join(self.tmpdir, "calibre"))
        self.db.set_setting("downloads_dir", os.path.join(self.tmpdir, "downloads"))
        self.db.set_setting("staging_dir", os.path.join(self.tmpdir, "staging"))
        self.db.set_setting("qbit_enabled", "false")
        self.db.set_setting("auto_download", "false")
        self.scheduler = MangaScheduler(self.db, CoverCache(self.tmpdir))
        self.series_id = self.insert_series()

    def insert_series(self, name=SERIES_NAME, **overrides):
        values = {
            "calibre_series_id": overrides.get("calibre_series_id", 1),
            "name": name,
            "authors": overrides.get("authors", "Old Author"),
            "publisher": overrides.get("publisher", "Old Publisher"),
            "tags": overrides.get("tags", "Old Tag"),
            "description": overrides.get("description", "Old description"),
            "book_count": overrides.get("book_count", 3),
            "latest_index": overrides.get("latest_index", 3.0),
            "website_url": overrides.get("website_url", ""),
            "owned_volumes_json": overrides.get("owned_volumes_json", json.dumps([1.0, 2.0])),
        }
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO series_tracking
                    (calibre_series_id, name, authors, publisher, tags, description,
                     book_count, latest_index, website_url, owned_volumes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    values["calibre_series_id"], values["name"], values["authors"],
                    values["publisher"], values["tags"], values["description"],
                    values["book_count"], values["latest_index"], values["website_url"],
                    values["owned_volumes_json"],
                ),
            )
            conn.commit()
            return cur.lastrowid

    def insert_release(self, guid, title, series_name=SERIES_NAME, **overrides):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO releases
                    (guid, title, series_name, link, download_url, status, download_status,
                     volumes_json, missing_vols_json, is_complete, cover_url)
                VALUES (?, ?, ?, ?, ?, ?, 'IDLE', ?, ?, ?, ?);
                """,
                (
                    guid, title, series_name, overrides.get("link", "https://nyaa.si/view/1"),
                    overrides.get("download_url", "magnet:?xt=urn:btih:" + "0" * 40),
                    overrides.get("status", "REQUESTED"),
                    json.dumps(overrides.get("volumes", [1.0, 2.0, 3.0])),
                    json.dumps(overrides.get("missing_volumes", [3.0])),
                    overrides.get("is_complete", 0),
                    overrides.get("cover_url", ""),
                ),
            )
            conn.commit()
            return cur.lastrowid

    def series(self, name=SERIES_NAME):
        return self.db.get_series_by_name(name)

    def events(self, event_type):
        return [row for row in self.db.get_logs(limit=200) if row["event_type"] == event_type]

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# 1. AniList force_refresh bypass + provider-down keeps cached values
# ===========================================================================
def test_force_refresh_paths():
    print("--> Testing AniList force_refresh (bypass fresh cache, provider-down keeps values)...")
    ctx = Ctx()
    try:
        ctx.db.cache_manga_metadata("known series", {
            "canonical_title": "Known Series",
            "romaji_title": "Known Series Romaji",
            "cover_url": "https://old/cover.jpg",
            "total_volumes": 12,
            "status": "RELEASING",
            "synopsis": "Old synopsis",
            "source": "anilist",
        })
        service = AniListMetadataService(db=ctx.db)
        calls = []

        def fake_fetch(term):
            calls.append(term)
            return {
                "canonical_title": "Known Series",
                "romaji_title": "Known Series Romaji",
                "cover_url": "https://new/cover.jpg",
                "total_volumes": 14,
                "status": "FINISHED",
                "synopsis": "New synopsis",
                "source": "anilist",
            }

        with patch.object(service, "_fetch_from_anilist", side_effect=fake_fetch):
            refreshed = service.resolve_manga_series(SERIES_NAME, force_refresh=True)
            assert refreshed["total_volumes"] == 14, refreshed
            assert refreshed["status"] == "FINISHED", refreshed
            assert calls, "force_refresh must query the provider instead of returning the cache"
            cached = ctx.db.get_cached_manga_metadata("known series")
            assert cached["total_volumes"] == 14, cached

            # Non-forced call now sees the refreshed cache and does not hit the network.
            calls.clear()
            cached_result = service.resolve_manga_series(SERIES_NAME)
            assert cached_result["total_volumes"] == 14, cached_result
            assert calls == [], calls

            # Provider unavailable on a forced refresh: keep the known values.
            with patch.object(service, "_fetch_from_anilist", return_value=None):
                kept = service.resolve_manga_series(SERIES_NAME, force_refresh=True)
            assert kept.get("total_volumes") == 14, kept
            assert kept.get("source") not in ("fallback", "cleaned"), kept
            assert kept.get("canonical_title") == "Known Series", kept
        print("    [PASSED] force path bypasses fresh cache and writes through; outage keeps values.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Provider metadata phase updates enrichment only; stable key untouched
# ===========================================================================
def test_metadata_phase_updates_and_key_unchanged():
    print("--> Testing provider metadata phase (display/enrichment only)...")
    ctx = Ctx()
    try:
        ctx.db.cache_manga_metadata("known series", {
            "canonical_title": "Known Series", "romaji_title": "Known Series Romaji",
            "cover_url": "https://old/cover.jpg", "total_volumes": 12,
            "status": "RELEASING", "synopsis": "Old synopsis", "source": "anilist",
        })
        release_id = ctx.insert_release("refresh-guid", "Known Series Vol. 03", cover_url="https://old/cover.jpg")
        ctx.scheduler.cover_cache = MagicMock()
        provider_meta = {
            "canonical_title": "Known Series (English)",
            "romaji_title": "Known Series Romaji",
            "cover_url": "https://new/cover.jpg",
            "total_volumes": 14,
            "status": "RELEASING",
            "synopsis": "New synopsis",
            "authors": "Provider Author",
            "source": "anilist",
        }
        with patch.object(ctx.scheduler.anilist, "resolve_manga_series", return_value=provider_meta) as resolve:
            summary = ctx.scheduler.refresh_series_provider_metadata(SERIES_NAME, actor="scheduler")
        resolve.assert_called_once()
        assert resolve.call_args.kwargs.get("force_refresh") is True, resolve.call_args
        assert summary["updated"] is True and summary["skipped"] is False, summary
        changes = " | ".join(summary["changes"])
        assert "total volumes 12 -> 14" in changes, changes
        assert "cover updated" in changes, changes
        assert "synopsis updated" in changes, changes
        row = ctx.series()
        assert row["name"] == SERIES_NAME, "provider data must never change the stable series key"
        assert row["description"] == "New synopsis", row["description"]
        assert row["authors"] == "Provider Author", row["authors"]
        release = ctx.db.get_release_by_id(release_id)
        assert release["cover_url"] == "https://new/cover.jpg", release["cover_url"]
        ctx.scheduler.cover_cache.cache_sync.assert_called()
        events = ctx.events("SERIES_METADATA_REFRESHED")
        assert events and events[0]["actor"] == "scheduler", events
        assert "total volumes 12 -> 14" in events[0]["message"], events[0]
        print("    [PASSED] changes audited, description/authors/cover updated, series name untouched.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Provider unavailable: keep existing values, report skip, never blank
# ===========================================================================
def test_metadata_phase_provider_down():
    print("--> Testing provider-down metadata phase keeps values...")
    ctx = Ctx()
    try:
        ctx.db.cache_manga_metadata("known series", {
            "canonical_title": "Known Series", "romaji_title": "Known Series Romaji",
            "cover_url": "https://old/cover.jpg", "total_volumes": 12,
            "status": "RELEASING", "synopsis": "Old synopsis", "source": "anilist",
        })
        before = ctx.series()
        with patch.object(ctx.scheduler.anilist, "resolve_manga_series", side_effect=RuntimeError("provider down")):
            with patch.object(ctx.scheduler.mangaupdates, "get_series_info", return_value={}):
                summary = ctx.scheduler.refresh_series_provider_metadata(SERIES_NAME, actor="scheduler")
        assert summary["updated"] is False and summary["skipped"] is True, summary
        after = ctx.series()
        assert after["description"] == before["description"], after["description"]
        assert after["name"] == SERIES_NAME
        events = ctx.events("SERIES_METADATA_REFRESHED")
        assert events and "kept cached values" in events[0]["message"], events[0]
        assert "provider down" in events[0]["message"], events[0]
        print("    [PASSED] provider outage reported as skip; cached display values retained.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Calibre phase: one-series re-read updates the row; unavailable skips
# ===========================================================================
class _StubScanner:
    available = True
    entry = {
        "series_id": 7,
        "series_name": SERIES_NAME,
        "book_count": 6,
        "latest_index": 6.0,
        "authors": "New Author",
        "publisher": "New Publisher",
        "tags": "Action, Drama",
        "cover_url": "https://calibre/cover.jpg",
    }

    def __init__(self, calibre_path):
        self.calibre_path = calibre_path

    def is_available(self):
        return _StubScanner.available

    def get_all_series(self):
        return [dict(_StubScanner.entry)]

    def get_series_volumes_map(self):
        return {SERIES_NAME.lower(): {1.0, 2.0, 3.0, 4.0, 5.0, 6.0}}


def test_calibre_phase():
    print("--> Testing Calibre refresh phase (stub scanner)...")
    ctx = Ctx()
    try:
        ctx.scheduler.cover_cache = MagicMock()
        _StubScanner.available = True
        with patch("app.scheduler.CalibreMangaScanner", _StubScanner):
            summary = ctx.scheduler.refresh_series_calibre_info(SERIES_NAME, actor="alice")
        assert summary["updated"] is True and summary["skipped"] is False, summary
        assert summary["book_count"] == 6, summary
        row = ctx.series()
        assert row["authors"] == "New Author", row
        assert row["publisher"] == "New Publisher", row
        assert row["tags"] == "Action, Drama", row
        assert int(row["book_count"]) == 6 and float(row["latest_index"]) == 6.0, row
        assert json.loads(row["owned_volumes_json"]) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], row
        assert row["calibre_series_id"] == 7, row
        assert row["last_sync"], row
        events = ctx.events("SERIES_CALIBRE_REFRESHED")
        assert events and events[0]["actor"] == "alice", events
        assert "book count" in events[0]["message"], events[0]

        # Calibre unavailable -> clean skip, row untouched, no exception.
        before = ctx.series()
        _StubScanner.available = False
        with patch("app.scheduler.CalibreMangaScanner", _StubScanner):
            skipped = ctx.scheduler.refresh_series_calibre_info(SERIES_NAME, actor="scheduler")
        assert skipped["updated"] is False and skipped["skipped"] is True, skipped
        assert skipped["reason"] == "Calibre unavailable", skipped
        after = ctx.series()
        assert after["authors"] == before["authors"], after
        print("    [PASSED] stub scanner updates authors/publisher/tags/count/owned volumes; outage skips.")
    finally:
        _StubScanner.available = True
        ctx.close()


# ===========================================================================
# 5. Series reconcile touches only that series' releases
# ===========================================================================
def test_reconcile_only_target_series():
    print("--> Testing per-series release reconcile isolation...")
    ctx = Ctx()
    try:
        target = ctx.insert_release("target-guid", "Known Series Vol. 03", missing_volumes=[3.0])
        other = ctx.insert_release("other-guid", "Other Series Vol. 01", series_name="Other Series", missing_volumes=[1.0])
        calls = []

        class _Matcher:
            def check_release(self, release, auto_request_monitored=True):
                calls.append(release["id"])
                return {"missing_volumes": [], "status": "IN_CALIBRE"}

        changed = ctx.scheduler._reconcile_series_releases(SERIES_NAME, _Matcher())
        assert calls == [target], calls
        assert changed == 1, changed
        assert ctx.db.get_release_by_id(target)["status"] == "IN_CALIBRE"
        other_row = ctx.db.get_release_by_id(other)
        assert other_row["status"] == "REQUESTED", "other series must not be touched"
        print("    [PASSED] only the refreshed series' releases are reconciled.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Total-volume change expands + reconciles; phases report in summary
# ===========================================================================
def test_total_volume_change_expands_and_summary():
    print("--> Testing total-volume change -> reconcile + complete-series expansion...")
    ctx = Ctx()
    try:
        target = ctx.insert_release("expand-target", "Known Series (Complete)", missing_volumes=[])
        expansion_calls = []

        def fake_expand(matcher=None, series_name=None):
            expansion_calls.append(series_name)
            return 1

        class _FakeNyaa:
            def __init__(self, *args, **kwargs):
                pass

            def search_series(self, *args, **kwargs):
                return []

        with patch.object(ctx.scheduler, "refresh_series_provider_metadata", return_value={
            "updated": True, "skipped": False, "changes": ["total volumes 12 -> 14"],
            "total_volumes": 14, "status": "RELEASING",
        }):
            with patch.object(ctx.scheduler, "refresh_series_calibre_info", return_value={
                "updated": False, "skipped": True, "reason": "Calibre unavailable", "changes": [],
            }):
                with patch.object(ctx.scheduler, "_series_refresh_matcher", return_value=MagicMock()):
                    with patch.object(ctx.scheduler, "_reconcile_series_releases", return_value=1) as reconcile:
                        with patch.object(ctx.scheduler, "expand_unresolved_complete_releases", side_effect=fake_expand):
                            with patch("app.scheduler.NyaaMangaProvider", _FakeNyaa):
                                result = ctx.scheduler.backfill_series(SERIES_NAME, max_pages=1)
        assert result["status"] == "success", result
        assert result["summary"]["metadata"]["changes"] == ["total volumes 12 -> 14"], result["summary"]
        assert result["summary"]["calibre"]["skipped"] is True, result["summary"]
        reconcile.assert_called_once()
        assert reconcile.call_args.args[0] == SERIES_NAME, reconcile.call_args
        assert expansion_calls == [SERIES_NAME], expansion_calls
        assert result["expanded"] == 1, result
        assert result["series_name"], result
        assert "Series refresh for" in result["message"], result["message"]
        assert "backfilled 0 releases" in result["message"], result["message"]
        events = {row["event_type"] for row in ctx.db.get_logs(limit=200)}
        assert "SERIES_BACKFILLED" in events, events
        assert target, "seeded release id"
        print("    [PASSED] total-volume change triggers scoped expansion and a structured summary.")
    finally:
        ctx.close()


# ===========================================================================
# 7. Phase isolation: metadata failure still runs Calibre + crawl; crawl failure
#    still leaves metadata/Calibre updates applied.
# ===========================================================================
def test_phase_isolation():
    print("--> Testing phase isolation (metadata fail / crawl fail)...")
    ctx = Ctx()

    class _FakeNyaaEmpty:
        def __init__(self, *args, **kwargs):
            pass

        def search_series(self, *args, **kwargs):
            return []

    class _FakeNyaaBroken:
        def __init__(self, *args, **kwargs):
            pass

        def search_series(self, *args, **kwargs):
            raise RuntimeError("nyaa is down")

    try:
        # Metadata phase fails -> Calibre + crawl still run, summary reports the error.
        with patch.object(ctx.scheduler, "refresh_series_provider_metadata", side_effect=RuntimeError("provider boom")):
            with patch.object(ctx.scheduler, "refresh_series_calibre_info", return_value={
                "updated": True, "skipped": False, "changes": ["authors updated"], "book_count": 4,
            }) as calibre_phase:
                with patch.object(ctx.scheduler, "_series_refresh_matcher", return_value=MagicMock()):
                    with patch.object(ctx.scheduler, "_reconcile_series_releases", return_value=0):
                        with patch("app.scheduler.NyaaMangaProvider", _FakeNyaaEmpty):
                            result = ctx.scheduler.backfill_series(SERIES_NAME, max_pages=1)
        calibre_phase.assert_called_once()
        assert result["summary"]["metadata"]["skipped"] is True, result["summary"]
        assert "provider boom" in json.dumps(result["summary"]["metadata"]), result["summary"]
        assert result["summary"]["calibre"]["updated"] is True, result["summary"]
        assert result["summary"]["backfill"]["status"] == "success", result["summary"]

        # Crawl phase fails -> metadata/Calibre results still reported and applied.
        with patch.object(ctx.scheduler, "refresh_series_provider_metadata", return_value={
            "updated": True, "skipped": False, "changes": ["cover updated"], "total_volumes": 5,
        }):
            with patch.object(ctx.scheduler, "refresh_series_calibre_info", return_value={
                "updated": True, "skipped": False, "changes": ["book count 3 -> 4"], "book_count": 4,
            }):
                with patch.object(ctx.scheduler, "_series_refresh_matcher", return_value=MagicMock()):
                    with patch.object(ctx.scheduler, "_reconcile_series_releases", return_value=0):
                        with patch("app.scheduler.NyaaMangaProvider", _FakeNyaaBroken):
                            result2 = ctx.scheduler.backfill_series(SERIES_NAME, max_pages=1)
        assert result2["status"] == "partial", result2
        assert result2["summary"]["backfill"]["status"] == "error", result2["summary"]
        assert "nyaa is down" in result2["summary"]["backfill"]["error"], result2["summary"]
        assert result2["summary"]["metadata"]["updated"] is True, result2["summary"]
        assert result2["summary"]["calibre"]["updated"] is True, result2["summary"]
        assert "Nyaa crawl failed" in result2["message"], result2["message"]
        print("    [PASSED] each phase failure is isolated and reported in the structured summary.")
    finally:
        ctx.close()


# ===========================================================================
# 8. Both endpoints start the same job and return the structured summary
# ===========================================================================
def test_endpoints_and_ui():
    print("--> Testing both backfill endpoints + tooltip/UI surface...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_series_refresh_api_")
    try:
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO series_tracking (name, authors) VALUES (?, ?);",
                (SERIES_NAME, "Author"),
            )
            conn.commit()
        series_row = ctx.db.get_series_by_name(SERIES_NAME)
        pending_summary = {
            "metadata": {"status": "pending"},
            "calibre": {"status": "pending"},
            "backfill": {"status": "pending"},
        }
        fake_start = MagicMock(return_value={
            "status": "started", "message": "Series refresh started", "summary": pending_summary,
        })
        with patch.object(ctx.scheduler, "run_series_backfill_async", fake_start):
            by_id = ctx.client.post(f"/api/series/{series_row['id']}/backfill", json_data={"max_pages": 2})
            by_name = ctx.client.post("/api/series/backfill", json_data={"series_name": SERIES_NAME, "max_pages": 2})
        for response in (by_id, by_name):
            assert response.status_code == 200, response.json()
            payload = response.json()
            assert payload["status"] == "started", payload
            assert payload["summary"]["metadata"]["status"] == "pending", payload
            assert set(payload["summary"]) == {"metadata", "calibre", "backfill"}, payload
        assert fake_start.call_count == 2, fake_start.call_count
        first_call, second_call = fake_start.call_args_list
        assert first_call.args[0] == SERIES_NAME, first_call
        assert second_call.args[0] == SERIES_NAME, second_call
        # Request actor is passed through for audit attribution.
        assert first_call.kwargs.get("actor"), first_call

        table_html = open(
            os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_table_row_chunk.html"),
            encoding="utf-8",
        ).read()
        card_html = open(
            os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_card_chunk.html"),
            encoding="utf-8",
        ).read()
        for html in (table_html, card_html):
            assert "Refresh metadata + Calibre info" in html, html[:500]
        print("    [PASSED] id/name endpoints start the same job with structured summary; tooltips updated.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running per-series refresh suite (task-24)")
    print("=" * 78)
    tests = [
        test_force_refresh_paths,
        test_metadata_phase_updates_and_key_unchanged,
        test_metadata_phase_provider_down,
        test_calibre_phase,
        test_reconcile_only_target_series,
        test_total_volume_change_expands_and_summary,
        test_phase_isolation,
        test_endpoints_and_ui,
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
        print(f"SERIES REFRESH SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL SERIES REFRESH TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
