import json
import logging
import re
import html
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

logger = logging.getLogger("mangatrax.metadata.mangaupdates")

MANGAUPDATES_SEARCH_API = "https://api.mangaupdates.com/v1/series/search"

def clean_description_text(raw_desc: str) -> str:
    """Cleans up MangaUpdates description text, stripping BBCode, HTML, Markdown links, and raw URLs."""
    if not raw_desc:
        return ""
    # Unescape HTML entities e.g. &quot;, &#039;, &amp;, &lt;, &gt;
    clean = html.unescape(raw_desc)
    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", " ", clean)
    # BBCode: convert [url=LINK]LABEL[/url] to LABEL
    clean = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", clean, flags=re.IGNORECASE)
    # Strip remaining BBCode tags e.g. [b], [/b], [i], [url], [/url], [spoiler]
    clean = re.sub(r"\[/?(?:b|i|u|s|url|quote|code|img|spoiler)[^\]]*\]", "", clean, flags=re.IGNORECASE)
    # Convert markdown links [Label](url) or [Label]\s*(url) -> Label (handles newlines between ] and ()
    clean = re.sub(r"\[([^\]]+)\]\s*\((?:[^)]+)\)", r"\1", clean)
    # Strip raw URLs (http:// or https://...) to prevent unbroken links breaking layouts
    clean = re.sub(r"https?://[^\s)\]]+", "", clean)
    # Clean up empty parens or brackets left behind after stripping URLs
    clean = re.sub(r"\(\s*\)", "", clean)
    clean = re.sub(r"\[\s*\]", "", clean)
    # Strip markdown bold/italic/strikethrough markers
    clean = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", clean)
    clean = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", clean)
    clean = re.sub(r"~~([^~]+)~~", r"\1", clean)
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
                    url = cached.get("romaji_title", "")
                    if not url or not url.startswith("http"):
                        url = f"https://www.mangaupdates.com/series.html?search={urllib.parse.quote_plus(clean_name)}"
                    return {
                        "title": cached.get("canonical_title") or clean_name,
                        "description": cached.get("synopsis", ""),
                        "url": url,
                        "cover_url": cached.get("cover_url", ""),
                        "genres": [],
                        "total_volumes": cached.get("total_volumes"),
                        "year": None,
                        "source": "cache"
                    }
            except Exception as e:
                logger.debug(f"MangaUpdates cache lookup failed for '{cache_key}': {e}")

        # 2. Query MangaUpdates API
        info = self._fetch_from_api(clean_name)

        # If not found or missing total_volumes, try candidate parts from delimiters
        if not info or not info.get("total_volumes"):
            raw_parts = [p.strip() for p in re.split(r"\s*[|/–—]\s*|\s+-\s+|//|;|:", clean_name) if p.strip()]
            if len(raw_parts) > 1:
                for p in raw_parts:
                    if len(p) >= 3 and p.lower() != clean_name.lower():
                        alt_info = self._fetch_from_api(p)
                        if alt_info:
                            if alt_info.get("total_volumes") or not info:
                                info = alt_info
                            if info.get("total_volumes"):
                                break

        # If still not found and contains subtitles or hyphens, try main title
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
                "total_volumes": None,
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
                    "total_volumes": info.get("total_volumes"),
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

                series_id = record.get("series_id")
                total_volumes = None
                if series_id:
                    try:
                        s_req = urllib.request.Request(
                            f"https://api.mangaupdates.com/v1/series/{series_id}",
                            headers={
                                "User-Agent": "MangaTrax/1.0 (+https://github.com/Wemew/MangaTrax)"
                            }
                        )
                        with urllib.request.urlopen(s_req, timeout=self.timeout) as s_resp:
                            if s_resp.status == 200:
                                s_data = json.loads(s_resp.read().decode("utf-8"))
                                st = s_data.get("status") or ""
                                m_vol = re.search(r'(\d+)\s*Volumes?', st, re.IGNORECASE)
                                if m_vol:
                                    total_volumes = int(m_vol.group(1))
                    except Exception:
                        pass

                return {
                    "title": title,
                    "description": clean_desc,
                    "url": url,
                    "cover_url": image_url,
                    "genres": genres,
                    "total_volumes": total_volumes,
                    "year": year,
                    "source": "mangaupdates"
                }
        except urllib.error.HTTPError as e:
            logger.debug(f"MangaUpdates API HTTP error {e.code} for '{search_term}': {e.reason}")
            return None
        except Exception as e:
            logger.debug(f"MangaUpdates API unavailable for '{search_term}': {e}")
            return None
