#!/usr/bin/env python3
"""M3 security remediation suite (PROJECT.md features 18-24).

Covers:
  * SSRF guard: scheme allow-list, loopback/RFC1918/link-local/metadata/
    multicast/reserved ranges, DNS re-check, fail-closed resolution errors,
    intranet single-label names - plus application at the outbound boundaries
    (cover fetch wrapper, webhook transport, RSS/HTML feed providers)
  * path traversal / probe containment for /api/calibre/test and directory
    settings (``..``, NUL, relative paths, symlink escapes)
  * stored-XSS defense: website_url validation + ``safe_url`` filter applied to
    every dynamic href
  * Pydantic schema rejection of unknown keys / invalid values on mutating routes
  * SQL LIKE wildcard escaping (search terms match literally)
  * /api/debug/action opt-in gating and explicit outbound timeouts

Run through the sanctioned runner (assertions compiled live):
    calibre-debug.exe -e "tests\\run_tests.py"
"""

import os
import socket
import sys
import tempfile
import traceback
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import schemas, security
from app.database import MangaDatabase
from app.notifier.webhook import NotificationService, _post_json
from app.providers.html_scraper import HtmlScraperProvider
from app.providers.rss import RSSFeedProvider
from e2e.harness import E2ETestContext, main_app


def _assert_raises_unsafe(callable_, label):
    try:
        callable_()
    except security.UnsafeURLError:
        return
    raise AssertionError(f"expected UnsafeURLError for {label}")


def _patch_dns(addresses=None, error=None):
    if error is not None:
        return patch("app.security.socket.getaddrinfo", side_effect=error)
    infos = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)) for address in addresses]
    return patch("app.security.socket.getaddrinfo", return_value=infos)


# ---------------------------------------------------------------------------
# 1. SSRF: schemes, literal ranges, DNS re-check
# ---------------------------------------------------------------------------
def test_ssrf_scheme_and_host_guards():
    print("--> Testing SSRF scheme / host rejection...")
    for url in (
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "gopher://example.com/",
        "ws://example.com/socket",
        "http://",
        "http:///nohost",
        "not-a-url",
        "https://user:pass@example.com/",
        "https://exa mple.com/",
        "https://example.com/\x00",
    ):
        _assert_raises_unsafe(lambda u=url: security.validate_public_url(u), url)

    # Literal private / loopback / metadata / multicast / reserved addresses.
    for host in (
        "127.0.0.1", "127.10.20.30", "0.0.0.0", "10.0.0.5", "172.16.9.9",
        "192.168.1.1", "169.254.169.254", "169.254.1.1", "100.64.0.1",
        "192.0.2.10", "198.18.0.1", "224.0.0.1", "255.255.255.255",
        "localhost", "metadata.google.internal", "intranet", "qbittorrent",
    ):
        _assert_raises_unsafe(lambda h=host: security.validate_public_url(f"http://{h}/x"), host)

    for host in ("[::1]", "[fc00::1]", "[fe80::1]", "[ff02::1]", "[::]"):
        _assert_raises_unsafe(lambda h=host: security.validate_public_url(f"http://{h}/x"), host)

    # DNS re-check: a public-looking hostname resolving to a private address is
    # rejected (DNS rebinding / hostile authoritative records).
    for internal in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "fc00::1"):
        with _patch_dns([internal]):
            _assert_raises_unsafe(
                lambda: security.validate_public_url("https://cdn.example.com/cover.jpg"),
                f"DNS->{internal}",
            )

    # Genuinely public resolution is accepted.
    with _patch_dns(["93.184.216.34"]):
        assert security.validate_public_url("https://cdn.example.com/cover.jpg") == "https://cdn.example.com/cover.jpg"

    # Resolution failure fails closed.
    with patch("app.security.socket.getaddrinfo", side_effect=socket.gaierror("no dns")):
        _assert_raises_unsafe(lambda: security.validate_public_url("https://nxdomain.example/x"), "nxdomain")

    # Structural variant performs no DNS but keeps the literal-host rules.
    with patch("app.security.socket.getaddrinfo", side_effect=AssertionError("DNS must not be called")):
        assert security.validate_outbound_url("https://nyaa.si/?page=rss") == "https://nyaa.si/?page=rss"
        assert security.validate_outbound_url("https://notify.home.arpa/webhook")
        _assert_raises_unsafe(lambda: security.validate_outbound_url("http://127.0.0.1/x"), "structural loopback")

    print("    [PASSED] SSRF guard rejects non-http(s), private ranges and rebinding DNS.")


