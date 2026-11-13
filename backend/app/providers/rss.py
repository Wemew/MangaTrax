import re
import socket
import logging
from typing import List, Dict, Any, Optional
import feedparser
from app import security
from app.providers.base import BaseFeedProvider, ProviderFetchError, ReleaseItem
from app.parser import extract_manga_bundle_and_volumes, is_chapter_only, is_light_novel

logger = logging.getLogger("mangatrax.providers.rss")

#: Source pages (jnovels and similar) put the actual download behind an anchor like
#: ``<a href="https://charexempire.com/xxxx">VOLUME 23</a>``. Keep that link so the
#: row can show a direct button instead of requiring a trip through the article.
_SOURCE_VOLUME_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href=["\'](?P<href>https?://[^"\']+)["\'][^>]*>\s*(?:volume|vol\.?)\s*\d+',
    re.IGNORECASE | re.DOTALL,
)
_SOURCE_HOST_FALLBACK_RE = re.compile(r'https?://charexempire\.com/[^\s"\'<>]+', re.IGNORECASE)


def extract_source_link(summary: str) -> str:
    """Extract a per-post direct download link (e.g. jnovels' VOLUME anchor).

    Returns an empty string when the post has no such anchor, so ordinary RSS feeds
    are unaffected. The first matching anchor wins; a charexempire link is used as a
    fallback for anchors whose text was altered.
    """
    html = str(summary or "")
    if not html:
        return ""
    match = _SOURCE_VOLUME_ANCHOR_RE.search(html)
    if match:
        return match.group("href").strip()
    fallback = _SOURCE_HOST_FALLBACK_RE.search(html)
    return fallback.group(0).strip() if fallback else ""

FEED_FETCH_TIMEOUT_SECONDS = 15.0


def _parse_feed_with_timeout(url: str, headers: Dict[str, str], timeout: float = FEED_FETCH_TIMEOUT_SECONDS):
    """Parse a remote RSS/Atom URL with an explicit socket timeout and shields.

    ``feedparser.parse(url)`` uses ``urllib`` internally without accepting a
    timeout argument, so the process-wide default socket timeout is temporarily
    lowered for the fetch (restored in ``finally``).  Any network/parse failure
    is contained and returns ``None`` instead of raising into the scheduler loop.
    """
    previous_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        return feedparser.parse(url, request_headers=headers)
    except Exception as exc:  # pragma: no cover - network error path
        logger.error("Error fetching RSS feed at %s: %s", url, exc)
        return None
    finally:
        socket.setdefaulttimeout(previous_timeout)


