import time
import logging
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.database import MangaDatabase
from app.covers import CoverCache
from app.calibre.scanner import CalibreMangaScanner
from app.calibre.matcher import MangaCalibreMatcher
from app.downloader.qbit import QBitClient
from app.tagger.cbz_tagger import auto_tag_staging_cbz_files
from app.providers.registry import create_provider

logger = logging.getLogger("mangatrax.scheduler")

class MangaScheduler:
    def __init__(self, db: MangaDatabase, cover_cache: CoverCache):
        self.db = db
        self.cover_cache = cover_cache
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def get_qbit_client(self) -> QBitClient:
        settings = self.db.get_all_settings()
        return QBitClient(
            base_url=settings.get("qbit_url", "http://localhost:8080"),
            username=settings.get("qbit_username", "admin"),
            password=settings.get("qbit_password", "adminadmin")
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
            synced_count = self.db.sync_calibre_series(series_list)

            # Build matcher
            volumes_map = scanner.get_all_volumes_per_series()
            matcher = MangaCalibreMatcher(series_list, volumes_map)

            # Check pending requests against Calibre
            pending = self.db.get_pending_requests()
            resolved = matcher.resolve_pending_requests(pending)

            for item in resolved:
                self.db.update_release_status(item["release_id"], "IN_CALIBRE")
                self.db.log_activity(
                    "REQUEST_RESOLVED",
                    f"Resolved: '{item['title']}' is now available in your Calibre library.",
                    item["reason"]
                )

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
        matcher = MangaCalibreMatcher(series_list, volumes_map)

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
                    total_found += 1
                    # SSRF safe sync cover caching
                    if rel.cover_url:
                        self.cover_cache.cache_sync(rel.cover_url)

                    # Check blacklist
                    if rel.series_name and self.db.is_series_blacklisted(rel.series_name):
                        status = "BLACKLISTED"
                        missing_vols = []
                    else:
                        match_result = matcher.check_release(rel.to_dict())
                        status = match_result["status"]
                        missing_vols = match_result.get("missing_volumes", [])

                    rel_dict = {
                        "guid": rel.guid,
                        "title": rel.title,
                        "series_name": rel.series_name,
                        "is_bundle": rel.is_bundle,
                        "volume_start": rel.volume_start,
                        "volume_end": rel.volume_end,
                        "volumes": rel.volumes,
                        "missing_volumes": missing_vols,
                        "download_url": rel.download_url,
                        "link": rel.link,
                        "cover_url": rel.cover_url,
                        "source_feed_name": feed_name,
                        "status": status,
                        "download_status": "IDLE"
                    }

                    is_new, rel_id = self.db.upsert_release(rel_dict)

                    # Auto-download trigger for REQUESTED releases
                    if status == "REQUESTED":
                        feed_requested += 1
                        total_requested += 1

                        if is_new and global_auto_download and feed.get("auto_download"):
                            success = qbit.add_torrent(
                                url_or_magnet=rel.download_url,
                                savepath=staging_dir,
                                category="manga",
                                tags="mangatrax"
                            )
                            if success:
                                total_sent_to_downloader += 1
                                self.db.update_download_status(rel_id, "DOWNLOADING")
                                self.db.log_activity(
                                    "AUTO_DOWNLOAD",
                                    f"Sent to downloader: '{rel.title}' (Missing: {missing_vols})",
                                    f"Source: {feed_name}"
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

    # --- Background Loop ---
    def _worker_loop(self):
        logger.info("MangaTrax background worker loop started.")
        last_calibre_sync = 0
        last_feed_check = 0
        last_tag_check = 0

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