def test_outbound_boundaries_are_guarded():
    print("--> Testing SSRF guard at cover / webhook / feed boundaries...")
    from app.covers import is_safe_public_url

    with _patch_dns(["127.0.0.1"]):
        assert is_safe_public_url("https://rebind.example/cover.jpg") is False
    with _patch_dns(["93.184.216.34"]):
        assert is_safe_public_url("https://cdn.example.com/cover.jpg") is True
    assert is_safe_public_url("javascript:alert(1)") is False
    assert is_safe_public_url("http://169.254.169.254/latest/meta-data/") is False

    # Webhook transport refuses unsafe targets before opening any socket.
    _assert_raises_unsafe(lambda: _post_json("http://127.0.0.1:8080/hook", {"x": 1}), "webhook loopback")
    _assert_raises_unsafe(lambda: _post_json("file:///etc/passwd", {"x": 1}), "webhook file scheme")

    # Webhook test endpoint result reports the rejection instead of raising.
    result = NotificationService(discord_webhook="file:///etc/passwd").test_webhook()
    assert result["status"] == "error", result
    assert result["results"]["discord"]["success"] is False

    # Feed providers refuse unsafe URLs and never touch the network.
    with patch("urllib.request.urlopen", side_effect=AssertionError("must not fetch")):
        assert RSSFeedProvider(name="blocked", url="http://127.0.0.1/feed").fetch_releases() == []
        assert HtmlScraperProvider(name="blocked", url="http://169.254.169.254/latest").fetch_releases() == []

    print("    [PASSED] cover fetch, webhook dispatch and feed fetches are SSRF-guarded.")


