import os
import sys
import tempfile
import shutil
import json
import sqlite3
import zipfile
import re
import types
import inspect
import asyncio
from typing import Dict, Any, List, Optional, Union, Callable

# Set paths
E2E_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.dirname(E2E_DIR)
BASE_DIR = os.path.dirname(TESTS_DIR)
BACKEND_DIR = os.path.join(BASE_DIR, "backend")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

# Set writable default DATA_DIR
TEMP_PARENT_DIR = tempfile.mkdtemp(prefix="mangatrax_e2e_parent_")
os.environ["DATA_DIR"] = TEMP_PARENT_DIR

# Coroutine runner that executes coroutines synchronously without requiring an OS event loop
def run_coroutine_sync(coro):
    if not inspect.iscoroutine(coro):
        return coro
    try:
        val = coro.send(None)
        while True:
            if inspect.iscoroutine(val):
                sub_res = run_coroutine_sync(val)
                val = coro.send(sub_res)
            elif hasattr(val, "result"):
                val = coro.send(val.result())
            else:
                val = coro.send(val)
    except StopIteration as e:
        return e.value

# Shim asyncio.to_thread if needed
async def _shim_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)

asyncio.to_thread = _shim_to_thread

# Install transparent FastAPI / Jinja2 shims if running inside standalone Calibre Python
try:
    import fastapi
    from fastapi import FastAPI, Request, Form, Query, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
    from fastapi.templating import Jinja2Templates
except ImportError:
    fa = types.ModuleType("fastapi")
    class FastAPI:
        def __init__(self, **kw):
            self.routes = []
            self.middleware_stack = []
        def get(self, path, **kw):
            def dec(f):
                f.__route_path__ = path
                f.__route_method__ = "GET"
                self.routes.append(f)
                return f
            return dec
        def post(self, path, **kw):
            def dec(f):
                f.__route_path__ = path
                f.__route_method__ = "POST"
                self.routes.append(f)
                return f
            return dec
        def delete(self, path, **kw):
            def dec(f):
                f.__route_path__ = path
                f.__route_method__ = "DELETE"
                self.routes.append(f)
                return f
            return dec
        def middleware(self, *a, **kw):
            def dec(f):
                self.middleware_stack.append(f)
                return f
            return dec

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: Any = None):
            self.status_code = status_code
            self.detail = detail
            super().__init__(f"HTTP {status_code}: {detail}")

    class Request:
        def __init__(self, method: str = "GET", path: str = "/", query_params: Optional[Dict[str, Any]] = None, json_data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None):
            self.method = method
            self.url = types.SimpleNamespace(path=path)
            self.query_params = query_params or {}
            self._json = json_data or {}
            self.headers = headers or {}
        async def json(self):
            return self._json

    fa.FastAPI = FastAPI
    fa.HTTPException = HTTPException
    fa.Request = Request
    fa.Form = lambda default=None, **kw: default
    fa.Query = lambda default=None, **kw: default
    sys.modules["fastapi"] = fa

    resp = types.ModuleType("fastapi.responses")
    class Response:
        def __init__(self, content: Union[str, bytes] = b"", status_code: int = 200, headers: Optional[Dict[str, str]] = None, media_type: Optional[str] = None):
            self.content = content.encode("utf-8") if isinstance(content, str) else content
            self.status_code = status_code
            self.headers = headers or {}
            self.media_type = media_type
        @property
        def text(self) -> str:
            if isinstance(self.content, bytes):
                return self.content.decode("utf-8", errors="replace")
            return str(self.content)
        def json(self) -> Any:
            return json.loads(self.text)

    class JSONResponse(Response):
        def __init__(self, content: Any = None, status_code: int = 200, headers: Optional[Dict[str, str]] = None):
            raw = json.dumps(content)
            super().__init__(content=raw, status_code=status_code, headers=headers, media_type="application/json")
            self._json_data = content
        def json(self) -> Any:
            return self._json_data

    class HTMLResponse(Response):
        def __init__(self, content: str = "", status_code: int = 200, headers: Optional[Dict[str, str]] = None):
            super().__init__(content=content, status_code=status_code, headers=headers, media_type="text/html")

    class FileResponse(Response):
        def __init__(self, path: str, status_code: int = 200, headers: Optional[Dict[str, str]] = None, media_type: Optional[str] = None):
            data = b""
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    data = f.read()
            super().__init__(content=data, status_code=status_code, headers=headers, media_type=media_type)

    class RedirectResponse(Response):
        def __init__(self, url: str, status_code: int = 307, headers: Optional[Dict[str, str]] = None):
            h = headers or {}
            h["Location"] = url
            super().__init__(content=b"", status_code=status_code, headers=h)

    resp.Response = Response
    resp.JSONResponse = JSONResponse
    resp.HTMLResponse = HTMLResponse
    resp.FileResponse = FileResponse
    resp.RedirectResponse = RedirectResponse
    sys.modules["fastapi.responses"] = resp

    tpl = types.ModuleType("fastapi.templating")
    class MockTemplate:
        def __init__(self, name: str):
            self.name = name
        def render(self, context: Optional[Dict[str, Any]] = None) -> str:
            return f"<!-- rendered template {self.name} -->"

    class Jinja2Templates:
        def __init__(self, directory: Optional[str] = None):
            self.env = types.SimpleNamespace(filters={})
        def TemplateResponse(self, request: Any = None, name: str = "", context: Optional[Dict[str, Any]] = None):
            return HTMLResponse(f"<!-- Rendered template {name} -->", status_code=200)
        def get_template(self, name: str) -> MockTemplate:
            return MockTemplate(name)

    tpl.Jinja2Templates = Jinja2Templates
    sys.modules["fastapi.templating"] = tpl

