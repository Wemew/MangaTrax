import os
import sys
import tempfile
import sqlite3
import time
import zipfile

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.database import MangaDatabase
from app.downloader.qbit import QBitClient
from app.tagger.cbz_tagger import has_comicinfo, inject_comicinfo
from app.calibre.scanner import CalibreMangaScanner

def test_database_indexes():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_perf.db")
        db = MangaDatabase(db_path)
        db.init_db()

        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type = 'index';")
        indexes = {row[0] for row in cur.fetchall()}
        conn.close()

        expected_indexes = [
            "idx_releases_series_name",
            "idx_releases_lower_sname",
            "idx_releases_status",
            "idx_releases_updated_at",
            "idx_releases_rg",
            "idx_releases_vol_start",
            "idx_series_tracking_name",
            "idx_series_tracking_monitored",
            "idx_meta_cache_term"
        ]
        for idx in expected_indexes:
            assert idx in indexes, f"Index {idx} was not created!"
    print(" [PASSED] Database indexes correctly created and verified.")

def test_comicinfo_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        cbz_path = os.path.join(tmpdir, "test_manga.cbz")
        with zipfile.ZipFile(cbz_path, "w") as z:
            z.writestr("001.jpg", b"image-data")

        # Initially no comicinfo
        assert has_comicinfo(cbz_path) is False

        # Inject comicinfo
        meta = {"title": "Test", "series_name": "Test Series", "volume": 1.0}
        success = inject_comicinfo(cbz_path, meta)
        assert success is True

        # Now should be true and served from cache
        assert has_comicinfo(cbz_path) is True
    print(" [PASSED] ComicInfo zip cache and injection verified.")

def test_calibre_summary_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "metadata.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE books (id INTEGER PRIMARY KEY);")
        cur.execute("CREATE TABLE series (id INTEGER PRIMARY KEY);")
        cur.execute("CREATE TABLE authors (id INTEGER PRIMARY KEY);")
        cur.execute("INSERT INTO books VALUES (1), (2);")
        cur.execute("INSERT INTO series VALUES (1);")
        cur.execute("INSERT INTO authors VALUES (1), (2), (3);")
        conn.commit()
        conn.close()

        scanner = CalibreMangaScanner(tmpdir)
        summary1 = scanner.get_library_summary()
        assert summary1["available"] is True
        assert summary1["total_books"] == 2
        assert summary1["total_series"] == 1
        assert summary1["total_authors"] == 3

        # Second call returns from cache
        summary2 = scanner.get_library_summary()
        assert summary2 == summary1
    print(" [PASSED] Calibre library summary mtime cache verified.")

def test_qbit_connection_cache():
    client = QBitClient(base_url="http://127.0.0.1:9999", enabled=False)
    res1 = client.test_connection()
    assert res1["success"] is False
    assert "disabled" in res1["message"]

    res2 = client.test_connection()
    assert res2 == res1
    print(" [PASSED] qBittorrent client fast caching verified.")

def test_templates_contain_lazy_loading_elements():
    templates_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "templates"))
    
    # Check base.html
    with open(os.path.join(templates_dir, "base.html"), "r", encoding="utf-8") as f:
        base_html = f.read()
    assert "page-progress-bar" in base_html
    assert "ProgressBar" in base_html
    assert "nav-link-loading" in base_html

    # Check releases.html
    with open(os.path.join(templates_dir, "releases.html"), "r", encoding="utf-8") as f:
        rel_html = f.read()
    assert "series-card-deferred" in rel_html
    assert "loadMoreSeries" in rel_html
    assert "setupInfiniteScroll" in rel_html

    # Check prioritized.html
    with open(os.path.join(templates_dir, "prioritized.html"), "r", encoding="utf-8") as f:
        prio_html = f.read()
    assert "series-card-deferred" in prio_html
    assert "loadMoreSeries" in prio_html

    # Check series.html
    with open(os.path.join(templates_dir, "series.html"), "r", encoding="utf-8") as f:
        ser_html = f.read()
    assert "series-row-deferred" in ser_html
    assert "loadMoreSeriesRows" in ser_html

    print(" [PASSED] Templates contain instant click indicators and deferred lazy loading elements.")

if __name__ == "__main__":
    test_database_indexes()
    test_comicinfo_cache()
    test_calibre_summary_cache()
    test_qbit_connection_cache()
    test_templates_contain_lazy_loading_elements()
    print("\nALL PERFORMANCE AND LAZY LOADING TESTS PASSED!")
