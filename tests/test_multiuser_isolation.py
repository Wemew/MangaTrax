#!/usr/bin/env python3
"""Multi-user data isolation, admin gating and request approval.

Covers:
  * shared Calibre rows visible to everyone vs per-user tracked membership,
  * per-user monitor toggle, blacklist and prioritized groups,
  * request approval queue (pending -> approved) and bypass users,
  * Admin role enforcement for sensitive endpoints,
  * per-user staging folder naming (stable, collision-safe).
"""

import json
import os
import sys
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from fastapi import HTTPException  # noqa: E402  (harness installs the shim when absent)

import app.main as main_app  # noqa: E402
from app import auth as auth_module  # noqa: E402
from app.auth import Identity  # noqa: E402


def _user(ctx, subject, username, admin=False):
    groups = ["mangatrax-admins"] if admin else []
    return ctx.db.upsert_user(
        subject=subject, username=username, display_name=username.title(),
        auth_source="oidc", groups=groups, is_admin=admin,
    )


def _series(ctx, name, calibre_id=None, monitored=1):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO series_tracking (name, authors, is_monitored, calibre_series_id) "
            "VALUES (?, '', ?, ?);",
            (name, monitored, calibre_id),
        )
        conn.commit()
        return cur.lastrowid


def _release(ctx, guid, title, series, status="DISCOVERY"):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, download_url) "
            "VALUES (?, ?, ?, ?, ?);",
            (guid, title, series, status, "magnet:?xt=urn:btih:" + guid),
        )
        conn.commit()
        return cur.lastrowid


def test_shared_vs_private_listing(ctx, red, blue):
    shared = _series(ctx, "Calibre Shared", calibre_id=11)
    private = _series(ctx, "Blue Private")
    ctx.db.add_user_series(blue["id"], private, is_monitored=True)

    red_names = {row["name"] for row in ctx.db.get_all_series(user_id=red["id"])}
    blue_names = {row["name"] for row in ctx.db.get_all_series(user_id=blue["id"])}
    assert "Calibre Shared" in red_names and "Calibre Shared" in blue_names
    assert "Blue Private" not in red_names, red_names
    assert "Blue Private" in blue_names, blue_names
    assert ctx.db.count_all_series(user_id=red["id"]) == len(red_names)
    assert ctx.db.count_all_series(user_id=blue["id"]) == len(blue_names)


def test_per_user_monitor_toggle(ctx, red, blue):
    shared = _series(ctx, "Shared Toggle", calibre_id=12)
    assert shared

    with patch.object(main_app, "_current_user_row", return_value=blue):
        res = ctx.client.post(f"/api/series/{shared}/toggle")
    assert res.status_code == 200, res.text
    first = res.json()["is_monitored"]
    membership = ctx.db.get_user_series_membership(blue["id"], shared)
    assert membership is not None, "toggle must create the user's membership"
    assert bool(membership["is_monitored"]) == first

    with patch.object(main_app, "_current_user_row", return_value=blue):
        res = ctx.client.post(f"/api/series/{shared}/toggle")
    assert res.json()["is_monitored"] is (not first)

    # Another user's state is untouched.
    red_membership = ctx.db.get_user_series_membership(red["id"], shared)
    assert red_membership is None, red_membership


def test_per_user_blacklist_and_groups(ctx, red, blue):
    with patch.object(main_app, "_current_user_row", return_value=blue):
        assert ctx.client.post("/api/blacklist", json_data={"name": "Bad Series", "reason": "nope"}).status_code == 200
        assert ctx.client.post("/api/prioritized/groups", json_data={"name": "BlueGroup", "notes": "", "priority": 2}).status_code == 200

    assert len(ctx.db.get_user_blacklist(blue["id"])) == 1
    assert ctx.db.get_user_blacklist(red["id"]) == []
    shared_names = {row["name"] for row in ctx.db.get_blacklisted_series()}
    assert "Bad Series" not in shared_names, \
        "per-user blacklist must not leak into the shared table"

    blue_groups = ctx.db.get_user_prioritized_groups(blue["id"])
    assert len(blue_groups) == 1 and blue_groups[0]["name"] == "BlueGroup"
    assert ctx.db.get_user_prioritized_groups(red["id"]) == []

    with patch.object(main_app, "_current_user_row", return_value=blue):
        listed = ctx.client.get("/api/prioritized/groups").json()["groups"]
    assert [g["name"] for g in listed] == ["BlueGroup"], listed

    # The per-user blacklist hides those series from that user's release list only.
    _release(ctx, "bl-1", "Bad Series v05", "Bad Series", status="REQUESTED")
    _release(ctx, "bl-2", "Good Series v05", "Good Series", status="REQUESTED")
    blue_names = {
        row["series_name"] for row in ctx.db.get_releases(status="REQUESTED", user_id=blue["id"])
    }
    red_names = {
        row["series_name"] for row in ctx.db.get_releases(status="REQUESTED", user_id=red["id"])
    }
    assert "Bad Series" not in blue_names, "per-user blacklist must hide the series"
    assert "Bad Series" in red_names, "another user's blacklist must not hide it"
    assert "Good Series" in blue_names and "Good Series" in red_names

    # The HTTP surface forwards the acting user into that filter.
    main_source = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    assert "sort=clean_sort, user_id=user_id" in main_source, "releases route must pass user_id"
    assert "tag_match=clean_match, sort=clean_sort, user_id=user_id" in main_source, \
        "discovery route must pass user_id"


