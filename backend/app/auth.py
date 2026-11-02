"""Authentication and identity for MangaTrax.

One component, three deployment modes, selected by ``MANGATRAX_AUTH_MODE``:

``none`` (default)
    No authentication.  Every request is allowed and audit-logged as
    ``anonymous`` so that user-triggered events stay distinguishable from
    automated ones.  This is the default so existing deployments keep working.

``basic``
    HTTP Basic authentication against ``MANGATRAX_BASIC_USER`` /
    ``MANGATRAX_BASIC_PASS`` (compared with :func:`secrets.compare_digest`, never
    ``==``).  Unauthenticated requests receive a ``401`` + ``WWW-Authenticate``
    challenge; browsers show their native credential dialog.

``oidc``
    OpenID Connect (PocketID / any standard confidential client) implemented
    with **Authlib** - no hand-rolled JWT/JWKS crypto anywhere in this module.
    ``GET /auth/login`` redirects to the discovered authorization endpoint with
    ``response_type=code``, ``scope=openid profile email``, ``state``, ``nonce``
    and PKCE S256; ``GET /auth/callback`` verifies state+nonce, exchanges the
    code at the token endpoint, validates the ID token (``iss``/``aud``/``exp``/
    ``nonce``) against the discovered JWKS and sets a signed session cookie;
    ``GET /auth/logout`` clears it and redirects to the advertised
    ``end_session_endpoint``.
    When ``MANGATRAX_BASIC_PASS`` is also set, HTTP Basic remains accepted as a
    **break-glass** path so a PocketID outage cannot lock the operator out.

Environment variables (all optional unless the mode needs them)
---------------------------------------------------------------
===============================  =========  ====================================
Variable                         Default    Meaning
===============================  =========  ====================================
``MANGATRAX_AUTH_MODE``          ``none``   ``none`` | ``basic`` | ``oidc``
``MANGATRAX_PUBLIC_URL``         ``""``     Public base URL, e.g.
                                            ``https://mangatrax.example.com``.
                                            ALWAYS used to build
                                            ``redirect_uri`` (``<public>/auth/callback``)
                                            and to decide the cookie ``Secure``
                                            flag (scheme).  The request ``Host`` /
                                            ``X-Forwarded-Host`` headers are never
                                            trusted because TLS terminates at the
                                            Traefik/NetBird proxy in front of the
                                            app and an attacker-controlled Host
                                            header could otherwise redirect the
                                            authorization code.
``MANGATRAX_SESSION_SECRET``     ``""``     HMAC key for the session cookie.
                                            Required in ``oidc`` mode; generate
                                            with ``python -c "import secrets;
                                            print(secrets.token_urlsafe(48))"``.
``MANGATRAX_BASIC_USER``         ``admin``  Basic username.
``MANGATRAX_BASIC_PASS``         ``""``     Basic password.  Empty disables Basic
                                            (and break-glass OIDC mode).
``MANGATRAX_OIDC_ISSUER``        ``""``     Issuer URL, e.g.
                                            ``https://pocketid.example.com``.
                                            Discovery is fetched from
                                            ``<issuer>/.well-known/openid-configuration``.
``MANGATRAX_OIDC_CLIENT_ID``     ``""``     PocketID client id.
``MANGATRAX_OIDC_CLIENT_SECRET`` ``""``     PocketID client secret.
``MANGATRAX_SESSION_MAX_AGE``    ``604800`` Session cookie lifetime in seconds
                                            (7 days).
``MANGATRAX_CSRF_PROTECT``       ``1``      Reject cross-origin unsafe requests
                                            when ``MANGATRAX_PUBLIC_URL`` is set
                                            (Origin/Referer check).
``MANGATRAX_DEBUG_ACTIONS``      ``0``      Enable ``/api/debug/action``.
===============================  =========  ====================================

PocketID client setup
---------------------
1. PocketID -> *Admin -> OIDC Clients -> Create Client*; name it ``MangaTrax``.
2. Set **Callback URL** to exactly ``<MANGATRAX_PUBLIC_URL>/auth/callback``
   (scheme/host/port must match ``MANGATRAX_PUBLIC_URL``; the app never derives
   it from the request).
3. Copy the generated **Client ID** / **Client Secret** into
   ``MANGATRAX_OIDC_CLIENT_ID`` / ``MANGATRAX_OIDC_CLIENT_SECRET``.
4. PocketID -> *Admin -> OIDC Clients -> MangaTrax -> Access*: add the
   **All Users** group (or the specific users/groups that may use MangaTrax).
   Without that access rule PocketID returns ``access_denied`` before the app
   ever sees a callback.
5. Set ``MANGATRAX_OIDC_ISSUER`` to the PocketID base URL (no trailing slash is
   fine; ``/.well-known/openid-configuration`` must be reachable from the app).
6. Set ``MANGATRAX_PUBLIC_URL`` to the URL users actually open (``https://...``)
   and ``MANGATRAX_SESSION_SECRET`` to a long random string.
7. Optional but recommended break-glass: also set ``MANGATRAX_BASIC_USER`` /
   ``MANGATRAX_BASIC_PASS`` so a down PocketID cannot lock you out.

Audit attribution
-----------------
The middleware sets the ``current_actor`` :class:`~contextvars.ContextVar` for
every request (the authenticated user, or ``anonymous`` in ``none`` mode).
``MangaDatabase.log_activity()`` reads it as its default, so all existing call
sites gain attribution without being rewritten.  Background worker threads have
no request context and explicitly pass ``actor="scheduler"``.

Refuse-to-start contract
------------------------
``AuthConfig.validate()`` raises :class:`AuthConfigError` with an actionable
message when a selected mode is missing required configuration.  ``main.py``
logs the message and exits rather than silently serving unauthenticated.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

logger = logging.getLogger("mangatrax.auth")

__all__ = [
    "AuthError",
    "AuthConfigError",
    "AuthProviderUnavailable",
    "TokenValidationError",
    "AUTH_MODES",
    "SCOPE",
    "SESSION_COOKIE_NAME",
    "STATE_COOKIE_NAME",
    "Identity",
    "AuthConfig",
    "SignedToken",
    "AuthDecision",
    "LoginStart",
    "LoginResult",
    "LogoutResult",
    "AuthManager",
    "OIDCClient",
    "validate_oidc_claims",
    "parse_basic_authorization",
    "sanitize_next_path",
    "current_actor",
    "get_current_actor",
    "set_current_actor",
    "reset_current_actor",
]

AUTH_MODES = ("none", "basic", "oidc")
SCOPE = "openid profile email"
SESSION_COOKIE_NAME = "mangatrax_session"
STATE_COOKIE_NAME = "mangatrax_oidc_state"
DEFAULT_SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 days
STATE_TTL_SECONDS = 10 * 60
CLOCK_SKEW_LEEWAY = 60
MIN_SESSION_SECRET_LENGTH = 16
PASSWORD_MASK = "********"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AuthError(Exception):
    """Base class for authentication/identity failures."""


class AuthConfigError(AuthError):
    """Invalid or incomplete authentication configuration (refuse to start)."""


class AuthProviderUnavailable(AuthError):
    """The OIDC provider (or a dependency such as Authlib) is unavailable."""


class TokenValidationError(AuthError):
    """An ID token / session token was rejected."""


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------
def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _constant_time_equals(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(str(left).encode("utf-8"), str(right).encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Request actor context (audit attribution)
# ---------------------------------------------------------------------------
current_actor: contextvars.ContextVar[str] = contextvars.ContextVar("current_actor", default="system")


def get_current_actor() -> str:
    """Return the audit actor for the current request / thread.

    HTTP middleware sets this per request; background threads inherit the
    default ``"system"`` and should pass ``actor="scheduler"`` explicitly.
    """
    try:
        return current_actor.get() or "system"
    except Exception:  # pragma: no cover - ContextVar is always available
        return "system"


def set_current_actor(actor: Optional[str]):
    """Bind *actor* to the current context; returns a token for resetting."""
    return current_actor.set(str(actor).strip() if actor and str(actor).strip() else "system")


def reset_current_actor(token) -> None:
    try:
        current_actor.reset(token)
    except Exception:  # pragma: no cover - reset in a different context
        pass


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Identity:
    """A resolved end-user identity (from an OIDC ID token or Basic credentials)."""

    subject: str
    username: str = ""
    email: str = ""
    display_name: str = ""
    auth_source: str = ""

    @property
    def actor(self) -> str:
        """Human-readable audit actor label persisted in ``activity_logs.actor``."""
        for candidate in (self.display_name, self.username, self.subject):
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        return "unknown"

    def to_session(self) -> Dict[str, Any]:
        return {
            "sub": self.subject,
            "username": self.username,
            "email": self.email,
            "name": self.display_name,
            "src": self.auth_source,
        }

    @classmethod
    def from_session(cls, data: Mapping[str, Any]) -> Optional["Identity"]:
        try:
            subject = str(data.get("sub") or "").strip()
        except Exception:
            return None
        if not subject:
            return None
        return cls(
            subject=subject,
            username=str(data.get("username") or ""),
            email=str(data.get("email") or ""),
            display_name=str(data.get("name") or ""),
            auth_source=str(data.get("src") or "oidc"),
        )

    @classmethod
    def from_oidc_claims(cls, claims: Mapping[str, Any]) -> "Identity":
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise TokenValidationError("ID token is missing the required 'sub' claim")
        email = str(claims.get("email") or "")
        preferred = str(claims.get("preferred_username") or "")
        name = str(claims.get("name") or "")
        return cls(
            subject=subject,
            username=preferred or email or subject,
            email=email,
            display_name=name or preferred or email,
            auth_source="oidc",
        )

    @classmethod
    def for_basic(cls, username: str) -> "Identity":
        return Identity(
            subject=f"basic:{username}",
            username=username,
            email="",
            display_name=username,
            auth_source="basic",
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _env_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AuthConfig:
    """Authentication configuration assembled from environment variables."""

    mode: str = "none"
    public_url: str = ""
    session_secret: str = ""
    basic_user: str = "admin"
    basic_pass: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    session_max_age: int = DEFAULT_SESSION_MAX_AGE
    csrf_protection: bool = True
    debug_actions: bool = False

    # -- construction -----------------------------------------------------
    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AuthConfig":
        source: Mapping[str, str] = os.environ if env is None else env

        def get(name: str, default: str = "") -> str:
            value = source.get(name)
            return default if value is None else str(value)

        try:
            max_age = int(get("MANGATRAX_SESSION_MAX_AGE", str(DEFAULT_SESSION_MAX_AGE)) or DEFAULT_SESSION_MAX_AGE)
        except (TypeError, ValueError):
            max_age = DEFAULT_SESSION_MAX_AGE
        max_age = max(300, min(max_age, 30 * 24 * 60 * 60))

        return cls(
            mode=(get("MANGATRAX_AUTH_MODE", "none").strip().lower() or "none"),
            public_url=get("MANGATRAX_PUBLIC_URL", "").strip().rstrip("/"),
            session_secret=get("MANGATRAX_SESSION_SECRET", "").strip(),
            basic_user=get("MANGATRAX_BASIC_USER", "admin").strip() or "admin",
            basic_pass=get("MANGATRAX_BASIC_PASS", ""),
            oidc_issuer=get("MANGATRAX_OIDC_ISSUER", "").strip().rstrip("/"),
            oidc_client_id=get("MANGATRAX_OIDC_CLIENT_ID", "").strip(),
            oidc_client_secret=get("MANGATRAX_OIDC_CLIENT_SECRET", "").strip(),
            session_max_age=max_age,
            csrf_protection=_env_bool(get("MANGATRAX_CSRF_PROTECT", None), True),
            debug_actions=_env_bool(get("MANGATRAX_DEBUG_ACTIONS", None), False),
        )

    # -- derived properties ----------------------------------------------
    @property
    def auth_enabled(self) -> bool:
        return self.mode in ("basic", "oidc")

    @property
    def break_glass_enabled(self) -> bool:
        return self.mode == "oidc" and bool(self.basic_pass)

    @property
    def callback_url(self) -> str:
        """OIDC redirect_uri - built ONLY from MANGATRAX_PUBLIC_URL."""
        if not self.public_url:
            return ""
        return f"{self.public_url}/auth/callback"

    @property
    def public_origin(self) -> str:
        if not self.public_url:
            return ""
        parsed = urllib.parse.urlsplit(self.public_url)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def cookie_secure(self) -> bool:
        """Secure flag derives from the scheme of MANGATRAX_PUBLIC_URL.

        The app itself only ever sees plain HTTP (TLS terminates at the
        Traefik/NetBird proxy), so ``request.url.scheme`` must not be used.
        """
        parsed = urllib.parse.urlsplit(self.public_url) if self.public_url else None
        return bool(parsed and parsed.scheme.lower() == "https")

    # -- validation --------------------------------------------------------
    def validate(self) -> None:
        """Raise :class:`AuthConfigError` with an actionable message when invalid."""
        mode = (self.mode or "").strip().lower()
        if mode not in AUTH_MODES:
            raise AuthConfigError(
                f"MANGATRAX_AUTH_MODE='{self.mode}' is invalid; use one of: {', '.join(AUTH_MODES)}"
            )

        if self.public_url:
            parsed = urllib.parse.urlsplit(self.public_url)
            if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
                raise AuthConfigError(
                    "MANGATRAX_PUBLIC_URL must be an absolute http(s) URL, "
                    f"e.g. https://mangatrax.example.com (got '{self.public_url}')"
                )
            if parsed.query or parsed.fragment:
                raise AuthConfigError("MANGATRAX_PUBLIC_URL must not contain a query string or fragment")
            if parsed.scheme.lower() != "https":
                logger.warning(
                    "MANGATRAX_PUBLIC_URL uses plain http; the session cookie will be sent "
                    "without the Secure flag. Put MangaTrax behind HTTPS (Traefik) in production."
                )

        if mode == "basic":
            if not self.basic_pass:
                raise AuthConfigError(
                    "MANGATRAX_AUTH_MODE=basic requires MANGATRAX_BASIC_PASS to be set"
                )
            if not self.basic_user:
                raise AuthConfigError("MANGATRAX_AUTH_MODE=basic requires MANGATRAX_BASIC_USER")

        if mode == "oidc":
            missing = []
            if not self.public_url:
                missing.append("MANGATRAX_PUBLIC_URL (used to build redirect_uri and the cookie Secure flag)")
            if not self.session_secret:
                missing.append("MANGATRAX_SESSION_SECRET (HMAC key for the session cookie)")
            elif len(self.session_secret) < MIN_SESSION_SECRET_LENGTH:
                missing.append(
                    f"MANGATRAX_SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} characters "
                    "(generate with: python -c \"import secrets; print(secrets.token_urlsafe(48))\")"
                )
            if not self.oidc_issuer:
                missing.append("MANGATRAX_OIDC_ISSUER (e.g. https://pocketid.example.com)")
            issuer_parsed = urllib.parse.urlsplit(self.oidc_issuer) if self.oidc_issuer else None
            if self.oidc_issuer and (
                not issuer_parsed
                or issuer_parsed.scheme.lower() not in ("http", "https")
                or not issuer_parsed.netloc
            ):
                missing.append("MANGATRAX_OIDC_ISSUER must be an absolute http(s) URL")
            if not self.oidc_client_id:
                missing.append("MANGATRAX_OIDC_CLIENT_ID")
            if not self.oidc_client_secret:
                missing.append("MANGATRAX_OIDC_CLIENT_SECRET")
            if missing:
                raise AuthConfigError(
                    "MANGATRAX_AUTH_MODE=oidc is not fully configured; missing/invalid: "
                    + "; ".join(missing)
                )

    def describe(self) -> Dict[str, Any]:
        """Configuration summary safe for startup logs (secrets masked)."""
        return {
            "mode": self.mode,
            "public_url": self.public_url or "(unset)",
            "callback_url": self.callback_url or "(unset)",
            "cookie_secure": self.cookie_secure,
            "session_secret": PASSWORD_MASK if self.session_secret else "(unset)",
            "basic_user": self.basic_user if self.basic_pass else "(disabled)",
            "basic_pass": PASSWORD_MASK if self.basic_pass else "(unset)",
            "oidc_issuer": self.oidc_issuer or "(unset)",
            "oidc_client_id": self.oidc_client_id or "(unset)",
            "oidc_client_secret": PASSWORD_MASK if self.oidc_client_secret else "(unset)",
            "csrf_protection": self.csrf_protection,
            "debug_actions": self.debug_actions,
        }


# ---------------------------------------------------------------------------
# Signed tokens (session cookie + short-lived OIDC state cookie)
# ---------------------------------------------------------------------------
class SignedToken:
    """Small HMAC-SHA256 signed JSON token.

    This is *not* JWT and is never used for OIDC token verification; it only
    protects the local session cookie and the short-lived OIDC state cookie.
    Format: ``base64url(json).base64url(hmac_sha256(json))``.
    """

    def __init__(self, secret: str, ttl_seconds: int, purpose: str):
        self.secret = str(secret or "")
        self.ttl_seconds = int(ttl_seconds)
        self.purpose = str(purpose)

    def _signature(self, body: bytes) -> bytes:
        return hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).digest()

    def sign(self, payload: Mapping[str, Any], now: Optional[float] = None, ttl: Optional[int] = None) -> str:
        issued = int(time.time() if now is None else now)
        body = dict(payload or {})
        body["v"] = 1
        body["p"] = self.purpose
        body["iat"] = issued
        body["exp"] = issued + int(self.ttl_seconds if ttl is None else ttl)
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return f"{_b64u_encode(raw)}.{_b64u_encode(self._signature(raw))}"

    def verify(self, token: Optional[str], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not token or not isinstance(token, str) or not self.secret:
            return None
        parts = token.split(".")
        if len(parts) != 2:
            return None
        try:
            raw = _b64u_decode(parts[0])
            provided_sig = _b64u_decode(parts[1])
        except (ValueError, TypeError):
            return None
        expected_sig = self._signature(raw)
        if not hmac.compare_digest(provided_sig, expected_sig):
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("v") != 1 or payload.get("p") != self.purpose:
            return None
        try:
            expires_at = int(payload.get("exp"))
        except (TypeError, ValueError):
            return None
        current = time.time() if now is None else now
        if current > expires_at:
            return None
        return payload


# ---------------------------------------------------------------------------
# Basic auth helpers
# ---------------------------------------------------------------------------
def parse_basic_authorization(header_value: Optional[str]) -> Optional[Tuple[str, str]]:
    """Parse an ``Authorization: Basic ...`` header into ``(username, password)``."""
    if not header_value or not isinstance(header_value, str):
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].strip().lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(parts[1].strip(), validate=True).decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    username, _, password = decoded.partition(":")
    return username, password


def basic_identity(config: AuthConfig, username: str, password: str) -> Optional[Identity]:
    """Constant-time credential comparison (``secrets.compare_digest``, never ``==``)."""
    if not config.basic_pass:
        return None
    user_ok = secrets.compare_digest(str(username).encode("utf-8"), config.basic_user.encode("utf-8"))
    pass_ok = secrets.compare_digest(str(password).encode("utf-8"), config.basic_pass.encode("utf-8"))
    if user_ok and pass_ok:
        return Identity.for_basic(config.basic_user)
    return None


def sanitize_next_path(next_path: Optional[str]) -> str:
    """Constrain a post-login redirect to a local, single-slash path."""
    if not next_path or not isinstance(next_path, str):
        return "/"
    candidate = next_path.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    if "\\" in candidate or "://" in candidate:
        return "/"
    if candidate.startswith("/auth/"):
        # Never bounce back into the login flow itself (redirect loop).
        return "/"
    if any(ord(ch) < 0x20 for ch in candidate):
        return "/"
    return candidate


def _headers_get(headers: Optional[Any], name: str) -> str:
    if not headers:
        return ""
    try:
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        if value is None:
            value = headers.get(name.upper())
        return "" if value is None else str(value)
    except Exception:  # pragma: no cover - exotic header mappings
        return ""


def _cookies_get(cookies: Optional[Any], name: str) -> str:
    if not cookies:
        return ""
    try:
        value = cookies.get(name)
        return "" if value is None else str(value)
    except Exception:  # pragma: no cover
        return ""


# ---------------------------------------------------------------------------
# OIDC claims validation (claims only - cryptographic verification is Authlib's)
# ---------------------------------------------------------------------------
def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def validate_oidc_claims(
    claims: Mapping[str, Any],
    *,
    issuer: str,
    audience: str,
    expected_nonce: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Validate the claims of a (already signature-verified) ID token.

    Enforces ``iss``, ``aud``, ``exp``, optional ``iat``/``nbf`` sanity, the
    nonce anti-replay binding, and the presence of ``sub``.  The cryptographic
    signature/JWKS part is delegated to Authlib in :meth:`OIDCClient.validate_id_token`.
    """
    if not isinstance(claims, Mapping):
        raise TokenValidationError("ID token claims must be a mapping")
    current = time.time() if now is None else float(now)
    normalized = dict(claims)

    token_issuer = normalized.get("iss")
    if not isinstance(token_issuer, str) or not token_issuer.strip():
        raise TokenValidationError("ID token is missing the 'iss' claim")
    if token_issuer.rstrip("/") != str(issuer).rstrip("/"):
        raise TokenValidationError(
            f"ID token issuer mismatch: expected '{issuer}', got '{token_issuer}'"
        )

    token_audience = normalized.get("aud")
    if token_audience is None:
        raise TokenValidationError("ID token is missing the 'aud' claim")
    if isinstance(token_audience, (list, tuple)):
        audiences = [str(item) for item in token_audience]
    else:
        audiences = [str(token_audience)]
    if str(audience) not in audiences:
        raise TokenValidationError(
            f"ID token audience mismatch: expected '{audience}', got '{token_audience}'"
        )

    expires_at = _as_number(normalized.get("exp"))
    if expires_at is None:
        raise TokenValidationError("ID token is missing a numeric 'exp' claim")
    if current > expires_at:
        raise TokenValidationError("ID token has expired")

    issued_at = _as_number(normalized.get("iat"))
    if issued_at is not None and issued_at > current + CLOCK_SKEW_LEEWAY:
        raise TokenValidationError("ID token 'iat' is in the future")
    not_before = _as_number(normalized.get("nbf"))
    if not_before is not None and current + CLOCK_SKEW_LEEWAY < not_before:
        raise TokenValidationError("ID token is not valid yet ('nbf')")

    if expected_nonce is not None:
        token_nonce = normalized.get("nonce")
        if not isinstance(token_nonce, str) or not _constant_time_equals(token_nonce, expected_nonce):
            raise TokenValidationError("ID token nonce mismatch (possible replay / CSRF)")

    if not str(normalized.get("sub") or "").strip():
        raise TokenValidationError("ID token is missing the 'sub' claim")

    return normalized


