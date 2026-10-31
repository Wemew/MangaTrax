import os
import json
import logging
from typing import Dict, Any, Optional
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
scheduler = MangaScheduler(db, cover_cache)

# Lifespan Context Manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MangaTrax application...")
    scheduler.start()
    # Trigger initial Calibre sync if available
    calibre_path = db.get_setting("calibre_path", CALIBRE_DIR)
    scanner = CalibreMangaScanner(calibre_path)
    if scanner.is_available():
        logger.info("Found Calibre library metadata.db on startup! Running initial manga scan...")
        scheduler.sync_calibre_library()
    else:
        logger.info(f"Calibre library metadata.db not yet found at {scanner.db_path}. Will re-check periodically.")
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

# --- HTML UI Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    stats = db.get_dashboard_stats()
    calibre_path = db.get_setting("calibre_path", CALIBRE_DIR)
    scanner = CalibreMangaScanner(calibre_path)
    calibre_status = scanner.get_library_summary()
    pending_requests = db.get_pending_requests()
    recent_logs = db.get_logs(limit=10)

    # Get active qBit torrents
    qbit = scheduler.get_qbit_client()
    torrents = qbit.get_torrents(category="manga") if qbit.test_connection().get("success") else []

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
    series_list = db.get_all_series()
    return templates.TemplateResponse(
        request=request,
        name="series.html",
        context={
            "active_page": "series",
            "series_list": series_list
        }
    )

@app.get("/releases", response_class=HTMLResponse)
async def releases_view(request: Request, status: Optional[str] = None):
    releases = db.get_releases(status=status, limit=500)
    return templates.TemplateResponse(
        request=request,
        name="releases.html",
        context={
            "active_page": "releases",
            "releases": releases,
            "current_status": status
        }
    )

@app.get("/discovery", response_class=HTMLResponse)
async def discovery_view(request: Request):
    discovered = db.get_discovery_releases(limit=100)
    blacklisted = db.get_blacklisted_series()
    return templates.TemplateResponse(
        request=request,
        name="discovery.html",
        context={
            "active_page": "discovery",
            "discovered_releases": discovered,
            "blacklisted_series": blacklisted
        }
    )

@app.get("/downloads", response_class=HTMLResponse)
async def downloads_view(request: Request):
    qbit = scheduler.get_qbit_client()
    qbit_status = qbit.test_connection()
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
            "staging_files": staging_files
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
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "settings": settings
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
async def api_downloader_test():
    """Test connection to qBittorrent."""
    qbit = scheduler.get_qbit_client()
    res = qbit.test_connection()
    return JSONResponse(res)

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
    releases = db.get_releases(limit=1000)
    target = next((r for r in releases if r["id"] == release_id), None)
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
        db.update_download_status(release_id, "DOWNLOADING")
        db.log_activity("MANUAL_DOWNLOAD", f"Sent to downloader: '{target['title']}'", f"URL: {download_url[:60]}...")
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

@app.post("/api/releases/{release_id}/status")
async def api_update_release_status(release_id: int, payload: Dict[str, Any]):
    """Update release status (e.g. IN_CALIBRE, REQUESTED, or IGNORED)."""
    status = payload.get("status")
    if not status:
        return JSONResponse({"status": "error", "message": "Status required"}, status_code=400)

    db.update_release_status(release_id, status)
    return JSONResponse({"status": "success"})

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

@app.post("/api/discovery/track")
async def api_discovery_track(payload: Dict[str, Any]):
    """Track a newly discovered series and request the volume."""
    series_name = payload.get("series_name")
    release_id = payload.get("release_id")
    if not series_name:
        return JSONResponse({"status": "error", "message": "Series name required"}, status_code=400)

    db.track_discovered_series(series_name, release_id)
    db.log_activity("SERIES_TRACKED", f"Started tracking new series: {series_name}")
    return JSONResponse({"status": "success"})

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

@app.post("/api/settings")
async def api_save_settings(payload: Dict[str, Any]):
    """Save application settings."""
    for k, v in payload.items():
        db.set_setting(k, str(v))
    db.log_activity("SETTINGS_UPDATED", "Application settings updated")
    return JSONResponse({"status": "success"})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
