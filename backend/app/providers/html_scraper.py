import logging
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from app import security
from app.providers.base import BaseFeedProvider, ReleaseItem
from app.parser import extract_manga_bundle_and_volumes, is_chapter_only, is_light_novel

logger = logging.getLogger("mangatrax.providers.html_scraper")

class HtmlScraperProvider(BaseFeedProvider):
    """
    HTML scraper for manga release websites and indexers.
    Config parameters:
      - item_selector: container selector (e.g. 'article, .post, tr.torrent-info')
      - title_selector: title selector (e.g. 'h2 a, .title a')
      - link_selector: details link selector
      - download_selector: download or magnet link selector (e.g. 'a[href^="magnet:"], a.download')
      - image_selector: cover image selector
      - next_selector: pagination selector
      - max_pages: number of pages to crawl (default 2)
    """
    def __init__(self, name: str, url: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(name, url, config)
        self.item_selector = self.config.get("item_selector") or "article, .post, .entry, .item, tr.row, tr"
        self.title_selector = self.config.get("title_selector") or "h2 a, h3 a, h1 a, .title a, a"
        self.link_selector = self.config.get("link_selector") or None
        self.download_selector = self.config.get("download_selector") or 'a[href^="magnet:"], a[href*=".torrent"], a.download'
        self.image_selector = self.config.get("image_selector") or "img"
        self.next_selector = self.config.get("next_selector") or "a.next, a.next-page, a[rel='next']"
        self.max_pages = int(self.config.get("max_pages", 2))

    def _fetch_page_html(self, url: str, headers: Dict[str, str]) -> Optional[str]:
        """
        Safely fetches HTML content with standard library ``urllib.request``.
        Guarantees that network errors never raise unhandled exceptions.
        """
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            logger.warning(f"HTTP error fetching {url} via urllib: {e.code} {e.reason}")
            return None
        except Exception as e:
            logger.warning(f"Network error fetching {url} via urllib: {e}")
            return None

    def fetch_releases(self) -> List[ReleaseItem]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        # M3 SSRF guard: reject non-http(s) schemes and literal loopback/private/
        # metadata/intranet hosts before initiating the crawl.
        try:
            security.validate_outbound_url(self.url)
        except security.UnsafeURLError as exc:
            logger.error("Rejected unsafe HTML scraper URL for '%s': %s", self.name, exc)
            return []
        results = []
        seen_links = set()
        current_url = self.url
        page_num = 1

        while current_url and page_num <= self.max_pages:
            html_text = self._fetch_page_html(current_url, headers)
            if not html_text:
                logger.warning(f"Could not retrieve HTML for page {page_num} ({current_url}). Stopping crawl.")
                break

            soup = BeautifulSoup(html_text, "html.parser")
            items = soup.select(self.item_selector)
            if not items:
                break

            for el in items:
                title_el = el.select_one(self.title_selector)
                if not title_el:
                    continue

                raw_title = title_el.get_text(strip=True)
                if not raw_title:
                    continue

                # Discard pure chapter updates
                if is_chapter_only(raw_title):
                    logger.debug(f"Discarding chapter-only release from HTML scraper '{self.name}': {raw_title}")
                    continue

                # Discard light novels and non-manga literature
                if is_light_novel(raw_title):
                    logger.debug(f"Discarding light novel release from HTML scraper '{self.name}': {raw_title}")
                    continue

                # Details Link
                link = ""
                if self.link_selector:
                    link_el = el.select_one(self.link_selector)
                    if link_el:
                        link = link_el.get("href", "")
                if not link and title_el.name == "a":
                    link = title_el.get("href", "")
                elif not link:
                    a_tag = el.select_one("a")
                    if a_tag:
                        link = a_tag.get("href", "")

                if link and link.startswith("/"):
                    parsed_base = urlparse(self.url)
                    link = f"{parsed_base.scheme}://{parsed_base.netloc}{link}"

                if not link or link in seen_links:
                    continue
                seen_links.add(link)

                # Download / Magnet URL
                download_url = None
                if self.download_selector:
                    dl_el = el.select_one(self.download_selector)
                    if dl_el:
                        download_url = dl_el.get("href", "")

                if not download_url:
                    download_url = link

                # Cover image
                cover_url = None
                if self.image_selector:
                    img_el = el.select_one(self.image_selector)
                    if img_el:
                        cover_url = img_el.get("src") or img_el.get("data-src")
                        if cover_url and cover_url.startswith("/"):
                            parsed_base = urlparse(self.url)
                            cover_url = f"{parsed_base.scheme}://{parsed_base.netloc}{cover_url}"

                parsed = extract_manga_bundle_and_volumes(raw_title)

                results.append(ReleaseItem(
                    title=raw_title,
                    link=link,
                    download_url=download_url,
                    series_name=parsed["series_name"],
                    release_group=parsed.get("release_group"),
                    is_bundle=parsed["is_bundle"],
                    volume_start=parsed["volume_start"],
                    volume_end=parsed["volume_end"],
                    volumes=parsed["volumes"],
                    cover_url=cover_url,
                    source_feed_name=self.name,
                    raw_data={"confidence": parsed["confidence"]}
                ))

            # Pagination
            if page_num < self.max_pages and self.next_selector:
                next_el = soup.select_one(self.next_selector)
                if next_el and next_el.get("href"):
                    next_url = next_el["href"]
                    if next_url.startswith("/"):
                        parsed_base = urlparse(self.url)
                        next_url = f"{parsed_base.scheme}://{parsed_base.netloc}{next_url}"
                    if next_url == current_url:
                        break
                    current_url = next_url
                    page_num += 1
                else:
                    break
            else:
                break

        return results