# ---------------------------------------------------------------------------
# OIDC client
# ---------------------------------------------------------------------------
class OIDCClient:
    """Authlib-backed OIDC confidential-client helper.

    Discovery / token-endpoint HTTP calls use the standard library with explicit
    timeouts and exception shields.  ID-token signature verification is
    delegated to Authlib (``authlib.jose.JsonWebKey`` + ``jwt.decode``); this
    class never implements JWT/JWKS crypto itself.  If Authlib is unavailable
    the provider fails closed with :class:`AuthProviderUnavailable` rather than
    falling back to an insecure path.
    """

    def __init__(
        self,
        config: AuthConfig,
        *,
        http_timeout: float = 10.0,
        token_verifier: Optional[Callable[[str, Mapping[str, Any]], Mapping[str, Any]]] = None,
        jwks_ttl: int = 3600,
    ):
        self.config = config
        self.http_timeout = float(http_timeout)
        self.token_verifier = token_verifier or self._verify_with_authlib
        self.jwks_ttl = int(jwks_ttl)
        self._discovery: Optional[Dict[str, Any]] = None
        self._discovery_at: float = 0.0
        self._jwks: Optional[Dict[str, Any]] = None
        self._jwks_at: float = 0.0

    # -- HTTP transport (stdlib, explicit timeout, exception shielded) ----
    def _http_get_json(self, url: str) -> Dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "MangaTrax/1.0 (OIDC)",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.http_timeout) as response:
                status = getattr(response, "status", 200)
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise AuthProviderUnavailable(f"OIDC endpoint returned HTTP {exc.code}: {url}") from exc
        except Exception as exc:
            raise AuthProviderUnavailable(f"OIDC request failed ({url}): {exc}") from exc
        if status != 200:
            raise AuthProviderUnavailable(f"OIDC endpoint returned HTTP {status}: {url}")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AuthProviderUnavailable(f"OIDC endpoint returned invalid JSON: {url}") from exc
        if not isinstance(document, dict):
            raise AuthProviderUnavailable(f"OIDC endpoint returned a non-object JSON document: {url}")
        return document

    def _http_post_form(self, url: str, form: Mapping[str, Any]) -> Dict[str, Any]:
        data = urllib.parse.urlencode(dict(form)).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "MangaTrax/1.0 (OIDC)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.http_timeout) as response:
                status = getattr(response, "status", 200)
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise AuthProviderUnavailable(f"OIDC token endpoint returned HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise AuthProviderUnavailable(f"OIDC token request failed: {exc}") from exc
        if status != 200:
            raise AuthProviderUnavailable(f"OIDC token endpoint returned HTTP {status}")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AuthProviderUnavailable("OIDC token endpoint returned invalid JSON") from exc
        if not isinstance(document, dict):
            raise AuthProviderUnavailable("OIDC token endpoint returned a non-object JSON document")
        if document.get("error"):
            raise AuthProviderUnavailable(
                f"OIDC token exchange failed: {document.get('error')}: {document.get('error_description', '')}"
            )
        return document

    # -- discovery ---------------------------------------------------------
    def discover(self, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force and self._discovery and (now - self._discovery_at) < self.jwks_ttl:
            return self._discovery
        if not self.config.oidc_issuer:
            raise AuthProviderUnavailable("MANGATRAX_OIDC_ISSUER is not configured")
        url = f"{self.config.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
        document = self._http_get_json(url)

        discovered_issuer = document.get("issuer")
        if discovered_issuer and str(discovered_issuer).rstrip("/") != self.config.oidc_issuer.rstrip("/"):
            raise AuthProviderUnavailable(
                "OIDC discovery document issuer does not match MANGATRAX_OIDC_ISSUER "
                f"(expected '{self.config.oidc_issuer}', got '{discovered_issuer}')"
            )
        for field_name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not document.get(field_name):
                raise AuthProviderUnavailable(f"OIDC discovery document is missing '{field_name}'")
        self._discovery = document
        self._discovery_at = now
        return document

    # -- authorization / token / logout -----------------------------------
    def build_authorization_url(self, state: str, nonce: str, code_challenge: str) -> str:
        document = self.discover()
        endpoint = str(document["authorization_endpoint"])
        params = {
            "response_type": "code",
            "client_id": self.config.oidc_client_id,
            "redirect_uri": self.config.callback_url,
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        separator = "&" if "?" in endpoint else "?"
        return f"{endpoint}{separator}{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: str) -> Dict[str, Any]:
        if not code:
            raise AuthProviderUnavailable("Authorization code is missing")
        document = self.discover()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.callback_url,
            "client_id": self.config.oidc_client_id,
            "client_secret": self.config.oidc_client_secret,
            "code_verifier": code_verifier,
        }
        token_response = self._http_post_form(str(document["token_endpoint"]), form)
        return token_response

    def _get_jwks(self, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force and self._jwks and (now - self._jwks_at) < self.jwks_ttl:
            return self._jwks
        document = self.discover()
        jwks = self._http_get_json(str(document["jwks_uri"]))
        if not isinstance(jwks.get("keys"), list) or not jwks["keys"]:
            raise AuthProviderUnavailable("OIDC JWKS document contains no keys")
        self._jwks = jwks
        self._jwks_at = now
        return jwks

    def _verify_with_authlib(self, id_token: str, jwks_document: Mapping[str, Any]) -> Mapping[str, Any]:
        """Production verifier: Authlib performs the JWT signature/JWKS verification."""
        try:
            from authlib.jose import JsonWebKey, jwt  # noqa: WPS433 (deliberate lazy import)
        except ImportError as exc:  # pragma: no cover - exercised in embedded-Python tests
            raise AuthProviderUnavailable(
                "Authlib is required for OIDC mode but is not installed in this interpreter. "
                "Install the app requirements (see backend/requirements.txt)."
            ) from exc
        try:
            key_set = JsonWebKey.import_key_set(dict(jwks_document))
        except Exception as exc:
            raise TokenValidationError(f"Could not import the OIDC JWKS: {exc}") from exc
        try:
            claims = jwt.decode(id_token, key_set)
        except Exception as exc:
            raise TokenValidationError(f"ID token signature verification failed: {exc}") from exc
        try:
            claims.validate()
        except Exception as exc:
            raise TokenValidationError(f"ID token claims are invalid: {exc}") from exc
        return dict(claims)

    def validate_id_token(
        self,
        id_token: str,
        *,
        expected_nonce: Optional[str],
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not id_token:
            raise TokenValidationError("OIDC token response did not include an id_token")
        jwks_document = self._get_jwks()
        claims = self.token_verifier(id_token, jwks_document)
        return validate_oidc_claims(
            claims,
            issuer=self.config.oidc_issuer,
            audience=self.config.oidc_client_id,
            expected_nonce=expected_nonce,
            now=now,
        )

    def end_session_url(self) -> Optional[str]:
        try:
            document = self.discover()
        except AuthProviderUnavailable as exc:
            logger.warning("Cannot determine OIDC end_session_endpoint: %s", exc)
            return None
        endpoint = document.get("end_session_endpoint")
        if not endpoint:
            return None
        params = {}
        if self.config.public_url:
            params["post_logout_redirect_uri"] = self.config.public_url
        if not params:
            return str(endpoint)
        separator = "&" if "?" in str(endpoint) else "?"
        return f"{endpoint}{separator}{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# Decisions returned to the web layer
# ---------------------------------------------------------------------------
@dataclass
class AuthDecision:
    """Outcome of :meth:`AuthManager.check_request`."""

    action: str = "allow"  # allow | redirect_login | unauthorized | forbidden
    identity: Optional[Identity] = None
    location: str = ""
    status_code: int = 200
    www_authenticate: str = ""
    detail: str = ""
    actor: str = "anonymous"

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


@dataclass
class LoginStart:
    authorization_url: str
    state_cookie: str
    state_cookie_name: str = STATE_COOKIE_NAME


@dataclass
class LoginResult:
    identity: Identity
    session_cookie: str
    redirect_url: str


@dataclass
class LogoutResult:
    redirect_url: Optional[str]
    session_cookie_name: str = SESSION_COOKIE_NAME


# ---------------------------------------------------------------------------
# Auth manager
# ---------------------------------------------------------------------------
_EXEMPT_PREFIXES = ("/auth/", "/static/", "/favicon.ico")
_EXEMPT_EXACT = frozenset({"/health", "/healthz"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
BASIC_CHALLENGE = 'Basic realm="MangaTrax", charset="UTF-8"'


class AuthManager:
    """Request authentication policy shared by the middleware and auth routes."""

    def __init__(
        self,
        config: AuthConfig,
        *,
        user_store: Optional[Any] = None,
        oidc: Optional[OIDCClient] = None,
        provision_interval: float = 300.0,
    ):
        self.config = config
        self.users = user_store
        self.oidc = oidc
        if self.oidc is None and config.mode == "oidc" and config.session_secret:
            self.oidc = OIDCClient(config)
        self.session_tokens = SignedToken(config.session_secret, config.session_max_age, "session")
        self.state_tokens = SignedToken(config.session_secret, STATE_TTL_SECONDS, "oidc_state")
        self.provision_interval = float(provision_interval)
        self._provisioned_at: Dict[str, float] = {}

    # -- identity resolution ----------------------------------------------
    def resolve_identity(self, headers: Optional[Any], cookies: Optional[Any]) -> Optional[Identity]:
        if not self.config.auth_enabled:
            return None

        if self.config.mode == "oidc":
            payload = self.session_tokens.verify(_cookies_get(cookies, SESSION_COOKIE_NAME))
            if payload:
                identity = Identity.from_session(payload)
                if identity:
                    return identity
            # Break-glass: PocketID outage must not lock the operator out.
            if self.config.break_glass_enabled:
                basic_header = _headers_get(headers, "authorization")
                parsed = parse_basic_authorization(basic_header)
                if parsed:
                    return basic_identity(self.config, parsed[0], parsed[1])
            return None

        # basic mode (or auth disabled handled above)
        basic_header = _headers_get(headers, "authorization")
        parsed = parse_basic_authorization(basic_header)
        if parsed:
            return basic_identity(self.config, parsed[0], parsed[1])
        return None

    # -- request policy ----------------------------------------------------
    @staticmethod
    def is_exempt_path(path: str) -> bool:
        candidate = (path or "/").split("?", 1)[0]
        if candidate in _EXEMPT_EXACT:
            return True
        return any(candidate.startswith(prefix) for prefix in _EXEMPT_PREFIXES)

    def _cross_origin_forbidden(self, method: str, headers: Optional[Any]) -> bool:
        if not (self.config.auth_enabled and self.config.csrf_protection and self.config.public_origin):
            return False
        if (method or "GET").upper() in _SAFE_METHODS:
            return False
        origin = _headers_get(headers, "origin").strip()
        if origin:
            return origin.rstrip("/") != self.config.public_origin
        referer = _headers_get(headers, "referer").strip()
        if referer:
            return not referer.startswith(self.config.public_origin + "/")
        # Non-browser clients (curl, scripts) do not send Origin/Referer.
        return False

    def check_request(
        self,
        method: str,
        path: str,
        headers: Optional[Any] = None,
        cookies: Optional[Any] = None,
    ) -> AuthDecision:
        candidate_path = (path or "/").split("?", 1)[0]
        identity = self.resolve_identity(headers, cookies)
        actor = identity.actor if identity else "anonymous"

        if not self.config.auth_enabled:
            return AuthDecision(action="allow", identity=None, actor="anonymous")

        if self._cross_origin_forbidden(method, headers):
            return AuthDecision(
                action="forbidden",
                status_code=403,
                detail="Cross-origin request rejected (Origin/Referer mismatch)",
                actor=actor,
            )

        if self.is_exempt_path(candidate_path):
            self.record_identity(identity)
            return AuthDecision(action="allow", identity=identity, actor=actor)

        if identity is not None:
            self.record_identity(identity)
            return AuthDecision(action="allow", identity=identity, actor=actor)

        # Unauthenticated.
        # ``basic`` mode always challenges.  In ``oidc`` mode with break-glass we
        # only challenge when the client actually tried Basic credentials (so a
        # browser re-prompts for them); normal users keep the PocketID redirect.
        presented_basic = bool(_headers_get(headers, "authorization"))
        if self.config.mode == "basic" or (self.config.break_glass_enabled and presented_basic):
            return AuthDecision(
                action="unauthorized",
                status_code=401,
                www_authenticate=BASIC_CHALLENGE,
                detail="Authentication required",
                actor="anonymous",
            )

        # OIDC mode, no valid session: API consumers get a JSON 401, browsers a
        # redirect to the login endpoint.
        if candidate_path.startswith("/api/"):
            return AuthDecision(
                action="unauthorized",
                status_code=401,
                detail="Authentication required",
                actor="anonymous",
            )
        location = "/auth/login"
        if candidate_path not in ("", "/"):
            location += "?next=" + urllib.parse.quote(candidate_path, safe="")
        return AuthDecision(action="redirect_login", location=location, status_code=302, actor="anonymous")

    # -- JIT user provisioning --------------------------------------------
    def record_identity(self, identity: Optional[Identity], *, force: bool = False) -> Optional[Dict[str, Any]]:
        """JIT-provision/refresh the ``users`` row for *identity* (throttled)."""
        if identity is None or self.users is None:
            return None
        key = f"{identity.auth_source}:{identity.subject}"
        now = time.time()
        if not force and (now - self._provisioned_at.get(key, 0.0)) < self.provision_interval:
            return None
        try:
            row = self.users.upsert_user(
                subject=identity.subject,
                username=identity.username,
                email=identity.email,
                display_name=identity.display_name,
                auth_source=identity.auth_source,
            )
        except Exception as exc:  # pragma: no cover - DB failure must not break auth
            logger.warning("Failed to JIT-provision user '%s': %s", identity.subject, exc)
            return None
        self._provisioned_at[key] = now
        return row

    # -- OIDC login flow ---------------------------------------------------
    def _require_oidc(self) -> OIDCClient:
        if self.config.mode != "oidc" or self.oidc is None:
            raise AuthError("OIDC login is only available when MANGATRAX_AUTH_MODE=oidc")
        return self.oidc

    def start_login(self, next_path: str = "/") -> LoginStart:
        oidc = self._require_oidc()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _b64u_encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
        state_cookie = self.state_tokens.sign(
            {
                "state": state,
                "nonce": nonce,
                "verifier": code_verifier,
                "next": sanitize_next_path(next_path),
            }
        )
        authorization_url = oidc.build_authorization_url(state, nonce, code_challenge)
        return LoginStart(authorization_url=authorization_url, state_cookie=state_cookie)

    def complete_login(
        self,
        code: Optional[str],
        state: Optional[str],
        state_cookie: Optional[str],
        *,
        now: Optional[float] = None,
    ) -> LoginResult:
        oidc = self._require_oidc()
        if not code:
            raise AuthError("OIDC callback did not include an authorization code")
        payload = self.state_tokens.verify(state_cookie, now=now)
        if not payload:
            raise AuthError("OIDC state cookie is missing, expired or has been tampered with")
        stored_state = str(payload.get("state") or "")
        if not state or not _constant_time_equals(str(state), stored_state):
            raise TokenValidationError("OIDC state parameter mismatch (possible CSRF)")
        code_verifier = str(payload.get("verifier") or "")
        if not code_verifier:
            raise AuthError("OIDC state cookie is missing the PKCE verifier")
        stored_nonce = str(payload.get("nonce") or "")

        token_response = oidc.exchange_code(code, code_verifier)
        claims = oidc.validate_id_token(token_response.get("id_token"), expected_nonce=stored_nonce, now=now)
        identity = Identity.from_oidc_claims(claims)
        self.record_identity(identity, force=True)
        session_cookie = self.session_tokens.sign(identity.to_session(), now=now)
        redirect_url = sanitize_next_path(str(payload.get("next") or "/"))
        return LoginResult(identity=identity, session_cookie=session_cookie, redirect_url=redirect_url)

    def logout(self) -> LogoutResult:
        target = None
        if self.config.mode == "oidc" and self.oidc is not None:
            try:
                target = self.oidc.end_session_url()
            except AuthProviderUnavailable as exc:  # pragma: no cover - defensive
                logger.warning("OIDC logout redirect unavailable: %s", exc)
        return LogoutResult(redirect_url=target)

    # -- diagnostics -------------------------------------------------------
    def health_payload(self) -> Dict[str, Any]:
        return {"status": "ok", "auth_mode": self.config.mode}