def test_cover_redirect_hop_ssrf_validation():
    """Every redirect hop of a cover download must be SSRF-validated before it is fetched.

    The cover cache used to rely on ``httpx`` with ``follow_redirects=False`` and a
    manual redirect loop; the dependency was removed in M4 in favour of
    ``urllib.request`` (handled manually, also without auto-following).  This test
    pins the security-critical behaviour of that loop so the stdlib transport can
    never silently start following redirects or drop a hop check.
    """
    print("--> Testing cover download redirect-hop SSRF validation (stdlib transport)...")
    import asyncio
    import urllib.error

    from app import covers
    from app.covers import CoverCache

    tmpdir = tempfile.mkdtemp(prefix="mangatrax_cover_redirect_")
    try:
        cache = CoverCache(tmpdir)
        png_body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

        class _FakeImageResponse:
            status = 200

            def __init__(self, body, content_length=None, send_length=True):
                self._body = body
                self._pos = 0
                self.headers = {}
                if send_length:
                    self.headers["Content-Length"] = str(len(body) if content_length is None else content_length)

            def read(self, size=-1):
                if size is None or size < 0:
                    chunk, self._pos = self._body[self._pos:], len(self._body)
                    return chunk
                chunk = self._body[self._pos:self._pos + size]
                self._pos += len(chunk)
                return chunk

            def close(self):
                pass

            def getcode(self):
                return self.status

        def _redirect(url, location, code=302):
            return urllib.error.HTTPError(url, code, "Found", {"Location": location}, None)

        # 1. A redirect to an internal/metadata address is never fetched.
        fetched = []
        timeouts = []

        def _unsafe_hop(req, timeout):
            fetched.append(req.full_url)
            timeouts.append(timeout)
            raise _redirect(req.full_url, "http://169.254.169.254/latest/meta-data/")

        with _patch_dns(["93.184.216.34"]), patch.object(covers, "_urlopen", side_effect=_unsafe_hop):
            assert cache.cache_sync("https://cdn.example.com/start.jpg") is None
        assert fetched == ["https://cdn.example.com/start.jpg"], fetched
        assert os.listdir(cache.covers_dir) == [], os.listdir(cache.covers_dir)

        # 2. A legitimate redirect chain is followed hop by hop and cached.
        fetched.clear()
        timeouts.clear()

        def _safe_chain(req, timeout):
            fetched.append(req.full_url)
            timeouts.append(timeout)
            if req.full_url == "https://cdn.example.com/start.jpg":
                raise _redirect(req.full_url, "/final.png", code=301)
            if req.full_url == "https://cdn.example.com/final.png":
                return _FakeImageResponse(png_body)
            raise AssertionError(f"unexpected hop fetched: {req.full_url}")

        with _patch_dns(["93.184.216.34"]), patch.object(covers, "_urlopen", side_effect=_safe_chain):
            cached_path = cache.cache_sync("https://cdn.example.com/start.jpg")
        assert cached_path and os.path.isfile(cached_path), cached_path
        assert fetched == [
            "https://cdn.example.com/start.jpg",
            "https://cdn.example.com/final.png",
        ], fetched
        # Explicit timeout on every hop (the httpx timeout this replaced).
        assert timeouts == [covers.DOWNLOAD_TIMEOUT_SECONDS] * 2, timeouts

        # 3. The async API delegates to the same validated download routine.
        import shutil
        shutil.rmtree(cache.covers_dir)
        os.makedirs(cache.covers_dir)

        def _ok_hop(req, timeout):
            fetched.append(req.full_url)
            return _FakeImageResponse(png_body)

        with _patch_dns(["93.184.216.34"]), patch.object(covers, "_urlopen", side_effect=_ok_hop):
            async_path = asyncio.run(cache.get_or_download("https://cdn.example.com/async.png"))
        assert async_path and os.path.isfile(async_path), async_path

        # 4. Redirect loops are capped (MAX_REDIRECTS hops, then give up).
        loop_fetches = []

        def _loop_hop(req, timeout):
            loop_fetches.append(req.full_url)
            raise _redirect(req.full_url, req.full_url)

        with _patch_dns(["93.184.216.34"]), patch.object(covers, "_urlopen", side_effect=_loop_hop):
            assert cache.cache_sync("https://cdn.example.com/loop.jpg") is None
        assert len(loop_fetches) == covers.MAX_REDIRECTS + 1, loop_fetches

        # 5. Streamed size cap still aborts the download (no Content-Length header, so
        #    the cap must be enforced while reading the body) and cleans the temp file.
        def _huge_hop(req, timeout):
            return _FakeImageResponse(png_body + b"\x00" * 4096, send_length=False)

        with _patch_dns(["93.184.216.34"]), \
                patch.object(covers, "MAX_COVER_BYTES", 1024), \
                patch.object(covers, "_urlopen", side_effect=_huge_hop):
            assert cache.cache_sync("https://cdn.example.com/huge.jpg") is None
        assert os.listdir(cache.covers_dir) == [], os.listdir(cache.covers_dir)

        # 6. Oversized Content-Length is rejected before streaming.
        def _oversized_hop(req, timeout):
            return _FakeImageResponse(png_body, content_length=10 * 1024 * 1024)

        with _patch_dns(["93.184.216.34"]), patch.object(covers, "_urlopen", side_effect=_oversized_hop):
            assert cache.cache_sync("https://cdn.example.com/oversized.jpg") is None

        # 7. Non-image payloads are still rejected by magic-byte validation.
        def _html_hop(req, timeout):
            return _FakeImageResponse(b"<html>not an image</html>")

        with _patch_dns(["93.184.216.34"]), patch.object(covers, "_urlopen", side_effect=_html_hop):
            assert cache.cache_sync("https://cdn.example.com/page.html") is None
        assert os.listdir(cache.covers_dir) == [], os.listdir(cache.covers_dir)

        print("    [PASSED] cover redirect hops are SSRF-validated, capped and size-limited.")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_stored_xss_defenses():
    print("--> Testing stored-XSS defenses (href schemes + safe_url filter)...")
    safe = [
        "http://example.com", "https://example.com/path?q=1#frag",
        "https://www.mangaupdates.com/series/abc",
    ]
    unsafe = [
        "javascript:alert(1)", "JavaScript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd", "vbscript:msgbox(1)", "mailto:a@b.c", "//evil.example/x",
        "/relative/path", " https://example.com", "https://example.com\x00",
    ]
    for url in safe:
        assert security.is_safe_href(url) is True, url
    for url in unsafe:
        assert security.is_safe_href(url) is False, url
    assert security.is_safe_href("") is False
    assert security.is_safe_href(None) is False

    assert security.safe_url_filter("https://example.com") == "https://example.com"
    assert security.safe_url_filter("javascript:alert(1)") == "#"
    assert security.safe_url_filter("javascript:alert(1)", fallback="") == ""
    assert security.safe_url_filter(None) == "#"

    # Persisting a website_url validates the scheme; empty clears it.
    assert security.validate_stored_website_url("") == ""
    assert security.validate_stored_website_url("  ") == ""
    assert security.validate_stored_website_url("https://example.com/x") == "https://example.com/x"
    for url in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd"):
        try:
            security.validate_stored_website_url(url)
            raise AssertionError(f"unsafe website_url accepted: {url}")
        except security.UnsafeURLError:
            pass

    # Schema-level validation rejects it before the DB layer.
    try:
        schemas.validate(schemas.SeriesLinkUpdateRequest, {"website_url": "javascript:alert(1)"})
        raise AssertionError("schema accepted a javascript: website_url")
    except schemas.ValidationError:
        pass

    # Every dynamic external href in the templates goes through safe_url.
    templates_dir = os.path.join(BACKEND_DIR, "app", "templates")
    dynamic_href_fragments = ["href=\"{{ r.link }}\"", "href=\"{{ item.link }}\"", "href=\"{{ s.website_url }}\""]
    for filename in (
        os.path.join(templates_dir, "discovery.html"),
        os.path.join(templates_dir, "partials", "flat_release_row_chunk.html"),
        os.path.join(templates_dir, "partials", "series_card_chunk.html"),
        os.path.join(templates_dir, "partials", "series_table_row_chunk.html"),
    ):
        with open(filename, "r", encoding="utf-8") as handle:
            content = handle.read()
        assert "|safe_url" in content, f"{filename} does not use the safe_url filter"
        for fragment in dynamic_href_fragments:
            assert fragment not in content, f"{filename} still renders an unvalidated href: {fragment}"

    print("    [PASSED] javascript:/data:/file: URLs cannot be stored or rendered as links.")