def test_request_approval_flow(ctx, red, blue):
    release_id = _release(ctx, "req-1", "Blue Wants This v01", "Blue Wants This")

    with patch.object(main_app, "_current_user_row", return_value=blue):
        res = ctx.client.post(f"/api/releases/{release_id}/download")
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "pending_approval"
    pending = ctx.db.list_pending_approvals()
    assert len(pending) == 1 and pending[0]["release_id"] == release_id, pending
    assert pending[0]["requester"] == "Blue"

    with patch.object(main_app, "_current_user_row", return_value=red):
        res = ctx.client.post(f"/api/admin/approvals/{pending[0]['id']}/approve")
    assert res.status_code == 200, res.text
    assert ctx.db.list_pending_approvals() == []
    release = ctx.db.get_release_by_id(release_id)
    assert release["status"] == "REQUESTED", release["status"]
    assert int(release["request_user_id"]) == blue["id"], release.get("request_user_id")

    # Rejected requests never touch the release status.
    release2 = _release(ctx, "req-2", "Reject Me v01", "Reject Me")
    with patch.object(main_app, "_current_user_row", return_value=blue):
        ctx.client.post(f"/api/releases/{release2}/download")
    pending2 = ctx.db.list_pending_approvals()
    with patch.object(main_app, "_current_user_row", return_value=red):
        res = ctx.client.post(f"/api/admin/approvals/{pending2[0]['id']}/reject")
    assert res.status_code == 200 and ctx.db.get_release_by_id(release2)["status"] == "DISCOVERY"

    # A rejected request can be raised again (it returns to pending, not stuck).
    with patch.object(main_app, "_current_user_row", return_value=blue):
        res = ctx.client.post(f"/api/releases/{release2}/download")
    assert res.status_code == 202, res.text
    assert ctx.db.get_release_request(blue["id"], release2)["status"] == "pending"


def test_approved_request_downloads_on_click(ctx, red, blue):
    """Approval is a one-time gate: the next Download click must actually submit."""
    ctx.db.set_user_flags(blue["id"], auto_download=False, bypass_approval=False)
    blue = ctx.db.get_user_by_id(blue["id"])
    release_id = _release(ctx, "appr-1", "Approved Release v01", "Approved Release")

    with patch.object(main_app, "_current_user_row", return_value=blue):
        first = ctx.client.post(f"/api/releases/{release_id}/download")
        repeat = ctx.client.post(f"/api/releases/{release_id}/download")
    assert first.status_code == 202 and first.json()["status"] == "pending_approval", first.text
    assert repeat.status_code == 202 and repeat.json()["status"] == "already_pending", repeat.text

    pending = [p for p in ctx.db.list_pending_approvals() if p["release_id"] == release_id]
    with patch.object(main_app, "_current_user_row", return_value=red):
        res = ctx.client.post(f"/api/admin/approvals/{pending[0]['id']}/approve")
    assert res.status_code == 200 and res.json()["auto_download"] is False, res.text

    seen = {}
    class StubQbit:
        enabled = True
        def extract_torrent_hash(self, url):
            return "approvedhash"
        def add_torrent(self, url_or_magnet, savepath=None, category=None, tags=None):
            seen["savepath"] = savepath
            return True
        def apply_selective_volume_download(self, torrent_hash, wanted_volumes):
            return {"status": "pending_metadata"}

    with patch.object(main_app, "_current_user_row", return_value=blue), \
         patch.object(main_app.scheduler, "get_qbit_client", return_value=StubQbit()), \
         patch.object(main_app, "resolve_downloader_engine", return_value="qbit"):
        res = ctx.client.post(f"/api/releases/{release_id}/download")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "success", res.text
    folder = ctx.db.ensure_user_staging_folder(blue["id"])
    assert seen["savepath"].replace("\\", "/").endswith(folder), seen
    assert ctx.db.get_release_request(blue["id"], release_id)["status"] == "approved"


