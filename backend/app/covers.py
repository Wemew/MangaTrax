import asyncio
import os
import hashlib
import logging
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urljoin

from app import security

logger = logging.getLogger("mangatrax.covers")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/jpeg,image/png,*/*;q=0.8",
}

# Security limits
MAX_COVER_BYTES = 5 * 1024 * 1024  # 5 MB maximum image size
DOWNLOAD_TIMEOUT_SECONDS = 7.0
MAX_REDIRECTS = 3
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
STREAM_CHUNK_SIZE = 8192


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Stops urllib from transparently following redirects.

    Each hop must be re-validated against the SSRF guard before it is fetched, so
    redirects are surfaced to :meth:`CoverCache._download_to_cache` via
    ``urllib.error.HTTPError`` and followed manually there.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urlopen(req: urllib.request.Request, timeout: float):
    """Open ``req`` without auto-following redirects (patched in tests)."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(req, timeout=timeout)


def is_safe_public_url(url: str) -> bool:
    """
    Prevents Server-Side Request Forgery (SSRF) for cover fetches.

    Delegates to the centralized :func:`app.security.validate_public_url` strict
    check (scheme + hostname + DNS resolution re-validation of every resolved
    address), which also defeats DNS-rebinding payloads.  Kept as a thin wrapper
    because this name is part of the pre-M3 cover-cache API.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        security.validate_public_url(url)
        return True
    except security.UnsafeURLError as exc:
        logger.warning(f"SSRF Protection blocked cover URL {url}: {exc}")
        return False

def detect_valid_image(data: bytes) -> Optional[str]:
    """
    Verifies image authenticity by inspecting file magic bytes.
    Guarantees that files downloaded to disk are legitimate raster images.
    """
    if len(data) < 16:
        return None

    # JPEG: FF D8 FF
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    # WEBP: RIFF .... WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"

    # GIF: GIF87a or GIF89a
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"

    return None

def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

class CoverCache:
    def __init__(self, data_dir: str):
        self.covers_dir = os.path.join(data_dir, "covers")
        os.makedirs(self.covers_dir, exist_ok=True)

    def get_filename(self, url: str) -> str:
        """Derive a safe, unguessable filename based on SHA-256 of the URL."""
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return f"{url_hash}.jpg"

    def get_path(self, url: str) -> str:
        return os.path.join(self.covers_dir, self.get_filename(url))

    def clear_cache(self) -> int:
        """Removes all cached cover images from disk. Returns count of files removed."""
        count = 0
        if os.path.isdir(self.covers_dir):
            for fname in os.listdir(self.covers_dir):
                fpath = os.path.join(self.covers_dir, fname)
                try:
                    if os.path.isfile(fpath):
                        os.remove(fpath)
                        count += 1
                except Exception as e:
                    logger.warning(f"Error removing cached cover {fpath}: {e}")
        return count

    async def get_or_download(self, url: str) -> Optional[str]:
        """
        Safely fetches and caches a cover image.

        The blocking socket I/O runs in a worker thread so the event loop is never
        stalled; the download itself is the same routine used by
        :meth:`cache_sync` (per-hop SSRF validation, explicit timeout, streamed
        size cap and magic-byte validation).
        """
        if not url:
            return None
        return await asyncio.to_thread(self._download_to_cache, url)

    def cache_sync(self, url: str) -> Optional[str]:
        """Synchronous version for background tasks with multi-hop SSRF and magic byte checks."""
        if not url:
            return None
        return self._download_to_cache(url)

    def _download_to_cache(self, url: str) -> Optional[str]:
        """Download ``url`` into the cache, validating every redirect hop.

        Preserves the pre-M3 behaviour exactly: the initial URL *and* every
        redirect target are SSRF-validated before they are fetched, the total
        number of hops is capped at ``MAX_REDIRECTS``, the socket timeout is
        explicit, the body is streamed and capped at ``MAX_COVER_BYTES`` (both
        from ``Content-Length`` and while reading), and only real raster image
        magic bytes are written to the cache.
        """
        filepath = self.get_path(url)
        if os.path.isfile(filepath) and os.path.getsize(filepath) > 0:
            return filepath

        temp_path = f"{filepath}.tmp"
        current_url = url

        try:
            for _redirect_idx in range(MAX_REDIRECTS + 1):
                if not is_safe_public_url(current_url):
                    logger.warning(f"Blocked unsafe/internal cover URL or redirect target: {current_url}")
                    return None

                req = urllib.request.Request(current_url, headers=HEADERS)
                try:
                    resp = _urlopen(req, DOWNLOAD_TIMEOUT_SECONDS)
                except urllib.error.HTTPError as exc:
                    code = exc.code
                    location = exc.headers.get("Location") if exc.headers else None
                    try:
                        exc.close()
                    except Exception:
                        pass
                    if code in REDIRECT_STATUSES and location:
                        current_url = urljoin(current_url, location)
                        continue
                    logger.warning(f"Failed to fetch image from {current_url}: HTTP {code}")
                    return None

                try:
                    status = getattr(resp, "status", None)
                    if status is None:
                        status = resp.getcode()
                    if status in REDIRECT_STATUSES:
                        # Defensive: a custom handler may surface a redirect response.
                        location = resp.headers.get("Location") if resp.headers else None
                        if not location:
                            return None
                        current_url = urljoin(current_url, location)
                        continue

                    if status != 200:
                        logger.warning(f"Failed to fetch image from {current_url}: HTTP {status}")
                        return None

                    content_length = resp.headers.get("Content-Length") or resp.headers.get("content-length")
                    if content_length and int(content_length) > MAX_COVER_BYTES:
                        logger.warning(f"Image too large ({content_length} bytes) for {current_url}")
                        return None

                    header_chunk = b""
                    downloaded_bytes = 0

                    with open(temp_path, "wb") as f:
                        while True:
                            chunk = resp.read(STREAM_CHUNK_SIZE)
                            if not chunk:
                                break
                            downloaded_bytes += len(chunk)
                            if downloaded_bytes > MAX_COVER_BYTES:
                                logger.warning(f"Image exceeded max byte limit during stream: {current_url}")
                                f.close()
                                _remove_quietly(temp_path)
                                return None

                            if len(header_chunk) < 32:
                                header_chunk += chunk

                            f.write(chunk)

                    # Validate magic bytes
                    image_type = detect_valid_image(header_chunk)
                    if not image_type:
                        logger.warning(f"Rejecting non-image format downloaded from {current_url}")
                        _remove_quietly(temp_path)
                        return None

                    # Atomic move into final cache location
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    os.rename(temp_path, filepath)
                    return filepath
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass

            return None

        except Exception as e:
            logger.debug(f"Cover download error for {url}: {e}")
            _remove_quietly(temp_path)
            return None
