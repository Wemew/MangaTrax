import time
import random
import logging
import urllib.parse
import urllib.request
import urllib.error
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional, Callable, Tuple
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.providers.base import BaseFeedProvider, ProviderFetchError, ReleaseItem
from app.parser import extract_manga_bundle_and_volumes, is_chapter_only, is_light_novel
from app.nyaa_source import DEFAULT_NYAA_BASE_URL, resolve_nyaa_base_url

logger = logging.getLogger("mangatrax.providers.nyaa")

# Kept for backwards compatibility (imports/tests may reference it); the effective
# base URL is resolved per provider from config["base_url"] -> nyaa_base_url setting
# -> this default.  A mirror/proxy configured by the user is used for EVERY URL:
# crawl pages (backfill), post view URLs, .torrent URLs and relative-href joins.
NYAA_BASE_URL = DEFAULT_NYAA_BASE_URL
NYAA_MANGA_CAT = "3_1"  # Literature - English-translated

# --- Fetch resilience / mirror politeness -----------------------------------
# Only a GENUINE end (HTTP 404 or a parsed page with zero torrent rows) may end a
# crawl normally.  Transient failures (429/5xx/timeouts/empty bodies) are retried
# and, if they persist, raised as ProviderFetchError so a backfill cannot report
# success after one flaky page.
NYAA_FETCH_ATTEMPTS = 3
NYAA_BACKOFF_SECONDS = (1.0, 3.0)   # waits after attempts 1 and 2, plus jitter
NYAA_RETRY_JITTER_SECONDS = 0.5
NYAA_MAX_RETRY_AFTER_SECONDS = 30.0  # cap for an advertised Retry-After
# A mirror/proxy is one serverless function; rapid sequential page requests are
# exactly what trips it into 429.  Never fetch HTML pages closer than this.
NYAA_MIN_PAGE_INTERVAL_SECONDS = 1.0
NYAA_HTML_TIMEOUT_SECONDS = 15.0

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
        # Mirror support: config["base_url"] wins over the saved nyaa_base_url
        # setting (injected here by the registry/scheduler); default nyaa.si last.
        self.base_url = resolve_nyaa_base_url(self.config)
        # Crawl observability consumed by the backfill worker: why the crawl stopped
        # and per-page (status, rows, items) info for the log/summary.
        self.last_crawl_reason: str = ""
        self.last_crawl_pages: List[Dict[str, Any]] = []
        self._page_interval: float = NYAA_MIN_PAGE_INTERVAL_SECONDS
        self._last_page_fetch_at: Optional[float] = None

    def _join_url(self, href: str) -> str:
        """Join a mirror-returned href against the configured base URL.

        Absolute hrefs are kept as-is.  Site-relative hrefs ("/view/123") are joined
        to the configured base (including any path prefix), so they are never
        rewritten back to the blocked nyaa.si host.
        """
        value = (href or "").strip()
        if not value:
            return ""
        if value.lower().startswith(("http://", "https://")):
            return value
        if value.startswith("//"):
            scheme = self.base_url.split(":", 1)[0]
            return f"{scheme}:{value}"
        return urllib.parse.urljoin(self.base_url.rstrip("/") + "/", value.lstrip("/"))

    def fetch_releases(self) -> List[ReleaseItem]:
        """Fetches releases according to configured query and max_pages.

        Raises :class:`ProviderFetchError` when the source could not be fetched at
        all (network/HTTP failure on the first page), so a dead Nyaa lookup is a
        visible feed failure instead of an empty "successful" poll.
        """
        return self.crawl_pages(
            max_pages=self.max_pages, query=self.query, raise_on_fetch_error=True,
        )

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

    # --- Fetch resilience / politeness (task-19) ---------------------------
    @staticmethod
    def _retry_after_seconds(headers) -> Optional[float]:
        """Parse a Retry-After header (seconds or HTTP-date), capped for sanity."""
        if not headers:
            return None
        try:
            raw = headers.get("Retry-After") if hasattr(headers, "get") else None
        except Exception:  # pragma: no cover - defensive
            return None
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        seconds: Optional[float]
        try:
            seconds = float(text)
        except ValueError:
            try:
                when = parsedate_to_datetime(text)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                seconds = (when - datetime.now(timezone.utc)).total_seconds()
            except Exception:
                return None
        if seconds is None:
            return None
        return max(0.0, min(float(seconds), NYAA_MAX_RETRY_AFTER_SECONDS))

    def _wait_for_page_interval(self) -> None:
        """Keep at least NYAA_MIN_PAGE_INTERVAL_SECONDS between page fetches.

        The interval also includes the configured delay (max of the two).  State
        lives on the provider, so the backfill's one-page-at-a-time calls are just
        as gentle as a single multi-page crawl.
        """
        if self._last_page_fetch_at is None:
            return
        logger.debug("Nyaa politeness: waiting %.2fs before the next page fetch.", self._page_interval)
        time.sleep(self._page_interval)

    def _fetch_page_html(self, page_url: str, headers: Dict[str, str], page: int) -> Tuple[int, str]:
        """Fetch one HTML page with bounded retries; raise ProviderFetchError if it fails.

        Only HTTP 404 is returned as a genuine end (``(404, "")``).  Everything else
        -- 429, 5xx, timeouts, connection resets, empty bodies -- is retried up to
        NYAA_FETCH_ATTEMPTS times with exponential backoff + jitter, honouring an
        advertised Retry-After.  A persistent failure raises instead of pretending
        the crawl ended, so callers cannot mistake flakiness for "no more pages".
        """
        last_error: Optional[Exception] = None
        last_status: Optional[int] = None
        for attempt in range(1, NYAA_FETCH_ATTEMPTS + 1):
            self._wait_for_page_interval()
            retry_after: Optional[float] = None
            try:
                req = urllib.request.Request(page_url, headers=headers)
                with urllib.request.urlopen(req, timeout=NYAA_HTML_TIMEOUT_SECONDS) as resp:
                    if resp.status == 404:
                        return 404, ""
                    html = resp.read().decode("utf-8", errors="replace")
                if not html or not html.strip():
                    raise ValueError("empty response body")
                return int(getattr(resp, "status", 200) or 200), html
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return 404, ""
                last_error = exc
                last_status = exc.code
                retry_after = self._retry_after_seconds(getattr(exc, "headers", None))
            except Exception as exc:
                last_error = exc
                last_status = None
            finally:
                # Start-to-start spacing guarantees the polite minimum even when the
                # fetch itself was slow.
                self._last_page_fetch_at = time.time()

            if attempt >= NYAA_FETCH_ATTEMPTS:
                break
            if retry_after is not None:
                wait_seconds = retry_after
                logger.warning(
                    "Nyaa page %s fetch failed (attempt %s/%s, HTTP %s); Retry-After %.1fs.",
                    page, attempt, NYAA_FETCH_ATTEMPTS, last_status, wait_seconds,
                )
            else:
                backoff = NYAA_BACKOFF_SECONDS[min(attempt - 1, len(NYAA_BACKOFF_SECONDS) - 1)]
                wait_seconds = backoff + random.uniform(0.0, NYAA_RETRY_JITTER_SECONDS)
                logger.warning(
                    "Nyaa page %s fetch failed (attempt %s/%s): %s; retrying in %.1fs.",
                    page, attempt, NYAA_FETCH_ATTEMPTS, last_error, wait_seconds,
                )
            time.sleep(wait_seconds)

        reason = f"HTTP {last_status}" if last_status is not None else str(last_error)
        self.last_crawl_reason = f"failed on page {page} ({reason})"
        raise ProviderFetchError(
            f"Failed to fetch Nyaa page {page} ({page_url}) after "
            f"{NYAA_FETCH_ATTEMPTS} attempts: {reason}"
        ) from last_error

    def crawl_pages(
        self,
        max_pages: int = 1,
        start_page: int = 1,
        query: Optional[str] = None,
        date_cutoff: Optional[datetime] = None,
        delay: Optional[float] = None,
        progress_cb: Optional[Any] = None,
        stop_check: Optional[Any] = None,
        raise_on_fetch_error: bool = True
    ) -> List[ReleaseItem]:
        """
        Crawls through Nyaa pages sequentially with polite rate-limiting.

        Stop reasons are exposed on ``last_crawl_reason`` (and per-page details on
        ``last_crawl_pages``) so callers can distinguish a genuine end of results
        from a failure: ``max pages reached (N)``, ``reached end of results (page N:
        ...)``, ``stopped by user (page N)``, ``date cutoff reached (page N)`` or
        ``failed on page N (...)``.  Transient page failures are retried; if they
        persist a ProviderFetchError is raised (page 1 always; later pages always
        with the default raise_on_fetch_error=True, which the backfill relies on).
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        delay_sec = delay if delay is not None else self.delay_seconds
        # The user's delay is an override, but never below the mirror-safe floor.
        self._page_interval = max(NYAA_MIN_PAGE_INTERVAL_SECONDS, float(delay_sec or 0.0))
        self.last_crawl_reason = ""
        self.last_crawl_pages = []
        results: List[ReleaseItem] = []
        seen_links = set()
        stop_reason = ""

        for page in range(start_page, start_page + max_pages):
            if stop_check and stop_check():
                stop_reason = f"stopped by user (page {page})"
                logger.info("Nyaa crawl stop requested by caller at page %s.", page)
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

            page_url = f"{self.base_url}/?{urllib.parse.urlencode(params)}"
            if progress_cb:
                progress_cb(page, max_pages, page_url)

            try:
                http_status, html = self._fetch_page_html(page_url, headers, page)
            except ProviderFetchError:
                self.last_crawl_pages.append({
                    "page": page, "status": None, "rows": 0, "items": 0,
                    "kind": "failed", "error": self.last_crawl_reason,
                })
                if raise_on_fetch_error or page == start_page:
                    raise
                stop_reason = self.last_crawl_reason
                logger.warning(
                    "Nyaa crawl stopping on page %s after a fetch failure (partial results kept): %s",
                    page, self.last_crawl_reason,
                )
                break

            if http_status == 404:
                stop_reason = f"reached end of results (page {page}: HTTP 404)"
                self.last_crawl_pages.append({
                    "page": page, "status": 404, "rows": 0, "items": 0,
                    "kind": "end_of_results", "error": "",
                })
                logger.info("Nyaa reached end of results at page %s (HTTP 404).", page)
                break

            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table.torrent-list tbody tr")
            if not rows:
                stop_reason = f"reached end of results (page {page}: no rows)"
                self.last_crawl_pages.append({
                    "page": page, "status": http_status, "rows": 0, "items": 0,
                    "kind": "end_of_results", "error": "",
                })
                logger.info("No torrent rows found on Nyaa page %s. End of results.", page)
                break

            page_items = 0
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
                view_url = self._join_url(view_href)

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
                        torrent_url = self._join_url(h)
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
                        authors=parsed.get("authors"),
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
                page_items += 1

            self.last_crawl_pages.append({
                "page": page, "status": http_status, "rows": len(rows),
                "items": page_items, "kind": "ok", "error": "",
            })
            logger.info(
                "Nyaa crawl page %s: HTTP %s, %s rows, %s item(s) parsed (run total %s).",
                page, http_status, len(rows), page_items, len(results),
            )

            if reached_date_cutoff:
                stop_reason = f"date cutoff reached (page {page})"
                break
        else:
            stop_reason = f"max pages reached ({max_pages})"

        if not stop_reason:
            stop_reason = f"max pages reached ({max_pages})"
        self.last_crawl_reason = stop_reason
        logger.info("Nyaa crawl finished: %s.", stop_reason)
        return results
