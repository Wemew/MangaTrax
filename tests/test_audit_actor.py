#!/usr/bin/env python3
"""M3 audit-trail suite: "which user triggered this download".

Covers:
  * activity_logs.actor migration and users table (subject UNIQUE, JIT upsert)
  * the current_actor ContextVar making log_activity() attribute existing call
    sites without rewriting them
  * the manual-download route carrying the logged-in actor
  * the background scheduler stamping actor='scheduler' (explicit, no request
    context) even while the surrounding thread has a user actor bound
  * the Logs page surfacing the actor column

Run through the sanctioned runner (assertions compiled live):
    calibre-debug.exe -e "tests\\run_tests.py"
"""

import os
import sys
import threading
import time
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth
from app.database import MangaDatabase
from e2e.harness import E2ETestContext, main_app


def _activities(db):
    return db.get_logs(limit=200)


def _find(rows, event_type):
    return [row for row in rows if row.get("event_type") == event_type]


# ---------------------------------------------------------------------------
# 1. Schema / migration
# ---------------------------------------------------------------------------
def test_activity_log_actor_migration():
    print("--> Testing activity_logs.actor + users table migration...")
    ctx = E2ETestContext(prefix="mangatrax_audit_")
    try:
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(activity_logs);")
            log_cols = {row["name"] for row in cur.fetchall()}
            cur.execute("PRAGMA table_info(users);")
            user_cols = {row["name"] for row in cur.fetchall()}

        assert "actor" in log_cols, f"activity_logs.actor missing: {sorted(log_cols)}"
        assert {"id", "subject", "username", "email", "display_name", "auth_source",
                "first_seen", "last_seen"} <= user_cols, f"users columns incomplete: {sorted(user_cols)}"

        # Migration is repeatable (guarded PRAGMA check must not raise).
        ctx.db.init_db()
        with ctx.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(activity_logs);")
            assert "actor" in {row["name"] for row in cur.fetchall()}
        print("    [PASSED] activity_logs.actor column and users table exist and migration is idempotent.")
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 2. ContextVar attribution without call-site churn
# ---------------------------------------------------------------------------
def test_contextvar_attribution():
    print("--> Testing contextvar-driven attribution in log_activity()...")
    ctx = E2ETestContext(prefix="mangatrax_audit_")
    try:
        assert auth.get_current_actor() == "system", "default actor must be 'system'"

        token = auth.set_current_actor("alice")
        try:
            ctx.db.log_activity("TEST_CONTEXT", "user-triggered event")
        finally:
            auth.reset_current_actor(token)

        # Explicit actor always wins over the contextvar (background worker contract).
        token = auth.set_current_actor("alice")
        try:
            ctx.db.log_activity("TEST_SCHEDULER", "automated event", actor="scheduler")
        finally:
            auth.reset_current_actor(token)

        after_reset = auth.get_current_actor()
        rows = _activities(ctx.db)
        context_rows = _find(rows, "TEST_CONTEXT")
        scheduler_rows = _find(rows, "TEST_SCHEDULER")
        assert context_rows and context_rows[0]["actor"] == "alice", context_rows
        assert scheduler_rows and scheduler_rows[0]["actor"] == "scheduler", scheduler_rows
        assert after_reset == "system", after_reset

        # actor='system' fallback when nothing is bound.
        ctx.db.log_activity("TEST_SYSTEM", "no actor context")
        system_rows = _find(_activities(ctx.db), "TEST_SYSTEM")
        assert system_rows[0]["actor"] == "system", system_rows

        print("    [PASSED] log_activity() defaults to the request actor; explicit actor wins.")
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 3. JIT users provisioning
# ---------------------------------------------------------------------------
def test_users_jit_provisioning():
    print("--> Testing JIT user provisioning/upsert...")
    ctx = E2ETestContext(prefix="mangatrax_audit_")
    try:
        first = ctx.db.upsert_user(
            subject="oidc-sub-1", username="alice", email="alice@example.com",
            display_name="Alice", auth_source="oidc",
        )
        assert first["subject"] == "oidc-sub-1", first
        assert first["username"] == "alice"
        assert first["auth_source"] == "oidc"
        assert first["first_seen"], first

        # Second login: same subject updates fields, must not create a duplicate.
        second = ctx.db.upsert_user(
            subject="oidc-sub-1", username="alice2", email="alice2@example.com",
            display_name="Alice Two", auth_source="oidc",
        )
        assert second["id"] == first["id"], (first["id"], second["id"])
        assert second["username"] == "alice2"
        assert len(ctx.db.get_all_users()) == 1, ctx.db.get_all_users()

        # Identity → actor label mapping.
        identity = auth.Identity.from_oidc_claims({
            "sub": "oidc-sub-2", "preferred_username": "bob", "name": "Bob",
            "email": "bob@example.com",
        })
        assert identity.actor == "Bob", identity.actor
        basic_identity = auth.Identity.for_basic("admin")
        assert basic_identity.actor == "admin"
        assert basic_identity.auth_source == "basic"

        # AuthManager.record_identity is the JIT hook used by the middleware/callback.
        manager = auth.AuthManager(
            auth.AuthConfig(mode="oidc", public_url="https://mangatrax.example.com",
                            session_secret="s" * 32, oidc_issuer="https://idp.example.com",
                            oidc_client_id="cid", oidc_client_secret="secret"),
            user_store=ctx.db,
        )
        manager.record_identity(identity, force=True)
        assert ctx.db.get_user("oidc-sub-2") is not None, ctx.db.get_all_users()

        # Basic-mode identities are recorded too (so break-glass/basic logins are
        # attributable beyond a generic "admin" label).
        import base64
        basic_config = auth.AuthConfig(mode="basic", basic_user="admin", basic_pass="hunter2")
        basic_manager = auth.AuthManager(basic_config, user_store=ctx.db)
        header = "Basic " + base64.b64encode(b"admin:hunter2").decode("ascii")
        decision = basic_manager.check_request("GET", "/series", {"authorization": header}, {})
        assert decision.action == "allow", decision
        recorded = ctx.db.get_user("basic:admin")
        assert recorded is not None and recorded["auth_source"] == "basic", recorded

        print("    [PASSED] users are JIT-provisioned on first login and refreshed afterwards.")
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 4. Manual download route attribution (the user's core question)
# ---------------------------------------------------------------------------
class _FakeQBit:
    def add_torrent(self, url_or_magnet=None, savepath=None, category=None, tags=None):
        self.last_add = url_or_magnet
        return True

    def extract_torrent_hash(self, url):
        return None

    def apply_selective_volume_download(self, torrent_hash, volumes):
        return {"status": "unsupported"}


def test_manual_download_route_is_attributed():
    print("--> Testing 'which user triggered this download' attribution...")
    ctx = E2ETestContext(prefix="mangatrax_audit_")
    try:
        _, release_id = ctx.db.upsert_release({
            "guid": "audit-manual-download-1",
            "title": "Frieren Vol. 1",
            "series_name": "Frieren",
            "download_url": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            "status": "REQUESTED",
        })

        token = auth.set_current_actor("alice")
        try:
            with patch.object(ctx.scheduler, "get_qbit_client", return_value=_FakeQBit()):
                response = ctx.client.post(f"/api/releases/{release_id}/download")
        finally:
            auth.reset_current_actor(token)

        assert response.status_code == 200, (response.status_code, response.text)
        rows = _find(_activities(ctx.db), "MANUAL_DOWNLOAD")
        assert rows, "manual download did not produce a MANUAL_DOWNLOAD activity log"
        assert rows[0]["actor"] == "alice", f"expected alice, got {rows[0]['actor']!r}"

        print("    [PASSED] the manual download event carries the logged-in user as actor.")
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 5. Background worker is stamped 'scheduler' even under a user actor
# ---------------------------------------------------------------------------
class _EmptyProvider:
    def fetch_releases(self):
        return []


def test_background_worker_actor_is_scheduler():
    print("--> Testing background scheduler actor stamping...")
    ctx = E2ETestContext(prefix="mangatrax_audit_")
    try:
        # Calibre sync runs in the scheduler and must stamp actor='scheduler'.
        ctx.create_mock_calibre_library([
            {"name": "Dungeon Meshi", "author": "Ryoko Kui", "volumes": [1.0, 2.0]},
        ])
        token = auth.set_current_actor("alice")
        try:
            ctx.scheduler.sync_calibre_library()
        finally:
            auth.reset_current_actor(token)
        sync_rows = _find(_activities(ctx.db), "CALIBRE_SYNC")
        assert sync_rows, "Calibre sync produced no CALIBRE_SYNC log"
        assert sync_rows[0]["actor"] == "scheduler", sync_rows[0]

        # Feed polling from a real background thread (no request context at all).
        import app.scheduler as scheduler_module
        with patch.object(scheduler_module, "create_provider", return_value=_EmptyProvider()):
            def _worker():
                ctx.scheduler.check_all_feeds()

            worker = threading.Thread(target=_worker, name="audit-feed-worker")
            worker.start()
            worker.join(timeout=30)
            assert not worker.is_alive(), "feed worker did not finish"

        feed_rows = _find(_activities(ctx.db), "FEED_POLL_COMPLETED")
        assert feed_rows, "feed polling produced no FEED_POLL_COMPLETED log"
        assert feed_rows[0]["actor"] == "scheduler", feed_rows[0]

        # Nothing in ctx.db should have been attributed to a phantom request user.
        assert all(row["actor"] in ("scheduler", "system") for row in _find(_activities(ctx.db), "CALIBRE_SYNC_ERROR") + feed_rows)

        print("    [PASSED] automated events are stamped actor='scheduler', never the request user.")
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 6. Logs page surfaces the actor
# ---------------------------------------------------------------------------
def test_logs_page_surfaces_actor():
    print("--> Testing Logs page actor column/badge...")
    template_path = os.path.join(BACKEND_DIR, "app", "templates", "logs.html")
    with open(template_path, "r", encoding="utf-8") as handle:
        template = handle.read()
    assert "log.actor" in template, "logs.html does not render the actor column/badge"
    assert "data-text=" in template and "log.actor" in template

    logs_view_source = os.path.join(BACKEND_DIR, "app", "main.py")
    with open(logs_view_source, "r", encoding="utf-8") as handle:
        main_source = handle.read()
    assert '"logs": logs' in main_source, "logs_view does not pass logs to the template"

    print("    [PASSED] Logs page renders an actor badge per activity row.")


def main():
    print("=" * 60)
    print("Running M3 Audit Trail & Actor Attribution Suite")
    print("=" * 60)
    tests = [
        test_activity_log_actor_migration,
        test_contextvar_attribution,
        test_users_jit_provisioning,
        test_manual_download_route_is_attributed,
        test_background_worker_actor_is_scheduler,
        test_logs_page_surfaces_actor,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception:
            failures += 1
            print(f"  [FAILED] {test.__name__}")
            traceback.print_exc()
    print("=" * 60)
    if failures:
        print(f"AUDIT TRAIL SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL AUDIT TRAIL TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
