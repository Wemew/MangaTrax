#!/usr/bin/env python3
"""task-18 suite: configurable Nyaa base URL (mirror/proxy), feed enable/disable,
host-agnostic cleanup and the seeded-feed migration.

User context: nyaa.si is DNS-blocked on their network; they run a passthrough mirror
(e.g. https://wemew-nyaa.vercel.app/).  Every Nyaa URL must come from the configured
base -- crawl pages used by backfill, view URLs, .torrent URLs and relative-href
joins -- and the built-in seeded feed must follow the mirror.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import sys
import tempfile
import shutil
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.covers import CoverCache  # noqa: E402
from app.database import (  # noqa: E402
    MangaDatabase,
    SEEDED_NYAA_FEED_NAME,
    SEEDED_NYAA_FEED_URL,
)
from app import nyaa_source  # noqa: E402
from app.nyaa_source import (  # noqa: E402
    DEFAULT_NYAA_BASE_URL,
    NyaaBaseURLError,
    normalize_nyaa_base_url,
    resolve_nyaa_base_url,
)
from app.providers.nyaa import NyaaMangaProvider  # noqa: E402
from app.providers.registry import create_provider  # noqa: E402
from app.scheduler import MangaScheduler  # noqa: E402
from app import schemas  # noqa: E402

MIRROR = "https://wemew-nyaa.vercel.app"

ROW_HTML = """
<html><body>
  <table class="torrent-list"><tbody>
    <tr>
      <td>Category</td>
      <td><a href="/view/101">Solo Leveling Vol. 01</a></td>
      <td><a href="/download/101.torrent">dl</a></td>
      <td>500 MiB</td>
      <td>2026-10-31</td>
      <td>10</td>
    </tr>
  </tbody></table>
