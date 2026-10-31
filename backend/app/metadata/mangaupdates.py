import json
import logging
import re
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

logger = logging.getLogger("mangatrax.metadata.mangaupdates")

MANGAUPDATES_SEARCH_API = "https://api.mangaupdates.com/v1/series/search"

def clean_description_text(raw_desc: str) -> str:
    """Cleans up MangaUpdates description text, stripping BBCode, HTML, and publisher tags."""
    if not raw_desc:
        return ""
    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", " ", raw_desc)
    # Strip BBCode tags e.g. [b], [/b], [i], [url=...], [/url]
    clean = re.sub(r"\[/?(?:b|i|u|s|url|quote|code|img|spoiler)[^\]]*\]", "", clean, flags=re.IGNORECASE)
    # Strip common publisher prefix lines e.g. "From Viz:", "From Yen Press:", "From Kodansha:"
    clean = re.sub(r"^\s*(?:From\s+[A-Za-z0-9\s\-]+:)\s*", "", clean, flags=re.IGNORECASE)
    # Clean up excessive whitespace
    clean = " ".join(clean.split())
    return clean

class MangaUpdatesService:
    """
    Public REST API client for MangaUpdates (Baka-Updates Manga).
    Fetches official synopsis, genres, year, and direct MangaUpdates URL.
    """
    def __init__(self, db=None, timeout: float = 5.0):
        self.db = db
        self.timeout = timeout

    def get_series_info(self, series_name: str) -> Dict[str, Any]:
        """
        Retrieves MangaUpdates metadata for a series name.
        Checks database cache first, then calls MangaUpdates public API.
        """
        clean_name = series_name.strip()
        if not clean_name:
            return {
                "title": "",
                "description": "",
                "url": "",
                "cover_url": "",
                "genres": [],
                "year": None,
                "source": "none"
            }

        cache_key = f"mu:{clean_name.lower()}"

        # 1. Check local DB cache
        if self.db:
            try:
                cached = self.db.get_cached_manga_metadata(cache_key)
                if cached and cached.get("synopsis"):
                    return {
                        "title": cached.get("canonical_title") or clean_name,
                        "description": cached.get("synopsis", ""),
                        "url": cached.get("romaji_title", ""), # using romaji_title as URL placeholder or custom field
                        "cover_url": cached.get("cover_url", ""),
                        "genres": [],
                        "year": None,
                        "source": "cache"
                    }
            except Exception as e:
                logger.debug(f"MangaUpdates cache lookup failed for '{cache_key}': {e}")

        # 2. Query MangaUpdates API
        info = self._fetch_from_api(clean_name)

        # If not found with full string and contains subtitles or hyphens, try main title
        if not info and ("-" in clean_name or ":" in clean_name):
            simplified = re.split(r"[-:]", clean_name)[0].strip()
            if len(simplified) >= 3 and simplified.lower() != clean_name.lower():
                info = self._fetch_from_api(simplified)

        if not info:
            info = {
                "title": clean_name,
                "description": "",
                "url": f"https://www.mangaupdates.com/series.html?search={urllib.parse.quote_plus(clean_name)}",
                "cover_url": "",
                "genres": [],
                "year": None,
                "source": "fallback"
            }

        # 3. Cache result in DB
        if self.db and info.get("description"):
            try:
                self.db.cache_manga_metadata(cache_key, {
                    "canonical_title": info.get("title") or clean_name,
                    "romaji_title": info.get("url") or "",
                    "cover_url": info.get("cover_url") or "",
                    "total_volumes": None,
                    "status": "UNKNOWN",
                    "synopsis": info.get("description", ""),
                    "source": "mangaupdates"
                })
            except Exception as e:
                logger.debug(f"Failed to cache MangaUpdates info for '{cache_key}': {e}")

        return info

    def _fetch_from_api(self, search_term: str) -> Optional[Dict[str, Any]]:
        payload = {
            "search": search_term,
            "stype": "title",
            "perpage": 1
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            MANGAUPDATES_SEARCH_API,
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
                data = json.loads(resp.read().decode("utf-8"))
                results = data.get("results", [])
                if not results:
                    return None

                record = results[0].get("record", {})
                title = record.get("title") or search_term
                raw_desc = record.get("description") or ""
                clean_desc = clean_description_text(raw_desc)
                url = record.get("url") or ""
                image_url = record.get("image", {}).get("url", {}).get("original") or ""
                genres = [g.get("genre") for g in record.get("genres", []) if g.get("genre")]
                year = record.get("year")

                return {
                    "title": title,
                    "description": clean_desc,
                    "url": url,
                    "cover_url": image_url,
                    "genres": genres,
                    "year": year,
                    "source": "mangaupdates"
                }
        except urllib.error.HTTPError as e:
            logger.debug(f"MangaUpdates API HTTP error {e.code} for '{search_term}': {e.reason}")
            return None
        except Exception as e:
            logger.debug(f"MangaUpdates API unavailable for '{search_term}': {e}")
            return None
