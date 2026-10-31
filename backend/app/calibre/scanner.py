import os
import sqlite3
import logging
from typing import List, Dict, Any, Optional, Set

logger = logging.getLogger("mangatrax.calibre")

_SUMMARY_CACHE: Dict[str, Any] = {}

class CalibreMangaScanner:
    """
    Zero-lock reader for Calibre's metadata.db.
    Extracts manga series, volumes owned, authors, publishers, and tags
    for automatic ComicInfo.xml metadata inheritance.
    """
    def __init__(self, calibre_path: str):
        self.calibre_path = calibre_path
        self.db_path = os.path.join(calibre_path, "metadata.db") if not calibre_path.endswith("metadata.db") else calibre_path

    def is_available(self) -> bool:
        return os.path.isfile(self.db_path) and os.access(self.db_path, os.R_OK)

    def _get_connection(self) -> sqlite3.Connection:
        if not self.is_available():
            raise FileNotFoundError(f"Calibre metadata.db not found at: {self.db_path}")

        abs_path = os.path.abspath(self.db_path).replace("\\", "/")
        uri = f"file:{abs_path}?mode=ro&immutable=1"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("PRAGMA query_only = ON;")
            cursor.execute("PRAGMA busy_timeout = 2000;")
            return conn
        except sqlite3.OperationalError as e:
            logger.warning(f"Fallback read-only connection: {e}")
            conn = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            return conn

    def get_library_summary(self) -> Dict[str, Any]:
        if not self.is_available():
            return {"available": False, "total_books": 0, "total_series": 0, "total_authors": 0}

        mtime = 0
        try:
            mtime = os.path.getmtime(self.db_path)
            cached = _SUMMARY_CACHE.get(self.db_path)
            if cached and cached.get("mtime") == mtime:
                return cached["data"]
        except Exception:
            mtime = 0

        conn = None
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM books;")
            total_books = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM series;")
            total_series = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM authors;")
            total_authors = cur.fetchone()[0]
            res = {
                "available": True,
                "db_path": self.db_path,
                "total_books": total_books,
                "total_series": total_series,
                "total_authors": total_authors
            }
            if mtime:
                _SUMMARY_CACHE[self.db_path] = {"mtime": mtime, "data": res}
            return res
        except Exception as e:
            return {"available": False, "error": str(e), "total_books": 0, "total_series": 0, "total_authors": 0}
        finally:
            if conn:
                conn.close()

    def get_all_series(self) -> List[Dict[str, Any]]:
        """
        Retrieves all manga series with book counts, latest volume, authors,
        and templates for publisher and tags (from existing Volume 1 or latest volume).
        """
        if not self.is_available():
            return []

        query = """
            SELECT 
                s.id AS series_id,
                s.name AS series_name,
                COUNT(bsl.book) AS book_count,
                COALESCE(MAX(b.series_index), 0.0) AS latest_index,
                GROUP_CONCAT(DISTINCT a.name) AS authors,
                (
                    SELECT p.name 
                    FROM books_publishers_link bpl
                    JOIN publishers p ON bpl.publisher = p.id
                    JOIN books_series_link bsl2 ON bpl.book = bsl2.book
                    WHERE bsl2.series = s.id LIMIT 1
                ) AS publisher,
                (
                    SELECT GROUP_CONCAT(DISTINCT t.name)
                    FROM books_tags_link btl
                    JOIN tags t ON btl.tag = t.id
                    JOIN books_series_link bsl3 ON btl.book = bsl3.book
                    WHERE bsl3.series = s.id
                ) AS tags
            FROM series s
            JOIN books_series_link bsl ON s.id = bsl.series
            JOIN books b ON bsl.book = b.id
            LEFT JOIN books_authors_link bal ON b.id = bal.book
            LEFT JOIN authors a ON bal.author = a.id
            GROUP BY s.id, s.name
            ORDER BY s.name COLLATE NOCASE ASC;
        """

        conn = None
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(query)
            results = []
            for row in cur.fetchall():
                results.append({
                    "series_id": row["series_id"],
                    "series_name": row["series_name"],
                    "book_count": row["book_count"],
                    "latest_index": float(row["latest_index"]),
                    "authors": row["authors"] or "Unknown",
                    "publisher": row["publisher"] or "",
                    "tags": row["tags"] or ""
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching manga series: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_series_volumes_map(self) -> Dict[str, Set[float]]:
        """Returns a map of normalized series name -> set of owned volume indices."""
        if not self.is_available():
            return {}

        query = """
            SELECT lower(s.name) AS ser_name, b.series_index
            FROM books b
            JOIN books_series_link bsl ON b.id = bsl.book
            JOIN series s ON bsl.series = s.id
            WHERE b.series_index IS NOT NULL;
        """
        conn = None
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(query)
            res: Dict[str, Set[float]] = {}
            for row in cur.fetchall():
                s_name = row["ser_name"]
                vol = round(float(row["series_index"]), 2)
                res.setdefault(s_name, set()).add(vol)
            return res
        except Exception as e:
            logger.error(f"Error reading volumes map: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    def get_all_volumes_per_series(self) -> Dict[str, Set[float]]:
        """Alias for get_series_volumes_map."""
        return self.get_series_volumes_map()