class RSSFeedProvider(BaseFeedProvider):
    """
    RSS / Atom feed provider for Manga releases and torrent trackers.
    Extracts bundle and volume information, handles torrent enclosures and magnet links.
    """
    def __init__(self, name: str, url: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(name, url, config)

    def fetch_releases(self) -> List[ReleaseItem]:
        headers = {
            "User-Agent": "MangaTrax/1.0 (Manga Release Monitor; +https://github.com/Wemew/MangaTrax)",
        }
        # M3 SSRF guard: reject non-http(s) schemes and literal loopback/private/
        # metadata/intranet hosts before any request is made.
        try:
            security.validate_outbound_url(self.url)
        except security.UnsafeURLError as exc:
            logger.error("Rejected unsafe RSS feed URL for '%s': %s", self.name, exc)
            return []

        feed = _parse_feed_with_timeout(self.url, headers)
        if feed is None:
            # A network/timeout failure must be a visible feed failure, not a
            # silently "successful" empty poll.
            raise ProviderFetchError(f"Failed to fetch RSS feed at {self.url} (network/timeout error).")
        if feed.bozo and not feed.entries:
            logger.error(f"Error parsing RSS feed at {self.url}: {feed.bozo_exception}")
            raise ProviderFetchError(f"Failed to parse RSS feed at {self.url}: {feed.bozo_exception}")

        results = []
        for entry in feed.entries:
            title = entry.get("title", "")
            if not title:
                continue

            # Discard pure chapter updates
            if is_chapter_only(title):
                logger.debug(f"Discarding chapter-only release from RSS feed '{self.name}': {title}")
                continue

            # Discard light novels and non-manga literature
            if is_light_novel(title):
                logger.debug(f"Discarding light novel release from RSS feed '{self.name}': {title}")
                continue

            raw_link = (entry.get("link") or "").strip()
            guid = (entry.get("id") or entry.get("guid") or "").strip()
            summary = entry.get("summary") or entry.get("description") or ""
            pub_date = entry.get("published") or entry.get("updated")

            # 1. Distinguish between Web Post Page URL and Torrent / Magnet Download URL
            web_post_link = ""
            download_url = None

            # Case A: GUID is an HTTP permalink to the post webpage (e.g. Nyaa https://nyaa.si/view/<id>)
            if guid.startswith("http") and not guid.endswith(".torrent") and not guid.startswith("magnet:"):
                web_post_link = guid

            # Case B: Check summary HTML for post view link (e.g. <a href="https://nyaa.si/view/...">)
            if not web_post_link and summary:
                view_match = re.search(r'href=["\'](https?://[^"\']*/view/\d+[^"\']*)["\']', summary)
                if view_match:
                    web_post_link = view_match.group(1)

            # Case C: If raw_link is a torrent file or magnet
            if raw_link.endswith(".torrent") or "/download/" in raw_link:
                download_url = raw_link
                if not web_post_link:
                    # Convert Nyaa /download/<id>.torrent to /view/<id>
                    web_post_link = re.sub(r"/download/(\d+)(?:\.torrent)?", r"/view/\1", raw_link)
            elif raw_link.startswith("magnet:"):
                download_url = raw_link
            elif not web_post_link:
                web_post_link = raw_link

            # Fallback for web_post_link
            if not web_post_link:
                web_post_link = raw_link

            # 2. Check enclosures for torrent or magnet if download_url not yet set
            if not download_url and "enclosures" in entry and entry.enclosures:
                for enc in entry.enclosures:
                    href = enc.get("href", "")
                    enc_type = enc.get("type", "")
                    if "torrent" in enc_type or href.endswith(".torrent") or href.startswith("magnet:"):
                        download_url = href
                        break

            # 3. Check for magnet link in description/summary or links
            if not download_url and summary:
                mag_match = re.search(r'href=["\'](magnet:\?[^"\']+)["\']', summary)
                if not mag_match:
                    mag_match = re.search(r'(magnet:\?[^\s<>"\']+)', summary)
                if mag_match:
                    download_url = mag_match.group(1)

            # 4. Check tracker specific fields (e.g. nyaa_infohash)
            infohash = entry.get("nyaa_infohash")
            if not download_url and infohash:
                import urllib.parse
                download_url = f"magnet:?xt=urn:btih:{infohash}&dn={urllib.parse.quote(title)}"

            if not download_url and "links" in entry:
                for l in entry.links:
                    href = l.get("href", "")
                    if href.startswith("magnet:") or href.endswith(".torrent"):
                        download_url = href
                        break

            if not download_url:
                download_url = raw_link

            # Extract cover image
            cover_url = None
            if "media_thumbnail" in entry and entry.media_thumbnail:
                cover_url = entry.media_thumbnail[0].get("url")
            elif "media_content" in entry and entry.media_content:
                cover_url = entry.media_content[0].get("url")
            elif "enclosures" in entry and entry.enclosures:
                for enc in entry.enclosures:
                    if "image" in enc.get("type", ""):
                        cover_url = enc.get("href")
                        break

            if not cover_url and summary:
                img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.IGNORECASE)
                if img_match:
                    cover_url = img_match.group(1)

            parsed = extract_manga_bundle_and_volumes(title)
            clean_desc = re.sub(r'<[^>]+>', '', summary).strip()[:400]

            # Append post metadata like size and seeders if present
            post_size = entry.get("nyaa_size")
            seeders = entry.get("nyaa_seeders")
            meta_snippet = ""
            if post_size:
                meta_snippet = f"Size: {post_size}"
                if seeders is not None:
                    meta_snippet += f" | Seeders: {seeders}"

            results.append(ReleaseItem(
                title=title,
                link=web_post_link,
                download_url=download_url,
                series_name=parsed["series_name"],
                release_group=parsed.get("release_group"),
                        release_groups=parsed.get("release_groups"),
                        authors=parsed.get("authors"),
                    revision=parsed.get("revision"),
                    publisher=parsed.get("publisher"),
                is_bundle=parsed["is_bundle"],
                volume_start=parsed["volume_start"],
                volume_end=parsed["volume_end"],
                volumes=parsed["volumes"],
                pub_date=pub_date,
                cover_url=cover_url,
                description=clean_desc,
                guid=entry.get("id") or entry.get("guid") or web_post_link,
                source_feed_name=self.name,
                source_link=extract_source_link(summary),
                raw_data={
                    "confidence": parsed["confidence"],
                    "post_size": post_size,
                    "seeders": seeders,
                    "infohash": infohash,
                    "meta_snippet": meta_snippet
                }
            ))

        return results