def test_download_ui_handles_pending(ctx):
    templates = os.path.join(BACKEND_DIR, "app", "templates")
    for name in ("releases.html", "prioritized.html", "index.html"):
        source = open(os.path.join(templates, name), encoding="utf-8").read()
        assert "pending_approval" in source, name
        assert "already_pending" in source, name


def test_approval_auto_download(ctx, red, blue):
    """Approved requests submit automatically only when the admin enabled it."""
    ctx.db.set_user_flags(blue["id"], auto_download=True)
    blue = ctx.db.get_user_by_id(blue["id"])
    release_id = _release(ctx, "auto-1", "Auto Release v01", "Auto Release")
    with patch.object(main_app, "_current_user_row", return_value=blue):
        ctx.client.post(f"/api/releases/{release_id}/download")
    pending = [p for p in ctx.db.list_pending_approvals() if p["release_id"] == release_id]
    assert len(pending) == 1, pending

    seen = {}
    class StubQbit:
        enabled = True
        def extract_torrent_hash(self, url):
            return "autohash"
        def add_torrent(self, url_or_magnet, savepath=None, category=None, tags=None):
            seen["savepath"] = savepath
            return True
        def apply_selective_volume_download(self, torrent_hash, wanted_volumes):
            return {"status": "pending_metadata"}

    with patch.object(main_app, "_current_user_row", return_value=red), \
         patch.object(main_app.scheduler, "get_qbit_client", return_value=StubQbit()), \
         patch.object(main_app, "resolve_downloader_engine", return_value="qbit"):
        res = ctx.client.post(f"/api/admin/approvals/{pending[0]['id']}/approve")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("download", {}).get("status") == "success", body
    folder = ctx.db.ensure_user_staging_folder(blue["id"])
    assert seen["savepath"].replace("\\", "/").endswith(folder), seen
    assert ctx.db.get_release_by_id(release_id)["status"] == "REQUESTED"

    # Turned off again: approval no longer submits the download by itself.
    ctx.db.set_user_flags(blue["id"], auto_download=False)
    blue = ctx.db.get_user_by_id(blue["id"])
    manual_id = _release(ctx, "auto-2", "Manual Release v01", "Manual Release")
    with patch.object(main_app, "_current_user_row", return_value=blue):
        ctx.client.post(f"/api/releases/{manual_id}/download")
    pending2 = [p for p in ctx.db.list_pending_approvals() if p["release_id"] == manual_id]
    with patch.object(main_app, "_current_user_row", return_value=red):
        res = ctx.client.post(f"/api/admin/approvals/{pending2[0]['id']}/approve")
    assert res.status_code == 200 and "download" not in res.json(), res.text


def test_bypass_user_downloads_to_own_staging(ctx, blue):
    ctx.db.set_user_flags(blue["id"], bypass_approval=True)
    blue = ctx.db.get_user_by_id(blue["id"])
    release_id = _release(ctx, "req-3", "Bypass Release v01", "Bypass Release")
    seen = {}

    class StubQbit:
        enabled = True
        def extract_torrent_hash(self, url):
            return "deadbeef"
        def add_torrent(self, url_or_magnet, savepath=None, category=None, tags=None):
            seen["savepath"] = savepath
            return True
        def apply_selective_volume_download(self, torrent_hash, wanted_volumes):
            return {"status": "pending_metadata"}

    with patch.object(main_app, "_current_user_row", return_value=blue), \
         patch.object(main_app.scheduler, "get_qbit_client", return_value=StubQbit()), \
         patch.object(main_app, "resolve_downloader_engine", return_value="qbit"):
        res = ctx.client.post(f"/api/releases/{release_id}/download")
    assert res.status_code == 200, res.text
    folder = ctx.db.ensure_user_staging_folder(blue["id"])
    assert seen["savepath"].replace("\\", "/").endswith(folder), seen
    pending = [p for p in ctx.db.list_pending_approvals() if p["release_id"] == release_id]
    assert pending == [], "bypass users skip the queue"


