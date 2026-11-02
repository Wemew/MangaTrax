import time
import logging
import urllib.parse
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime

from bs4 import BeautifulSoup

from app.providers.base import BaseFeedProvider, ReleaseItem
from app.parser import extract_manga_bundle_and_volumes, is_chapter_only, is_light_novel

logger = logging.getLogger("mangatrax.providers.nyaa")

NYAA_BASE_URL = "https://nyaa.si"
NYAA_MANGA_CAT = "3_1"  # Literature - English-translated

class NyaaMangaProvider(BaseFeedProvider):
    """
    Dedicated, zero-authentication Nyaa provider and historical crawler for Manga releases.
    Supports:
      1. Paginated historical category crawls (p=1, p=2, ... p=N).
      2. Targeted series search (q="Series Name").
      3. Date/timestamp bounded crawl to go back in time to a certain cutoff.
      4. Rich post extraction: direct view URL, .torrent file, magnet, size, seeders, date.
    """
    def __init__(self, name: str = "Nyaa Manga", url: str = "https://nyaa.si/?f=0&c=3_1", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, url, config)
        self.category = self.config.get("category", NYAA_MANGA_CAT)
        self.max_pages = int(self.config.get("max_pages", 2))
        self.delay_seconds = float(self.config.get("delay_seconds", 1.5))
        self.query = self.config.get("query", "")

    def fetch_releases(self) -> List[ReleaseItem]:
        """Fetches releases according to configured query and max_pages."""
        return self.crawl_pages(max_pages=self.max_pages, query=self.query)

    def search_series(
        self,
        series_name: str,
        max_pages: int = 3,
        alt_names: Optional[List[str]] = None,
        stop_check: Optional[Callable[[], bool]] = None
    ) -> List[ReleaseItem]:
        """
        Executes a targeted search on Nyaa for a specific series.
        Also searches any alternate names (e.g. Romaji or English aliases) if given.
        """
        clean_name = series_name.strip()
        if not clean_name:
            return []

        all_results = []
        seen_links = set()

        if stop_check and stop_check():
            return []

        # Search primary series title
        primary_results = self.crawl_pages(max_pages=max_pages, query=clean_name, stop_check=stop_check)
        for r in primary_results:
            if r.link not in seen_links:
                seen_links.add(r.link)
                all_results.append(r)

        # Search alt names if provided
        if alt_names:
            for alt in alt_names:
                if stop_check and stop_check():
                    break
                alt_clean = (alt or "").strip()
                if alt_clean and alt_clean.lower() != clean_name.lower():
                    time.sleep(self.delay_seconds)
                    if stop_check and stop_check():
                        break
                    alt_res = self.crawl_pages(max_pages=max_pages, query=alt_clean, stop_check=stop_check)
                    for r in alt_res:
                        if r.link not in seen_links:
                            seen_links.add(r.link)
                            all_results.append(r)

        return all_results

    def crawl_pages(
        self,
        max_pages: int = 1,
        start_page: int = 1,
        query: Optional[str] = None,
        date_cutoff: Optional[datetime] = None,
        delay: Optional[float] = None,
        progress_cb: Optional[Any] = None,
        stop_check: Optional[Any] = None
    ) -> List[ReleaseItem]:
        """
        Crawls through Nyaa pages sequentially with polite rate-limiting.
        Optionally stops when reaching a date_cutoff or max_pages.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        delay_sec = delay if delay is not None else self.delay_seconds
        results: List[ReleaseItem] = []
        seen_links = set()

        for page in range(start_page, start_page + max_pages):
            if stop_check and stop_check():
                logger.info("Nyaa crawl stop requested by caller.")
                break

            # Construct page URL
            params = {
                "f": "0",
                "c": self.category,
                "s": "id",
                "o": "desc",
                "p": str(page)
            }
            if query:
                params["q"] = query

            page_url = f"{NYAA_BASE_URL}/?{urllib.parse.urlencode(params)}"
            if progress_cb:
                progress_cb(page, max_pages, page_url)

            try:
                req = urllib.request.Request(page_url, headers=headers)
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    if resp.status == 404:
                        logger.info(f"Nyaa reached end of results at page {page}.")
                        break
                    html = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    logger.info(f"Nyaa reached end of results at page {page}.")
                    break
                logger.warning(f"Error fetching Nyaa page {page} ({page_url}): {e}")
                break
            except Exception as e:
                logger.warning(f"Error fetching Nyaa page {page} ({page_url}): {e}")
                break

            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table.torrent-list tbody tr")
            if not rows:
                logger.info(f"No torrent rows found on Nyaa page {page}. End of search.")
                break

            reached_date_cutoff = False
            for r in rows:
                # 1. Parse Title & View Link from 2nd Column
                links = r.select("td:nth-of-type(2) a")
                # Filter out comment icon links (which have class 'comments')
                title_links = [a for a in links if not a.get("class") or "comments" not in a.get("class", [])]
                if not title_links:
                    continue
                title_el = title_links[-1]
                raw_title = title_el.get_text(strip=True)
                view_href = title_el.get("href", "")
                view_url = f"{NYAA_BASE_URL}{view_href}" if view_href.startswith("/") else view_href

                if not raw_title or not view_url or view_url in seen_links:
                    continue
                seen_links.add(view_url)

                # 2. Discard pure chapter-only releases
                if is_chapter_only(raw_title):
                    continue

                # 3. Discard Light Novels and non-manga literature
                if is_light_novel(raw_title):
                    logger.debug(f"Discarding light novel release: {raw_title}")
                    continue

                # 3. Parse Download & Magnet links from 3rd Column
                dl_links = r.select("td:nth-of-type(3) a")
                torrent_url = ""
                magnet_url = ""
                for a in dl_links:
                    h = a.get("href", "")
                    if h.endswith(".torrent") or "/download/" in h:
                        torrent_url = f"{NYAA_BASE_URL}{h}" if h.startswith("/") else h
                    elif h.startswith("magnet:"):
                        magnet_url = h

                download_url = magnet_url or torrent_url or view_url

                # 4. File Size from 4th Column
                size_td = r.select_one("td:nth-of-type(4)")
                post_size = size_td.get_text(strip=True) if size_td else ""

                # 5. Timestamp & Date Cutoff from 5th Column
                date_td = r.select_one("td:nth-of-type(5)")
                pub_date = date_td.get_text(strip=True) if date_td else ""
                raw_timestamp = date_td.get("data-timestamp") if date_td else None

                if date_cutoff and raw_timestamp:
                    try:
                        item_dt = datetime.utcfromtimestamp(int(raw_timestamp))
                        if item_dt < date_cutoff:
                            reached_date_cutoff = True
                            logger.info(f"Reached date cutoff {date_cutoff} at release date {item_dt}. Stopping.")
                            break
                    except Exception:
                        pass

                # 6. Seeders & Leechers
                seeds_td = r.select_one("td:nth-of-type(6)")
                seeders_val = None
                if seeds_td:
                    try:
                        seeders_val = int(seeds_td.get_text(strip=True))
                    except ValueError:
                        pass

                parsed = extract_manga_bundle_and_volumes(raw_title)

                meta_desc = f"Size: {post_size}" if post_size else ""
                if seeders_val is not None:
                    meta_desc += f" | Seeders: {seeders_val}"

                results.append(ReleaseItem(
                    title=raw_title,
                    link=view_url,
                    download_url=download_url,
                    series_name=parsed["series_name"],
                    release_group=parsed.get("release_group"),
                        release_groups=parsed.get("release_groups"),
                    revision=parsed.get("revision"),
                    publisher=parsed.get("publisher"),
                    is_bundle=parsed["is_bundle"],
                    is_complete=parsed.get("is_complete", False),
                    volume_start=parsed["volume_start"],
                    volume_end=parsed["volume_end"],
                    volumes=parsed["volumes"],
                    pub_date=pub_date or datetime.utcnow().isoformat(),
                    description=meta_desc,
                    guid=view_url,
                    source_feed_name=self.name,
                    raw_data={
                        "confidence": parsed["confidence"],
                        "post_size": post_size,
                        "seeders": seeders_val,
                        "torrent_url": torrent_url,
                        "magnet_url": magnet_url
                    }
                ))

            if reached_date_cutoff:
                break

            # Polite delay between pages (respects start_page offset)
            if page < (start_page + max_pages - 1) and delay_sec > 0:
                time.sleep(delay_sec)

        return results
