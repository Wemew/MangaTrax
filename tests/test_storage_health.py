#!/usr/bin/env python3
"""task-28 suite: storage writability pre-flight (downloads/staging mounts).

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import storage_health  # noqa: E402
from app.tagger import pipeline as downloads_pipeline  # noqa: E402
from test_downloads_routing import KNOWN_TEMPLATE, Ctx, _make_cbz  # noqa: E402


def _capture_logs(logger_name, level=logging.WARNING):
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Handler(level=level)
    target = logging.getLogger(logger_name)
    target.addHandler(handler)
    return records, handler, target


# ===========================================================================
# 1. Probe: writable, missing, non-directory, simulated non-writable
# ===========================================================================
def test_probe_writable_missing_and_unwritable():
    print("--> Testing probe_path writable/missing/non-writable results...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_storage_probe_")
    try:
        writable = os.path.join(tmpdir, "writable")
        os.makedirs(writable)
        probe = storage_health.probe_path(writable)
        assert probe["exists"] is True and probe["is_dir"] is True, probe
        assert probe["writable"] is True, probe
        assert probe["errno"] is None and probe["error"] == "", probe
        assert isinstance(probe["free_bytes"], int) and probe["free_bytes"] > 0, probe
        assert probe["uid"] is not None, probe

        missing = os.path.join(tmpdir, "does-not-exist")
        probe = storage_health.probe_path(missing)
        assert probe["exists"] is False and probe["writable"] is False, probe
        assert "does not exist" in probe["error"], probe

        file_path = os.path.join(tmpdir, "file.txt")
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("x")
        probe = storage_health.probe_path(file_path)
        assert probe["exists"] is True and probe["is_dir"] is False, probe
        assert "not a directory" in probe["error"], probe

        # Raw OSError path: simulate a permission-denied mkstemp (works on every OS).
        with patch(
            "app.storage_health.tempfile.mkstemp",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            probe = storage_health.probe_path(writable)
        assert probe["writable"] is False, probe
        assert probe["errno"] == 13 and "Permission denied" in probe["error"], probe

        # Real chmod 0o555 non-writable dir (POSIX non-root only; skipped on Windows/root).
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            locked = os.path.join(tmpdir, "locked")
            os.makedirs(locked)
            original_mode = stat.S_IMODE(os.stat(locked).st_mode)
            try:
                os.chmod(locked, 0o555)
                probe = storage_health.probe_path(locked)
                assert probe["writable"] is False, probe
                assert probe["errno"] in (13, 1), probe
            finally:
                os.chmod(locked, original_mode)
        else:
            print("    [SKIP] real 0o555 probe (Windows or running as root)")
        print("    [PASSED] writable/missing/non-directory/permission-denied probes are non-throwing.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        try:
            os.chmod(tmpdir, 0o755)
        except OSError:
            pass


# ===========================================================================
# 2. build_storage_health + actionable hint/warning text
# ===========================================================================
def test_health_block_and_hints():
    print("--> Testing storage health block + actionable hint text...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_storage_health_")
    try:
        good = os.path.join(tmpdir, "good")
        os.makedirs(good)
        missing = os.path.join(tmpdir, "missing")
        health = storage_health.build_storage_health({"downloads": good, "staging": missing})
        assert health["ok"] is False, health
        assert health["downloads"]["writable"] is True, health["downloads"]
        assert health["staging"]["exists"] is False, health["staging"]
        assert health["paths"]["staging"]["path"] == missing, health["paths"]
        assert "checked_at" in health, health

        hint = storage_health.format_hint(good, PermissionError(13, "Permission denied"))
        assert "storage path" in hint and good in hint, hint
        assert "uid" in hint and "errno 13" in hint, hint
        assert "PUID/PGID" in hint and "mount ownership/ACL" in hint, hint
        missing_hint = storage_health.format_hint(missing, PermissionError(13, "Permission denied"))
        assert missing in missing_hint and "errno 13" in missing_hint, missing_hint
        assert "PUID/PGID" in missing_hint, missing_hint

        with patch("app.storage_health.tempfile.mkstemp", side_effect=PermissionError(13, "Permission denied")):
            locked_probe = storage_health.probe_path(good)
        warning = storage_health.warning_message("staging", locked_probe)
        assert "NOT writable" in warning and "uid" in warning and "errno 13" in warning, warning
        assert "PUID/PGID" in warning, warning
        assert storage_health.format_hint(good) == "", "writable probe without errno needs no hint"
        print("    [PASSED] health block and uid/errno/PUID hint + startup warning format are actionable.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 3. Startup/settings-save probe persists storage_health_json and warns
# ===========================================================================
def test_startup_probe_persists_and_warns():
    print("--> Testing startup probe persistence + warning...")
    from e2e.harness import E2ETestContext

    import app.main as main_module

    ctx = E2ETestContext(prefix="mangatrax_storage_startup_")
    try:
        missing = os.path.join(ctx.temp_dir, "missing-staging")
        ctx.db.set_setting("staging_dir", missing)
        ctx.db.set_setting("downloads_dir", ctx.staging_dir if os.path.isdir(ctx.staging_dir) else ctx.temp_dir)
        logs, handler, logger = _capture_logs("mangatrax")
        try:
            health = main_module._refresh_storage_health("startup")
        finally:
            logger.removeHandler(handler)
        assert health["ok"] is False, health
        assert health["staging"]["writable"] is False, health["staging"]
        stored = json.loads(ctx.db.get_setting("storage_health_json"))
        assert stored["staging"]["path"] == missing, stored
        assert stored["staging"]["writable"] is False, stored
        warnings = [record.getMessage() for record in logs if "Storage check" in record.getMessage()]
        assert warnings and "NOT writable" in warnings[0] and "PUID/PGID" in warnings[0], warnings
        print("    [PASSED] startup probe persisted both paths and logged the actionable warning.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Health endpoint exposes the storage block (writable + unwritable)
# ===========================================================================
def test_health_endpoint_storage_block():
    print("--> Testing /health storage block...")
    from e2e.harness import E2ETestContext

    import app.main as main_module

    ctx = E2ETestContext(prefix="mangatrax_storage_health_api_")
    try:
        downloads = os.path.join(ctx.temp_dir, "downloads")
        staging = os.path.join(ctx.temp_dir, "staging2")
        os.makedirs(downloads, exist_ok=True)
        os.makedirs(staging, exist_ok=True)
        ctx.db.set_setting("downloads_dir", downloads)
        ctx.db.set_setting("staging_dir", staging)
        main_module._refresh_storage_health("test")
        payload = ctx.client.get("/health").json()
        assert payload["status"] == "ok", payload
        storage = payload["storage"]
        assert storage["ok"] is True, storage
        assert storage["downloads"]["path"] == downloads and storage["downloads"]["writable"] is True, storage
        assert storage["staging"]["path"] == staging and storage["staging"]["writable"] is True, storage
        assert all(k in storage["downloads"] for k in ("path", "writable", "uid", "error", "free_bytes"))

        ctx.db.set_setting("staging_dir", os.path.join(ctx.temp_dir, "gone"))
        main_module._refresh_storage_health("test-missing")
        payload = ctx.client.get("/health").json()
        assert payload["storage"]["ok"] is False, payload["storage"]
        assert payload["storage"]["staging"]["writable"] is False, payload["storage"]["staging"]
        print("    [PASSED] /health includes both paths with writable/uid/error/free_bytes.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Settings save probes the saved paths; UI shows inline/banner warnings
# ===========================================================================
def test_settings_save_probe_and_ui_warnings():
    print("--> Testing settings-save probe + Downloads/Settings UI warnings...")
    from e2e.harness import E2ETestContext

    import app.main as main_module

    ctx = E2ETestContext(prefix="mangatrax_storage_ui_")
    try:
        downloads = os.path.join(ctx.temp_dir, "dl")
        os.makedirs(downloads, exist_ok=True)
        missing = os.path.join(ctx.temp_dir, "missing-staging")
        logs, handler, logger = _capture_logs("mangatrax")
        try:
            response = ctx.client.post(
                "/api/settings",
                json_data={"downloads_dir": downloads, "staging_dir": missing},
            )
        finally:
            logger.removeHandler(handler)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success", body
        assert body["storage"]["staging"]["writable"] is False, body["storage"]
        assert body["storage"]["downloads"]["writable"] is True, body["storage"]
        stored = json.loads(ctx.db.get_setting("storage_health_json"))
        assert stored["staging"]["path"] == missing, stored
        warnings = [record.getMessage() for record in logs if "Storage check" in record.getMessage()]
        assert warnings and "PUID/PGID" in warnings[0], warnings

        # The opaque CALIBRE runner has no Jinja2 (the harness shims templates), so
        # verify the context/data source AND the real template sources/wiring.
        health_payload = main_module._storage_health_payload()
        assert health_payload["staging"]["path"] == missing, health_payload
        assert health_payload["staging"]["writable"] is False, health_payload
        templates_dir = os.path.join(BACKEND_DIR, "app", "templates")
        banner_source = open(
            os.path.join(templates_dir, "partials", "storage_warning.html"), encoding="utf-8"
        ).read()
        for needle in ("Storage permission problem detected", "not writable", "uid",
                       "errno", "PUID/PGID", "storage_health.staging.path"):
            assert needle in banner_source, (needle, banner_source)
        settings_source = open(
            os.path.join(templates_dir, "settings.html"), encoding="utf-8"
        ).read()
        assert settings_source.count('include "partials/storage_path_warning.html"') == 2, "both inputs wired"
        path_warning_source = open(
            os.path.join(templates_dir, "partials", "storage_path_warning.html"), encoding="utf-8"
        ).read()
        for needle in ("Not writable by uid", "errno", "check mount ownership/ACL (PUID/PGID)"):
            assert needle in path_warning_source, (needle, path_warning_source)
        downloads_source = open(
            os.path.join(templates_dir, "downloads.html"), encoding="utf-8"
        ).read()
        assert 'include "partials/storage_warning.html"' in downloads_source, "downloads banner not wired"
        print("    [PASSED] settings save probes/persists/warns; Downloads banner and Settings inline hint are wired.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Routing failure log appends the actionable storage hint
# ===========================================================================
def test_routing_failure_log_contains_hint():
    print("--> Testing routing failure log contains the storage hint...")
    ctx = Ctx()
    try:
        source = os.path.join(ctx.downloads, "Known Series Vol. 02.cbz")
        _make_cbz(source)
        logs, handler, logger = _capture_logs("mangatrax.tagger.pipeline")
        try:
            with patch(
                "app.tagger.pipeline.os.replace",
                side_effect=PermissionError(13, "Permission denied"),
            ):
                result = ctx.run(templates=KNOWN_TEMPLATE)
        finally:
            logger.removeHandler(handler)
        assert result["failed"] == 1 and result["tagged_and_moved"] == 0, result
        assert os.path.isfile(source), "failed route must keep the source file"
        messages = [record.getMessage() for record in logs]
        route_messages = [message for message in messages if "Failed to route" in message]
        assert route_messages, messages
        text = route_messages[-1]
        assert "Permission denied" in text, text
        assert "storage path" in text and "uid" in text, text
        assert "errno 13" in text, text
        assert "PUID/PGID" in text and "mount ownership/ACL" in text, text
        print("    [PASSED] per-file routing failure keeps the try/except and logs the actionable hint.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running storage-health suite (task-28)")
    print("=" * 78)
    tests = [
        test_probe_writable_missing_and_unwritable,
        test_health_block_and_hints,
        test_startup_probe_persists_and_warns,
        test_health_endpoint_storage_block,
        test_settings_save_probe_and_ui_warnings,
        test_routing_failure_log_contains_hint,
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
        print(f"STORAGE HEALTH SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STORAGE HEALTH TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
