#!/usr/bin/env python3
"""task-39 suite: flat staging routing, collision policy, flatten + manual action.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import shutil
import sys
import tempfile
import traceback
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app.tagger import pipeline as downloads_pipeline  # noqa: E402
from test_downloads_routing import KNOWN_TEMPLATE, Ctx, _make_cbz  # noqa: E402


def _all_staging_files(staging_dir):
    found = []
    for root, _dirs, files in os.walk(staging_dir):
        for name in files:
            found.append(os.path.relpath(os.path.join(root, name), staging_dir))
    return sorted(found)


def _subdirs(staging_dir):
    return sorted(
        name for name in os.listdir(staging_dir)
        if os.path.isdir(os.path.join(staging_dir, name))
    )


# ===========================================================================
# 1. Routing writes directly to the staging root (no subfolders)
# ===========================================================================
def test_routing_is_flat():
    print("--> Testing routing lands directly in the staging root...")
    ctx = Ctx()
    try:
        source = os.path.join(ctx.downloads, "Some Torrent Dir", "Known Series v01.cbz")
        _make_cbz(source)
        result = ctx.run(templates=KNOWN_TEMPLATE)
        assert result["tagged_and_moved"] == 1, result
        assert os.path.isfile(os.path.join(ctx.staging, "Known Series v01.cbz")), _all_staging_files(ctx.staging)
        assert _subdirs(ctx.staging) == [], _subdirs(ctx.staging)
        assert _all_staging_files(ctx.staging) == ["Known Series v01.cbz"], _all_staging_files(ctx.staging)
        print("    [PASSED] staging/<file>.cbz only; no torrent subfolder created.")
    finally:
        ctx.close()


# ===========================================================================
# 2. Collision policy: different size -> (2); same size -> skipped
# ===========================================================================
def test_collision_policy():
    print("--> Testing flat-staging collision policy...")
    ctx = Ctx()
    try:
        first = os.path.join(ctx.downloads, "torrent-a", "Known Series v01.cbz")
        _make_cbz(first)
        ctx.run(templates=KNOWN_TEMPLATE)
        root_file = os.path.join(ctx.staging, "Known Series v01.cbz")
        assert os.path.isfile(root_file)
        first_size = os.path.getsize(root_file)

        second = os.path.join(ctx.downloads, "torrent-b", "Known Series v01.cbz")
        _make_cbz(second, entries={"001.jpg": b"different-size-bytes", "002.jpg": b"more"})
        result = ctx.run(templates=KNOWN_TEMPLATE)
        renamed = os.path.join(ctx.staging, "Known Series v01 (2).cbz")
        assert result["tagged_and_moved"] == 1, result
        assert os.path.isfile(renamed), _all_staging_files(ctx.staging)
        assert os.path.getsize(root_file) == first_size, "original must not be overwritten"
        assert os.path.getsize(renamed) != first_size, _all_staging_files(ctx.staging)

        same = os.path.join(ctx.downloads, "torrent-c", "Known Series v01.cbz")
        _make_cbz(same)
        result = ctx.run(templates=KNOWN_TEMPLATE)
        assert result["skipped_existing"] == 1, result
        assert os.path.getsize(root_file) == first_size
        assert "Known Series v01 (3).cbz" not in _all_staging_files(ctx.staging), _all_staging_files(ctx.staging)
        print("    [PASSED] different-size collision -> (2); same-size duplicate skipped without overwrite.")
    finally:
        ctx.close()


# ===========================================================================
# 3. flatten_staging_dir: moves/skips/renames, temps untouched, dirs pruned
# ===========================================================================
def test_flatten_existing_staging():
    print("--> Testing flatten_staging_dir...")
    ctx = Ctx()
    try:
        os.makedirs(ctx.staging, exist_ok=True)
        _make_cbz(os.path.join(ctx.staging, "Old Nested", "Series v01.cbz"))
        _make_cbz(os.path.join(ctx.staging, "Old Nested", "Deep", "Series v02.cbz"))
        with open(os.path.join(ctx.staging, "Old Nested", "inprogress.cbz.part"), "wb") as handle:
            handle.write(b"partial")
        with open(os.path.join(ctx.staging, "Old Nested", "x.mangatrax-part"), "wb") as handle:
            handle.write(b"partial2")
        os.makedirs(os.path.join(ctx.staging, "Empty Only"))
        stats = downloads_pipeline.flatten_staging_dir(ctx.staging, db=ctx.db)
        assert stats["flattened"] == 2, stats
        assert os.path.isfile(os.path.join(ctx.staging, "Series v01.cbz")), _all_staging_files(ctx.staging)
        assert os.path.isfile(os.path.join(ctx.staging, "Series v02.cbz")), _all_staging_files(ctx.staging)
        assert os.path.isfile(os.path.join(ctx.staging, "Old Nested", "inprogress.cbz.part")), "temp .part must stay"
        assert os.path.isfile(os.path.join(ctx.staging, "Old Nested", "x.mangatrax-part")), "temp mangatrax-part must stay"
        assert not os.path.isdir(os.path.join(ctx.staging, "Empty Only")), "empty dir should be pruned"
        assert stats["dirs_removed"] >= 1, stats
        # Collisions: same size skipped; different size renamed.
        nested = os.path.join(ctx.staging, "Another Nested")
        os.makedirs(nested)
        shutil.copyfile(
            os.path.join(ctx.staging, "Series v01.cbz"),
            os.path.join(nested, "Series v01.cbz"),
        )
        _make_cbz(os.path.join(nested, "Series v02.cbz"), entries={"001.jpg": b"different"})
        stats2 = downloads_pipeline.flatten_staging_dir(ctx.staging, db=ctx.db)
        assert stats2["skipped_existing"] == 1, stats2
        assert stats2["conflicts_renamed"] == 1, stats2
        assert os.path.isfile(os.path.join(ctx.staging, "Series v02 (2).cbz")), _all_staging_files(ctx.staging)
        assert not os.path.isdir(nested), "nested dir removed after moving"
        print("    [PASSED] nested files moved, temps untouched, collisions handled, dirs pruned.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Routing auto-flattens legacy staging, is idempotent
# ===========================================================================
def test_auto_flatten_idempotent():
    print("--> Testing automatic flatten in routing (idempotent)...")
    ctx = Ctx()
    try:
        legacy = os.path.join(ctx.staging, "Legacy Torrent")
        os.makedirs(legacy, exist_ok=True)
        _make_cbz(os.path.join(legacy, "Known Series v05.cbz"))
        result = ctx.run(templates=KNOWN_TEMPLATE)
        assert result["flattened"] >= 1, result
        assert os.path.isfile(os.path.join(ctx.staging, "Known Series v05.cbz")), _all_staging_files(ctx.staging)
        assert not os.path.isdir(legacy), "legacy folder should be pruned"
        second = ctx.run(templates=KNOWN_TEMPLATE)
        assert second["flattened"] == 0, second
        assert _subdirs(ctx.staging) == [], _subdirs(ctx.staging)
        print("    [PASSED] routing flattens legacy staging and is idempotent (second pass does nothing).")
    finally:
        ctx.close()


# ===========================================================================
# 5. Manual endpoint + button wiring
# ===========================================================================
def test_endpoint_and_button():
    print("--> Testing POST /api/downloads/flatten-staging + button wiring...")
    ctx = E2ETestContext(prefix="mangatrax_flatten_api_")
    try:
        staging = os.path.join(ctx.temp_dir, "staging_flat")
        os.makedirs(os.path.join(staging, "Nested Torrent"))
        _make_cbz(os.path.join(staging, "Nested Torrent", "Series v01.cbz"))
        _make_cbz(os.path.join(staging, "Nested Torrent", "Series v02.cbz"))
        ctx.db.set_setting("staging_dir", staging)
        response = ctx.client.post("/api/downloads/flatten-staging")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success" and body["flattened"] == 2, body
        assert os.path.isfile(os.path.join(staging, "Series v01.cbz"))
        assert not os.path.isdir(os.path.join(staging, "Nested Torrent"))
        template = open(
            os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8",
        ).read()
        for needle in ("Flatten staging", "flattenStaging()", "/api/downloads/flatten-staging",
                       "btn-flatten-staging"):
            assert needle in template, f"missing flatten wiring: {needle}"
        print("    [PASSED] endpoint flattens staging and the Downloads button is wired.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Containment/symlink safety + docs
# ===========================================================================
def test_containment_and_docs():
    print("--> Testing flatten containment + README flat-staging docs...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_flatten_safe_")
    try:
        staging = os.path.join(tmpdir, "staging")
        outside = os.path.join(tmpdir, "outside")
        os.makedirs(staging)
        os.makedirs(outside)
        _make_cbz(os.path.join(outside, "outside.cbz"))
        link = os.path.join(staging, "linked")
        try:
            os.symlink(outside, link, target_is_directory=True)
            real_link = True
        except (OSError, NotImplementedError):
            real_link = False
        stats = downloads_pipeline.flatten_staging_dir(staging)
        assert os.path.isfile(os.path.join(outside, "outside.cbz")), "symlinked-outside file must survive"
        if real_link:
            assert os.path.islink(link), "symlink dir must not be removed"
        assert stats["flattened"] == 0, stats
        readme = open(os.path.join(BASE_DIR, "README.md"), encoding="utf-8").read()
        for needle in ("Staging layout (flat)", "/staging/<file>.cbz", "Flatten staging",
                       "same name and same size", "(2).cbz"):
            assert needle in readme, f"missing README flat-staging text: {needle}"
        print("    [PASSED] flatten never traverses symlinks out of staging; README documents flat staging.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 78)
    print("Running flat staging suite (task-39)")
    print("=" * 78)
    tests = [
        test_routing_is_flat,
        test_collision_policy,
        test_flatten_existing_staging,
        test_auto_flatten_idempotent,
        test_endpoint_and_button,
        test_containment_and_docs,
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
        print(f"FLAT STAGING SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL FLAT STAGING TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
