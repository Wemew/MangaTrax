#!/usr/bin/env python3
"""task-14 suite: startup availability + cheap Calibre reconcile.

Three regressions are guarded here:

1. **Serve immediately.**  The FastAPI ``lifespan`` used to run the whole Calibre
   sync (and the legacy key repair) BEFORE ``yield``, so the web UI was unavailable
   for the duration (~2 min on a real library).  ``lifespan`` must now only start
   the worker thread; the worker performs the initial repair + sync as its FIRST
   action, exception-shielded.
2. **Cheap reconcile.**  ``match_manga_series_in_calibre()`` re-normalised the whole
   Calibre library for every release (~40-120 ms/release against 3000 series).
   ``CalibreSeriesIndex`` is built once per matcher: exact dict fast path, bounded
   substring pass and fuzzy scoring only for plausible candidates.
3. **Ownership guard.**  ``entrypoint.sh`` must only run the recursive ``chown``
   when something is actually mis-owned.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import asyncio
import os
import sys
import tempfile
import shutil
import time
import traceback
import random
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.calibre.matcher import MangaCalibreMatcher
from app.covers import CoverCache
from app.database import MangaDatabase
from app.parser import CalibreSeriesIndex, match_manga_series_in_calibre
from app.scheduler import MangaScheduler

MAIN_SOURCE = os.path.join(BACKEND_DIR, "app", "main.py")
SCHEDULER_SOURCE = os.path.join(BACKEND_DIR, "app", "scheduler.py")
ENTRYPOINT = os.path.join(BASE_DIR, "entrypoint.sh")


def _source(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class _FakeMatcher:
    """Minimal matcher double for the incremental-reconcile test."""

    def __init__(self):
        self.checked = []

    def check_release(self, release, auto_request_monitored=True):
        self.checked.append(release["id"])
        return {
            "status": release.get("status"),
            "missing_volumes": release.get("missing_volumes", []),
            "matched_series": None,
        }


# ===========================================================================
# 1. Lifespan yields before any heavy work (source-level guard)
# ===========================================================================
def test_lifespan_yields_before_heavy_work():
    print("--> Testing lifespan availability-first contract (source guard)...")
    source = _source(MAIN_SOURCE)
    start = source.index("async def lifespan(app: FastAPI):")
    body = source[start:]
    # Analyze CODE only (the explanatory comment names the background method and
    # even the word "yield", so find the real top-level yield statement).
    code_lines = []
    yield_index = None
    offset = 0
    for line in body.splitlines(keepends=True):
        if line.strip() == "yield" and line.startswith("    ") and yield_index is None:
            yield_index = offset
        if not line.lstrip().startswith("#"):
            code_lines.append(line)
        offset += len(line)
    assert yield_index is not None, "lifespan must contain a top-level `yield`"
    code_before_yield = "".join(code_lines)
    code_before_yield = code_before_yield[:code_before_yield.index("\n    yield")]
    after_yield = body[yield_index:]

    # No Calibre sync / repair / scanner work between `async def lifespan` and `yield`.
    for heavy in (
        "sync_calibre_library(",
        "repair_series_matching_keys(",
        "perform_initial_sync(",
        "CalibreMangaScanner(",
    ):
        assert heavy not in code_before_yield, (heavy, code_before_yield)
    assert "scheduler.start()" in code_before_yield, code_before_yield
    # Shutdown still stops the worker after the yield.
    assert "scheduler.stop()" in after_yield, after_yield

    # scheduler.start() itself must not run the repair synchronously.
    scheduler_source = _source(SCHEDULER_SOURCE)
    start_method = scheduler_source[scheduler_source.index("    def start(self):"):]
    start_method = start_method[:start_method.index("    def stop(self):")]
    assert "repair_series_matching_keys" not in start_method, start_method
    assert "sync_calibre_library" not in start_method, start_method
    assert "perform_initial_sync" in scheduler_source
    print("    [PASSED] lifespan only starts the worker; no blocking sync before yield.")


# ===========================================================================
# 2. Entering the app lifespan is immediate and never calls sync inline
# ===========================================================================
def test_lifespan_entry_is_fast():
    print("--> Testing lifespan entry does not wait on Calibre sync...")
    # e2e.harness installs the FastAPI shim used by the embedded interpreter.
    from e2e.harness import main_app

    class _FakeScheduler:
        def __init__(self):
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    fake = _FakeScheduler()
    original = main_app.scheduler
    main_app.scheduler = fake
    try:
        async def _run():
            began = time.perf_counter()
            async with main_app.lifespan(main_app.app):
                return time.perf_counter() - began

        # Use a deliberately slow failure mode: if lifespan tried to call a sync method,
        # the fake would raise AttributeError and the test would fail.
        elapsed = asyncio.run(_run())
        assert fake.started is True, "lifespan must start the scheduler"
        assert fake.stopped is True, "lifespan must stop the scheduler on shutdown"
        assert elapsed < 1.0, f"lifespan entry must be immediate, took {elapsed:.2f}s"
    finally:
        main_app.scheduler = original
    print("    [PASSED] entering the app lifespan returns immediately.")


# ===========================================================================
# 3. The worker performs exactly one initial sync and start() does not block
# ===========================================================================
def test_worker_initial_sync_once():
    print("--> Testing worker initial sync on start...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_perf_start_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        db.set_setting("calibre_path", os.path.join(tmpdir, "calibre"))
        scheduler = MangaScheduler(db, CoverCache(tmpdir))
        calls = {"repair": 0, "sync": 0}

        scheduler.repair_series_matching_keys = lambda actor="scheduler": calls.__setitem__("repair", calls["repair"] + 1) or 0

        def _stub_sync():
            calls["sync"] += 1
            scheduler.last_calibre_sync = time.time()  # real sync sets this; prevents a 2nd immediate sync
            return {"success": True, "message": "stub", "synced_series": 0, "resolved_requests": 0}

        scheduler.sync_calibre_library = _stub_sync
        # Keep the rest of the worker loop inert so this test only observes the initial sync.
        scheduler.check_all_feeds = lambda: {}
        scheduler.tag_staging_files = lambda: 0
        scheduler.check_and_filter_active_downloads = lambda: 0
        scheduler.expand_unresolved_complete_releases = lambda *args, **kwargs: 0
        scheduler.db.reconcile_release_volume_state = lambda: 0

        class _AvailableScanner:
            db_path = os.path.join(tmpdir, "calibre", "metadata.db")

            def __init__(self, *_args, **_kwargs):
                pass

            def is_available(self):
                return True

            def get_all_series(self):
                return []

            def get_series_volumes_map(self):
                return {}

        with patch("app.scheduler.CalibreMangaScanner", _AvailableScanner):
            began = time.perf_counter()
            scheduler.start()
            start_elapsed = time.perf_counter() - began
            assert start_elapsed < 0.5, f"start() must not block on the sync (took {start_elapsed:.2f}s)"
            assert calls["sync"] == 0, "start() must not sync synchronously"

            deadline = time.time() + 3.0
            while time.time() < deadline and calls["sync"] == 0:
                time.sleep(0.02)
            time.sleep(0.2)
        scheduler.stop()
        assert calls["sync"] == 1, calls
        assert calls["repair"] == 1, calls
        status = scheduler.get_calibre_sync_status()
        assert status["initial_sync_done"] is True, status
        assert status["in_progress"] is False, status
        print("    [PASSED] worker runs the initial repair+sync once; start() is non-blocking.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 4. A sync exception can never kill the worker
# ===========================================================================
def test_worker_survives_initial_sync_exception():
    print("--> Testing worker survives initial-sync exceptions...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_perf_survive_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        scheduler = MangaScheduler(db, CoverCache(tmpdir))

        # (a) The shielded wrapper swallows sub-failures of repair and sync.
        scheduler.repair_series_matching_keys = lambda actor="scheduler": (_ for _ in ()).throw(RuntimeError("repair boom"))
        scheduler.sync_calibre_library = lambda: (_ for _ in ()).throw(RuntimeError("sync boom"))
        results = scheduler.perform_initial_sync()
        assert results["repair"] == 0, results
        assert scheduler._initial_sync_done is True, results

        # (b) Even if perform_initial_sync itself raises, the thread keeps running.
        scheduler2 = MangaScheduler(db, CoverCache(tmpdir))
        scheduler2.perform_initial_sync = lambda: (_ for _ in ()).throw(RuntimeError("total boom"))
        scheduler2.start()
        time.sleep(0.6)
        alive = scheduler2.thread is not None and scheduler2.thread.is_alive()
        running = scheduler2.running
        scheduler2.stop()
        assert alive is True, "worker thread must survive an initial-sync exception"
        assert running is True, "worker must remain marked running"
        print("    [PASSED] initial sync failures are shielded; worker keeps running.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 5. Indexed matching: same semantics for the classic cases
# ===========================================================================
def test_index_match_semantics():
    print("--> Testing CalibreSeriesIndex match semantics...")
    calibre = [
        {"series_name": "Frieren: Beyond Journey's End", "series_id": 1},
        {"series_name": "Hataraku Maou-sama!", "series_id": 2},
        {"series_name": "One Punch Man", "series_id": 3},
        {"series_name": "Attack on Titan", "series_id": 4},
        {"series_name": "Blue Lock", "series_id": 5},
        {"series_name": "Blue Lock - Episode Nagi", "series_id": 6},
    ]
    cases = [
        # exact (case/punctuation insensitive via normalize_text)
        ("frieren beyond journeys end", 1),
        ("Frieren: Beyond Journey's End", 1),
        # dual-title query: the candidate part matches the Calibre series exactly
        ("Hataraku Maou-sama! | The Devil is a Part-Timer!", 2),
        # substring containment (high coverage: len_ratio >= 0.70)
        ("One Punch Man v01", 3),
        # fuzzy typo (> 0.80 similarity)
        ("Attack on Titen", 4),
        # no match
        ("Completely Unrelated Manga", None),
    ]
    index = CalibreSeriesIndex(calibre)
    for query, expected_id in cases:
        match = index.match(query)
        got = match.get("series_id") if match else None
        assert got == expected_id, (query, got, expected_id)
        # The public parser helper (single implementation) must agree.
        public = match_manga_series_in_calibre(query, calibre)
        public_id = public.get("series_id") if public else None
        assert public_id == expected_id, (query, public_id, expected_id)
    print("    [PASSED] exact / dual-title / substring / fuzzy / no-match semantics kept.")


# ===========================================================================
# 6. Reconcile benchmark: index precomputed, exact fast, big win vs naive
# ===========================================================================
def test_reconcile_benchmark():
    print("--> Testing reconcile performance (precomputed index)...")
    series_count = 3000
    release_count = 1000
    random.seed(11)
    series_list = []
    volumes_map = {}
    for index in range(series_count):
        name = f"Test Series Number {index:04d}"
        series_list.append({"series_name": name, "series_id": index})
        volumes_map[name.lower()] = {1.0, 2.0, 3.0}

    releases = []
    for index in range(release_count):
        kind = index % 10
        if kind < 6:
            name = f"Unknown Manga Title {index:04d}"
        elif kind < 9:
            name = f"Test Series Number {random.randrange(series_count):04d}"
        else:
            name = f"Test Serie Number {random.randrange(series_count):04d}"
        releases.append({
            "id": index,
            "series_name": name,
            "volumes": [1.0, 2.0, 3.0],
            "is_bundle": True,
            "status": "REQUESTED",
            "title": name,
        })

    began = time.perf_counter()
    matcher = MangaCalibreMatcher(series_list, volumes_map, {})
    construct_seconds = time.perf_counter() - began
    assert construct_seconds < 3.0, f"index construction too slow: {construct_seconds:.2f}s"
    assert len(matcher._index) == series_count, (len(matcher._index), series_count)
    assert len(matcher._index._by_norm) == series_count, "exact-match dict not precomputed"

    began = time.perf_counter()
    for release in releases:
        matcher.check_release(release)
    reconcile_seconds = time.perf_counter() - began
    per_release_ms = reconcile_seconds / release_count * 1000
    # Coarse, CI-safe bound: the unindexed matcher measured ~42 s here (and ~117 ms
    # per release on the reporter's machine); anything near that must fail loudly.
    assert reconcile_seconds < 10.0, f"{release_count} releases took {reconcile_seconds:.2f}s"
    assert per_release_ms < 20.0, f"{per_release_ms:.1f} ms/release"

    # Exact-match fast path: O(1) dict hit, well under 1 ms even including Python overhead.
    exact_releases = [rel for rel in releases if "Series Number" in rel["series_name"]][:200]
    began = time.perf_counter()
    for release in exact_releases:
        match = matcher.find_series(release["series_name"])
        assert match is not None, release["series_name"]
    index_avg_ms = (time.perf_counter() - began) / len(exact_releases) * 1000
    assert index_avg_ms < 1.0, f"indexed exact match {index_avg_ms:.3f} ms (target < 1 ms)"

    # Relative improvement: the naive helper rebuilds the index per call.
    absent = [rel["series_name"] for rel in releases if rel["series_name"].startswith("Unknown")][:3]
    began = time.perf_counter()
    for name in absent:
        match_manga_series_in_calibre(name, series_list)
    naive_avg_ms = (time.perf_counter() - began) / len(absent) * 1000
    assert naive_avg_ms > index_avg_ms * 3, (naive_avg_ms, index_avg_ms)
    print(
        f"    [PASSED] index built in {construct_seconds*1000:.0f} ms; "
        f"{release_count} releases in {reconcile_seconds*1000:.0f} ms "
        f"({per_release_ms:.2f} ms/release; exact {index_avg_ms:.3f} ms; naive absent {naive_avg_ms:.1f} ms)."
    )


# ===========================================================================
# 7. Reconcile is incremental and bounded
# ===========================================================================
def test_reconcile_incremental_and_bounded():
    print("--> Testing incremental/bounded reconcile...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_perf_reconcile_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        scheduler = MangaScheduler(db, CoverCache(tmpdir))
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json) "
                "VALUES ('g-stable', 'Stable v01-03', 'Stable', 'IN_CALIBRE', '[1.0, 2.0, 3.0]', '[]');"
            )
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json) "
                "VALUES ('g-missing', 'Missing v01-04', 'Missing', 'IN_CALIBRE', '[1.0, 2.0, 3.0, 4.0]', '[4.0]');"
            )
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, volumes_json, missing_vols_json) "
                "VALUES ('g-requested', 'Requested v01', 'Requested', 'REQUESTED', '[1.0]', '[]');"
            )
            conn.commit()

        fake = _FakeMatcher()
        changed = scheduler._reconcile_releases_with_calibre(fake)
        assert changed == 0, changed  # fake keeps everything unchanged
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM releases WHERE guid = 'g-stable';")
            stable_id = cur.fetchone()["id"]
        assert stable_id not in fake.checked, (stable_id, fake.checked)
        assert len(fake.checked) == 2, fake.checked  # missing + requested, not the stable row

        # Bounded passes rotate through the remaining candidates.
        scheduler.reconcile_batch_size = 1
        scheduler._reconcile_cursor = 0
        first = _FakeMatcher()
        scheduler._reconcile_releases_with_calibre(first)
        second = _FakeMatcher()
        scheduler._reconcile_releases_with_calibre(second)
        assert len(first.checked) == 1, first.checked
        assert len(second.checked) == 1, second.checked
        assert first.checked != second.checked, (first.checked, second.checked)
        print("    [PASSED] stable rows skipped; bounded batches rotate to the next tick.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 8. entrypoint.sh ownership guard
# ===========================================================================
def test_entrypoint_ownership_guard():
    print("--> Testing entrypoint chown guard...")
    source = _source(ENTRYPOINT)
    assert source.startswith("#!/bin/sh\n"), "entrypoint must stay POSIX sh"
    assert "[[ " not in source, "entrypoint must not use bash-only [[ ... ]]"
    # The guard covers every bind-mounted runtime directory (data, staging, downloads).
    for guarded in ("/app/data", "/staging", "/downloads"):
        assert guarded in source, source
    assert "find /app/data /staging /downloads ! -user appuser -print -quit" in source, source
    assert "grep -q ." in source, source
    # The recursive chown only runs inside the mis-owned check.
    guard_index = source.index("if find /app/data /staging /downloads ! -user appuser")
    chown_index = source.index("chown -R appuser:appuser")
    assert guard_index < chown_index, source
    assert "\n    fi\n" in source[guard_index:], source[guard_index:]
    # Privilege drop preserved.
    assert "exec gosu appuser \"$@\"" in source, source
    print("    [PASSED] recursive chown guarded; gosu drop preserved.")


def main():
    print("=" * 78)
    print("Running startup-availability / reconcile-performance suite (task-14)")
    print("=" * 78)
    tests = [
        test_lifespan_yields_before_heavy_work,
        test_lifespan_entry_is_fast,
        test_worker_initial_sync_once,
        test_worker_survives_initial_sync_exception,
        test_index_match_semantics,
        test_reconcile_benchmark,
        test_reconcile_incremental_and_bounded,
        test_entrypoint_ownership_guard,
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
        print(f"STARTUP/PERF SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STARTUP/PERF TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
