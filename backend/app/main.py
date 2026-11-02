import os
import json
import math
import asyncio
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
from app import auth as auth_module
from app import schemas
from app import security

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

# --- Authentication (MANGATRAX_AUTH_MODE = none | basic | oidc; default none) ---
# Misconfiguration in an enabled mode must never silently fall back to an open
# server: validate() raises, we log an actionable message and exit.
AUTH_CONFIG = auth_module.AuthConfig.from_env()
try:
    AUTH_CONFIG.validate()
except auth_module.AuthConfigError as exc:
    logger.critical("Authentication configuration error: %s", exc)
    logger.critical("Refusing to start with an unusable authentication configuration.")
    raise SystemExit(1)
logger.info("Authentication configuration: %s", AUTH_CONFIG.describe())
AUTH = auth_module.AuthManager(AUTH_CONFIG, user_store=db)

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

# --- Authentication middleware ---
# Registered BEFORE the security-headers middleware so that challenge/redirect
# responses produced here still pass through it and receive the security headers.
# `MANGATRAX_AUTH_MODE=none` (the default) lets every request through and stamps
# the audit actor as "anonymous"; background worker events use actor="scheduler".
@app.middleware("http")
async def enforce_authentication(request: Request, call_next):
    try:
        decision = AUTH.check_request(request.method, request.url.path, request.headers, request.cookies)
    except Exception as exc:  # pragma: no cover - defensive: never fail open
        logger.error("Authentication middleware error: %s", exc)
        return JSONResponse({"detail": "Authentication error"}, status_code=500)

    actor_token = auth_module.set_current_actor(decision.actor)
    try:
        if decision.action == "redirect_login":
            return RedirectResponse(decision.location or "/auth/login", status_code=302)
        if decision.action == "unauthorized":
            response_headers = {}
            if decision.www_authenticate:
                response_headers["WWW-Authenticate"] = decision.www_authenticate
            return JSONResponse(
                {"detail": decision.detail or "Authentication required"},
                status_code=401,
                headers=response_headers,
            )
        if decision.action == "forbidden":
            return JSONResponse(
                {"detail": decision.detail or "Request rejected"},
                status_code=403,
            )
        return await call_next(request)
    finally:
        auth_module.reset_current_actor(actor_token)

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
# Stored-XSS defense: only http(s) URLs may be rendered as an href.
templates.env.filters["safe_url"] = security.safe_url_filter


def _register_template_global(name: str, value) -> None:
    """Expose a Jinja global when the template engine supports globals.

    The E2E harness replaces Jinja2Templates with a minimal in-memory shim, so
    this is deliberately tolerant of a mock environment object.
    """
    try:
        templates.env.globals[name] = value
    except Exception:  # pragma: no cover - mock template environment
        pass


_register_template_global("auth_mode", AUTH_CONFIG.mode)
_register_template_global("auth_actor", auth_module.get_current_actor)
_register_template_global("debug_actions_enabled", AUTH_CONFIG.debug_actions)


def _validated_payload(model_cls, payload, *, status_code: int = 400):
    """Validate a JSON payload against a Pydantic model, mapping errors to HTTP 400."""
    try:
        return schemas.validate(model_cls, payload)
    except schemas.ValidationError as exc:
        raise HTTPException(status_code=status_code, detail=schemas.validation_error_messages(exc)) from exc


def _operator_allowed_path_roots():
    """Optional deployment-level path allow-list (``MANGATRAX_PATH_ROOTS``).

    ``os.pathsep``-separated list of directories that ``calibre_path`` /
    ``staging_dir`` settings may point into.  Unset means the traversal, NUL,
    relative-path and symlink checks still apply, but new mount points can be
    configured freely (existing deployments keep working).
    """
    raw = os.getenv("MANGATRAX_PATH_ROOTS", "") or ""
    return [part.strip() for part in raw.split(os.pathsep) if part.strip()]