def test_whoami_reports_groups_and_role(ctx):
    original_mode = main_app.AUTH.config.mode
    original_groups = main_app.AUTH.config.admin_groups
    original_set = main_app.AUTH._admin_group_set
    try:
        main_app.AUTH.config.mode = "oidc"
        main_app.AUTH.config.admin_groups = ("admin",)
        main_app.AUTH._admin_group_set = {"admin"}

        token = auth_module.set_current_identity(
            Identity(subject="oidc|red", username="red", auth_source="oidc", groups=("admin",))
        )
        try:
            data = ctx.client.get("/api/whoami").json()
        finally:
            auth_module.reset_current_identity(token)
        assert data["subject"] == "oidc|red", data
        assert data["groups"] == ["admin"], data
        assert data["is_admin"] is True, data
        assert data["admin_groups"] == ["admin"], data

        token = auth_module.set_current_identity(
            Identity(subject="oidc|blue", username="blue", auth_source="oidc", groups=("readers",))
        )
        try:
            data = ctx.client.get("/api/whoami").json()
        finally:
            auth_module.reset_current_identity(token)
        assert data["groups"] == ["readers"] and data["is_admin"] is False, data
    finally:
        main_app.AUTH.config.mode = original_mode
        main_app.AUTH.config.admin_groups = original_groups
        main_app.AUTH._admin_group_set = original_set


def test_require_admin_gate(ctx, red, blue):
    original_mode = main_app.AUTH.config.mode
    original_groups = main_app.AUTH.config.admin_groups
    original_set = main_app.AUTH._admin_group_set
    try:
        main_app.AUTH.config.mode = "oidc"
        main_app.AUTH.config.admin_groups = ("mangatrax-admins",)
        main_app.AUTH._admin_group_set = {"mangatrax-admins"}

        token = auth_module.set_current_identity(
            Identity(subject="blue", username="blue", auth_source="oidc", groups=())
        )
        try:
            try:
                main_app.require_admin()
                raise AssertionError("non-admin must be rejected")
            except HTTPException as exc:
                assert exc.status_code == 403, exc
        finally:
            auth_module.reset_current_identity(token)

        token = auth_module.set_current_identity(
            Identity(subject="red", username="red", auth_source="oidc", groups=("mangatrax-admins",))
        )
        try:
            main_app.require_admin()  # no raise
        finally:
            auth_module.reset_current_identity(token)
    finally:
        main_app.AUTH.config.mode = original_mode
        main_app.AUTH.config.admin_groups = original_groups
        main_app.AUTH._admin_group_set = original_set


def test_staging_dir_scoping(ctx, red, blue):
    ctx.db.set_setting("staging_dir", ctx.staging_dir)
    main_app._CURRENT_USER_CACHE.set(None)

    folder = ctx.db.ensure_user_staging_folder(blue["id"])
    with patch.object(main_app, "_current_user_row", return_value=blue):
        blue_dir = main_app._staging_dir_setting()
    assert os.path.realpath(blue_dir) == os.path.realpath(os.path.join(ctx.staging_dir, folder)), blue_dir
    assert os.path.isdir(blue_dir), "the user staging folder must be created on demand"

    with patch.object(main_app, "_current_user_row", return_value=red):
        assert main_app._staging_dir_setting() == ctx.staging_dir, "admins scan the whole tree"
    with patch.object(main_app, "_current_user_row", return_value=None):
        assert main_app._staging_dir_setting() == ctx.staging_dir, "legacy mode is unchanged"

    # A staged file in any user folder still counts as "in staging" globally.
    from app.staged_ownership import build_staged_volumes_map
    os.makedirs(blue_dir, exist_ok=True)
    open(os.path.join(blue_dir, "Blue Private Series v02.cbz"), "wb").close()
    shared_map = build_staged_volumes_map(ctx.db, ctx.staging_dir)
    assert shared_map, "staging map must see the user folder"
    assert any(2.0 in volumes for volumes in shared_map.values()), shared_map


