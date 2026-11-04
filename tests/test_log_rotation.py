#!/usr/bin/env python3
"""task-35 suite: Docker log caps, rotating app log, activity retention + WAL checkpoint.

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
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # installs FastAPI/Jinja shims in Calibre
import app.main as main_module  # noqa: E402
from app import schemas  # noqa: E402
from app.database import MangaDatabase  # noqa: E402
from test_torbox_downloader import Ctx  # noqa: E402


def _insert_log(db, message, created_at):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO activity_logs (event_type, message, details, actor, created_at) "
            "VALUES ('TEST_EVENT', ?, '', 'system', ?);",
            (message, created_at),
        )
        conn.commit()


def _count_logs(db):
    with db.get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM activity_logs;").fetchone()[0])


# ===========================================================================
# 1. Retention math: old deleted, recent kept, 0 disables, max rows trims
# ===========================================================================
def test_retention_math():
    print("--> Testing activity retention math...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_retention_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        old = (datetime.utcnow() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
        recent = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        _insert_log(db, "old-one", old)
        _insert_log(db, "old-two", old)
        _insert_log(db, "recent", recent)
        result = db.prune_activity_logs(retention_days=90, max_rows=0, checkpoint=True)
        assert result["deleted_old"] == 2, result
        assert result["deleted_excess"] == 0, result
        assert _count_logs(db) == 1, _count_logs(db)
        assert result["checkpointed"] is True, result

        # retention_days=0 disables age deletion.
        _insert_log(db, "old-three", old)
        result = db.prune_activity_logs(retention_days=0, max_rows=0, checkpoint=False)
        assert result["deleted_old"] == 0, result
        assert _count_logs(db) == 2, _count_logs(db)
        assert result["checkpointed"] is False, result

        # Max-row cap trims the oldest excess.
        for index in range(5):
            _insert_log(db, f"row-{index}", recent)
        result = db.prune_activity_logs(retention_days=0, max_rows=3, checkpoint=False)
        assert result["deleted_excess"] == 4, result
        assert _count_logs(db) == 3, result
        print("    [PASSED] old rows pruned, recent kept, 0 disables, max-rows trims oldest excess.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 2. Index migration + scheduler single log/audit line
# ===========================================================================
def test_index_and_single_audit_line():
    print("--> Testing activity index + single ACTIVITY_PRUNED line...")
    ctx = Ctx()
    try:
        with ctx.db.get_connection() as conn:
            indexes = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'activity_logs';"
            ).fetchall()}
        assert "idx_activity_logs_created_at" in indexes, indexes
        old = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        _insert_log(ctx.db, "ancient", old)
        records = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Handler(logging.INFO)
        logger = logging.getLogger("mangatrax.scheduler")
        previous = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            result = ctx.scheduler.prune_activity_logs(actor="tester")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        assert result["deleted_old"] >= 1, result
        messages = [record.getMessage() for record in records if "Activity log pruned" in record.getMessage()]
        assert len(messages) == 1, messages
        events = [row for row in ctx.db.get_logs(limit=50) if row["event_type"] == "ACTIVITY_PRUNED"]
        assert len(events) == 1, events
        assert "deleted_old" in events[0]["message"] and events[0]["actor"] == "tester", events[0]
        again = ctx.scheduler.prune_activity_logs(actor="tester")
        assert again["deleted_old"] == 0, again
        assert len([row for row in ctx.db.get_logs(limit=50) if row["event_type"] == "ACTIVITY_PRUNED"]) == 1
        print("    [PASSED] index exists; pruner logs/audits exactly once and is quiet when nothing is old.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Rotating file handler: single attach, config, write, disable, dir failure
# ===========================================================================
def test_rotating_file_handler():
    print("--> Testing optional rotating file handler...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_filelog_")
    ctx = Ctx()
    handler = None
    try:
        ctx.db.set_setting("log_to_file", "true")
        ctx.db.set_setting("log_file_max_mb", "2")
        ctx.db.set_setting("log_file_backups", "3")
        old_data_dir = main_module.DATA_DIR
        main_module.DATA_DIR = tmpdir
        try:
            info = main_module.configure_file_logging(ctx.db)
            handlers = [h for h in logging.getLogger().handlers if getattr(h, "name", "") == "mangatrax-file"]
            assert len(handlers) == 1, handlers
            handler = handlers[0]
            assert isinstance(handler, logging.handlers.RotatingFileHandler), handler
            assert handler.maxBytes == 2 * 1024 * 1024, handler.maxBytes
            assert handler.backupCount == 3, handler.backupCount
            assert info["enabled"] is True and info["path"].endswith("mangatrax.log"), info
            logging.getLogger("mangatrax").info("rotation-test-line")
            handler.flush()
            with open(info["path"], "r", encoding="utf-8") as handle:
                assert "rotation-test-line" in handle.read()

            again = main_module.configure_file_logging(ctx.db)
            handlers = [h for h in logging.getLogger().handlers if getattr(h, "name", "") == "mangatrax-file"]
            assert len(handlers) == 1, "handler must attach exactly once"
            assert again["enabled"] is True

            ctx.db.set_setting("log_to_file", "false")
            disabled = main_module.configure_file_logging(ctx.db)
            handlers = [h for h in logging.getLogger().handlers if getattr(h, "name", "") == "mangatrax-file"]
            assert disabled["enabled"] is False and handlers == [], disabled

            # Directory creation failure is handled (no raise, no handler attached).
            ctx.db.set_setting("log_to_file", "true")
            with patch("app.main.os.makedirs", side_effect=OSError(13, "Permission denied")):
                failed = main_module.configure_file_logging(ctx.db)
            handlers = [h for h in logging.getLogger().handlers if getattr(h, "name", "") == "mangatrax-file"]
            assert failed["enabled"] is False and handlers == [], (failed, handlers)
        finally:
            main_module.DATA_DIR = old_data_dir
            for existing in [h for h in logging.getLogger().handlers if getattr(h, "name", "") == "mangatrax-file"]:
                logging.getLogger().removeHandler(existing)
                existing.close()
        print("    [PASSED] one rotating handler with configured limits; idempotent; disable/removal and dir failure handled.")
    finally:
        ctx.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 4. Settings bounds + compose/README wiring
# ===========================================================================
def test_settings_and_docs():
    print("--> Testing logging settings bounds + compose/README wiring...")
    for key, bad in (
        ("log_file_max_mb", "0"), ("log_file_max_mb", "101"),
        ("log_file_backups", "0"), ("log_file_backups", "21"),
        ("activity_retention_days", "-1"), ("activity_retention_days", "3651"),
        ("activity_max_rows", "-1"), ("activity_max_rows", "1000001"),
    ):
        try:
            schemas.validate(schemas.SettingsUpdateRequest, {key: bad})
        except schemas.ValidationError:
            continue
        assert False, f"{key} must reject {bad!r}"
    model = schemas.validate(schemas.SettingsUpdateRequest, {
        "log_to_file": "on", "log_file_max_mb": "20", "log_file_backups": "4",
        "activity_retention_days": "0", "activity_max_rows": "1000",
    })
    assert model.log_to_file == "true" and model.log_file_max_mb == "20", model
    assert model.log_file_backups == "4" and model.activity_retention_days == "0", model
    for name in ("docker-compose.yml", "docker-compose.truenas.yml"):
        text = open(os.path.join(BASE_DIR, name), encoding="utf-8").read()
        assert "logging:" in text and 'driver: json-file' in text, name
        assert 'max-size: "10m"' in text and 'max-file: "5"' in text, name
    readme = open(os.path.join(BASE_DIR, "README.md"), encoding="utf-8").read()
    for needle in ("Log rotation & activity retention", "docker inspect -f", "daemon.json",
                   "activity_retention_days", "RotatingFileHandler", "wal_checkpoint"):
        assert needle in readme, f"missing README: {needle}"
    settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    for needle in ("setting-log-to-file", "setting-log-max-mb", "setting-log-backups",
                   "setting-activity-retention", "setting-activity-max-rows", "pruneActivityNow"):
        assert needle in settings, f"missing settings UI: {needle}"
    logs = open(os.path.join(BACKEND_DIR, "app", "templates", "logs.html"), encoding="utf-8").read()
    assert "pruneActivityLogs" in logs and "/api/logs/prune" in logs
    print("    [PASSED] schema bounds, compose json-file caps, README and both UI controls wired.")


# ===========================================================================
# 5. /health logs block + POST /api/logs/prune endpoint
# ===========================================================================
def test_health_and_prune_endpoint():
    print("--> Testing /health logs block + POST /api/logs/prune...")
    ctx = E2ETestContext(prefix="mangatrax_logs_api_")
    try:
        old = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        with ctx.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO activity_logs (event_type, message, details, actor, created_at) "
                "VALUES ('OLD_EVENT', 'ancient row', '', 'system', ?);",
                (old,),
            )
            conn.commit()
        health = ctx.client.get("/health").json()
        logs = health["logs"]
        for key in ("activity_rows", "oldest_created_at", "oldest_age_days", "db_size_bytes",
                    "wal_size_bytes", "retention_days", "max_rows", "file_log"):
            assert key in logs, (key, logs)
        assert logs["activity_rows"] >= 1 and logs["oldest_age_days"] >= 300, logs
        response = ctx.client.post("/api/logs/prune")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success" and body["deleted_old"] >= 1, body
        assert "remaining" in body, body
        print("    [PASSED] /health exposes logs block; prune-now endpoint deletes with counts.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running log rotation/retention suite (task-35)")
    print("=" * 78)
    tests = [
        test_retention_math,
        test_index_and_single_audit_line,
        test_rotating_file_handler,
        test_settings_and_docs,
        test_health_and_prune_endpoint,
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
        print(f"LOG ROTATION SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL LOG ROTATION/RETENTION TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
