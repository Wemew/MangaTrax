import re
import logging
from typing import List, Dict, Any, Optional
import feedparser
from app.providers.base import BaseFeedProvider, ReleaseItem
from app.parser import extract_manga_bundle_and_volumes

logger = logging.getLogger("mangatrax.providers.rss")

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
        feed = feedparser.parse(self.url, request_headers=headers)
        if feed.bozo and not feed.entries:
            logger.error(f"Error parsing RSS feed at {self.url}: {feed.bozo_exception}")
            return []

        results = []
        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            if not title:
                continue

            pub_date = entry.get("published") or entry.get("updated")
            summary = entry.get("summary") or entry.get("description") or ""

            # Find download URL: check enclosures for torrent or magnet
            download_url = None
            if "enclosures" in entry and entry.enclosures:
                for enc in entry.enclosures:
                    href = enc.get("href", "")
                    enc_type = enc.get("type", "")
                    if "torrent" in enc_type or href.endswith(".torrent") or href.startswith("magnet:"):
                        download_url = href
                        break

            # If no enclosure torrent, search for magnet link in description/summary or links
            if not download_url and summary:
                mag_match = re.search(r'href=["\'](magnet:\?[^"\']+)["\']', summary)
                if not mag_match:
                    mag_match = re.search(r'(magnet:\?[^\s<>"\']+)', summary)
                if mag_match:
                    download_url = mag_match.group(1)

            if not download_url and "links" in entry:
                for l in entry.links:
                    href = l.get("href", "")
                    if href.startswith("magnet:") or href.endswith(".torrent"):
                        download_url = href
                        break

            if not download_url:
                download_url = link

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

            results.append(ReleaseItem(
                title=title,
                link=link,
                download_url=download_url,
                series_name=parsed["series_name"],
                is_bundle=parsed["is_bundle"],
                volume_start=parsed["volume_start"],
                volume_end=parsed["volume_end"],
                volumes=parsed["volumes"],
                pub_date=pub_date,
                cover_url=cover_url,
                description=clean_desc,
                guid=entry.get("id") or entry.get("guid"),
                source_feed_name=self.name,
                raw_data={"confidence": parsed["confidence"]}
            ))

        return results