def test_per_user_routing(ctx, blue):
    import zipfile

    _series(ctx, "Routed Series", calibre_id=None)  # template so routing can tag
    folder = ctx.db.ensure_user_staging_folder(blue["id"])
    downloads_root = ctx.scheduler.get_downloads_dir()
    user_downloads = os.path.join(downloads_root, folder)
    os.makedirs(user_downloads, exist_ok=True)
    filename = "Routed Series v02.cbz"
    with zipfile.ZipFile(os.path.join(user_downloads, filename), "w") as archive:
        archive.writestr("001.jpg", b"fake-image-bytes")

    # The shared/global pass must never pull a user's file into the staging root.
    user_staging = os.path.join(ctx.staging_dir, folder)
    nested = os.path.join(user_staging, "nested")
    os.makedirs(nested, exist_ok=True)
    open(os.path.join(nested, "Nested Series v01.cbz"), "wb").close()
    ctx.scheduler.process_downloads_to_staging(actor="scheduler")
    assert os.path.isfile(os.path.join(user_downloads, filename)), "global pass touched a user folder"
    assert not os.path.exists(os.path.join(ctx.staging_dir, filename)), "file leaked into the shared root"
    assert os.path.isfile(os.path.join(nested, "Nested Series v01.cbz")), \
        "global flatten must not flatten a user's staging folder"

    result = ctx.scheduler.process_downloads_to_staging_for_users()
    assert folder in result, result
    assert "error" not in (result.get(folder) or {}), result.get(folder)
    user_staging = os.path.join(ctx.staging_dir, folder)
    assert os.path.isdir(user_staging)
    assert os.path.isfile(os.path.join(user_staging, filename)), os.listdir(user_staging)
    # Flat inside the user folder, and never at the shared root.
    assert not os.path.exists(os.path.join(ctx.staging_dir, filename))


def test_admin_surface_source_guards(ctx):
    templates_dir = os.path.join(BACKEND_DIR, "app", "templates")
    main_source = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    for decorator_prefix in (
        '@app.post("/api/settings"',
        '@app.post("/api/debug/action"',
        '@app.get("/admin"',
        '@app.get("/logs"',
        '@app.get("/settings"',
        '@app.post("/api/admin/approvals/{approval_id}/approve"',
        '@app.post("/api/releases/{release_id}/status"',
        '@app.post("/api/series/{series_id}/identity"',
    ):
        idx = main_source.find(decorator_prefix)
        assert idx != -1, decorator_prefix
        line_end = main_source.find("\n", idx)
        decorator = main_source[idx:line_end]
        assert "require_admin" in decorator, (decorator_prefix, decorator)

    series_row = open(os.path.join(templates_dir, "partials", "series_table_row_chunk.html"), encoding="utf-8").read()
    assert "{% if is_admin() %}" in series_row, "admin-only row actions must be wrapped"
    release_row = open(os.path.join(templates_dir, "partials", "flat_release_row_chunk.html"), encoding="utf-8").read()
    assert "{% if is_admin() %}" in release_row, "catalog mutation buttons must be admin-only"


def test_complete_expansion_stops_requerying(ctx):
    """Expanded complete releases and failed lookups must not hit AniList every pass."""
    with ctx.db.get_connection() as conn:
        conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, is_complete, "
            "volumes_json, missing_vols_json) VALUES (?,?,?,?,?,?,?)",
            ("complete-expanded", "Expanded Series (Complete)", "Expanded Series", "DISCOVERY", 1,
             json.dumps([1.0, 2.0, 3.0]), json.dumps([1.0, 2.0, 3.0])),
        )
        conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, is_complete) VALUES (?,?,?,?,?)",
            ("complete-unknown", "Unknown Series (Complete)", "Unknown Series", "DISCOVERY", 1),
        )
        conn.commit()

    calls = []
    def _identity(name, *args, **kwargs):
        calls.append(name)
        return {}  # provider has no volume count for this title

    with patch.object(ctx.scheduler.anilist, "resolve_manga_series", side_effect=_identity), \
         patch.object(ctx.scheduler.mangaupdates, "get_series_info", return_value={}):
        first = ctx.scheduler.expand_unresolved_complete_releases()
    assert first == 0
    assert calls, "the unknown complete release should be tried once"
    seen_norms = [str(name).strip().lower() for name in calls]
    assert len(seen_norms) == len(set(seen_norms)), calls  # no duplicate candidate storm in one pass
    assert len(calls) <= 2, calls  # series name + full title, deduplicated
    after_first = len(calls)

    with patch.object(ctx.scheduler.anilist, "resolve_manga_series", side_effect=_identity), \
         patch.object(ctx.scheduler.mangaupdates, "get_series_info", return_value={}):
        second = ctx.scheduler.expand_unresolved_complete_releases()
        third = ctx.scheduler.expand_unresolved_complete_releases()
    assert second == 0 and third == 0
    assert len(calls) == after_first, "throttled passes must not re-query AniList"

    with patch.object(ctx.scheduler.anilist, "resolve_manga_series", side_effect=_identity), \
         patch.object(ctx.scheduler.mangaupdates, "get_series_info", return_value={}):
        forced = ctx.scheduler.expand_unresolved_complete_releases(series_name="Unknown Series")
    assert forced == 0
    assert len(calls) > after_first, "an explicit series refresh bypasses the throttle"