# --- Authentication & health endpoints (exempt from the auth gate) ---

@app.get("/health")
async def health_check():
    """Liveness probe (exempt from auth so Docker/Traefik health checks work)."""
    return JSONResponse(AUTH.health_payload())


@app.get("/auth/login")
async def auth_login(next: str = Query("/")):
    """Start the OIDC authorization-code + PKCE flow (redirect to PocketID)."""
    if AUTH_CONFIG.mode != "oidc":
        # Nothing to log in to: basic shows the browser challenge from the
        # middleware, none has no authentication at all.
        return RedirectResponse("/", status_code=302)
    try:
        start = AUTH.start_login(next)
    except auth_module.AuthError as exc:
        logger.error("Could not start OIDC login: %s", exc)
        if AUTH_CONFIG.break_glass_enabled:
            # PocketID is unreachable: fall back to the Basic challenge so the
            # operator can still get in with the break-glass credentials.
            return JSONResponse(
                {"detail": "OIDC provider unavailable; use break-glass Basic credentials"},
                status_code=401,
                headers={"WWW-Authenticate": auth_module.BASIC_CHALLENGE},
            )
        return JSONResponse({"detail": "OIDC provider unavailable"}, status_code=503)
    response = RedirectResponse(start.authorization_url, status_code=302)
    response.set_cookie(
        key=start.state_cookie_name,
        value=start.state_cookie,
        max_age=auth_module.STATE_TTL_SECONDS,
        httponly=True,
        secure=AUTH_CONFIG.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Complete the OIDC flow: verify state+nonce, exchange code, set session."""
    if AUTH_CONFIG.mode != "oidc":
        return RedirectResponse("/", status_code=302)
    if error:
        logger.warning("OIDC provider returned an error: %s", error)
        return RedirectResponse("/auth/login?error=oidc_denied", status_code=302)
    state_cookie = request.cookies.get(auth_module.STATE_COOKIE_NAME, "")
    try:
        result = AUTH.complete_login(code, state, state_cookie)
    except auth_module.AuthError as exc:
        # Generic message to the browser; the reason is only logged server-side.
        logger.warning("OIDC callback rejected: %s", exc)
        return RedirectResponse("/auth/login?error=oidc_failed", status_code=302)
    response = RedirectResponse(result.redirect_url or "/", status_code=302)
    response.set_cookie(
        key=auth_module.SESSION_COOKIE_NAME,
        value=result.session_cookie,
        max_age=AUTH_CONFIG.session_max_age,
        httponly=True,
        secure=AUTH_CONFIG.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(auth_module.STATE_COOKIE_NAME, path="/")
    return response


@app.get("/auth/logout")
async def auth_logout():
    """Clear the local session and, when advertised, log out at the IdP too."""
    result = AUTH.logout()
    response = RedirectResponse(result.redirect_url or "/", status_code=302)
    response.delete_cookie(auth_module.SESSION_COOKIE_NAME, path="/")
    return response

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
            "prioritized_groups": prioritized_groups,
            "debug_actions_enabled": AUTH_CONFIG.debug_actions
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
    """Test connection to Calibre library at given path.

    M3 feature 21: the probe path is contained to the configured library roots
    (blocking path traversal, symlink escapes and arbitrary filesystem probing).
    """
    configured_calibre = db.get_setting("calibre_path", CALIBRE_DIR)
    configured_staging = db.get_setting("staging_dir", "")
    allowed_roots = [root for root in (configured_calibre, CALIBRE_DIR, configured_staging) if root]
    try:
        safe_path = security.ensure_path_within_roots(path, allowed_roots)
    except security.UnsafePathError as exc:
        raise HTTPException(status_code=403, detail=f"Rejected calibre path: {exc}") from exc
    scanner = CalibreMangaScanner(safe_path)
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
    model = _validated_payload(schemas.FeedTestRequest, payload)
    url = model.url
    feed_type = model.feed_type
    config = model.config

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
    model = _validated_payload(schemas.FeedCreateRequest, payload)
    feed_id = db.add_feed(
        name=model.name,
        feed_type=model.feed_type,
        url=model.url,
        config_json=json.dumps(model.config),
        auto_download=model.auto_download,
        interval=model.interval
    )
    db.log_activity("FEED_ADDED", f"Added manga feed: {model.name}", f"Type: {model.feed_type}, URL: {model.url}")
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
    model = _validated_payload(schemas.DownloaderTestRequest, payload or {})
    url = model.qbit_url
    username = model.qbit_username
    password = model.qbit_password

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
    model = _validated_payload(schemas.DownloaderConfigureRequest, payload)
    qbit_url = (model.qbit_url or "").strip()
    qbit_user = (model.qbit_username or "").strip()
    qbit_pass = model.qbit_password
    qbit_enabled = str(model.qbit_enabled if model.qbit_enabled is not None else "true").lower()
    qbit_category = (model.qbit_category or "manga").strip() or "manga"
    qbit_tags = (model.qbit_tags or "mangatrax").strip() or "mangatrax"

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
    """Update or save custom website link for a series (http/https only)."""
    model = _validated_payload(schemas.SeriesLinkUpdateRequest, payload)
    db.update_series_link(series_id, model.website_url)
    return JSONResponse({"status": "success", "website_url": model.website_url})

@app.post("/api/series/{series_id}/backfill")
async def api_series_backfill(series_id: int, payload: Optional[Dict[str, Any]] = None):
    """Executes a targeted Nyaa search backfill in background for a tracked series."""
    model = _validated_payload(schemas.SeriesBackfillRequest, payload or {})
    series_list = db.get_all_series()
    target = next((s for s in series_list if s["id"] == series_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Series not found")

    res = scheduler.run_series_backfill_async(target["name"], max_pages=model.max_pages)
    return JSONResponse(res)

@app.post("/api/series/backfill")
async def api_series_backfill_by_name(payload: Dict[str, Any]):
    """Executes a targeted Nyaa search backfill in background by series name."""
    model = _validated_payload(schemas.SeriesBackfillByNameRequest, payload)
    res = scheduler.run_series_backfill_async(model.series_name, max_pages=model.max_pages)
    return JSONResponse(res)

@app.post("/api/releases/{release_id}/status")
async def api_update_release_status(release_id: int, payload: Dict[str, Any]):
    """Update release status (e.g. IN_CALIBRE, REQUESTED, or IGNORED)."""
    model = _validated_payload(schemas.ReleaseStatusUpdateRequest, payload)
    db.update_release_status(release_id, model.status)
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
    model = _validated_payload(schemas.BlacklistCreateRequest, payload)
    success = db.add_to_blacklist(model.name, model.reason)
    db.log_activity("SERIES_BLACKLISTED", f"Blacklisted series: {model.name}", model.reason)
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
    model = _validated_payload(schemas.PrioritizedGroupCreateRequest, payload)
    success = db.add_prioritized_group(model.name, model.notes, model.priority)
    db.log_activity("GROUP_PRIORITIZED", f"Added prioritized release group: {model.name}", model.notes)
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
    """Track a newly discovered series under its STABLE matching key.

    The stable key is the Calibre series name when the series exists in Calibre,
    otherwise the name the user typed.  AniList's canonical English title is
    display-only metadata and must never be persisted as the series name (that
    used to break Calibre matching: ``Dungeon Meshi`` -> ``Delicious in Dungeon``).
    Matching tries, in priority order: Calibre-tracked name -> user-typed name ->
    AniList canonical English -> AniList romaji (Calibre always wins).
    """
    model = _validated_payload(schemas.DiscoveryTrackRequest, payload)
    typed_name = model.series_name
    release_id = model.release_id

    # Resolve the stable key (Calibre name first).  This still resolves + caches
    # AniList metadata (cover / total_volumes / synopsis) -- that part is orthogonal
    # and wanted; it just no longer decides the stored series name.
    identity = await asyncio.to_thread(scheduler.resolve_series_identity, typed_name)
    stable_name = identity.get("stable_name") or typed_name
    display_name = identity.get("display_name") or stable_name

    # Re-key the release (and any sibling row still stored under the typed name) so
    # releases.series_name and series_tracking.name stay one stable key.
    db.track_discovered_series(stable_name, release_id, legacy_names=[typed_name])
    tracked_msg = f"Started tracking new series: '{stable_name}'"
    if display_name and display_name != stable_name:
        tracked_msg += f" (display title: '{display_name}')"
    db.log_activity("SERIES_TRACKED", tracked_msg)
    if release_id:
        try:
            rel_item = db.get_release_by_id(release_id)
            if rel_item:
                scheduler.get_notifier().notify_new_request(rel_item)
        except Exception as e:
            logger.debug(f"Webhook notification failed on track: {e}")
    return JSONResponse({"status": "success", "tracked_name": stable_name})

@app.post("/api/discovery/blacklist")
async def api_discovery_blacklist(payload: Dict[str, Any]):
    """Blacklist a series directly from a discovery card."""
    model = _validated_payload(schemas.DiscoveryBlacklistRequest, payload)
    db.add_to_blacklist(model.series_name, model.reason)
    db.log_activity("SERIES_BLACKLISTED", f"Blacklisted series from discovery: {model.series_name}")
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
    """Save application settings (strict key whitelist; unknown keys rejected)."""
    model = _validated_payload(schemas.SettingsUpdateRequest, payload)
    provided = model.model_dump(exclude_unset=True, exclude_none=True) if hasattr(model, "model_dump") else {}
    operator_roots = _operator_allowed_path_roots()
    if operator_roots:
        for key in ("calibre_path", "staging_dir"):
            if key in provided:
                try:
                    security.ensure_path_within_roots(str(provided[key]), operator_roots)
                except security.UnsafePathError as exc:
                    raise HTTPException(status_code=400, detail=f"{key}: {exc}") from exc
    for k, v in provided.items():
        db.set_setting(k, str(v))
    db.log_activity("SETTINGS_UPDATED", "Application settings updated")
    return JSONResponse({"status": "success"})

@app.post("/api/settings/test-webhook")
async def api_test_webhook(payload: Optional[Dict[str, Any]] = None):
    """Test Discord and/or generic webhook endpoints with immediate feedback."""
    model = _validated_payload(schemas.WebhookTestRequest, payload or {})
    discord_url = model.discord_webhook_url
    generic_url = model.generic_webhook_url or model.apprise_url
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

    model = _validated_payload(schemas.BackfillStartRequest, payload or {})
    max_pages = model.max_pages
    delay = model.delay
    start_page = model.start_page if model.start_page is not None else 1

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
    """Debug maintenance operations: clear discovery, descriptions, or cover cache.

    M3 feature: destructive maintenance endpoints are opt-in only.  They are
    disabled by default and require ``MANGATRAX_DEBUG_ACTIONS=1``.
    """
    if not AUTH_CONFIG.debug_actions:
        raise HTTPException(
            status_code=403,
            detail="Debug actions are disabled. Set MANGATRAX_DEBUG_ACTIONS=1 to enable them.",
        )
    model = _validated_payload(schemas.DebugActionRequest, payload)
    action = model.action
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
        # The old behaviour renamed matching keys to AniList English titles, which
        # broke Calibre matching (P3a).  The action is kept for API compatibility but
        # now performs the guarded, idempotent legacy re-key repair instead: releases
        # stranded under an AniList display title are re-keyed back to their tracked
        # series' stable name.
        count = db.repair_rekeyed_series_names()
        return JSONResponse({"status": "success", "message": f"Re-keyed {count} release row(s) back to their tracked series' stable matching keys."})
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
