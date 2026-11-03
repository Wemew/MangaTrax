"""Torbox debrid download engine (task-20, Phase 1+2).

Stdlib-only client for https://api.torbox.app/v1/api.  A release is submitted as a
torrent (magnet preferred, .torrent upload fallback), polled through ``mylist`` with
``bypass_cache=true`` (the list cache refreshes only every 600 s), then downloaded to
the staging folder via ``requestdl`` links.  Downloads are streamed with the same
hardened pattern as the cover cache: every URL and redirect hop passes the SSRF guard,
timeouts are explicit, partial data goes to ``.part`` and is atomically renamed, sizes
are capped, and zip archives are extracted with zip-slip/symlink protection.

Secrets: the API key is only ever sent in the ``Authorization`` header (or the
``token=`` query parameter of ``requestdl``); it is never logged, included in error
messages, or persisted anywhere outside ``app_settings``.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from app import security
from app.downloader.base import (
    DownloaderAuthError,
    DownloaderClient,
    DownloaderError,
    DownloaderFileError,
    DownloaderNotFoundError,
    DownloaderRateLimitError,
    DownloaderSlotError,
    extract_torrent_hash,
)

logger = logging.getLogger("mangatrax.downloader.torbox")

TORBOX_API_BASE = "https://api.torbox.app/v1/api"
REDACTED_SECRET = "'***'"
TORBOX_API_TIMEOUT_SECONDS = 30.0
TORBOX_STREAM_TIMEOUT_SECONDS = 60.0
TORBOX_STREAM_CHUNK_BYTES = 1024 * 256
TORBOX_MAX_FILE_BYTES = 16 * 1024 * 1024 * 1024  # 16 GiB sanity cap for one file
TORBOX_MAX_TORRENT_BYTES = 10 * 1024 * 1024  # .torrent metadata sanity cap
TORBOX_MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

# Torbox torrent states -> normalised engine states.
_READY_STATES = {"completed", "cached", "uploading", "download finished"}
_DOWNLOADING_STATES = {"downloading", "metadl", "checkingresumedata", "checking"}
_STALLED_STATES = {"stalled (no seeds)", "stalled", "paused", "queued", "metadl waiting"}
_FAILED_STATES = {"error", "failed", "missingfiles", "dead"}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Return redirect responses instead of following them blindly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _open_no_redirect(req: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(req, timeout=timeout)


def _retry_after_seconds(headers) -> Optional[float]:
    if not headers:
        return None
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
            seconds = when.timestamp() - time.time()
        except Exception:
            return None
    return max(0.0, min(float(seconds), 600.0))


def _release_value(release: Any, *names: str) -> Any:
    for name in names:
        value = release.get(name) if isinstance(release, dict) else getattr(release, name, None)
        if value:
            return value
    return None


def _safe_basename(name: str) -> str:
    """Filesystem-safe basename; rejects traversal and control characters."""
    candidate = str(name or "").replace("\\", "/").split("/")[-1].strip()
    candidate = candidate.replace("\x00", "")
    if candidate in ("", ".", ".."):
        raise DownloaderFileError(f"Unsafe file name from Torbox: {name!r}")
    return candidate


def _resolve_within(base_dir: str, name: str) -> str:
    """Join *name* under *base_dir* and guarantee the result stays inside it."""
    root = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(root, name))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise DownloaderFileError(f"Unsafe path outside the download folder: {name!r}")
    return candidate


class TorboxDownloader:
    """Torbox implementation of :class:`DownloaderClient`."""

    name = "torbox"

    def __init__(
        self,
        api_key: str,
        base_url: str = TORBOX_API_BASE,
        enabled: bool = True,
        delete_after_download: bool = True,
        timeout: float = TORBOX_API_TIMEOUT_SECONDS,
        max_file_bytes: int = TORBOX_MAX_FILE_BYTES,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or TORBOX_API_BASE).rstrip("/")
        self.enabled = bool(enabled) and bool(self.api_key)
        self.delete_after_download = bool(delete_after_download)
        self.timeout = float(timeout or TORBOX_API_TIMEOUT_SECONDS)
        self.max_file_bytes = int(max_file_bytes or TORBOX_MAX_FILE_BYTES)

    def __repr__(self) -> str:
        """Masked representation: ``%r``/tracebacks can never leak the API key."""
        return (
            f"TorboxDownloader(api_key={REDACTED_SECRET}, enabled={self.enabled}, "
            f"base_url={self.base_url!r}, delete_after_download={self.delete_after_download})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    # -- HTTP plumbing -------------------------------------------------------
    def _safe_label(self, path: str) -> str:
        return path.split("?", 1)[0]

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        raw_body: Optional[bytes] = None,
        content_type: str = "application/json",
        token_query: bool = False,
        timeout: Optional[float] = None,
    ) -> Any:
        """Call the Torbox API and return the ``data`` payload.

        Raises only typed :class:`DownloaderError` subclasses whose messages never
        contain the API key (the requestdl URL contains ``token=`` and is never
        echoed).
        """
        if not self.enabled:
            raise DownloaderAuthError(
                "Torbox is not enabled or no API key is configured (Settings → Downloader)."
            )
        query = dict(params or {})
        if token_query:
            query["token"] = self.api_key
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        try:
            security.validate_public_url(self.base_url, resolve_dns=False)
        except security.UnsafeURLError as exc:
            raise DownloaderError(f"Torbox API base URL is not allowed: {exc}") from exc

        headers = {
            "User-Agent": "MangaTrax/1.0",
            "Accept": "application/json",
        }
        if not token_query:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
        elif raw_body is not None:
            data = raw_body
        headers["Content-Type"] = content_type

        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        label = self._safe_label(path)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
            retry_after = _retry_after_seconds(getattr(exc, "headers", None))
            detail = ""
            try:
                payload = json.loads(exc.read().decode("utf-8", errors="replace"))
                detail = str(payload.get("detail") or payload.get("error") or "").strip()
            except Exception:
                detail = ""
            suffix = f" ({detail})" if detail else ""
            if code == 401 or code == 403:
                raise DownloaderAuthError(
                    f"Torbox rejected the API key (HTTP {code}) calling {label}{suffix}."
                ) from None
            if code == 429:
                raise DownloaderRateLimitError(
                    f"Torbox rate limit hit (HTTP 429) calling {label}; retry in "
                    f"{retry_after if retry_after is not None else 30:.0f}s.",
                    retry_after=retry_after,
                ) from None
            if code == 404:
                raise DownloaderNotFoundError(f"Torbox resource not found calling {label}{suffix}.") from None
            if code in (409, 402) or "slot" in detail.lower() or "concurrent" in detail.lower():
                raise DownloaderSlotError(
                    f"Torbox has no free active slot for {label}{suffix}. "
                    "Wait for an active download to finish or delete one in Torbox."
                ) from None
            raise DownloaderError(f"Torbox HTTP {code} calling {label}{suffix}.") from None
        except DownloaderError:
            raise
        except Exception as exc:
            raise DownloaderError(f"Torbox connection error calling {label}: {exc}") from exc

        try:
            payload = json.loads(body) if body else {}
        except ValueError as exc:
            raise DownloaderError(f"Torbox returned invalid JSON calling {label}.") from exc
        if isinstance(payload, dict) and payload.get("success") is False:
            message = str(payload.get("detail") or payload.get("error") or "unknown error")
            lowered = message.lower()
            if "api key" in lowered or "token" in lowered or "unauthor" in lowered:
                raise DownloaderAuthError(f"Torbox authentication failed: {message}")
            if "slot" in lowered or "concurrent" in lowered:
                raise DownloaderSlotError(f"Torbox slot limit reached: {message}")
            raise DownloaderError(f"Torbox error calling {label}: {message}")
        if isinstance(payload, dict) and "data" in payload:
            return payload.get("data")
        return payload

    @staticmethod
    def _multipart_body(
        boundary: str,
        fields: Optional[Dict[str, str]] = None,
        file_field: Optional[str] = None,
        filename: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
    ) -> bytes:
        """Build a multipart/form-data body (text fields and/or one file part)."""
        parts: List[bytes] = []
        for key, value in (fields or {}).items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
            )
        if file_field:
            parts.append(
                (
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                    f"filename=\"{filename or 'release.torrent'}\"\r\n"
                    "Content-Type: application/x-bittorrent\r\n\r\n"
                ).encode("utf-8")
                + (file_bytes or b"")
                + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(parts)

    def _post_multipart_fields(self, path: str, fields: Dict[str, str]) -> Any:
        """POST a fields-only multipart/form-data body (Torbox createtorrent magnet)."""
        boundary = f"----MangaTrax{int(time.time() * 1000)}"
        body = self._multipart_body(boundary, fields=fields)
        return self._request(
            "POST", path, raw_body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )

    def _post_multipart(
        self, path: str, fields: Dict[str, str], file_field: str, filename: str, file_bytes: bytes,
    ) -> Any:
        boundary = f"----MangaTrax{int(time.time() * 1000)}"
        body = self._multipart_body(
            boundary, fields=fields, file_field=file_field, filename=filename, file_bytes=file_bytes,
        )
        return self._request(
            "POST", path, raw_body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )

    # -- Cache / submission --------------------------------------------------
    def _check_cached(self, infohash: str) -> bool:
        data = self._request(
            "GET", "/torrents/checkcached",
            params={"hash": infohash, "format": "list", "list_files": "true"},
        )
        if isinstance(data, dict):
            if infohash in data:
                return True
            return any(str(entry.get("hash") or "").lower() == infohash for entry in data.values()
                       if isinstance(entry, dict))
        if isinstance(data, list):
            return any(str(entry).lower() == infohash for entry in data)
        return False

    def _fetch_torrent_url_once(self, url: str) -> bytes:
        """Fetch a .torrent body, validating every URL/redirect hop (no key involved)."""
        current_url = url
        for _hop in range(TORBOX_MAX_REDIRECTS + 1):
            try:
                security.validate_public_url(current_url, resolve_dns=False)
            except security.UnsafeURLError as exc:
                raise DownloaderError(f"Unsafe torrent URL ({exc})") from exc
            request = urllib.request.Request(
                current_url,
                headers={"User-Agent": "MangaTrax/1.0", "Accept": "application/x-bittorrent,*/*"},
            )
            try:
                response = _open_no_redirect(request, self.timeout)
            except urllib.error.HTTPError as exc:
                location = exc.headers.get("Location") if exc.headers else None
                try:
                    exc.close()
                except Exception:
                    pass
                if exc.code in REDIRECT_STATUSES and location:
                    current_url = urllib.parse.urljoin(current_url, location)
                    continue
                raise DownloaderError(f"HTTP {exc.code}") from None
            except Exception as exc:
                raise DownloaderError(str(exc)) from exc
            try:
                status = getattr(response, "status", None) or 200
                if status in REDIRECT_STATUSES:
                    location = response.headers.get("Location") if response.headers else None
                    if not location:
                        raise DownloaderError("redirect without a Location header")
                    current_url = urllib.parse.urljoin(current_url, location)
                    continue
                if status != 200:
                    raise DownloaderError(f"HTTP {status}")
                data = response.read(TORBOX_MAX_TORRENT_BYTES + 1)
                if len(data) > TORBOX_MAX_TORRENT_BYTES:
                    raise DownloaderError("torrent file exceeds the size cap")
                if not data:
                    raise DownloaderError("empty response body")
                return data
            finally:
                try:
                    response.close()
                except Exception:
                    pass
        raise DownloaderError("too many redirects")

    def _fetch_torrent_bytes(self, url: str) -> bytes:
        """Fetch + validate a bencoded torrent; one retry with backoff on failure."""
        last_error: Optional[DownloaderError] = None
        for attempt in (1, 2):
            try:
                data = self._fetch_torrent_url_once(url)
            except DownloaderError as exc:
                last_error = exc
                if attempt == 1:
                    logger.warning(
                        "Torrent fetch from %s failed (attempt 1/2): %s; retrying in 1s.", url, exc,
                    )
                    time.sleep(1.0)
                    continue
                break
            if not self._looks_like_torrent(data):
                raise DownloaderFileError(
                    f"'{url}' did not return a bencoded .torrent file (likely an HTML/JSON page)."
                )
            return data
        raise DownloaderError(f"Could not fetch the .torrent file from {url} after 2 attempts: {last_error}")

    @staticmethod
    def _looks_like_torrent(data: bytes) -> bool:
        """A bencoded torrent starts with ``d`` and contains the ``4:info`` key."""
        if not data or data[:1] != b"d":
            return False
        head = data[:512].lower()
        if b"<html" in head or b"<!doctype" in head or head.lstrip().startswith(b"{"):
            return False
        return b"4:info" in data

    _CANDIDATE_FIELDS = ("download_url", "link", "guid", "magnet", "torrent_url", "torrent_hash")

    def _release_candidates(self, release: Any) -> List[str]:
        values: List[str] = []
        for field in self._CANDIDATE_FIELDS:
            value = _release_value(release, field)
            if value is None:
                continue
            text = str(value).strip()
            if text and text not in values:
                values.append(text)
        return values

    @staticmethod
    def _infohash_from_text(text: str) -> Optional[str]:
        value = str(text or "").strip()
        if not value:
            return None
        infohash = extract_torrent_hash(value)
        if infohash:
            return infohash
        if re.fullmatch(r"[0-9a-fA-F]{40}", value):
            return value.lower()
        if re.fullmatch(r"[A-Za-z2-7]{32}", value):
            return extract_torrent_hash(f"magnet:?xt=urn:btih:{value}")
        return None

    def _resolve_submission(self, release: Any) -> Dict[str, Any]:
        """Resolve a submission form: magnet -> infohash magnet -> torrent URL/file.

        Candidates come from download_url/link/guid/magnet/torrent_url/torrent_hash.
        A ``/view/<id>`` URL is turned into ``<base>/download/<id>.torrent`` before any
        fetch, and the fetched bytes must be a real bencoded torrent.
        """
        candidates = self._release_candidates(release)
        title = str(_release_value(release, "title") or "").strip()
        for candidate in candidates:
            if candidate.lower().startswith("magnet:"):
                return {
                    "form": "magnet", "magnet": candidate,
                    "infohash": extract_torrent_hash(candidate), "tried": candidates,
                }
        for candidate in candidates:
            infohash = self._infohash_from_text(candidate)
            if infohash:
                return {
                    "form": "magnet", "magnet": f"magnet:?xt=urn:btih:{infohash}",
                    "infohash": infohash, "tried": candidates,
                }

        derived_urls: List[str] = []
        torrent_urls: List[str] = []
        for candidate in candidates:
            plain = candidate.split("?", 1)[0]
            match = re.match(r"^(?P<base>.+?)/view/(?P<id>\d+)$", plain)
            if match:
                derived = f"{match.group('base')}/download/{match.group('id')}.torrent"
                derived_urls.append(derived)
                torrent_urls.append(derived)
        for candidate in candidates:
            plain = candidate.split("?", 1)[0]
            if plain.lower().endswith(".torrent") and plain not in torrent_urls:
                torrent_urls.append(plain)

        fetch_errors: List[str] = []
        for url in torrent_urls:
            try:
                data = self._fetch_torrent_bytes(url)
                form = "derived-torrent-url" if url in derived_urls else "torrent-file"
                return {"form": form, "torrent_bytes": data, "torrent_url": url, "tried": candidates}
            except DownloaderError as exc:
                fetch_errors.append(f"{url}: {exc}")

        detail = "; ".join(fetch_errors[:3]) if fetch_errors else "none"
        raise DownloaderError(
            f"Torbox submit for '{title or 'unknown release'}' found no magnet or valid .torrent "
            f"metadata (candidates: {candidates or 'none'}; derived torrent URLs: "
            f"{derived_urls or 'none'}; fetch errors: {detail}). "
            "Attach a magnet link or a working .torrent URL."
        )

    def submit(self, release: Any, savepath: str, wanted_volumes: Optional[List[float]] = None) -> Dict[str, Any]:
        """Create the torrent in Torbox and return the persisted handle dict."""
        title = str(_release_value(release, "title") or "").strip()
        # Resolve a *real* identifier first: magnet -> infohash magnet -> derived
        # /download/<id>.torrent (from /view/<id>) -> validated .torrent bytes.  A view
        # page or HTML/JSON body can never be uploaded as a torrent.
        resolution = self._resolve_submission(release)
        form = str(resolution.get("form") or "magnet")
        infohash = resolution.get("infohash") or extract_torrent_hash(resolution.get("magnet") or "")
        cached = False
        if infohash:
            try:
                cached = self._check_cached(infohash)
            except (DownloaderAuthError, DownloaderRateLimitError):
                # Bad key / hard rate limit: surface it, do not silently continue.
                raise
            except DownloaderError as exc:
                logger.debug("Torbox checkcached failed (continuing with submit): %s", exc)

        if form == "magnet":
            # TorBox expects multipart/form-data with a `magnet` field — the magnet must
            # NOT be a query parameter (the API answers 400 "provide a file or magnet").
            logger.info("Torbox submit for '%s' using submission form 'magnet'.", title)
            data = self._post_multipart_fields("/torrents/createtorrent", {"magnet": resolution["magnet"]})
        else:
            logger.info(
                "Torbox submit for '%s' using submission form '%s' (%s).",
                title, form, resolution.get("torrent_url") or "",
            )
            data = self._post_multipart(
                "/torrents/createtorrent", {}, "file", "release.torrent", resolution["torrent_bytes"],
            )

        if isinstance(data, dict):
            torrent_id = data.get("torrent_id") or data.get("id")
            queued_id = data.get("queued_id")
            created_hash = str(data.get("hash") or infohash or "").lower()
        elif isinstance(data, int):
            torrent_id, queued_id, created_hash = data, None, (infohash or "")
        else:
            torrent_id, queued_id, created_hash = None, None, (infohash or "")

        wanted = [float(v) for v in (wanted_volumes or [])]
        if torrent_id:
            handle = {
                "engine": self.name,
                "torrent_id": int(torrent_id),
                "hash": created_hash,
                "savepath": savepath,
                "wanted_volumes": wanted,
                "cached": bool(cached),
            }
            return {"handle": handle, "state": "ready" if cached else "queued", "cached": bool(cached)}

        if queued_id:
            # The account has no free active slot: Torbox queued the submission.
            handle = {
                "engine": self.name,
                "torrent_id": None,
                "queued_id": int(queued_id),
                "hash": created_hash,
                "savepath": savepath,
                "wanted_volumes": wanted,
                "cached": False,
            }
            return {"handle": handle, "state": "queued_slot", "cached": False, "queued_id": int(queued_id)}

        raise DownloaderError(
            "Torbox did not return a torrent_id or queued_id for the submitted release."
        )

    # -- Status --------------------------------------------------------------
    @staticmethod
    def _normalise_state(raw_state: str, row: Dict[str, Any]) -> str:
        state = str(raw_state or "").strip().lower()
        if row.get("download_finished") or row.get("download_present"):
            return "ready"
        if state in _READY_STATES:
            return "ready"
        if state in _FAILED_STATES:
            return "failed"
        if state in _STALLED_STATES:
            return "stalled"
        if state in _DOWNLOADING_STATES:
            return "downloading"
        return "queued"

    def _queued_rows(self) -> List[Dict[str, Any]]:
        """Fetch the account's queued submissions (``GET /torrents/getqueued``)."""
        data = self._request("GET", "/torrents/getqueued")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and data:
            return [data]
        return []

    @staticmethod
    def _row_queued_id(row: Dict[str, Any]) -> Optional[int]:
        for key in ("queued_id", "id", "torrent_id"):
            try:
                value = int(row.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    @staticmethod
    def _row_torrent_id(row: Dict[str, Any]) -> Optional[int]:
        try:
            value = int(row.get("torrent_id") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _queued_snapshot(self, queued_id: int, infohash: str) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
        """Return (queued row, promoted torrent_id) for a queued submission.

        Torbox activates a queued item by giving it a real ``torrent_id``; in some
        transitions the queued row disappears entirely, so the fallback is to look the
        infohash up in ``mylist``.
        """
        rows = self._queued_rows()
        row = next((item for item in rows if self._row_queued_id(item) == int(queued_id)), None)
        if row is not None:
            promoted = self._row_torrent_id(row)
            if promoted:
                return row, promoted
            return row, None
        if infohash:
            try:
                data = self._request("GET", "/torrents/mylist", params={"bypass_cache": "true"})
            except DownloaderError:
                return None, None
            mylist = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
            for item in mylist:
                if not isinstance(item, dict):
                    continue
                if str(item.get("hash") or "").lower() == infohash.lower():
                    try:
                        found_id = int(item.get("id") or item.get("torrent_id") or 0)
                    except (TypeError, ValueError):
                        found_id = 0
                    if found_id > 0:
                        return None, found_id
        return None, None

    def _queued_status(self, handle: Dict[str, Any]) -> Dict[str, Any]:
        queued_id = int(handle.get("queued_id") or 0)
        infohash = str(handle.get("hash") or "")
        try:
            row, promoted = self._queued_snapshot(queued_id, infohash)
        except DownloaderNotFoundError:
            # A just-submitted queue item may not be visible for a moment; treat it as
            # still queued instead of failing the release.
            row, promoted = None, None
        if promoted:
            # Activated: continue with the normal active-torrent status payload.
            info = self.status({
                "engine": self.name,
                "torrent_id": promoted,
                "hash": infohash,
            })
            info["promoted_torrent_id"] = promoted
            info["queued_id"] = queued_id
            return info
        position = None
        active_limit = None
        if row:
            position = row.get("queue_position") or row.get("position")
            active_limit = row.get("active_limit") or row.get("current_active_downloads")
        return {
            "state": "queued_slot",
            "raw_state": "queued",
            "progress": 0.0,
            "size": int(row.get("size") or 0) if row else 0,
            "download_speed": 0,
            "eta": None,
            "seeds": 0,
            "files": [],
            "cached": False,
            "queued_id": queued_id,
            "queue_position": position,
            "active_limit": active_limit,
            "handle": dict(handle or {}),
        }

    def _find_torrent_row(self, torrent_id: int) -> Dict[str, Any]:
        data = self._request(
            "GET", "/torrents/mylist",
            params={"id": str(torrent_id), "bypass_cache": "true"},
        )
        rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
        row = next((item for item in rows if isinstance(item, dict)
                    and str(item.get("id") or item.get("torrent_id") or "") == str(torrent_id)), None)
        if row is None:
            raise DownloaderNotFoundError(f"Torbox torrent {torrent_id} is no longer in the account.")
        return row

    def status(self, handle: Dict[str, Any]) -> Dict[str, Any]:
        handle = handle or {}
        torrent_id = handle.get("torrent_id")
        if not torrent_id and handle.get("queued_id"):
            return self._queued_status(handle)
        if not torrent_id:
            raise DownloaderNotFoundError("Torbox handle is missing its torrent_id/queued_id.")
        row = self._find_torrent_row(int(torrent_id))
        raw_state = str(row.get("download_state") or row.get("state") or "")
        normalised = self._normalise_state(raw_state, row)
        progress = row.get("progress")
        try:
            progress_value = float(progress)
            if progress_value > 1.0:
                progress_value = progress_value / 100.0
        except (TypeError, ValueError):
            progress_value = 0.0
        files = []
        for idx, item in enumerate(row.get("files") or []):
            if not isinstance(item, dict):
                continue
            files.append({
                "id": item.get("id", idx),
                "name": item.get("name") or item.get("short_name") or "",
                "size": item.get("size") or 0,
            })
        return {
            "state": normalised,
            "raw_state": raw_state,
            "progress": max(0.0, min(1.0, progress_value)),
            "size": row.get("size") or 0,
            "download_speed": row.get("download_speed") or 0,
            "eta": row.get("eta"),
            "seeds": row.get("seeds"),
            "files": files,
            "cached": normalised == "ready" and raw_state.lower() == "cached",
            "handle": dict(handle or {}),
        }

    # -- Downloading ---------------------------------------------------------
    def _requestdl(self, torrent_id: int, file_id: Optional[int] = None) -> str:
        params: Dict[str, Any] = {"torrent_id": str(torrent_id)}
        if file_id is None:
            params["zip_link"] = "true"
        else:
            params["file_id"] = str(file_id)
        data = self._request("GET", "/torrents/requestdl", params=params, token_query=True)
        if isinstance(data, str) and data:
            return data
        if isinstance(data, dict):
            for key in ("url", "link", "download_url", "dl_url"):
                if data.get(key):
                    return str(data[key])
        raise DownloaderError("Torbox did not return a download link from requestdl.")

    def _stream_to_file(self, url: str, dest_path: str, progress_cb=None, label: str = "") -> int:
        current_url = url
        for _hop in range(TORBOX_MAX_REDIRECTS + 1):
            try:
                security.validate_public_url(current_url, resolve_dns=False)
            except security.UnsafeURLError as exc:
                raise DownloaderFileError(f"Blocked unsafe download URL ({exc})") from exc
            request = urllib.request.Request(current_url, headers={"User-Agent": "MangaTrax/1.0"})
            try:
                response = _open_no_redirect(request, TORBOX_STREAM_TIMEOUT_SECONDS)
            except urllib.error.HTTPError as exc:
                location = exc.headers.get("Location") if exc.headers else None
                try:
                    exc.close()
                except Exception:
                    pass
                if exc.code in REDIRECT_STATUSES and location:
                    current_url = urllib.parse.urljoin(current_url, location)
                    continue
                raise DownloaderFileError(f"Download failed (HTTP {exc.code}) for {label or 'file'}.") from None
            try:
                status = getattr(response, "status", None) or 200
                if status in REDIRECT_STATUSES:
                    location = response.headers.get("Location") if response.headers else None
                    if not location:
                        raise DownloaderFileError(f"Redirect without Location for {label or 'file'}.")
                    current_url = urllib.parse.urljoin(current_url, location)
                    continue
                if status != 200:
                    raise DownloaderFileError(f"Download failed (HTTP {status}) for {label or 'file'}.")
                content_length = response.headers.get("Content-Length") if response.headers else None
                expected = int(content_length) if content_length else 0
                if expected and expected > self.max_file_bytes:
                    raise DownloaderFileError(
                        f"Refusing {label or 'file'}: {expected} bytes exceeds the size cap."
                    )
                written = 0
                with open(dest_path, "wb") as handle:
                    while True:
                        chunk = response.read(TORBOX_STREAM_CHUNK_BYTES)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > self.max_file_bytes:
                            raise DownloaderFileError(f"{label or 'file'} exceeded the size cap while streaming.")
                        handle.write(chunk)
                if expected and written < expected:
                    raise DownloaderFileError(
                        f"Partial download for {label or 'file'}: {written}/{expected} bytes."
                    )
                if written == 0:
                    raise DownloaderFileError(f"Empty download for {label or 'file'}.")
                if progress_cb:
                    try:
                        progress_cb(label, written)
                    except Exception:
                        pass
                return written
            finally:
                try:
                    response.close()
                except Exception:
                    pass
        raise DownloaderFileError(f"Too many redirects downloading {label or 'file'}.")

    def _download_to(self, url: str, dest_dir: str, name: str, progress_cb=None) -> Dict[str, Any]:
        final_path = _resolve_within(dest_dir, name)
        os.makedirs(dest_dir, exist_ok=True)
        partial_path = f"{final_path}.part"
        try:
            written = self._stream_to_file(url, partial_path, progress_cb=progress_cb, label=name)
            os.replace(partial_path, final_path)
        except Exception:
            try:
                if os.path.isfile(partial_path):
                    os.remove(partial_path)
            except OSError:
                pass
            raise
        return {"name": name, "path": final_path, "size": written}

    @staticmethod
    def _safe_extract_zip(zip_path: str, dest_dir: str) -> List[Dict[str, Any]]:
        """Extract a Torbox zip archive, rejecting absolute paths, ``..`` and symlinks."""
        root = os.path.realpath(dest_dir)
        saved: List[Dict[str, Any]] = []
        with zipfile.ZipFile(zip_path, "r") as archive:
            for member in archive.infolist():
                name = member.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                if name.startswith("/") or re.match(r"^[a-zA-Z]:", name) or "\x00" in name:
                    raise DownloaderFileError(f"Unsafe absolute path in Torbox zip: {member.filename!r}")
                parts = [part for part in name.split("/") if part not in ("", ".")]
                if any(part == ".." for part in parts):
                    raise DownloaderFileError(f"Unsafe '..' path in Torbox zip: {member.filename!r}")
                mode = (member.external_attr >> 16) & 0o170000
                if mode == 0o120000:  # symlink
                    raise DownloaderFileError(f"Refusing symlink entry in Torbox zip: {member.filename!r}")
                target = os.path.realpath(os.path.join(root, *parts))
                if target != root and not target.startswith(root + os.sep):
                    raise DownloaderFileError(f"Zip entry escapes the download folder: {member.filename!r}")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with archive.open(member, "r") as source, open(target, "wb") as handle:
                    while True:
                        chunk = source.read(TORBOX_STREAM_CHUNK_BYTES)
                        if not chunk:
                            break
                        handle.write(chunk)
                saved.append({"name": parts[-1], "path": target, "size": member.file_size})
        if not saved:
            raise DownloaderFileError("The Torbox zip archive contained no files.")
        return saved

    def fetch_files(
        self,
        handle: Dict[str, Any],
        files: Any,
        dest_dir: str,
        progress_cb=None,
    ) -> List[Dict[str, Any]]:
        """Download the wanted files (or the whole torrent as a zip) into *dest_dir*.

        ``files``: ``None``/``"all"``/empty list → single ``zip_link`` download.
        A list of dicts with ``id``/``name`` → per-file ``requestdl`` downloads.  The
        caller decides the volume subset; this method never silently skips a file.
        """
        torrent_id = (handle or {}).get("torrent_id")
        if not torrent_id:
            raise DownloaderNotFoundError("Torbox handle is missing its torrent_id.")
        os.makedirs(dest_dir, exist_ok=True)

        if files in (None, "all") or files == []:
            url = self._requestdl(int(torrent_id), file_id=None)
            zip_path = os.path.join(dest_dir, ".torbox-download.zip.part")
            try:
                self._stream_to_file(url, zip_path, label="torrent.zip")
                saved = self._safe_extract_zip(zip_path, dest_dir)
            finally:
                try:
                    if os.path.isfile(zip_path):
                        os.remove(zip_path)
                except OSError:
                    pass
            return saved

        if not isinstance(files, list):
            raise DownloaderFileError("Torbox fetch_files expects a file list or 'all'.")
        saved: List[Dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                raise DownloaderFileError("Torbox file entries must be mappings.")
            name = _safe_basename(item.get("name") or item.get("short_name") or item.get("path") or "")
            file_id = item.get("id")
            if file_id is None:
                raise DownloaderFileError(f"Torbox file {name!r} has no file id.")
            url = self._requestdl(int(torrent_id), file_id=int(file_id))
            saved.append(self._download_to(url, dest_dir, name, progress_cb=progress_cb))
        if not saved:
            raise DownloaderFileError("No files were downloaded from Torbox.")
        return saved

    def cancel(self, handle: Dict[str, Any], delete_remote: bool = True) -> bool:
        handle = handle or {}
        torrent_id = handle.get("torrent_id")
        queued_id = handle.get("queued_id")
        if not torrent_id and not queued_id:
            return False
        if not delete_remote:
            return True
        payload: Dict[str, Any] = {"operation": "delete"}
        if torrent_id:
            payload["torrent_id"] = int(torrent_id)
        else:
            payload["queued_id"] = int(queued_id)
        try:
            self._request("POST", "/torrents/controltorrent", json_body=payload)
            return True
        except DownloaderNotFoundError:
            return True
        except DownloaderError as exc:
            logger.warning("Could not delete Torbox job %s: %s", payload, exc)
            return False

    def test_connection(self) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "success": False,
                "engine": self.name,
                "message": "Torbox is disabled or there is no API key configured.",
            }
        try:
            data = self._request("GET", "/user/me")
        except DownloaderAuthError as exc:
            return {"success": False, "engine": self.name, "message": str(exc)}
        except DownloaderError as exc:
            return {"success": False, "engine": self.name, "message": str(exc)}
        data = data if isinstance(data, dict) else {}
        plan = (
            data.get("plan") or data.get("plan_name") or data.get("subscription")
            or data.get("plan_id") or ""
        )
        active_slots = (
            data.get("active_slot_count") or data.get("active_slots")
            or data.get("slots") or data.get("active_slots_used") or 0
        )
        try:
            active_slots = int(active_slots)
        except (TypeError, ValueError):
            active_slots = 0
        message = f"Connected to Torbox ({plan or 'plan unknown'}); {active_slots} active slot(s)."
        return {
            "success": True,
            "engine": self.name,
            "plan": str(plan or ""),
            "active_slots": active_slots,
            "message": message,
        }