# Now safely import application modules
import app.main as main_app
from app.database import MangaDatabase
from app.scheduler import MangaScheduler
from app.covers import CoverCache
from app.calibre.scanner import CalibreMangaScanner
from app.calibre.matcher import MangaCalibreMatcher
from app.downloader.qbit import QBitClient
from app.tagger.cbz_tagger import generate_comicinfo_xml, inject_comicinfo, has_comicinfo, auto_tag_staging_cbz_files
from app.parser import extract_manga_bundle_and_volumes, string_similarity, clean_manga_title
from app.metadata.anilist import AniListMetadataService
from app.metadata.mangaupdates import MangaUpdatesService


# ---------------------------------------------------------------------------
# Hermetic isolation of live external metadata services.
#
# TEST_INFRA.md promises that the E2E suite runs with "zero external network
# connectivity".  Without this guard the suite silently depended on live AniList
# / MangaUpdates responses: `/api/discovery/track` resolves the canonical
# English title over the network and tracks the series under *that* name, which
# made tier 3/4 assertions and staging-tagging flows non-deterministic and
# order-dependent (passing only when AniList happened to be unreachable or
# returned the unchanged title).
#
# Disabling only the two HTTP transports keeps every real code path (DB cache
# lookup, delimiter candidates, fallback to the raw title) intact while making
# the result deterministic: the services behave exactly as they do in an
# offline deployment.
# ---------------------------------------------------------------------------
def _offline_anilist_fetch(self, search_term):
    return None


def _offline_mangaupdates_fetch(self, search_term):
    return None


AniListMetadataService._fetch_from_anilist = _offline_anilist_fetch
MangaUpdatesService._fetch_from_api = _offline_mangaupdates_fetch


class ResponseWrapper:
    """Unified response object providing synchronous .status_code, .json(), .text access."""
    def __init__(self, status_code: int, content: Union[bytes, str] = b"", headers: Optional[Dict[str, str]] = None, json_data: Any = None):
        self.status_code = status_code
        self.content = content.encode("utf-8") if isinstance(content, str) else content
        self.headers = headers or {}
        self._json_data = json_data

    @property
    def text(self) -> str:
        if isinstance(self.content, bytes):
            return self.content.decode("utf-8", errors="replace")
        return str(self.content)

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        try:
            return json.loads(self.text)
        except Exception:
            return None