def test_anilist_negative_volume_cache(ctx):
    from app.metadata.anilist import AniListMetadataService
    service = AniListMetadataService(db=ctx.db)
    meta = {
        "canonical_title": "No Volume Series", "romaji_title": "", "cover_url": "",
        "total_volumes": None, "status": "RELEASING", "synopsis": "",
        "source": "anilist", "anilist_id": 424242, "synonyms": [], "genres": [], "tags": [],
        "tags_sorted": [], "matched_term": "no volume series",
    }
    with patch.object(service, "_fetch_from_anilist", return_value=meta) as fetch:
        first = service.resolve_manga_series("No Volume Series")
        second = service.resolve_manga_series("No Volume Series")
        forced = service.resolve_manga_series("No Volume Series", force_refresh=True)
    assert first["anilist_id"] == 424242 and second["anilist_id"] == 424242
    assert forced["anilist_id"] == 424242
    assert fetch.call_count == 2, fetch.call_count  # one normal + one forced; cached miss avoided


def test_torbox_force_whole_override(ctx, red):
    ctx.db.set_setting("torbox_strict_volume_selection", "true")
    release_id = _release(ctx, "force-1", "Force Me v01", "Force Me", status="REQUESTED")
    with ctx.db.get_connection() as conn:
        conn.execute("UPDATE releases SET volumes_json = '[]', volumes_json = '[]' WHERE id = ?;", (release_id,))
        conn.commit()

    submitted = {}
    class StubTorbox:
        name = "torbox"
        def submit(self, release, savepath, wanted_volumes=None):
            submitted["wanted"] = list(wanted_volumes or [])
            submitted["title"] = release.get("title")
            return {"handle": {"torrent_id": 777, "hash": "h", "savepath": savepath,
                               "wanted_volumes": list(wanted_volumes or [])}, "state": "ready"}

    with patch.object(ctx.scheduler, "get_downloads_dir", return_value=ctx.temp_dir), \
         patch("app.scheduler.build_downloader", return_value=StubTorbox()):
        plain = ctx.scheduler.submit_torbox_download(
            release_id, "Force Me v01", "magnet:?xt=urn:btih:deadbeef", missing_volumes=[],
        )
        forced = ctx.scheduler.submit_torbox_download(
            release_id, "Force Me v01", "magnet:?xt=urn:btih:deadbeef", missing_volumes=[],
            force_whole=True,
        )
    assert plain.get("skipped") is True, plain
    assert forced.get("success") and not forced.get("skipped"), forced
    assert submitted["wanted"] == [], submitted
    assert int(ctx.db.get_release_by_id(release_id)["torbox_force_whole"]) == 1

    # The fetch plan ignores strict selection for this release.
    files = [
        {"name": "Force Me v01.cbz", "size": 10},
        {"name": "Force Me v02.cbz", "size": 10},
    ]
    plan = ctx.scheduler._plan_torbox_selection(
        ctx.db.get_release_by_id(release_id), files, [], total_size=0,
    )
    assert plan.get("action") == "files", plan
    assert len(plan.get("files") or []) == len(files), plan
    assert "forced whole torrent" in (plan.get("reason") or ""), plan

    # The endpoint forwards force=true and advertises the retry on a plain skip.
    with patch.object(main_app, "_current_user_row", return_value=red), \
         patch.object(main_app, "resolve_downloader_engine", return_value="torbox"), \
         patch.object(main_app.scheduler, "submit_torbox_download", return_value={
             "success": True, "skipped": True, "message": "nothing missing"}) as submit:
        plain_res = ctx.client.post(f"/api/releases/{release_id}/download")
    assert plain_res.status_code == 200 and plain_res.json()["status"] == "skipped"
    assert plain_res.json()["force_available"] is True, plain_res.text

    with patch.object(main_app, "_current_user_row", return_value=red), \
         patch.object(main_app, "resolve_downloader_engine", return_value="torbox"), \
         patch.object(main_app.scheduler, "submit_torbox_download", return_value={
             "success": True, "skipped": False, "engine": "torbox"}) as submit:
        forced_res = ctx.client.post(
            f"/api/releases/{release_id}/download", json_data={"force": True},
        )
    assert forced_res.status_code == 200, forced_res.text
    assert submit.call_args.kwargs.get("force_whole") is True, submit.call_args

    templates = os.path.join(BACKEND_DIR, "app", "templates")
    for name in ("releases.html", "prioritized.html", "index.html"):
        source = open(os.path.join(templates, name), encoding="utf-8").read()
        assert "force: true" in source, name
        assert "within 4 seconds" in source, name


