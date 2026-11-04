import os
import json
import math
import time
import threading
import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.database import MangaDatabase
from app.database import release_dedupe_signature
from app.calibre.scanner import CalibreMangaScanner
from app.providers.registry import create_provider
from app.scheduler import MangaScheduler
from app.covers import CoverCache
from app.downloader.qbit import QBitClient
from app import downloads_inventory, network_diagnostics, storage_health
from app.downloader import as_bool, build_downloader, resolve_downloader_engine
from app.downloader.base import DownloaderError, parse_queued_handle_id
from app.downloader.torbox import TorboxDownloader
from app.tagger import cbz_tagger, series_audit
from app.tagger import pipeline as downloads_pipeline
from app.tagger.cbz_tagger import has_comicinfo
from app.metadata.mangaupdates import MangaUpdatesService
from app.parser import extract_manga_bundle_and_volumes
from app import auth as auth_module
from app import schemas
from app import security
from app import search as search_module
from app import maintenance

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mangatrax")

# Torbox status last-good cache (task-32): transient API blips must not empty the UI.
_TORBOX_STATUS_TTL_SECONDS = 1.0
_TORBOX_STATUS_STALE_AFTER_SECONDS = 30.0
_TORBOX_SPEED_HISTORY_LIMIT = 120
_TORBOX_SPEED_AVG_SAMPLES = 10
_TORBOX_STATUS_CACHE: Dict[str, Any] = {"payload": None, "at": 0.0, "building": False}
_TORBOX_STATUS_LOCK = threading.Lock()
_TORBOX_SPEED_HISTORY: Dict[str, list] = {}
_NETWORK_SPEEDTEST_LAST: Dict[str, float] = {}
_NETWORK_SPEEDTEST_MIN_INTERVAL_SECONDS = 60.0

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
    # Availability first: NOTHING heavy may run before `yield`.  The legacy series
    # key repair and the initial Calibre sync used to run here (blocking the web UI
    # for ~2 minutes on a real library); they are now the FIRST action of the
    # scheduler worker thread (MangaScheduler.perform_initial_sync), which is
    # exception-shielded and refreshes the UI data as it completes.
    try:
        _refresh_storage_health("startup")
    except Exception as exc:  # pragma: no cover - diagnostics must never block startup
        logger.debug("Startup storage probe failed: %s", exc)
    scheduler.start()
    logger.info("Web UI is serving; the initial Calibre sync runs in the background worker.")
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


# ---------------------------------------------------------------------------
# Shared staging-directory helpers (M2 features 16 + CBZ <Series> repair)
# ---------------------------------------------------------------------------
# The tagger endpoints accept a *staging-relative filename* from the client.  The
# value is only ever used as a lookup key into the staging scan -- it is never
# joined into a filesystem path -- so ``..``/absolute/drive-rooted payloads cannot
# escape the staging directory even before the defence-in-depth checks below.
# The resolved real path is then re-validated for containment, symlink escape and
# the ``.cbz`` suffix before any read or write.

_STAGING_FILENAME_MAX_LENGTH = 512


def _staging_dir_setting() -> str:
    return db.get_setting("staging_dir", "/staging")


def _storage_health_paths() -> Dict[str, str]:
    """The two pipeline mounts probed for writability."""
    return {
        "downloads": scheduler.get_downloads_dir(create=False),
        "staging": _staging_dir_setting(),
    }


def _refresh_storage_health(reason: str = "") -> Dict[str, Any]:
    """Probe both pipeline folders, persist the result and warn about unwritable ones."""
    health = storage_health.build_storage_health(_storage_health_paths())
    try:
        db.set_setting("storage_health_json", json.dumps(health))
    except Exception as exc:  # pragma: no cover - diagnostics must never break startup
        logger.debug("Could not persist storage health: %s", exc)
    for label, probe in (health.get("paths") or {}).items():
        if not probe.get("writable"):
            logger.warning(storage_health.warning_message(label, probe))
    if reason:
        logger.info("Storage health refreshed (%s): ok=%s", reason, health.get("ok"))
    return health


def _storage_health_payload(force: bool = False) -> Dict[str, Any]:
    """Return the persisted probe when it still matches the configured paths."""
    if not force:
        try:
            raw = db.get_setting("storage_health_json", "")
            stored = json.loads(raw) if raw else {}
            current = _storage_health_paths()
            if stored.get("paths") and all(
                (stored.get("paths") or {}).get(label, {}).get("path") == value
                for label, value in current.items()
            ):
                return stored
        except Exception:
            pass
    return _refresh_storage_health("lazy")


def _staging_cbz_entries() -> list:
    """Enumerate staged ``.cbz`` files (read-only; the 'existing staging scan')."""
    return cbz_tagger.scan_staging_cbz_files(_staging_dir_setting())


def _resolve_staging_cbz(filename: str) -> dict:
    """Resolve a client-supplied filename to a validated staged ``.cbz`` entry.

    Security rules (all enforced here, before any file is opened):
      * the value must be a plain staging-relative name (no ``..``, no absolute or
        drive-rooted paths, no control bytes);
      * it must match exactly one entry of the staging scan, either by its
        staging-relative path or by its basename -- free-form paths are rejected;
      * the real path must stay inside ``staging_dir`` (symlink escapes included);
      * symlinked archives are rejected and the file must still be a regular
        ``.cbz`` that exists.
    """
    staging_dir = _staging_dir_setting()
    if not staging_dir or not os.path.isdir(staging_dir):
        raise HTTPException(status_code=400, detail="staging_dir is not configured or does not exist")

    if not isinstance(filename, str) or not filename.strip():
        raise HTTPException(status_code=400, detail="filename is required")

    candidate = filename.strip().replace("\\", "/")
    if not candidate or len(candidate) > _STAGING_FILENAME_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="filename is required")
    parts = [part for part in candidate.split("/") if part]
    if any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="filename must not contain '..' traversal segments")
    if candidate.startswith("/") or (len(candidate) > 1 and candidate[1] == ":"):
        raise HTTPException(status_code=400, detail="filename must be relative to the staging directory")

    entries = _staging_cbz_entries()
    matches = [entry for entry in entries if entry["relative"] == candidate]
    if not matches:
        matches = [entry for entry in entries if entry["name"] == candidate]
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"'{candidate}' is not a .cbz file present in the staging scan",
        )
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{candidate}' matches {len(matches)} staged files; pass the "
                "staging-relative path instead of the bare filename"
            ),
        )

    entry = matches[0]
    resolved = entry["path"]
    if not str(resolved).lower().endswith(".cbz"):
        raise HTTPException(status_code=400, detail="only .cbz archives can be previewed or tagged")
    if os.path.islink(resolved):
        raise HTTPException(status_code=403, detail="symlinked archives are not accepted")
    try:
        safe_path = security.ensure_path_within_roots(resolved, [staging_dir])
    except security.UnsafePathError as exc:
        raise HTTPException(status_code=403, detail=f"Rejected staging path: {exc}") from exc
    if not os.path.isfile(safe_path):
        raise HTTPException(status_code=404, detail="the staged archive no longer exists")

    resolved_entry = dict(entry)
    resolved_entry["path"] = safe_path
    return resolved_entry


# ---------------------------------------------------------------------------
# Downloader status (M2 feature 15 / PROJECT.md feature 15)
# ---------------------------------------------------------------------------
# The live progress API must never make a page render block on an unreachable or
# hung downloader: every call uses an explicit short socket timeout and any
# failure degrades to a valid, empty payload (the page still renders).