# ---------------------------------------------------------------------------
# 3. Path traversal / containment
# ---------------------------------------------------------------------------
def test_path_traversal_defenses():
    print("--> Testing path containment (...'..', symlinks, absolute roots)...")
    root = tempfile.mkdtemp(prefix="mangatrax_paths_")
    outside = tempfile.mkdtemp(prefix="mangatrax_outside_")
    try:
        lib = os.path.join(root, "calibre")
        nested = os.path.join(lib, "sub", "dir")
        os.makedirs(nested)

        assert security.ensure_path_within_roots(lib, [root]) == os.path.realpath(lib)
        assert security.ensure_path_within_roots(nested, [root]) == os.path.realpath(nested)
        assert security.ensure_path_within_roots(root, [root]) == os.path.realpath(root)

        for bad in (
            os.path.join(root, "..", "escape"),
            root + os.sep + ".." + os.sep + "etc",
            "../relative",
            "relative/path",
            "calibre\x00/injected",
            outside,
        ):
            try:
                security.ensure_path_within_roots(bad, [root])
                raise AssertionError(f"unsafe path accepted: {bad!r}")
            except security.UnsafePathError:
                pass

        # Symlink escape: a link inside the root pointing outside must resolve
        # outside the root and therefore be rejected.
        link = os.path.join(lib, "escape-link")
        try:
            os.symlink(outside, link)
            symlink_created = True
        except (OSError, NotImplementedError):
            symlink_created = False
        if symlink_created:
            try:
                security.ensure_path_within_roots(os.path.join(link, "secret"), [root])
                raise AssertionError("symlink escape was accepted")
            except security.UnsafePathError:
                pass

        # Directory settings: traversal/NUL/relative rejected, absolute accepted.
        assert security.validate_directory_setting(lib, setting_name="calibre_path") == os.path.realpath(lib)
        for bad in ("../staging", "staging", "x\x00y"):
            try:
                security.validate_directory_setting(bad, setting_name="staging_dir")
                raise AssertionError(f"unsafe directory setting accepted: {bad!r}")
            except security.UnsafePathError:
                pass
        file_path = os.path.join(root, "not-a-dir.txt")
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("x")
        try:
            security.validate_directory_setting(file_path, setting_name="calibre_path")
            raise AssertionError("file path accepted as a directory setting")
        except security.UnsafePathError:
            pass

        print("    [PASSED] path traversal, NUL bytes, relative paths and symlink escapes are blocked.")
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. SQL hardening
# ---------------------------------------------------------------------------
def test_sql_like_escaping():
    print("--> Testing SQL LIKE wildcard escaping / parameterised LIMIT...")
    temp_dir = tempfile.mkdtemp(prefix="mangatrax_sql_")
    try:
        db = MangaDatabase(os.path.join(temp_dir, "test.db"))
        db.sync_calibre_series([
            {"series_id": 1, "series_name": "100% Manga", "book_count": 1, "latest_index": 1.0},
            {"series_id": 2, "series_name": "100X Manga", "book_count": 1, "latest_index": 1.0},
            {"series_id": 3, "series_name": "Under_score", "book_count": 1, "latest_index": 1.0},
            {"series_id": 4, "series_name": "UnderXscore", "book_count": 1, "latest_index": 1.0},
        ])

        percent_matches = [row["name"] for row in db.get_all_series(search="%")]
        assert percent_matches == ["100% Manga"], percent_matches

        underscore_matches = [row["name"] for row in db.get_all_series(search="_")]
        assert underscore_matches == ["Under_score"], underscore_matches

        # Ordinary searches and pagination still work (LIMIT/OFFSET are bound).
        assert [row["name"] for row in db.get_all_series(search="100")] == ["100% Manga", "100X Manga"]
        assert len(db.get_all_series(limit=2, offset=0)) == 2
        assert len(db.get_all_series(limit=2, offset=2)) == 2
        assert db.count_all_series(search="%") == 1

        assert security.escape_like("100%_\\") == "100\\%\\_\\\\"
        assert security.like_contains("A%B") == "%A\\%B%"
        print("    [PASSED] LIKE wildcards are escaped and LIMIT/OFFSET are parameterised.")
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Pydantic schema rejection
# ---------------------------------------------------------------------------
def test_schema_validation():
    print("--> Testing Pydantic schema validation on mutating payloads...")

    def expect_invalid(model, payload, label):
        try:
            schemas.validate(model, payload)
            raise AssertionError(f"invalid payload accepted: {label} -> {payload}")
        except schemas.ValidationError:
            return

    # Unknown keys are rejected on every mutating model.
    expect_invalid(schemas.FeedCreateRequest,
                   {"name": "f", "url": "https://nyaa.si/rss", "evil": "x"}, "feed unknown key")
    expect_invalid(schemas.FeedCreateRequest,
                   {"name": "f", "url": "https://nyaa.si/rss", "feed_type": "rss", "interval": 1}, "interval < 5")
    expect_invalid(schemas.FeedCreateRequest,
                   {"name": "f", "url": "https://nyaa.si/rss", "feed_type": "bogus"}, "bad feed type")
    expect_invalid(schemas.FeedCreateRequest, {"name": "", "url": "https://nyaa.si/rss"}, "blank name")
    expect_invalid(schemas.FeedCreateRequest, {"name": "f", "url": ""}, "blank url")
    expect_invalid(schemas.FeedCreateRequest, {"name": "f", "url": "javascript:alert(1)"}, "js feed url")
    expect_invalid(schemas.FeedTestRequest, {"url": "http://127.0.0.1/feed"}, "loopback feed test url")
    expect_invalid(schemas.ReleaseStatusUpdateRequest, {"status": "HACKED"}, "invalid status")
    expect_invalid(schemas.ReleaseStatusUpdateRequest, {}, "missing status")
    expect_invalid(schemas.SeriesLinkUpdateRequest, {"website_url": "javascript:alert(1)"}, "js website")
    expect_invalid(schemas.SeriesLinkUpdateRequest, {"website_url": "https://ok.example", "x": 1}, "link unknown key")
    expect_invalid(schemas.SettingsUpdateRequest, {"unknown_setting": "x"}, "unknown setting")
    expect_invalid(schemas.SettingsUpdateRequest, {"staging_dir": "../escape"}, "traversal staging dir")
    expect_invalid(schemas.DebugActionRequest, {"action": "drop-tables"}, "unknown debug action")
    expect_invalid(schemas.WebhookTestRequest, {"discord_webhook_url": "file:///etc/passwd"}, "file webhook")
    expect_invalid(schemas.BackfillStartRequest, {"max_pages": -1}, "negative max pages")
    expect_invalid(schemas.BackfillStartRequest, {"start_page": 0}, "start page 0")
    expect_invalid(schemas.DiscoveryTrackRequest, {"series_name": ""}, "blank series name")
    expect_invalid(schemas.PrioritizedGroupCreateRequest, {"name": "g", "priority": 999}, "priority too high")

    # Valid payloads still validate.
    model = schemas.validate(schemas.FeedCreateRequest, {
        "name": "Nyaa", "feed_type": "rss", "url": "https://nyaa.si/?page=rss",
        "interval": 60, "auto_download": True,
    })
    assert model.url == "https://nyaa.si/?page=rss"
    assert model.auto_download is True

    settings_model = schemas.validate(schemas.SettingsUpdateRequest, {"app_timezone": "Europe/Copenhagen"})
    assert settings_model.model_dump(exclude_unset=True) == {"app_timezone": "Europe/Copenhagen"}

    print("    [PASSED] schema models reject unknown keys and invalid values.")


