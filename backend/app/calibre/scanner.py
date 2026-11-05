import os
import sqlite3
import logging
from typing import List, Dict, Any, Set
from app.parser import parse_calibre_volumes_and_series, split_series_candidate_parts

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
            # Resolve volume bundles and standalone books
            vols_map = self.get_series_volumes_map()
            for s in results:
                owned = vols_map.get(s["series_name"].lower(), set())
                if not owned:
                    # The volumes map is keyed by both lower-case and normalized names.
                    from app.parser import normalize_text
                    owned = vols_map.get(normalize_text(s["series_name"]), set())
                # CRITICAL: expose the ACTUAL owned volumes.  Consumers such as the
                # staging-duplicate purge must never substitute book_count for this
                # (a series with N books does not imply volumes 1..N are present).
                s["volumes"] = sorted(float(v) for v in (owned or set()))
                if owned:
                    max_owned = max(owned)
                    if max_owned > s["latest_index"]:
                        s["latest_index"] = max_owned

            # Check books not attached to any series (standalones or untagged series)
            existing_norm = {s["series_name"].lower() for s in results}
            cur.execute("""
                SELECT b.id, b.title, GROUP_CONCAT(DISTINCT a.name) AS authors
                FROM books b
                LEFT JOIN books_series_link bsl ON b.id = bsl.book
                LEFT JOIN books_authors_link bal ON b.id = bal.book
                LEFT JOIN authors a ON bal.author = a.id
                WHERE bsl.series IS NULL
                GROUP BY b.id, b.title;
            """)
            for b_row in cur.fetchall():
                b_title = b_row["title"] or ""
                extracted_series, vols = parse_calibre_volumes_and_series(b_title, None, None)
                if extracted_series and vols and extracted_series.lower() not in existing_norm:
                    norm_ext = extracted_series.lower()
                    existing_norm.add(norm_ext)
                    all_owned = vols_map.get(norm_ext, vols)
                    results.append({
                        "series_id": b_row["id"] + 1000000,
                        "series_name": extracted_series,
                        "book_count": 1,
                        "latest_index": max(all_owned) if all_owned else 1.0,
                        "volumes": sorted(float(v) for v in (all_owned or vols or set())),
                        "authors": b_row["authors"] or "Unknown",
                        "publisher": "",
                        "tags": ""
                    })

            return results
        except Exception as e:
            logger.error(f"Error fetching manga series: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_series_volumes_map(self) -> Dict[str, Set[float]]:
        """
        Returns a map of normalized series name -> set of owned volume indices.
        Extracts single volumes as well as volume bundles (e.g. 'Aharen-san is Indecipherable v01-02'
        where Calibre series_index only records 1.0, ensuring all contained volumes {1.0, 2.0}
        are recognized as owned).
        """
        if not self.is_available():
            return {}

        query = """
            SELECT s.id AS series_id, s.name AS ser_name, b.title, b.series_index
            FROM books b
            LEFT JOIN books_series_link bsl ON b.id = bsl.book
            LEFT JOIN series s ON bsl.series = s.id;
        """
        conn = None
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(query)
            res: Dict[str, Set[float]] = {}
            from app.parser import normalize_text
            for row in cur.fetchall():
                s_id = row["series_id"]
                s_name = row["ser_name"] or ""
                b_title = row["title"] or ""
                idx = float(row["series_index"]) if row["series_index"] is not None else None

                extracted_series, vols = parse_calibre_volumes_and_series(b_title, s_name, idx)
                target_series = s_name.strip() or (extracted_series or "").strip()
                if target_series and vols:
                    t_lower = target_series.lower()
                    t_norm = normalize_text(target_series)
                    res.setdefault(t_lower, set()).update(vols)
                    if t_norm:
                        res.setdefault(t_norm, set()).update(vols)
                    if s_id is not None:
                        res.setdefault(str(s_id), set()).update(vols)
                        res.setdefault(s_id, set()).update(vols)
                    parts = split_series_candidate_parts(target_series)
                    if len(parts) > 1:
                        for p in parts:
                            res.setdefault(p.lower(), set()).update(vols)
                            p_norm = normalize_text(p)
                            if p_norm:
                                res.setdefault(p_norm, set()).update(vols)
            return res
        except Exception as e:
            logger.error(f"Error reading volumes map: {e}")
            return {}
        finally:
            if conn:
                conn.close()