def test_unresolved_series_queue_and_manual_link(ctx, red):
    with ctx.db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, is_complete) VALUES (?,?,?,?,?)",
            ("unresolved-1", "Mystery Saga (Complete)", "Mystery Saga", "DISCOVERY", 1),
        )
        release_id = cur.lastrowid
        conn.commit()

    def _no_meta(name, *args, **kwargs):
        return {}

    with patch.object(ctx.scheduler.anilist, "resolve_manga_series", side_effect=_no_meta), \
         patch.object(ctx.scheduler.mangaupdates, "get_series_info", return_value={}):
        ctx.scheduler.expand_unresolved_complete_releases()
    failures = [f for f in ctx.db.list_anilist_failures() if f["series_name"] == "Mystery Saga"]
    assert len(failures) == 1, failures
    entry = failures[0]
    assert int(entry["attempts"]) == 1, entry
    assert int(entry["release_id"]) == release_id, entry

    with patch.object(main_app, "_current_user_row", return_value=red):
        listed = ctx.client.get("/api/admin/anilist-failures")
    assert listed.status_code == 200, listed.text
    assert any(f["series_name"] == "Mystery Saga" for f in listed.json()["failures"]), listed.text

    meta = {
        "anilist_id": 5555, "canonical_title": "Mystery Saga", "romaji_title": "",
        "cover_url": "", "total_volumes": 3, "status": "FINISHED", "synopsis": "",
        "source": "anilist", "synonyms": [], "genres": [], "tags": [], "tags_sorted": [],
        "matched_term": "mystery saga",
    }
    with patch.object(main_app, "_current_user_row", return_value=red), \
         patch.object(ctx.scheduler.anilist, "resolve_by_anilist_id", return_value=meta):
        linked = ctx.client.post(
            f"/api/admin/anilist-failures/{entry['id']}/link",
            json_data={"anilist_id": "https://anilist.co/manga/5555/Mystery-Saga"},
        )
    assert linked.status_code == 200, linked.text
    body = linked.json()
    assert body["anilist_id"] == 5555 and body["expanded"] >= 1, body
    assert [f for f in ctx.db.list_anilist_failures() if f["series_name"] == "Mystery Saga"] == []
    assert ctx.db.get_release_by_id(release_id)["volumes"] == [1.0, 2.0, 3.0]

    # Dismiss removes an entry without touching the release state.
    ctx.db.record_anilist_failure("Dismiss Me", None, "", "test")
    with patch.object(main_app, "_current_user_row", return_value=red):
        rows = ctx.client.get("/api/admin/anilist-failures").json()["failures"]
        dismiss_id = next(row["id"] for row in rows if row["series_name"] == "Dismiss Me")
        res = ctx.client.delete(f"/api/admin/anilist-failures/{dismiss_id}")
    assert res.status_code == 200
    assert [f for f in ctx.db.list_anilist_failures() if f["series_name"] == "Dismiss Me"] == []

    settings_html = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    assert 'id="section-anilist-failures"' in settings_html
    assert "/api/admin/anilist-failures" in settings_html


