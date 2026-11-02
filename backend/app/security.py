"""Centralized outbound-URL validation, SSRF defense, path containment and SQL escaping.

This module is deliberately stdlib-only so it can be imported by every layer
(web routes, providers, notifier, database helper, background scheduler) without
pulling in a web framework or third-party HTTP client.

Threat model
------------
* **SSRF (M3 feature 18)** - user-supplied URLs are fetched server-side by the
  cover cache, the feed providers and the webhook notifier.  ``validate_public_url()``
  is the *strict* check (scheme + hostname + DNS resolution re-check of every
  resolved address, fail-closed) and ``validate_outbound_url()`` is the
  *structural* check (same scheme/literal-host rules, no DNS).
  The split is intentional:
    - The strict variant is used where a user explicitly asks the server to
      probe a URL from the API surface (``/api/feeds/test``,
      ``/api/settings/test-webhook``) and on every redirect hop of the cover
      cache (already enforced there before this module existed).
    - The structural variant is used in the *background* fetch/dispatch paths
      (feed polling, notification delivery) because those URLs are persisted
      configuration and the test contract for this repository requires the
      offline/embedded-Python suites to produce identical results without DNS.
  Both variants reject non-http(s) schemes, credentials in the URL, literal
  loopback/RFC1918/link-local/RFC3927-metadata/multicast/reserved addresses,
  ``localhost`` style names and single-label (intranet) hostnames.
* **Stored XSS (M3 feature 20)** - ``is_safe_href()``/``safe_url_filter()`` only
  allow absolute http(s) links, so ``javascript:``/``data:``/``vbscript:``/
  ``file:`` values can neither be saved as a series website URL nor rendered as
  an ``href``.
* **Path traversal & probe defense (M3 feature 21)** - ``ensure_path_within_roots()``/
  ``validate_directory_setting()`` reject NUL bytes, ``..`` segments, relative
  paths and symlink escapes (the resolved real path must stay inside one of the
  allowed roots).
* **SQL hardening (M3 feature 22)** - ``escape_like()``/``like_contains()``
  neutralise ``%``/``_``/``\\`` wildcards so a search term is always matched
  literally (callers must add ``ESCAPE '\\'`` to the SQL).
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.parse
from typing import Iterable, Optional, Sequence

__all__ = [
    "UnsafeURLError",
    "UnsafePathError",
    "ALLOWED_SCHEMES",
    "validate_public_url",
    "validate_outbound_url",
    "is_safe_href",
    "safe_url_filter",
    "validate_stored_website_url",
    "escape_like",
    "like_contains",
    "LIKE_ESCAPE_SQL",
    "ensure_path_within_roots",
    "validate_directory_setting",
    "resolve_path",
    "MAX_URL_LENGTH",
    "MAX_PATH_LENGTH",
]


class UnsafeURLError(ValueError):
    """Raised when a URL is not allow-listed for server-side fetching."""


class UnsafePathError(ValueError):
    """Raised when a path escapes its allowed roots or is malformed."""


MAX_URL_LENGTH = 2048
MAX_PATH_LENGTH = 4096
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Host names that always point at the local machine / cloud metadata services.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "metadata.google.internal.",
        "instance-data",
        "instance-data.ec2.internal",
    }
)

# Address ranges that must never be fetchable from the server.
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",          # "this host"
        "10.0.0.0/8",         # RFC1918 private
        "100.64.0.0/10",      # CGNAT / carrier shared space
        "127.0.0.0/8",        # loopback
        "169.254.0.0/16",     # link-local incl. 169.254.169.254 cloud metadata
        "172.16.0.0/12",      # RFC1918 private
        "192.0.0.0/24",       # IETF protocol assignments
        "192.0.2.0/24",       # TEST-NET-1
        "192.168.0.0/16",     # RFC1918 private
        "198.18.0.0/15",      # benchmarking
        "198.51.100.0/24",    # TEST-NET-2
        "203.0.113.0/24",     # TEST-NET-3
        "224.0.0.0/4",        # multicast
        "240.0.0.0/4",        # reserved + broadcast
        "::/128",             # unspecified IPv6
        "::1/128",            # IPv6 loopback
        "fc00::/7",           # IPv6 unique local (RFC4193)
        "fe80::/10",          # IPv6 link-local
        "ff00::/8",           # IPv6 multicast
        "::ffff:0:0/96",      # IPv4-mapped IPv6 (re-checked as IPv4 below)
    )
)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_TRAVERSAL_SPLIT_RE = re.compile(r"[\\/]+")


# ---------------------------------------------------------------------------
# URL validation / SSRF
# ---------------------------------------------------------------------------
def _normalize_hostname(hostname: str) -> str:
    return (hostname or "").strip().strip(".").strip("[").strip("]").lower()


def is_forbidden_ip(value) -> bool:
    """Return True if *value* (ipaddress object or string) is a non-public address."""
    try:
        ip = value if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ipaddress.ip_address(str(value).strip())
    except ValueError:
        # Unparseable address: fail closed.
        return True

    # IPv4-mapped IPv6 (::ffff:127.0.0.1) must be judged by its IPv4 semantics.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    for network in _BLOCKED_NETWORKS:
        if network.version == ip.version and ip in network:
            return True

    # Defence in depth: Python's own classification flags (covers future ranges).
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_hostname_addresses(hostname: str, port: Optional[int]) -> Sequence[str]:
    """Resolve *hostname* and return every address (IPv4 + IPv6) as strings.

    Kept as a module-level function so tests can patch DNS resolution without
    touching the network.  ``socket.gaierror`` propagates to the caller, which
    converts it into a fail-closed ``UnsafeURLError``.
    """
    infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    addresses = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and sockaddr[0] and sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    return addresses


def _check_url_structure(url: str) -> urllib.parse.SplitResult:
    if not isinstance(url, str):
        raise UnsafeURLError("URL must be a string")
    candidate = url.strip()
    if not candidate:
        raise UnsafeURLError("URL must not be empty")
    if len(candidate) > MAX_URL_LENGTH:
        raise UnsafeURLError(f"URL exceeds the {MAX_URL_LENGTH} character limit")
    if _CONTROL_CHARS_RE.search(candidate) or any(ch.isspace() for ch in candidate):
        raise UnsafeURLError("URL contains whitespace or control characters")

    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError as exc:  # pragma: no cover - urlsplit rarely raises
        raise UnsafeURLError(f"Malformed URL: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"URL scheme '{parsed.scheme}' is not allowed (http/https only)")
    if not parsed.netloc:
        raise UnsafeURLError("URL is missing a host")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URL must not embed credentials")

    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError(f"URL has an invalid host or port: {exc}") from exc
    if not hostname:
        raise UnsafeURLError("URL is missing a host")

    normalized = _normalize_hostname(hostname)
    if normalized in _BLOCKED_HOSTNAMES or normalized.endswith(".localhost"):
        raise UnsafeURLError(f"URL host '{hostname}' is a loopback service")

    try:
        literal_ip = ipaddress.ip_address(normalized)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if is_forbidden_ip(literal_ip):
            raise UnsafeURLError(f"URL host '{hostname}' is a private/loopback/metadata address")
    elif "." not in normalized:
        # Single-label names ("intranet", "qbittorrent") resolve through the
        # local search domain and are virtually always internal services.
        raise UnsafeURLError(f"URL host '{hostname}' is not a public fully-qualified domain name")

    return parsed


def validate_public_url(url: str, *, resolve_dns: bool = True, allow_private: bool = False) -> str:
    """Validate that *url* is a public http(s) URL and return it normalised.

    ``resolve_dns=True`` (the strict/default mode) additionally resolves the
    hostname and re-checks every resolved address, which also defeats
    DNS-rebinding style payloads where a public-looking name points at
    127.0.0.1 / 169.254.169.254 / RFC1918 space.  Resolution failures fail
    closed (``UnsafeURLError``) instead of silently allowing the request.

    Raises ``UnsafeURLError`` on any violation.
    """
    parsed = _check_url_structure(url)
    if allow_private:
        return url.strip()

    if resolve_dns:
        hostname = _normalize_hostname(parsed.hostname or "")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            addresses = resolve_hostname_addresses(hostname, port)
        except (socket.gaierror, UnicodeError, OSError) as exc:
            raise UnsafeURLError(f"Could not resolve URL host '{hostname}': {exc}") from exc
        if not addresses:
            raise UnsafeURLError(f"URL host '{hostname}' did not resolve to any address")
        for address in addresses:
            if is_forbidden_ip(address):
                raise UnsafeURLError(
                    f"URL host '{hostname}' resolves to a non-public address ({address})"
                )
    return url.strip()


def validate_outbound_url(url: str) -> str:
    """Structural (no DNS) variant of :func:`validate_public_url`.

    Used by background fetch/dispatch paths where persisted configuration is
    re-validated cheaply.  All scheme/host/literal-address rules still apply.
    """
    return validate_public_url(url, resolve_dns=False)


# ---------------------------------------------------------------------------
# Stored XSS: href scheme allow-listing
# ---------------------------------------------------------------------------
def is_safe_href(url: Optional[str]) -> bool:
    """True only for absolute http(s) URLs (http:// or https://)."""
    if not url or not isinstance(url, str):
        return False
    candidate = url.strip()
    if not candidate or candidate != url:
        return False
    if len(candidate) > MAX_URL_LENGTH:
        return False
    if _CONTROL_CHARS_RE.search(candidate) or any(ch.isspace() for ch in candidate):
        return False
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return False
    if (parsed.scheme or "").lower() not in ALLOWED_SCHEMES:
        return False
    if not parsed.netloc or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    return True


def safe_url_filter(url: Optional[str], fallback: str = "#") -> str:
    """Jinja ``safe_url`` filter: returns the URL only when its scheme is http(s)."""
    return url if is_safe_href(url) else fallback


def validate_stored_website_url(url: Optional[str]) -> str:
    """Validate a user-supplied ``website_url`` before it is persisted.

    Empty values are allowed (they clear the link).  Anything else must be an
    absolute http(s) URL; ``javascript:``/``data:``/``file:`` payloads are
    rejected with :class:`UnsafeURLError`.
    """
    if url is None:
        return ""
    candidate = str(url).strip()
    if not candidate:
        return ""
    if not is_safe_href(candidate):
        raise UnsafeURLError("website_url must be an absolute http:// or https:// URL")
    return candidate


# ---------------------------------------------------------------------------
# SQL LIKE hardening
# ---------------------------------------------------------------------------
LIKE_ESCAPE_SQL = "\\"


def escape_like(value: str) -> str:
    """Escape SQL ``LIKE`` wildcards so the term is matched literally."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(value: str) -> str:
    """Return a ``%term%`` LIKE pattern with wildcards escaped.

    Callers must append ``ESCAPE '\\'`` to the LIKE clause.
    """
    return f"%{escape_like(value)}%"


# ---------------------------------------------------------------------------
# Path traversal / containment (M3 feature 21)
# ---------------------------------------------------------------------------
def resolve_path(path: str) -> str:
    """Canonicalise *path*: expand ``~``, make absolute, resolve symlinks."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def _check_path_shape(path: str) -> str:
    if not isinstance(path, str):
        raise UnsafePathError("path must be a string")
    candidate = path.strip()
    if not candidate:
        raise UnsafePathError("path must not be empty")
    if len(candidate) > MAX_PATH_LENGTH:
        raise UnsafePathError(f"path exceeds the {MAX_PATH_LENGTH} character limit")
    if "\x00" in candidate:
        raise UnsafePathError("path contains a NUL byte")
    parts = [part for part in _TRAVERSAL_SPLIT_RE.split(candidate) if part]
    if any(part == ".." for part in parts):
        raise UnsafePathError("path must not contain '..' traversal segments")
    expanded = os.path.expanduser(candidate)
    # ``ntpath.isabs('/custom/staging')`` is False on Windows even though the
    # path is rooted; accept either a native absolute path or a rooted path.
    if not os.path.isabs(expanded) and not expanded.startswith(("/", "\\")):
        raise UnsafePathError("path must be absolute")
    return candidate


def _is_within(candidate: str, root: str) -> bool:
    candidate_norm = os.path.normcase(candidate)
    root_norm = os.path.normcase(root)
    try:
        return os.path.commonpath([candidate_norm, root_norm]) == root_norm
    except ValueError:
        # Different drives on Windows.
        return False


def ensure_path_within_roots(path: str, roots: Iterable[str]) -> str:
    """Return the resolved *path* only if it lives inside one of *roots*.

    Rejects empty/relative paths, NUL bytes, ``..`` segments and symlink
    escapes (the ``realpath`` target must still be inside an allowed root).
    """
    candidate = _check_path_shape(path)
    resolved = resolve_path(candidate)

    checked_roots = []
    for root in roots or ():
        if not root:
            continue
        try:
            root_candidate = _check_path_shape(str(root))
        except UnsafePathError:
            # An invalid configured root must not grant access.
            continue
        checked_roots.append(resolve_path(root_candidate))

    if not checked_roots:
        raise UnsafePathError("no allowed path roots are configured")

    for root in checked_roots:
        if _is_within(resolved, root):
            return resolved
    raise UnsafePathError("path is outside the allowed directory roots")


def validate_directory_setting(path: str, *, setting_name: str = "path") -> str:
    """Validate a ``calibre_path``/``staging_dir`` style setting.

    Blocks traversal/NUL/relative values and resolves symlinks; the caller may
    additionally enforce :func:`ensure_path_within_roots` against the deployment
    roots (see ``/api/calibre/test``).
    """
    candidate = _check_path_shape(path)
    resolved = resolve_path(candidate)
    if os.path.exists(resolved) and not os.path.isdir(resolved):
        raise UnsafePathError(f"{setting_name} exists but is not a directory: {resolved}")
    return resolved