# ---------------------------------------------------------------------------
# 6. Route-level enforcement (debug gating, path probe, schemas)
# ---------------------------------------------------------------------------
def _detail(response):
    payload = response.json() or {}
    return str(payload.get("detail", ""))


def test_route_level_enforcement():
    print("--> Testing route-level enforcement...")
    ctx = E2ETestContext(prefix="mangatrax_security_")
    try:
        # /api/debug/action is opt-in only (disabled by default in the app config).
        if main_app.AUTH_CONFIG.debug_actions:  # pragma: no cover - local operator override
            print("    note: MANGATRAX_DEBUG_ACTIONS is enabled in this environment; gating check skipped.")
        else:
            debug_response = ctx.client.post("/api/debug/action", json_data={"action": "clear-discovery"})
            assert debug_response.status_code == 403, (debug_response.status_code, debug_response.text)
            assert "MANGATRAX_DEBUG_ACTIONS" in _detail(debug_response), _detail(debug_response)

        # Unknown settings keys are rejected instead of persisted.
        settings_response = ctx.client.post("/api/settings", json_data={"shell_hook": "rm -rf /"})
        assert settings_response.status_code == 400, settings_response.status_code
        assert ctx.db.get_setting("shell_hook", "") == ""

        # website_url with a javascript: scheme is rejected and not stored.
        ctx.db.track_discovered_series("Oshi no Ko")
        series = ctx.db.get_all_series()
        series_id = series[0]["id"]
        link_response = ctx.client.post(
            f"/api/series/{series_id}/link", json_data={"website_url": "javascript:alert(document.cookie)"}
        )
        assert link_response.status_code == 400, link_response.status_code
        assert ctx.db.get_all_series()[0]["website_url"] != "javascript:alert(document.cookie)"

        # /api/calibre/test refuses to probe a path outside the configured roots.
        outside_root = tempfile.mkdtemp(prefix="mangatrax_probe_")
        try:
            probe = ctx.client.get("/api/calibre/test", params={"path": outside_root})
            assert probe.status_code == 403, (probe.status_code, _detail(probe))
            assert "outside the allowed" in _detail(probe) or "Rejected" in _detail(probe), _detail(probe)
        finally:
            import shutil
            shutil.rmtree(outside_root, ignore_errors=True)

        # The configured Calibre root itself remains testable.
        ok_probe = ctx.client.get("/api/calibre/test", params={"path": ctx.calibre_dir})
        assert ok_probe.status_code == 200, (ok_probe.status_code, ok_probe.text)

        # /api/feeds/test rejects SSRF targets at the schema boundary (400).
        ssrf_response = ctx.client.post(
            "/api/feeds/test", json_data={"url": "http://169.254.169.254/latest/meta-data/", "feed_type": "rss"}
        )
        assert ssrf_response.status_code == 400, (ssrf_response.status_code, ssrf_response.text)

        # /api/feeds rejects unknown keys.
        bad_feed = ctx.client.post("/api/feeds", json_data={
            "name": "x", "url": "https://nyaa.si/rss", "feed_type": "rss", "unexpected": True,
        })
        assert bad_feed.status_code == 400, bad_feed.status_code

        # Optional deployment-level containment: when MANGATRAX_PATH_ROOTS is
        # configured, directory settings outside those roots are rejected.
        outside_staging = tempfile.mkdtemp(prefix="mangatrax_staging_outside_")
        inside_staging = os.path.join(ctx.calibre_dir, "staging-inside-root")
        try:
            with patch.dict(os.environ, {"MANGATRAX_PATH_ROOTS": ctx.calibre_dir}):
                rejected = ctx.client.post("/api/settings", json_data={"staging_dir": outside_staging})
                assert rejected.status_code == 400, (rejected.status_code, _detail(rejected))
                assert ctx.db.get_setting("staging_dir") == ctx.staging_dir
                accepted = ctx.client.post("/api/settings", json_data={"staging_dir": inside_staging})
                assert accepted.status_code == 200, (accepted.status_code, _detail(accepted))
                assert ctx.db.get_setting("staging_dir") == inside_staging
        finally:
            import shutil
            shutil.rmtree(outside_staging, ignore_errors=True)

        print("    [PASSED] routes enforce debug gating, path containment and schema validation.")
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 7. Explicit outbound timeouts / shields
# ---------------------------------------------------------------------------
def test_outbound_timeout_shields():
    print("--> Testing explicit outbound timeouts and exception shields...")
    from app.providers import rss as rss_module

    assert rss_module.FEED_FETCH_TIMEOUT_SECONDS > 0
    original_default = socket.getdefaulttimeout()

    class _Boom(Exception):
        pass

    def boom(*args, **kwargs):
        raise _Boom("network down")

    with patch.object(rss_module.feedparser, "parse", side_effect=boom):
        provider = RSSFeedProvider(name="offline", url="https://example.com/rss")
        assert provider.fetch_releases() == []
    # The temporary socket timeout must always be restored.
    assert socket.getdefaulttimeout() == original_default, socket.getdefaulttimeout()

    # Metadata/feed HTTP clients use explicit positive timeouts.
    from app.metadata.anilist import AniListMetadataService
    from app.metadata.mangaupdates import MangaUpdatesService
    from app.providers.html_scraper import HtmlScraperProvider as _Html
    from app.providers.nyaa import NyaaMangaProvider
    from app.downloader.qbit import QBitClient

    assert AniListMetadataService().timeout > 0
    assert MangaUpdatesService().timeout > 0
    assert QBitClient().login.__defaults__ is not None

    # HTML scraper/nya aren't imported for their attributes only; validate source
    # for explicit urlopen timeouts as a regression guard.
    for module_path, needle in (
        (os.path.join(BACKEND_DIR, "app", "providers", "html_scraper.py"), "timeout=15.0"),
        (os.path.join(BACKEND_DIR, "app", "providers", "nyaa.py"), "timeout=15.0"),
        (os.path.join(BACKEND_DIR, "app", "metadata", "anilist.py"), "timeout=self.timeout"),
    ):
        with open(module_path, "r", encoding="utf-8") as handle:
            assert needle in handle.read(), f"{module_path} lost its explicit timeout ({needle})"

    print("    [PASSED] outbound calls carry explicit timeouts and never leak exceptions.")


def main():
    print("=" * 60)
    print("Running M3 Security Remediation Suite")
    print("=" * 60)
    tests = [
        test_ssrf_scheme_and_host_guards,
        test_outbound_boundaries_are_guarded,
        test_stored_xss_defenses,
        test_path_traversal_defenses,
        test_sql_like_escaping,
        test_schema_validation,
        test_route_level_enforcement,
        test_outbound_timeout_shields,
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
        print(f"SECURITY REMEDIATION SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL SECURITY REMEDIATION TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