class E2EClient:
    """Opaque-box test client that invokes API routes directly through the application router."""
    def __init__(self, context: "E2ETestContext"):
        self.context = context

    def _match_route(self, path: str, method: str):
        # Match routes registered in main_app.app
        for r in main_app.app.routes:
            r_method = getattr(r, "__route_method__", None)
            r_path = getattr(r, "__route_path__", None)
            if r_method != method:
                continue

            # Convert OpenAPI path pattern to regex (e.g. /api/feeds/{feed_id} -> /api/feeds/(?P<feed_id>[^/]+))
            pattern = re.sub(r"\{([a-zA-Z0-9_]+)\}", r"(?P<\1>[^/]+)", r_path)
            pattern = f"^{pattern}$"
            m = re.match(pattern, path)
            if m:
                return r, m.groupdict()
        return None, {}

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, json_data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> ResponseWrapper:
        route_func, path_params = self._match_route(path, method)
        if not route_func:
            return ResponseWrapper(status_code=404, json_data={"detail": f"Route not found: {method} {path}"})

        # Build FastAPI Request
        req_cls = sys.modules["fastapi"].Request
        req = req_cls(method=method, path=path, query_params=params or {}, json_data=json_data or {}, headers=headers or {})

        sig = inspect.signature(route_func)
        kwargs = {}
        for param_name, param in sig.parameters.items():
            if param_name == "request":
                kwargs["request"] = req
            elif param_name == "payload":
                kwargs["payload"] = json_data or {}
            elif param_name in path_params:
                val = path_params[param_name]
                if param.annotation == int or (isinstance(param.annotation, type) and issubclass(param.annotation, int)):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                kwargs[param_name] = val
            elif params and param_name in params:
                val = params[param_name]
                if param.annotation == int or (isinstance(param.annotation, type) and issubclass(param.annotation, int)):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                elif param.annotation == bool or (isinstance(param.annotation, type) and issubclass(param.annotation, bool)):
                    val = str(val).lower() in ("true", "1", "yes")
                kwargs[param_name] = val
            elif param.default is not inspect.Parameter.empty:
                kwargs[param_name] = param.default

        try:
            raw_call = route_func(**kwargs)
            raw_resp = run_coroutine_sync(raw_call)

            # Unwrap response
            if hasattr(raw_resp, "status_code"):
                j = None
                if hasattr(raw_resp, "json"):
                    try:
                        j = raw_resp.json()
                    except Exception:
                        pass
                return ResponseWrapper(status_code=raw_resp.status_code, content=getattr(raw_resp, "content", b""), headers=getattr(raw_resp, "headers", {}), json_data=j)
            elif isinstance(raw_resp, dict):
                return ResponseWrapper(status_code=200, json_data=raw_resp)
            elif isinstance(raw_resp, list):
                return ResponseWrapper(status_code=200, json_data=raw_resp)
            else:
                return ResponseWrapper(status_code=200, content=str(raw_resp))
        except sys.modules["fastapi"].HTTPException as e:
            return ResponseWrapper(status_code=e.status_code, json_data={"detail": e.detail})
        except Exception as e:
            return ResponseWrapper(status_code=500, json_data={"detail": str(e), "error": type(e).__name__})

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> ResponseWrapper:
        return self.request("GET", path, params=params, headers=headers)

    def post(self, path: str, json_data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> ResponseWrapper:
        return self.request("POST", path, params=params, json_data=json_data, headers=headers)

    def delete(self, path: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> ResponseWrapper:
        return self.request("DELETE", path, params=params, headers=headers)


class E2ETestContext:
    """Isolated execution sandbox for an opaque-box test scenario."""
    def __init__(self, prefix: str = "mangatrax_e2e_"):
        self.temp_dir = tempfile.mkdtemp(prefix=prefix)
        self.data_dir = os.path.join(self.temp_dir, "data")
        self.staging_dir = os.path.join(self.temp_dir, "staging")
        self.calibre_dir = os.path.join(self.temp_dir, "calibre")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.staging_dir, exist_ok=True)
        os.makedirs(self.calibre_dir, exist_ok=True)

        self.db_path = os.path.join(self.data_dir, "mangatrax.db")
        self.db = MangaDatabase(self.db_path)
        self.db.init_db()

        # Disable external downloader by default to keep tests fully isolated
        self.db.set_setting("qbit_enabled", "false")
        self.db.set_setting("calibre_path", self.calibre_dir)
        self.db.set_setting("staging_dir", self.staging_dir)

        self.cover_cache = CoverCache(self.data_dir)
        self.scheduler = MangaScheduler(self.db, self.cover_cache)

        # Wire active globals in main_app to point to this isolated context.
        # main_app.mangaupdates must be rebound as well: it is a module-level
        # singleton wired to the process-wide database at import time, so any
        # route that uses it would otherwise read/write outside this context
        # (cross-context state leakage).
        self._orig_db = main_app.db
        self._orig_scheduler = main_app.scheduler
        self._orig_cover_cache = main_app.cover_cache
        self._orig_mangaupdates = main_app.mangaupdates

        main_app.db = self.db
        main_app.scheduler = self.scheduler
        main_app.cover_cache = self.cover_cache
        main_app.mangaupdates = MangaUpdatesService(db=self.db)

        self.client = E2EClient(self)

    def close(self):
        main_app.db = self._orig_db
        main_app.scheduler = self._orig_scheduler
        main_app.cover_cache = self._orig_cover_cache
        main_app.mangaupdates = self._orig_mangaupdates
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def create_mock_calibre_library(self, series_defs: List[Dict[str, Any]]) -> str:
        """
        Creates a mock Calibre library metadata.db.
        series_defs: list of dicts:
          {
            "name": "One Piece",
            "author": "Eiichiro Oda",
            "publisher": "VIZ Media",
            "tags": ["Action", "Shounen"],
            "volumes": [1.0, 2.0, 3.0]
          }
        """
        db_file = os.path.join(self.calibre_dir, "metadata.db")
        if os.path.exists(db_file):
            os.remove(db_file)

        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        cur.executescript("""
            CREATE TABLE books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                sort TEXT,
                author_sort TEXT,
                series_index REAL DEFAULT 1.0,
                path TEXT DEFAULT '',
                has_cover INTEGER DEFAULT 0
            );
            CREATE TABLE series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                sort TEXT
            );
            CREATE TABLE books_series_link (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book INTEGER NOT NULL,
                series INTEGER NOT NULL
            );
            CREATE TABLE authors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                sort TEXT,
                link TEXT DEFAULT ''
            );
            CREATE TABLE books_authors_link (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book INTEGER NOT NULL,
                author INTEGER NOT NULL
            );
            CREATE TABLE publishers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                sort TEXT
            );
            CREATE TABLE books_publishers_link (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book INTEGER NOT NULL,
                publisher INTEGER NOT NULL
            );
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
            CREATE TABLE books_tags_link (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book INTEGER NOT NULL,
                tag INTEGER NOT NULL
            );
        """)

        s_id = 1
        a_id = 1
        p_id = 1
        t_id = 1

        for s_data in series_defs:
            cur.execute("INSERT INTO series (id, name) VALUES (?, ?);", (s_id, s_data["name"]))
            cur.execute("INSERT OR IGNORE INTO authors (id, name) VALUES (?, ?);", (a_id, s_data.get("author", "Unknown")))
            cur.execute("INSERT OR IGNORE INTO publishers (id, name) VALUES (?, ?);", (p_id, s_data.get("publisher", "Unknown Publisher")))

            tag_ids = []
            for tag in s_data.get("tags", ["Manga"]):
                cur.execute("INSERT OR IGNORE INTO tags (id, name) VALUES (?, ?);", (t_id, tag))
                tag_ids.append(t_id)
                t_id += 1

            for vol in s_data.get("volumes", [1.0]):
                cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"{s_data['name']} Vol. {int(vol)}", float(vol)))
                b_id = cur.lastrowid
                cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?);", (b_id, s_id))
                cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, ?);", (b_id, a_id))
                cur.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, ?);", (b_id, p_id))
                for tid in tag_ids:
                    cur.execute("INSERT INTO books_tags_link (book, tag) VALUES (?, ?);", (b_id, tid))

            s_id += 1
            a_id += 1
            p_id += 1

        conn.commit()
        conn.close()
        return db_file

    def create_mock_cbz(self, filename: str, pages: int = 3, existing_xml: Optional[str] = None) -> str:
        """Creates a mock .cbz archive in staging directory."""
        cbz_path = os.path.join(self.staging_dir, filename)
        with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in range(1, pages + 1):
                z.writestr(f"{p:03d}.jpg", f"fake-jpeg-data-for-page-{p}".encode("utf-8"))
            if existing_xml:
                z.writestr("ComicInfo.xml", existing_xml.encode("utf-8"))
        return cbz_path


