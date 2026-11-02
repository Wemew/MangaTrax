import os
import json
import math
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.database import MangaDatabase
from app.calibre.scanner import CalibreMangaScanner
from app.providers.registry import create_provider
from app.scheduler import MangaScheduler
from app.covers import CoverCache
from app.downloader.qbit import QBitClient
from app.tagger.cbz_tagger import has_comicinfo
from app.metadata.mangaupdates import MangaUpdatesService

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mangatrax")

# Directories & Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "..", "data"))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "mangatrax.db")
CALIBRE_DIR = os.getenv("CALIBRE_DIR", "/calibre")

# Initialize Database, Cache & Scheduler
db = MangaDatabase(DB_PATH)
cover_cache = CoverCache(DATA_DIR)
mangaupdates = MangaUpdatesService(db=db)
scheduler = MangaScheduler(db, cover_cache)

# Lifespan Context Manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MangaTrax application...")
    # Trigger initial Calibre sync if available BEFORE starting background scheduler loop
    calibre_path = db.get_setting("calibre_path", CALIBRE_DIR)
    scanner = CalibreMangaScanner(calibre_path)
    if scanner.is_available():
        logger.info("Found Calibre library metadata.db on startup! Running initial manga scan...")
        scheduler.sync_calibre_library()
    else:
        logger.info(f"Calibre library metadata.db not yet found at {scanner.db_path}. Will re-check periodically.")
    scheduler.start()
    yield
    logger.info("Shutting down MangaTrax application...")
    scheduler.stop()

app = FastAPI(title="MangaTrax", lifespan=lifespan)

# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# Setup Templates
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

from app.timezone_util import format_localtime, format_localdate

def jinja_localtime(value: Optional[str], fmt: str = "%Y-%m-%d %H:%M") -> str:
    user_tz = db.get_setting("app_timezone", "")
    return format_localtime(value, fmt=fmt, custom_tz=user_tz)

def jinja_localdate(value: Optional[str]) -> str:
    user_tz = db.get_setting("app_timezone", "")
    return format_localdate(value, custom_tz=user_tz)

templates.env.filters["localtime"] = jinja_localtime
templates.env.filters["localdate"] = jinja_localdate

# --- HTML UI Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    stats = db.get_dashboard_stats()
    calibre_path = db.get_setting("calibre_path", CALIBRE_DIR)
    scanner = CalibreMangaScanner(calibre_path)
    calibre_status = scanner.get_library_summary()
    pending_requests = db.get_pending_requests(limit=10)
    recent_logs = db.get_logs(limit=10)

    # Get active qBit torrents with fast timeout to prevent UI stall
    qbit = scheduler.get_qbit_client()
    torrents = qbit.get_torrents(category="manga") if qbit.test_connection(timeout=1.5).get("success") else []

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "active_page": "dashboard",
            "stats": stats,
            "calibre_status": calibre_status,
            "pending_requests": pending_requests,
            "recent_logs": recent_logs,
            "torrents": torrents[:5]
        }
    )

@app.get("/series", response_class=HTMLResponse)
async def series_view(request: Request):
    total_count = db.count_all_series()
    return templates.TemplateResponse(
        request=request,
        name="series.html",
        context={
            "active_page": "series",
            "series_list": [],
            "total_series": total_count
        }
    )

@app.get("/releases", response_class=HTMLResponse)
async def releases_view(request: Request, status: Optional[str] = None, view: str = Query("series")):
    # Instant page response: page shell renders in <10ms and lazy loads series dynamically
    return templates.TemplateResponse(
        request=request,
        name="releases.html",
        context={
            "active_page": "releases",
            "releases": [],
            "grouped_series": [],
            "current_status": status,
            "current_view": view
        }
    )

@app.get("/prioritized", response_class=HTMLResponse)
async def prioritized_view(request: Request, status: Optional[str] = None, view: str = Query("series")):
    prioritized_groups = db.get_prioritized_groups()
    return templates.TemplateResponse(
        request=request,
        name="prioritized.html",
        context={
            "active_page": "prioritized",
            "prioritized_groups": prioritized_groups,
            "releases": [],
            "grouped_series": [],
            "current_status": status,
            "current_view": view
        }
    )

