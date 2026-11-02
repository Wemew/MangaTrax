#!/usr/bin/env python3
"""M3 authentication-mode suite: ``none`` / ``basic`` / ``oidc``.

Covers:
  * AuthConfig defaults + refuse-to-start validation for every mode
  * redirect_uri and cookie Secure flag derived ONLY from MANGATRAX_PUBLIC_URL
    (Host / X-Forwarded-Host played no part)
  * session cookie HMAC signing (round-trip, tamper, expiry, wrong secret)
  * Basic auth with secrets.compare_digest (never ==)
  * middleware policy: HTML -> 302 /auth/login, /api/* -> 401 JSON, exemptions
  * break-glass Basic when mode=oidc
  * state/nonce mismatch, expired token, bad signature, JIT user creation
  * the FastAPI middleware wrapper maps decisions to real 302/401/403 responses

Run through the sanctioned runner (assertions compiled live):
    calibre-debug.exe -e "tests\\run_tests.py"
"""

import base64
import os
import sys
import time
import traceback
import types
import urllib.parse
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth
from app.database import MangaDatabase
from e2e.harness import E2ETestContext, main_app, run_coroutine_sync

PUBLIC_URL = "https://mangatrax.example.com"
ISSUER = "https://pocketid.example.com"
SESSION_SECRET = "0123456789abcdef0123456789abcdef"


def _oidc_config(**overrides):
    data = dict(
        mode="oidc",
        public_url=PUBLIC_URL,
        session_secret=SESSION_SECRET,
        oidc_issuer=ISSUER,
        oidc_client_id="mangatrax-client",
        oidc_client_secret="super-secret",
        basic_user="admin",
        basic_pass="",
    )
    data.update(overrides)
    return auth.AuthConfig(**data)


def _basic_config(**overrides):
    data = dict(mode="basic", basic_user="admin", basic_pass="hunter2")
    data.update(overrides)
    return auth.AuthConfig(**data)


def _basic_header(username, password):
    raw = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"authorization": f"Basic {raw}"}


def _basic_identity_claims(now):
    return {
        "sub": "pocketid-sub-alice",
        "preferred_username": "alice",
        "email": "alice@example.com",
        "name": "Alice",
        "iss": ISSUER,
        "aud": "mangatrax-client",
        "iat": now,
        "exp": now + 300,
    }


