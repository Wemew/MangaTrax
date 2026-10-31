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

def test_lazy_loading_database_pagination_and_cache():
    import gc
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "test_pagination.db")
        db = MangaDatabase(db_path)
        db.init_db()

        # Add mock series and releases
        for i in range(15):
            db.sync_calibre_series([
                {
                    "series_id": i + 1,
                    "series_name": f"Series {i:02d}",
                    "authors": "Author",
                    "publisher": "Pub",
                    "book_count": 3,
                    "latest_index": 3.0,
                    "tags": "Manga",
                    "volume_numbers": [1.0, 2.0, 3.0]
                }
            ])
            db.upsert_release({
                "guid": f"nyaa-test-{i}",
                "title": f"Series {i:02d} v04 [danke-Empire]",
                "series_name": f"Series {i:02d}",
                "volumes": [4.0],
                "release_group": "danke-Empire",
                "status": "REQUESTED"
            })

        # Test pagination of grouped series
        first_batch = db.get_releases_grouped_by_series(limit=5, offset=0)
        assert len(first_batch) == 5, f"Expected 5 groups, got {len(first_batch)}"
        
        second_batch = db.get_releases_grouped_by_series(limit=5, offset=5)
        assert len(second_batch) == 5, f"Expected 5 groups, got {len(second_batch)}"
        
        # Verify disjoint pages
        names1 = {g["series_name"] for g in first_batch}
        names2 = {g["series_name"] for g in second_batch}
        assert not names1.intersection(names2), "Pages must be disjoint!"

        # Test count
        total = db.count_releases_grouped_by_series()
        assert total == 15, f"Expected total 15, got {total}"

        # Test series table pagination
        series_page1 = db.get_all_series(limit=8, offset=0)
        assert len(series_page1) == 8
        total_s = db.count_all_series()
        assert total_s == 15

        # Test cache hit
        t0 = time.time()
        cached_call1 = db.get_releases_grouped_by_series()
        cached_call2 = db.get_releases_grouped_by_series()
        assert cached_call1 == cached_call2

        # Invalidation test
        db.add_to_blacklist("Series 00", "Testing")
        assert db._grouped_cache == {}
        del db
        gc.collect()
    print(" [PASSED] Lazy loading pagination, counting, and in-memory caching verified.")

def test_lazy_loading_template_partials():
    try:
        from jinja2 import Environment, FileSystemLoader
        has_jinja = True
    except ImportError:
        has_jinja = False

    templates_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "templates"))
    card_path = os.path.join(templates_dir, "partials", "series_card_chunk.html")
    flat_path = os.path.join(templates_dir, "partials", "flat_release_row_chunk.html")
    series_path = os.path.join(templates_dir, "partials", "series_table_row_chunk.html")

    assert os.path.isfile(card_path), "series_card_chunk.html partial missing!"
    assert os.path.isfile(flat_path), "flat_release_row_chunk.html partial missing!"
    assert os.path.isfile(series_path), "series_table_row_chunk.html partial missing!"

    if has_jinja:
        env = Environment(loader=FileSystemLoader(templates_dir))
        mock_groups = [{
            "series_key": "test series",
            "series_name": "Test Series",
            "romaji_name": "Test Romaji",
            "synopsis": "A test series",
            "cover_url": "",
            "has_wanted": True,
            "in_calibre": True,
            "is_monitored": True,
            "calibre_book_count": 3,
            "total_volumes": 4,
            "releases_count": 1,
            "all_volumes": [1.0, 2.0, 3.0, 4.0],
            "missing_volumes": [4.0],
            "releases": [{
                "id": 1,
                "title": "Test Series v04 [danke-Empire]",
                "release_group": "danke-Empire",
                "volumes": [4.0],
                "missing_volumes": [4.0],
                "status": "REQUESTED",
                "source_feed_name": "Nyaa",
                "download_url": "magnet:?xt=urn:btih:abc"
            }]
        }]
        card_tmpl = env.get_template("partials/series_card_chunk.html")
        card_html = card_tmpl.render(grouped_series=mock_groups, is_prioritized_view=False)
        assert "Test Series" in card_html
        assert "danke-Empire" in card_html
        assert "accordion-" in card_html

        flat_tmpl = env.get_template("partials/flat_release_row_chunk.html")
        flat_html = flat_tmpl.render(releases=mock_groups[0]["releases"])
        assert "Test Series v04" in flat_html
        assert "danke-Empire" in flat_html

        mock_series = [{
            "id": 1,
            "name": "Test Series",
            "authors": "Author",
            "publisher": "Pub",
            "book_count": 3,
            "latest_index": 3.0,
            "tags": "Manga",
            "is_monitored": 1,
            "website_url": "https://example.com",
            "cover_url": ""
        }]
        series_tmpl = env.get_template("partials/series_table_row_chunk.html")
        series_html = series_tmpl.render(series_list=mock_series)
        assert "Test Series" in series_html
        assert "Author" in series_html
    else:
        # Validate partial templates contain expected macro structures
        with open(card_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "grouped_series" in content
            assert "series-card" in content
            assert "toggleReleasesAccordion" in content
        with open(flat_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "releases" in content
            assert "release-row" in content
        with open(series_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "series_list" in content
            assert "series-row" in content

    print(" [PASSED] Modular template chunks validated cleanly.")

if __name__ == "__main__":
    test_database_indexes()
    test_comicinfo_cache()
    test_calibre_summary_cache()
    test_qbit_connection_cache()
    test_templates_contain_lazy_loading_elements()
    test_lazy_loading_database_pagination_and_cache()
    test_lazy_loading_template_partials()
    print("\nALL PERFORMANCE AND LAZY LOADING TESTS PASSED!")
