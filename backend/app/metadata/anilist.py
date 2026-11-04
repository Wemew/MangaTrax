import json
import logging
import re
import time
import threading
import collections
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from app.parser import split_series_candidate_parts

logger = logging.getLogger("mangatrax.metadata.anilist")

ANILIST_GRAPHQL_ENDPOINT = "https://graphql.anilist.co"

class AniListRateLimiter:
    """
    Thread-safe sliding-window rate limiter for AniList GraphQL API.
    Enforces the official rate limit of 90 requests per 60-second window,
    and dynamically respects X-RateLimit-Remaining and Retry-After HTTP headers.
    """
    def __init__(self, max_requests: int = 90, time_window: float = 60.0):
        self.max_requests = max_requests
        self.time_window = time_window
        self.lock = threading.Lock()
        self.timestamps = collections.deque()
        self.retry_until: float = 0.0

    def wait_if_needed(self):
        with self.lock:
            now = time.time()
            # If under an explicit 429 Retry-After penalty, pause until it expires
            if self.retry_until > now:
                wait_time = self.retry_until - now
                logger.warning(f"AniList rate limit cooldown active. Pausing for {wait_time:.1f}s...")
                time.sleep(wait_time)
                now = time.time()

            # Evict timestamps older than the sliding window
            while self.timestamps and (now - self.timestamps[0]) >= self.time_window:
                self.timestamps.popleft()

            # If at capacity, wait until the oldest request timestamp rolls out of the window
            if len(self.timestamps) >= self.max_requests:
                wait_time = self.time_window - (now - self.timestamps[0]) + 0.05
                if wait_time > 0:
                    logger.info(f"AniList sliding-window rate limit reached ({len(self.timestamps)}/{self.max_requests} req). Pausing for {wait_time:.2f}s...")
                    time.sleep(wait_time)
                    now = time.time()
                    while self.timestamps and (now - self.timestamps[0]) >= self.time_window:
                        self.timestamps.popleft()

            self.timestamps.append(time.time())

    def handle_response_headers(self, headers):
        """Inspects AniList response headers to track remaining quota."""
        if not headers:
            return
        try:
            remaining = None
            if hasattr(headers, "get"):
                remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
            if remaining is not None:
                rem_val = int(remaining)
                if rem_val <= 2:
                    logger.warning(f"AniList quota nearly exhausted ({rem_val} requests remaining). Pacing requests...")
                    with self.lock:
                        time.sleep(1.0)
        except Exception:
            pass

    def handle_rate_limit_error(self, headers=None):
        """Handles HTTP 429 Too Many Requests by setting retry_until cooldown."""
        wait_secs = 60.0
        if headers and hasattr(headers, "get"):
            retry_after = headers.get("Retry-After") or headers.get("retry-after")
            if retry_after:
                try:
                    wait_secs = max(1.0, float(retry_after))
                except ValueError:
                    wait_secs = 60.0
        logger.warning(f"AniList 429 Too Many Requests received. Setting rate-limit cooldown for {wait_secs}s.")
        with self.lock:
            self.retry_until = max(self.retry_until, time.time() + wait_secs)

_RATE_LIMITER = AniListRateLimiter(max_requests=90, time_window=60.0)

ANILIST_QUERY = """
query ($search: String) {
  Media(search: $search, type: MANGA, format_in: [MANGA, ONE_SHOT]) {
    id
    format
    title {
      english
      romaji
      native
    }
    coverImage {
      large
      extraLarge
    }
    volumes
    chapters
    status
    description
    genres
    tags {
      id
      name
      rank
      isMediaSpoiler
      isGeneralSpoiler
      category
    }
    synonyms
  }
}
"""

