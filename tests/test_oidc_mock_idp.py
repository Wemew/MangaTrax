#!/usr/bin/env python3
"""M3 OIDC suite against a MOCK PocketID-style provider (no live identity provider).

The test spins up a real loopback HTTP server that serves:

  * ``/.well-known/openid-configuration`` (discovery)
  * ``/jwks`` (JSON Web Key Set)
  * ``/token`` (authorization-code + PKCE token endpoint)

and issues real HS256-signed ID tokens, so the whole discovery -> authorization
request -> code exchange -> ID-token validation flow is exercised over HTTP.

IMPORTANT - TEST DOUBLE BOUNDARY
--------------------------------
Production ID-token verification lives exclusively in Authlib (``OIDCClient.
_verify_with_authlib``).  Authlib/cryptography are not installed in Calibre's
embedded Python interpreter, which is where this repository's suites run, so
this suite injects a tiny HS256 verifier double to sign/verify the *mock
provider's* tokens.  That is a test fixture, not application code: production
cryptography is never reimplemented in ``backend/app``.  A dedicated test below
asserts that the production verifier fails closed (raises
``AuthProviderUnavailable``) when Authlib is unavailable instead of silently
skipping signature verification.

Run through the sanctioned runner (assertions compiled live):
    calibre-debug.exe -e "tests\\run_tests.py"
"""

import base64
import hashlib
import hmac
import http.server
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth
from e2e.harness import E2ETestContext

HS_SECRET = "mangatrax-mock-idp-hs256-secret-0123456789"
SCOPE_EXPECTED = "openid profile email groups"
REDIRECT_URI = "https://mangatrax.example.com/auth/callback"


# ---------------------------------------------------------------------------
# Test-double JWT helpers (mock provider side only)
# ---------------------------------------------------------------------------
def _b64u_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(value):
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))


def make_hs256_id_token(claims, kid="test-key", secret=HS_SECRET):
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    signing_input = (
        _b64u_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64u_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    )
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64u_encode(signature)}"


