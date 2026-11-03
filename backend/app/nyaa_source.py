"""Single source of truth for the Nyaa/tracker base URL (mirror support).

The user's network blocks ``nyaa.si`` by DNS (twelve99 at Stockholm IX), so they run
their own passthrough mirror/proxy (e.g. ``https://wemew-nyaa.vercel.app/``).  Every
Nyaa URL this app builds -- HTML crawl pages used by backfill, post view URLs,
``.torrent`` URLs and relative-href joins -- must come from this configurable base,
never from a hardcoded host.

Resolution order (first non-empty wins):
    1. per-feed ``config["base_url"]``
    2. the ``nyaa_base_url`` application setting
    3. :data:`DEFAULT_NYAA_BASE_URL` (``https://nyaa.si``, for backwards compatibility)

Normalisation rules (``normalize_nyaa_base_url``):
    * the value is stripped; empty input is rejected;
    * it must be an absolute ``http``/``https`` URL with a host (bare bases and full
      Nyaa-style URLs such as ``https://mirror/?page=rss&c=3_1&f=0`` are both accepted);
    * embedded credentials (``user:pass@host``) and non-numeric/malformed ports are
      rejected;
    * any query string and fragment are DROPPED (the provider builds its own
      ``?page=rss...`` / ``?p=N&c=3_1...`` parameters);
    * the host is lower-cased; IPv6 literals keep their brackets; default ports
      (80/http, 443/https) are dropped, other explicit ports are kept;
    * a path prefix is preserved without a trailing slash (``https://host/nyaa``),
      ``.``/``..`` path segments are rejected;
    * structural SSRF validation runs through ``security.validate_public_url(...,
      resolve_dns=False)``: non-public schemes, loopback/private/metadata literal hosts
      and single-label intranet names are rejected.  DNS is resolved later by the
      normal fetch paths, keeping this helper offline-friendly.
"""

import logging
import urllib.parse

from app import security

logger = logging.getLogger("mangatrax.nyaa_source")

DEFAULT_NYAA_BASE_URL = "https://nyaa.si"


class NyaaBaseURLError(ValueError):
    """Raised when a Nyaa base URL is missing, malformed or not publicly usable."""


def normalize_nyaa_base_url(value) -> str:
    """Normalise a bare base or full Nyaa-style URL; see the module docstring."""
    if value is None:
        raise NyaaBaseURLError("Nyaa base URL must not be empty")
    raw = str(value).strip()
    if not raw:
        raise NyaaBaseURLError("Nyaa base URL must not be empty")

    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise NyaaBaseURLError(f"Malformed Nyaa base URL: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise NyaaBaseURLError("Nyaa base URL must use http:// or https://")
    if parsed.username or parsed.password:
        raise NyaaBaseURLError("Nyaa base URL must not embed credentials")

    try:
        hostname = (parsed.hostname or "").strip().lower()
        port = parsed.port
    except ValueError as exc:
        raise NyaaBaseURLError(f"Nyaa base URL has an invalid host or port: {exc}") from exc
    if not hostname:
        raise NyaaBaseURLError("Nyaa base URL is missing a host")

    if ":" in hostname:  # IPv6 literal
        host_part = f"[{hostname}]"
    else:
        host_part = hostname
    default_port = 80 if scheme == "http" else 443
    netloc = host_part if port in (None, default_port) else f"{host_part}:{port}"

    path_prefix = (parsed.path or "").strip("/")
    if path_prefix:
        segments = path_prefix.split("/")
        if any(segment in (".", "..") for segment in segments):
            raise NyaaBaseURLError("Nyaa base URL path must not contain '.' or '..' segments")
        path_prefix = "/" + "/".join(segments)

    base = f"{scheme}://{netloc}{path_prefix}"

    try:
        security.validate_public_url(base, resolve_dns=False)
    except security.UnsafeURLError as exc:
        raise NyaaBaseURLError(f"Unsafe Nyaa base URL: {exc}") from exc
    return base


def resolve_nyaa_base_url(feed_config=None, setting_value=None) -> str:
    """Resolve the effective base: per-feed config -> setting -> default.

    Invalid candidates are logged and skipped so a bad feed config can never take
    the whole tracker offline; the default is the last-resort fallback.
    """
    config = feed_config if isinstance(feed_config, dict) else {}
    for candidate, origin in (
        (config.get("base_url"), "feed config"),
        (setting_value, "nyaa_base_url setting"),
    ):
        if not candidate:
            continue
        try:
            return normalize_nyaa_base_url(candidate)
        except NyaaBaseURLError as exc:
            logger.warning("Ignoring invalid Nyaa base URL from %s: %s", origin, exc)
    return DEFAULT_NYAA_BASE_URL