def generate_rss_xml(items: List[Dict[str, Any]]) -> str:
    """Generates standard RSS 2.0 XML string."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '  <channel>',
        '    <title>Mock Manga RSS Feed</title>',
        '    <link>https://example.com/feed</link>',
        '    <description>Mock feed for testing</description>'
    ]
    for it in items:
        lines.append('    <item>')
        lines.append(f'      <title>{it.get("title", "")}</title>')
        lines.append(f'      <link>{it.get("link", "https://example.com")}</link>')
        lines.append(f'      <guid>{it.get("guid", it.get("link", "guid-1"))}</guid>')
        if "pubDate" in it:
            lines.append(f'      <pubDate>{it["pubDate"]}</pubDate>')
        if "enclosure" in it:
            lines.append(f'      <enclosure url="{it["enclosure"]}" type="application/x-bittorrent" />')
        lines.append('    </item>')
    lines.append('  </channel>')
    lines.append('</rss>')
    return "\n".join(lines)


def generate_nyaa_rss(items: List[Dict[str, Any]]) -> str:
    """Generates Nyaa-compatible XML feed string."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:nyaa="https://nyaa.si/xmlns/nyaa">',
        '  <channel>',
        '    <title>Nyaa - Manga</title>',
        '    <link>https://nyaa.si/</link>'
    ]
def generate_nyaa_html(items: List[Dict[str, Any]]) -> str:
    """Generates Nyaa-compatible HTML page with torrent-list table."""
    rows = []
    for it in items:
        view_url = it.get("view_url", "/view/1000")
        title = it.get("title", "Sample Manga Vol 1")
        torrent_url = it.get("torrent_url", "/download/1000.torrent")
        magnet_url = it.get("magnet", "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567")
        size = it.get("size", "150.0 MiB")
        timestamp = it.get("timestamp", "1700000000")
        seeders = it.get("seeders", "25")
        rows.append(f"""
        <tr>
            <td>Literature</td>
            <td><a href="{view_url}">{title}</a></td>
            <td><a href="{torrent_url}">DL</a> <a href="{magnet_url}">Magnet</a></td>
            <td>{size}</td>
            <td data-timestamp="{timestamp}">2026-11-01 00:00</td>
            <td>{seeders}</td>
            <td>5</td>
            <td>100</td>
        </tr>
        """)
    return f"""
    <html>
      <body>
        <table class="torrent-list">
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </body>
    </html>
    """

