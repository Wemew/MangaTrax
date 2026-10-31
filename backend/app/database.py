import os
import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import logging

logger = logging.getLogger("mangatrax.database")

class MangaDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        db_dir = os.path.dirname(os.path.abspath(db_path))
        try:
            os.makedirs(db_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating database directory '{db_dir}': {e}")
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
        except sqlite3.OperationalError as e:
            uid = getattr(os, "getuid", lambda: "N/A")()
            gid = getattr(os, "getgid", lambda: "N/A")()
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            dir_stat = ""
            try:
                st = os.stat(db_dir)
                dir_stat = f"Directory permissions: mode={oct(st.st_mode)}, uid={st.st_uid}, gid={st.st_gid}"
            except Exception:
                pass
            raise sqlite3.OperationalError(
                f"Failed to open/create database file '{self.db_path}'. {dir_stat}. "
                f"Running as UID={uid}, GID={gid}. "
                f"Please ensure the data directory on your host is writable by UID {uid} "
                f"(e.g. 'chmod -R 777 <host_folder>' on TrueNAS) or configure PUID/PGID without 'user:' in Docker."
            ) from e

        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode = DELETE;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cur = conn.cursor()

            # Manga series tracking (from Calibre)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS series_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calibre_series_id INTEGER,
                    name TEXT UNIQUE NOT NULL,
                    authors TEXT DEFAULT '',
                    publisher TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    book_count INTEGER DEFAULT 0,
                    latest_index REAL DEFAULT 0.0,
                    is_monitored INTEGER DEFAULT 1,
                    website_url TEXT DEFAULT '',
                    last_sync TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Add description column if missing on upgrade
            cur.execute("PRAGMA table_info(series_tracking);")
            cols = [r["name"] for r in cur.fetchall()]
            if "description" not in cols:
                cur.execute("ALTER TABLE series_tracking ADD COLUMN description TEXT DEFAULT '';")

            # Blacklisted manga series
            cur.execute("""
                CREATE TABLE IF NOT EXISTS series_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    reason TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Releases & Bundles table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guid TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    series_name TEXT,
                    release_group TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    mangaupdates_url TEXT DEFAULT '',
                    is_bundle INTEGER DEFAULT 0,
                    volume_start REAL,
                    volume_end REAL,
                    volumes_json TEXT DEFAULT '[]',
                    missing_vols_json TEXT DEFAULT '[]',
                    download_url TEXT,
                    link TEXT,
                    cover_url TEXT,
                    source_feed_name TEXT,
                    status TEXT DEFAULT 'REQUESTED',
                    download_status TEXT DEFAULT 'IDLE',
                    torrent_hash TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Add release_group, description, mangaupdates_url columns if missing on upgrade
            cur.execute("PRAGMA table_info(releases);")
            r_cols = [r["name"] for r in cur.fetchall()]
            if "release_group" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN release_group TEXT DEFAULT '';")
            if "description" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN description TEXT DEFAULT '';")
            if "mangaupdates_url" not in r_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN mangaupdates_url TEXT DEFAULT '';")

            # Upgrade any Nyaa links pointing to raw torrent downloads to their web post view URLs
            cur.execute("""
                UPDATE releases 
                SET link = replace(replace(link, '/download/', '/view/'), '.torrent', '')
                WHERE link LIKE '%nyaa.si/download/%.torrent';
            """)

            # Sanitize legacy uncleaned markdown/HTML descriptions in database
            try:
                cur.execute("SELECT id, description FROM releases WHERE description LIKE '%http%' OR description LIKE '%[%';")
                dirty_rows = cur.fetchall()
                if dirty_rows:
                    from app.metadata.mangaupdates import clean_description_text
                    for r_id, r_desc in dirty_rows:
                        if r_desc:
                            cur.execute("UPDATE releases SET description = ? WHERE id = ?;", (clean_description_text(r_desc), r_id))
            except Exception:
                pass

            # Purge any light novel / non-manga releases inadvertently stored in database
            try:
                cur.execute("SELECT id, title FROM releases;")
                all_releases = cur.fetchall()
                from app.parser import is_light_novel
                ln_ids = [r["id"] for r in all_releases if is_light_novel(r["title"])]
                if ln_ids:
                    cur.executemany("DELETE FROM releases WHERE id = ?;", [(lid,) for lid in ln_ids])
            except Exception:
                pass

            # Prioritized Release Groups table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS prioritized_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    notes TEXT DEFAULT '',
                    priority INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Seed well-known high quality release groups if empty
            cur.execute("SELECT count(*) FROM prioritized_groups;")
            if cur.fetchone()[0] == 0:
                for grp, note in [
                    ("danke-Empire", "Official digital manga releases and high-res rips"),
                    ("1R0N", "High quality digital manga releases"),
                    ("Lu自主规制", "Digital HQ manga volumes"),
                    ("a-sloth", "Clean digital CBZ editions")
                ]:
                    cur.execute("INSERT OR IGNORE INTO prioritized_groups (name, notes) VALUES (?, ?);", (grp, note))

            # Feeds configuration table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    feed_type TEXT NOT NULL,
                    url TEXT NOT NULL,
                    config_json TEXT DEFAULT '{}',
                    enabled INTEGER DEFAULT 1,
                    auto_download INTEGER DEFAULT 1,
                    check_interval_minutes INTEGER DEFAULT 60,
                    last_checked TIMESTAMP,
                    last_status TEXT,
                    last_error TEXT
                );
            """)

            # Activity logs
            cur.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Settings
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            # Manga metadata cache (AniList / Canonical Manga Database)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS manga_metadata_cache (
                    search_term TEXT PRIMARY KEY,
                    canonical_title TEXT NOT NULL,
                    romaji_title TEXT DEFAULT '',
                    cover_url TEXT DEFAULT '',
                    total_volumes INTEGER,
                    status TEXT DEFAULT 'UNKNOWN',
                    synopsis TEXT DEFAULT '',
                    source TEXT DEFAULT 'anilist',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Default settings
            cur.execute("SELECT count(*) FROM app_settings;")
            if cur.fetchone()[0] == 0:
                defaults = {
                    "calibre_path": os.getenv("CALIBRE_DIR", "/calibre"),
                    "qbit_url": os.getenv("QBIT_URL", "http://localhost:8080"),
                    "qbit_username": os.getenv("QBIT_USER", "admin"),
                    "qbit_password": os.getenv("QBIT_PASS", "adminadmin"),
                    "qbit_enabled": "true",
                    "qbit_category": "manga",
                    "qbit_tags": "mangatrax",
                    "staging_dir": os.getenv("STAGING_DIR", "/staging"),
                    "auto_download": "true",
                    "auto_tag": "true",
                    "calibre_sync_interval_minutes": "15",
                    "feed_check_interval_minutes": "60",
                    "backfill_max_pages": "15",
                    "backfill_delay_seconds": "2.0",
                    "discord_webhook_url": ""
                }
                for k, v in defaults.items():
                    cur.execute("INSERT INTO app_settings (key, value) VALUES (?, ?);", (k, v))

            # Ensure in-app downloader keys exist in case of upgrade
            for k, v in [("qbit_enabled", "true"), ("qbit_category", "manga"), ("qbit_tags", "mangatrax")]:
                cur.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?);", (k, v))

            # Seed sample manga feed if empty
            cur.execute("SELECT count(*) FROM feeds;")
            if cur.fetchone()[0] == 0:
                cur.execute("""
                    INSERT INTO feeds (name, feed_type, url, config_json, enabled, auto_download, check_interval_minutes)
                    VALUES (?, ?, ?, ?, 1, 1, 60);
                """, (
                    "Manga Releases (RSS)",
                    "rss",
                    "https://nyaa.si/?page=rss&q=manga+cbz&c=3_1&f=0",
                    json.dumps({"description": "Monitors CBZ Manga releases."})
                ))

            conn.commit()

    # --- Settings ---
    def get_setting(self, key: str, default: str = "") -> str:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = ?;", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?;", (key, value, value))
            conn.commit()

    def get_all_settings(self) -> Dict[str, str]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM app_settings;")
            return {row["key"]: row["value"] for row in cur.fetchall()}

    # --- Series Tracking ---
    def sync_calibre_series(self, calibre_series_list: List[Dict[str, Any]]) -> int:
        updated_count = 0
        from urllib.parse import quote_plus
        with self.get_connection() as conn:
            cur = conn.cursor()
            for s in calibre_series_list:
                name = s["series_name"]
                ser_id = s["series_id"]
                count = s["book_count"]
                latest = s["latest_index"]
                authors = s.get("authors", "")
                publisher = s.get("publisher", "")
                tags = s.get("tags", "")
                default_web_url = f"https://www.mangaupdates.com/series.html?search={quote_plus(name)}"

                cur.execute("""
                    INSERT INTO series_tracking (
                        calibre_series_id, name, authors, publisher, tags,
                        book_count, latest_index, is_monitored, website_url, last_sync
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(name) DO UPDATE SET
                        calibre_series_id = excluded.calibre_series_id,
                        authors = CASE WHEN excluded.authors != '' THEN excluded.authors ELSE series_tracking.authors END,
                        publisher = CASE WHEN excluded.publisher != '' THEN excluded.publisher ELSE series_tracking.publisher END,
                        tags = CASE WHEN excluded.tags != '' THEN excluded.tags ELSE series_tracking.tags END,
                        book_count = excluded.book_count,
                        latest_index = excluded.latest_index,
                        website_url = CASE WHEN series_tracking.website_url = '' OR series_tracking.website_url IS NULL THEN excluded.website_url ELSE series_tracking.website_url END,
                        last_sync = CURRENT_TIMESTAMP;
                """, (ser_id, name, authors, publisher, tags, count, latest, default_web_url))
                updated_count += 1
            conn.commit()
        return updated_count

    def get_all_series(self, monitored_only: bool = False) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            base_sql = """
                SELECT 
                    st.*,
                    (SELECT r.cover_url FROM releases r WHERE lower(r.series_name) = lower(st.name) AND r.cover_url IS NOT NULL AND r.cover_url != '' ORDER BY r.id DESC LIMIT 1) AS cover_url
                FROM series_tracking st
            """
            if monitored_only:
                cur.execute(f"{base_sql} WHERE st.is_monitored = 1 ORDER BY st.name COLLATE NOCASE ASC;")
            else:
                cur.execute(f"{base_sql} ORDER BY st.is_monitored DESC, st.name COLLATE NOCASE ASC;")
            return [dict(row) for row in cur.fetchall()]

    def toggle_series_monitored(self, series_id: int) -> bool:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE series_tracking SET is_monitored = 1 - is_monitored WHERE id = ?;", (series_id,))
            conn.commit()
            cur.execute("SELECT is_monitored FROM series_tracking WHERE id = ?;", (series_id,))
            row = cur.fetchone()
            return bool(row["is_monitored"]) if row else False

    def update_series_link(self, series_id: int, website_url: str):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE series_tracking SET website_url = ? WHERE id = ?;", (website_url.strip(), series_id))
            conn.commit()

    def get_series_templates_map(self) -> Dict[str, Dict[str, Any]]:
        """Returns map of normalized series name -> template dict for ComicInfo.xml metadata injection."""
        series_list = self.get_all_series()
        from app.parser import normalize_text
        res = {}
        for s in series_list:
            norm = normalize_text(s["name"])
            res[norm] = {
                "series_name": s["name"],
                "authors": s.get("authors", ""),
                "publisher": s.get("publisher", ""),
                "tags": s.get("tags", "")
            }
        return res

    # --- Blacklist ---
    def add_to_blacklist(self, series_name: str, reason: str = "") -> bool:
        clean = series_name.strip()
        if not clean:
            return False
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO series_blacklist (name, reason, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET reason = excluded.reason;
            """, (clean, reason))
            # Cancel existing requests
            cur.execute("UPDATE releases SET status = 'BLACKLISTED', updated_at = CURRENT_TIMESTAMP WHERE lower(series_name) = lower(?);", (clean,))
            cur.execute("UPDATE series_tracking SET is_monitored = 0 WHERE lower(name) = lower(?);", (clean,))
            conn.commit()
            return True

    def remove_from_blacklist(self, blacklist_id: int):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM series_blacklist WHERE id = ?;", (blacklist_id,))
            conn.commit()

    def get_blacklisted_series(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM series_blacklist ORDER BY name COLLATE NOCASE ASC;")
            return [dict(row) for row in cur.fetchall()]

    def is_series_blacklisted(self, series_name: str) -> bool:
        if not series_name:
            return False
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM series_blacklist WHERE lower(name) = lower(?);", (series_name.strip(),))
            return cur.fetchone() is not None

    # --- Releases & Bundles ---
    def upsert_release(self, rel: Dict[str, Any]) -> Tuple[bool, int]:
        guid = rel["guid"]
        vols_json = json.dumps(rel.get("volumes") or [])
        missing_json = json.dumps(rel.get("missing_volumes") or [])

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, status, download_status FROM releases WHERE guid = ?;", (guid,))
            existing = cur.fetchone()

            if existing:
                rel_id = existing["id"]
                cur.execute("""
                    UPDATE releases SET
                        title = ?,
                        series_name = ?,
                        release_group = COALESCE(?, release_group),
                        description = CASE WHEN ? != '' AND ? IS NOT NULL THEN ? ELSE description END,
                        mangaupdates_url = CASE WHEN ? != '' AND ? IS NOT NULL THEN ? ELSE mangaupdates_url END,
                        is_bundle = ?,
                        volume_start = ?,
                        volume_end = ?,
                        volumes_json = ?,
                        missing_vols_json = ?,
                        download_url = ?,
                        link = ?,
                        cover_url = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                """, (
                    rel["title"],
                    rel.get("series_name"),
                    rel.get("release_group"),
                    rel.get("description", ""),
                    rel.get("description"),
                    rel.get("description", ""),
                    rel.get("mangaupdates_url", ""),
                    rel.get("mangaupdates_url"),
                    rel.get("mangaupdates_url", ""),
                    1 if rel.get("is_bundle") else 0,
                    rel.get("volume_start"),
                    rel.get("volume_end"),
                    vols_json,
                    missing_json,
                    rel.get("download_url"),
                    rel.get("link"),
                    rel.get("cover_url"),
                    rel_id
                ))
                conn.commit()
                return False, rel_id
            else:
                cur.execute("""
                    INSERT INTO releases (
                        guid, title, series_name, release_group, description, mangaupdates_url,
                        is_bundle, volume_start, volume_end, volumes_json, missing_vols_json,
                        download_url, link, cover_url, source_feed_name, status, download_status, torrent_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    guid,
                    rel["title"],
                    rel.get("series_name"),
                    rel.get("release_group") or "",
                    rel.get("description") or "",
                    rel.get("mangaupdates_url") or "",
                    1 if rel.get("is_bundle") else 0,
                    rel.get("volume_start"),
                    rel.get("volume_end"),
                    vols_json,
                    missing_json,
                    rel.get("download_url"),
                    rel.get("link"),
                    rel.get("cover_url"),
                    rel.get("source_feed_name"),
                    rel.get("status", "REQUESTED"),
                    rel.get("download_status", "IDLE"),
                    rel.get("torrent_hash")
                ))
                rel_id = cur.lastrowid
                conn.commit()
                return True, rel_id

    def update_release_status(self, release_id: int, status: str):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE releases SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (status, release_id))
            conn.commit()

    def update_download_status(self, release_id: int, download_status: str, torrent_hash: Optional[str] = None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE releases 
                SET download_status = ?, torrent_hash = COALESCE(?, torrent_hash), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (download_status, torrent_hash, release_id))
            conn.commit()

    def get_releases(self, status: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT lower(name) FROM prioritized_groups;")
            p_groups = {row[0] for row in cur.fetchall()}

            if status:
                cur.execute("SELECT * FROM releases WHERE status = ? ORDER BY updated_at DESC LIMIT ?;", (status, limit))
            else:
                cur.execute("""
                    SELECT * FROM releases 
                    ORDER BY 
                        CASE status WHEN 'REQUESTED' THEN 1 WHEN 'DISCOVERY' THEN 2 WHEN 'IN_CALIBRE' THEN 3 ELSE 4 END,
                        updated_at DESC 
                    LIMIT ?;
                """, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
                r["missing_volumes"] = json.loads(r["missing_vols_json"] or "[]")
                r_group = (r.get("release_group") or "").strip().lower()
                r["is_prioritized"] = r_group in p_groups if r_group else False
            return rows

    def get_pending_requests(self) -> List[Dict[str, Any]]:
        return self.get_releases(status="REQUESTED", limit=1000)

    def get_release_by_id(self, release_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM releases WHERE id = ?;", (release_id,))
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["volumes"] = json.loads(d.get("volumes_json") or "[]")
                return d
            return None

    def count_discovery_releases(self, search: Optional[str] = None) -> int:
        """
        Returns total count of unique discovered manga series (Volume 1 or unnumbered standalones)
        which are not in Calibre, not monitored in series_tracking, and not blacklisted.
        Deduplicates by series name so each series is counted at most once.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            search_clause = ""
            params = []
            if search and search.strip():
                clean_s = f"%{search.strip().lower()}%"
                search_clause = "AND (lower(r.title) LIKE ? OR lower(r.series_name) LIKE ?)"
                params.extend([clean_s, clean_s])

            query = f"""
                WITH ranked_discovery AS (
                    SELECT r.id,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(trim(coalesce(nullif(r.series_name, ''), r.title)))
                               ORDER BY 
                                   (CASE WHEN r.cover_url IS NOT NULL AND length(trim(r.cover_url)) > 0 THEN 1 ELSE 0 END) DESC,
                                   (CASE WHEN r.description IS NOT NULL AND length(trim(r.description)) > 0 THEN 1 ELSE 0 END) DESC,
                                   (CASE WHEN r.mangaupdates_url IS NOT NULL AND length(trim(r.mangaupdates_url)) > 0 THEN 1 ELSE 0 END) DESC,
                                   r.updated_at DESC,
                                   r.id DESC
                           ) as rn
                    FROM releases r
                    WHERE (r.volume_start = 1.0 OR r.volume_start IS NULL)
                      AND r.status NOT IN ('IN_CALIBRE', 'BLACKLISTED', 'IGNORED')
                      AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                      AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_tracking WHERE is_monitored = 1))
                      {search_clause}
                )
                SELECT count(*) FROM ranked_discovery WHERE rn = 1;
            """
            cur.execute(query, tuple(params))
            row = cur.fetchone()
            return row[0] if row else 0

    def get_discovery_releases(self, limit: int = 24, offset: int = 0, search: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Returns releases that are Volume 1 or unnumbered (standalone),
        which are NOT currently in Calibre, NOT in series_tracking, and NOT blacklisted.
        Deduplicates by series name so each potential manga series appears at most once.
        Supports pagination with limit & offset, and optional keyword search.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            search_clause = ""
            params = []
            if search and search.strip():
                clean_s = f"%{search.strip().lower()}%"
                search_clause = "AND (lower(r.title) LIKE ? OR lower(r.series_name) LIKE ?)"
                params.extend([clean_s, clean_s])

            query = f"""
                WITH ranked_discovery AS (
                    SELECT r.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(trim(coalesce(nullif(r.series_name, ''), r.title)))
                               ORDER BY 
                                   (CASE WHEN r.cover_url IS NOT NULL AND length(trim(r.cover_url)) > 0 THEN 1 ELSE 0 END) DESC,
                                   (CASE WHEN r.description IS NOT NULL AND length(trim(r.description)) > 0 THEN 1 ELSE 0 END) DESC,
                                   (CASE WHEN r.mangaupdates_url IS NOT NULL AND length(trim(r.mangaupdates_url)) > 0 THEN 1 ELSE 0 END) DESC,
                                   r.updated_at DESC,
                                   r.id DESC
                           ) as rn
                    FROM releases r
                    WHERE (r.volume_start = 1.0 OR r.volume_start IS NULL)
                      AND r.status NOT IN ('IN_CALIBRE', 'BLACKLISTED', 'IGNORED')
                      AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                      AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_tracking WHERE is_monitored = 1))
                      {search_clause}
                )
                SELECT * FROM ranked_discovery
                WHERE rn = 1
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?;
            """
            params.extend([limit, offset])
            cur.execute(query, tuple(params))
            rows = [dict(row) for row in cur.fetchall()]
            for r in rows:
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
                # Fallback to cache if description, mangaupdates_url, or canonical English title wasn't directly populated on release
                s_name = r.get("series_name")
                from app.metadata.mangaupdates import clean_description_text
                if r.get("description"):
                    r["description"] = clean_description_text(r["description"])

                if s_name:
                    # Check AniList cache for canonical English title
                    meta_cached = self.get_cached_manga_metadata(s_name.lower())
                    if meta_cached:
                        canonical = meta_cached.get("canonical_title")
                        if canonical and canonical.strip().lower() != s_name.strip().lower():
                            r["series_name"] = canonical
                            r["romaji_name"] = meta_cached.get("romaji_title") or s_name
                        if not r.get("cover_url") and meta_cached.get("cover_url"):
                            r["cover_url"] = meta_cached["cover_url"]

                    # Check MangaUpdates cache
                    if not r.get("description") or not r.get("mangaupdates_url") or not str(r.get("mangaupdates_url", "")).startswith("http"):
                        mu_cached = self.get_cached_manga_metadata(f"mu:{s_name.lower()}")
                        if mu_cached:
                            if not r.get("description") and mu_cached.get("synopsis"):
                                r["description"] = clean_description_text(mu_cached["synopsis"])
                            if mu_cached.get("romaji_title") and str(mu_cached["romaji_title"]).startswith("http"):
                                r["mangaupdates_url"] = mu_cached["romaji_title"]

                if not r.get("mangaupdates_url") or not str(r.get("mangaupdates_url", "")).startswith("http"):
                    from urllib.parse import quote_plus
                    target_query = s_name or r.get("title") or "manga"
                    r["mangaupdates_url"] = f"https://www.mangaupdates.com/series.html?search={quote_plus(target_query)}"
            return rows

    def update_release_details(self, release_id: int, description: Optional[str] = None, link: Optional[str] = None, cover_url: Optional[str] = None, mangaupdates_url: Optional[str] = None):
        if description:
            from app.metadata.mangaupdates import clean_description_text
            description = clean_description_text(description)
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE releases 
                SET link = COALESCE(?, link),
                    cover_url = COALESCE(?, cover_url),
                    description = COALESCE(?, description),
                    mangaupdates_url = COALESCE(?, mangaupdates_url),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (link, cover_url, description, mangaupdates_url, release_id))
            conn.commit()

    def track_discovered_series(self, series_name: str, release_id: Optional[int] = None) -> bool:
        clean = series_name.strip()
        if not clean:
            return False
        from urllib.parse import quote_plus
        with self.get_connection() as conn:
            cur = conn.cursor()
            default_link = f"https://www.mangaupdates.com/series.html?search={quote_plus(clean)}"
            description = ""

            # If release has a valid link or mangaupdates_url, prioritize it
            if release_id:
                cur.execute("SELECT link, mangaupdates_url, description FROM releases WHERE id = ?;", (release_id,))
                row = cur.fetchone()
                if row:
                    if row["mangaupdates_url"]:
                        default_link = row["mangaupdates_url"]
                    elif row["link"] and not row["link"].startswith("magnet:"):
                        default_link = row["link"]
                    if row["description"]:
                        description = row["description"]

            # If description is still empty, check cache
            if not description:
                cached = self.get_cached_manga_metadata(f"mu:{clean.lower()}")
                if cached and cached.get("synopsis"):
                    description = cached["synopsis"]
                    if not default_link or "search=" in default_link:
                        default_link = cached.get("romaji_title") or default_link

            cur.execute("""
                INSERT INTO series_tracking (name, book_count, latest_index, is_monitored, website_url, description, last_sync)
                VALUES (?, 0, 0.0, 1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET 
                    is_monitored = 1,
                    website_url = CASE WHEN series_tracking.website_url = '' OR series_tracking.website_url IS NULL OR series_tracking.website_url LIKE '%search=%' THEN excluded.website_url ELSE series_tracking.website_url END,
                    description = CASE WHEN series_tracking.description = '' OR series_tracking.description IS NULL THEN excluded.description ELSE series_tracking.description END;
            """, (clean, default_link, description))
            if release_id:
                cur.execute("UPDATE releases SET status = 'REQUESTED', updated_at = CURRENT_TIMESTAMP WHERE id = ?;", (release_id,))
            else:
                cur.execute("UPDATE releases SET status = 'REQUESTED', updated_at = CURRENT_TIMESTAMP WHERE lower(series_name) = lower(?);", (clean,))
            conn.commit()
            return True

    # --- Feeds ---
    def get_all_feeds(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM feeds ORDER BY id ASC;")
            return [dict(r) for r in cur.fetchall()]

    def get_feed(self, feed_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM feeds WHERE id = ?;", (feed_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def add_feed(self, name: str, feed_type: str, url: str, config_json: str = "{}", auto_download: bool = True, interval: int = 60) -> int:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO feeds (name, feed_type, url, config_json, enabled, auto_download, check_interval_minutes)
                VALUES (?, ?, ?, ?, 1, ?, ?);
            """, (name, feed_type, url, config_json, 1 if auto_download else 0, interval))
            conn.commit()
            return cur.lastrowid

    def delete_feed(self, feed_id: int):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM feeds WHERE id = ?;", (feed_id,))
            conn.commit()

    def update_feed_status(self, feed_id: int, status: str, error: Optional[str] = None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE feeds SET last_checked = CURRENT_TIMESTAMP, last_status = ?, last_error = ? WHERE id = ?;", (status, error, feed_id))
            conn.commit()

    # --- Activity Logs ---
    def log_activity(self, event_type: str, message: str, details: str = ""):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO activity_logs (event_type, message, details) VALUES (?, ?, ?);", (event_type, message, details))
            conn.commit()

    def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT ?;", (limit,))
            return [dict(r) for r in cur.fetchall()]

    # --- Dashboard Summary ---
    def get_dashboard_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM series_tracking WHERE is_monitored = 1;")
            monitored_series = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM releases WHERE status = 'REQUESTED';")
            pending_requests = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM releases WHERE status = 'IN_CALIBRE';")
            acquired_books = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM releases WHERE download_status = 'DOWNLOADING';")
            active_downloads = cur.fetchone()[0]

            discovered_count = self.count_discovery_releases()

            return {
                "monitored_series": monitored_series,
                "pending_requests": pending_requests,
                "acquired_books": acquired_books,
                "active_downloads": active_downloads,
                "discovered_count": discovered_count
            }

    # --- Debug / Maintenance Operations ---
    def clear_all_descriptions(self) -> int:
        """Clears all stored descriptions and synopses in series_tracking."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE series_tracking SET description = '';")
            count = cur.rowcount
            conn.commit()
            self.log_activity("DEBUG_CLEAR", "Cleared all manga descriptions", f"Reset descriptions across {count} tracked series.")
            return count

    def clear_discovered_series(self) -> int:
        """Clears unreviewed new series from the Discovery page (status='DISCOVERY')."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM releases WHERE status = 'DISCOVERY';")
            count = cur.rowcount
            conn.commit()
            self.log_activity("DEBUG_CLEAR", "Cleared discovered manga series", f"Removed {count} unreviewed discovery releases.")
            return count

    # --- Manga Metadata Cache (AniList) ---
    def get_cached_manga_metadata(self, search_term: str) -> Optional[Dict[str, Any]]:
        clean_key = search_term.strip().lower()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM manga_metadata_cache WHERE lower(search_term) = lower(?);", (clean_key,))
            row = cur.fetchone()
            if row:
                return {
                    "canonical_title": row["canonical_title"],
                    "romaji_title": row["romaji_title"],
                    "cover_url": row["cover_url"],
                    "total_volumes": row["total_volumes"],
                    "status": row["status"],
                    "synopsis": row["synopsis"],
                    "source": row["source"]
                }
            return None

    def cache_manga_metadata(self, search_term: str, data: Dict[str, Any]):
        clean_key = search_term.strip().lower()
        canonical = data.get("canonical_title") or search_term
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO manga_metadata_cache (
                    search_term, canonical_title, romaji_title, cover_url,
                    total_volumes, status, synopsis, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(search_term) DO UPDATE SET
                    canonical_title = excluded.canonical_title,
                    romaji_title = excluded.romaji_title,
                    cover_url = CASE WHEN excluded.cover_url != '' THEN excluded.cover_url ELSE manga_metadata_cache.cover_url END,
                    total_volumes = excluded.total_volumes,
                    status = excluded.status,
                    synopsis = excluded.synopsis,
                    source = excluded.source;
            """, (
                clean_key,
                canonical,
                data.get("romaji_title", ""),
                data.get("cover_url", ""),
                data.get("total_volumes"),
                data.get("status", "UNKNOWN"),
                data.get("synopsis", ""),
                data.get("source", "anilist")
            ))
            conn.commit()

    def get_all_manga_metadata_cache(self) -> Dict[str, Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM manga_metadata_cache;")
            return {row["search_term"].lower(): dict(row) for row in cur.fetchall()}

    def normalize_series_titles_to_english(self, anilist_service) -> int:
        """
        Scans all releases in the database, queries their canonical English titles
        via metadata/AniList, and updates any Romaji titles to their English equivalents.
        """
        updated_count = 0
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT series_name FROM releases WHERE series_name IS NOT NULL AND series_name != '';")
            distinct_series = [row[0] for row in cur.fetchall()]

            for s_name in distinct_series:
                try:
                    meta = anilist_service.resolve_manga_series(s_name)
                    canonical = (meta.get("canonical_title") or meta.get("english_title") or "").strip()
                    if canonical and canonical.lower() != s_name.strip().lower():
                        cur.execute("""
                            UPDATE releases 
                            SET series_name = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE lower(series_name) = lower(?);
                        """, (canonical, s_name))
                        updated_count += cur.rowcount
                except Exception as e:
                    logger.debug(f"Normalization failed for '{s_name}': {e}")

            conn.commit()
            if updated_count > 0:
                self.log_activity(
                    "TITLES_NORMALIZED",
                    f"Converted {updated_count} release titles to official English equivalents."
                )
        return updated_count

    # --- Group Releases by Series ---
    def get_releases_grouped_by_series(self, status: Optional[str] = None, prioritized_only: bool = False) -> List[Dict[str, Any]]:
        """
        Groups releases under canonical series, aggregating all bundle volumes,
        highlighting owned vs missing volumes against Calibre tracking.
        If prioritized_only is True, filters to series having releases from prioritized groups.
        """
        from app.parser import normalize_text

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT lower(name) FROM prioritized_groups;")
            p_groups = {row[0] for row in cur.fetchall()}

            if prioritized_only:
                if status:
                    cur.execute("""
                        SELECT * FROM releases 
                        WHERE status = ? AND lower(COALESCE(release_group, '')) IN (SELECT lower(name) FROM prioritized_groups)
                        ORDER BY updated_at DESC;
                    """, (status,))
                else:
                    cur.execute("""
                        SELECT * FROM releases 
                        WHERE lower(COALESCE(release_group, '')) IN (SELECT lower(name) FROM prioritized_groups)
                        ORDER BY 
                            CASE status WHEN 'REQUESTED' THEN 1 WHEN 'DISCOVERY' THEN 2 WHEN 'IN_CALIBRE' THEN 3 ELSE 4 END,
                            updated_at DESC;
                    """)
            else:
                if status:
                    cur.execute("SELECT * FROM releases WHERE status = ? ORDER BY updated_at DESC;", (status,))
                else:
                    cur.execute("""
                        SELECT * FROM releases 
                        ORDER BY 
                            CASE status WHEN 'REQUESTED' THEN 1 WHEN 'DISCOVERY' THEN 2 WHEN 'IN_CALIBRE' THEN 3 ELSE 4 END,
                            updated_at DESC;
                    """)

            raw_releases = [dict(r) for r in cur.fetchall()]
            for r in raw_releases:
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
                r["missing_volumes"] = json.loads(r["missing_vols_json"] or "[]")
                r_group = (r.get("release_group") or "").strip().lower()
                r["is_prioritized"] = r_group in p_groups if r_group else False

            cur.execute("SELECT * FROM series_tracking;")
            tracked_map = {row["name"].lower(): dict(row) for row in cur.fetchall()}

            cur.execute("SELECT * FROM manga_metadata_cache;")
            cache_map = {row["search_term"].lower(): dict(row) for row in cur.fetchall()}

            groups_dict: Dict[str, Dict[str, Any]] = {}

            for rel in raw_releases:
                s_name = (rel.get("series_name") or "Uncategorized").strip()
                s_key = normalize_text(s_name) or "uncategorized"

                if s_key not in groups_dict:
                    calibre_match = None
                    for t_name_lower, t_row in tracked_map.items():
                        if normalize_text(t_name_lower) == s_key:
                            calibre_match = t_row
                            break

                    meta_match = cache_map.get(s_key) or cache_map.get(s_name.lower()) or {}

                    canonical_title = (
                        meta_match.get("canonical_title")
                        or (calibre_match.get("name") if calibre_match else None)
                        or s_name
                    )

                    cover = (
                        meta_match.get("cover_url")
                        or rel.get("cover_url")
                        or (calibre_match.get("cover_url") if calibre_match else "")
                        or ""
                    )

                    groups_dict[s_key] = {
                        "series_key": s_key,
                        "series_name": canonical_title,
                        "raw_name": s_name,
                        "cover_url": cover,
                        "in_calibre": calibre_match is not None,
                        "is_monitored": bool(calibre_match.get("is_monitored", 0)) if calibre_match else False,
                        "calibre_series_id": calibre_match.get("id") if calibre_match else None,
                        "calibre_book_count": calibre_match.get("book_count", 0) if calibre_match else 0,
                        "calibre_latest_index": calibre_match.get("latest_index", 0.0) if calibre_match else 0.0,
                        "total_volumes": meta_match.get("total_volumes"),
                        "status": meta_match.get("status", "UNKNOWN"),
                        "synopsis": meta_match.get("synopsis", "") or (calibre_match.get("description", "") if calibre_match else ""),
                        "releases": [],
                        "all_volumes_set": set(),
                        "missing_volumes_set": set(),
                        "has_wanted": False,
                        "has_discovery": False,
                        "has_in_calibre": False,
                        "has_prioritized": False
                    }

                group = groups_dict[s_key]
                group["releases"].append(rel)

                for v in rel["volumes"]:
                    group["all_volumes_set"].add(v)
                for mv in rel["missing_volumes"]:
                    group["missing_volumes_set"].add(mv)

                if rel["status"] == "REQUESTED":
                    group["has_wanted"] = True
                elif rel["status"] == "DISCOVERY":
                    group["has_discovery"] = True
                elif rel["status"] == "IN_CALIBRE":
                    group["has_in_calibre"] = True

                if rel.get("is_prioritized"):
                    group["has_prioritized"] = True

                if not group["cover_url"] and rel.get("cover_url"):
                    group["cover_url"] = rel["cover_url"]

            result_groups = []
            for group in groups_dict.values():
                group["all_volumes"] = sorted(list(group["all_volumes_set"]))
                group["missing_volumes"] = sorted(list(group["missing_volumes_set"]))
                group["releases_count"] = len(group["releases"])
                group["wanted_count"] = sum(1 for r in group["releases"] if r["status"] == "REQUESTED")
                group["prioritized_count"] = sum(1 for r in group["releases"] if r.get("is_prioritized"))
                # Remove temporary sets before passing to Jinja templates
                del group["all_volumes_set"]
                del group["missing_volumes_set"]
                result_groups.append(group)

            def sort_key(g):
                if g["has_wanted"] and g["has_prioritized"]:
                    priority = 1
                elif g["has_wanted"]:
                    priority = 2
                elif g["has_prioritized"]:
                    priority = 3
                elif g["is_monitored"]:
                    priority = 4
                elif g["has_discovery"]:
                    priority = 5
                elif g["in_calibre"]:
                    priority = 6
                else:
                    priority = 7
                return (priority, g["series_name"].lower())

            result_groups.sort(key=sort_key)
            return result_groups

    # --- Prioritized Release Groups ---
    def get_prioritized_groups(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT pg.*, 
                       (SELECT count(*) FROM releases r WHERE lower(COALESCE(r.release_group, '')) = lower(pg.name)) as release_count
                FROM prioritized_groups pg
                ORDER BY pg.priority DESC, pg.name COLLATE NOCASE ASC;
            """)
            return [dict(r) for r in cur.fetchall()]

    def add_prioritized_group(self, name: str, notes: str = "", priority: int = 1) -> bool:
        clean = name.strip()
        if not clean:
            return False
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO prioritized_groups (name, notes, priority, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET notes = excluded.notes, priority = excluded.priority;
            """, (clean, notes, priority))
            conn.commit()
            return True

    def remove_prioritized_group(self, group_id: int):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM prioritized_groups WHERE id = ?;", (group_id,))
            conn.commit()

    def is_group_prioritized(self, group_name: str) -> bool:
        if not group_name:
            return False
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM prioritized_groups WHERE lower(name) = lower(?);", (group_name.strip(),))
            return cur.fetchone() is not None

    def get_prioritized_releases(self, status: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        """Returns releases published by prioritized release groups."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if status:
                cur.execute("""
                    SELECT * FROM releases 
                    WHERE status = ? AND lower(COALESCE(release_group, '')) IN (SELECT lower(name) FROM prioritized_groups)
                    ORDER BY updated_at DESC LIMIT ?;
                """, (status, limit))
            else:
                cur.execute("""
                    SELECT * FROM releases 
                    WHERE lower(COALESCE(release_group, '')) IN (SELECT lower(name) FROM prioritized_groups)
                    ORDER BY 
                        CASE status WHEN 'REQUESTED' THEN 1 WHEN 'DISCOVERY' THEN 2 WHEN 'IN_CALIBRE' THEN 3 ELSE 4 END,
                        updated_at DESC 
                    LIMIT ?;
                """, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
                r["missing_volumes"] = json.loads(r["missing_vols_json"] or "[]")
                r["is_prioritized"] = True
            return rows