def clean_query_term(raw: str) -> str:
    """Prepares a raw series title for optimal AniList search matching."""
    if not raw:
        return ""
    # Strip brackets, parentheses, extensions
    s = re.sub(r'\[.*?\]', ' ', raw)
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r'\.(?:cbz|cbr|zip|rar|pdf|epub)$', ' ', s, flags=re.IGNORECASE)
    # Strip volume/chapter indicators
    s = re.sub(r'\b(?:v|vol|volume|book|ch|chapter)\.?\s*\d+.*$', ' ', s, flags=re.IGNORECASE)
    # Strip common release descriptors
    s = re.sub(r'\b(?:digital|colored|complete|edition|hd|1080p|720p|manga|raw|webrip|scanlation|retail)\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'[-–—:_/\\|;]+', ' ', s)
    return " ".join(s.split())

class AniListMetadataService:
    """
    Public GraphQL metadata service querying AniList.
    Provides canonical manga titles, official cover art, total volume counts, and descriptions.
    Uses SQLite database cache to minimize external queries and handle network boundaries gracefully.
    """
    def __init__(self, db=None, timeout: float = 4.0):
        self.db = db
        self.timeout = timeout

    def resolve_manga_series(self, raw_series_name: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Resolves a canonical manga series title and metadata.

        1. Checks local cache first (unless ``force_refresh`` is set).
        2. Queries AniList GraphQL if not cached / a refresh is forced.
        3. Gracefully returns cleaned title on failure or offline environment.

        ``force_refresh=True`` bypasses the cached read and freshness check (so a
        stale cache row is never returned as fresh), still goes through the AniList
        rate limiter, and still writes authoritative results through
        ``db.cache_manga_metadata`` for the search term, romaji and canonical titles.
        If the forced lookup fails or yields nothing authoritative, the existing cached
        values are returned instead of a blank fallback so callers can keep them.
        """
        cleaned_term = clean_query_term(raw_series_name)
        if not cleaned_term:
            return {
                "canonical_title": raw_series_name or "Unknown",
                "romaji_title": "",
                "cover_url": "",
                "total_volumes": None,
                "status": "UNKNOWN",
                "synopsis": "",
                "source": "fallback",
                "anilist_id": None,
                "synonyms": [],
                "genres": [],
                "tags": [],
                "tags_sorted": [],
                "matched_term": str(raw_series_name or ""),
            }

        cache_key = cleaned_term.lower()

        # Snapshot the current cache row: a forced refresh falls back to it when the
        # provider is unavailable, and normal callers use it for the cache hit path.
        cached_snapshot: Optional[Dict[str, Any]] = None
        if self.db:
            try:
                cached_snapshot = self.db.get_cached_manga_metadata(cache_key)
            except Exception as e:
                logger.debug(f"Cache lookup failed for '{cache_key}': {e}")

        # 1. Check local DB cache (ignore failed fallback cache entries)
        if self.db and not force_refresh:
            try:
                cached = cached_snapshot
                if cached and cached.get("source") not in ("fallback", "cleaned") and cached.get("total_volumes"):
                    return cached
                # Check candidate parts from delimiters in cache
                raw_parts = split_series_candidate_parts(raw_series_name, extra_delimiters=":")
                if len(raw_parts) > 1:
                    for p in raw_parts:
                        p_clean = clean_query_term(p)
                        if p_clean and p_clean.lower() != cache_key:
                            p_cached = self.db.get_cached_manga_metadata(p_clean.lower())
                            if p_cached and p_cached.get("canonical_title") and p_cached.get("source") not in ("fallback", "cleaned") and p_cached.get("total_volumes"):
                                return p_cached
            except Exception as e:
                logger.debug(f"Cache lookup failed for '{cache_key}': {e}")

        # 2. Query AniList
        metadata = self._fetch_from_anilist(cleaned_term)

        # 3. If AniList didn't match or returned no volume count with full string, try candidate parts from delimiters
        if not metadata or not metadata.get("total_volumes"):
            raw_parts = split_series_candidate_parts(raw_series_name, extra_delimiters=":")
            if len(raw_parts) > 1:
                for p in raw_parts:
                    p_clean = clean_query_term(p)
                    if len(p_clean) >= 3 and p_clean.lower() != cleaned_term.lower():
                        alt_meta = self._fetch_from_anilist(p_clean)
                        if alt_meta:
                            if alt_meta.get("total_volumes") or not metadata:
                                metadata = alt_meta
                            if metadata.get("total_volumes"):
                                break

        if not metadata and ("-" in cleaned_term or ":" in cleaned_term):
            simplified = re.split(r"[-:]", cleaned_term)[0].strip()
            if len(simplified) >= 3 and simplified.lower() != cleaned_term.lower():
                metadata = self._fetch_from_anilist(simplified)

        if force_refresh and (not metadata or metadata.get("source") in ("fallback", "cleaned")):
            # Provider unavailable/empty during a forced refresh: keep the known values.
            if cached_snapshot and cached_snapshot.get("source") not in ("fallback", "cleaned"):
                logger.info("Forced metadata refresh for '%s' unavailable; keeping cached values.", cache_key)
                return cached_snapshot

        if not metadata:
            # Fallback preserving raw_series_name and its delimiters
            metadata = {
                "canonical_title": raw_series_name,
                "romaji_title": "",
                "cover_url": "",
                "total_volumes": None,
                "status": "UNKNOWN",
                "synopsis": "",
                "source": "fallback",
                "anilist_id": None,
                "synonyms": [],
                "genres": [],
                "tags": [],
                "tags_sorted": [],
                "matched_term": str(raw_series_name or ""),
            }

        # 4. Save to DB cache if authoritative metadata
        if self.db and metadata.get("source") not in ("fallback", "cleaned"):
            try:
                self.db.cache_manga_metadata(cache_key, metadata)
                # Also cross-cache under romaji and canonical titles if different
                romaji = (metadata.get("romaji_title") or "").strip().lower()
                canonical = (metadata.get("canonical_title") or "").strip().lower()
                if romaji and romaji != cache_key:
                    self.db.cache_manga_metadata(romaji, metadata)
                if canonical and canonical != cache_key:
                    self.db.cache_manga_metadata(canonical, metadata)
            except Exception as e:
                logger.debug(f"Failed to cache metadata for '{cache_key}': {e}")

        return metadata

    def resolve_english_title(self, raw_series_name: str) -> str:
        """
        Returns the official or canonical English title for a series name,
        converting romaji titles (e.g. 'Shingeki no Kyojin' -> 'Attack on Titan').
        Falls back to raw_series_name if no distinct English title is found.
        """
        if not raw_series_name:
            return ""
        info = self.resolve_manga_series(raw_series_name)
        if info and info.get("source") not in ("fallback", "cleaned"):
            canonical = info.get("canonical_title")
            if canonical:
                return canonical
        return raw_series_name

    def _fetch_from_anilist(self, search_term: str) -> Optional[Dict[str, Any]]:
        payload = {
            "query": ANILIST_QUERY,
            "variables": {"search": search_term}
        }
        data_bytes = json.dumps(payload).encode("utf-8")

        for attempt in range(2):
            _RATE_LIMITER.wait_if_needed()
            req = urllib.request.Request(
                ANILIST_GRAPHQL_ENDPOINT,
                data=data_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "MangaTrax/1.0 (+https://github.com/Wemew/MangaTrax)"
                }
            )

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    _RATE_LIMITER.handle_response_headers(resp.headers)
                    if resp.status != 200:
                        return None
                    result = json.loads(resp.read().decode("utf-8"))
                    media = result.get("data", {}).get("Media")
                    if not media:
                        return None

                    format_val = (media.get("format") or "").upper()
                    if format_val in ("NOVEL",):
                        return None

                    titles = media.get("title", {})
                    english = (titles.get("english") or "").strip()
                    romaji = (titles.get("romaji") or "").strip()
                    synonyms = media.get("synonyms") or []

                    # Prefer English title over romaji
                    canonical = english
                    if not canonical and synonyms:
                        for syn in synonyms:
                            s_clean = syn.strip()
                            if s_clean and s_clean.lower() != romaji.lower() and re.search(r"\b(?:the|a|an|of|in|to|is|for|my|no|war|voice|shape|world|demon|titan|hero)\b", s_clean, re.IGNORECASE):
                                canonical = s_clean
                                break
                    if not canonical:
                        canonical = romaji or search_term

                    raw_tags = media.get("tags") or []
                    parsed_tags = []
                    for tag in raw_tags:
                        if not isinstance(tag, dict):
                            continue
                        name = str(tag.get("name") or "").strip()
                        if not name:
                            continue
                        parsed_tags.append({
                            "name": name,
                            "rank": int(tag.get("rank") or 0),
                            "category": str(tag.get("category") or ""),
                            "spoiler": bool(tag.get("isMediaSpoiler") or tag.get("isGeneralSpoiler")),
                        })
                    # AniList prevalence: rank descending; Python sort is stable so ties
                    # keep provider order.
                    parsed_tags.sort(key=lambda item: -item["rank"])
                    genres = [str(item).strip() for item in (media.get("genres") or []) if str(item).strip()]
                    display_tags = [item["name"] for item in parsed_tags if not item["spoiler"]]
                    cover = media.get("coverImage", {}).get("extraLarge") or media.get("coverImage", {}).get("large") or ""
                    volumes = media.get("volumes")
                    status = media.get("status") or "UNKNOWN"
                    desc = media.get("description") or ""
                    clean_desc = re.sub(r"<[^>]+>", "", desc).strip()

                    return {
                        "canonical_title": canonical,
                        "english_title": canonical,
                        "romaji_title": romaji,
                        # Identity evidence (task-40): the exact AniList media id and its
                        # own aliases, plus which search term produced this entry.  A
                        # parent-prefix match must never lend its aliases/identity to a
                        # spinoff release.
                        "anilist_id": media.get("id"),
                        "synonyms": [str(item).strip() for item in synonyms if str(item or "").strip()],
                        "genres": genres,
                        "tags": parsed_tags,
                        "tags_sorted": display_tags,
                        "matched_term": str(search_term or ""),
                        "cover_url": cover,
                        "total_volumes": volumes,
                        "status": status,
                        "synopsis": clean_desc,
                        "source": "anilist"
                    }
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    _RATE_LIMITER.handle_rate_limit_error(e.headers)
                    if attempt == 0:
                        logger.info(f"Retrying AniList query for '{search_term}' after 429 rate limit cooldown...")
                        continue
                elif e.code == 404:
                    logger.debug(f"AniList returned 404 for '{search_term}'")
                else:
                    logger.warning(f"AniList HTTP Error {e.code} for '{search_term}': {e.reason}")
                return None
            except Exception as e:
                logger.debug(f"AniList lookup unavailable for '{search_term}': {e}")
                return None

        return None
