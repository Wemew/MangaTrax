import os
import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

class MangaDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
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
                    book_count INTEGER DEFAULT 0,
                    latest_index REAL DEFAULT 0.0,
                    is_monitored INTEGER DEFAULT 1,
                    website_url TEXT DEFAULT '',
                    last_sync TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

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

            # Default settings
            cur.execute("SELECT count(*) FROM app_settings;")
            if cur.fetchone()[0] == 0:
                defaults = {
                    "calibre_path": os.getenv("CALIBRE_DIR", "/calibre"),
                    "qbit_url": os.getenv("QBIT_URL", "http://localhost:8080"),
                    "qbit_username": os.getenv("QBIT_USER", "admin"),
                    "qbit_password": os.getenv("QBIT_PASS", "adminadmin"),
                    "staging_dir": os.getenv("STAGING_DIR", "/staging"),
                    "auto_download": "true",
                    "auto_tag": "true",
                    "calibre_sync_interval_minutes": "15",
                    "feed_check_interval_minutes": "60",
                    "discord_webhook_url": ""
                }
                for k, v in defaults.items():
                    cur.execute("INSERT INTO app_settings (key, value) VALUES (?, ?);", (k, v))

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
                        guid, title, series_name, is_bundle, volume_start,
                        volume_end, volumes_json, missing_vols_json, download_url,
                        link, cover_url, source_feed_name, status, download_status, torrent_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    guid,
                    rel["title"],
                    rel.get("series_name"),
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
            return rows

    def get_pending_requests(self) -> List[Dict[str, Any]]:
        return self.get_releases(status="REQUESTED", limit=1000)

    def get_discovery_releases(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT r.* 
                FROM releases r
                WHERE (r.volume_start = 1.0 OR r.volume_start IS NULL)
                  AND r.status NOT IN ('IN_CALIBRE', 'BLACKLISTED')
                  AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                  AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_tracking WHERE is_monitored = 1))
                ORDER BY r.updated_at DESC LIMIT ?;
            """, (limit,))
            rows = [dict(row) for row in cur.fetchall()]
            for r in rows:
                r["volumes"] = json.loads(r["volumes_json"] or "[]")
            return rows

    def track_discovered_series(self, series_name: str, release_id: Optional[int] = None) -> bool:
        clean = series_name.strip()
        if not clean:
            return False
        from urllib.parse import quote_plus
        with self.get_connection() as conn:
            cur = conn.cursor()
            default_link = f"https://www.mangaupdates.com/series.html?search={quote_plus(clean)}"
            cur.execute("""
                INSERT INTO series_tracking (name, book_count, latest_index, is_monitored, website_url, last_sync)
                VALUES (?, 0, 0.0, 1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET is_monitored = 1;
            """, (clean, default_link))
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

            cur.execute("""
                SELECT count(*) 
                FROM releases r
                WHERE (r.volume_start = 1.0 OR r.volume_start IS NULL)
                  AND r.status NOT IN ('IN_CALIBRE', 'BLACKLISTED')
                  AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
                  AND (r.series_name IS NULL OR lower(r.series_name) NOT IN (SELECT lower(name) FROM series_tracking WHERE is_monitored = 1));
            """)
            discovered_count = cur.fetchone()[0]

            return {
                "monitored_series": monitored_series,
                "pending_requests": pending_requests,
                "acquired_books": acquired_books,
                "active_downloads": active_downloads,
                "discovered_count": discovered_count
            }