_DEFAULT_DOWNLOADER_STATUS_TIMEOUT = 1.5
_MAX_DOWNLOADER_STATUS_TIMEOUT = 5.0
_QBIT_ACTIVE_STATES = frozenset({
    "downloading", "metadl", "stalldl", "forceddl", "queueddl", "checkingdl",
    "allocating", "checkingresumedata",
})


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _torrent_status_view(torrent: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one qBittorrent torrent row for the status API."""
    state = str(torrent.get("state") or "").strip()
    progress = max(0.0, min(1.0, _as_float(torrent.get("progress"))))
    eta_raw = torrent.get("eta")
    eta_seconds: Optional[int]
    if eta_raw is None:
        eta_seconds = None
    else:
        eta_value = int(_as_float(eta_raw, 0))
        # qBittorrent reports 8640000 for "unknown / infinite".
        eta_seconds = eta_value if 0 < eta_value < 8640000 else None
    active = state.lower() in _QBIT_ACTIVE_STATES
    size_bytes = int(_as_float(torrent.get("size")))
    downloaded_bytes = int(_as_float(torrent.get("downloaded")))
    download_speed = int(_as_float(torrent.get("dlspeed")))
    upload_speed = int(_as_float(torrent.get("upspeed")))
    return {
        "hash": str(torrent.get("hash") or ""),
        "name": str(torrent.get("name") or ""),
        "state": state,
        "category": str(torrent.get("category") or ""),
        "progress": round(progress, 4),
        "progress_percent": round(progress * 100.0, 1),
        "size_bytes": size_bytes,
        "downloaded_bytes": downloaded_bytes,
        "download_speed": download_speed,
        "upload_speed": upload_speed,
        "eta_seconds": eta_seconds,
        "ratio": round(_as_float(torrent.get("ratio")), 3),
        "seeds": int(_as_float(torrent.get("num_seeds"))),
        "peers": int(_as_float(torrent.get("num_leechs"))),
        "is_active": active,
        # qBittorrent-native keys kept for the existing downloads template, so the
        # HTML view and the JSON API share one normalized snapshot.
        "size": size_bytes,
        "downloaded": downloaded_bytes,
        "dlspeed": download_speed,
        "upspeed": upload_speed,
        "eta": int(eta_seconds) if eta_seconds is not None else 0,
        "num_seeds": int(_as_float(torrent.get("num_seeds"))),
        "num_leechs": int(_as_float(torrent.get("num_leechs"))),
    }


def _build_downloader_status(timeout: Optional[float] = None) -> Dict[str, Any]:
    """Build the ``/api/downloads/status`` payload without ever raising.

    ``timeout`` is clamped to a short range (default 1.5s, max 5s) and passed to
    every downloader call, so an unreachable/hung qBittorrent costs at most one
    timeout and always yields a valid, degraded payload instead of a 500 or a
    stalled page render.
    """
    effective_timeout = _as_float(timeout, _DEFAULT_DOWNLOADER_STATUS_TIMEOUT)
    if effective_timeout <= 0:
        effective_timeout = _DEFAULT_DOWNLOADER_STATUS_TIMEOUT
    effective_timeout = min(effective_timeout, _MAX_DOWNLOADER_STATUS_TIMEOUT)

    payload: Dict[str, Any] = {
        "status": "degraded",
        "degraded": True,
        "engine": resolve_downloader_engine(db),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeout_seconds": round(effective_timeout, 2),
        "downloader": {
            "enabled": False,
            "connected": False,
            "version": "",
            "message": "Downloader status is currently unavailable.",
        },
        "torrents": [],
        "active": [],
        "count": 0,
        "totals": {
            "download_speed": 0,
            "upload_speed": 0,
            "progress": 0.0,
            "eta_seconds": None,
        },
    }

    if payload["engine"] == "torbox":
        return _torbox_status_cached(payload, effective_timeout)

    try:
        qbit = scheduler.get_qbit_client()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not build downloader client for status API: %s", exc)
        return payload

    payload["downloader"]["enabled"] = bool(getattr(qbit, "enabled", False))
    if not payload["downloader"]["enabled"]:
        payload["status"] = "disabled"
        payload["degraded"] = False
        payload["downloader"]["message"] = "Downloader is disabled in MangaTrax settings."
        return payload

    try:
        connection = qbit.test_connection(timeout=effective_timeout) or {}
    except Exception as exc:
        connection = {"success": False, "message": f"Connection error: {exc}", "version": ""}

    connected = bool(connection.get("success"))
    payload["downloader"]["connected"] = connected
    payload["downloader"]["version"] = str(connection.get("version") or "")
    payload["downloader"]["message"] = str(connection.get("message") or "")
    if not connected:
        payload["torrents"] = _torbox_db_rows()
        payload["active"] = [row for row in payload["torrents"] if row.get("is_active")]
        payload["count"] = len(payload["torrents"])
        payload["live_state_available"] = False
        payload["degraded"] = True
        _torbox_apply_speed_history(payload["torrents"], live=False)
        return payload

    try:
        category = db.get_setting("qbit_category", "manga") or "manga"
        raw_torrents = qbit.get_torrents(category=category, timeout=effective_timeout)
    except Exception as exc:
        payload["degraded"] = True
        payload["downloader"]["message"] = f"Connected, but the torrent list failed: {exc}"
        return payload

    torrents = [_torrent_status_view(torrent) for torrent in (raw_torrents or []) if isinstance(torrent, dict)]
    payload["live_state_available"] = True
    active = [torrent for torrent in torrents if torrent["is_active"]]
    total_size = sum(torrent["size_bytes"] for torrent in active)
    downloaded = sum(torrent["downloaded_bytes"] for torrent in active)

    payload.update({
        "status": "success",
        "degraded": False,
        "torrents": torrents,
        "active": active,
        "count": len(torrents),
        "totals": {
            "download_speed": sum(torrent["download_speed"] for torrent in active),
            "upload_speed": sum(torrent["upload_speed"] for torrent in active),
            "progress": round(downloaded / total_size, 4) if total_size else 0.0,
            "eta_seconds": _aggregate_eta(active),
        },
    })
    return payload


def _safe_json_list(raw: Any) -> list:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return values if isinstance(values, list) else []


def _torbox_release_view(release: Dict[str, Any], info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalise a Torbox release row (+optional live status) for the status API."""
    info = info or {}
    try:
        progress_data = json.loads(release.get("torbox_progress_json") or "{}")
    except (TypeError, ValueError):
        progress_data = {}
    if not isinstance(progress_data, dict):
        progress_data = {}
    local_phase = str(progress_data.get("phase") or "")
    db_status = str(release.get("download_status") or "").upper()
    # The DB is authoritative for failures: Torbox may still report `Ready` for a
    # torrent we refused locally (e.g. over the size cap), which used to look stuck.
    state = "failed" if db_status == "FAILED" else str(
        info.get("state") or ("failed" if db_status == "FAILED" else "queued")
    )
    if local_phase in ("fetching", "routing") and db_status != "FAILED":
        state = local_phase
    files = info.get("files") or []
    saved = release.get("downloader_id") or ""
    progress = float(info.get("progress") or 0.0)
    size = int(info.get("size") or 0)
    local_total = int(progress_data.get("total_bytes") or 0) or size
    local_downloaded = int(progress_data.get("downloaded_bytes") or 0)
    if local_phase == "fetching" and local_total:
        progress = max(0.0, min(1.0, local_downloaded / local_total))
    local_speed = int(progress_data.get("speed_bps") or 0)
    if local_phase == "fetching":
        speed = local_speed or speed
    local_eta = None
    if local_phase == "fetching" and local_speed > 0 and local_total > local_downloaded:
        local_eta = int((local_total - local_downloaded) / local_speed)
    progress_pct = round(progress * 100.0, 1)
    try:
        last_checked_epoch = float(db.get_setting("torbox_last_poll_at", "0") or 0)
    except (TypeError, ValueError):
        last_checked_epoch = 0.0
    last_checked_iso = (
        datetime.fromtimestamp(last_checked_epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if last_checked_epoch else ""
    )
    last_checked_seconds_ago = (
        max(0, int(time.time() - last_checked_epoch)) if last_checked_epoch else None
    )
    eta = info.get("eta") if isinstance(info.get("eta"), (int, float)) else None
    speed = int(info.get("download_speed") or 0)
    try:
        wanted_volumes = sorted(float(v) for v in json.loads(release.get("download_wanted_json") or "[]"))
        if not wanted_volumes:
            wanted_volumes = sorted(float(v) for v in json.loads(release.get("missing_vols_json") or "[]"))
    except Exception:
        wanted_volumes = []
    strict_volumes = as_bool(db.get_setting("torbox_strict_volume_selection", "true"), default=True)
    if wanted_volumes:
        selection = "missing volumes " + ", ".join(
            str(int(v)) if float(v).is_integer() else str(v) for v in wanted_volumes
        )
    elif strict_volumes:
        selection = "nothing missing"
    else:
        selection = "legacy whole-torrent fallback allowed"
    failed = state == "failed" or db_status == "FAILED"
    error = str(release.get("download_error") or "").strip()
    if failed and not error:
        error = f"Torbox reported state '{info.get('raw_state') or 'failed'}'."
    can_retry = bool(failed and str(release.get("status") or "") in ("REQUESTED", "DISCOVERY", "NEW"))
    return {
        "hash": str(release.get("torrent_hash") or saved),
        "name": str(release.get("title") or ""),
        "state": state,
        "failed": failed,
        "error": error,
        "can_retry": can_retry,
        "attempts": int(release.get("download_attempts") or 0),
        "download_status": db_status,
        "phase": local_phase or ("done" if db_status == "COMPLETED" else "ready" if state == "ready" else ""),
        "progress_pct": progress_pct,
        "downloaded_bytes": local_downloaded if local_phase == "fetching" else int(round(size * progress)),
        "total_bytes": local_total,
        "speed_bps": local_speed if local_phase == "fetching" else 0,
        "eta_seconds": local_eta,
        "last_checked_at": last_checked_iso,
        "last_checked_seconds_ago": last_checked_seconds_ago,
        "downloaded_at": str(release.get("downloaded_at") or ""),
        "routed_at": str(release.get("routed_at") or ""),
        "download_files": _safe_json_list(release.get("download_files_json")),
        "raw_state": str(info.get("raw_state") or ""),
        "engine": "torbox",
        "cached": bool(info.get("cached")),
        "files": files,
        "selection": selection,
        "wanted_volumes": wanted_volumes,
        "strict_volume_selection": strict_volumes,
        "category": "manga",
        "progress": round(max(0.0, min(1.0, progress)), 4),
        "progress_percent": round(max(0.0, min(1.0, progress)) * 100.0, 1),
        "size_bytes": size,
        # Template-compatible aliases (downloads.html historically reads qBittorrent's
        # raw field names; keep both so neither engine breaks the table).
        "size": size,
        "eta": eta,
        "dlspeed": speed,
        "downloaded_bytes": int(round(size * progress)),
        "download_speed": speed,
        "upload_speed": 0,
        "eta_seconds": int(eta) if eta is not None else None,
        "ratio": 0.0,
        "seeds": info.get("seeds") or 0,
        "is_active": (not failed) and state in ("queued", "queued_slot", "downloading", "stalled"),
        "queued": state == "queued_slot",
        "queue_position": info.get("queue_position"),
        "active_limit": info.get("active_limit"),
        "release_id": release.get("id"),
    }


def _torbox_db_rows(limit: int = 20) -> list:
    """DB-derived Torbox rows (available even when the live API is down)."""
    try:
        releases = db.get_downloader_releases("torbox", limit=limit)
    except Exception as exc:
        logger.debug("Could not read Torbox releases for status: %s", exc)
        return []
    return [_torbox_release_view(release, None) for release in releases]


def _torbox_decorate(payload: Dict[str, Any], now: float, stale: bool, age: float) -> Dict[str, Any]:
    """Attach cache/staleness metadata without mutating the cached object."""
    result = dict(payload)
    result["downloader"] = dict(payload.get("downloader") or {})
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["cache_age_seconds"] = round(max(0.0, age), 2)
    result["stale"] = bool(stale)
    result["stale_age_seconds"] = round(max(0.0, age), 1) if stale else 0
    result["live_state_available"] = not bool(stale) and bool(payload.get("live_state_available", False))
    if stale:
        result["degraded"] = True
        result["downloader"]["message"] = (
            (result["downloader"].get("message") or "")
            + f" (showing last known state from {max(0, int(age))}s ago)"
        ).strip()
        result["torrents"] = list(payload.get("torrents") or [])
        result["active"] = [row for row in result["torrents"] if row.get("is_active")]
        result["count"] = len(result["torrents"])
    return result


def _torbox_apply_speed_history(torrents: list, live: bool) -> None:
    """Capped per-release speed history + rolling average for the UI sparkline."""
    for torrent in torrents:
        key = str(torrent.get("release_id") or torrent.get("hash") or torrent.get("name"))
        speed = int(torrent.get("speed_bps") or torrent.get("download_speed") or 0)
        history = _TORBOX_SPEED_HISTORY.setdefault(key, [])
        if live:
            history.append(max(0, speed))
            if len(history) > _TORBOX_SPEED_HISTORY_LIMIT:
                del history[:-_TORBOX_SPEED_HISTORY_LIMIT]
        torrent["speed_history"] = list(history[-60:])
        samples = history[-_TORBOX_SPEED_AVG_SAMPLES:]
        torrent["speed_avg_bps"] = int(sum(samples) / len(samples)) if samples else 0


def _torbox_status_cached(initial_payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """Last-good cache + single-flight around the Torbox status build (≈1 s TTL)."""
    now = time.time()
    db_identity = id(db)
    with _TORBOX_STATUS_LOCK:
        if _TORBOX_STATUS_CACHE.get("db_id") not in (None, db_identity):
            _TORBOX_STATUS_CACHE.update({"payload": None, "at": 0.0, "building": False})
        _TORBOX_STATUS_CACHE["db_id"] = db_identity
        cached = _TORBOX_STATUS_CACHE.get("payload")
        cached_at = float(_TORBOX_STATUS_CACHE.get("at") or 0.0)
        if cached is not None and (now - cached_at) < _TORBOX_STATUS_TTL_SECONDS:
            return _torbox_decorate(cached, now, stale=False, age=now - cached_at)
        if _TORBOX_STATUS_CACHE.get("building"):
            if cached is not None:
                return _torbox_decorate(cached, now, stale=True, age=now - cached_at)
            # Single-flight: never start a second concurrent build.  With no last-good
            # state, serve DB-derived rows until the in-flight build finishes.
            fallback = dict(initial_payload)
            fallback["torrents"] = _torbox_db_rows()
            fallback["active"] = [row for row in fallback["torrents"] if row.get("is_active")]
            fallback["count"] = len(fallback["torrents"])
            fallback["live_state_available"] = False
            fallback["status"] = "degraded"
            fallback["degraded"] = True
            fallback["downloader"] = dict(fallback.get("downloader") or {})
            fallback["downloader"]["message"] = "Refreshing downloader state…"
            _torbox_apply_speed_history(fallback["torrents"], live=False)
            return _torbox_decorate(fallback, now, stale=False, age=0.0)
        _TORBOX_STATUS_CACHE["building"] = True

    built: Optional[Dict[str, Any]] = None
    try:
        built = _build_torbox_downloader_status(initial_payload, timeout)
    except Exception as exc:  # pragma: no cover - defensive: status must never 500
        logger.debug("Torbox status build failed: %s", exc)
        built = None
    finally:
        with _TORBOX_STATUS_LOCK:
            _TORBOX_STATUS_CACHE["building"] = False

    if built is not None and built.get("status") == "success" and (built.get("downloader") or {}).get("connected"):
        _torbox_apply_speed_history(built.get("torrents") or [], live=True)
        with _TORBOX_STATUS_LOCK:
            _TORBOX_STATUS_CACHE["payload"] = built
            _TORBOX_STATUS_CACHE["at"] = time.time()
        return _torbox_decorate(built, time.time(), stale=False, age=0.0)

    now = time.time()
    with _TORBOX_STATUS_LOCK:
        cached = _TORBOX_STATUS_CACHE.get("payload")
        cached_at = float(_TORBOX_STATUS_CACHE.get("at") or 0.0)
    if cached is not None and (now - cached_at) <= _TORBOX_STATUS_STALE_AFTER_SECONDS:
        return _torbox_decorate(cached, now, stale=True, age=now - cached_at)

    # No usable last-good state: serve DB-derived rows (live_state_available False).
    fallback = built or dict(initial_payload)
    fallback["torrents"] = _torbox_db_rows()
    fallback["active"] = [row for row in fallback["torrents"] if row.get("is_active")]
    fallback["count"] = len(fallback["torrents"])
    fallback["live_state_available"] = False
    fallback["status"] = fallback.get("status") or "degraded"
    fallback["degraded"] = True
    _torbox_apply_speed_history(fallback["torrents"], live=False)
    return _torbox_decorate(fallback, now, stale=cached is not None, age=(now - cached_at) if cached else 0.0)


def _build_torbox_downloader_status(payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """Torbox branch of ``/api/downloads/status``; never raises."""
    payload["downloader"]["enabled"] = as_bool(db.get_setting("torbox_enabled", "false"), default=False)
    if not payload["downloader"]["enabled"]:
        payload["status"] = "disabled"
        payload["degraded"] = False
        payload["downloader"]["message"] = "Torbox is disabled in MangaTrax settings."
        payload["torrents"] = _torbox_db_rows()
        payload["active"] = [row for row in payload["torrents"] if row.get("is_active")]
        payload["count"] = len(payload["torrents"])
        payload["live_state_available"] = False
        _torbox_apply_speed_history(payload["torrents"], live=False)
        return payload

    try:
        downloader = build_downloader(db, engine="torbox")
        downloader.timeout = max(1.0, min(float(timeout), 30.0))
    except Exception as exc:  # pragma: no cover - defensive
        payload["downloader"]["message"] = f"Could not construct the Torbox client: {exc}"
        payload["torrents"] = _torbox_db_rows()
        payload["active"] = [row for row in payload["torrents"] if row.get("is_active")]
        payload["count"] = len(payload["torrents"])
        payload["live_state_available"] = False
        _torbox_apply_speed_history(payload["torrents"], live=False)
        return payload

    try:
        connection = downloader.test_connection() or {}
    except Exception as exc:
        connection = {"success": False, "message": f"Connection error: {exc}"}

    connected = bool(connection.get("success"))
    payload["downloader"]["connected"] = connected
    payload["downloader"]["version"] = "torbox"
    payload["downloader"]["message"] = str(connection.get("message") or "")
    payload["downloader"]["plan"] = str(connection.get("plan") or "")
    payload["downloader"]["active_slots"] = connection.get("active_slots")
    if not connected:
        return payload

    try:
        releases = db.get_downloader_releases("torbox", limit=20)
    except Exception as exc:
        payload["degraded"] = True
        payload["downloader"]["message"] = f"Connected, but the Torbox queue failed: {exc}"
        payload["torrents"] = _torbox_db_rows()
        payload["active"] = [row for row in payload["torrents"] if row.get("is_active")]
        payload["count"] = len(payload["torrents"])
        payload["live_state_available"] = False
        _torbox_apply_speed_history(payload["torrents"], live=False)
        return payload

    torrents = []
    payload["degraded"] = False
    for release in releases:
        info: Optional[Dict[str, Any]] = None
        if release.get("download_status") == "DOWNLOADING":
            queued_id = parse_queued_handle_id(release.get("downloader_id"))
            if queued_id is not None:
                handle = {
                    "engine": "torbox",
                    "queued_id": queued_id,
                    "torrent_id": None,
                    "hash": release.get("torrent_hash") or "",
                }
            else:
                handle = {"torrent_id": release.get("downloader_id"), "hash": release.get("torrent_hash") or ""}
            try:
                if handle.get("torrent_id") or handle.get("queued_id"):
                    info = downloader.status(handle)
            except DownloaderError as exc:
                info = None
                payload["degraded"] = True
                payload["downloader"]["message"] = f"Queue partially unavailable: {exc}"
        torrents.append(_torbox_release_view(release, info))

    active = [torrent for torrent in torrents if torrent["is_active"]]
    total_size = sum(torrent["size_bytes"] for torrent in active)
    downloaded = sum(torrent["downloaded_bytes"] for torrent in active)
    try:
        last_checked_epoch = float(db.get_setting("torbox_last_poll_at", "0") or 0)
    except (TypeError, ValueError):
        last_checked_epoch = 0.0
    payload["downloader"]["last_checked_at"] = (
        datetime.fromtimestamp(last_checked_epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if last_checked_epoch else ""
    )
    payload["downloader"]["last_checked_seconds_ago"] = (
        max(0, int(time.time() - last_checked_epoch)) if last_checked_epoch else None
    )
    payload.update({
        "status": "success",
        "degraded": bool(payload["degraded"]),
        "torrents": torrents,
        "active": active,
        "count": len(torrents),
        "totals": {
            "download_speed": sum(torrent["download_speed"] for torrent in active),
            "upload_speed": 0,
            "progress": round(downloaded / total_size, 4) if total_size else 0.0,
            "eta_seconds": _aggregate_eta(active),
        },
    })
    return payload


def _aggregate_eta(active_torrents: list) -> Optional[int]:
    """Overall remaining seconds across active torrents (None when unknown)."""
    remaining_bytes = 0
    speed = 0
    for torrent in active_torrents:
        remaining = max(0, torrent["size_bytes"] - torrent["downloaded_bytes"])
        remaining_bytes += remaining
        speed += torrent["download_speed"]
    if remaining_bytes <= 0:
        return 0
    if speed <= 0:
        return None
    return int(remaining_bytes / speed)



# --- Authentication & health endpoints (exempt from the auth gate) ---

@app.get("/health")
async def health_check():
    """Liveness probe (exempt from auth so Docker/Traefik health checks work)."""
    payload = AUTH.health_payload()
    try:
        payload["storage"] = _storage_health_payload()
    except Exception as exc:  # pragma: no cover - /health must never fail on diagnostics
        payload["storage"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        payload["network"] = network_diagnostics.collect_network_diagnostics(
            settings=db.get_all_settings(),
        )
    except Exception as exc:  # pragma: no cover - diagnostics must never fail /health
        payload["network"] = {"error": f"{type(exc).__name__}: {exc}", "warnings": []}
    return JSONResponse(payload)


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

    # Live downloader snapshot behind a short timeout, off the event loop, so a
    # hung/unreachable qBittorrent can never stall the dashboard render.
    download_status = await asyncio.to_thread(_build_downloader_status, 1.0)
    torrents = download_status.get("torrents") or []

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
        # Dedupe identity uses the SORTED group set so "(1r0n, Ushi)" and
        # "[1r0n] [Ushi]" are the same release regardless of tag style/order.
        sig = release_dedupe_signature(r)
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
    # Short, explicit timeout + graceful degradation: an unreachable downloader
    # renders the page with a "degraded" banner instead of blocking on sockets.
    download_status = await asyncio.to_thread(_build_downloader_status, 1.2)
    try:
        inventory = await asyncio.to_thread(downloads_inventory.refresh_inventory, db, scheduler.get_downloads_dir(create=False))
    except Exception as exc:  # pragma: no cover - page must render even if the scan fails
        logger.debug("Downloads inventory scan for page failed: %s", exc)
        inventory = downloads_inventory.load_inventory(db)
    try:
        scanned_at = datetime.strptime(inventory.get("scanned_at") or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        inventory_scanned_seconds = max(0, int((datetime.now(timezone.utc) - scanned_at).total_seconds()))
    except (TypeError, ValueError):
        inventory_scanned_seconds = None
    qbit_status = {
        "success": download_status["downloader"]["connected"],
        "message": download_status["downloader"]["message"],
        "version": download_status["downloader"]["version"],
        "degraded": download_status.get("degraded", False),
    }
    torrents = download_status.get("torrents") or []

    # Check files in staging directory
    staging_dir = _staging_dir_setting()
    staging_files = []
    if os.path.isdir(staging_dir):
        for entry in cbz_tagger.scan_staging_cbz_files(staging_dir):
            staging_files.append({
                "name": entry["relative"],
                "size_mb": entry["size_mb"],
                "has_comicinfo": has_comicinfo(entry["path"]),
            })

    # task-22: raw downloads awaiting a Calibre series (read-only scan for the UI).
    downloads_dir = db.get_setting("downloads_dir", "/downloads")
    try:
        awaiting_series = downloads_pipeline.scan_awaiting_series(
            downloads_dir, db.get_series_templates_map(),
        )
    except Exception as exc:  # pragma: no cover - a broken scan must not break the page
        logger.debug("Could not scan awaiting downloads series: %s", exc)
        awaiting_series = []
    awaiting_total = sum(int(item.get("count") or 0) for item in awaiting_series)

    return templates.TemplateResponse(
        request=request,
        name="downloads.html",
        context={
            "active_page": "downloads",
            "qbit_status": qbit_status,
            "engine": download_status.get("engine") or "qbit",
            "torrents": torrents,
            "torbox_last_checked_seconds_ago": (download_status.get("downloader") or {}).get("last_checked_seconds_ago"),
            "staging_dir": staging_dir,
            "downloads_dir": downloads_dir,
            "storage_health": _storage_health_payload(),
            "downloads_inventory": inventory,
            "downloads_inventory_scanned_seconds": inventory_scanned_seconds,
            "staging_files": staging_files,
            "awaiting_series": awaiting_series,
            "awaiting_total": awaiting_total,
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
    torbox_key_set = bool(str(settings.get("torbox_api_key", "") or "").strip())
    settings["torbox_api_key"] = schemas.TORBOX_API_KEY_MASK if torbox_key_set else ""
    prioritized_groups = db.get_prioritized_groups()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "settings": settings,
            "prioritized_groups": prioritized_groups,
            "torbox_api_key_set": torbox_key_set,
            "storage_health": _storage_health_payload(),
            "network_diagnostics": network_diagnostics.collect_network_diagnostics(
                settings=db.get_all_settings(),
            ),
            "debug_actions_enabled": AUTH_CONFIG.debug_actions
        }
    )

# --- API Endpoints ---

@app.post("/api/calibre/sync")
async def api_calibre_sync():
    """Trigger manual Calibre library rescan and bundle request resolution.

    Deliberately synchronous: a user who clicks "Sync now" waits for the result.
    The background worker's own sync respects ``scheduler._calibre_sync_lock``, so a
    manual trigger and the startup/first-tick sync can never run concurrently.
    """
    res = scheduler.sync_calibre_library()
    return JSONResponse(res)

@app.get("/api/calibre/status")
async def api_calibre_status():
    """Cheap, non-blocking sync state so the UI can show "initial sync running"."""
    return JSONResponse(scheduler.get_calibre_sync_status())

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
    """Start a feed poll in the background and return immediately.

    The poll is network-heavy (Nyaa/RSS/HTML fetches) and used to run inline inside
    this async handler, which blocked the event loop: the whole web UI froze for the
    duration of the poll.  It now runs on the scheduler's daemon thread; poll
    ``GET /api/feeds/check/status`` for progress and the result (including per-feed
    failures, so a dead Nyaa lookup no longer looks like a silent success).
    """
    return JSONResponse(scheduler.start_feed_check())

@app.get("/api/feeds/check/status")
async def api_feeds_check_status():
    """Non-blocking feed-poll status: running flag, timestamps and last result."""
    return JSONResponse(scheduler.get_feed_check_status())

@app.post("/api/feeds/test")
async def api_feeds_test(payload: Dict[str, Any]):
    """Live test a feed or scraper extraction without saving to database."""
    model = _validated_payload(schemas.FeedTestRequest, payload)
    url = model.url
    feed_type = model.feed_type
    config = model.config

    try:
        provider = create_provider(
            {"name": "Test Feed", "feed_type": feed_type, "url": url, "config_json": json.dumps(config)},
            nyaa_base_url=scheduler._nyaa_base_url(),
        )
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

@app.post("/api/feeds/{feed_id}/toggle")
async def api_toggle_feed(feed_id: int, payload: Dict[str, Any]):
    """Enable/disable a feed without deleting it.

    A blocked/broken source (e.g. nyaa.si on a network where it is DNS-blocked) can
    be switched off; the scheduler already skips disabled feeds.
    """
    model = _validated_payload(schemas.FeedToggleRequest, payload)
    if not db.set_feed_enabled(feed_id, model.enabled):
        raise HTTPException(status_code=404, detail="Feed not found")
    message = f"Feed #{feed_id} {'enabled' if model.enabled else 'disabled'}"
    db.log_activity(
        "FEED_ENABLED" if model.enabled else "FEED_DISABLED",
        message,
    )
    return JSONResponse({"status": "success", "feed_id": feed_id, "enabled": model.enabled})

@app.post("/api/staging/tag")
async def api_staging_tag():
    """Process downloads: tag+moved into staging when the series is in Calibre.

    Also keeps the legacy staging pass (already-staged archives) so files that were
    put in staging manually still get tagged.  The manual action runs with the
    request actor (not 'scheduler') in the audit trail.
    """
    routing = await asyncio.to_thread(scheduler.process_downloads_to_staging, None)
    legacy = await asyncio.to_thread(scheduler.tag_staging_files)
    message = (
        f"Processed downloads: {routing.get('tagged_and_moved', 0)} tagged and moved to staging, "
        f"{routing.get('awaiting_calibre', 0)} awaiting a Calibre series, "
        f"{routing.get('failed', 0)} failed, {routing.get('skipped_existing', 0)} already present; "
        f"{legacy.get('tagged_count', 0)} existing staging file(s) tagged."
    )
    return JSONResponse({
        "status": "success" if routing.get("failed", 0) == 0 else "partial",
        "success": routing.get("failed", 0) == 0,
        "message": message,
        "tagged_count": routing.get("tagged_and_moved", 0) + legacy.get("tagged_count", 0),
        "tagged_files": legacy.get("tagged_files", []),
        **routing,
    })


# --- Live downloader progress (PROJECT.md feature 15) ---

@app.get("/api/downloads/status")
async def api_downloads_status(timeout: float = Query(default=_DEFAULT_DOWNLOADER_STATUS_TIMEOUT, ge=0.2, le=_MAX_DOWNLOADER_STATUS_TIMEOUT)):
    """Active torrent states, progress, speeds and ETA with graceful degradation.

    Always returns HTTP 200 with a valid payload: when the downloader is disabled
    or unreachable the response is ``{"status": "disabled"|"degraded", ...}`` with
    empty torrent lists, so callers can render the page synchronously.  The socket
    timeout is short and clamped (default 1.5s, max 5s) and the blocking client
    runs in a worker thread, so a hung downloader cannot stall the event loop.
    """
    payload = await asyncio.to_thread(_build_downloader_status, timeout)
    try:
        inventory = downloads_inventory.load_inventory(db)
        payload["inventory"] = inventory
        payload["last_scanned_at"] = inventory.get("scanned_at") or db.get_setting("downloads_scanned_at", "")
    except Exception as exc:  # pragma: no cover - status must never fail on inventory
        logger.debug("Could not attach downloads inventory: %s", exc)
        payload["inventory"] = {"entries": [], "scanned_at": ""}
        payload["last_scanned_at"] = ""
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.post("/api/downloads/torbox/poll")
async def api_torbox_poll_now():
    """Check now: run one Torbox poll pass immediately and return fresh counters."""
    if resolve_downloader_engine(db) != "torbox":
        raise HTTPException(status_code=409, detail="Torbox is not the active downloader engine")
    result = await asyncio.to_thread(scheduler.torbox_poll_now)
    status_payload = await asyncio.to_thread(_build_downloader_status, 1.2)
    return JSONResponse({
        "status": result.get("status") or "ok",
        "checked": result.get("processed", 0),
        "completed": result.get("completed", 0),
        "failed": result.get("failed", 0),
        "queued_fetch": result.get("queued_fetch", 0),
        "fetching": result.get("fetching", 0),
        "last_checked_at": result.get("last_checked_at") or "",
        "torrents": status_payload.get("torrents") or [],
        "downloader": status_payload.get("downloader") or {},
    })


@app.post("/api/downloads/scan")
async def api_downloads_scan():
    """Rescan the raw downloads folder and return the fresh inventory."""
    inventory = await asyncio.to_thread(
        downloads_inventory.refresh_inventory, db, scheduler.get_downloads_dir(create=False),
    )
    return JSONResponse({
        "status": "success",
        "inventory": inventory,
        "last_scanned_at": inventory.get("scanned_at") or "",
        "entries": inventory.get("entries") or [],
    })


@app.get("/api/diagnostics/network")
async def api_network_diagnostics():
    """Full in-container network snapshot (read-only, cheap, no network I/O)."""
    snapshot = await asyncio.to_thread(
        network_diagnostics.collect_network_diagnostics, db.get_all_settings(),
    )
    return JSONResponse(snapshot)


@app.post("/api/diagnostics/speedtest")
async def api_network_speedtest(payload: Optional[Dict[str, Any]] = None):
    """Bounded speed test against the fixed Cloudflare endpoint (never arbitrary URLs)."""
    now = time.time()
    last = _NETWORK_SPEEDTEST_LAST.get("at", 0.0)
    if now - last < _NETWORK_SPEEDTEST_MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"Speed test rate-limited; wait {int(_NETWORK_SPEEDTEST_MIN_INTERVAL_SECONDS - (now - last))}s.",
        )
    _NETWORK_SPEEDTEST_LAST["at"] = now
    body = payload or {}
    try:
        bytes_total = int(body.get("bytes") or network_diagnostics.SPEEDTEST_DEFAULT_BYTES)
        connections = int(body.get("connections") or network_diagnostics.SPEEDTEST_DEFAULT_CONNECTIONS)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="bytes and connections must be integers")
    result = await asyncio.to_thread(
        network_diagnostics.run_speedtest,
        bytes_total, connections, network_diagnostics.SPEEDTEST_URL,
        network_diagnostics.SPEEDTEST_TIMEOUT_SECONDS, db.get_all_settings(),
    )
    try:
        db.set_setting("last_speedtest_json", json.dumps({
            key: result.get(key) for key in
            ("status", "bytes_requested", "bytes_transferred", "connections",
             "aggregate_mbps", "per_connection", "wall_seconds", "capped", "error")
        }))
    except Exception as exc:  # pragma: no cover - result persistence is best effort
        logger.debug("Could not persist last speed test: %s", exc)
    if result.get("status") == "error" and (
        "must be between" in str(result.get("error") or "")
        or "URL rejected" in str(result.get("error") or "")
    ):
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


# --- Tagger: preview / manual tagging (PROJECT.md feature 16) ---

def _preview_staging_cbz(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Read a staged CBZ's current ComicInfo (read-only; safe to poll)."""
    parsed = extract_manga_bundle_and_volumes(entry["name"])
    result: Dict[str, Any] = {
        "status": "success",
        "file": entry["relative"],
        "name": entry["name"],
        "size_mb": entry["size_mb"],
        "has_comicinfo": False,
        "comicinfo": None,
        "parsed": {
            "series_name": parsed.get("series_name") or "",
            "volume_start": parsed.get("volume_start"),
            "volume_end": parsed.get("volume_end"),
            "volumes": parsed.get("volumes") or [],
            "is_bundle": bool(parsed.get("is_bundle")),
        },
        "warning": None,
    }
    try:
        fields = cbz_tagger.read_comicinfo(entry["path"])
    except cbz_tagger.UnreadableArchiveError as exc:
        raise HTTPException(status_code=422, detail=f"Unreadable .cbz archive: {exc}") from exc
    except cbz_tagger.MalformedComicInfoError as exc:
        result["status"] = "invalid_comicinfo"
        result["has_comicinfo"] = True
        result["warning"] = str(exc)
        return result

    if fields is None:
        return result
    result["has_comicinfo"] = True
    result["comicinfo"] = fields
    return result


def _format_volume_value(volume: Optional[str]) -> Optional[str]:
    text = (volume or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number == int(number) else str(number)


def _manual_tag_updates(model) -> Dict[str, Any]:
    updates: Dict[str, Any] = {"Series": model.series}
    volume = _format_volume_value(model.volume)
    if volume:
        updates["Number"] = volume
        updates["Volume"] = volume
    if model.title:
        updates["Title"] = model.title
    if model.writer:
        updates["Writer"] = model.writer
        updates["Penciller"] = model.writer
    if model.publisher:
        updates["Publisher"] = model.publisher
    if model.genre:
        updates["Genre"] = model.genre
    return updates


def _manual_tag_staging_cbz(entry: Dict[str, Any], model) -> Dict[str, Any]:
    """Write user-supplied ComicInfo metadata into one staged CBZ (explicit action)."""
    via_fallback = False
    try:
        existing_xml = cbz_tagger.read_comicinfo_xml(entry["path"])
    except cbz_tagger.ComicInfoError as exc:
        raise HTTPException(status_code=422, detail=f"Unreadable .cbz archive: {exc}") from exc

    updates = _manual_tag_updates(model)
    volume = updates.get("Volume")
    defaults = {
        "Title": f"{model.series} Vol. {volume}" if volume else model.series,
        "Manga": "YesAndRightToLeft",
    }

    def _with_defaults(base: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(defaults)
        merged.update(base)
        return merged

    try:
        xml = cbz_tagger.build_comicinfo_xml(
            existing_xml, updates if existing_xml else _with_defaults(updates)
        )
    except cbz_tagger.MalformedComicInfoError:
        # An explicit manual tag is the supported way to replace a corrupt
        # ComicInfo.xml, so fall back to a fresh document built from the request.
        via_fallback = True
        xml = cbz_tagger.build_comicinfo_xml(None, _with_defaults(updates))

    if not cbz_tagger.rewrite_comicinfo(entry["path"], xml):
        raise HTTPException(status_code=500, detail="Failed to rewrite ComicInfo.xml")

    db.log_activity(
        "MANUAL_TAG_CBZ",
        f"Manually tagged '{entry['relative']}' as series '{model.series}'",
        f"Volume: {volume or '-'}; writer: {model.writer or '-'}; publisher: {model.publisher or '-'}",
    )
    return {
        "status": "success",
        "file": entry["relative"],
        "name": entry["name"],
        "replaced_invalid_comicinfo": via_fallback,
        "comicinfo": cbz_tagger.read_comicinfo(entry["path"]) or {},
    }


@app.get("/api/tagger/preview")
async def api_tagger_preview(filename: str = Query(..., min_length=1, max_length=_STAGING_FILENAME_MAX_LENGTH)):
    """Return one staged CBZ's current ComicInfo.xml without modifying it.

    ``filename`` must be a file returned by the staging scan; traversal payloads,
    symlinks and free-form paths are rejected by ``_resolve_staging_cbz``.
    """
    entry = await asyncio.to_thread(_resolve_staging_cbz, filename)
    return JSONResponse(await asyncio.to_thread(_preview_staging_cbz, entry))


@app.post("/api/tagger/manual-tag")
async def api_tagger_manual_tag(payload: Dict[str, Any]):
    """Write user-supplied metadata into one staged CBZ (feature 16 workflow).

    This is how a brand-new series is hand-tagged before Calibre knows it: the
    user supplies the series name (the matching key) plus any known volume/title/
    writer/publisher/genre.  Only fields present in the request are written; the
    existing ComicInfo.xml is updated in place (no duplicate entries) and image
    entries are copied untouched.
    """
    model = _validated_payload(schemas.ManualTagRequest, payload)
    entry = await asyncio.to_thread(_resolve_staging_cbz, model.filename)
    result = await asyncio.to_thread(_manual_tag_staging_cbz, entry, model)
    return JSONResponse(result)


# --- CBZ <Series> audit / explicit repair (the old rename-bug data repair) ---

def _calibre_series_snapshot() -> Optional[list]:
    """Live Calibre series list when the library is reachable (else ``None``)."""
    try:
        scanner = CalibreMangaScanner(db.get_setting("calibre_path", CALIBRE_DIR))
        if scanner.is_available():
            return scanner.get_all_series()
    except Exception as exc:  # pragma: no cover - audit falls back to synced rows
        logger.debug("Calibre snapshot unavailable for the CBZ audit: %s", exc)
    return None


def _run_staging_cbz_audit() -> Dict[str, Any]:
    return series_audit.audit_staging_cbz_files(
        db, _staging_dir_setting(), calibre_series=_calibre_series_snapshot()
    )


@app.get("/api/tagger/audit")
async def api_tagger_audit():
    """Read-only audit of staged CBZ ``<Series>`` values (never modifies a file).

    Reports per file the current ``<Series>``, the suggested corrected name, why
    the file is/ isn't safe to repair, and a summary.  Run this before
    ``POST /api/tagger/repair-series`` and let the user select the files.
    """
    return JSONResponse(await asyncio.to_thread(_run_staging_cbz_audit))


def _repair_staging_cbz_series(
    filenames: list,
    repair_writer: bool = True,
    repair_publisher: bool = True,
) -> Dict[str, Any]:
    """Repair ``<Series>``/Writer/Publisher for the explicitly selected files only.

    Every selection is resolved through the staging scan first; if any filename is
    invalid the whole request is rejected before a single write (so a typo cannot
    half-apply a selection).  For each resolved file the audit is re-run and only
    provably safe changes are written.  All writes are audit-logged through the
    request's authenticated actor.
    """
    resolved = [_resolve_staging_cbz(name) for name in filenames]
    reference = series_audit.build_series_reference(db, calibre_series=_calibre_series_snapshot())

    counts = {"requested": len(resolved), "repaired": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    results: list = []

    for entry in resolved:
        record = series_audit.audit_entry(entry, reference)
        plan = series_audit.plan_repair(
            record, repair_writer=repair_writer, repair_publisher=repair_publisher
        )
        item: Dict[str, Any] = {
            "file": record["file"],
            "name": record["name"],
            "status": record["status"],
            "outcome": "skipped",
            "changes": [],
            "suggested_series": record.get("suggested_series"),
            "reason": plan.get("skipped_reason") or record.get("reason") or "",
        }

        if not plan["updates"]:
            if record["status"] == series_audit.STATUS_OK and not record.get("changes"):
                item["outcome"] = "unchanged"
                item["reason"] = "already correct; nothing to rewrite (idempotent)"
                counts["unchanged"] += 1
            else:
                counts["skipped"] += 1
            results.append(item)
            continue

        try:
            existing_xml = cbz_tagger.read_comicinfo_xml(entry["path"])
            xml = cbz_tagger.build_comicinfo_xml(existing_xml, plan["updates"])
            written = cbz_tagger.rewrite_comicinfo(entry["path"], xml)
        except Exception as exc:  # noqa: BLE001 - per-file failure must not abort the batch
            item.update(outcome="failed", reason=f"rewrite failed: {exc}")
            counts["failed"] += 1
            results.append(item)
            continue

        if not written:
            item.update(outcome="failed", reason="rewrite failed (archive left untouched)")
            counts["failed"] += 1
            results.append(item)
            continue

        item["outcome"] = "repaired"
        item["changes"] = plan["changes"]
        counts["repaired"] += 1
        db.log_activity(
            "CBZ_SERIES_REPAIRED",
            f"Repaired ComicInfo.xml in '{record['file']}' ({', '.join(plan['changes'])})",
            f"Series -> '{plan['updates'].get('Series', record.get('current_series')) or ''}'; "
            f"changes: {', '.join(plan['changes'])}",
        )
        results.append(item)

    return {
        "status": "success" if counts["failed"] == 0 else "partial",
        **counts,
        "results": results,
    }


@app.post("/api/tagger/repair-series")
async def api_tagger_repair_series(payload: Dict[str, Any]):
    """Explicitly repair the ``<Series>`` value of user-selected staged CBZ files.

    There is no bulk/"all files" mode: ``files`` must name every archive to touch
    (max 200 per request).  Only files the audit proves to belong to exactly one
    known series are rewritten; ``ambiguous``/``unknown_series``/``untagged``
    files are reported and skipped, never guessed at.  The destructive half sits
    behind the normal auth middleware, and every rewrite is logged with the
    authenticated actor.
    """
    model = _validated_payload(schemas.SeriesRepairRequest, payload)
    result = await asyncio.to_thread(
        _repair_staging_cbz_series, model.files, model.repair_writer, model.repair_publisher
    )
    return JSONResponse(result)


# --- Unified search with AniList alias resolution (PROJECT.md feature 17) ---

@app.get("/api/search")
async def api_unified_search(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
):
    """Search series, their AniList alternate titles, and releases in one call.

    A query matches either the stable matching key or an authoritative AniList
    canonical/romaji alias, so either spelling finds the series.  Every series hit
    returns the stable key (``stable_name``) plus the cosmetic ``display_title``,
    and ``match``/``matched_alias`` explain why it matched.
    """
    query = (q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="q is required and must not be blank")
    result = await asyncio.to_thread(search_module.unified_search, db, query, limit)
    return JSONResponse(result)

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

@app.post("/api/downloader/torbox/test")
async def api_downloader_torbox_test(payload: Optional[Dict[str, Any]] = None):
    """Test the Torbox connection (plan/slots).  An unsaved key may be supplied."""
    model = _validated_payload(schemas.TorboxTestRequest, payload or {})
    provided_key = (model.torbox_api_key or "").strip()
    if provided_key and provided_key != schemas.TORBOX_API_KEY_MASK:
        client = TorboxDownloader(api_key=provided_key, enabled=True)
    else:
        client = build_downloader(db, engine="torbox")
    return JSONResponse(client.test_connection())

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


_TORBOX_RETRY_MIN_SECONDS = 5.0
_TORBOX_RETRY_LAST: Dict[int, float] = {}


@app.post("/api/downloads/torbox/{release_id}/retry")
async def api_torbox_retry(release_id: int):
    """Re-submit a failed Torbox release with the normal strict selection rules."""
    target = db.get_release_by_id(release_id)
    if not target:
        raise HTTPException(status_code=404, detail="Release not found")
    if resolve_downloader_engine(db) != "torbox":
        raise HTTPException(status_code=409, detail="Torbox is not the active downloader engine")
    now = time.time()
    last = _TORBOX_RETRY_LAST.get(release_id, 0.0)
    if now - last < _TORBOX_RETRY_MIN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"Retry rate-limited; wait {_TORBOX_RETRY_MIN_SECONDS:g}s between retries.",
        )
    _TORBOX_RETRY_LAST[release_id] = now

    # Drop the stale remote job first (it may still hold an active slot), then reset.
    old_handle = scheduler._torbox_handle_for_release(release_id)
    if old_handle:
        try:
            await asyncio.to_thread(build_downloader(db, engine="torbox").cancel, old_handle)
        except Exception as exc:
            logger.debug("Could not cancel stale Torbox job on retry: %s", exc)
    db.clear_download_error(release_id)
    db.update_downloader_state(release_id, "IDLE", "torbox")
    missing = await asyncio.to_thread(scheduler.resolve_release_missing_volumes, target)
    result = await asyncio.to_thread(
        scheduler.submit_torbox_download,
        release_id,
        str(target.get("title") or ""),
        str(target.get("download_url") or target.get("link") or ""),
        link=str(target.get("link") or ""),
        missing_volumes=missing,
        source="retry",
        guid=str(target.get("guid") or ""),
        torrent_hash=str(target.get("torrent_hash") or ""),
    )
    if result.get("success"):
        db.clear_download_error(release_id)
        if result.get("skipped"):
            return JSONResponse({"status": "skipped", "message": result.get("message") or "Nothing missing"})
        return JSONResponse({
            "status": "success",
            "engine": "torbox",
            "message": result.get("message") or "Torbox retry submitted",
        })
    message = str(result.get("message") or "Torbox rejected the retry")
    db.set_download_error(release_id, f"{message} (retry rejected)")
    db.update_downloader_state(release_id, "FAILED", "torbox")
    return JSONResponse({"status": "error", "message": message})


@app.post("/api/downloads/torbox/{release_id}/remove")
async def api_torbox_remove(release_id: int):
    """Remove a Torbox release's remote job and clear its local failed state."""
    target = db.get_release_by_id(release_id)
    if not target:
        raise HTTPException(status_code=404, detail="Release not found")
    handle = scheduler._torbox_handle_for_release(release_id)
    deleted = False
    if handle:
        try:
            deleted = bool(await asyncio.to_thread(build_downloader(db, engine="torbox").cancel, handle))
        except Exception as exc:
            logger.debug("Could not remove Torbox job for release %s: %s", release_id, exc)
    db.update_downloader_state(release_id, "IDLE", "torbox")
    db.clear_download_error(release_id)
    return JSONResponse({"status": "success" if deleted else "error", "removed": deleted})

@app.post("/api/releases/{release_id}/download")
async def api_release_download(release_id: int):
    """Manually send a release to the configured download engine (qBittorrent or Torbox)."""
    target = db.get_release_by_id(release_id)
    if not target:
        raise HTTPException(status_code=404, detail="Release not found")

    download_url = target.get("download_url") or target.get("link")
    downloads_dir = scheduler.get_downloads_dir()
    # Accurate wanted volumes at every entry point: stored values, else the matcher.
    missing = await asyncio.to_thread(scheduler.resolve_release_missing_volumes, target)
    vols = target.get("volumes") or []

    if resolve_downloader_engine(db) == "torbox":
        result = scheduler.submit_torbox_download(
            release_id,
            str(target.get("title") or ""),
            str(download_url or ""),
            link=str(target.get("link") or ""),
            missing_volumes=[float(v) for v in (missing or [])],
            source="manual",
            guid=str(target.get("guid") or ""),
            torrent_hash=str(target.get("torrent_hash") or ""),
        )
        if result.get("success"):
            if result.get("skipped"):
                return JSONResponse({
                    "status": "skipped",
                    "engine": "torbox",
                    "message": result.get("message") or "Nothing missing; Torbox submit skipped",
                })
            db.log_activity("MANUAL_DOWNLOAD", f"Sent to Torbox: '{target['title']}'", "Engine: torbox")
            await asyncio.to_thread(scheduler.process_downloads_to_staging, None)
            return JSONResponse({
                "status": "success",
                "engine": "torbox",
                "cached": bool(result.get("cached")),
                "message": "Sent to Torbox successfully",
            })
        return JSONResponse(
            {"status": "error", "engine": "torbox", "message": result.get("message") or "Torbox rejected the release"},
            status_code=500,
        )

    qbit = scheduler.get_qbit_client()

    success = qbit.add_torrent(
        url_or_magnet=download_url,
        savepath=downloads_dir,
        category="manga",
        tags="mangatrax"
    )

    if success:
        torrent_h = qbit.extract_torrent_hash(download_url)
        db.update_download_handle(
            release_id, "qbit", downloader_id=torrent_h or "",
            torrent_hash=torrent_h, wanted_volumes=[float(v) for v in (missing or [])],
        )
        db.log_activity("MANUAL_DOWNLOAD", f"Sent to downloader: '{target['title']}'", f"URL: {download_url[:60]}...")

        if torrent_h and target.get("is_bundle") and missing and len(missing) < len(vols):
            sel_res = qbit.apply_selective_volume_download(torrent_h, [float(v) for v in missing])
            if sel_res.get("status") == "filtered":
                db.log_activity(
                    "SELECTIVE_DOWNLOAD",
                    f"Selective download applied for '{target['title']}': skipped {len(sel_res.get('skipped_volumes', []))} owned volumes; downloading {len(sel_res.get('kept_volumes', []))} missing volumes."
                )

        await asyncio.to_thread(scheduler.process_downloads_to_staging, None)
        return JSONResponse({"status": "success", "engine": "qbit", "message": "Sent to downloader successfully"})
    else:
        return JSONResponse({"status": "error", "engine": "qbit", "message": "Failed to add release to downloader"}, status_code=500)

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
    """Refresh one series in the background: metadata, Calibre info, then Nyaa crawl."""
    model = _validated_payload(schemas.SeriesBackfillRequest, payload or {})
    series_list = db.get_all_series()
    target = next((s for s in series_list if s["id"] == series_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Series not found")

    res = scheduler.run_series_backfill_async(
        target["name"], max_pages=model.max_pages, actor=auth_module.get_current_actor(),
    )
    return JSONResponse(res)

@app.post("/api/series/backfill")
async def api_series_backfill_by_name(payload: Dict[str, Any]):
    """Refresh one series by name in the background: metadata, Calibre info, then Nyaa crawl."""
    model = _validated_payload(schemas.SeriesBackfillByNameRequest, payload)
    res = scheduler.run_series_backfill_async(
        model.series_name, max_pages=model.max_pages, actor=auth_module.get_current_actor(),
    )
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

@app.get("/api/settings")
async def api_get_settings():
    """Return settings with secrets masked (the Torbox API key is never echoed)."""
    settings = db.get_all_settings()
    if str(settings.get("torbox_api_key", "") or "").strip():
        settings["torbox_api_key"] = schemas.TORBOX_API_KEY_MASK
    return JSONResponse({"status": "success", "settings": settings})

@app.post("/api/settings")
async def api_save_settings(payload: Dict[str, Any]):
    """Save application settings (strict key whitelist; unknown keys rejected)."""
    model = _validated_payload(schemas.SettingsUpdateRequest, payload)
    provided = model.model_dump(exclude_unset=True, exclude_none=True) if hasattr(model, "model_dump") else {}
    operator_roots = _operator_allowed_path_roots()
    if operator_roots:
        for key in ("calibre_path", "staging_dir", "downloads_dir"):
            if key in provided:
                try:
                    security.ensure_path_within_roots(str(provided[key]), operator_roots)
                except security.UnsafePathError as exc:
                    raise HTTPException(status_code=400, detail=f"{key}: {exc}") from exc
    # task-22: downloads_dir holds raw downloads and must not be the staging folder.
    if "downloads_dir" in provided:
        staging_value = str(provided.get("staging_dir") or db.get_setting("staging_dir", "/staging"))
        try:
            staging_real = os.path.realpath(security.validate_directory_setting(
                staging_value, setting_name="staging_dir"))
            downloads_real = os.path.realpath(str(provided["downloads_dir"]))
        except security.UnsafePathError as exc:
            raise HTTPException(status_code=400, detail=f"downloads_dir: {exc}") from exc
        if os.path.normcase(staging_real) == os.path.normcase(downloads_real):
            raise HTTPException(
                status_code=400,
                detail="downloads_dir must differ from staging_dir (raw downloads vs processed files)",
            )
    # Never persist the masked placeholder back over a real Torbox key (and an empty
    # value from the UI means "unchanged", not "clear").
    if "torbox_api_key" in provided:
        candidate = str(provided["torbox_api_key"] or "").strip()
        if not candidate or candidate == schemas.TORBOX_API_KEY_MASK:
            provided.pop("torbox_api_key", None)
    for k, v in provided.items():
        db.set_setting(k, str(v))
    if "nyaa_base_url" in provided:
        # Mirror/proxy support: repoint the built-in seeded feed at the new base
        # (only the exact stock row; user-created feeds are never touched).
        migrated = db.migrate_seeded_nyaa_feed(str(provided["nyaa_base_url"]))
        if migrated:
            db.log_activity(
                "NYAA_BASE_UPDATED",
                f"Nyaa base URL set to {provided['nyaa_base_url']}; repointed {migrated} built-in feed(s).",
            )
    db.log_activity("SETTINGS_UPDATED", "Application settings updated")
    storage = {}
    if "staging_dir" in provided or "downloads_dir" in provided:
        try:
            storage = _refresh_storage_health("settings")
        except Exception as exc:  # pragma: no cover - save must succeed regardless
            logger.debug("Storage probe after settings save failed: %s", exc)
        try:
            await asyncio.to_thread(scheduler.periodic_downloads_inventory_and_route)
        except Exception as exc:  # pragma: no cover - save must succeed regardless
            logger.debug("Routing/inventory pass after settings save failed: %s", exc)
    return JSONResponse({"status": "success", "storage": storage})

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

@app.post("/api/maintenance/clear")
async def api_maintenance_clear(payload: Dict[str, Any]):
    """Safe maintenance/clear actions (Settings -> Maintenance).

    These are re-fetchable cache/discovery cleanups, so they are available by
    default: they are deliberately NOT gated by ``MANGATRAX_DEBUG_ACTIONS``.  The
    global authentication middleware and the CSRF Origin/Referer check still apply
    exactly as for every other mutating route, and the strict
    ``MaintenanceClearRequest`` model (extra="forbid", closed action enum) prevents
    a destructive action name from ever reaching this handler.
    """
    model = _validated_payload(schemas.MaintenanceClearRequest, payload)
    try:
        result = maintenance.perform(model.action, db, cover_cache)
    except maintenance.UnknownMaintenanceAction as exc:  # unreachable via the enum
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


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
    elif action == "purge-non-manga":
        # D1: the explicit, opt-in counterpart of the daily re-typing pass.  Startup
        # only *types* non-manga rows (never deletes); a user who genuinely wants them
        # gone asks for it here.  Rows typed manga/unknown are never touched.
        count = db.purge_non_manga_releases()
        return JSONResponse({"status": "success", "message": f"Purged {count} non-manga release row(s) (manga and unknown rows were kept)."})
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
