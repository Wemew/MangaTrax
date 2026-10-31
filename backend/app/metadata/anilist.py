import json
import logging
import re
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

logger = logging.getLogger("mangatrax.metadata.anilist")

ANILIST_GRAPHQL_ENDPOINT = "https://graphql.anilist.co"

ANILIST_QUERY = """
query ($search: String) {
  Media(search: $search, type: MANGA) {
    id
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
    s = re.sub(r'[-–—:_/\\]+', ' ', s)
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

    def resolve_manga_series(self, raw_series_name: str) -> Dict[str, Any]:
        """
        Resolves a canonical manga series title and metadata.
        1. Checks local cache first.
        2. Queries AniList GraphQL if not cached.
        3. Gracefully returns cleaned title on failure or offline environment.
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
                "source": "fallback"
            }

        cache_key = cleaned_term.lower()

        # 1. Check local DB cache
        if self.db:
            try:
                cached = self.db.get_cached_manga_metadata(cache_key)
                if cached:
                    return cached
            except Exception as e:
                logger.debug(f"Cache lookup failed for '{cache_key}': {e}")

        # 2. Query AniList
        metadata = self._fetch_from_anilist(cleaned_term)

        # 3. If AniList didn't match with full string, try simplified primary title before delimiter
        if not metadata and ("-" in cleaned_term or ":" in cleaned_term):
            simplified = re.split(r"[-:]", cleaned_term)[0].strip()
            if len(simplified) >= 3 and simplified.lower() != cleaned_term.lower():
                metadata = self._fetch_from_anilist(simplified)

        if not metadata:
            # Fallback to cleaned term
            metadata = {
                "canonical_title": cleaned_term,
                "romaji_title": "",
                "cover_url": "",
                "total_volumes": None,
                "status": "UNKNOWN",
                "synopsis": "",
                "source": "cleaned"
            }

        # 4. Save to DB cache
        if self.db:
            try:
                self.db.cache_manga_metadata(cache_key, metadata)
            except Exception as e:
                logger.debug(f"Failed to cache metadata for '{cache_key}': {e}")

        return metadata

    def _fetch_from_anilist(self, search_term: str) -> Optional[Dict[str, Any]]:
        payload = {
            "query": ANILIST_QUERY,
            "variables": {"search": search_term}
        }
        data_bytes = json.dumps(payload).encode("utf-8")
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
                if resp.status != 200:
                    return None
                result = json.loads(resp.read().decode("utf-8"))
                media = result.get("data", {}).get("Media")
                if not media:
                    return None

                titles = media.get("title", {})
                canonical = titles.get("english") or titles.get("romaji") or search_term
                romaji = titles.get("romaji") or ""
                cover = media.get("coverImage", {}).get("extraLarge") or media.get("coverImage", {}).get("large") or ""
                volumes = media.get("volumes")
                status = media.get("status") or "UNKNOWN"
                desc = media.get("description") or ""
                clean_desc = re.sub(r"<[^>]+>", "", desc).strip()

                return {
                    "canonical_title": canonical,
                    "romaji_title": romaji,
                    "cover_url": cover,
                    "total_volumes": volumes,
                    "status": status,
                    "synopsis": clean_desc,
                    "source": "anilist"
                }
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.debug(f"AniList returned 404 for '{search_term}'")
            else:
                logger.warning(f"AniList HTTP Error {e.code} for '{search_term}': {e.reason}")
            return None
        except Exception as e:
            logger.debug(f"AniList lookup unavailable for '{search_term}': {e}")
            return None
