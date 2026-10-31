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
                # Check candidate parts from delimiters in cache
                raw_parts = [p.strip() for p in re.split(r"\s*[|/–—]\s*|\s+-\s+|:", raw_series_name) if p.strip()]
                if len(raw_parts) > 1:
                    for p in raw_parts:
                        p_clean = clean_query_term(p)
                        if p_clean and p_clean.lower() != cache_key:
                            p_cached = self.db.get_cached_manga_metadata(p_clean.lower())
                            if p_cached and p_cached.get("canonical_title"):
                                return p_cached
            except Exception as e:
                logger.debug(f"Cache lookup failed for '{cache_key}': {e}")

        # 2. Query AniList
        metadata = self._fetch_from_anilist(cleaned_term)

        # 3. If AniList didn't match with full string, try candidate parts from delimiters (|, /, //, -, —)
        if not metadata:
            raw_parts = [p.strip() for p in re.split(r"\s*[|/–—]\s*|\s+-\s+|:", raw_series_name) if p.strip()]
            if len(raw_parts) > 1:
                for p in raw_parts:
                    p_clean = clean_query_term(p)
                    if len(p_clean) >= 3 and p_clean.lower() != cleaned_term.lower():
                        metadata = self._fetch_from_anilist(p_clean)
                        if metadata:
                            break

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
        canonical = info.get("canonical_title")
        return canonical if canonical else raw_series_name

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

                cover = media.get("coverImage", {}).get("extraLarge") or media.get("coverImage", {}).get("large") or ""
                volumes = media.get("volumes")
                status = media.get("status") or "UNKNOWN"
                desc = media.get("description") or ""
                clean_desc = re.sub(r"<[^>]+>", "", desc).strip()

                return {
                    "canonical_title": canonical,
                    "english_title": canonical,
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