def hs256_test_verifier(id_token, jwks_document):
    """TEST FIXTURE ONLY: verify the mock IdP's HS256 signature against its JWKS."""
    parts = str(id_token).split(".")
    if len(parts) != 3:
        raise auth.TokenValidationError("malformed JWT")
    try:
        header = json.loads(_b64u_decode(parts[0]))
        provided_signature = _b64u_decode(parts[2])
    except Exception as exc:
        raise auth.TokenValidationError(f"malformed JWT: {exc}") from exc
    if header.get("alg") != "HS256":
        raise auth.TokenValidationError("unexpected JWT alg")
    keys = (jwks_document or {}).get("keys") or []
    key = next((candidate for candidate in keys if candidate.get("kid") == header.get("kid")), None)
    if key is None:
        raise auth.TokenValidationError(f"unknown key id {header.get('kid')!r}")
    try:
        key_bytes = _b64u_decode(key.get("k", ""))
    except Exception as exc:
        raise auth.TokenValidationError(f"invalid JWKS key: {exc}") from exc
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    expected_signature = hmac.new(key_bytes, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise auth.TokenValidationError("ID token signature verification failed (bad signature)")
    try:
        return json.loads(_b64u_decode(parts[1]))
    except Exception as exc:
        raise auth.TokenValidationError(f"malformed JWT payload: {exc}") from exc


# ---------------------------------------------------------------------------
# Mock PocketID server
# ---------------------------------------------------------------------------
class _MockIdPHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence the test output
        pass

    @property
    def state(self):
        return self.server.idp_state

    def _issuer(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        state = self.state
        state["get_count"] = state.get("get_count", 0) + 1
        if path == "/.well-known/openid-configuration":
            issuer = state.get("issuer_override") or self._issuer()
            self._send_json({
                "issuer": issuer,
                "authorization_endpoint": f"{self._issuer()}/authorize",
                "token_endpoint": f"{self._issuer()}/token",
                "jwks_uri": f"{self._issuer()}/jwks",
                "end_session_endpoint": f"{self._issuer()}/logout",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["HS256"],
                "scopes_supported": ["openid", "profile", "email"],
                "code_challenge_methods_supported": ["S256"],
            })
        elif path == "/jwks":
            self._send_json({"keys": [{
                "kty": "oct",
                "kid": "test-key",
                "alg": "HS256",
                "use": "sig",
                "k": _b64u_encode(HS_SECRET.encode("utf-8")),
            }]})
        elif path == "/logout":
            self._send_json({"logged_out": True})
        else:
            self._send_json({"error": "not_found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path != "/token":
            self._send_json({"error": "not_found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        form = {key: values[0] for key, values in urllib.parse.parse_qs(body).items()}
        state = self.state
        state["token_requests"] = state.get("token_requests", 0) + 1

        if form.get("grant_type") != "authorization_code":
            self._send_json({"error": "unsupported_grant_type"}, 400)
            return
        if form.get("code") != "good-code" or state.get("token_error"):
            self._send_json({"error": "invalid_grant", "error_description": "bad code"}, 400)
            return
        if not form.get("code_verifier"):
            self._send_json({"error": "invalid_request", "error_description": "PKCE verifier missing"}, 400)
            return
        if state.get("expected_callback") and form.get("redirect_uri") != state["expected_callback"]:
            self._send_json({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, 400)
            return

        now = time.time()
        claims = {
            "iss": state.get("issuer_override") or self._issuer(),
            "aud": state.get("audience", "mangatrax-client"),
            "sub": "mock-subject-alice",
            "preferred_username": "alice",
            "email": "alice@example.test",
            "name": "Alice Example",
            "iat": now,
            "exp": now + state.get("exp_offset", 300),
            "nonce": state.get("nonce", ""),
        }
        id_token = make_hs256_id_token(claims)
        if state.get("tamper_token"):
            id_token = id_token[:-4] + "abcd"
        self._send_json({
            "access_token": "mock-access-token",
            "token_type": "Bearer",
            "expires_in": 300,
            "id_token": id_token,
        })


class MockIdP:
    def __init__(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockIdPHandler)
        self.server.idp_state = {"exp_offset": 300, "get_count": 0, "token_requests": 0}
        self.thread = threading.Thread(target=self.server.serve_forever, name="mock-idp", daemon=True)

    @property
    def state(self):
        return self.server.idp_state

    @property
    def issuer(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


def _client_for(issuer, **kwargs):
    config = auth.AuthConfig(
        mode="oidc",
        public_url="https://mangatrax.example.com",
        session_secret="0123456789abcdef0123456789abcdef",
        oidc_issuer=issuer.rstrip("/"),
        oidc_client_id="mangatrax-client",
        oidc_client_secret="mock-client-secret",
    )
    return config, auth.OIDCClient(config, token_verifier=hs256_test_verifier, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_discovery_and_authorization_url():
    print("--> Testing OIDC discovery + authorization URL (state/nonce/PKCE)...")
    with MockIdP() as idp:
        config, client = _client_for(idp.issuer)
        document = client.discover()
        assert document["issuer"] == idp.issuer, document["issuer"]
        assert document["authorization_endpoint"] == f"{idp.issuer}/authorize"
        assert document["token_endpoint"] == f"{idp.issuer}/token"
        assert document["jwks_uri"] == f"{idp.issuer}/jwks"

        state, nonce, verifier = "state-value", "nonce-value", "verifier-value"
        challenge = auth._b64u_encode(hashlib.sha256(verifier.encode("ascii")).digest())
        url = client.build_authorization_url(state, nonce, challenge)
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        assert parsed.netloc == idp.issuer.replace("http://", ""), parsed.netloc
        assert query["response_type"] == ["code"]
        assert query["scope"] == [SCOPE_EXPECTED], query.get("scope")
        assert query["client_id"] == ["mangatrax-client"]
        assert query["state"] == [state]
        assert query["nonce"] == [nonce]
        assert query["code_challenge"] == [challenge]
        assert query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == [REDIRECT_URI], query.get("redirect_uri")

        # Discovery is cached (no extra HTTP round-trip).
        before = idp.state["get_count"]
        client.discover()
        assert idp.state["get_count"] == before, "discovery document was not cached"

        print("    [PASSED] discovery, state/nonce/PKCE and redirect_uri verified.")


def test_id_token_validation_success():
    print("--> Testing ID-token validation success path...")
    with MockIdP() as idp:
        config, client = _client_for(idp.issuer)
        nonce = "expected-nonce"
        now = time.time()
        token = make_hs256_id_token({
            "iss": idp.issuer,
            "aud": "mangatrax-client",
            "sub": "user-1",
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
        })
        claims = client.validate_id_token(token, expected_nonce=nonce)
        assert claims["sub"] == "user-1", claims
        assert claims["aud"] == "mangatrax-client"
        print("    [PASSED] a well-formed ID token validates against the mock JWKS.")


def test_id_token_rejections():
    print("--> Testing ID-token rejection: bad signature, expired, wrong nonce/aud/iss...")
    with MockIdP() as idp:
        config, client = _client_for(idp.issuer)
        now = time.time()

        def claims(**overrides):
            data = {
                "iss": idp.issuer,
                "aud": "mangatrax-client",
                "sub": "user-1",
                "iat": now,
                "exp": now + 300,
                "nonce": "good-nonce",
            }
            data.update(overrides)
            return data

        # 1. Tampered signature.
        valid = make_hs256_id_token(claims())
        tampered = valid[:-4] + ("abcd" if valid[-4:] != "abcd" else "efgh")
        try:
            client.validate_id_token(tampered, expected_nonce="good-nonce")
            raise AssertionError("tampered signature was accepted")
        except auth.TokenValidationError as exc:
            assert "signature" in str(exc).lower(), str(exc)

        # 2. Wrong signing secret (attacker-minted token).
        foreign = make_hs256_id_token(claims(), secret="attacker-secret")
        try:
            client.validate_id_token(foreign, expected_nonce="good-nonce")
            raise AssertionError("token signed with a foreign secret was accepted")
        except auth.TokenValidationError as exc:
            assert "signature" in str(exc).lower(), str(exc)

        # 3. Expired token.
        try:
            client.validate_id_token(
                make_hs256_id_token(claims(exp=now - 60)), expected_nonce="good-nonce"
            )
            raise AssertionError("expired token was accepted")
        except auth.TokenValidationError as exc:
            assert "expired" in str(exc).lower(), str(exc)

        # 4. Nonce mismatch (replay/CSRF).
        try:
            client.validate_id_token(
                make_hs256_id_token(claims(nonce="attacker")), expected_nonce="good-nonce"
            )
            raise AssertionError("nonce mismatch was accepted")
        except auth.TokenValidationError as exc:
            assert "nonce" in str(exc).lower(), str(exc)

        # 5. Wrong audience.
        try:
            client.validate_id_token(
                make_hs256_id_token(claims(aud="some-other-client")), expected_nonce="good-nonce"
            )
            raise AssertionError("wrong audience was accepted")
        except auth.TokenValidationError as exc:
            assert "audience" in str(exc).lower(), str(exc)

        # 6. Wrong issuer.
        try:
            client.validate_id_token(
                make_hs256_id_token(claims(iss="https://evil.example")), expected_nonce="good-nonce"
            )
            raise AssertionError("wrong issuer was accepted")
        except auth.TokenValidationError as exc:
            assert "issuer" in str(exc).lower(), str(exc)

        print("    [PASSED] bad signature, expired, replay and claim-mismatch tokens are rejected.")


def test_discovery_failures_and_logout():
    print("--> Testing discovery failure handling + end_session redirect...")
    with MockIdP() as idp:
        config, client = _client_for(idp.issuer)
        # end_session_endpoint is advertised -> logout redirect includes the public URL.
        target = client.end_session_url()
        assert target and target.startswith(f"{idp.issuer}/logout"), target
        assert "post_logout_redirect_uri=" in target, target

        # Discovery issuer mismatch must be rejected (mix-up defense).
        idp.state["issuer_override"] = "https://attacker.example"
        try:
            client.discover(force=True)
            raise AssertionError("discovery issuer mismatch was accepted")
        except auth.AuthProviderUnavailable as exc:
            assert "issuer" in str(exc).lower(), str(exc)
        idp.state["issuer_override"] = ""

        # Provider unreachable -> AuthProviderUnavailable (clear message, no hang).
        config_dead = auth.AuthConfig(
            mode="oidc",
            public_url="https://mangatrax.example.com",
            session_secret="0123456789abcdef0123456789abcdef",
            oidc_issuer="http://127.0.0.1:1",
            oidc_client_id="mangatrax-client",
            oidc_client_secret="mock-client-secret",
        )
        dead_client = auth.OIDCClient(config_dead, token_verifier=hs256_test_verifier, http_timeout=1.0)
        try:
            dead_client.discover()
            raise AssertionError("unreachable provider did not raise")
        except auth.AuthProviderUnavailable as exc:
            assert "OIDC" in str(exc), str(exc)

        print("    [PASSED] discovery failures are contained; logout redirect is advertised.")


def test_full_login_flow_and_jit_user():
    print("--> Testing full OIDC login flow against the mock provider (JIT user)...")
    ctx = E2ETestContext(prefix="mangatrax_oidc_")
    try:
        with MockIdP() as idp:
            config, client = _client_for(idp.issuer)
            manager = auth.AuthManager(config, user_store=ctx.db, oidc=client)

            start = manager.start_login("/releases")
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(start.authorization_url).query)
            state = query["state"][0]
            nonce = query["nonce"][0]
            assert query["redirect_uri"] == [REDIRECT_URI]

            # The provider builds the ID token containing the nonce it received.
            idp.state["nonce"] = nonce
            idp.state["expected_callback"] = config.callback_url

            result = manager.complete_login("good-code", state, start.state_cookie)
            assert result.identity.subject == "mock-subject-alice", result.identity
            assert result.identity.username == "alice"
            assert result.redirect_url == "/releases", result.redirect_url
            assert manager.session_tokens.verify(result.session_cookie)["sub"] == "mock-subject-alice"

            # JIT provisioning happened on first successful login.
            user = ctx.db.get_user("mock-subject-alice")
            assert user is not None and user["auth_source"] == "oidc", user
            assert idp.state["token_requests"] == 1, idp.state["token_requests"]

            # A second login with the same code/state cookie is still bound to the
            # original state; a forged state is rejected before any token request.
            before = idp.state["token_requests"]
            try:
                manager.complete_login("good-code", "forged-state", start.state_cookie)
                raise AssertionError("forged state accepted")
            except auth.TokenValidationError:
                pass
            assert idp.state["token_requests"] == before, "token endpoint was hit with a forged state"

            # Authorization-code exchange failures surface as AuthProviderUnavailable.
            start_bad = manager.start_login("/")
            idp.state["token_error"] = True
            try:
                manager.complete_login("bad-code", urllib.parse.parse_qs(
                    urllib.parse.urlsplit(start_bad.authorization_url).query
                )["state"][0], start_bad.state_cookie)
                raise AssertionError("failed code exchange was accepted")
            except auth.AuthProviderUnavailable:
                pass
            finally:
                idp.state["token_error"] = False

            # A tampered token from the provider is rejected.
            start_bad_token = manager.start_login("/")
            idp.state["nonce"] = urllib.parse.parse_qs(
                urllib.parse.urlsplit(start_bad_token.authorization_url).query
            )["nonce"][0]
            idp.state["tamper_token"] = True
            try:
                manager.complete_login("good-code", urllib.parse.parse_qs(
                    urllib.parse.urlsplit(start_bad_token.authorization_url).query
                )["state"][0], start_bad_token.state_cookie)
                raise AssertionError("tampered provider token was accepted")
            except auth.TokenValidationError:
                pass
            finally:
                idp.state["tamper_token"] = False

            print("    [PASSED] end-to-end OIDC login, JIT user, and rejection paths verified.")
    finally:
        ctx.close()


def test_production_verifier_uses_authlib_or_fails_closed():
    print("--> Testing production ID-token verifier requires Authlib (no insecure fallback)...")
    import importlib.util
    authlib_available = importlib.util.find_spec("authlib") is not None
    with MockIdP() as idp:
        # No token_verifier override: production path.
        config = auth.AuthConfig(
            mode="oidc",
            public_url="https://mangatrax.example.com",
            session_secret="0123456789abcdef0123456789abcdef",
            oidc_issuer=idp.issuer,
            oidc_client_id="mangatrax-client",
            oidc_client_secret="mock-client-secret",
        )
        client = auth.OIDCClient(config)
        now = time.time()
        token = make_hs256_id_token({
            "iss": idp.issuer, "aud": "mangatrax-client", "sub": "user-1",
            "iat": now, "exp": now + 300, "nonce": "n",
        })
        try:
            claims = client.validate_id_token(token, expected_nonce="n")
        except auth.AuthProviderUnavailable as exc:
            assert authlib_available is False, "Authlib is installed but the production verifier failed"
            assert "Authlib" in str(exc), str(exc)
            print("    [PASSED] without Authlib the production verifier fails closed (no hand-rolled crypto).")
        else:
            # Full deployment environment: Authlib really verified the JWT.
            assert authlib_available is True, "production verifier accepted a token without Authlib"
            assert claims["sub"] == "user-1", claims
            print("    [PASSED] production verifier delegates to Authlib and accepted a valid token.")


def main():
    print("=" * 60)
    print("Running M3 OIDC Mock-Provider Suite")
    print("=" * 60)
    tests = [
        test_discovery_and_authorization_url,
        test_id_token_validation_success,
        test_id_token_rejections,
        test_discovery_failures_and_logout,
        test_full_login_flow_and_jit_user,
        test_production_verifier_uses_authlib_or_fails_closed,
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
        print(f"OIDC MOCK PROVIDER SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL OIDC MOCK PROVIDER TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