</body></html>
"""


class _DummyResponse:
    status = 200

    def __init__(self, body=ROW_HTML):
        self._body = body

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ===========================================================================
# 1. Normalisation rules
# ===========================================================================
def test_normalisation():
    print("--> Testing Nyaa base URL normalisation rules...")
    cases = [
        (DEFAULT_NYAA_BASE_URL, DEFAULT_NYAA_BASE_URL),
        (MIRROR, MIRROR),
        (MIRROR + "/", MIRROR),
        (MIRROR + "/?page=rss&c=3_1&f=0", MIRROR),
        (MIRROR + "/?page=rss&c=3_1&f=0#frag", MIRROR),
        ("https://mirror.example/nyaa", "https://mirror.example/nyaa"),
        ("https://mirror.example/nyaa/", "https://mirror.example/nyaa"),
        ("https://mirror.example/nyaa/?page=rss", "https://mirror.example/nyaa"),
        ("HTTPS://Mirror.Example/Path", "https://mirror.example/Path"),
        ("http://mirror.example:8080/", "http://mirror.example:8080"),
        ("https://mirror.example:443/x", "https://mirror.example/x"),
        ("  https://mirror.example  ", "https://mirror.example"),
    ]
    for raw, expected in cases:
        assert normalize_nyaa_base_url(raw) == expected, (raw, normalize_nyaa_base_url(raw))

    rejected = [
        "", "   ", None,
        "ftp://mirror.example",
        "mirror.example",
        "https://",
        "https://user:pass@mirror.example",
        "http://127.0.0.1:8080",
        "http://localhost",
        "https://169.254.169.254/",
        "http://intranet",
        "https://mirror.example/../etc",
        "https://mirror.example:notaport",
    ]
    for raw in rejected:
        try:
            normalize_nyaa_base_url(raw)
        except NyaaBaseURLError:
            continue
        else:
            assert False, f"base URL must be rejected: {raw!r}"
    print("    [PASSED] bare/full/path/port forms; credentials, private hosts, junk rejected.")


# ===========================================================================
# 2. Resolution precedence: feed config -> setting -> default
# ===========================================================================
def test_resolution_precedence():
    print("--> Testing Nyaa base URL resolution precedence...")
    assert resolve_nyaa_base_url({}, None) == DEFAULT_NYAA_BASE_URL
    assert resolve_nyaa_base_url({}, MIRROR) == MIRROR
    assert resolve_nyaa_base_url({"base_url": "https://feed-mirror.example"}, MIRROR) == "https://feed-mirror.example"
    # Invalid feed config falls back to the setting.
    assert resolve_nyaa_base_url({"base_url": "ftp://bad"}, MIRROR) == MIRROR
    # Invalid setting falls back to the default.
    assert resolve_nyaa_base_url({}, "not-a-url") == DEFAULT_NYAA_BASE_URL
    print("    [PASSED] config beats setting beats default; invalid values fall back.")


# ===========================================================================
# 3. URL construction uses the mirror for pages, view and torrent URLs
# ===========================================================================
def _capture_crawl(base_url):
    provider = NyaaMangaProvider(
        name="mirror", config={"base_url": base_url, "delay_seconds": 0, "max_pages": 1},
    )
    requested = []

    def fake_urlopen(request, timeout=None):
        requested.append(request.full_url if hasattr(request, "full_url") else str(request))
        return _DummyResponse()

    with patch("app.providers.nyaa.urllib.request.urlopen", side_effect=fake_urlopen):
        releases = provider.fetch_releases()
    return requested, releases


def test_url_construction():
    print("--> Testing every Nyaa URL uses the configured mirror...")
    requested, releases = _capture_crawl(MIRROR)
    assert len(releases) == 1, releases
    release = releases[0]
    assert requested and requested[0].startswith(MIRROR + "/?"), requested
    assert "c=3_1" in requested[0] and "p=1" in requested[0], requested[0]
    assert release.link == MIRROR + "/view/101", release.link
    assert release.download_url == MIRROR + "/download/101.torrent", release.download_url
    assert "nyaa.si" not in release.link and "nyaa.si" not in release.download_url

    # Default (no config, no setting) stays nyaa.si for backwards compatibility.
    requested_default, _ = _capture_crawl(DEFAULT_NYAA_BASE_URL)
    assert requested_default[0].startswith(DEFAULT_NYAA_BASE_URL + "/?"), requested_default
    assert NyaaMangaProvider(name="default", config={"delay_seconds": 0}).base_url == DEFAULT_NYAA_BASE_URL

    # Path-prefixed mirror keeps the prefix for pages, relative view and torrent joins.
    prefixed = "https://mirror.example/nyaa"
    requested_path, releases_path = _capture_crawl(prefixed)
    assert requested_path[0].startswith(prefixed + "/?"), requested_path
    assert releases_path[0].link == prefixed + "/view/101", releases_path[0].link
    assert releases_path[0].download_url == prefixed + "/download/101.torrent", releases_path[0].download_url
    print("    [PASSED] crawl/view/torrent URLs are mirror-hosted (path prefix preserved).")


# ===========================================================================
# 4. Registry injection and backfill provider base
# ===========================================================================
def test_registry_and_backfill_base():
    print("--> Testing registry injection + backfill provider base...")
    feed = {"name": "Nyaa", "feed_type": "nyaa", "url": "https://nyaa.si/", "config_json": "{}"}
    provider = create_provider(feed, nyaa_base_url=MIRROR)
    assert provider is not None and provider.base_url == MIRROR, getattr(provider, "base_url", None)

    # A per-feed config overrides the injected setting.
    per_feed = dict(feed)
    per_feed["config_json"] = json.dumps({"base_url": "https://feed-mirror.example"})
    provider_feed = create_provider(per_feed, nyaa_base_url=MIRROR)
    assert provider_feed.base_url == "https://feed-mirror.example", provider_feed.base_url

    # Scheduler helper resolves the setting, and both backfill provider
    # constructions pass it through (source guard for the backfill paths).
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_nyaa_src_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        db.set_setting("nyaa_base_url", MIRROR + "/?page=rss&c=3_1&f=0")
        scheduler = MangaScheduler(db, CoverCache(tmpdir))
        assert scheduler._nyaa_base_url() == MIRROR, scheduler._nyaa_base_url()
        db.set_setting("nyaa_base_url", "not a url")
        assert scheduler._nyaa_base_url() == DEFAULT_NYAA_BASE_URL
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    scheduler_source = open(os.path.join(BACKEND_DIR, "app", "scheduler.py"), encoding="utf-8").read()
    assert 'name="Nyaa Manga Backfill"' in scheduler_source
    assert "base_url" in scheduler_source
    assert "self._nyaa_base_url()" in scheduler_source
    assert "create_provider(feed, nyaa_base_url=self._nyaa_base_url())" in scheduler_source
    print("    [PASSED] registry injects the mirror; backfill constructions use the resolved base.")


# ===========================================================================
# 5. Seeded default feed migration (idempotent, built-in row only)
# ===========================================================================
def _feed_by_name(db, name):
    return next((feed for feed in db.get_all_feeds() if feed["name"] == name), None)


def test_seeded_feed_migration():
    print("--> Testing seeded-feed mirror migration...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_nyaa_migrate_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        seeded = _feed_by_name(db, SEEDED_NYAA_FEED_NAME)
        assert seeded is not None, [f["name"] for f in db.get_all_feeds()]
        assert seeded["url"] == SEEDED_NYAA_FEED_URL, seeded["url"]
        # Default setting: no rewrite.
        assert db.migrate_seeded_nyaa_feed() == 0
        assert _feed_by_name(db, SEEDED_NYAA_FEED_NAME)["url"] == SEEDED_NYAA_FEED_URL

        # User-created feeds must never be touched, even with the stock URL.
        user_feed_id = db.add_feed("My Custom Feed", "rss", "https://example.com/custom.xml")
        user_stock_id = db.add_feed("Copy", "rss", SEEDED_NYAA_FEED_URL)

        # Configure a mirror and migrate.
        db.set_setting("nyaa_base_url", MIRROR)
        migrated = db.migrate_seeded_nyaa_feed()
        assert migrated == 1, migrated
        expected = MIRROR + "/?page=rss&q=manga+cbz&c=3_1&f=0"
        assert _feed_by_name(db, SEEDED_NYAA_FEED_NAME)["url"] == expected, _feed_by_name(db, SEEDED_NYAA_FEED_NAME)
        # Idempotent: second run is a no-op.
        assert db.migrate_seeded_nyaa_feed() == 0
        assert _feed_by_name(db, SEEDED_NYAA_FEED_NAME)["url"] == expected
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT url FROM feeds WHERE id = ?;", (user_feed_id,))
            assert cur.fetchone()["url"] == "https://example.com/custom.xml"
            cur.execute("SELECT url FROM feeds WHERE id = ?;", (user_stock_id,))
            assert cur.fetchone()["url"] == SEEDED_NYAA_FEED_URL

        # Startup init_db also applies it (setting saved before restart).
        db2 = MangaDatabase(os.path.join(tmpdir, "mangatrax2.db"))
        db2.set_setting("nyaa_base_url", "https://startup-mirror.example")
        db2.init_db()
        startup_feed = _feed_by_name(db2, SEEDED_NYAA_FEED_NAME)
        assert startup_feed["url"].startswith("https://startup-mirror.example/?"), startup_feed["url"]
        assert "page=rss" in startup_feed["url"] and "c=3_1" in startup_feed["url"], startup_feed["url"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("    [PASSED] seeded row repointed with query preserved; idempotent; user feeds untouched.")


# ===========================================================================
# 6. Feed enable/disable endpoint + scheduler skip
# ===========================================================================
def test_feed_toggle():
    print("--> Testing feed enable/disable endpoint and scheduler skip...")
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_nyaa_toggle_")
    try:
        # Use the seeded feed (deterministic row).
        seeded = _feed_by_name(ctx.db, SEEDED_NYAA_FEED_NAME)
        feed_id = seeded["id"]
        response = ctx.client.post(f"/api/feeds/{feed_id}/toggle", json_data={"enabled": False})
        assert response.status_code == 200, (response.status_code, response.json())
        assert response.json()["enabled"] is False, response.json()
        assert _feed_by_name(ctx.db, SEEDED_NYAA_FEED_NAME)["enabled"] == 0

        calls = {"count": 0}

        class _CountingProvider:
            def fetch_releases(self):
                calls["count"] += 1
                return []

        with patch("app.scheduler.create_provider", return_value=_CountingProvider()):
            ctx.scheduler.check_all_feeds()
        assert calls["count"] == 0, "scheduler must skip disabled feeds"

        response_on = ctx.client.post(f"/api/feeds/{feed_id}/toggle", json_data={"enabled": True})
        assert response_on.status_code == 200, response_on.json()
        assert _feed_by_name(ctx.db, SEEDED_NYAA_FEED_NAME)["enabled"] == 1
        with patch("app.scheduler.create_provider", return_value=_CountingProvider()):
            ctx.scheduler.check_all_feeds()
        assert calls["count"] == 1, "enabled feed must be polled"

        # Schema: unknown keys rejected; unknown feed id -> 404.
        bad = ctx.client.post(f"/api/feeds/{feed_id}/toggle", json_data={"enabled": True, "extra": 1})
        assert bad.status_code == 400, (bad.status_code, bad.json())
        missing = ctx.client.post("/api/feeds/99999/toggle", json_data={"enabled": True})
        assert missing.status_code == 404, (missing.status_code, missing.json())
        # Audit rows exist for enable/disable.
        events = {row["event_type"] for row in ctx.db.get_logs(limit=100)}
        assert "FEED_DISABLED" in events and "FEED_ENABLED" in events, events
    finally:
        ctx.close()
    print("    [PASSED] toggle persists, scheduler skips disabled feeds, audited, 400/404 guarded.")


# ===========================================================================
# 7. Host-agnostic cleanup query
# ===========================================================================
def test_cleanup_is_host_agnostic():
    print("--> Testing host-agnostic torrent-link cleanup...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_nyaa_cleanup_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO releases (guid, title, series_name, link) VALUES (?, ?, ?, ?);",
                ("mirror-guid", "Mirror Release v01", "Mirror Release",
                 "https://wemew-nyaa.vercel.app/download/123.torrent"),
            )
            conn.commit()
        db.init_db()
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT link FROM releases WHERE guid = 'mirror-guid';")
            assert cur.fetchone()["link"] == "https://wemew-nyaa.vercel.app/view/123"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("    [PASSED] mirror-hosted .torrent links are rewritten to view links too.")


# ===========================================================================
# 8. Settings schema + UI/docs surface
# ===========================================================================
def test_settings_and_docs():
    print("--> Testing settings schema and UI/docs surface...")
    assert "nyaa_base_url" in schemas.SETTINGS_KEYS
    model = schemas.validate(
        schemas.SettingsUpdateRequest,
        {"nyaa_base_url": MIRROR + "/?page=rss&c=3_1&f=0"},
    )
    assert model.nyaa_base_url == MIRROR, model.nyaa_base_url
    for bad in ("ftp://mirror.example", "http://127.0.0.1", "https://user:pass@mirror.example"):
        try:
            schemas.validate(schemas.SettingsUpdateRequest, {"nyaa_base_url": bad})
        except schemas.ValidationError:
            continue
        else:
            assert False, f"settings validator must reject: {bad}"

    settings_html = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    assert 'id="setting-nyaa-base-url"' in settings_html
    assert "Nyaa Base URL" in settings_html
    assert "?page=rss" in settings_html and "?p=N" in settings_html
    assert "nyaa_base_url: document.getElementById('setting-nyaa-base-url')" in settings_html
    assert 'id="section-tracker"' in settings_html

    feeds_html = open(os.path.join(BACKEND_DIR, "app", "templates", "feeds.html"), encoding="utf-8").read()
    assert "toggleFeed(" in feeds_html and "/toggle" in feeds_html
    assert "aria-label=\"Disable feed" in feeds_html and "aria-label=\"Enable feed" in feeds_html

    readme = open(os.path.join(BASE_DIR, "README.md"), encoding="utf-8").read()
    assert "Nyaa mirror / proxy" in readme
    assert "nyaa_base_url" in readme and "MANGATRAX_NYAA_BASE_URL" in readme
    assert "/download/<id>.torrent" in readme

    # End-to-end: POST /api/settings normalises the value and repoints the seeded feed.
    from e2e.harness import E2ETestContext

    ctx = E2ETestContext(prefix="mangatrax_nyaa_settings_")
    try:
        before = _feed_by_name(ctx.db, SEEDED_NYAA_FEED_NAME)
        assert before["url"] == SEEDED_NYAA_FEED_URL, before["url"]
        response = ctx.client.post(
            "/api/settings",
            json_data={"nyaa_base_url": MIRROR + "/?page=rss&c=3_1&f=0"},
        )
        assert response.status_code == 200, (response.status_code, response.json())
        assert ctx.db.get_setting("nyaa_base_url") == MIRROR, ctx.db.get_setting("nyaa_base_url")
        after = _feed_by_name(ctx.db, SEEDED_NYAA_FEED_NAME)
        assert after["url"] == MIRROR + "/?page=rss&q=manga+cbz&c=3_1&f=0", after["url"]
        events = {row["event_type"] for row in ctx.db.get_logs(limit=100)}
        assert "NYAA_BASE_UPDATED" in events, events
    finally:
        ctx.close()
    print("    [PASSED] setting validated/normalised, UI fields + toggle present, README documents mirror.")


def main():
    print("=" * 78)
    print("Running Nyaa mirror/proxy suite (task-18)")
    print("=" * 78)
    tests = [
        test_normalisation,
        test_resolution_precedence,
        test_url_construction,
        test_registry_and_backfill_base,
        test_seeded_feed_migration,
        test_feed_toggle,
        test_cleanup_is_host_agnostic,
        test_settings_and_docs,
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
        print(f"NYAA-MIRROR SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL NYAA-MIRROR TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