# --- Lazy Loading API Endpoints ---
@app.get("/api/releases/cards")
async def api_releases_cards(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(8, ge=1, le=50),
    search: Optional[str] = Query(None)
):
    clean_s = search.strip() if search else None
    groups = db.get_releases_grouped_by_series(status=status, limit=limit, offset=offset, search=clean_s)
    total = db.count_releases_grouped_by_series(status=status, search=clean_s)
    has_more = (offset + len(groups)) < total
    html = templates.get_template("partials/series_card_chunk.html").render(
        {"request": request, "grouped_series": groups, "is_prioritized_view": False}
    )
    return JSONResponse({
        "status": "success",
        "offset": offset,
        "count": len(groups),
        "total": total,
        "has_more": has_more,
        "html": html
    })

@app.get("/api/prioritized/cards")
async def api_prioritized_cards(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(8, ge=1, le=50),
    search: Optional[str] = Query(None)
):
    clean_s = search.strip() if search else None
    groups = db.get_releases_grouped_by_series(status=status, prioritized_only=True, limit=limit, offset=offset, search=clean_s)
    total = db.count_releases_grouped_by_series(status=status, prioritized_only=True, search=clean_s)
    has_more = (offset + len(groups)) < total
    html = templates.get_template("partials/series_card_chunk.html").render(
        {"request": request, "grouped_series": groups, "is_prioritized_view": True}
    )
    return JSONResponse({
        "status": "success",
        "offset": offset,
        "count": len(groups),
        "total": total,
        "has_more": has_more,
        "html": html
    })

@app.get("/api/releases/flat_rows")
async def api_releases_flat_rows(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200)
):
    releases = db.get_releases(status=status, limit=limit, offset=offset)
    seen_sigs = set()
    deduped = []
    for r in releases:
        sig = r.get("guid") or (r.get("download_url") or r.get("link") or r.get("title"), tuple(sorted(r.get("volumes", []))), (r.get("release_group") or "").lower())
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped.append(r)
    has_more = len(releases) == limit
    html = templates.get_template("partials/flat_release_row_chunk.html").render(
        {"request": request, "releases": deduped}
    )
    return JSONResponse({
        "status": "success",
        "offset": offset,
        "count": len(deduped),
        "has_more": has_more,
        "html": html
    })

@app.get("/api/prioritized/flat_rows")
async def api_prioritized_flat_rows(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200)
):
    releases = db.get_prioritized_releases(status=status, limit=limit, offset=offset)
    has_more = len(releases) == limit
    html = templates.get_template("partials/flat_release_row_chunk.html").render(
        {"request": request, "releases": releases}
    )
    return JSONResponse({
        "status": "success",
        "offset": offset,
        "count": len(releases),
        "has_more": has_more,
        "html": html
    })

@app.get("/api/series/rows")
async def api_series_rows(
    request: Request,
    monitored_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None)
):
    clean_s = search.strip() if search else None
    series_list = db.get_all_series(monitored_only=monitored_only, limit=limit, offset=offset, search=clean_s)
    total = db.count_all_series(monitored_only=monitored_only, search=clean_s)
    has_more = (offset + len(series_list)) < total
    html = templates.get_template("partials/series_table_row_chunk.html").render(
        {"request": request, "series_list": series_list}
    )
    return JSONResponse({
        "status": "success",
        "offset": offset,
        "count": len(series_list),
        "total": total,
        "has_more": has_more,
        "html": html
    })

@app.get("/api/releases/grouped")
async def api_releases_grouped(status: Optional[str] = None):
    groups = db.get_releases_grouped_by_series(status=status)
    return JSONResponse({"status": "success", "count": len(groups), "series_groups": groups})

@app.get("/discovery", response_class=HTMLResponse)
async def discovery_view(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=6, le=120),
    q: Optional[str] = Query(None)
):
    clean_q = q.strip() if q else None
    total_count = db.count_discovery_releases(search=clean_q)
    total_pages = max(1, math.ceil(total_count / page_size))
    current_page = min(page, total_pages) if total_count > 0 else 1
    offset = (current_page - 1) * page_size
    discovered = db.get_discovery_releases(limit=page_size, offset=offset, search=clean_q)
    blacklisted = db.get_blacklisted_series()

    return templates.TemplateResponse(
        request=request,
        name="discovery.html",
        context={
            "active_page": "discovery",
            "discovered_releases": discovered,
            "blacklisted_series": blacklisted,
            "current_page": current_page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_count": total_count,
            "search_query": clean_q or ""
        }
    )

