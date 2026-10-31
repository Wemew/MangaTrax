import time
import logging
import threading
import re
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.database import MangaDatabase
from app.covers import CoverCache
from app.calibre.scanner import CalibreMangaScanner
from app.calibre.matcher import MangaCalibreMatcher
from app.downloader.qbit import QBitClient
from app.tagger.cbz_tagger import auto_tag_staging_cbz_files
from app.providers.registry import create_provider
from app.providers.nyaa import NyaaMangaProvider
from app.metadata.anilist import AniListMetadataService
from app.metadata.mangaupdates import MangaUpdatesService
from app.parser import is_chapter_only, is_light_novel, is_complete_series_release
from app.notifier.webhook import NotificationService

logger = logging.getLogger("mangatrax.scheduler")

class MangaScheduler:
    def __init__(self, db: MangaDatabase, cover_cache: CoverCache):
        self.db = db
        self.cover_cache = cover_cache
        self.anilist = AniListMetadataService(db=self.db)
        self.mangaupdates = MangaUpdatesService(db=self.db)
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.is_backfilling = False
        self.stop_backfill_requested = False

    def get_notifier(self) -> NotificationService:
        discord = self.db.get_setting("discord_webhook_url", "")
        generic = self.db.get_setting("apprise_url", "") or self.db.get_setting("generic_webhook_url", "")
        return NotificationService(discord_webhook=discord, generic_webhook=generic)

    def get_qbit_client(self) -> QBitClient:
        settings = self.db.get_all_settings()
        enabled_str = str(settings.get("qbit_enabled", "true")).lower()
        is_enabled = enabled_str not in ("false", "0", "no", "off")
        return QBitClient(
            base_url=settings.get("qbit_url", "http://localhost:8080"),
            username=settings.get("qbit_username", "admin"),
            password=settings.get("qbit_password", "adminadmin"),
            enabled=is_enabled
        )

    # --- Calibre Sync Task ---
    def sync_calibre_library(self) -> Dict[str, Any]:
        """
        Scans the Calibre metadata.db in zero-lock read-only mode, updates series tracking,
        and auto-resolves any requests for volumes that have now been added to Calibre.
        """
        calibre_path = self.db.get_setting("calibre_path", "/calibre")
        scanner = CalibreMangaScanner(calibre_path)
        if not scanner.is_available():
            msg = f"Calibre library not accessible at: {calibre_path}"
            logger.warning(msg)
            return {"success": False, "message": msg, "synced_series": 0, "resolved_requests": 0}

        try:
            series_list = scanner.get_all_series()
            volumes_map = scanner.get_all_volumes_per_series()
            synced_count = self.db.sync_calibre_series(series_list, volumes_map=volumes_map)

            # Build matcher
            meta_cache = self.db.get_all_manga_metadata_cache()
            matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

            # Check pending requests against Calibre
            pending = self.db.get_pending_requests()
            resolved = matcher.resolve_pending_requests(pending)

            notifier = self.get_notifier()
            for item in resolved:
                self.db.update_release_status(item["release_id"], "IN_CALIBRE")
                self.db.log_activity(
                    "REQUEST_RESOLVED",
                    f"Resolved: '{item['title']}' is now available in your Calibre library.",
                    item["reason"]
                )
                try:
                    rel_row = self.db.get_release_by_id(item["release_id"])
                    if rel_row:
                        notifier.notify_acquired(rel_row)
                except Exception as e:
                    logger.debug(f"Webhook notification failed for acquired release: {e}")

            # Reconcile all active releases against Calibre matcher to update missing_volumes and statuses
            all_active = self.db.get_releases(limit=5000)
            for r in all_active:
                if r.get("status") in ("REQUESTED", "DISCOVERY", "IN_CALIBRE", "NEW"):
                    res = matcher.check_release(r, auto_request_monitored=True)
                    new_missing = res.get("missing_volumes", [])
                    new_status = res.get("status")
                    old_missing = r.get("missing_volumes", [])
                    old_status = r.get("status")
                    if new_missing != old_missing or new_status != old_status:
                        self.db.update_release_volumes_and_status(
                            r["id"],
                            r.get("volumes", []),
                            new_missing,
                            new_status
                        )

            # Auto-expand any unexpanded complete releases against freshly synced Calibre library
            try:
                self.expand_unresolved_complete_releases(matcher=matcher)
            except Exception as e:
                logger.debug(f"Complete releases expansion error during Calibre sync: {e}")

            summary = f"Synced {synced_count} manga series from Calibre; auto-resolved {len(resolved)} acquired requests."
            logger.info(summary)
            self.db.log_activity("CALIBRE_SYNC", summary)
            return {
                "success": True,
                "message": summary,
                "synced_series": synced_count,
                "resolved_requests": len(resolved)
            }
        except Exception as e:
            err = f"Error syncing Calibre library: {e}"
            logger.error(err)
            self.db.log_activity("CALIBRE_SYNC_ERROR", err)
            return {"success": False, "message": err, "synced_series": 0, "resolved_requests": 0}

    # --- Feed Check Task ---
    def check_all_feeds(self) -> Dict[str, Any]:
        """
        Polls all enabled feeds, parses manga bundles/volumes, matches against Calibre,
        caches cover images, and auto-sends downloads to qBittorrent if enabled.
        """
        feeds = self.db.get_all_feeds()
        total_found = 0
        total_requested = 0
        total_sent_to_downloader = 0

        # Load Calibre context for matching
        calibre_path = self.db.get_setting("calibre_path", "/calibre")
        scanner = CalibreMangaScanner(calibre_path)
        series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
        volumes_map = scanner.get_all_volumes_per_series() if scanner.is_available() else {}
        meta_cache = self.db.get_all_manga_metadata_cache()
        matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

        global_auto_download = self.db.get_setting("auto_download", "true").lower() == "true"
        staging_dir = self.db.get_setting("staging_dir", "/staging")
        qbit = self.get_qbit_client()

        for feed in feeds:
            if not feed.get("enabled"):
                continue

            feed_id = feed["id"]
            feed_name = feed["name"]
            provider = create_provider(feed)
            if not provider:
                self.db.update_feed_status(feed_id, "ERROR", "Unsupported feed provider")
                continue

            try:
                releases = provider.fetch_releases()
                feed_requested = 0

                for rel in releases:
                    # Discard pure chapter updates and light novels
                    if is_chapter_only(rel.title) or is_light_novel(rel.title):
                        continue

                    total_found += 1

                    rel_group = getattr(rel, "release_group", None) or rel.to_dict().get("release_group")

                    # Canonical English series metadata & cover resolution
                    canonical_series = rel.series_name
                    cover_url = rel.cover_url
                    alt_names = []
                    if rel.series_name:
                        # Convert any Romaji title to official English equivalent via AniList
                        meta = self.anilist.resolve_manga_series(rel.series_name)
                        english_title = (meta.get("canonical_title") or meta.get("english_title") or "").strip()
                        if english_title:
                            canonical_series = english_title
                        if not cover_url and meta.get("cover_url"):
                            cover_url = meta["cover_url"]

                        romaji = (meta.get("romaji_title") or "").strip()
                        if romaji and romaji.lower() != canonical_series.lower():
                            alt_names.append(romaji)
                        if rel.series_name.lower() != canonical_series.lower():
                            alt_names.append(rel.series_name)

                        # Extract sub-title candidates if dual-title (e.g. Romaji | English)
                        dual_parts = [p.strip() for p in re.split(r"\s*[|/–—]\s*|\s+-\s+", rel.series_name) if p.strip()]
                        if len(dual_parts) > 1:
                            alt_names.extend(dual_parts)

                        # Match against Calibre
                        calibre_match = matcher.find_series(canonical_series, alt_names=alt_names)
                        if calibre_match and not english_title:
                            calibre_match_name = calibre_match.get("series_name") or calibre_match.get("name", "")
                            if calibre_match_name:
                                canonical_series = calibre_match_name

                    # SSRF safe sync cover caching
                    if cover_url:
                        self.cover_cache.cache_sync(cover_url)

                    # Check blacklist
                    test_name = canonical_series or rel.series_name
                    if test_name and self.db.is_series_blacklisted(test_name):
                        status = "BLACKLISTED"
                        missing_vols = []
                    else:
                        match_payload = rel.to_dict()
                        match_payload["series_name"] = canonical_series
                        match_result = matcher.check_release(match_payload, alt_series_names=alt_names)
                        status = match_result["status"]
                        missing_vols = match_result.get("missing_volumes", [])

                    desc = ""
                    mu_url = ""
                    if status == "DISCOVERY" and canonical_series:
                        try:
                            mu_info = self.mangaupdates.get_series_info(canonical_series)
                            if mu_info:
                                desc = mu_info.get("description", "")
                                mu_url = mu_info.get("url", "")
                                if not cover_url and mu_info.get("cover_url"):
                                    cover_url = mu_info["cover_url"]
                                    self.cover_cache.cache_sync(cover_url)
                        except Exception as e:
                            logger.debug(f"Failed to fetch MangaUpdates info for '{canonical_series}': {e}")

                    rel_dict = {
                        "guid": rel.guid,
                        "title": rel.title,
                        "series_name": canonical_series,
                        "release_group": rel_group,
                        "is_bundle": rel.is_bundle,
                        "volume_start": rel.volume_start,
                        "volume_end": rel.volume_end,
                        "volumes": rel.volumes,
                        "missing_volumes": missing_vols,
                        "download_url": rel.download_url,
                        "link": rel.link,
                        "cover_url": cover_url,
                        "description": desc,
                        "mangaupdates_url": mu_url,
                        "source_feed_name": feed_name,
                        "status": status,
                        "download_status": "IDLE"
                    }

                    is_new, rel_id = self.db.upsert_release(rel_dict)

                    # Auto-download trigger for REQUESTED releases
                    if status == "REQUESTED":
                        feed_requested += 1
                        total_requested += 1

                        if is_new:
                            try:
                                self.get_notifier().notify_new_request(rel_dict)
                            except Exception as e:
                                logger.debug(f"Webhook notification failed for new request: {e}")

                        if is_new and global_auto_download and feed.get("auto_download"):
                            success = qbit.add_torrent(
                                url_or_magnet=rel.download_url,
                                savepath=staging_dir,
                                category="manga",
                                tags="mangatrax"
                            )
                            if success:
                                total_sent_to_downloader += 1
                                torrent_h = qbit.extract_torrent_hash(rel.download_url) if hasattr(qbit, "extract_torrent_hash") else None
                                if not isinstance(torrent_h, str):
                                    torrent_h = None
                                self.db.update_download_status(rel_id, "DOWNLOADING", torrent_h)
                                self.db.log_activity(
                                    "AUTO_DOWNLOAD",
                                    f"Sent to downloader: '{rel.title}' (Missing: {missing_vols})",
                                    f"Source: {feed_name}"
                                )
                                if torrent_h and rel.is_bundle and missing_vols and len(missing_vols) < len(rel.volumes):
                                    sel_res = qbit.apply_selective_volume_download(torrent_h, [float(v) for v in missing_vols])
                                    if sel_res.get("status") == "filtered":
                                        self.db.log_activity(
                                            "SELECTIVE_DOWNLOAD",
                                            f"Selective download applied for '{rel.title}': skipped {len(sel_res.get('skipped_volumes', []))} owned volumes; downloading {len(sel_res.get('kept_volumes', []))} missing volumes."
                                        )

                self.db.update_feed_status(feed_id, "OK", None)
            except Exception as e:
                err_msg = str(e)
                logger.error(f"Error checking feed '{feed_name}': {err_msg}")
                self.db.update_feed_status(feed_id, "ERROR", err_msg)
                self.db.log_activity("FEED_ERROR", f"Feed error in '{feed_name}': {err_msg}")

        summary = f"Feeds checked: {total_found} releases found, {total_requested} requested, {total_sent_to_downloader} sent to downloader."
        logger.info(summary)
        self.db.log_activity("FEED_POLL_COMPLETED", summary)
        return {
            "success": True,
            "message": summary,
            "releases_found": total_found,
            "requested": total_requested,
            "sent_to_downloader": total_sent_to_downloader
        }

    # --- Staging Auto-Tag Task ---
    def tag_staging_files(self) -> Dict[str, Any]:
        """
        Scans staging directory for .cbz archives, matches series against existing Calibre series templates,
        and injects standard ComicInfo.xml metadata.
        """
        staging_dir = self.db.get_setting("staging_dir", "/staging")
        series_templates = self.db.get_series_templates_map()

        try:
            tagged = auto_tag_staging_cbz_files(staging_dir, series_templates)
            for item in tagged:
                self.db.log_activity(
                    "AUTO_TAG_CBZ",
                    f"Injected ComicInfo.xml into '{item['filename']}' for series '{item['series_name']}' Vol {item['volume']}",
                    item["filepath"]
                )

            summary = f"Scanned staging directory: {len(tagged)} .cbz files tagged with ComicInfo.xml."
            logger.info(summary)
            return {"success": True, "message": summary, "tagged_count": len(tagged), "tagged_files": tagged}
        except Exception as e:
            err = f"Error during staging auto-tagging: {e}"
            logger.error(err)
            return {"success": False, "message": err, "tagged_count": 0, "tagged_files": []}

    # --- Historical Backfill Tasks ---
    def stop_backfill(self):
        """Signals the running backfiller to gracefully stop."""
        self.stop_backfill_requested = True
        self.db.set_setting("backfill_status", "stopping")
        self.db.set_setting("backfill_progress", "Stop requested, finishing current item...")

    def get_backfill_status(self) -> Dict[str, Any]:
        try:
            last_p = int(self.db.get_setting("backfill_last_page", "1"))
        except (ValueError, TypeError):
            last_p = 1
        return {
            "running": self.is_backfilling,
            "status": self.db.get_setting("backfill_status", "idle"),
            "progress": self.db.get_setting("backfill_progress", "Idle"),
            "last_page": last_p
        }

    def _process_release(self, rel, feed_name: str, matcher: MangaCalibreMatcher) -> tuple:
        """
        Normalizes Romaji to canonical English title, checks Calibre matcher with aliases,
        pulls MangaUpdates synopsis if Discovery, and upserts release into SQLite.
        Returns (is_new, status, missing_volumes, rel_id, canonical_series).
        """
        if is_chapter_only(rel.title):
            return (False, "CHAPTER_ONLY", [], None, None)
        if is_light_novel(rel.title):
            return (False, "LIGHT_NOVEL", [], None, None)

        rel_group = getattr(rel, "release_group", None) or rel.to_dict().get("release_group")
        is_comp = getattr(rel, "is_complete", False) or is_complete_series_release(rel.title)
        if is_comp:
            rel.is_complete = True
            rel.is_bundle = True

        # 1. Canonical English series metadata & cover resolution
        canonical_series = rel.series_name
        cover_url = rel.cover_url
        alt_names = []
        meta = None
        if rel.series_name:
            meta = self.anilist.resolve_manga_series(rel.series_name)
            english_title = (meta.get("canonical_title") or meta.get("english_title") or "").strip()
            if english_title:
                canonical_series = english_title
            if not cover_url and meta.get("cover_url"):
                cover_url = meta["cover_url"]

            romaji = (meta.get("romaji_title") or "").strip()
            if romaji and romaji.lower() != canonical_series.lower():
                alt_names.append(romaji)
            if rel.series_name.lower() != canonical_series.lower():
                alt_names.append(rel.series_name)

            dual_parts = [p.strip() for p in re.split(r"\s*[|/–—]\s*|\s+-\s+", rel.series_name) if p.strip()]
            if len(dual_parts) > 1:
                alt_names.extend(dual_parts)

            calibre_match = matcher.find_series(canonical_series, alt_names=alt_names)
            if calibre_match and not english_title:
                calibre_match_name = calibre_match.get("series_name") or calibre_match.get("name", "")
                if calibre_match_name:
                    canonical_series = calibre_match_name

        # If release is marked (complete) and has no volume labels, fetch total volume count from external sources
        if is_comp and not rel.volumes:
            total_vols = meta.get("total_volumes") if meta else None
            # If not yet resolved, query AniList with candidate alternate titles
            if not total_vols and alt_names:
                for alt in alt_names:
                    alt_meta = self.anilist.resolve_manga_series(alt)
                    if alt_meta and alt_meta.get("total_volumes"):
                        total_vols = alt_meta["total_volumes"]
                        if not meta or not meta.get("canonical_title"):
                            meta = alt_meta
                        break
            # If still not resolved, query MangaUpdates
            if not total_vols and canonical_series:
                try:
                    mu_info = self.mangaupdates.get_series_info(canonical_series)
                    if mu_info and mu_info.get("total_volumes"):
                        total_vols = mu_info["total_volumes"]
                except Exception as e:
                    logger.debug(f"MangaUpdates volume count lookup failed for '{canonical_series}': {e}")

            if total_vols and int(total_vols) > 0:
                v_count = int(total_vols)
                rel.volume_start = 1.0
                rel.volume_end = float(v_count)
                rel.volumes = [float(v) for v in range(1, v_count + 1)]
                logger.info(f"Resolved complete series '{canonical_series}' to {v_count} volumes from external metadata.")

        if cover_url:
            self.cover_cache.cache_sync(cover_url)

        # 2. Blacklist check
        test_name = canonical_series or rel.series_name
        if test_name and self.db.is_series_blacklisted(test_name):
            status = "BLACKLISTED"
            missing_vols = []
        else:
            match_payload = rel.to_dict()
            match_payload["series_name"] = canonical_series
            match_payload["is_complete"] = is_comp
            match_res = matcher.check_release(match_payload, alt_series_names=alt_names)
            status = match_res["status"]
            missing_vols = match_res.get("missing_volumes", [])

        desc = getattr(rel, "description", "") or ""
        mu_url = ""
        if status == "DISCOVERY" and canonical_series:
            try:
                mu_info = self.mangaupdates.get_series_info(canonical_series)
                if mu_info:
                    if mu_info.get("description"):
                        desc = mu_info["description"]
                    mu_url = mu_info.get("url", "")
                    if not cover_url and mu_info.get("cover_url"):
                        cover_url = mu_info["cover_url"]
                        self.cover_cache.cache_sync(cover_url)
            except Exception as e:
                logger.debug(f"Backfill MangaUpdates fetch failed for '{canonical_series}': {e}")

        rel_dict = {
            "guid": rel.guid,
            "title": rel.title,
            "series_name": canonical_series,
            "release_group": rel_group,
            "is_bundle": rel.is_bundle,
            "is_complete": 1 if is_comp else 0,
            "volume_start": rel.volume_start,
            "volume_end": rel.volume_end,
            "volumes": rel.volumes,
            "missing_volumes": missing_vols,
            "download_url": rel.download_url,
            "link": rel.link,
            "cover_url": cover_url,
            "description": desc,
            "mangaupdates_url": mu_url,
            "source_feed_name": feed_name,
            "status": status,
            "download_status": "IDLE"
        }

        is_new, rel_id = self.db.upsert_release(rel_dict)
        return (is_new, status, missing_vols, rel_id, canonical_series)

    def detect_series_titles(self, series_name: str) -> List[str]:
        """
        Detects and extracts all distinct titles for a series (e.g. English canonical title,
        Romaji alias, parts separated by delimiters like | or /, and titles from DB releases).
        """
        from app.parser import normalize_text

        clean_name = series_name.strip()
        if not clean_name:
            return []

        candidates = []

        # 1. Delimiters in the provided series name (| , / , // , - , — , ;)
        parts = [p.strip() for p in re.split(r"\s*[|/–—]\s*|\s+-\s+|//|;", clean_name) if p.strip()]
        if len(parts) > 1:
            candidates.extend(parts)

        # 2. Parentheses e.g. "English Title (Romaji Title)"
        if "(" in clean_name and ")" in clean_name:
            m_paren = re.findall(r"\((.*?)\)", clean_name)
            base = re.sub(r"\(.*?\)", "", clean_name).strip()
            if base:
                candidates.append(base)
            candidates.extend(m_paren)

        # 3. Inspect existing releases in database under this series
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                prefix = clean_name[:25].strip()
                cur.execute("""
                    SELECT title, series_name FROM releases 
                    WHERE lower(series_name) = lower(?)
                       OR lower(series_name) LIKE ?
                       OR lower(title) LIKE ?
                    LIMIT 20;
                """, (clean_name, f"%{prefix.lower()}%", f"%{prefix.lower()}%"))
                for row in cur.fetchall():
                    for field in [row["series_name"], row["title"]]:
                        if not field:
                            continue
                        sub_parts = [p.strip() for p in re.split(r"\s*[|/–—]\s*|\s+-\s+|//|;", field) if p.strip()]
                        if len(sub_parts) > 1:
                            candidates.extend(sub_parts)
        except Exception as e:
            logger.debug(f"Failed to inspect DB releases for dual titles: {e}")

        # 4. Check AniList metadata cache or query AniList
        initial_check = list(candidates) if candidates else [clean_name]
        for term in initial_check[:3]:
            try:
                meta = self.anilist.resolve_manga_series(term)
                if meta:
                    canon = (meta.get("canonical_title") or meta.get("english_title") or "").strip()
                    romaji = (meta.get("romaji_title") or "").strip()
                    if canon:
                        candidates.append(canon)
                    if romaji:
                        candidates.append(romaji)
            except Exception:
                pass

        # 5. Clean, strip volume keywords, and deduplicate
        search_titles: List[str] = []
        seen_norms = set()
        for c in candidates or [clean_name]:
            c_clean = re.sub(r"\[.*?\]|\(.*?\)", "", c).strip(" -–—:,|./")
            c_clean = re.sub(r"\b(?:volumes?|volume|vol|book|v)\b\.?\s*\d+.*$", "", c_clean, flags=re.IGNORECASE).strip(" -–—:,|./")
            norm = normalize_text(c_clean)
            if norm and len(norm) >= 3 and norm not in seen_norms:
                seen_norms.add(norm)
                search_titles.append(c_clean)

        # If multiple distinct sub-titles exist, drop any giant combined concatenation string
        if len(search_titles) > 1:
            filtered = []
            for t in search_titles:
                t_norm = normalize_text(t)
                is_combined = False
                for other in search_titles:
                    if t != other and len(t) > len(other) and normalize_text(other) in t_norm:
                        if len(t) >= len(other) + 15:
                            is_combined = True
                            break
                if not is_combined:
                    filtered.append(t)
            if filtered:
                search_titles = filtered

        return search_titles or [clean_name]

    def backfill_series(self, series_name: str, max_pages: int = 3) -> Dict[str, Any]:
        """
        Targeted historical backfill for an individual manga series on Nyaa.
        Queries all past volume releases & bundles on Nyaa for the series and resolves them against Calibre.
        When multiple titles are detected (e.g. Romaji and English), searches both titles on Nyaa.
        """
        clean_name = series_name.strip()
        if not clean_name:
            return {"status": "error", "message": "Series name required"}

        # 1. Detect all distinct series titles (e.g. English title, Romaji title, delimiter parts)
        detected_titles = self.detect_series_titles(clean_name)
        primary_title = detected_titles[0]
        alt_titles = detected_titles[1:]

        title_display = " & ".join(f"'{t}'" for t in detected_titles)
        self.db.set_setting("backfill_progress", f"Backfilling {title_display} on Nyaa (up to {max_pages} pages each)...")
        logger.info(f"Starting targeted backfill for {title_display} on Nyaa (max_pages={max_pages})")

        # 2. Build Calibre matcher context
        calibre_path = self.db.get_setting("calibre_path", "/calibre")
        scanner = CalibreMangaScanner(calibre_path)
        series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
        volumes_map = scanner.get_all_volumes_per_series() if scanner.is_available() else {}
        meta_cache = self.db.get_all_manga_metadata_cache()
        matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

        global_auto_download = self.db.get_setting("auto_download", "true").lower() == "true"
        staging_dir = self.db.get_setting("staging_dir", "/staging")
        qbit = self.get_qbit_client()

        # 3. Query Nyaa targeted series search across primary and alternate titles
        nyaa = NyaaMangaProvider(name=f"Nyaa: {primary_title}", config={"delay_seconds": 1.0})
        releases = nyaa.search_series(
            primary_title,
            max_pages=max_pages,
            alt_names=alt_titles,
            stop_check=lambda: self.stop_backfill_requested
        )

        total_found = 0
        total_requested = 0
        total_sent_to_downloader = 0

        for rel in releases:
            if self.stop_backfill_requested:
                break
            is_new, status, missing_vols, rel_id, _ = self._process_release(rel, f"Nyaa: {primary_title}", matcher)
            if status in ("CHAPTER_ONLY", "LIGHT_NOVEL"):
                continue

            total_found += 1
            if status == "REQUESTED":
                total_requested += 1
                if is_new:
                    try:
                        rel_data = self.db.get_release_by_id(rel_id) if rel_id else None
                        if rel_data:
                            self.get_notifier().notify_new_request(rel_data)
                    except Exception as e:
                        logger.debug(f"Webhook notification failed for backfill request: {e}")
                if is_new and global_auto_download and rel_id:
                    success = qbit.add_torrent(
                        url_or_magnet=rel.download_url,
                        savepath=staging_dir,
                        category="manga",
                        tags="mangatrax"
                    )
                    if success:
                        total_sent_to_downloader += 1
                        torrent_h = qbit.extract_torrent_hash(rel.download_url) if hasattr(qbit, "extract_torrent_hash") else None
                        if not isinstance(torrent_h, str):
                            torrent_h = None
                        self.db.update_download_status(rel_id, "DOWNLOADING", torrent_h)
                        if torrent_h and rel.is_bundle and missing_vols and len(missing_vols) < len(rel.volumes):
                            qbit.apply_selective_volume_download(torrent_h, [float(v) for v in missing_vols])

        summary = f"Backfilled {title_display}: found {total_found} releases ({total_requested} wanted/missing volumes, {total_sent_to_downloader} sent to downloader)."
        self.db.log_activity("SERIES_BACKFILLED", summary)
        return {
            "status": "success",
            "message": summary,
            "series_name": primary_title,
            "searched_titles": detected_titles,
            "releases_found": total_found,
            "requested": total_requested,
            "sent_to_downloader": total_sent_to_downloader
        }

    def run_series_backfill_async(self, series_name: str, max_pages: int = 3) -> Dict[str, Any]:
        """
        Launches targeted series backfill in a background daemon thread,
        allowing the user to navigate pages while tracking live progress globally.
        """
        if self.is_backfilling:
            return {"status": "in_progress", "message": "Another backfill is already running"}

        self.is_backfilling = True
        self.stop_backfill_requested = False

        def _worker():
            self.db.set_setting("backfill_status", "running")
            self.db.set_setting("backfill_progress", f"Backfilling '{series_name}' on Nyaa (up to {max_pages} pages)...")
            try:
                res = self.backfill_series(series_name, max_pages=max_pages)
                self.db.set_setting("backfill_status", "idle")
                self.db.set_setting("backfill_progress", res.get("message", f"Backfill for '{series_name}' complete."))
            except Exception as e:
                logger.error(f"Error in async series backfill: {e}")
                self.db.set_setting("backfill_status", "error")
                self.db.set_setting("backfill_progress", f"Backfill failed: {e}")
            finally:
                self.is_backfilling = False

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return {"status": "started", "message": f"Historical backfill for '{series_name}' started in background."}

    def run_polite_backfill(self, max_pages: Optional[int] = None, delay_seconds: Optional[float] = None, start_page: int = 1) -> Dict[str, Any]:
        """
        Runs a polite historical backfill in a background daemon thread:
        Paginates backwards through Nyaa Manga releases (starting from start_page up to max_pages) and configured feeds,
        populating newly discovered series (Vol. 1 & standalones) and missing volumes for tracked series.
        """
        if self.is_backfilling:
            return {"status": "in_progress", "message": "Backfill already running"}

        start_p = max(1, int(start_page or 1))
        self.is_backfilling = True
        self.stop_backfill_requested = False
        t = threading.Thread(
            target=self._run_polite_backfill_worker,
            args=(max_pages, delay_seconds, start_p),
            daemon=True
        )
        t.start()
        return {"status": "started", "message": f"Polite backfill started from page {start_p} in background"}

    def _run_polite_backfill_worker(self, max_pages: Optional[int] = None, delay_seconds: Optional[float] = None, start_page: int = 1):
        if max_pages is None:
            try:
                max_pages = int(self.db.get_setting("backfill_max_pages", "15"))
            except ValueError:
                max_pages = 15

        if delay_seconds is None:
            try:
                delay_seconds = float(self.db.get_setting("backfill_delay_seconds", "2.0"))
            except ValueError:
                delay_seconds = 2.0

        effective_max = 9999 if max_pages <= 0 else max_pages
        start_p = max(1, int(start_page or 1))
        end_p = (start_p + effective_max - 1) if max_pages > 0 else 9999
        target_pages_text = f"{max_pages} pages (pages {start_p} to {end_p})" if max_pages > 0 else f"unlimited pages starting from page {start_p}"

        self.db.set_setting("backfill_status", "running")
        self.db.set_setting("backfill_progress", f"Starting polite manga historical backfill ({target_pages_text})...")

        try:
            total_discovered = 0
            total_processed = 0

            calibre_path = self.db.get_setting("calibre_path", "/calibre")
            scanner = CalibreMangaScanner(calibre_path)
            series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
            volumes_map = scanner.get_all_volumes_per_series() if scanner.is_available() else {}
            meta_cache = self.db.get_all_manga_metadata_cache()
            matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

            nyaa_provider = NyaaMangaProvider(name="Nyaa Manga Backfill", config={"delay_seconds": delay_seconds})

            for page_num in range(start_p, end_p + 1):
                if self.stop_backfill_requested:
                    self.db.set_setting("backfill_last_page", str(page_num))
                    break

                self.db.set_setting("backfill_last_page", str(page_num))
                self.db.set_setting(
                    "backfill_progress",
                    f"Crawling Nyaa Manga Page {page_num} of {end_p} (Found {total_discovered} discoveries, {total_processed} processed)..."
                )

                page_releases = nyaa_provider.crawl_pages(
                    max_pages=1,
                    start_page=page_num,
                    delay=0,
                    stop_check=lambda: self.stop_backfill_requested
                )

                if not page_releases:
                    logger.info(f"Nyaa backfill reached end of available pages at page {page_num}.")
                    break

                for rel in page_releases:
                    if self.stop_backfill_requested:
                        break

                    is_new, status, missing_vols, _, _ = self._process_release(rel, "Nyaa Manga", matcher)
                    if status in ("CHAPTER_ONLY", "LIGHT_NOVEL"):
                        continue

                    total_processed += 1
                    if is_new and status == "DISCOVERY":
                        total_discovered += 1

                # Polite delay between pages
                if page_num < end_p and delay_seconds > 0 and not self.stop_backfill_requested:
                    time.sleep(delay_seconds)

            summary = f"Historical backfill complete: crawled up to page {page_num if 'page_num' in locals() else start_p}, processed {total_processed} releases, found {total_discovered} new discoveries."
            self.db.set_setting("backfill_status", "idle")
            self.db.set_setting("backfill_progress", summary)
            self.db.log_activity("BACKFILL_COMPLETED", summary)

        except Exception as e:
            err = f"Backfill error: {e}"
            logger.error(err)
            self.db.set_setting("backfill_status", "error")
            self.db.set_setting("backfill_progress", err)
            self.db.log_activity("BACKFILL_ERROR", err)
        finally:
            self.is_backfilling = False

    def check_and_filter_active_downloads(self) -> int:
        """
        Scans active DOWNLOADING releases that are bundles with missing_volumes.
        If qBittorrent has resolved the metadata, adjusts file priorities so that
        only the missing volumes are downloaded, saving bandwidth and disk space.
        """
        qbit = self.get_qbit_client()
        if not qbit.enabled:
            return 0

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, series_name, torrent_hash, download_url, volumes_json AS volumes, missing_vols_json AS missing_volumes, is_bundle
                FROM releases
                WHERE download_status = 'DOWNLOADING' AND is_bundle = 1;
            """)
            rows = [dict(r) for r in cur.fetchall()]

        filtered_count = 0
        for r in rows:
            h = r.get("torrent_hash")
            if not h and r.get("download_url"):
                h = qbit.extract_torrent_hash(r["download_url"])
                if h:
                    self.db.update_download_status(r["id"], "DOWNLOADING", h)

            if not h:
                continue

            missing = r.get("missing_volumes")
            if isinstance(missing, str):
                import json
                try:
                    missing = json.loads(missing)
                except Exception:
                    missing = []
            if not missing or not isinstance(missing, list):
                continue

            vols = r.get("volumes")
            if isinstance(vols, str):
                import json
                try:
                    vols = json.loads(vols)
                except Exception:
                    vols = []

            # Only need filtering if we already own some volumes in this bundle
            if vols and len(missing) >= len(vols):
                continue

            res = qbit.apply_selective_volume_download(h, [float(v) for v in missing])
            if res.get("status") == "filtered":
                filtered_count += 1
                skipped_vols = res.get("skipped_volumes", [])
                self.db.log_activity(
                    "SELECTIVE_DOWNLOAD",
                    f"Optimized torrent for '{r['title']}': downloading {len(res.get('kept_volumes', []))} missing volumes; skipped {len(skipped_vols)} volumes already in Calibre ({skipped_vols}).",
                    f"Torrent Hash: {h[:10]}..."
                )
        return filtered_count

    def expand_unresolved_complete_releases(self, matcher: Optional[MangaCalibreMatcher] = None) -> int:
        """
        Scans releases in the database that are complete series compilations
        but have no parsed volume numbers. Queries AniList/MangaUpdates for total volume count,
        expands into individual volumes [1.0 .. N], and matches against Calibre to calculate
        missing volumes and accurate status (e.g. IN_CALIBRE, REQUESTED, DISCOVERY).
        """
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, guid, title, series_name, volumes_json, missing_vols_json, status, download_url, link, cover_url
                    FROM releases
                    WHERE is_complete = 1 OR lower(title) LIKE '%(complete)%' OR lower(title) LIKE '%[complete]%' OR lower(title) LIKE '%complete series%';
                """)
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"Error querying unexpanded complete releases: {e}")
            return 0

        unexpanded = []
        for r in rows:
            v_raw = r.get("volumes_json")
            m_raw = r.get("missing_vols_json")
            v_list = []
            m_list = []
            if v_raw:
                try:
                    v_list = json.loads(v_raw)
                except Exception:
                    v_list = []
            if m_raw:
                try:
                    m_list = json.loads(m_raw)
                except Exception:
                    m_list = []
            if not v_list or (v_list and len(m_list) == len(v_list)):
                unexpanded.append(r)

        if not unexpanded:
            return 0

        logger.info(f"Checking {len(unexpanded)} complete series release(s) for volume expansion in background...")

        if not matcher:
            calibre_path = self.db.get_setting("calibre_path", "/calibre")
            scanner = CalibreMangaScanner(calibre_path)
            series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
            volumes_map = scanner.get_all_volumes_per_series() if scanner.is_available() else {}
            meta_cache = self.db.get_all_manga_metadata_cache()
            matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

        expanded_count = 0
        for r in unexpanded:
            rel_id = r["id"]
            title = r["title"]
            s_name = (r.get("series_name") or "").strip()

            candidates = []
            if s_name:
                candidates.append(s_name)
                detected = self.detect_series_titles(s_name)
                for d in detected:
                    if d not in candidates:
                        candidates.append(d)
            candidates.append(title)

            tot = None
            resolved_meta = None

            # If volumes were already resolved, we only need to reconcile against Calibre
            v_existing = []
            if r.get("volumes_json"):
                try:
                    v_existing = json.loads(r["volumes_json"])
                except Exception:
                    v_existing = []
            if v_existing:
                tot = len(v_existing)

            if not tot:
                for cand in candidates:
                    try:
                        m = self.anilist.resolve_manga_series(cand)
                        if m and m.get("total_volumes"):
                            tot = int(m["total_volumes"])
                            resolved_meta = m
                            break
                    except Exception:
                        pass

                if not tot and s_name:
                    try:
                        for cand in candidates:
                            mu = self.mangaupdates.get_series_info(cand)
                            if mu and mu.get("total_volumes"):
                                tot = int(mu["total_volumes"])
                                break
                    except Exception:
                        pass

            if tot and tot > 0:
                vols = [float(v) for v in range(1, tot + 1)]
                v_json = json.dumps(vols)

                # Match against Calibre
                payload = {
                    "series_name": (resolved_meta.get("canonical_title") if resolved_meta else None) or s_name,
                    "title": title,
                    "is_bundle": True,
                    "is_complete": True,
                    "volumes": vols,
                    "volume_start": 1.0,
                    "volume_end": float(tot)
                }
                match_res = matcher.check_release(payload, alt_series_names=candidates)
                new_status = match_res.get("status", r.get("status") or "DISCOVERY")
                missing_vols = match_res.get("missing_volumes", vols)
                m_json = json.dumps(missing_vols)

                try:
                    with self.db.get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            UPDATE releases
                            SET is_bundle = 1, is_complete = 1, volume_start = 1.0, volume_end = ?,
                                volumes_json = ?, missing_vols_json = ?, status = ?
                            WHERE id = ?;
                        """, (float(tot), v_json, m_json, new_status, rel_id))
                        conn.commit()

                    expanded_count += 1
                    logger.info(f"Expanded complete series '{title}' to {tot} volumes (Status: {new_status}, Missing: {len(missing_vols)} vols).")
                except Exception as e:
                    logger.error(f"Error updating expanded complete release {rel_id}: {e}")

        return expanded_count

    # --- Background Loop ---
    def _worker_loop(self):
        logger.info("MangaTrax background worker loop started.")
        # Asynchronously expand any complete series releases that need volume counts from AniList/MangaUpdates
        try:
            self.expand_unresolved_complete_releases()
        except Exception as e:
            logger.debug(f"Initial complete releases expansion error: {e}")

        last_calibre_sync = 0
        last_feed_check = 0
        last_tag_check = 0
        last_dl_check = 0

        while self.running:
            now = time.time()
            try:
                # Interval settings (in minutes)
                calibre_int = float(self.db.get_setting("calibre_sync_interval_minutes", "15")) * 60
                feed_int = float(self.db.get_setting("feed_check_interval_minutes", "60")) * 60
                auto_tag_enabled = self.db.get_setting("auto_tag", "true").lower() == "true"

                # Check Calibre
                if now - last_calibre_sync >= calibre_int:
                    self.sync_calibre_library()
                    last_calibre_sync = time.time()

                # Check Feeds
                if now - last_feed_check >= feed_int:
                    self.check_all_feeds()
                    last_feed_check = time.time()

                # Auto-tag staging every 5 minutes if enabled
                if auto_tag_enabled and (now - last_tag_check >= 300):
                    self.tag_staging_files()
                    last_tag_check = time.time()

                # Check and optimize bundle downloads every 30 seconds
                if now - last_dl_check >= 30:
                    self.check_and_filter_active_downloads()
                    last_dl_check = time.time()

            except Exception as e:
                logger.error(f"Error in background worker loop: {e}")

            time.sleep(10)

        logger.info("MangaTrax background worker loop stopped.")

    def start(self):
        with self._lock:
            if self.running:
                return
            self.running = True
            self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="MangaTraxScheduler")
            self.thread.start()

    def stop(self):
        with self._lock:
            self.running = False
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=2.0)