def test_staging_groups_split_by_owner(ctx):
    import zipfile
    from app import staging_groups as staging_groups_module

    root = os.path.join(ctx.temp_dir, "owner-staging")
    for relative in ("Series A v01.cbz", "blue/Series A v02.cbz", "red/Other Series v01.cbz"):
        target = os.path.join(root, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("001.jpg", b"x")

    groups = staging_groups_module.build_staging_groups(
        root, owner_labels={"blue": "Blue", "red": "Red"},
    )
    owners = {(g["owner"], g["owner_label"], g["series"]) for g in groups}
    assert ("", "Shared / legacy", "Series A") in owners, owners
    assert ("blue", "Blue", "Series A") in owners, owners
    assert ("red", "Red", "Other Series") in owners, owners
    assert len(groups) == 3, owners
    for group in groups:
        assert all(f["owner"] == group["owner"] for f in group["files"]), group

    template = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    assert 'id="staging-owner-filter"' in template
    assert 'data-owner="{{ group.owner }}"' in template
    assert "applyStagingFilters" in template


def test_admin_owner_bucket_merges_own_folder(ctx, red):
    import zipfile
    from app import staging_groups as staging_groups_module

    red_folder = ctx.db.ensure_user_staging_folder(red["id"])
    root = os.path.join(ctx.temp_dir, "admin-owner-staging")
    os.makedirs(os.path.join(root, red_folder), exist_ok=True)
    for relative in ("Legacy v01.cbz", f"{red_folder}/Own v01.cbz", "blue/Other v01.cbz"):
        target = os.path.join(root, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("001.jpg", b"x")

    red_row = ctx.db.get_user_by_id(red["id"])
    with patch.object(main_app, "_current_user_row", return_value=red_row):
        labels = main_app._staging_owner_labels()
        options = main_app._staging_owner_options(include_all=True)
        groups = staging_groups_module.build_staging_groups(root, owner_labels=labels)

    assert red_folder.lower() not in labels, labels
    assert options[0] == {"value": "", "label": "Mine / shared"}, options
    assert options[1]["value"] == "all", options
    bucket = {
        g["series"]: g["owner"]
        for g in groups
    }
    assert bucket == {"Legacy": "", "Own": "", "Other": "blue"}, bucket


def test_retag_is_scoped_to_owner(ctx):
    import zipfile
    from app import staging_groups as staging_groups_module
    from app.tagger import cbz_tagger

    root = os.path.join(ctx.temp_dir, "retag-owner-staging")
    relative_files = ("Split Series v01.cbz", "blue/Split Series v01.cbz", "red/Split Series v01.cbz")
    for relative in relative_files:
        target = os.path.join(root, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("001.jpg", b"x")

    result = staging_groups_module.retag_staging_group(
        ctx.db, root, "Split Series", "Split Renamed",
        owner="blue", owner_labels={"blue": "Blue", "red": "Red"},
    )
    assert result["tagged"] == 1, result
    blue_series = (cbz_tagger.read_comicinfo(os.path.join(root, "blue/Split Series v01.cbz")) or {}).get("Series")
    root_series = (cbz_tagger.read_comicinfo(os.path.join(root, "Split Series v01.cbz")) or {}).get("Series")
    red_series = (cbz_tagger.read_comicinfo(os.path.join(root, "red/Split Series v01.cbz")) or {}).get("Series")
    assert blue_series == "Split Renamed", blue_series
    assert not root_series, root_series
    assert not red_series, red_series


def test_staging_folders(ctx):
    one = ctx.db.upsert_user(subject="oidc|same-1", username="Same Name", auth_source="oidc")
    two = ctx.db.upsert_user(subject="oidc|same-2", username="Same Name", auth_source="oidc")
    folder_one = ctx.db.ensure_user_staging_folder(one["id"])
    folder_two = ctx.db.ensure_user_staging_folder(two["id"])
    assert folder_one == "same-name", folder_one
    assert folder_two != folder_one, (folder_one, folder_two)
    assert ctx.db.ensure_user_staging_folder(one["id"]) == folder_one, "folder must be stable"


def main():
    ctx = E2ETestContext(prefix="mangatrax_multiuser_iso_")
    try:
        red = _user(ctx, "oidc|red", "red", admin=True)
        blue = _user(ctx, "oidc|blue", "blue")
        test_shared_vs_private_listing(ctx, red, blue)
        test_per_user_monitor_toggle(ctx, red, blue)
        test_per_user_blacklist_and_groups(ctx, red, blue)
        test_request_approval_flow(ctx, red, blue)
        test_approved_request_downloads_on_click(ctx, red, blue)
        test_download_ui_handles_pending(ctx)
        test_approval_auto_download(ctx, red, blue)
        test_bypass_user_downloads_to_own_staging(ctx, blue)
        test_whoami_reports_groups_and_role(ctx)
        test_require_admin_gate(ctx, red, blue)
        test_admin_surface_source_guards(ctx)
        test_staging_dir_scoping(ctx, red, blue)
        test_per_user_routing(ctx, blue)
        test_complete_expansion_stops_requerying(ctx)
        test_torbox_force_whole_override(ctx, red)
        test_unresolved_series_queue_and_manual_link(ctx, red)
        test_anilist_negative_volume_cache(ctx)
        test_staging_groups_split_by_owner(ctx)
        test_admin_owner_bucket_merges_own_folder(ctx, red)
        test_retag_is_scoped_to_owner(ctx)
        test_staging_folders(ctx)
        print("multi-user isolation suite PASSED")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