@app.get("/downloads", response_class=HTMLResponse)
async def downloads_view(request: Request):
    qbit = scheduler.get_qbit_client()
    qbit_status = qbit.test_connection(timeout=2.0)
    torrents = qbit.get_torrents(category="manga") if qbit_status.get("success") else []

    # Check files in staging directory
    staging_dir = db.get_setting("staging_dir", "/staging")
    staging_files = []
    if os.path.isdir(staging_dir):
        for root, _, files in os.walk(staging_dir):
            for f in sorted(files):
                if f.lower().endswith(".cbz"):
                    full_p = os.path.join(root, f)
                    staging_files.append({
                        "name": f,
                        "size_mb": round(os.path.getsize(full_p) / (1024 * 1024), 2),
                        "has_comicinfo": has_comicinfo(full_p)
                    })

    return templates.TemplateResponse(
        request=request,
        name="downloads.html",
        context={
            "active_page": "downloads",
            "qbit_status": qbit_status,
            "torrents": torrents,
            "staging_dir": staging_dir,
            "staging_files": staging_files,
            "settings": db.get_all_settings()
        }
    )

@app.get("/feeds", response_class=HTMLResponse)
async def feeds_view(request: Request):
    feeds = db.get_all_feeds()
    return templates.TemplateResponse(
        request=request,
        name="feeds.html",
        context={
            "active_page": "feeds",
            "feeds": feeds
        }
    )

@app.get("/logs", response_class=HTMLResponse)
async def logs_view(request: Request):
    logs = db.get_logs(limit=150)
    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={
            "active_page": "logs",
            "logs": logs
        }
    )

@app.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    settings = db.get_all_settings()
    prioritized_groups = db.get_prioritized_groups()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "settings": settings,
            "prioritized_groups": prioritized_groups
        }
    )

# --- API Endpoints ---

@app.post("/api/calibre/sync")
async def api_calibre_sync():
    """Trigger manual Calibre library rescan and bundle request resolution."""
    res = scheduler.sync_calibre_library()
    return JSONResponse(res)

@app.get("/api/calibre/test")
async def api_calibre_test(path: str = Query(...)):
    """Test connection to Calibre library at given path."""
    scanner = CalibreMangaScanner(path)
    summary = scanner.get_library_summary()
    return JSONResponse(summary)

