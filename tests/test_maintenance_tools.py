#!/usr/bin/env python3
"""task-13 suite: safe maintenance tools exposed by default, destructive ones gated.

MangaTrax already had clear buttons, but the whole card was wrapped in
``{% if debug_actions_enabled %}`` (``MANGATRAX_DEBUG_ACTIONS=1``, default 0), so a
normal deployment showed nothing.  The fix splits SAFE cleanups (re-fetchable caches
/ unreviewed Discovery rows) from DESTRUCTIVE debug actions:

* ``backend/app/maintenance.py`` owns the safe action map + ``perform()``;
* ``POST /api/maintenance/clear`` serves them with a strict Pydantic enum and
  ``extra="forbid"``, behind the normal auth/CSRF middleware but NOT the debug flag;
* ``/api/debug/action`` is unchanged and still 403s for ``purge-non-manga``,
  ``normalize-english-titles`` and ``reset-all-data`` unless the flag is set;
* every safe action writes a ``MAINTENANCE_CLEARED`` activity row with the actor;
* the Settings page renders the Maintenance card unconditionally and keeps the
  Advanced / Dangerous controls inside the debug-gated block.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import sys
import tempfile
import shutil
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import auth
from app import maintenance
from app import schemas
from app.covers import CoverCache
from app.database import MangaDatabase
from e2e.harness import E2ETestContext, main_app

SETTINGS_TEMPLATE = os.path.join(BACKEND_DIR, "app", "templates", "settings.html")
MAIN_SOURCE = os.path.join(BACKEND_DIR, "app", "main.py")


class _Ctx:
    """Isolated database + cover cache for module-level action tests."""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_maintenance_")
        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.cover_cache = CoverCache(self.tmpdir)

    def add_release(self, guid, series, status, description=""):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, status, description) VALUES (?, ?, ?, ?, ?);",
                (guid, f"{series} v01", series, status, description),
            )
            conn.commit()

    def add_tracked_series(self, name, description=""):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO series_tracking (name, is_monitored, description) VALUES (?, 1, ?);",
                (name, description),
            )
            conn.commit()

    def add_blacklist(self, name):
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO series_blacklist (name, reason) VALUES (?, 'test');", (name,))
            conn.commit()

    def release_guids(self):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT guid FROM releases ORDER BY guid;")
            return [row["guid"] for row in cur.fetchall()]

    def tracked_descriptions(self):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT description FROM series_tracking ORDER BY name;")
            return [row["description"] for row in cur.fetchall()]

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# 1. Safe action map + typed unknown-action error
# ===========================================================================
def test_safe_action_map():
    print("--> Testing SAFE_ACTIONS map and typed unknown-action error...")
    assert set(maintenance.SAFE_ACTIONS) == {
        "clear-cover-cache",
        "clear-discovery",
        "clear-descriptions",
    }, sorted(maintenance.SAFE_ACTIONS)
    for action, meta in maintenance.SAFE_ACTIONS.items():
        assert meta.get("title") and meta.get("description") and meta.get("icon"), (action, meta)
        assert maintenance.is_safe_action(action) is True
    # Destructive actions are NOT safe and must never be routed through this module.
    for dangerous in ("purge-non-manga", "normalize-english-titles", "reset-all-data", "clear-thumbnails"):
        assert maintenance.is_safe_action(dangerous) is False, dangerous

    ctx = _Ctx()
    try:
        try:
            maintenance.perform("purge-non-manga", ctx.db, ctx.cover_cache)
        except maintenance.UnknownMaintenanceAction as exc:
            assert "purge-non-manga" in str(exc), str(exc)
        else:
            assert False, "destructive action must not be allowed through maintenance.perform()"
        try:
            maintenance.perform("definitely-not-an-action", ctx.db, ctx.cover_cache)
        except maintenance.UnknownMaintenanceAction as exc:
            assert "definitely-not-an-action" in str(exc), str(exc)
        else:
            assert False, "unknown action must raise UnknownMaintenanceAction"
    finally:
        ctx.close()
    print("    [PASSED] only the three safe actions are exposed; others raise.")


# ===========================================================================
# 2. clear-discovery scoping
# ===========================================================================
def test_clear_discovery_scoping():
    print("--> Testing clear-discovery scoping (never requested/tracked/etc.)...")
    ctx = _Ctx()
    try:
        ctx.add_tracked_series("Tracked Manga")
        ctx.add_blacklist("Blacklisted Manga")
        ctx.add_release("g1", "Untracked Manga", "DISCOVERY")
        ctx.add_release("g2", "Tracked Manga", "DISCOVERY")
        ctx.add_release("g3", "Untracked Manga", "REQUESTED")
        ctx.add_release("g4", "Untracked Manga", "IN_CALIBRE")
        ctx.add_release("g5", "Untracked Manga", "IGNORED")
        ctx.add_release("g6", "Blacklisted Manga", "DISCOVERY")
        ctx.add_release("g7", "Untracked Manga", "BLACKLISTED")

        result = maintenance.perform("clear-discovery", ctx.db, ctx.cover_cache)
        assert result["status"] == "success", result
        assert result["count"] == 1, result
        assert "1" in result["message"], result["message"]
        assert ctx.release_guids() == ["g2", "g3", "g4", "g5", "g6", "g7"], ctx.release_guids()
        # A second run is a harmless no-op.
        again = maintenance.perform("clear-discovery", ctx.db, ctx.cover_cache)
        assert again["count"] == 0, again
        assert "0" in again["message"], again["message"]
        print("    [PASSED] only unreviewed, untracked, unblacklisted DISCOVERY rows removed.")
    finally:
        ctx.close()


# ===========================================================================
# 3. clear-descriptions only blanks text
# ===========================================================================
def test_clear_descriptions_scoping():
    print("--> Testing clear-descriptions only blanks text...")
    ctx = _Ctx()
    try:
        ctx.add_release("g1", "Series One", "REQUESTED", description="A description.")
        ctx.add_release("g2", "Series Two", "DISCOVERY", description="Another description.")
        ctx.add_tracked_series("Series One", description="Tracked synopsis.")
        before = ctx.release_guids()
        assert len(before) == 2, before

        result = maintenance.perform("clear-descriptions", ctx.db, ctx.cover_cache)
        assert result["status"] == "success", result
        # 2 release rows + 1 tracked series blanked.
        assert result["count"] == 3, result
        assert "3" in result["message"], result["message"]

        assert ctx.release_guids() == before, ctx.release_guids()
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT description FROM releases ORDER BY guid;")
            release_descriptions = [row["description"] for row in cur.fetchall()]
        assert release_descriptions == ["", ""], release_descriptions
        assert ctx.tracked_descriptions() == [""], ctx.tracked_descriptions()
        assert maintenance.perform("clear-descriptions", ctx.db, ctx.cover_cache)["count"] == 0
        print("    [PASSED] descriptions blanked; no releases/series deleted.")
    finally:
        ctx.close()


# ===========================================================================
# 4. clear-cover-cache removes files and is idempotent
# ===========================================================================
def test_clear_cover_cache():
    print("--> Testing clear-cover-cache removed files + idempotency...")
    ctx = _Ctx()
    try:
        for index in range(3):
            with open(os.path.join(ctx.cover_cache.covers_dir, f"cover{index}.jpg"), "w", encoding="utf-8") as handle:
                handle.write("image-bytes")
        assert len(os.listdir(ctx.cover_cache.covers_dir)) == 3

        result = maintenance.perform("clear-cover-cache", ctx.db, ctx.cover_cache)
        assert result["status"] == "success", result
        assert result["count"] == 3, result
        assert "3" in result["message"], result["message"]
        assert os.listdir(ctx.cover_cache.covers_dir) == [], os.listdir(ctx.cover_cache.covers_dir)

        again = maintenance.perform("clear-cover-cache", ctx.db, ctx.cover_cache)
        assert again["count"] == 0, again
        assert "0" in again["message"], again["message"]
        assert os.path.isdir(ctx.cover_cache.covers_dir), "covers dir must remain usable"
        print("    [PASSED] cached files purged; second run is a no-op.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Strict Pydantic payload (enum + extra="forbid")
# ===========================================================================
def test_strict_schema():
    print("--> Testing MaintenanceClearRequest strictness...")
    model = schemas.validate(schemas.MaintenanceClearRequest, {"action": "clear-discovery"})
    assert model.action == "clear-discovery", model.action
    for action in ("clear-cover-cache", "clear-discovery", "clear-descriptions"):
        assert schemas.validate(schemas.MaintenanceClearRequest, {"action": action}).action == action

    rejected = [
        {"action": "purge-non-manga"},
        {"action": "reset-all-data"},
        {"action": "normalize-english-titles"},
        {"action": "clear-thumbnails"},
        {"action": "clear-discovery", "unexpected": 1},
        {},
    ]
    for payload in rejected:
        try:
            schemas.validate(schemas.MaintenanceClearRequest, payload)
        except schemas.ValidationError:
            continue
        else:
            assert False, f"payload must be rejected: {payload}"
    # Source-level: the model is strict and the destructive names are absent.
    with open(os.path.join(BACKEND_DIR, "app", "schemas.py"), encoding="utf-8") as handle:
        source = handle.read()
    start = source.index("class MaintenanceClearRequest")
    end = source.index("class ", start + 10)
    model_source = source[start:end]
    # Ignore the explanatory docstring (it names the destructive actions on purpose).
    model_code = model_source[model_source.index("model_config"):]
    assert 'extra="forbid"' in model_code, model_code
    for action in ("clear-cover-cache", "clear-discovery", "clear-descriptions"):
        assert action in model_code, model_code
    for dangerous in ("purge-non-manga", "reset-all-data", "normalize-english-titles"):
        assert dangerous not in model_code, (dangerous, model_code)
    print("    [PASSED] enum closed, unknown keys rejected, destructive names impossible.")


# ===========================================================================
# 6. Route wiring: maintenance is NOT debug-gated; debug still IS
# ===========================================================================
def test_route_wiring_source_guard():
    print("--> Testing route wiring source guards...")
    with open(MAIN_SOURCE, encoding="utf-8") as handle:
        source = handle.read()
    maintenance_start = source.index('@app.post("/api/maintenance/clear")')
    debug_start = source.index('@app.post("/api/debug/action")')
    assert maintenance_start < debug_start, "maintenance route should precede the debug route"

    maintenance_segment = source[maintenance_start:debug_start]
    # No debug-flag gate in the handler: the destructive gate expression must be absent
    # (the explanatory docstring may name the env var, so test the code, not prose).
    assert "AUTH_CONFIG.debug_actions" not in maintenance_segment, maintenance_segment
    assert "if not AUTH_CONFIG.debug_actions" not in maintenance_segment, maintenance_segment
    assert "schemas.MaintenanceClearRequest" in maintenance_segment, maintenance_segment
    assert "maintenance.perform" in maintenance_segment, maintenance_segment

    debug_segment = source[debug_start:source.index('if __name__ == "__main__":')]
    assert "AUTH_CONFIG.debug_actions" in debug_segment, debug_segment
    assert "MANGATRAX_DEBUG_ACTIONS" in debug_segment, debug_segment
    for action in ("purge-non-manga", "normalize-english-titles", "reset-all-data",
                   "clear-discovery", "clear-descriptions", "clear-thumbnails"):
        assert action in debug_segment, (action, debug_segment)
    print("    [PASSED] safe route has no debug flag; /api/debug/action keeps its 403 gate.")


# ===========================================================================
# 7. Route behaviour through the app (safe = 200, destructive = 403 by default)
# ===========================================================================
def test_routes_behaviour():
    print("--> Testing maintenance route vs debug route behaviour...")
    ctx = E2ETestContext(prefix="mangatrax_maintenance_route_")
    try:
        # Safe action: available without the debug flag.
        response = ctx.client.post("/api/maintenance/clear", json_data={"action": "clear-discovery"})
        assert response.status_code == 200, (response.status_code, response.json())
        assert response.json().get("status") == "success", response.json()
        assert "Cleared" in response.json().get("message", ""), response.json()

        # Strict payload: unknown key and destructive action names are rejected 400.
        for bad_payload in (
            {"action": "clear-discovery", "unexpected": True},
            {"action": "purge-non-manga"},
            {"action": "reset-all-data"},
            {"action": "clear-thumbnails"},
        ):
            bad = ctx.client.post("/api/maintenance/clear", json_data=bad_payload)
            assert bad.status_code == 400, (bad_payload, bad.status_code, bad.json())

        # Destructive actions remain debug-gated (default: flag off -> 403).
        if not main_app.AUTH_CONFIG.debug_actions:  # pragma: no cover - operator override
            for action in ("purge-non-manga", "normalize-english-titles", "reset-all-data"):
                blocked = ctx.client.post("/api/debug/action", json_data={"action": action})
                assert blocked.status_code == 403, (action, blocked.status_code, blocked.json())
                assert "MANGATRAX_DEBUG_ACTIONS" in json.dumps(blocked.json()), blocked.json()
        else:
            print("    note: MANGATRAX_DEBUG_ACTIONS enabled; 403 checks skipped.")
    finally:
        ctx.close()
    print("    [PASSED] 200 for safe actions, 400 for destructive names, debug still 403.")


# ===========================================================================
# 8. Settings template: Maintenance card outside the debug gate
# ===========================================================================
def test_template_maintenance_always_visible():
    print("--> Testing Settings template structure...")
    with open(SETTINGS_TEMPLATE, encoding="utf-8") as handle:
        source = handle.read()
    if_start = source.index("{% if debug_actions_enabled %}")
    if_end = source.index("{% endif %}", if_start)
    gated_section = source[if_start:if_end]
    always_section = source[:if_start]

    # The safe action cards + buttons live outside the gated block.
    for marker in (
        "btn-maint-cover-cache",
        "btn-maint-clear-discovery",
        "btn-maint-clear-descriptions",
        "maintenanceAction('clear-cover-cache')",
        "maintenanceAction('clear-discovery')",
        "maintenanceAction('clear-descriptions')",
    ):
        assert marker in always_section, marker
        assert marker not in gated_section, marker
    assert "Maintenance" in always_section, "always-visible Maintenance heading missing"

    # Destructive controls remain inside the gated block.
    for marker in (
        "debugAction('purge-non-manga')",
        "debugAction('normalize-english-titles')",
        "handleResetDataClick()",
        "Danger Zone",
    ):
        assert marker in gated_section, marker
    # No clear-* debug buttons should remain in the gated block.
    for legacy in ("debugAction('clear-thumbnails')", "debugAction('clear-discovery')",
                   "debugAction('clear-descriptions')"):
        assert legacy not in gated_section, legacy

    # JS posts to the maintenance route, confirms, toasts and disables the button.
    script = source[if_end:]
    assert "/api/maintenance/clear" in script, script[-2000:]
    assert "MAINTENANCE_CONFIRMATIONS" in script
    assert "btn.disabled = true" in script and "btn.disabled = false" in script
    assert "showToast(data.message" in script
    print("    [PASSED] safe cards unconditional; dangerous controls stay debug-gated.")


# ===========================================================================
# 9. Audit: MAINTENANCE_CLEARED row carries the authenticated actor
# ===========================================================================
def test_audit_actor():
    print("--> Testing MAINTENANCE_CLEARED audit row with actor...")
    ctx = _Ctx()
    try:
        ctx.add_release("g1", "Untracked Manga", "DISCOVERY")
        assert auth.get_current_actor() == "system", auth.get_current_actor()
        token = auth.set_current_actor("alice")
        try:
            result = maintenance.perform("clear-discovery", ctx.db, ctx.cover_cache)
        finally:
            auth.reset_current_actor(token)

        rows = [row for row in ctx.db.get_logs(limit=100)
                if row.get("event_type") == maintenance.MAINTENANCE_EVENT]
        assert len(rows) == 1, rows
        row = rows[0]
        assert row.get("actor") == "alice", row
        details = f"{row.get('message', '')} {row.get('details', '')}"
        assert "clear-discovery" in details, details
        assert f"count={result['count']}" in details, details
        print("    [PASSED] audit row records actor, action and count.")
    finally:
        ctx.close()


def main():
    print("=" * 78)
    print("Running safe maintenance / clear tools suite (task-13)")
    print("=" * 78)
    tests = [
        test_safe_action_map,
        test_clear_discovery_scoping,
        test_clear_descriptions_scoping,
        test_clear_cover_cache,
        test_strict_schema,
        test_route_wiring_source_guard,
        test_routes_behaviour,
        test_template_maintenance_always_visible,
        test_audit_actor,
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
        print(f"MAINTENANCE SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL MAINTENANCE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