class FakeOIDC:
    """Minimal OIDCClient double for AuthManager flow tests.

    Cryptographic ID-token verification is Authlib's job in production; this
    double replays the same contract (exchange code, verify claims incl. nonce).
    """

    def __init__(self, config, claims=None, token="good-token"):
        self.config = config
        self.claims = claims or {}
        self.token = token
        self.authorization_calls = []

    def build_authorization_url(self, state, nonce, code_challenge):
        self.authorization_calls.append({"state": state, "nonce": nonce, "challenge": code_challenge})
        params = {
            "response_type": "code",
            "client_id": self.config.oidc_client_id,
            "redirect_uri": self.config.callback_url,
            "scope": auth.SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return "https://pocketid.example.com/authorize?" + urllib.parse.urlencode(params)

    def exchange_code(self, code, code_verifier):
        if code != "good-code":
            raise auth.AuthProviderUnavailable("invalid authorization code")
        if not code_verifier:
            raise auth.AuthProviderUnavailable("missing PKCE verifier")
        return {"access_token": "at", "id_token": self.token}

    def validate_id_token(self, id_token, expected_nonce, now=None):
        if id_token != self.token:
            raise auth.TokenValidationError("ID token signature verification failed")
        claims = dict(self.claims)
        if "nonce" not in claims:
            claims["nonce"] = expected_nonce
        return auth.validate_oidc_claims(
            claims,
            issuer=self.config.oidc_issuer,
            audience=self.config.oidc_client_id,
            expected_nonce=expected_nonce,
            now=now,
        )

    def end_session_url(self):
        return "https://pocketid.example.com/logout?post_logout_redirect_uri=" + urllib.parse.quote(
            self.config.public_url
        )


# ---------------------------------------------------------------------------
# 1. Configuration / refuse-to-start validation
# ---------------------------------------------------------------------------
def test_config_defaults_and_validation():
    print("--> Testing AuthConfig defaults and refuse-to-start validation...")
    default = auth.AuthConfig.from_env({})
    assert default.mode == "none", default.mode
    assert default.auth_enabled is False
    assert default.csrf_protection is True
    assert default.debug_actions is False
    default.validate()  # none mode needs no configuration

    # Every mode's required configuration is enforced with an actionable message.
    for bad_mode in ("oauth", "OIDC", "", "bearer"):
        config = auth.AuthConfig(mode=bad_mode)
        try:
            config.validate()
            raise AssertionError(f"invalid mode {bad_mode!r} was accepted")
        except auth.AuthConfigError as exc:
            assert "MANGATRAX_AUTH_MODE" in str(exc)

    try:
        auth.AuthConfig(mode="basic", basic_user="admin", basic_pass="").validate()
        raise AssertionError("basic mode without a password was accepted")
    except auth.AuthConfigError as exc:
        assert "MANGATRAX_BASIC_PASS" in str(exc)

    try:
        auth.AuthConfig(mode="oidc").validate()
        raise AssertionError("oidc mode with no configuration was accepted")
    except auth.AuthConfigError as exc:
        message = str(exc)
        for variable in ("MANGATRAX_PUBLIC_URL", "MANGATRAX_SESSION_SECRET",
                         "MANGATRAX_OIDC_ISSUER", "MANGATRAX_OIDC_CLIENT_ID",
                         "MANGATRAX_OIDC_CLIENT_SECRET"):
            assert variable in message, f"{variable} missing from: {message}"

    try:
        _oidc_config(session_secret="short").validate()
        raise AssertionError("short session secret was accepted")
    except auth.AuthConfigError as exc:
        assert "MANGATRAX_SESSION_SECRET" in str(exc)

    try:
        _oidc_config(public_url="mangatrax.example.com").validate()
        raise AssertionError("relative public URL was accepted")
    except auth.AuthConfigError as exc:
        assert "MANGATRAX_PUBLIC_URL" in str(exc)

    _oidc_config().validate()
    _basic_config().validate()
    print("    [PASSED] invalid/degraded auth configurations refuse to start.")


def test_public_url_derivations():
    print("--> Testing redirect_uri / cookie Secure derivation from MANGATRAX_PUBLIC_URL only...")
    https_config = _oidc_config()
    http_config = _oidc_config(public_url="http://mangatrax.lan")

    assert https_config.callback_url == f"{PUBLIC_URL}/auth/callback", https_config.callback_url
    assert https_config.cookie_secure is True
    assert https_config.public_origin == PUBLIC_URL
    assert http_config.cookie_secure is False
    assert http_config.callback_url == "http://mangatrax.lan/auth/callback"

    # No Host / X-Forwarded-Host input exists on the derivation at all: the same
    # config produces the same callback regardless of any request headers.
    manager = auth.AuthManager(https_config, oidc=FakeOIDC(https_config))
    start = manager.start_login("/releases")
    parsed = urllib.parse.urlsplit(start.authorization_url)
    query = urllib.parse.parse_qs(parsed.query)
    assert query["redirect_uri"] == [f"{PUBLIC_URL}/auth/callback"], query.get("redirect_uri")

    # describe() masks secrets for startup logging.
    described = https_config.describe()
    assert described["session_secret"] != SESSION_SECRET
    assert described["oidc_client_secret"] != "super-secret"
    assert described["mode"] == "oidc"

    print("    [PASSED] redirect_uri and cookie Secure flag come from MANGATRAX_PUBLIC_URL only.")


# ---------------------------------------------------------------------------
# 2. Signed session tokens
# ---------------------------------------------------------------------------
def test_signed_session_token():
    print("--> Testing HMAC-signed session cookie...")
    signer = auth.SignedToken(SESSION_SECRET, 3600, "session")
    token = signer.sign({"sub": "abc", "username": "alice"})
    payload = signer.verify(token)
    assert payload and payload["sub"] == "abc" and payload["p"] == "session", payload

    # Tampered payload rejected.
    header, signature = token.split(".")
    forged = base64.urlsafe_b64encode(b'{"sub":"admin","v":1,"p":"session","iat":0,"exp":9999999999}').rstrip(b"=").decode()
    assert signer.verify(f"{forged}.{signature}") is None

    # Wrong secret rejected.
    other = auth.SignedToken("x" * 32, 3600, "session")
    assert other.verify(token) is None

    # Wrong purpose rejected (a state cookie cannot become a session).
    state_signer = auth.SignedToken(SESSION_SECRET, 3600, "oidc_state")
    assert state_signer.verify(token) is None

    # Expiry enforced.
    expired = signer.sign({"sub": "abc"}, now=time.time() - 7200)
    assert signer.verify(expired) is None
    assert signer.verify(signer.sign({"sub": "abc"}, now=time.time() - 7200), now=time.time() - 7100) is not None

    # Garbage tokens are rejected without raising.
    for junk in ("", ".", "a.b.c", None, "not-a-token"):
        assert signer.verify(junk) is None
    print("    [PASSED] session cookies are signed, tamper-proof and expire.")


# ---------------------------------------------------------------------------
# 3. Basic auth (secrets.compare_digest)
# ---------------------------------------------------------------------------
def test_basic_auth_constant_time_comparison():
    print("--> Testing Basic credential comparison with secrets.compare_digest...")
    config = _basic_config()
    manager = auth.AuthManager(config)

    good = _basic_header("admin", "hunter2")
    decision = manager.check_request("GET", "/series", good, {})
    assert decision.action == "allow" and decision.identity is not None, decision
    assert decision.identity.auth_source == "basic"
    assert decision.actor == "admin"

    for headers in (
        _basic_header("admin", "wrong"),
        _basic_header("root", "hunter2"),
        {"authorization": "Basic not-base64!!"},
        {"authorization": "Bearer abc"},
        {},
    ):
        decision = manager.check_request("GET", "/api/series/rows", headers, {})
        assert decision.action == "unauthorized", (headers, decision)
        assert decision.status_code == 401
        assert decision.www_authenticate.startswith("Basic"), decision.www_authenticate

    # Verify the comparison helper is actually used (not plain ==).
    calls = []
    real_compare = __import__("hmac").compare_digest

    def tracking_compare(left, right):
        calls.append((left, right))
        return real_compare(left, right)

    with patch.object(auth.secrets, "compare_digest", side_effect=tracking_compare):
        manager.check_request("GET", "/", _basic_header("admin", "hunter2"), {})
    assert calls, "secrets.compare_digest was not used to compare Basic credentials"

    print("    [PASSED] Basic credentials use constant-time comparison and 401 challenges.")


# ---------------------------------------------------------------------------
# 4. Middleware policy decisions
# ---------------------------------------------------------------------------
def test_oidc_middleware_policy():
    print("--> Testing middleware gate policy (none/basic/oidc, exemptions, CSRF)...")
    none_manager = auth.AuthManager(auth.AuthConfig.from_env({}))
    decision = none_manager.check_request("GET", "/series", {}, {})
    assert decision.action == "allow" and decision.identity is None
    assert decision.actor == "anonymous"

    basic_manager = auth.AuthManager(_basic_config())
    assert basic_manager.check_request("GET", "/series", {}, {}).status_code == 401
    assert basic_manager.check_request("GET", "/api/series/rows", {}, {}).status_code == 401
    assert basic_manager.check_request("GET", "/health", {}, {}).action == "allow"

    config = _oidc_config()
    manager = auth.AuthManager(config, oidc=FakeOIDC(config))

    # Unauthenticated HTML route -> redirect to /auth/login with a local next path.
    decision = manager.check_request("GET", "/releases", {}, {})
    assert decision.action == "redirect_login", decision
    assert decision.location == "/auth/login?next=%2Freleases", decision.location

    # Unauthenticated API route -> 401 JSON (no redirect).
    decision = manager.check_request("GET", "/api/series/rows", {}, {})
    assert decision.action == "unauthorized" and decision.status_code == 401, decision

    # Exempt paths.
    for path in ("/health", "/healthz", "/auth/login", "/auth/callback", "/static/app.css", "/favicon.ico"):
        assert manager.check_request("GET", path, {}, {}).action == "allow", path

    # Valid session cookie -> allowed, actor from identity.
    identity = auth.Identity.from_oidc_claims(_basic_identity_claims(time.time()))
    session_cookie = manager.session_tokens.sign(identity.to_session())
    decision = manager.check_request("GET", "/api/series/rows", {}, {auth.SESSION_COOKIE_NAME: session_cookie})
    assert decision.action == "allow" and decision.identity.actor == "Alice", decision

    # Expired / tampered session -> unauthenticated again.
    expired = manager.session_tokens.sign(identity.to_session(), now=time.time() - 700000)
    assert manager.check_request("GET", "/series", {}, {auth.SESSION_COOKIE_NAME: expired}).action == "redirect_login"
    assert manager.check_request("GET", "/series", {}, {auth.SESSION_COOKIE_NAME: session_cookie + "x"}).action == "redirect_login"

    # CSRF: cross-origin unsafe request rejected, same-origin allowed.
    cross = manager.check_request("POST", "/api/settings", {"origin": "https://evil.example"}, {})
    assert cross.action == "forbidden" and cross.status_code == 403, cross
    same = manager.check_request(
        "POST", "/api/settings", {"origin": PUBLIC_URL},
        {auth.SESSION_COOKIE_NAME: session_cookie},
    )
    assert same.action == "allow", same
    # Safe methods are never blocked by the CSRF origin check.
    assert manager.check_request("GET", "/api/series/rows", {"origin": "https://evil.example"}, {}).action == "unauthorized"
    print("    [PASSED] middleware policy: HTML redirects, API 401s, exemptions, session + CSRF.")


def test_break_glass_basic_in_oidc_mode():
    print("--> Testing break-glass Basic while mode=oidc...")
    config = _oidc_config(basic_pass="breakglass")
    manager = auth.AuthManager(config, oidc=FakeOIDC(config))
    assert config.break_glass_enabled is True

    decision = manager.check_request("GET", "/api/series/rows", _basic_header("admin", "breakglass"), {})
    assert decision.action == "allow", decision
    assert decision.identity is not None and decision.identity.auth_source == "basic"

    # A wrong Basic attempt re-challenges instead of bouncing to PocketID.
    wrong = manager.check_request("GET", "/api/series/rows", _basic_header("admin", "nope"), {})
    assert wrong.action == "unauthorized", wrong
    assert wrong.www_authenticate.startswith("Basic"), wrong.www_authenticate

    # An ordinary browser visitor still gets the OIDC redirect (no Basic prompt).
    html = manager.check_request("GET", "/releases", {}, {})
    assert html.action == "redirect_login", html

    no_breakglass = _oidc_config(basic_pass="")
    assert no_breakglass.break_glass_enabled is False
    manager2 = auth.AuthManager(no_breakglass, oidc=FakeOIDC(no_breakglass))
    assert manager2.check_request(
        "GET", "/api/series/rows", _basic_header("admin", "breakglass"), {}
    ).action == "unauthorized"

    # PocketID outage: /auth/login falls back to the Basic challenge instead of 503.
    class _DownOIDC(FakeOIDC):
        def build_authorization_url(self, state, nonce, code_challenge):
            raise auth.AuthProviderUnavailable("provider down")

    down_config = _oidc_config(basic_pass="breakglass")
    down_manager = auth.AuthManager(down_config, oidc=_DownOIDC(down_config))
    with patch.object(main_app, "AUTH", down_manager), patch.object(main_app, "AUTH_CONFIG", down_config):
        response = run_coroutine_sync(main_app.auth_login("/"))
    assert response.status_code == 401, getattr(response, "status_code", None)
    assert response.headers.get("WWW-Authenticate", "").startswith("Basic"), response.headers

    # Without break-glass the same outage is a clean 503.
    plain_config = _oidc_config(basic_pass="")
    plain_manager = auth.AuthManager(plain_config, oidc=_DownOIDC(plain_config))
    with patch.object(main_app, "AUTH", plain_manager), patch.object(main_app, "AUTH_CONFIG", plain_config):
        response = run_coroutine_sync(main_app.auth_login("/"))
    assert response.status_code == 503, getattr(response, "status_code", None)

    print("    [PASSED] Basic survives a PocketID outage only when break-glass is configured.")


# ---------------------------------------------------------------------------
# 5. OIDC login flow logic (state/nonce/expiry/signature) + JIT users
# ---------------------------------------------------------------------------
def test_oidc_login_flow_with_double():
    print("--> Testing OIDC login flow: state, nonce, expiry, bad signature, JIT user...")
    ctx = E2ETestContext(prefix="mangatrax_auth_")
    try:
        config = _oidc_config()
        claims = _basic_identity_claims(time.time())
        manager = auth.AuthManager(config, user_store=ctx.db, oidc=FakeOIDC(config, claims=claims))

        start = manager.start_login("/releases")
        parsed = urllib.parse.urlsplit(start.authorization_url)
        query = urllib.parse.parse_qs(parsed.query)
        state = query["state"][0]
        assert query["nonce"][0], "nonce missing from authorization request"
        assert query["code_challenge_method"] == ["S256"], query
        assert query["code_challenge"][0], "PKCE challenge missing"
        assert query["scope"] == ["openid profile email"], query.get("scope")
        assert query["redirect_uri"] == [config.callback_url]

        result = manager.complete_login("good-code", state, start.state_cookie)
        assert result.identity.username == "alice", result.identity
        assert result.redirect_url == "/releases", result.redirect_url
        assert manager.session_tokens.verify(result.session_cookie)["sub"] == "pocketid-sub-alice"

        # JIT user created on first successful login.
        user = ctx.db.get_user("pocketid-sub-alice")
        assert user is not None and user["auth_source"] == "oidc", user
        assert user["display_name"] == "Alice", user

        # State mismatch (CSRF) rejected.
        try:
            manager.complete_login("good-code", "wrong-state", start.state_cookie)
            raise AssertionError("state mismatch was accepted")
        except auth.TokenValidationError as exc:
            assert "state" in str(exc).lower()

        # Missing/expired/tampered state cookie rejected.
        for cookie, code in (
            (None, "good-code"),
            (start.state_cookie + "tampered", "good-code"),
            ("", "good-code"),
            (start.state_cookie, None),
        ):
            try:
                manager.complete_login(code, state, cookie)
                raise AssertionError(f"bad state cookie/code accepted: cookie={cookie!r} code={code!r}")
            except auth.AuthError:
                pass

        # Expired state cookie rejected (10 minute TTL).
        assert manager.state_tokens.verify(start.state_cookie, now=time.time() + 10000) is None

        # Nonce mismatch rejected.
        nonce_bad = dict(claims)
        nonce_bad["nonce"] = "attacker-nonce"
        manager_nonce = auth.AuthManager(config, user_store=ctx.db, oidc=FakeOIDC(config, claims=nonce_bad))
        start2 = manager_nonce.start_login("/")
        try:
            manager_nonce.complete_login("good-code", urllib.parse.parse_qs(
                urllib.parse.urlsplit(start2.authorization_url).query
            )["state"][0], start2.state_cookie)
            raise AssertionError("nonce mismatch was accepted")
        except auth.TokenValidationError as exc:
            assert "nonce" in str(exc).lower(), str(exc)

        # Expired ID token rejected.
        expired = _basic_identity_claims(time.time() - 10000)
        manager_exp = auth.AuthManager(config, user_store=ctx.db, oidc=FakeOIDC(config, claims=expired))
        start3 = manager_exp.start_login("/")
        try:
            manager_exp.complete_login("good-code", urllib.parse.parse_qs(
                urllib.parse.urlsplit(start3.authorization_url).query
            )["state"][0], start3.state_cookie)
            raise AssertionError("expired ID token was accepted")
        except auth.TokenValidationError as exc:
            assert "expired" in str(exc).lower(), str(exc)

        # Bad signature rejected (the fake raises like Authlib's BadSignatureError).
        manager_bad_sig = auth.AuthManager(
            config, user_store=ctx.db, oidc=FakeOIDC(config, claims=claims, token="good-token")
        )
        start4 = manager_bad_sig.start_login("/")
        with patch.object(
            manager_bad_sig.oidc,
            "exchange_code",
            return_value={"access_token": "at", "id_token": "tampered-token"},
        ):
            try:
                manager_bad_sig.complete_login("good-code", urllib.parse.parse_qs(
                    urllib.parse.urlsplit(start4.authorization_url).query
                )["state"][0], start4.state_cookie)
                raise AssertionError("bad signature token was accepted")
            except auth.TokenValidationError:
                pass

        assert ctx.db.get_user("pocketid-sub-alice")["last_seen"]
        print("    [PASSED] OIDC state/nonce/expiry/signature checks and JIT provisioning work.")
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 6. Real FastAPI middleware wrapper mapping
# ---------------------------------------------------------------------------
def _fake_request(method, path, headers=None, cookies=None):
    request = types.SimpleNamespace()
    request.method = method
    request.url = types.SimpleNamespace(path=path)
    request.headers = headers or {}
    request.cookies = cookies or {}
    return request


def test_fastapi_middleware_mapping():
    print("--> Testing the FastAPI middleware wrapper (302/401/403 mapping)...")
    config = _oidc_config()
    manager = auth.AuthManager(config, oidc=FakeOIDC(config))

    async def call_next(request):
        return types.SimpleNamespace(kind="passed", actor=auth.get_current_actor())

    def run(request):
        return run_coroutine_sync(main_app.enforce_authentication(request, call_next))

    with patch.object(main_app, "AUTH", manager):
        # HTML -> 302 /auth/login.
        response = run(_fake_request("GET", "/releases"))
        assert response.status_code == 302, response.status_code
        assert response.headers.get("Location") == "/auth/login?next=%2Freleases", response.headers

        # API -> 401 JSON.
        response = run(_fake_request("GET", "/api/series/rows"))
        assert response.status_code == 401, response.status_code
        assert "Authentication required" in response.text, response.text

        # Exempt path passes through, with the anonymous actor bound.
        response = run(_fake_request("GET", "/health"))
        assert getattr(response, "kind", "") == "passed", response
        assert response.actor == "anonymous", response.actor
        assert auth.get_current_actor() == "system", "actor context leaked after the request"

        # CSRF mismatch -> 403.
        response = run(_fake_request("POST", "/api/settings", {"origin": "https://evil.example"}))
        assert response.status_code == 403, response.status_code

    basic_manager = auth.AuthManager(_basic_config())
    with patch.object(main_app, "AUTH", basic_manager):
        response = run(_fake_request("GET", "/api/series/rows"))
        assert response.status_code == 401, response.status_code
        assert response.headers.get("WWW-Authenticate", "").startswith("Basic"), response.headers

        response = run(_fake_request("GET", "/series", _basic_header("admin", "hunter2")))
        assert getattr(response, "kind", "") == "passed", response
        assert response.actor == "admin", response.actor

    none_manager = auth.AuthManager(auth.AuthConfig.from_env({}))
    with patch.object(main_app, "AUTH", none_manager):
        response = run(_fake_request("GET", "/anything"))
        assert getattr(response, "kind", "") == "passed", response

    print("    [PASSED] FastAPI middleware maps decisions to 302/401/403 and binds the actor.")


def main():
    print("=" * 60)
    print("Running M3 Authentication Modes Suite (none/basic/oidc)")
    print("=" * 60)
    tests = [
        test_config_defaults_and_validation,
        test_public_url_derivations,
        test_signed_session_token,
        test_basic_auth_constant_time_comparison,
        test_oidc_middleware_policy,
        test_break_glass_basic_in_oidc_mode,
        test_oidc_login_flow_with_double,
        test_fastapi_middleware_mapping,
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
        print(f"AUTH MODES SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL AUTH MODE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