FALLBACK_COVER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="150" viewBox="0 0 100 150"><rect width="100" height="150" rx="8" fill="#1e293b" stroke="#334155" stroke-width="2"/><path d="M35 55h30M35 70h30M35 85h20" stroke="#64748b" stroke-width="2" stroke-linecap="round"/><text x="50" y="115" font-family="system-ui,-apple-system,sans-serif" font-size="10" font-weight="600" fill="#f43f5e" text-anchor="middle">MANGA</text></svg>"""

@app.get("/api/covers")
async def api_get_cover(url: str = Query(...)):
    """Serves locally cached cover image, downloading on-demand if not already on disk."""
    if not url:
        return Response(content=FALLBACK_COVER_SVG, media_type="image/svg+xml", status_code=200)

    filepath = await cover_cache.get_or_download(url)
    if filepath and os.path.isfile(filepath):
        media_type = "image/jpeg"
        if filepath.endswith(".png"):
            media_type = "image/png"
        elif filepath.endswith(".webp"):
            media_type = "image/webp"

        return FileResponse(
            filepath,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=864000"}
        )

    return Response(content=FALLBACK_COVER_SVG, media_type="image/svg+xml", status_code=200)

@app.post("/api/feeds/check")
async def api_feeds_check():
    """Trigger check for all enabled feeds."""
    res = scheduler.check_all_feeds()
    return JSONResponse(res)

@app.post("/api/feeds/test")
async def api_feeds_test(payload: Dict[str, Any]):
    """Live test a feed or scraper extraction without saving to database."""
    url = payload.get("url", "")
    feed_type = payload.get("feed_type", "rss")
    config = payload.get("config", {})

    if not url:
        return JSONResponse({"status": "error", "message": "URL is required"}, status_code=400)

    try:
        provider = create_provider({"name": "Test Feed", "feed_type": feed_type, "url": url, "config_json": json.dumps(config)})
        if not provider:
            return JSONResponse({"status": "error", "message": "Invalid provider type"}, status_code=400)
        releases = provider.fetch_releases()
        items = [r.to_dict() for r in releases]
        return JSONResponse({"status": "success", "count": len(items), "items": items[:25]})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/feeds")
async def api_add_feed(payload: Dict[str, Any]):
    """Add a new feed or web scraper."""
    name = payload.get("name")
    feed_type = payload.get("feed_type")
    url = payload.get("url")
    interval = payload.get("interval", 60)
    auto_download = payload.get("auto_download", True)
    config = payload.get("config", {})

    if not name or not url or not feed_type:
        return JSONResponse({"status": "error", "message": "Missing required fields"}, status_code=400)

    feed_id = db.add_feed(
        name=name,
        feed_type=feed_type,
        url=url,
        config_json=json.dumps(config),
        auto_download=auto_download,
        interval=interval
    )
    db.log_activity("FEED_ADDED", f"Added manga feed: {name}", f"Type: {feed_type}, URL: {url}")
    return JSONResponse({"status": "success", "feed_id": feed_id})

@app.delete("/api/feeds/{feed_id}")
async def api_delete_feed(feed_id: int):
    """Delete a feed."""
    db.delete_feed(feed_id)
    return JSONResponse({"status": "success"})

@app.post("/api/staging/tag")
async def api_staging_tag():
    """Trigger ComicInfo.xml tagging on .cbz files in staging folder using inherited series metadata."""
    res = scheduler.tag_staging_files()
    return JSONResponse(res)

@app.post("/api/downloader/test")
async def api_downloader_test(payload: Optional[Dict[str, Any]] = None):
    """Test connection to qBittorrent, either with saved settings or transient test settings."""
    p = payload or {}
    url = p.get("qbit_url")
    username = p.get("qbit_username")
    password = p.get("qbit_password")

    if url is not None or username is not None:
        client = QBitClient(
            base_url=url or "http://localhost:8080",
            username=username or "",
            password=password or "",
            enabled=True
        )
    else:
        client = scheduler.get_qbit_client()

    res = client.test_connection()
    return JSONResponse(res)

@app.post("/api/downloader/configure")
async def api_downloader_configure(payload: Dict[str, Any]):
    """Save in-app BitTorrent downloader configuration and test connection."""
    qbit_url = (payload.get("qbit_url") or "").strip()
    qbit_user = (payload.get("qbit_username") or "").strip()
    qbit_pass = payload.get("qbit_password")
    qbit_enabled = str(payload.get("qbit_enabled", "true")).lower()
    qbit_category = (payload.get("qbit_category") or "manga").strip() or "manga"
    qbit_tags = (payload.get("qbit_tags") or "mangatrax").strip() or "mangatrax"

    if qbit_url:
        db.set_setting("qbit_url", qbit_url)
    db.set_setting("qbit_username", qbit_user)
    if qbit_pass is not None:
        db.set_setting("qbit_password", qbit_pass)
    db.set_setting("qbit_enabled", qbit_enabled)
    db.set_setting("qbit_category", qbit_category)
    db.set_setting("qbit_tags", qbit_tags)

    db.log_activity("DOWNLOADER_CONFIGURED", f"BitTorrent downloader configured in-app: {qbit_url} (Enabled: {qbit_enabled})")

    client = scheduler.get_qbit_client()
    test_res = client.test_connection()
    return JSONResponse({
        "status": "success",
        "message": "Downloader settings saved successfully",
        "connection": test_res
    })

@app.get("/api/downloader/queue")
async def api_downloader_queue():
    """Get active torrent queue."""
    qbit = scheduler.get_qbit_client()
    torrents = qbit.get_torrents(category="manga")
    return JSONResponse({"status": "success", "torrents": torrents})

@app.post("/api/downloader/pause/{torrent_hash}")
async def api_downloader_pause(torrent_hash: str):
    qbit = scheduler.get_qbit_client()
    success = qbit.pause_torrent(torrent_hash)
    return JSONResponse({"status": "success" if success else "error"})

@app.post("/api/downloader/resume/{torrent_hash}")
async def api_downloader_resume(torrent_hash: str):
    qbit = scheduler.get_qbit_client()
    success = qbit.resume_torrent(torrent_hash)
    return JSONResponse({"status": "success" if success else "error"})

@app.delete("/api/downloader/delete/{torrent_hash}")
async def api_downloader_delete(torrent_hash: str):
    qbit = scheduler.get_qbit_client()
    success = qbit.delete_torrent(torrent_hash, delete_files=False)
    return JSONResponse({"status": "success" if success else "error"})

@app.post("/api/releases/{release_id}/download")
async def api_release_download(release_id: int):
    """Manually send release to qBittorrent downloader."""
    target = db.get_release_by_id(release_id)
    if not target:
        raise HTTPException(status_code=404, detail="Release not found")

    download_url = target.get("download_url") or target.get("link")
    staging_dir = db.get_setting("staging_dir", "/staging")
    qbit = scheduler.get_qbit_client()

    success = qbit.add_torrent(
        url_or_magnet=download_url,
        savepath=staging_dir,
        category="manga",
        tags="mangatrax"
    )

    if success:
        torrent_h = qbit.extract_torrent_hash(download_url)
        db.update_download_status(release_id, "DOWNLOADING", torrent_h)
        db.log_activity("MANUAL_DOWNLOAD", f"Sent to downloader: '{target['title']}'", f"URL: {download_url[:60]}...")

        missing = target.get("missing_volumes") or json.loads(target.get("missing_vols_json") or "[]")
        vols = target.get("volumes") or []
        if torrent_h and target.get("is_bundle") and missing and len(missing) < len(vols):
            sel_res = qbit.apply_selective_volume_download(torrent_h, [float(v) for v in missing])
            if sel_res.get("status") == "filtered":
                db.log_activity(
                    "SELECTIVE_DOWNLOAD",
                    f"Selective download applied for '{target['title']}': skipped {len(sel_res.get('skipped_volumes', []))} owned volumes; downloading {len(sel_res.get('kept_volumes', []))} missing volumes."
                )

        return JSONResponse({"status": "success", "message": "Sent to downloader successfully"})
    else:
        return JSONResponse({"status": "error", "message": "Failed to add release to downloader"}, status_code=500)

@app.post("/api/series/{series_id}/toggle")
async def api_toggle_series(series_id: int):
    """Toggle monitored status for a series."""
    is_monitored = db.toggle_series_monitored(series_id)
    return JSONResponse({"status": "success", "is_monitored": is_monitored})

@app.post("/api/series/{series_id}/link")
async def api_update_series_link(series_id: int, payload: Dict[str, Any]):
    """Update or save custom website link for a series."""
    website_url = payload.get("website_url", "")
    db.update_series_link(series_id, website_url)
    return JSONResponse({"status": "success", "website_url": website_url})

@app.post("/api/series/{series_id}/backfill")
async def api_series_backfill(series_id: int, payload: Optional[Dict[str, Any]] = None):
    """Executes a targeted Nyaa search backfill in background for a tracked series."""
    series_list = db.get_all_series()
    target = next((s for s in series_list if s["id"] == series_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Series not found")

    p = payload or {}
    max_pages = int(p.get("max_pages", 3))
    res = scheduler.run_series_backfill_async(target["name"], max_pages=max_pages)
    return JSONResponse(res)

@app.post("/api/series/backfill")
async def api_series_backfill_by_name(payload: Dict[str, Any]):
    """Executes a targeted Nyaa search backfill in background by series name."""
    series_name = payload.get("series_name")
    if not series_name:
        return JSONResponse({"status": "error", "message": "Series name required"}, status_code=400)
    max_pages = int(payload.get("max_pages", 3))
    res = scheduler.run_series_backfill_async(series_name, max_pages=max_pages)
    return JSONResponse(res)

@app.post("/api/releases/{release_id}/status")
async def api_update_release_status(release_id: int, payload: Dict[str, Any]):
    """Update release status (e.g. IN_CALIBRE, REQUESTED, or IGNORED)."""
    status = payload.get("status")
    if not status:
        return JSONResponse({"status": "error", "message": "Status required"}, status_code=400)

    db.update_release_status(release_id, status)
    return JSONResponse({"status": "success"})

@app.post("/api/releases/{release_id}/ignore")
async def api_ignore_release(release_id: int):
    """Marks a release as IGNORED."""
    db.update_release_status(release_id, "IGNORED")
    db.log_activity("RELEASE_IGNORED", f"Ignored release #{release_id}")
    return JSONResponse({"status": "success", "message": "Release marked as ignored"})

@app.post("/api/releases/{release_id}/unignore")
async def api_unignore_release(release_id: int):
    """Restores an IGNORED release to REQUESTED."""
    db.update_release_status(release_id, "REQUESTED")
    db.log_activity("RELEASE_RESTORED", f"Restored release #{release_id} from ignored")
    return JSONResponse({"status": "success", "message": "Release restored from ignored"})

@app.post("/api/blacklist")
async def api_add_blacklist(payload: Dict[str, Any]):
    """Add a series to the blacklist and purge pending requests for it."""
    name = payload.get("name")
    reason = payload.get("reason", "")
    if not name:
        return JSONResponse({"status": "error", "message": "Series name required"}, status_code=400)

    success = db.add_to_blacklist(name, reason)
    db.log_activity("SERIES_BLACKLISTED", f"Blacklisted series: {name}", reason)
    return JSONResponse({"status": "success"})

@app.delete("/api/blacklist/{blacklist_id}")
async def api_delete_blacklist(blacklist_id: int):
    """Remove a series from the blacklist."""
    db.remove_from_blacklist(blacklist_id)
    db.log_activity("BLACKLIST_REMOVED", f"Removed series #{blacklist_id} from blacklist")
    return JSONResponse({"status": "success"})

# --- Prioritized Release Groups Endpoints ---

@app.get("/api/prioritized/groups")
async def api_get_prioritized_groups():
    groups = db.get_prioritized_groups()
    return JSONResponse({"status": "success", "groups": groups})

@app.post("/api/prioritized/groups")
async def api_add_prioritized_group(payload: Dict[str, Any]):
    name = payload.get("name")
    notes = payload.get("notes", "")
    priority = payload.get("priority", 1)
    if not name:
        return JSONResponse({"status": "error", "message": "Group name required"}, status_code=400)

    success = db.add_prioritized_group(name, notes, priority)
    db.log_activity("GROUP_PRIORITIZED", f"Added prioritized release group: {name}", notes)
    return JSONResponse({"status": "success"})

@app.delete("/api/prioritized/groups/{group_id}")
async def api_delete_prioritized_group(group_id: int):
    db.remove_prioritized_group(group_id)
    db.log_activity("GROUP_UNPRIORITIZED", f"Removed prioritized group #{group_id}")
    return JSONResponse({"status": "success"})

@app.get("/api/prioritized/releases")
async def api_get_prioritized_releases(status: Optional[str] = None):
    releases = db.get_prioritized_releases(status=status)
    return JSONResponse({"status": "success", "count": len(releases), "releases": releases})

@app.post("/api/discovery/track")
async def api_discovery_track(payload: Dict[str, Any]):
    """Track a newly discovered series and request the volume, using canonical English series name."""
    series_name = payload.get("series_name")
    release_id = payload.get("release_id")
    if not series_name:
        return JSONResponse({"status": "error", "message": "Series name required"}, status_code=400)

    # Prefer canonical English title
    english_name = scheduler.anilist.resolve_english_title(series_name)
    target_name = english_name if english_name else series_name

    db.track_discovered_series(target_name, release_id)
    db.log_activity("SERIES_TRACKED", f"Started tracking new series: {target_name}")
    if release_id:
        try:
            rel_item = db.get_release_by_id(release_id)
            if rel_item:
                scheduler.get_notifier().notify_new_request(rel_item)
        except Exception as e:
            logger.debug(f"Webhook notification failed on track: {e}")
    return JSONResponse({"status": "success", "tracked_name": target_name})

@app.post("/api/discovery/blacklist")
async def api_discovery_blacklist(payload: Dict[str, Any]):
    """Blacklist a series directly from a discovery card."""
    series_name = payload.get("series_name")
    reason = payload.get("reason", "Blacklisted from Discovery panel")
    if not series_name:
        return JSONResponse({"status": "error", "message": "Series name required"}, status_code=400)

    db.add_to_blacklist(series_name, reason)
    db.log_activity("SERIES_BLACKLISTED", f"Blacklisted series from discovery: {series_name}")
    return JSONResponse({"status": "success"})

@app.post("/api/discovery/{release_id}/fetch-info")
async def api_discovery_fetch_info(release_id: int):
    """Fetches series synopsis and MangaUpdates link for a discovery release on demand."""
    import asyncio
    target = db.get_release_by_id(release_id)
    if not target:
        raise HTTPException(status_code=404, detail="Discovery release not found")

    series_name = target.get("series_name") or target.get("title")
    info = await asyncio.to_thread(mangaupdates.get_series_info, series_name)
    desc = info.get("description", "")
    mu_url = info.get("url", "")
    cover_url = info.get("cover_url", "")

    db.update_release_details(
        release_id=release_id,
        description=desc,
        mangaupdates_url=mu_url,
        cover_url=cover_url if cover_url else None
    )
    if cover_url:
        await asyncio.to_thread(cover_cache.cache_sync, cover_url)

    return JSONResponse({
        "status": "success",
        "description": desc,
        "mangaupdates_url": mu_url,
        "cover_url": cover_url
    })

@app.post("/api/settings")
async def api_save_settings(payload: Dict[str, Any]):
    """Save application settings."""
    for k, v in payload.items():
        db.set_setting(k, str(v))
    db.log_activity("SETTINGS_UPDATED", "Application settings updated")
    return JSONResponse({"status": "success"})

@app.post("/api/settings/test-webhook")
async def api_test_webhook(payload: Optional[Dict[str, Any]] = None):
    """Test Discord and/or generic webhook endpoints with immediate feedback."""
    p = payload or {}
    discord_url = p.get("discord_webhook_url")
    generic_url = p.get("generic_webhook_url") or p.get("apprise_url")
    notifier = scheduler.get_notifier()
    res = notifier.test_webhook(discord_url=discord_url, apprise_url=generic_url)
    return JSONResponse(res)

# --- Polite Historical Backfill Endpoints ---

@app.post("/api/backfill/start")
async def api_backfill_start(payload: Optional[Dict[str, Any]] = None):
    """Starts polite historical backfill across feeds."""
    import asyncio
    if scheduler.is_backfilling:
        return JSONResponse({"status": "in_progress", "message": "Backfill already running"})

    p = payload or {}
    max_pages = p.get("max_pages")
    if max_pages is not None:
        max_pages = int(max_pages)
    delay = p.get("delay")
    if delay is not None:
        delay = float(delay)
    start_page = p.get("start_page")
    if start_page is not None:
        try:
            start_page = max(1, int(start_page))
        except (ValueError, TypeError):
            start_page = 1
    else:
        start_page = 1

    scheduler.run_polite_backfill(
        max_pages=max_pages,
        delay_seconds=delay,
        start_page=start_page
    )
    return JSONResponse({"status": "started", "message": f"Polite backfill started from page {start_page} in background"})

@app.post("/api/backfill/stop")
async def api_backfill_stop():
    """Signals the running backfiller to stop gracefully."""
    scheduler.stop_backfill()
    return JSONResponse({"status": "stopping", "message": "Backfill stopping"})

@app.get("/api/backfill/status")
async def api_backfill_status():
    """Returns current backfill progress and state."""
    return JSONResponse(scheduler.get_backfill_status())

@app.post("/api/debug/action")
async def api_debug_action(payload: Dict[str, Any]):
    """Debug maintenance operations: clear discovery, descriptions, or cover cache."""
    action = payload.get("action")
    if action == "clear-discovery":
        count = db.clear_discovered_series()
        return JSONResponse({"status": "success", "message": f"Cleared {count} unreviewed manga series from Discovery."})
    elif action == "clear-descriptions":
        count = db.clear_all_descriptions()
        return JSONResponse({"status": "success", "message": f"Cleared descriptions across {count} items."})
    elif action == "clear-thumbnails":
        count = cover_cache.clear_cache()
        return JSONResponse({"status": "success", "message": f"Purged {count} cached cover images from disk."})
    elif action == "normalize-english-titles":
        count = db.normalize_series_titles_to_english(scheduler.anilist)
        return JSONResponse({"status": "success", "message": f"Normalized {count} manga releases to official English titles."})
    elif action == "reset-all-data":
        db.reset_all_data()
        cover_cache.clear_cache()
        try:
            scheduler.sync_calibre_library()
        except Exception:
            pass
        return JSONResponse({"status": "success", "message": "All application data, release history, and caches have been reset successfully."})
    else:
        return JSONResponse({"status": "error", "message": f"Unknown action: {action}"}, status_code=400)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
