import os
import re
import json
import math
import difflib
import time
import threading
import asyncio
import contextvars
import logging
import logging.handlers
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.database import MangaDatabase
from app.database import release_dedupe_signature
from app.calibre.scanner import CalibreMangaScanner
from app.providers.registry import create_provider
from app.scheduler import MangaScheduler
from app.covers import CoverCache
from app.downloader.qbit import QBitClient
from app import archive_conversion, background_tasks, download_failures, ln_releases, downloads_inventory, downloads_inventory_cleanup, downloads_reconcile, false_series_matches, network_diagnostics, staging_duplicates, staging_groups, storage_health
from app.downloader import as_bool, build_downloader, resolve_downloader_engine
from app.downloader.base import DownloaderError, parse_queued_handle_id
from app.downloader.torbox import TorboxDownloader
from app.tagger import cbz_tagger, series_audit
from app.tagger import pipeline as downloads_pipeline
from app.tagger.cbz_tagger import has_comicinfo
from app.metadata.anilist import parse_anilist_id
from app.metadata.mangaupdates import MangaUpdatesService
from app.parser import (
    clean_series_for_matching,
    extract_manga_bundle_and_volumes,
    normalize_text,
    series_name_candidates,
)
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


def _invalidate_torbox_status_cache() -> None:
    """Drop the last-good status payload so a queue change is visible immediately.

    Without this, the 1s status cache can keep serving a payload that still contains a
    row the user just removed, which re-renders the removed item for a moment (and
    indefinitely if live rebuilds keep failing while the stale payload is reused).
    """
    with _TORBOX_STATUS_LOCK:
        _TORBOX_STATUS_CACHE.update({"payload": None, "at": 0.0, "building": False})

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

_FILE_LOG_HANDLER_NAME = "mangatrax-file"


def _file_log_path() -> str:
    return os.path.join(str(DATA_DIR), "logs", "mangatrax.log")


def _file_logging_info() -> Dict[str, Any]:
    path = _file_log_path()
    handler = next(
        (h for h in logging.getLogger().handlers if getattr(h, "name", "") == _FILE_LOG_HANDLER_NAME),
        None,
    )
    try:
        size = os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        size = 0
    return {
        "enabled": handler is not None,
        "path": path,
        "size_bytes": size,
        "max_mb": db.get_setting("log_file_max_mb", "10"),
        "backups": db.get_setting("log_file_backups", "5"),
    }


def configure_file_logging(db_handle=None) -> Dict[str, Any]:
    """Attach exactly one rotating file handler when log_to_file=true (idempotent)."""
    db_handle = db_handle or db
    try:
        enabled = as_bool(db_handle.get_setting("log_to_file", "false"), default=False)
        root = logging.getLogger()
        existing = next(
            (h for h in root.handlers if getattr(h, "name", "") == _FILE_LOG_HANDLER_NAME),
            None,
        )
        if not enabled:
            if existing is not None:
                try:
                    root.removeHandler(existing)
                    existing.close()
                except Exception:
                    pass
            return _file_logging_info()
        try:
            max_mb = max(1, min(int(db_handle.get_setting("log_file_max_mb", "10") or 10), 100))
        except (TypeError, ValueError):
            max_mb = 10
        try:
            backups = max(1, min(int(db_handle.get_setting("log_file_backups", "5") or 5), 20))
        except (TypeError, ValueError):
            backups = 5
        path = _file_log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError as exc:
            logger.warning("Could not create log directory '%s': %s; file logging disabled.", os.path.dirname(path), exc)
            return _file_logging_info()
        if existing is not None:
            root.removeHandler(existing)
            try:
                existing.close()
            except Exception:
                pass
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_mb * 1024 * 1024, backupCount=backups, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        handler.setLevel(logging.INFO)
        handler.name = _FILE_LOG_HANDLER_NAME
        root.addHandler(handler)
        logger.info(
            "File logging enabled: %s (max %s MB, %s backups; total ≈ %s MB).",
            path, max_mb, backups, max_mb * (backups + 1),
        )
    except Exception as exc:  # pragma: no cover - logging must never break startup
        logger.warning("Could not configure file logging: %s", exc)
    return _file_logging_info()


configure_file_logging(db)


def _activity_logs_health() -> Dict[str, Any]:
    stats = db.activity_log_stats()
    try:
        oldest_age_days = None
        if stats.get("oldest_created_at"):
            oldest = datetime.strptime(str(stats["oldest_created_at"]), "%Y-%m-%d %H:%M:%S")
            oldest_age_days = round((datetime.utcnow() - oldest).total_seconds() / 86400.0, 2)
    except Exception:
        oldest_age_days = None
    try:
        db_size = os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0
    except OSError:
        db_size = 0
    wal_path = DB_PATH + "-wal"
    try:
        wal_size = os.path.getsize(wal_path) if os.path.isfile(wal_path) else 0
    except OSError:
        wal_size = 0
    return {
        "activity_rows": stats.get("count", 0),
        "oldest_created_at": stats.get("oldest_created_at", ""),
        "oldest_age_days": oldest_age_days,
        "db_size_bytes": db_size,
        "wal_size_bytes": wal_size,
        "retention_days": db.get_setting("activity_retention_days", "90"),
        "max_rows": db.get_setting("activity_max_rows", "50000"),
        "file_log": _file_logging_info(),
    }

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
    identity_token = auth_module.set_current_identity(decision.identity)
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
        auth_module.reset_current_identity(identity_token)
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
try:
    from app.tag_display import display_tags as _display_tags
    templates.env.globals["tag_display"] = _display_tags
except Exception:  # pragma: no cover - E2E shim has no Jinja environment
    pass

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
_register_template_global(
    "is_admin", lambda: AUTH.is_admin(auth_module.get_current_identity())
)

def require_admin() -> None:
    """FastAPI dependency: Admin role required for sensitive surfaces.

    ``none``/``basic`` deployments are single-operator and stay admin; an OIDC
    user without a configured admin group gets a JSON 403.
    """
    if not AUTH.config.auth_enabled:
        return
    if not AUTH.is_admin(auth_module.get_current_identity()):
        raise HTTPException(status_code=403, detail="Admin role required")
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


_CURRENT_USER_CACHE: "contextvars.ContextVar[Optional[tuple]]" = contextvars.ContextVar(
    "mangatrax_current_user", default=None
)


def _current_user_row() -> Optional[Dict[str, Any]]:
    """The ``users`` row for the authenticated request identity (memoised per request)."""
    identity = auth_module.get_current_identity()
    if identity is None:
        return None
    key = f"{identity.auth_source}:{identity.subject}"
    cached = _CURRENT_USER_CACHE.get()
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        row = db.get_user(identity.subject)
    except Exception as exc:  # pragma: no cover - identity must never break a request
        logger.debug("Could not resolve current user row: %s", exc)
        row = None
    if row is not None and row.get("is_admin"):
        # First admin login on an upgraded deployment inherits the existing
        # single-user data exactly once (memberships/blacklist/groups).
        try:
            if not str(db.get_setting("multi_user_legacy_claimed_by", "") or "").strip():
                counts = db.claim_legacy_data_for_admin(int(row["id"]))
                logger.info("Legacy data claimed by admin '%s': %s", row.get("username"), counts)
        except Exception as exc:  # pragma: no cover - migration must never break auth
            logger.warning("Could not claim legacy data for admin: %s", exc)
    _CURRENT_USER_CACHE.set((key, row))
    return row


def _current_user_id() -> Optional[int]:
    row = _current_user_row()
    return int(row["id"]) if row else None


def _staging_root_setting() -> str:
    return db.get_setting("staging_dir", "/staging")


def _user_staging_dir(user: Dict[str, Any]) -> str:
    """``<staging>/<folder>`` for one user, created on demand."""
    root = _staging_root_setting()
    folder = db.ensure_user_staging_folder(int(user["id"]))
    path = os.path.join(root, folder)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:  # pragma: no cover - permissions surface via storage health
        logger.warning("Could not create staging folder '%s': %s", path, exc)
    return path


def _staging_dir_setting() -> str:
    """Request-scoped staging directory.

    Authenticated non-admins get their private ``staging/<folder>``; admins (and
    the legacy single-operator ``none`` mode) get the staging root, whose scans
    recurse into every user folder so the "in staging" state stays global.
    """
    user = _current_user_row()
    if user is None or user.get("is_admin"):
        return _staging_root_setting()
    return _user_staging_dir(user)


def _storage_health_paths() -> Dict[str, str]:
    """The two pipeline mounts probed for writability."""
    return {
        "downloads": scheduler.get_downloads_dir(create=False),
        "staging": _staging_root_setting(),
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


def _staging_owner_labels() -> Dict[str, str]:
    """Registered user staging-folder names mapped to human labels.

    The acting admin's own folder is deliberately left out: the admin scans the
    root, so their files (root + ``staging/<their-folder>/``) form the single
    "Mine / shared" bucket instead of being split across two owners.
    """
    labels: Dict[str, str] = {}
    try:
        users = db.list_users()
    except Exception:
        users = []
    current = _current_user_row()
    own_folder = ""
    if current is not None and current.get("is_admin"):
        own_folder = str(current.get("staging_folder") or "").strip().lower()
    for user in users:
        folder = str(user.get("staging_folder") or "").strip().lower()
        if not folder or folder == own_folder:
            continue
        labels[folder] = str(
            user.get("display_name") or user.get("username") or folder
        ).strip() or folder
    return labels


def _staging_owner_options(include_all: bool = True) -> list:
    """Dropdown options for the staging owner filter (empty for single-folder views).

    Only an admin (or the legacy no-login operator) scans the shared staging root;
    a normal user sees just their own folder, so no filter is offered.
    """
    if not include_all:
        return []
    labels = _staging_owner_labels()
    if not labels:
        return []
    # "Mine" is the default view: the admin's own feed stays separate from the
    # other users' downloads; "All owners" is the opt-in overview.
    options = [{"value": "", "label": "Mine / shared"}]
    options.append({"value": "all", "label": "All owners"})
    for folder in sorted(labels):
        options.append({"value": folder, "label": labels[folder]})
    return options


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
        "eta_seconds": int(eta_seconds) if eta_seconds is not None else 0,
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
    if local_phase in ("fetching", "routing", "processing") and db_status != "FAILED":
        state = local_phase
    files = info.get("files") or []
    saved = release.get("downloader_id") or ""
    # Normalise every numeric field up front: templates must never do arithmetic on
    # None (task-36 hotfix).  Local fetch progress wins while phase == fetching; the
    # live Torbox values are used for everything else and only when valid.
    progress = float(info.get("progress") or 0.0)
    size = int(info.get("size") or 0)
    local_total = int(progress_data.get("total_bytes") or 0) or size
    local_downloaded = int(progress_data.get("downloaded_bytes") or 0)
    if local_phase == "fetching" and local_total:
        progress = local_downloaded / local_total
    progress = max(0.0, min(1.0, progress))
    local_speed = int(progress_data.get("speed_bps") or 0)
    live_speed = int(info.get("download_speed") or 0)
    live_eta_raw = info.get("eta")
    live_eta = 0
    if isinstance(live_eta_raw, (int, float)):
        try:
            live_eta = max(0, int(live_eta_raw))
        except (TypeError, ValueError):
            live_eta = 0
    local_eta = None
    if local_phase == "fetching" and local_speed > 0 and local_total > local_downloaded:
        local_eta = max(0, int((local_total - local_downloaded) / local_speed))
    if local_phase == "fetching":
        speed = local_speed or live_speed
        eta = int(local_eta if local_eta is not None else live_eta)
    else:
        speed = live_speed
        eta = live_eta
    downloaded_bytes = local_downloaded if local_phase == "fetching" else int(round(size * progress))
    total_bytes = int(local_total or size or 0)
    speed_bps = int(local_speed if local_phase == "fetching" else live_speed)
    eta = int(eta or 0)
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
    strict_hint = ""
    error_lower = error.lower()
    if failed and "strict volume selection" in error_lower:
        if wanted_volumes and all(float(v) >= 100 for v in wanted_volumes):
            strict_hint = (
                "These look like chapter numbers stored as volumes (e.g. 205-215). Run Settings → "
                "Maintenance → Suspicious volume ranges (Audit, then Repair selected), then Retry."
            )
        elif "absent from the torrent" in error_lower:
            strict_hint = (
                "This torrent does not contain the volume(s) MangaTrax still needs. Pick another release, "
                "or disable Settings → Torbox → strict volume selection to allow whole-torrent downloads."
            )
        elif "cannot be mapped" in error_lower:
            strict_hint = (
                "The torrent's file names could not be matched to volumes. Disable Settings → Torbox → "
                "strict volume selection to allow a whole-torrent download, or pick another release."
            )
        elif "no file list" in error_lower:
            strict_hint = (
                "Torbox returned no file list, so strict selection cannot verify the volumes. Remove this "
                "item and pick another release."
            )
    return {
        "hash": str(release.get("torrent_hash") or saved),
        "name": str(release.get("title") or ""),
        "state": state,
        "failed": failed,
        "error": error,
        "strict_hint": strict_hint,
        "can_retry": can_retry,
        "attempts": int(release.get("download_attempts") or 0),
        "download_status": db_status,
        "phase": local_phase or ("done" if db_status == "COMPLETED" else "ready" if state == "ready" else ""),
        "progress_pct": progress_pct,
        "downloaded_bytes": downloaded_bytes,
        "total_bytes": total_bytes,
        "speed_bps": speed_bps,
        "eta_seconds": eta,
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
        "downloaded_bytes": downloaded_bytes,
        "download_speed": speed,
        "upload_speed": 0,
        "eta_seconds": eta,
        "ratio": 0.0,
        "seeds": int(info.get("seeds") or 0),
        "is_active": (not failed) and (
            state in ("queued", "queued_slot", "downloading", "stalled", "fetching", "routing", "processing")
            or local_phase in ("ready", "fetching", "routing")
        ),
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
        payload["logs"] = _activity_logs_health()
    except Exception as exc:  # pragma: no cover - diagnostics must never fail /health
        payload["logs"] = {"error": f"{type(exc).__name__}: {exc}"}
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

@app.get("/api/whoami")
async def api_whoami():
    """What the IdP actually delivered for this session (never secret material).

    Diagnoses role problems: if ``groups`` is empty, the client's ``groups``
    scope or the IdP claim configuration is wrong, not MangaTrax's role mapping.
    """
    identity = auth_module.get_current_identity()
    return JSONResponse({
        "auth_mode": AUTH_CONFIG.mode,
        "subject": identity.subject if identity else None,
        "username": identity.username if identity else None,
        "groups": list(identity.groups) if identity else [],
        "groups_claim": AUTH_CONFIG.oidc_groups_claim,
        "admin_groups": list(AUTH_CONFIG.admin_groups),
        "is_admin": AUTH.is_admin(identity),
    })


# --- HTML UI Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    stats = db.get_dashboard_stats()
    calibre_path = db.get_setting("calibre_path", CALIBRE_DIR)
    scanner = CalibreMangaScanner(calibre_path)
    calibre_status = scanner.get_library_summary()
    pending_requests = db.get_pending_requests(limit=10, user_id=_current_user_id())
    recent_logs = db.get_logs(limit=10)

    # Live downloader snapshot behind a short timeout, off the event loop, so a
    # hung/unreachable qBittorrent can never stall the dashboard render.
    download_status = await asyncio.to_thread(_build_downloader_status, 1.0)
    torrents = download_status.get("torrents") or []
    try:
        manual_pickup_count = await asyncio.to_thread(db.count_active_manual_pickups)
    except Exception:
        manual_pickup_count = 0

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
async def releases_view(request: Request, status: Optional[str] = None, view: str = Query("series"), sort: str = Query("updated")):
    # Instant page response: page shell renders in <10ms and lazy loads series dynamically.
    # The total is rendered up front so the Load More control exists from first paint
    # (disabled while the first batch loads) instead of appearing after a round-trip.
    try:
        initial_total = (
            db.count_releases(status=status)
            if view == "flat"
            else db.count_releases_grouped_by_series(status=status)
        )
    except Exception:
        initial_total = 0
    return templates.TemplateResponse(
        request=request,
        name="releases.html",
        context={
            "active_page": "releases",
            "releases": [],
            "grouped_series": [],
            "current_status": status,
            "current_view": view,
            "initial_total": initial_total,
            "sort": sort if sort in ("post_date", "updated", "series") else "updated",
        }
    )

@app.get("/prioritized", response_class=HTMLResponse)
async def prioritized_view(request: Request, status: Optional[str] = None, view: str = Query("series")):
    user_id = _current_user_id()
    prioritized_groups = (
        db.get_user_prioritized_groups(user_id) if user_id is not None else db.get_prioritized_groups()
    )
    try:
        initial_total = (
            db.count_releases_grouped_by_series(status=status, prioritized_only=(view != "flat"))
            if view != "flat"
            else 0
        )
    except Exception:
        initial_total = 0
    return templates.TemplateResponse(
        request=request,
        name="prioritized.html",
        context={
            "active_page": "prioritized",
            "prioritized_groups": prioritized_groups,
            "releases": [],
            "grouped_series": [],
            "current_status": status,
            "current_view": view,
            "initial_total": initial_total,
        }
    )

# --- Background tasks (task-47) ---
@app.get("/api/tasks")
async def api_tasks(active: int = Query(0, ge=0, le=1), kind: Optional[str] = None,
                    limit: int = Query(50, ge=1, le=200)):
    tasks = await asyncio.to_thread(
        db.list_background_tasks, bool(active), kind, limit,
    )
    return JSONResponse({
        "status": "success",
        "active_count": len(db.list_background_tasks(active_only=True, limit=200)),
        "tasks": [background_tasks.serialize_task(task) for task in tasks],
    })


@app.get("/api/tasks/{task_id}")
async def api_task_detail(task_id: int):
    task = await asyncio.to_thread(db.get_background_task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return JSONResponse({"status": "success", "task": background_tasks.serialize_task(task)})


@app.post("/api/tasks/{task_id}/cancel")
async def api_task_cancel(task_id: int):
    task = await asyncio.to_thread(db.get_background_task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not background_tasks.cancel_supported(task_id):
        raise HTTPException(status_code=409, detail="This task does not support cancellation")
    background_tasks.request_cancel(db, task_id)
    return JSONResponse({"status": "success", "task_id": task_id, "cancelling": True})


def _recorded_task(kind: str, title: str, fn, actor: str = "user",
                   scope: Optional[str] = None) -> tuple:
    """Run a quick/moderate action with persisted progress (foreground compatibility).

    Long network actions use ``background_tasks.run_in_background`` instead; quick
    maintenance actions still record a task so the global UI and reload-reattachment
    have consistent state.
    """
    task_id = db.create_background_task(kind, title, scope=scope, actor=actor)
    reporter = background_tasks.TaskReporter(db, task_id, None)
    db.log_activity("BACKGROUND_TASK_STARTED", f"Task #{task_id}: {title}", kind)
    try:
        result = fn(reporter)
        if isinstance(result, dict):
            counts = ", ".join(
                f"{key}: {value}" for key, value in result.items()
                if isinstance(value, int) and not isinstance(value, bool)
            )
            if counts:
                current = db.get_background_task(task_id) or {}
                if int(current.get("items_total") or 0) > 0:
                    reporter.report(message=f"Completed — {counts}", progress_pct=100.0, force=True)
                else:
                    reporter.report(1, 1, f"Completed — {counts}", progress_pct=100.0, force=True)
        db.finish_background_task(task_id, "success", result=result)
        db.log_activity("BACKGROUND_TASK_FINISHED", f"Task #{task_id} finished", json.dumps(result or {}, default=str)[:500])
        return result, background_tasks.serialize_task(db.get_background_task(task_id))
    except Exception as exc:
        db.finish_background_task(task_id, "failed", f"{type(exc).__name__}: {exc}")
        db.log_activity("BACKGROUND_TASK_FINISHED", f"Task #{task_id} failed", str(exc)[:500])
        raise


# --- Lazy Loading API Endpoints ---
_SERIES_CARD_RELEASE_CAP = 10

_RELEASE_STATE_LABELS = {
    "downloading": "Downloading",
    "downloaded": "Downloaded (awaiting staging)",
    "in_staging": "In staging",
    "in_calibre": "In Calibre",
    "failed": "Failed",
    "waiting_inflight": "Waiting for in-flight download",
    "not_downloaded": "Not downloaded",
}


def release_download_state(release: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Derived download/staging/Calibre state for a release row (task-46)."""
    release = release or {}
    download_status = str(release.get("download_status") or "").upper()
    release_status = str(release.get("status") or "").upper()
    progress: Dict[str, Any] = {}
    try:
        payload = json.loads(release.get("torbox_progress_json") or "{}")
        if isinstance(payload, dict):
            progress = payload
    except (TypeError, ValueError):
        progress = {}
    progress_pct = progress.get("progress_pct")
    if progress_pct is None:
        try:
            progress_pct = round(float(progress.get("downloaded_bytes") or 0)
                                 / max(1.0, float(progress.get("total_bytes") or 0)) * 100.0, 1)
        except (TypeError, ValueError):
            progress_pct = 0.0
    speed_bps = int(progress.get("speed_bps") or release.get("download_speed") or 0)
    error = str(release.get("download_error") or "").strip()
    if download_status == "FAILED" or release_status == "FAILED":
        state = "failed"
    elif release_status == "IN_CALIBRE":
        state = "in_calibre"
    elif release_status == "IN_STAGING":
        state = "in_staging"
    elif download_status == "COMPLETED" and release.get("routed_at"):
        state = "in_staging"
    elif download_status in ("DOWNLOADING", "QUEUED"):
        state = "downloading"
    elif download_status == "PENDING_DEDUPE":
        state = "waiting_inflight"
    elif download_status == "COMPLETED":
        state = "downloaded"
    else:
        state = "not_downloaded"
    return {
        "release_id": release.get("id"),
        "state": state,
        "label": _RELEASE_STATE_LABELS.get(state, "Not downloaded"),
        "progress_pct": round(float(progress_pct or 0.0), 1),
        "speed_bps": speed_bps,
        "error": error,
        "can_download": state in ("not_downloaded", "failed"),
        "can_retry": state == "failed",
        "terminal": state in ("in_staging", "in_calibre"),
    }


def _decorate_release_state(release: Dict[str, Any]) -> Dict[str, Any]:
    release["download_state"] = release_download_state(release)
    return release


def _cap_group_releases(groups: list, cap: int = _SERIES_CARD_RELEASE_CAP) -> list:
    for group in groups or []:
        releases = list(group.get("releases") or [])
        group["release_total"] = len(releases)
        group["releases_shown"] = min(cap, len(releases))
        group["releases"] = [_decorate_release_state(r) for r in releases[:cap]]
    return groups


@app.get("/api/releases/cards")
async def api_releases_cards(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(8, ge=1, le=50),
    search: Optional[str] = Query(None),
    sort: str = Query("updated")
):
    clean_s = search.strip() if search else None
    clean_sort = sort if sort in ("post_date", "updated", "series") else "updated"
    groups = _cap_group_releases(
        db.get_releases_grouped_by_series(status=status, limit=limit, offset=offset, search=clean_s, sort=clean_sort)
    )
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
    search: Optional[str] = Query(None),
    sort: str = Query("updated")
):
    clean_s = search.strip() if search else None
    clean_sort = sort if sort in ("post_date", "updated", "series") else "updated"
    groups = _cap_group_releases(
        db.get_releases_grouped_by_series(status=status, prioritized_only=True, limit=limit, offset=offset, search=clean_s, sort=clean_sort)
    )
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
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    sort: str = Query("updated")
):
    clean_search = search.strip() if search else None
    clean_sort = sort if sort in ("post_date", "updated", "series") else "updated"
    user_id = _current_user_id()
    releases = db.get_releases(
        status=status, limit=limit, offset=offset, search=clean_search,
        sort=clean_sort, user_id=user_id,
    )
    total = db.count_releases(status=status, search=clean_search, user_id=user_id)
    seen_sigs = set()
    deduped = []
    for r in releases:
        # Dedupe identity uses the SORTED group set so "(1r0n, Ushi)" and
        # "[1r0n] [Ushi]" are the same release regardless of tag style/order.
        sig = release_dedupe_signature(r)
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped.append(r)
    has_more = (offset + len(releases)) < total
    deduped = [_decorate_release_state(r) for r in deduped]
    html = templates.get_template("partials/flat_release_row_chunk.html").render(
        {"request": request, "releases": deduped}
    )
    return JSONResponse({
        "status": "success",
        "offset": offset,
        "count": len(deduped),
        "total": total,
        "has_more": has_more,
        "html": html
    })


@app.get("/api/releases/series_releases")
async def api_series_releases(
    request: Request,
    series_key: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(_SERIES_CARD_RELEASE_CAP, ge=1, le=50),
    status: Optional[str] = None,
):
    groups = db.get_releases_grouped_by_series(status=status, search=series_key)
    group = None
    target_norm = (series_key or "").strip().lower()
    for candidate in groups or []:
        if (candidate.get("series_key") or "").strip().lower() == target_norm or (
            candidate.get("series_name") or ""
        ).strip().lower() == target_norm:
            group = candidate
            break
    releases = list((group or {}).get("releases") or [])
    total = len(releases)
    page = [_decorate_release_state(r) for r in releases[offset:offset + limit]]
    html = templates.get_template("partials/series_release_rows_chunk.html").render(
        {"request": request, "releases": page}
    )
    return JSONResponse({
        "status": "success",
        "offset": offset,
        "count": len(page),
        "total": total,
        "has_more": (offset + len(page)) < total,
        "html": html,
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
    user_id = _current_user_id()
    series_list = db.get_all_series(
        monitored_only=monitored_only, limit=limit, offset=offset, search=clean_s,
        user_id=user_id,
    )
    total = db.count_all_series(monitored_only=monitored_only, search=clean_s, user_id=user_id)
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

@app.post("/api/maintenance/refresh-tags")
async def api_refresh_provider_tags():
    """Backfill AniList tags onto existing releases/series (persisted task)."""
    info = background_tasks.run_in_background(
        db, "tags:refresh", "Refresh provider tags",
        lambda reporter: scheduler.refresh_provider_tags(
            progress=reporter.report, cancel_check=lambda: reporter.cancelled,
        ),
        actor=auth_module.get_current_actor(), scope="tags", cancellable=True,
    )
    return JSONResponse({"status": "started", **info})


@app.get("/api/tags")
async def api_tags(q: Optional[str] = None, limit: int = Query(20, ge=1, le=50)):
    tags = await asyncio.to_thread(db.get_known_tags, q or "", limit)
    return JSONResponse({"status": "success", "tags": tags, "count": len(tags)})


@app.get("/discovery", response_class=HTMLResponse)
async def discovery_view(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=6, le=120),
    q: Optional[str] = Query(None),
    include_tags: Optional[str] = Query(None),
    exclude_tags: Optional[str] = Query(None),
    tag_match: str = Query("any"),
    sort: str = Query("newest"),
):
    clean_q = q.strip() if q else None
    include_list = [item.strip() for item in (include_tags or "").split(",") if item.strip()]
    exclude_list = [item.strip() for item in (exclude_tags or "").split(",") if item.strip()]
    clean_match = tag_match if tag_match in ("any", "all") else "any"
    clean_sort = sort if sort in ("relevance", "newest", "series", "post_date") else "newest"
    user_id = _current_user_id()
    total_count = db.count_discovery_releases(
        search=clean_q, include_tags=include_list, exclude_tags=exclude_list,
        tag_match=clean_match, user_id=user_id,
    )
    total_pages = max(1, math.ceil(total_count / page_size))
    current_page = min(page, total_pages) if total_count > 0 else 1
    offset = (current_page - 1) * page_size
    discovered = db.get_discovery_releases(
        limit=page_size, offset=offset, search=clean_q,
        include_tags=include_list, exclude_tags=exclude_list,
        tag_match=clean_match, sort=clean_sort, user_id=user_id,
    )
    from app.tag_display import display_tags
    for row in discovered:
        row["tag_display"] = display_tags(row.get("tags_json"), view="discovery")
    blacklisted = (
        db.get_user_blacklist(user_id) if user_id is not None else db.get_blacklisted_series()
    )

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
            "search_query": clean_q or "",
            "include_tags": include_list,
            "exclude_tags": exclude_list,
            "tag_match": clean_match,
            "tag_sort": clean_sort,
        }
    )

@app.get("/downloads/manual-pickup", response_class=HTMLResponse)
async def manual_pickup_view(request: Request):
    pickups = await asyncio.to_thread(db.list_torbox_manual_pickups, False)
    enriched = []
    for pickup in pickups:
        item = dict(pickup)
        try:
            item["files"] = json.loads(item.get("files_json") or "[]")
        except (TypeError, ValueError):
            item["files"] = []
        try:
            item["wanted_volumes"] = json.loads(item.get("wanted_json") or "[]")
        except (TypeError, ValueError):
            item["wanted_volumes"] = []
        try:
            item["release"] = db.get_release_by_id(int(item.get("release_id") or 0)) or {}
        except Exception:
            item["release"] = {}
        item["total_bytes"] = sum(int(file.get("size") or 0) for file in item["files"])
        enriched.append(item)
    settings = db.get_all_settings()
    cap = 0
    try:
        cap = max(0, int(settings.get("torbox_manual_pickup_max_items", "10") or 10))
    except (TypeError, ValueError):
        cap = 10
    try:
        expiry_days = max(0, int(settings.get("torbox_manual_pickup_expiry_days", "7") or 7))
    except (TypeError, ValueError):
        expiry_days = 7
    return templates.TemplateResponse(
        request=request, name="manual_pickup.html",
        context={
            "active_page": "downloads", "pickups": enriched,
            "active_pickup_count": len(enriched), "pickup_cap": cap,
            "pickup_expiry_days": expiry_days,
            "pickup_enabled": str(settings.get("torbox_manual_pickup_enabled", "true")).lower() != "false",
        },
    )


@app.get("/api/torbox/manual-pickups")
async def api_manual_pickups(include_finished: int = Query(0, ge=0, le=1)):
    pickups = await asyncio.to_thread(db.list_torbox_manual_pickups, bool(include_finished))
    result = []
    for pickup in pickups:
        item = dict(pickup)
        try:
            item["files"] = json.loads(item.get("files_json") or "[]")
        except (TypeError, ValueError):
            item["files"] = []
        result.append(item)
    return JSONResponse({"status": "success", "pickups": result,
                         "count": len(result), "active_count": db.count_active_manual_pickups()})


@app.post("/api/torbox/manual-pickups/{pickup_id}/map")
async def api_manual_pickup_map(pickup_id: int, payload: Dict[str, Any]):
    mapping = payload.get("mapping") or payload.get("files") or []
    if not isinstance(mapping, list):
        raise HTTPException(status_code=400, detail="mapping must be a list")
    try:
        result = await asyncio.to_thread(scheduler.update_manual_pickup_mapping, pickup_id, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(result)


@app.post("/api/torbox/manual-pickups/{pickup_id}/volumes")
async def api_manual_pickup_volumes(pickup_id: int, payload: Dict[str, Any]):
    volumes = payload.get("volumes") or payload.get("wanted_volumes") or []
    if not isinstance(volumes, list):
        raise HTTPException(status_code=400, detail="volumes must be a list")
    try:
        result = await asyncio.to_thread(
            scheduler.update_manual_pickup_release_volumes, pickup_id, volumes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@app.post("/api/torbox/manual-pickups/{pickup_id}/fetch")
async def api_manual_pickup_fetch(pickup_id: int, payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    raw_indexes = body.get("file_indexes") or body.get("indexes") or []
    indexes = None
    if not body.get("all"):
        try:
            indexes = [int(value) for value in raw_indexes]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="file_indexes must be integers")
        if not indexes:
            raise HTTPException(status_code=400, detail="Select at least one file or use all=true")
    actor = auth_module.get_current_actor()

    def _worker(reporter):
        reporter.report(0, len(indexes or []), "Fetching manual pickup files…", force=True)
        result = scheduler._fetch_manual_pickup_files(
            pickup_id, indexes, actor=actor, progress=reporter.report,
        )
        reporter.report(
            result.get("fetched", 0), result.get("fetched", 0) + result.get("failed", 0),
            f"Fetched {result.get('fetched', 0)} file(s); failed {result.get('failed', 0)}",
            progress_pct=100.0, force=True,
        )
        return result

    info = background_tasks.run_in_background(
        db, "torbox:pickup:fetch", f"Fetch manual pickup #{pickup_id}", _worker,
        actor=actor, scope=str(pickup_id),
    )
    return JSONResponse({"status": "started", **info})


@app.post("/api/torbox/manual-pickups/{pickup_id}/retry")
async def api_manual_pickup_retry(pickup_id: int, payload: Optional[Dict[str, Any]] = None):
    actor = auth_module.get_current_actor()

    def _worker(reporter):
        reporter.report(0, 1, "Retrying strict volume mapping…", force=True)
        result = scheduler.retry_manual_pickup_strict(
            pickup_id, actor=actor, progress=reporter.report,
        )
        reporter.report(
            result.get("fetched", 0), result.get("fetched", 0) + result.get("failed", 0),
            f"Strict retry fetched {result.get('fetched', 0)} file(s)",
            progress_pct=100.0, force=True,
        )
        return result

    info = background_tasks.run_in_background(
        db, "torbox:pickup:retry", f"Retry manual pickup #{pickup_id}", _worker,
        actor=actor, scope=str(pickup_id),
    )
    return JSONResponse({"status": "started", **info})


@app.post("/api/torbox/manual-pickups/{pickup_id}/discard")
async def api_manual_pickup_discard(pickup_id: int):
    try:
        result = await asyncio.to_thread(
            scheduler.discard_manual_pickup, pickup_id, auth_module.get_current_actor(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(result)


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
    try:
        manual_pickup_count = await asyncio.to_thread(db.count_active_manual_pickups)
    except Exception:
        manual_pickup_count = 0

    # Check files in staging directory
    staging_dir = _staging_dir_setting()
    current_user = _current_user_row()
    admin_scope = current_user is None or bool(current_user.get("is_admin"))
    staging_owner_labels = _staging_owner_labels()
    staging_owner_options = _staging_owner_options(include_all=admin_scope)
    staging_files = []
    staging_groups_data = []
    if os.path.isdir(staging_dir):
        try:
            staging_groups_data = staging_groups.build_staging_groups(
                staging_dir, owner_labels=staging_owner_labels,
            )
        except Exception as exc:
            logger.debug("Could not build staging groups: %s", exc)
        for entry in cbz_tagger.scan_staging_cbz_files(staging_dir):
            staging_files.append({
                "name": entry["relative"],
                # The Delete button posts this staging-relative path; without it the
                # client-side handler has no target and silently returns.
                "relative": entry["relative"],
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
            "staging_groups": staging_groups_data,
            "staging_owner_options": staging_owner_options,
            "staging_owner_labels": staging_owner_labels,
            "awaiting_series": awaiting_series,
            "awaiting_total": awaiting_total,
            "settings": db.get_all_settings(),
            "manual_pickup_count": manual_pickup_count,
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

@app.get("/logs", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
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

@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def admin_view(request: Request):
    """Admin panel: user roles, per-user policy flags and request approvals."""
    users = []
    for row in db.list_users():
        entry = dict(row)
        try:
            entry["groups"] = json.loads(row.get("groups_json") or "[]")
        except (TypeError, ValueError):
            entry["groups"] = []
        users.append(entry)
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "active_page": "admin",
            "users": users,
            "pending_approvals": db.list_pending_approvals() if hasattr(db, "list_pending_approvals") else [],
        }
    )


@app.post("/api/admin/approvals/{approval_id}/approve", dependencies=[Depends(require_admin)])
async def api_approve_release_request(approval_id: int):
    """Approve a pending request; auto-download submits it when the user allows it."""
    result = await asyncio.to_thread(
        db.decide_release_request, approval_id, "approve", auth_module.get_current_actor(),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Pending request not found")
    request = result.get("request") or {}
    requester = await asyncio.to_thread(db.get_user_by_id, request.get("user_id")) or {}
    payload = {
        "status": "success",
        "decision": "approved",
        "request": result,
        "auto_download": bool(requester.get("auto_download")),
        "message": (
            "Approved and sent to the downloader"
            if requester.get("auto_download") else
            "Approved — press Download on the release to start it (auto-download is off for this user)"
        ),
    }
    if requester.get("auto_download"):
        download_response = await api_release_download(int(request["release_id"]))
        raw_body = getattr(download_response, "body", None)
        if raw_body is None:
            raw_body = getattr(download_response, "content", b"{}")
        try:
            payload["download"] = json.loads(raw_body or b"{}")
        except (TypeError, ValueError):
            payload["download"] = {"status": "unknown"}
    return JSONResponse(payload)


@app.post("/api/admin/approvals/{approval_id}/reject", dependencies=[Depends(require_admin)])
async def api_reject_release_request(approval_id: int):
    result = await asyncio.to_thread(
        db.decide_release_request, approval_id, "reject", auth_module.get_current_actor(),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Pending request not found")
    return JSONResponse({"status": "success", "decision": "rejected", "request": result})


@app.get("/api/admin/anilist-failures", dependencies=[Depends(require_admin)])
async def api_admin_anilist_failures():
    """Series whose AniList/MangaUpdates volume lookup failed (manual pin queue)."""
    rows = await asyncio.to_thread(db.list_anilist_failures)
    return JSONResponse({"status": "success", "failures": rows, "count": len(rows)})


@app.post("/api/admin/anilist-failures/{failure_id}/link", dependencies=[Depends(require_admin)])
async def api_admin_link_anilist_failure(
    failure_id: int, payload: Optional[Dict[str, Any]] = None,
):
    """Pin one unresolved series to an exact AniList entry, then retry expansion."""
    body = payload or {}
    failure = await asyncio.to_thread(db.get_anilist_failure, failure_id)
    if not failure:
        raise HTTPException(status_code=404, detail="Failure entry not found")
    ref = body.get("anilist_id", body.get("anilist_url"))
    anilist_id = parse_anilist_id(ref)
    if anilist_id is None:
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="anilist_id, anilist_url or query is required")
        meta = await asyncio.to_thread(scheduler.anilist.resolve_manga_series, query, True)
        anilist_id = parse_anilist_id((meta or {}).get("anilist_id"))
    else:
        meta = await asyncio.to_thread(scheduler.anilist.resolve_by_anilist_id, anilist_id, True)
    if not meta or anilist_id is None:
        raise HTTPException(status_code=404, detail="No AniList entry found for that reference")

    series_name = str(failure.get("series_name") or "").strip()
    tracked = await asyncio.to_thread(db.get_series_by_name, series_name)
    if tracked:
        await asyncio.to_thread(
            db.set_series_anilist_mapping, int(tracked["id"]), anilist_id, meta,
            actor=auth_module.get_current_actor(),
        )
    else:
        await asyncio.to_thread(db.cache_manga_metadata, series_name, meta)
    # Drop the throttle marker for its releases and expand immediately: the pinned
    # metadata (or the cached entry) now carries the volume count.
    await asyncio.to_thread(db.clear_anilist_failure, series_name)
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE releases SET complete_expand_attempted_at = '' "
            "WHERE lower(trim(series_name)) = lower(trim(?));",
            (series_name,),
        )
        conn.commit()
    try:
        expanded = await asyncio.to_thread(
            scheduler.expand_unresolved_complete_releases, None, series_name,
        )
    except Exception as exc:  # pragma: no cover - expansion is best effort
        logger.warning("Expansion after AniList link failed for '%s': %s", series_name, exc)
        expanded = 0
    db.log_activity(
        "ANILIST_FAILURE_LINKED",
        f"Pinned AniList #{anilist_id} for unresolved series '{series_name}'",
        json.dumps({"anilist_id": anilist_id, "failure_id": int(failure_id), "expanded": expanded}),
        actor=auth_module.get_current_actor(),
    )
    return JSONResponse({
        "status": "success",
        "series_name": series_name,
        "anilist_id": anilist_id,
        "canonical_title": str(meta.get("canonical_title") or ""),
        "expanded": int(expanded or 0),
    })


@app.post("/api/admin/anilist-failures/{failure_id}/ignore", dependencies=[Depends(require_admin)])
async def api_admin_ignore_anilist_failure(
    failure_id: int, payload: Optional[Dict[str, Any]] = None,
):
    """Mark an unresolved series as "no volume data expected" (or reopen it).

    Ignored entries stop being retried by the background expansion pass but stay
    listed in a foldout so the admin can still pin an AniList id later.
    """
    body = payload or {}
    ignored = body.get("ignored", True)
    if not isinstance(ignored, bool):
        raise HTTPException(status_code=400, detail="ignored must be a boolean")
    updated = await asyncio.to_thread(db.set_anilist_failure_ignored, failure_id, ignored)
    if not updated:
        raise HTTPException(status_code=404, detail="Failure entry not found")
    db.log_activity(
        "ANILIST_FAILURE_IGNORED" if ignored else "ANILIST_FAILURE_REOPENED",
        f"{'Marked' if ignored else 'Reopened'} unresolved series '{updated.get('series_name')}'"
        + (" as having no volume data" if ignored else ""),
        json.dumps({"failure_id": int(failure_id), "ignored": ignored}),
        actor=auth_module.get_current_actor(),
    )
    return JSONResponse({"status": "success", "failure": updated})


@app.delete("/api/admin/anilist-failures/{failure_id}", dependencies=[Depends(require_admin)])
async def api_admin_dismiss_anilist_failure(failure_id: int):
    failure = await asyncio.to_thread(db.get_anilist_failure, failure_id)
    if not failure:
        raise HTTPException(status_code=404, detail="Failure entry not found")
    await asyncio.to_thread(db.clear_anilist_failure, str(failure.get("series_name") or ""))
    return JSONResponse({"status": "success", "dismissed": True})


@app.get("/api/admin/users", dependencies=[Depends(require_admin)])
async def api_admin_users():
    return JSONResponse({"status": "success", "users": db.list_users()})


@app.post("/api/admin/users/{user_id}/flags", dependencies=[Depends(require_admin)])
async def api_admin_user_flags(user_id: int, payload: Optional[Dict[str, Any]] = None):
    """Admin toggles a user's auto-download / approval-bypass policy."""
    body = payload or {}
    auto_download = body.get("auto_download")
    bypass_approval = body.get("bypass_approval")
    for name, value in (("auto_download", auto_download), ("bypass_approval", bypass_approval)):
        if value is not None and not isinstance(value, bool):
            raise HTTPException(status_code=400, detail=f"{name} must be a boolean")
    updated = db.set_user_flags(
        user_id,
        auto_download=auto_download,
        bypass_approval=bypass_approval,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    db.log_activity(
        "USER_FLAGS_UPDATED",
        f"Admin updated policy flags for '{updated.get('display_name') or updated.get('username') or updated.get('subject')}'",
        json.dumps({"user_id": user_id, "auto_download": auto_download, "bypass_approval": bypass_approval}),
        actor=auth_module.get_current_actor(),
    )
    return JSONResponse({"status": "success", "user": updated})


@app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
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
            "manual_pickup_count": db.count_active_manual_pickups(),
            "prioritized_groups": prioritized_groups,
            "torbox_api_key_set": torbox_key_set,
            "storage_health": _storage_health_payload(),
            "network_diagnostics": network_diagnostics.collect_network_diagnostics(
                settings=db.get_all_settings(),
            ),
            "file_logging": _file_logging_info(),
            "activity_logs_health": _activity_logs_health(),
            "release_cache": db.count_cached_releases(),
            "series_alias_overrides": db.get_series_alias_overrides(),
            "release_series": [
                {"name": row.get("name")}
                for row in (db.get_all_series() or [])
                if row.get("name")
            ],
            "debug_actions_enabled": AUTH_CONFIG.debug_actions
        }
    )

# --- API Endpoints ---

@app.post("/api/calibre/sync", dependencies=[Depends(require_admin)])
async def api_calibre_sync():
    """Trigger manual Calibre library rescan and bundle request resolution.

    Deliberately synchronous: a user who clicks "Sync now" waits for the result.
    The background worker's own sync respects ``scheduler._calibre_sync_lock``, so a
    manual trigger and the startup/first-tick sync can never run concurrently.
    """
    def _sync_worker(reporter):
        reporter.report(0, 0, "Scanning Calibre library…", force=True)
        result = scheduler.sync_calibre_library(progress=reporter.report)
        reporter.report(message="Calibre sync complete", progress_pct=100.0, force=True)
        return result
    res, task = await asyncio.to_thread(
        _recorded_task, "sync:calibre", "Sync Calibre library", _sync_worker,
        auth_module.get_current_actor(), None,
    )
    payload = dict(res or {})
    payload["task_id"] = task["id"]
    payload["task"] = task
    payload.setdefault("status", "success")
    _invalidate_calibre_snapshot()
    return JSONResponse(payload)

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

@app.post("/api/feeds/check", dependencies=[Depends(require_admin)])
async def api_feeds_check():
    """Start a feed poll in the background and return immediately.

    The poll is network-heavy (Nyaa/RSS/HTML fetches) and used to run inline inside
    this async handler, which blocked the event loop: the whole web UI froze for the
    duration of the poll.  It now runs on the scheduler's daemon thread; poll
    ``GET /api/feeds/check/status`` for progress and the result (including per-feed
    failures, so a dead Nyaa lookup no longer looks like a silent success).
    """
    started = scheduler.start_feed_check()

    def _feed_monitor(reporter):
        reporter.report(0, 0, "Polling feeds…", force=True)
        import time as _time
        while True:
            status = scheduler.get_feed_check_status() or {}
            if not status.get("running"):
                break
            reporter.report(0, 0, f"Polling feeds… {status.get('message') or ''}".strip(), force=True)
            _time.sleep(0.75)
        final = scheduler.get_feed_check_status() or {}
        reporter.report(1, 1, "Feed poll complete", progress_pct=100.0, force=True)
        return final
    task_info = background_tasks.run_in_background(
        db, "feeds:poll", "Poll Feeds", _feed_monitor, actor=auth_module.get_current_actor(),
        scope="feeds",
    )
    payload = dict(started or {})
    payload["task_id"] = task_info["task_id"]
    payload["task"] = task_info["task"]
    return JSONResponse(payload)

@app.get("/api/feeds/check/status")
async def api_feeds_check_status():
    """Non-blocking feed-poll status: running flag, timestamps and last result."""
    return JSONResponse(scheduler.get_feed_check_status())

@app.post("/api/feeds/test", dependencies=[Depends(require_admin)])
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

@app.post("/api/feeds", dependencies=[Depends(require_admin)])
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

@app.delete("/api/feeds/{feed_id}", dependencies=[Depends(require_admin)])
async def api_delete_feed(feed_id: int):
    """Delete a feed."""
    db.delete_feed(feed_id)
    return JSONResponse({"status": "success"})

@app.post("/api/feeds/{feed_id}/toggle", dependencies=[Depends(require_admin)])
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

@app.post("/api/staging/tag", dependencies=[Depends(require_admin)])
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


@app.post("/api/downloads/prune-empty", dependencies=[Depends(require_admin)])
async def api_downloads_prune_empty(payload: Optional[Dict[str, Any]] = None):
    """Manually prune EMPTY leftover folders (never rmtree; busy dirs skipped)."""
    body = payload or {}
    include_staging = bool(body.get("include_staging"))
    result, task = await asyncio.to_thread(
        _recorded_task, "downloads:prune", "Prune empty download folders",
        lambda reporter: downloads_pipeline.prune_empty_folders(
            scheduler.get_downloads_dir(create=False), _staging_dir_setting(), db,
            include_staging, "user",
        ),
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({"status": "success", **result, "task_id": task["id"], "task": task})


@app.post("/api/downloads/flatten-staging", dependencies=[Depends(require_admin)])
async def api_downloads_flatten_staging():
    """Move every file in nested staging folders into the flat staging root."""
    result, task = await asyncio.to_thread(
        _recorded_task, "downloads:flatten", "Flatten staging folder",
        lambda reporter: downloads_pipeline.flatten_staging_dir(
            _staging_dir_setting(), db,
            exclude_dirs={
                str(user.get("staging_folder") or "").strip().lower()
                for user in db.list_users()
                if str(user.get("staging_folder") or "").strip()
            },
        ),
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({"status": "success", **result, "task_id": task["id"], "task": task})


@app.post("/api/downloads/scan")
async def api_downloads_scan():
    """Rescan the raw downloads folder and return the fresh inventory."""
    from app import downloads_reconcile

    def _rescan_worker(reporter):
        reporter.report(0, 0, "Scanning downloads folder…", force=True)
        inv = downloads_inventory.refresh_inventory(
            db, scheduler.get_downloads_dir(create=False),
        )
        reporter.report(0, 0, "Reconciling downloaded/staging release files…", force=True)
        rec = downloads_reconcile.reconcile_missing_release_files(
            db, scheduler.get_downloads_dir(create=False), _staging_dir_setting(),
            calibre_series=_calibre_series_snapshot(),
        )
        reporter.report(
            rec["checked"], rec["checked"],
            f"Checked {rec['checked']} release(s); reset {rec['reset']} stale "
            f"({rec['in_calibre']} confirmed in Calibre)",
            progress_pct=100.0, force=True,
        )
        return {"inventory": inv, "reconciled": rec}

    combined, task = await asyncio.to_thread(
        _recorded_task, "downloads:rescan", "Rescan downloads folder", _rescan_worker,
        auth_module.get_current_actor(), None,
    )
    inventory = combined["inventory"]
    reconciled = combined["reconciled"]
    return JSONResponse({
        "status": "success",
        "inventory": inventory,
        "last_scanned_at": inventory.get("scanned_at") or "",
        "entries": inventory.get("entries") or [],
        "reconciled": reconciled,
        "task_id": task["id"],
        "task": task,
    })


@app.get("/api/downloads/auto-download/diagnostics")
async def api_auto_download_diagnostics():
    """Read-only: why auto-download is/isn't firing + recent decisions."""
    data = await asyncio.to_thread(scheduler.get_auto_download_diagnostics)
    return JSONResponse(data)


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
    def _speed_worker(reporter):
        reporter.report(0, connections, f"Speed test: {connections} connection(s), {bytes_total} bytes…", force=True)
        outcome = network_diagnostics.run_speedtest(
            bytes_total, connections, network_diagnostics.SPEEDTEST_URL,
            network_diagnostics.SPEEDTEST_TIMEOUT_SECONDS, db.get_all_settings(),
            progress=reporter.report,
        )
        reporter.report(connections, connections, "Speed test complete", progress_pct=100.0, force=True)
        return outcome
    result, task = await asyncio.to_thread(
        _recorded_task, "speedtest", "Network speed test", _speed_worker,
        auth_module.get_current_actor(), None,
    )
    result = dict(result or {})
    result["task_id"] = task["id"]
    result["task"] = task
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


def _calibre_series_match_items(search: Optional[str] = None, source: Optional[str] = None) -> list:
    """Calibre series list for the manual-match picker, ranked by similarity."""
    snapshot = _calibre_series_snapshot()
    rows = snapshot
    if not rows:
        rows = []
        for item in db.get_all_series() or []:
            if item.get("calibre_series_id") is None:
                continue
            rows.append({
                "id": item.get("calibre_series_id"),
                "series_id": item.get("calibre_series_id"),
                "series_name": item.get("name"),
                "name": item.get("name"),
                "authors": item.get("authors") or "",
                "book_count": item.get("book_count") or 0,
            })
    needle = normalize_text(search or "")
    source_norm = normalize_text(source or "")
    items = []
    for row in rows or []:
        name = str(row.get("series_name") or row.get("name") or "").strip()
        if not name:
            continue
        if needle and needle not in normalize_text(name):
            continue
        score = 0.0
        if source_norm:
            score = max(
                difflib.SequenceMatcher(None, source_norm, normalize_text(name)).ratio(),
                difflib.SequenceMatcher(None, normalize_text(clean_series_for_matching(source or "")), normalize_text(name)).ratio(),
            )
        items.append({
            "id": row.get("id") or row.get("series_id"),
            "name": name,
            "authors": row.get("authors") or "",
            "book_count": row.get("book_count") or 0,
            "score": round(score, 3),
        })
    if source_norm:
        items.sort(key=lambda item: item["score"], reverse=True)
    return items


@app.get("/api/calibre/series")
async def api_calibre_series_list(search: Optional[str] = None, source: Optional[str] = None):
    items = await asyncio.to_thread(_calibre_series_match_items, search, source)
    return JSONResponse({"status": "success", "series": items, "count": len(items)})


def _match_awaiting_series(
    source_series: str,
    calibre_series_id: Optional[int],
    new_series_title: Optional[str],
    actor: str,
) -> Dict[str, Any]:
    from app.parser import clean_series_for_matching, normalize_text

    source = str(source_series or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="source_series is required")
    calibre_id = None
    target_name = ""
    target_meta: Dict[str, Any] = {}
    if calibre_series_id is not None:
        try:
            calibre_id = int(calibre_series_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="calibre_series_id must be an integer")
        candidates = _calibre_series_snapshot() or []
        if not candidates:
            candidates = [
                {
                    "id": row.get("calibre_series_id"), "series_name": row.get("name"),
                    "name": row.get("name"), "authors": row.get("authors") or "",
                    "book_count": row.get("book_count") or 0,
                }
                for row in (db.get_all_series() or []) if row.get("calibre_series_id") is not None
            ]
        for candidate in candidates:
            candidate_id = candidate.get("id") or candidate.get("series_id")
            if candidate_id is not None and int(candidate_id) == calibre_id:
                target_name = str(candidate.get("series_name") or candidate.get("name") or "").strip()
                target_meta = candidate
                break
        if not target_name:
            raise HTTPException(status_code=404, detail="Calibre series id not found")
        try:
            db.sync_calibre_series([{
                "series_name": target_name,
                "series_id": calibre_id,
                "book_count": target_meta.get("book_count") or 0,
                "latest_index": target_meta.get("latest_index") or target_meta.get("book_count") or 0,
                "authors": target_meta.get("authors") or "",
                "publisher": target_meta.get("publisher") or "",
                "tags": target_meta.get("tags") or "",
            }])
        except Exception as exc:
            logger.warning("Could not ensure tracking for Calibre series '%s': %s", target_name, exc)
    elif new_series_title:
        target_name = str(new_series_title).strip()
        if not target_name:
            raise HTTPException(status_code=400, detail="new_series_title must not be empty")
        db.track_discovered_series(target_name)
    else:
        raise HTTPException(status_code=400, detail="provide calibre_series_id or new_series_title")

    override = db.add_series_alias_override(source, target_name, calibre_id, actor=actor)
    # Register every parse-shape candidate from this sample so future files with a
    # different imperfect primary still auto-match (task-43).
    try:
        for candidate in series_name_candidates(source):
            if normalize_text(candidate) and normalize_text(candidate) != normalize_text(source):
                db.add_series_alias_override(candidate, target_name, calibre_id, actor=actor)
    except Exception as exc:
        logger.debug("Could not register candidate alias overrides for '%s': %s", source, exc)
    release_ids: List[int] = []
    source_norms = {normalize_text(source), normalize_text(clean_series_for_matching(source))}
    source_norms.discard("")
    try:
        with db.get_connection() as conn:
            rows = conn.execute("SELECT id, series_name FROM releases;").fetchall()
            for row in rows:
                if normalize_text(row["series_name"] or "") in source_norms:
                    release_ids.append(int(row["id"]))
            if release_ids:
                placeholders = ",".join("?" for _ in release_ids)
                conn.execute(
                    f"UPDATE releases SET series_name = ?, updated_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders});",
                    [target_name, *release_ids],
                )
                conn.commit()
    except Exception as exc:
        logger.warning("Could not re-key releases for manual series match '%s': %s", source, exc)
    db._invalidate_grouped_cache()
    moved = 0
    try:
        user_folders = {
            str(user.get("staging_folder") or "").strip().lower()
            for user in db.list_users()
            if str(user.get("staging_folder") or "").strip()
        }
        route_result = downloads_pipeline.process_downloads_to_staging(
            scheduler.get_downloads_dir(create=False),
            _staging_dir_setting(),
            db.get_series_templates_map(),
            db,
            actor=actor,
            exclude_dirs=user_folders,
        )
        moved = int(route_result.get("tagged_and_moved") or 0)
        scheduler.notify_processed_batches(route_result)
    except Exception as exc:
        logger.warning("Routing after manual series match failed for '%s': %s", source, exc)
    db.log_activity(
        "SERIES_MANUALLY_MATCHED",
        f"Matched awaiting series '{source}' to '{target_name}'",
        json.dumps({
            "source": source, "target": target_name,
            "calibre_series_id": calibre_id, "release_ids": release_ids,
            "moved_files": moved,
        }),
        actor=actor,
    )
    return {
        "status": "success",
        "target": {"name": target_name, "calibre_series_id": calibre_id},
        "source_series": source,
        "override": override,
        "retagged_files": moved,
        "moved_files": moved,
        "release_ids": release_ids,
    }


@app.post("/api/downloads/awaiting-series/match")
async def api_match_awaiting_series(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    result = await asyncio.to_thread(
        _match_awaiting_series,
        str(body.get("source_series") or ""),
        body.get("calibre_series_id"),
        str(body.get("new_series_title") or "").strip() or None,
        auth_module.get_current_actor(),
    )
    return JSONResponse(result)


@app.get("/api/maintenance/suspicious-volume-ranges")
async def api_suspicious_volume_ranges():
    result, task = await asyncio.to_thread(
        _recorded_task, "audit:volume-ranges", "Audit suspicious volume ranges",
        lambda reporter: db.audit_suspicious_volume_ranges(progress=reporter.report),
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


@app.get("/api/maintenance/light-novels/audit")
async def api_ln_audit():
    result = await asyncio.to_thread(ln_releases.audit_ln_releases, db)
    return JSONResponse(result)


@app.post("/api/maintenance/light-novels/cleanup", dependencies=[Depends(require_admin)])
async def api_ln_cleanup(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm must be true to clean up light-novel releases")
    raw_ids = body.get("release_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="release_ids must be a non-empty list")
    action = str(body.get("action") or "").strip().lower()
    if action not in ("ignore", "delete"):
        raise HTTPException(status_code=400, detail="action must be 'ignore' or 'delete'")
    try:
        ids = [int(value) for value in raw_ids[:500]]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="release_ids must be integers")
    actor = auth_module.get_current_actor()

    def _worker(reporter):
        reporter.report(0, len(ids), f"{action.title()} selected light-novel releases…", force=True)
        result = ln_releases.cleanup_ln_releases(db, ids, action, actor=actor)
        reporter.report(len(ids), len(ids), f"{result['changed']} changed, {result['skipped']} skipped",
                        progress_pct=100.0, force=True)
        return result

    result, task = await asyncio.to_thread(
        _recorded_task, f"repair:light-novels-{action}", f"Light-novel releases: {action}",
        _worker, actor, None,
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


@app.post("/api/maintenance/false-series/audit")
async def api_false_series_audit():
    snapshot = await asyncio.to_thread(_calibre_series_snapshot)
    if not snapshot:
        snapshot = await asyncio.to_thread(db.get_all_series)
    result = await asyncio.to_thread(
        false_series_matches.audit_false_series_matches, db, snapshot or [],
    )
    return JSONResponse(result)


@app.post("/api/maintenance/false-series/repair", dependencies=[Depends(require_admin)])
async def api_false_series_repair(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm must be true to repair false series matches")
    raw_ids = body.get("release_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="release_ids must be a non-empty list")
    try:
        ids = [int(value) for value in raw_ids[:500]]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="release_ids must be integers")
    snapshot = await asyncio.to_thread(_calibre_series_snapshot)
    if not snapshot:
        snapshot = await asyncio.to_thread(db.get_all_series)

    def _worker(reporter):
        reporter.report(0, len(ids), "Repairing false series matches…", force=True)
        result = false_series_matches.repair_false_series_matches(
            db, ids, snapshot or [], progress=reporter.report,
        )
        reporter.report(len(ids), len(ids), f"Repaired {result.get('repaired', 0)} release(s)",
                        progress_pct=100.0, force=True)
        return result

    result, task = await asyncio.to_thread(
        _recorded_task, "repair:false-series", "Repair false series matches", _worker,
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


@app.post("/api/maintenance/repair-volume-ranges", dependencies=[Depends(require_admin)])
async def api_repair_volume_ranges(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    ids = body.get("release_ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="release_ids must be a list")
    cleaned = []
    for value in ids[:200]:
        try:
            cleaned.append(int(value))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="release_ids must be integers")
    result, task = await asyncio.to_thread(
        _recorded_task, "repair:volume-ranges", "Repair suspicious volume ranges",
        lambda reporter: db.repair_suspicious_volume_ranges(
            cleaned, auth_module.get_current_actor(), progress=reporter.report,
        ),
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


@app.get("/api/maintenance/series-aliases")
async def api_series_aliases():
    return JSONResponse({"status": "success", "aliases": db.get_series_alias_overrides()})


@app.delete("/api/maintenance/series-aliases/{override_id}")
async def api_delete_series_alias(override_id: int):
    deleted = db.delete_series_alias_override(override_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Series alias override not found")
    db._invalidate_grouped_cache()
    return JSONResponse({"status": "success", "deleted": 1})


@app.get("/api/maintenance/releases-cache")
async def api_releases_cache(
    series: Optional[str] = None, statuses: Optional[str] = None,
    exclude_statuses: Optional[str] = None,
):
    """Cheap preview count of clearable cached releases."""
    status_list = [item.strip() for item in (statuses or "").split(",") if item.strip()] or None
    exclude_list = [item.strip() for item in (exclude_statuses or "").split(",") if item.strip()] or None
    result = await asyncio.to_thread(db.count_cached_releases, series, status_list, exclude_list)
    return JSONResponse({"status": "success", **result})


@app.post("/api/maintenance/clear-releases", dependencies=[Depends(require_admin)])
async def api_clear_releases(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm must be true to clear cached releases")
    series_name = str(body.get("series_name") or "").strip() or None
    raw_statuses = body.get("statuses")
    if raw_statuses is None:
        statuses = None
    elif isinstance(raw_statuses, str):
        statuses = [item.strip() for item in raw_statuses.split(",") if item.strip()] or None
    elif isinstance(raw_statuses, list):
        statuses = [str(item).strip() for item in raw_statuses if str(item or "").strip()] or None
    else:
        raise HTTPException(status_code=400, detail="statuses must be a list or comma-separated string")
    raw_excluded = body.get("exclude_statuses")
    if raw_excluded is None:
        exclude_statuses = None
    elif isinstance(raw_excluded, list):
        exclude_statuses = [str(item).strip() for item in raw_excluded if str(item or "").strip()] or None
    else:
        raise HTTPException(status_code=400, detail="exclude_statuses must be a list")
    result, task = await asyncio.to_thread(
        _recorded_task, "releases:clear", "Clear cached releases",
        lambda reporter: db.clear_cached_releases(
            series_name, statuses, auth_module.get_current_actor(), exclude_statuses,
        ),
        auth_module.get_current_actor(), series_name,
    )
    return JSONResponse({"status": "success", **result, "task_id": task["id"], "task": task})


@app.get("/api/maintenance/series-attribution")
async def api_series_attribution_audit():
    """Read-only audit of releases stamped under the wrong (spinoff/parent) series."""
    result, task = await asyncio.to_thread(
        _recorded_task, "audit:attribution", "Audit series attribution",
        lambda reporter: scheduler.audit_series_attribution(progress=reporter.report),
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


@app.post("/api/maintenance/series-attribution-repair", dependencies=[Depends(require_admin)])
async def api_series_attribution_repair(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    release_ids = body.get("release_ids") or []
    if not isinstance(release_ids, list):
        raise HTTPException(status_code=400, detail="release_ids must be a list")
    cleaned = []
    for value in release_ids[:200]:
        try:
            cleaned.append(int(value))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="release_ids must be integers")
    result, task = await asyncio.to_thread(
        _recorded_task, "repair:attribution", "Repair series attribution",
        lambda reporter: scheduler.repair_series_attribution(
            cleaned, auth_module.get_current_actor(), progress=reporter.report,
        ),
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


@app.post("/api/logs/prune", dependencies=[Depends(require_admin)])
async def api_logs_prune():
    """Manually prune activity_logs (retention + max rows) and checkpoint the WAL."""
    result, task = await asyncio.to_thread(
        _recorded_task, "logs:prune", "Prune activity log",
        lambda reporter: scheduler.prune_activity_logs("user"),
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({"status": "success", **result, "task_id": task["id"], "task": task})


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
    if getattr(model, "omnibus", False):
        # An omnibus must not look like volume 1: strip a trailing volume marker and
        # append "Omnibus" instead (the marker later volumes are checked against).
        title = str(model.title or "").strip()
        title = re.sub(
            r"\s+v\d+(?:\.\d+)?(?:\s*[-–]\s*v?\d+(?:\.\d+)?)?\s*$", "", title,
            flags=re.IGNORECASE,
        ).strip()
        if not title:
            title = f"{model.series} Omnibus"
        elif not title.lower().endswith("omnibus"):
            title = f"{title} Omnibus"
        updates["Title"] = title
    elif model.title:
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
    if getattr(model, "omnibus", False):
        defaults = {"Title": f"{model.series} Omnibus", "Manga": "YesAndRightToLeft"}
    else:
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

    renamed_from = None
    new_filename = str(getattr(model, "new_filename", "") or "").strip()
    if new_filename:
        if new_filename in (".", "..") or "/" in new_filename or "\\" in new_filename:
            raise HTTPException(status_code=400, detail="new_filename must be a plain filename")
        if not new_filename.lower().endswith(".cbz"):
            raise HTTPException(status_code=400, detail="new_filename must end in .cbz")
        staging_root = os.path.realpath(_staging_dir_setting())
        target = os.path.realpath(os.path.join(os.path.dirname(entry["path"]), new_filename))
        if target != staging_root and not target.startswith(staging_root + os.sep):
            raise HTTPException(status_code=403, detail="new_filename escapes the staging directory")
        if target != os.path.realpath(entry["path"]):
            if os.path.exists(target):
                raise HTTPException(status_code=409, detail="A staged file with that name already exists")
            renamed_from = entry["relative"]
            try:
                os.replace(entry["path"], target)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Rename failed: {exc}") from exc
            db.log_activity(
                "STAGING_FILE_RENAMED",
                f"Renamed staged file '{renamed_from}' → '{new_filename}'",
                f"series: {model.series}; volume: {model.volume or '-'}",
            )
            entry = dict(entry, path=target, name=new_filename,
                         relative=os.path.relpath(target, staging_root).replace(os.sep, "/"))
    try:
        from app.staged_ownership import invalidate_staged_ownership_cache
        invalidate_staged_ownership_cache()
    except Exception:
        pass
    try:
        db._invalidate_grouped_cache()
    except Exception:
        pass

    db.log_activity(
        "MANUAL_TAG_CBZ",
        f"Manually tagged '{entry['relative']}' as series '{model.series}'",
        f"Volume: {volume or '-'}; writer: {model.writer or '-'}; publisher: {model.publisher or '-'}",
    )
    return {
        "status": "success",
        "file": entry["relative"],
        "name": entry["name"],
        "renamed_from": renamed_from,
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


@app.post("/api/tagger/manual-tag", dependencies=[Depends(require_admin)])
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

_CALIBRE_SNAPSHOT_TTL = 10.0
_calibre_snapshot_lock = threading.Lock()
_calibre_snapshot_cache: Optional[list] = None
_calibre_snapshot_cached_at = 0.0


def _invalidate_calibre_snapshot() -> None:
    """Drop the memoised Calibre series list after a series-identity mutation."""
    global _calibre_snapshot_cache, _calibre_snapshot_cached_at
    with _calibre_snapshot_lock:
        _calibre_snapshot_cache = None
        _calibre_snapshot_cached_at = 0.0


def _calibre_series_snapshot() -> Optional[list]:
    """Live Calibre series list when the library is reachable (else ``None``).

    Memoised for 10s under a single-flight lock: every picker keystroke used to
    rescan the whole Calibre library, and a burst of re-binds shared nothing.
    Mutating endpoints call ``_invalidate_calibre_snapshot()``.
    """
    global _calibre_snapshot_cache, _calibre_snapshot_cached_at
    with _calibre_snapshot_lock:
        now = time.monotonic()
        if _calibre_snapshot_cache is not None and (now - _calibre_snapshot_cached_at) < _CALIBRE_SNAPSHOT_TTL:
            return _calibre_snapshot_cache
        try:
            scanner = CalibreMangaScanner(db.get_setting("calibre_path", CALIBRE_DIR))
            if scanner.is_available():
                _calibre_snapshot_cache = scanner.get_all_series()
                _calibre_snapshot_cached_at = time.monotonic()
                return _calibre_snapshot_cache
        except Exception as exc:  # pragma: no cover - audit falls back to synced rows
            logger.debug("Calibre snapshot unavailable for the CBZ audit: %s", exc)
        return None


def _run_staging_cbz_audit() -> Dict[str, Any]:
    return series_audit.audit_staging_cbz_files(
        db, _staging_dir_setting(), calibre_series=_calibre_series_snapshot()
    )


def _staging_calibre_snapshot():
    snapshot = _calibre_series_snapshot()
    if snapshot is None:
        try:
            snapshot = db.get_all_series()
        except Exception:
            snapshot = []
    return snapshot or []


def _downloads_root() -> str:
    return db.get_setting("downloads_dir", scheduler.get_downloads_dir(create=False))


def _resolve_awaiting_download_file(relative: str):
    """Resolve one client-supplied raw-download file to a verified awaiting entry."""
    if not isinstance(relative, str) or not relative.strip():
        raise HTTPException(status_code=400, detail="relative is required")
    candidate = relative.strip().replace("\\", "/")
    parts = [part for part in candidate.split("/") if part]
    if not parts or any(part == ".." for part in parts) or candidate.startswith("/") or (len(candidate) > 1 and candidate[1] == ":"):
        raise HTTPException(status_code=400, detail="relative must be a downloads-relative path")
    root = _downloads_root()
    if not root or not os.path.isdir(root):
        raise HTTPException(status_code=400, detail="downloads_dir is not configured or does not exist")
    entries = downloads_pipeline.scan_awaiting_series(root, db.get_series_templates_map())
    matches = []
    for bucket in entries:
        for item in bucket.get("files") or []:
            if item.get("relative") == candidate:
                matches.append((bucket, item))
    if not matches:
        bare = candidate.rsplit("/", 1)[-1]
        matches = [
            (bucket, item) for bucket in entries for item in bucket.get("files") or []
            if item.get("name") == bare
        ]
    if not matches:
        raise HTTPException(status_code=404, detail=f"'{candidate}' is not an awaiting-Calibre file")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="ambiguous filename; pass the downloads-relative path")
    bucket, item = matches[0]
    path = os.path.join(root, str(item["relative"]).replace("/", os.sep))
    if os.path.islink(path):
        raise HTTPException(status_code=403, detail="symlinked files are not accepted")
    try:
        safe_path = security.ensure_path_within_roots(path, [root])
    except security.UnsafePathError as exc:
        raise HTTPException(status_code=403, detail=f"Rejected downloads path: {exc}") from exc
    if not os.path.isfile(safe_path):
        raise HTTPException(status_code=404, detail="the file no longer exists")
    return bucket, item, safe_path, root


def _prune_empty_parents(path: str, root: str) -> None:
    try:
        root_real = os.path.realpath(root)
        parent = os.path.dirname(os.path.realpath(path))
        while parent and parent != root_real and os.path.commonpath([root_real, parent]) == root_real:
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)
    except (OSError, ValueError):
        return


def _failure_file_path(row: Dict[str, Any], root: str) -> str:
    relative = str(row.get("file_relative") or "").replace("\\", "/")
    parts = [part for part in relative.split("/") if part]
    if not parts or any(part == ".." for part in parts) or relative.startswith("/") or (len(relative) > 1 and relative[1] == ":"):
        raise HTTPException(status_code=400, detail="recorded failure path is not downloads-relative")
    path = os.path.join(root, relative.replace("/", os.sep))
    if os.path.islink(path):
        raise HTTPException(status_code=403, detail="symlinked files are not accepted")
    try:
        safe_path = security.ensure_path_within_roots(path, [root])
    except security.UnsafePathError as exc:
        raise HTTPException(status_code=403, detail=f"Rejected downloads path: {exc}") from exc
    if not os.path.isfile(safe_path):
        raise HTTPException(status_code=404, detail="the failed file no longer exists")
    return safe_path


@app.post("/api/downloads/inventory/audit", dependencies=[Depends(require_admin)])
async def api_inventory_cleanup_audit():
    """Read-only preview of duplicate / already-present raw inventory entries."""
    result = await asyncio.to_thread(
        downloads_inventory_cleanup.audit_inventory_cleanup,
        db, scheduler.get_downloads_dir(create=False), _staging_dir_setting(), _calibre_series_snapshot(),
    )
    return JSONResponse(result)


@app.post("/api/downloads/inventory/clear", dependencies=[Depends(require_admin)])
async def api_inventory_cleanup_clear(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm must be true to clear raw inventory entries")
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in ("duplicates", "already_present", "all"):
        raise HTTPException(status_code=400, detail="mode must be duplicates, already_present or all")
    relatives = body.get("relatives")
    if relatives is not None and not isinstance(relatives, list):
        raise HTTPException(status_code=400, detail="relatives must be a list")
    root = scheduler.get_downloads_dir(create=False)
    staging = _staging_dir_setting()
    calibre = _calibre_series_snapshot()
    actor = auth_module.get_current_actor()

    def _worker(reporter):
        reporter.report(0, len(relatives or []), "Revalidating raw inventory candidates…", force=True)
        return downloads_inventory_cleanup.clear_inventory(
            db, root, staging, mode, relatives=[str(value) for value in (relatives or [])],
            calibre_series=calibre, report=reporter.report,
        )

    info = background_tasks.run_in_background(
        db, "downloads:inventory-clear", f"Clear raw inventory ({mode})", _worker,
        actor=actor, scope="downloads",
    )
    return JSONResponse({"status": "started", **info})


@app.post("/api/downloads/inventory/delete", dependencies=[Depends(require_admin)])
async def api_inventory_cleanup_delete(payload: Dict[str, Any]):
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm must be true to delete a raw inventory entry")
    relative = str(payload.get("relative") or "").strip()
    if not relative:
        raise HTTPException(status_code=400, detail="relative is required")
    try:
        result = await asyncio.to_thread(
            downloads_inventory_cleanup.delete_inventory_entry,
            db, scheduler.get_downloads_dir(create=False), relative,
        )
    except ValueError as exc:
        message = str(exc)
        status = 404 if "not in the current" in message or "no longer exists" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    try:
        downloads_inventory.refresh_inventory(db, scheduler.get_downloads_dir(create=False))
    except Exception as exc:
        logger.debug("Inventory refresh after raw delete failed: %s", exc)
    return JSONResponse(result)


@app.get("/api/downloads/failures")
async def api_download_failures():
    root = scheduler.get_downloads_dir(create=False)
    rows = await asyncio.to_thread(download_failures.list_failures, db, root)
    return JSONResponse({"status": "success", "failures": rows, "count": len(rows)})


@app.post("/api/downloads/failures/scan", dependencies=[Depends(require_admin)])
async def api_download_failures_scan():
    root = scheduler.get_downloads_dir(create=False)

    def _worker(reporter):
        reporter.report(0, 0, "Scanning downloads for unreadable archives…", force=True)
        stats = download_failures.scan_failures(db, root)
        rows = download_failures.list_failures(db, root)
        reporter.report(stats.get("checked", 0), stats.get("checked", 0),
                        f"Checked {stats.get('checked', 0)} file(s); {stats.get('failed', 0)} failed",
                        progress_pct=100.0, force=True)
        return {"scan": stats, "failures": rows}

    result, task = await asyncio.to_thread(
        _recorded_task, "downloads:failures-scan", "Scan failed documents", _worker,
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({"status": "success", **result, "task_id": task["id"], "task": task})


@app.post("/api/downloads/failures/{failure_id}/retry", dependencies=[Depends(require_admin)])
async def api_download_failure_retry(failure_id: int):
    row = await asyncio.to_thread(db.get_download_failure, failure_id)
    if not row or str(row.get("status")) != "failed":
        raise HTTPException(status_code=404, detail="Failed document not found")
    root = scheduler.get_downloads_dir(create=False)
    info = await asyncio.to_thread(download_failures.serialize_failure, db, row, root)
    if not info.get("release_id"):
        raise HTTPException(status_code=409, detail="No matching release for this file; correct the series/volume mapping first")
    if not info.get("can_retry"):
        raise HTTPException(status_code=409, detail=info.get("retry_reason") or "Matched release cannot be retried")
    response = await api_release_download(int(info["release_id"]))
    try:
        body = json.loads(response.body) if hasattr(response, "body") else {}
    except (TypeError, ValueError):
        body = {}
    if getattr(response, "status_code", 200) >= 400:
        raise HTTPException(
            status_code=409,
            detail=body.get("message") or "Downloader rejected the retry",
        )
    db.mark_download_failure(failure_id, "retried", increments_attempt=True)
    db.log_activity(
        "DOWNLOAD_FAILURE_RETRIED",
        f"Retried failed document '{info['file_relative']}' via release #{info['release_id']}",
        json.dumps({"failure_id": failure_id, "release_id": info["release_id"], "engine": body.get("engine")}),
    )
    return JSONResponse({"status": "success", "failure_id": failure_id, "release_id": info["release_id"],
                         "download": body, "state": release_download_state(db.get_release_by_id(int(info["release_id"])))})


@app.post("/api/downloads/failures/{failure_id}/convert", dependencies=[Depends(require_admin)])
async def api_download_failure_convert(failure_id: int):
    """Convert one convertible raw archive to CBZ, then re-run routing (task-67)."""
    row = await asyncio.to_thread(db.get_download_failure, failure_id)
    if not row or str(row.get("status")) != "failed":
        raise HTTPException(status_code=404, detail="Failed document not found")
    root = scheduler.get_downloads_dir(create=False)
    info = await asyncio.to_thread(download_failures.serialize_failure, db, row, root)
    if not info.get("can_convert"):
        raise HTTPException(status_code=409, detail="This file is not a convertible comic archive")
    path = _failure_file_path(row, root)
    if not path or not os.path.isfile(path) or os.path.islink(path):
        raise HTTPException(status_code=404, detail="Failed document file not found")
    actor = auth_module.get_current_actor()

    def _worker(reporter):
        reporter.report(0, 1, f"Converting {info.get('name') or ''} to CBZ…", force=True)
        result = archive_conversion.convert_archive_to_cbz(path)
        if result.get("status") == "success":
            try:
                db.clear_download_failure(str(row.get("file_relative") or ""))
            except Exception:
                pass
            try:
                db.log_activity(
                    "ARCHIVE_CONVERTED_TO_CBZ",
                    f"Converted '{info.get('name')}' to CBZ",
                    json.dumps({"entries": result.get("entries"), "bytes": result.get("bytes"), "source": "failure_row"}),
                )
            except Exception:
                pass
            try:
                routing = scheduler.process_downloads_to_staging(actor=actor)
            except Exception as exc:
                logger.debug("Routing after conversion failed: %s", exc)
                routing = {}
            reporter.report(1, 1, f"Converted to {os.path.basename(result.get('target', ''))}", progress_pct=100.0, force=True)
            return {"status": "success", **result, "routing": routing}
        reporter.report(1, 1, f"Conversion failed: {result.get('reason')}", progress_pct=100.0, force=True)
        return {"status": "error", **result}

    result, task = await asyncio.to_thread(
        _recorded_task, "downloads:archive-convert", f"Convert failed document #{failure_id}",
        _worker, actor, str(row.get("file_relative") or ""),
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


def _delete_failure_rows(rows: List[Dict[str, Any]], root: str) -> Dict[str, Any]:
    deleted = skipped = failed = bytes_freed = 0
    deleted_ids: List[int] = []
    for row in rows:
        failure_id = int(row.get("id") or 0)
        try:
            path = _failure_file_path(row, root)
        except HTTPException as exc:
            skipped += 1
            if exc.status_code == 404:
                try:
                    db.mark_download_failure(failure_id, "deleted")
                except Exception:
                    pass
            continue
        try:
            size = os.path.getsize(path)
            os.remove(path)
            deleted += 1
            bytes_freed += size
            deleted_ids.append(failure_id)
            db.mark_download_failure(failure_id, "deleted")
            db.log_activity(
                "DOWNLOAD_FAILURE_DELETED",
                f"Deleted failed document '{row.get('file_relative')}'",
                json.dumps({"failure_id": failure_id, "relative": row.get("file_relative"), "size": size}),
            )
            _prune_empty_parents(path, root)
        except OSError as exc:
            failed += 1
            logger.warning("Could not delete failed document %s: %s", path, exc)
    return {"deleted": deleted, "skipped": skipped, "failed": failed, "bytes_freed": bytes_freed,
            "deleted_ids": deleted_ids}


@app.post("/api/downloads/failures/{failure_id}/delete", dependencies=[Depends(require_admin)])
async def api_download_failure_delete(failure_id: int):
    row = await asyncio.to_thread(db.get_download_failure, failure_id)
    if not row or str(row.get("status")) != "failed":
        raise HTTPException(status_code=404, detail="Failed document not found")
    root = scheduler.get_downloads_dir(create=False)
    # A single delete validates the recorded path and surfaces the error (traversal/
    # symlink/vanished) instead of silently skipping it.
    _failure_file_path(row, root)
    result = await asyncio.to_thread(_delete_failure_rows, [row], root)
    try:
        await asyncio.to_thread(
            downloads_reconcile.reconcile_missing_release_files,
            db, root, _staging_dir_setting(), _calibre_series_snapshot(),
        )
    except Exception as exc:
        logger.debug("Reconcile after failed-document delete failed: %s", exc)
    remaining = len(await asyncio.to_thread(db.list_download_failures))
    return JSONResponse({"status": "success", **result, "remaining": remaining})


@app.post("/api/downloads/failures/delete-all", dependencies=[Depends(require_admin)])
async def api_download_failures_delete_all(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm must be true to delete all failed documents")
    root = scheduler.get_downloads_dir(create=False)
    rows = await asyncio.to_thread(db.list_download_failures)
    result = await asyncio.to_thread(_delete_failure_rows, rows, root)
    try:
        await asyncio.to_thread(
            downloads_reconcile.reconcile_missing_release_files,
            db, root, _staging_dir_setting(), _calibre_series_snapshot(),
        )
    except Exception as exc:
        logger.debug("Reconcile after failed-document bulk delete failed: %s", exc)
    return JSONResponse({"status": "success", **result, "remaining": len(await asyncio.to_thread(db.list_download_failures))})


@app.get("/api/downloads/staging-groups")
async def api_staging_groups():
    groups = await asyncio.to_thread(
        staging_groups.build_staging_groups,
        _staging_dir_setting(), owner_labels=_staging_owner_labels(),
    )
    return JSONResponse({"status": "success", "groups": groups, "count": len(groups)})


@app.post("/api/downloads/staging-series/match")
async def api_staging_series_match(payload: Dict[str, Any]):
    """Retag every staged file in a series group and link/merge the Calibre series."""
    source = str(payload.get("source_series") or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="source_series is required")
    owner = str(payload.get("owner") or "").strip().lower()
    owner_labels = _staging_owner_labels()
    groups = await asyncio.to_thread(
        staging_groups.build_staging_groups,
        _staging_dir_setting(), owner_labels=owner_labels,
    )
    from app.parser import normalize_text as _normalize_text
    if not any(
        _normalize_text(group.get("series") or "") == _normalize_text(source)
        and str(group.get("owner") or "") == owner
        for group in groups
    ):
        raise HTTPException(status_code=404, detail=f"staging series '{source}' not found for that owner")
    target = str(payload.get("target_series") or payload.get("new_series_title") or "").strip()
    calibre_series = None
    if not target:
        try:
            calibre_id = int(payload.get("calibre_series_id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="calibre_series_id or new_series_title is required")
        snapshot = await asyncio.to_thread(_calibre_series_snapshot)
        if not snapshot:
            snapshot = await asyncio.to_thread(db.get_all_series)
        calibre_series = next(
            (row for row in snapshot or []
             if int(row.get("series_id") or row.get("id") or row.get("calibre_series_id") or 0) == calibre_id),
            None,
        )
        if not calibre_series:
            raise HTTPException(status_code=404, detail="Calibre series not found")
        target = str(calibre_series.get("series_name") or calibre_series.get("name") or "").strip()
    actor = auth_module.get_current_actor()
    staging_dir = _staging_dir_setting()

    def _worker(reporter):
        return staging_groups.retag_staging_group(
            db, staging_dir, source, target, calibre_series=calibre_series,
            owner=owner, owner_labels=owner_labels,
            actor=actor, reporter=reporter.report,
        )

    info = background_tasks.run_in_background(
        db, "downloads:staging-series-match", f"Match staging series '{source}' → {target}",
        _worker, actor=actor, scope=source,
    )
    return JSONResponse({"status": "started", "source": source, "target": target, **info})


@app.post("/api/downloads/files/delete")
async def api_downloads_file_delete(payload: Dict[str, Any]):
    """Delete exactly one verified staging or awaiting-Calibre file (task-57)."""
    area = str(payload.get("area") or "").strip().lower()
    raw_name = payload.get("relative") or payload.get("filename")
    if area not in ("staging", "downloads"):
        raise HTTPException(status_code=400, detail="area must be 'staging' or 'downloads'")
    if area == "staging":
        entry = await asyncio.to_thread(_resolve_staging_cbz, str(raw_name or ""))
        path = entry["path"]
        root = _staging_dir_setting()
        series = str((extract_manga_bundle_and_volumes(entry.get("name") or "") or {}).get("series_name") or "")
        relative = entry.get("relative") or entry.get("name")
        series_remaining = None
    else:
        bucket, item, path, root = await asyncio.to_thread(
            _resolve_awaiting_download_file, str(raw_name or ""),
        )
        series = bucket.get("series") or ""
        relative = item.get("relative") or item.get("name")
        series_remaining = max(0, int(bucket.get("count") or 0) - 1)
    # Re-validate immediately before removal.
    if os.path.islink(path):
        raise HTTPException(status_code=403, detail="symlinked files are not accepted")
    try:
        security.ensure_path_within_roots(path, [root])
    except security.UnsafePathError as exc:
        raise HTTPException(status_code=403, detail=f"Rejected path: {exc}") from exc
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="the file no longer exists")
    try:
        size = os.path.getsize(path)
        os.remove(path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not delete file: {exc}") from exc
    _prune_empty_parents(path, root)
    if area == "staging":
        try:
            from app.staged_ownership import invalidate_staged_ownership_cache
            invalidate_staged_ownership_cache()
        except Exception as exc:
            logger.debug("Could not invalidate staged ownership cache: %s", exc)
    try:
        db.log_activity(
            "DOWNLOAD_FILE_DELETED",
            f"Deleted {area} file '{relative}'",
            json.dumps({"area": area, "relative": relative, "size": size, "series": series}),
        )
    except Exception as exc:
        logger.debug("Could not log file deletion: %s", exc)
    # Keep release bookkeeping accurate: a deleted staging/raw file must not leave a
    # release stuck COMPLETED/in-staging when no copy remains.
    try:
        await asyncio.to_thread(
            downloads_reconcile.reconcile_missing_release_files,
            db, scheduler.get_downloads_dir(create=False), _staging_dir_setting(),
            _calibre_series_snapshot(),
        )
    except Exception as exc:
        logger.debug("Reconcile after file deletion failed: %s", exc)
    if area == "downloads":
        try:
            await asyncio.to_thread(
                downloads_inventory.refresh_inventory, db,
                scheduler.get_downloads_dir(create=False),
            )
        except Exception as exc:
            logger.debug("Inventory refresh after file deletion failed: %s", exc)
        remaining = 0
        try:
            remaining = sum(len(bucket.get("files") or []) for bucket in
                            downloads_pipeline.scan_awaiting_series(
                                scheduler.get_downloads_dir(create=False), db.get_series_templates_map(),
                            ))
        except Exception:
            remaining = 0
    else:
        remaining = len(cbz_tagger.scan_staging_cbz_files(_staging_dir_setting()))
    return JSONResponse({
        "status": "success", "area": area, "deleted": True, "relative": relative,
        "remaining_files": remaining, "series": series, "series_remaining": series_remaining,
    })


@app.get("/api/downloads/staging-duplicates")
async def api_staging_duplicates():
    """Read-only audit: staging copies whose series+volumes are already in Calibre."""
    result = await asyncio.to_thread(
        staging_duplicates.audit_staging_duplicates,
        db, _staging_dir_setting(), _staging_calibre_snapshot(),
    )
    return JSONResponse({**result, "staging_dir": _staging_dir_setting()})


@app.post("/api/downloads/staging-duplicates/purge", dependencies=[Depends(require_admin)])
async def api_staging_duplicates_purge(payload: Optional[Dict[str, Any]] = None):
    body = payload or {}
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm must be true to purge staging duplicates")
    filenames = body.get("filenames")
    all_files = body.get("all") is True
    if not all_files:
        if not isinstance(filenames, list) or not filenames:
            raise HTTPException(status_code=400, detail="filenames must be a non-empty list (or all=true)")
    actor = auth_module.get_current_actor()
    calibre = _staging_calibre_snapshot()

    def _worker(reporter):
        return staging_duplicates.purge_staging_duplicates(
            db, _staging_dir_setting(), filenames=filenames, all_files=all_files,
            confirm=True, report=reporter.report, calibre_series=calibre,
        )

    info = background_tasks.run_in_background(
        db, "downloads:staging-purge", "Purge staging duplicates", _worker,
        actor=actor, scope="staging",
    )
    return JSONResponse({"status": "started", **info})


@app.get("/api/tagger/audit")
async def api_tagger_audit():
    """Read-only audit of staged CBZ ``<Series>`` values (never modifies a file)."""
    def _worker(reporter):
        reporter.report(0, 0, "Scanning staging files…", force=True)
        result = series_audit.audit_staging_cbz_files(
            db, _staging_dir_setting(), calibre_series=_calibre_series_snapshot(),
            progress=reporter.report,
        )
        return result
    result, task = await asyncio.to_thread(
        _recorded_task, "audit:comicinfo", "Audit ComicInfo series tags", _worker,
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


def _repair_staging_cbz_series(
    filenames: list,
    repair_writer: bool = True,
    repair_publisher: bool = True,
    progress=None,
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
    field_counts: Dict[str, int] = {}
    results: list = []

    for index, entry in enumerate(resolved, 1):
        if progress:
            progress(index - 1, len(resolved), f"Repairing {entry.get('name') or entry.get('relative') or ''}")
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
        for field in plan["changes"]:
            field_counts[field] = field_counts.get(field, 0) + 1
        db.log_activity(
            "CBZ_SERIES_REPAIRED",
            f"Repaired ComicInfo.xml in '{record['file']}' ({', '.join(plan['changes'])})",
            f"Series -> '{plan['updates'].get('Series', record.get('current_series')) or ''}'; "
            f"changes: {', '.join(plan['changes'])}",
        )
        results.append(item)

    if progress:
        progress(len(resolved), len(resolved), "ComicInfo repair complete")
    return {
        "status": "success" if counts["failed"] == 0 else "partial",
        **counts,
        "field_counts": field_counts,
        "results": results,
    }


@app.post("/api/tagger/repair-series", dependencies=[Depends(require_admin)])
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
    result, task = await asyncio.to_thread(
        _recorded_task, "repair:comicinfo", "Repair ComicInfo metadata",
        lambda reporter: _repair_staging_cbz_series(
            model.files, model.repair_writer, model.repair_publisher, progress=reporter.report,
        ),
        auth_module.get_current_actor(), None,
    )
    return JSONResponse({**result, "task_id": task["id"], "task": task})


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

@app.post("/api/downloader/test", dependencies=[Depends(require_admin)])
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

@app.post("/api/downloader/torbox/test", dependencies=[Depends(require_admin)])
async def api_downloader_torbox_test(payload: Optional[Dict[str, Any]] = None):
    """Test the Torbox connection (plan/slots).  An unsaved key may be supplied."""
    model = _validated_payload(schemas.TorboxTestRequest, payload or {})
    provided_key = (model.torbox_api_key or "").strip()
    if provided_key and provided_key != schemas.TORBOX_API_KEY_MASK:
        client = TorboxDownloader(api_key=provided_key, enabled=True)
    else:
        client = build_downloader(db, engine="torbox")
    return JSONResponse(client.test_connection())

@app.post("/api/downloader/configure", dependencies=[Depends(require_admin)])
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

@app.post("/api/downloader/pause/{torrent_hash}", dependencies=[Depends(require_admin)])
async def api_downloader_pause(torrent_hash: str):
    qbit = scheduler.get_qbit_client()
    success = qbit.pause_torrent(torrent_hash)
    return JSONResponse({"status": "success" if success else "error"})

@app.post("/api/downloader/resume/{torrent_hash}", dependencies=[Depends(require_admin)])
async def api_downloader_resume(torrent_hash: str):
    qbit = scheduler.get_qbit_client()
    success = qbit.resume_torrent(torrent_hash)
    return JSONResponse({"status": "success" if success else "error"})

@app.delete("/api/downloader/delete/{torrent_hash}", dependencies=[Depends(require_admin)])
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
    if str(target.get("status") or "").upper() == "IN_STAGING":
        raise HTTPException(
            status_code=409,
            detail="Volumes are already staged and waiting for Calibre import; not downloading again",
        )
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
    """Remove a Torbox release from the queue (best-effort remote delete, always local).

    Local cleanup must succeed even when the remote torrent is already gone (or Torbox
    is unreachable): a stale handle used to leave the row in the queue forever because
    the request errored before clearing the row.
    """
    target = db.get_release_by_id(release_id)
    if not target:
        raise HTTPException(status_code=404, detail="Release not found")
    # FAILED rows already had their remote cleanup attempted when they failed
    # (torbox_delete_on_failure). Re-cancelling them can block on Torbox timeouts and
    # makes the Remove button appear dead, so failed rows are cleared locally only.
    is_failed = str(target.get("download_status") or "").upper() == "FAILED"
    handle = {} if is_failed else scheduler._torbox_handle_for_release(release_id)
    deleted = False
    remote_error = ""
    if handle:
        try:
            deleted = bool(await asyncio.to_thread(build_downloader(db, engine="torbox").cancel, handle))
        except Exception as exc:
            remote_error = str(exc)
            logger.debug("Could not remove Torbox job for release %s: %s", release_id, exc)
    db.clear_downloader_submission(release_id, "torbox")
    _invalidate_torbox_status_cache()
    return JSONResponse({
        "status": "success",
        "removed": True,
        "remote_deleted": deleted,
        "remote_error": remote_error,
        "message": (
            "Removed failed item from the queue" if is_failed
            else ("Removed from Torbox queue" if deleted else "Removed from the queue (no active remote job)")
        ),
    })


@app.post("/api/downloads/torbox/clear-failed")
async def api_torbox_clear_failed():
    """Remove every FAILED Torbox queue item locally (fast, no remote waits), with progress."""

    def _clear_worker(reporter):
        rows = db.get_downloader_releases("torbox", limit=500)
        failed_rows = [row for row in rows if str(row.get("download_status") or "").upper() == "FAILED"]
        total = len(failed_rows)
        reporter.report(0, total, f"Removing {total} failed Torbox item(s)…", force=True)
        for index, row in enumerate(failed_rows, 1):
            release_id = row.get("id")
            db.clear_downloader_submission(release_id, "torbox")
            reporter.report(index, total, f"Removed '{str(row.get('title') or '')[:60]}'")
        _invalidate_torbox_status_cache()
        return {
            "failed_entries": total,
            "removed": total,
            "remote_deleted": 0,
        }

    result, task = await asyncio.to_thread(
        _recorded_task, "downloads:torbox-clear-failed", "Clear failed Torbox queue items",
        _clear_worker, auth_module.get_current_actor(), None,
    )
    return JSONResponse({"status": "success", **result, "task_id": task["id"], "task": task})

@app.get("/api/releases/{release_id}/state")
async def api_release_download_state(release_id: int):
    target = db.get_release_by_id(release_id)
    if not target:
        raise HTTPException(status_code=404, detail="Release not found")
    return JSONResponse({"status": "success", **release_download_state(target)})


@app.post("/api/releases/{release_id}/download")
async def api_release_download(release_id: int, payload: Optional[Dict[str, Any]] = None):
    """Manually send a release to the configured download engine (qBittorrent or Torbox).

    ``force=true`` (second click after a "nothing missing" skip) submits the whole
    release and disables strict per-volume selection for it, so the user can pick
    volumes after the download.
    """
    body = payload or {}
    force_whole = body.get("force") is True
    target = db.get_release_by_id(release_id)
    if not target:
        raise HTTPException(status_code=404, detail="Release not found")
    if str(target.get("status") or "").upper() == "IN_STAGING":
        raise HTTPException(
            status_code=409,
            detail="Volumes are already staged and waiting for Calibre import; not downloading again",
        )

    # --- Multi-user request policy -------------------------------------
    # Normal users queue a request for admin approval unless an admin granted
    # them the bypass flag. Admins (and the legacy no-login deployment) keep the
    # direct-download behavior.
    requester = _current_user_row()
    if requester is not None and not requester.get("is_admin") and not requester.get("bypass_approval"):
        existing_request = await asyncio.to_thread(
            db.get_release_request, int(requester["id"]), release_id,
        )
        if not (existing_request and existing_request.get("status") == "approved"):
            # First click (or a rejected request raised again) queues it; an
            # already-approved request falls through and downloads normally so the
            # user is never asked to seek approval twice.
            pending = await asyncio.to_thread(
                db.create_release_request, int(requester["id"]), release_id,
            )
            already_waiting = (existing_request or {}).get("status") == "pending"
            db.log_activity(
                "RELEASE_REQUEST_PENDING",
                f"Requested '{target.get('title')}' (awaiting admin approval)",
                json.dumps({"release_id": release_id, "request_id": pending.get("id")}),
            )
            return JSONResponse({
                "status": "already_pending" if already_waiting else "pending_approval",
                "message": (
                    "This request is already waiting for an admin to approve it."
                    if already_waiting else
                    "Request sent — an admin must approve it before it downloads."
                ),
                "request": pending,
            }, status_code=202)
    if requester is not None and not requester.get("is_admin"):
        # A bypassing user owns the routing target; an admin download keeps the
        # already-approved requester's folder.
        await asyncio.to_thread(db.set_release_request_user, release_id, int(requester["id"]))
        target["request_user_id"] = int(requester["id"])

    download_url = target.get("download_url") or target.get("link")
    downloads_dir = scheduler.get_downloads_dir()
    request_user = None
    request_user_id = target.get("request_user_id")
    if request_user_id:
        request_user = await asyncio.to_thread(db.get_user_by_id, int(request_user_id))
        if request_user is not None:
            folder = await asyncio.to_thread(db.ensure_user_staging_folder, int(request_user["id"]))
            downloads_dir = os.path.join(downloads_dir, folder)
            try:
                os.makedirs(downloads_dir, exist_ok=True)
            except OSError as exc:  # pragma: no cover - surfaced via storage health
                logger.warning("Could not create user downloads folder '%s': %s", downloads_dir, exc)
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
            source="manual-force" if force_whole else "manual",
            guid=str(target.get("guid") or ""),
            torrent_hash=str(target.get("torrent_hash") or ""),
            force_whole=force_whole,
        )
        if result.get("success"):
            if result.get("skipped"):
                return JSONResponse({
                    "status": "skipped",
                    "engine": "torbox",
                    "force_available": not force_whole,
                    "message": result.get("message") or "Nothing missing; Torbox submit skipped",
                    "state": release_download_state(db.get_release_by_id(release_id)),
                })
            db.log_activity(
                "MANUAL_DOWNLOAD",
                f"Sent to Torbox: '{target['title']}'" + (" (forced whole torrent)" if force_whole else ""),
                "Engine: torbox",
            )
            await asyncio.to_thread(scheduler.process_downloads_to_staging, None)
            return JSONResponse({
                "status": "success",
                "engine": "torbox",
                "cached": bool(result.get("cached")),
                "message": "Sent to Torbox successfully",
                "state": release_download_state(db.get_release_by_id(release_id)),
            })
        return JSONResponse(
            {"status": "error", "engine": "torbox", "message": result.get("message") or "Torbox rejected the release",
             "state": release_download_state(db.get_release_by_id(release_id))},
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

        if (not force_whole and torrent_h and target.get("is_bundle")
                and missing and len(missing) < len(vols)):
            sel_res = qbit.apply_selective_volume_download(torrent_h, [float(v) for v in missing])
            if sel_res.get("status") == "filtered":
                db.log_activity(
                    "SELECTIVE_DOWNLOAD",
                    f"Selective download applied for '{target['title']}': skipped {len(sel_res.get('skipped_volumes', []))} owned volumes; downloading {len(sel_res.get('kept_volumes', []))} missing volumes."
                )

        await asyncio.to_thread(scheduler.process_downloads_to_staging, None)
        return JSONResponse({
            "status": "success", "engine": "qbit", "message": "Sent to downloader successfully",
            "state": release_download_state(db.get_release_by_id(release_id)),
        })
    else:
        return JSONResponse({
            "status": "error", "engine": "qbit", "message": "Failed to add release to downloader",
            "state": release_download_state(db.get_release_by_id(release_id)),
        }, status_code=500)

@app.post("/api/series/{series_id}/toggle")
async def api_toggle_series(series_id: int):
    """Toggle monitored status for a series (per-user under a real login)."""
    user_id = _current_user_id()
    if user_id is not None:
        row = db.get_series_by_id(series_id)
        if not row:
            raise HTTPException(status_code=404, detail="Series not found")
        membership = db.get_user_series_membership(user_id, series_id)
        if membership:
            new_value = not bool(membership.get("is_monitored"))
            db.set_user_series_monitored(user_id, series_id, new_value)
        else:
            # First toggle on a shared Calibre row inherits its state, then flips.
            new_value = not bool(row.get("is_monitored"))
            db.add_user_series(user_id, series_id, is_monitored=new_value)
        return JSONResponse({"status": "success", "is_monitored": new_value})
    is_monitored = db.toggle_series_monitored(series_id)
    return JSONResponse({"status": "success", "is_monitored": is_monitored})

@app.post("/api/series/{series_id}/link", dependencies=[Depends(require_admin)])
async def api_update_series_link(series_id: int, payload: Dict[str, Any]):
    """Update or save custom website link for a series (http/https only)."""
    model = _validated_payload(schemas.SeriesLinkUpdateRequest, payload)
    db.update_series_link(series_id, model.website_url)
    return JSONResponse({"status": "success", "website_url": model.website_url})

@app.post("/api/releases/{release_id}/different-series", dependencies=[Depends(require_admin)])
async def api_release_different_series(release_id: int, payload: Dict[str, Any]):
    """Re-key exactly one release to another series (no global alias) — task-66."""
    release = await asyncio.to_thread(db.get_release_by_id, release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    body = payload or {}
    detach = body.get("detach") is True
    remember = body.get("remember") is True
    new_title = str(body.get("new_series_title") or "").strip()
    calibre_series = None
    if detach:
        try:
            from app.parser import extract_manga_bundle_and_volumes
            target = str(extract_manga_bundle_and_volumes(str(release.get("title") or "")).get("series_name") or "").strip()
        except Exception:
            target = ""
        target = target or str(release.get("series_name") or "").strip()
    elif new_title:
        target = new_title
    else:
        try:
            calibre_id = int(body.get("calibre_series_id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="calibre_series_id, new_series_title or detach is required")
        snapshot = await asyncio.to_thread(_calibre_series_snapshot)
        if not snapshot:
            snapshot = await asyncio.to_thread(db.get_all_series)
        calibre_series = next(
            (row for row in snapshot or []
             if int(row.get("series_id") or row.get("id") or row.get("calibre_series_id") or 0) == calibre_id),
            None,
        )
        if not calibre_series:
            raise HTTPException(status_code=404, detail="Calibre series not found")
        target = str(calibre_series.get("series_name") or calibre_series.get("name") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="target series is empty")
    old_status = str(release.get("status") or "")
    veto = old_status.upper() in ("IGNORED", "BLACKLISTED")
    candidate = dict(release)
    candidate["series_name"] = target
    try:
        matcher = scheduler._series_refresh_matcher()
        if detach:
            raise RuntimeError("detach")
        checked = matcher.check_release(candidate, auto_request_monitored=True)
        status = checked.get("status") or ("DISCOVERY" if release.get("volumes") == [1.0] else "NEW")
        missing = checked.get("missing_volumes") or []
    except Exception:
        volumes = release.get("volumes") or []
        missing = volumes
        status = "DISCOVERY" if (volumes == [1.0] or not volumes or release.get("is_complete") or release.get("is_bundle")) else "NEW"
    if veto:
        status = old_status
    elif detach:
        # Explicit "detach → Discovery" overrides sticky REQUESTED for this row.
        status = status if status in ("DISCOVERY", "NEW") else "DISCOVERY"
    else:
        from app.database import resolve_status
        status = resolve_status(old_status, status) or status
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE releases SET series_name = ?, status = ?, missing_vols_json = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (target, status, json.dumps([float(v) for v in missing]), release_id),
        )
        conn.commit()
    try:
        db._invalidate_grouped_cache()
    except Exception:
        pass
    _invalidate_calibre_snapshot()
    alias_saved = None
    if remember and not detach:
        source_name = str(release.get("series_name") or "").strip()
        if source_name and normalize_text(source_name) != normalize_text(target):
            target_calibre_id = None
            if calibre_series:
                target_calibre_id = int(calibre_series.get("series_id") or calibre_series.get("id") or 0) or None
            try:
                alias_saved = await asyncio.to_thread(
                    db.add_series_alias_override,
                    source_name, target, target_calibre_id,
                    actor=auth_module.get_current_actor(),
                )
            except Exception as exc:
                logger.warning("Could not remember the release re-bind alias: %s", exc)
    try:
        db.log_activity(
            "RELEASE_REKEYED_DIFFERENT_SERIES",
            f"Re-keyed release #{release_id} from '{release.get('series_name')}' to '{target}'",
            json.dumps({"detach": detach, "status": status, "no_alias": alias_saved is None}),
        )
    except Exception:
        pass
    updated = db.get_release_by_id(release_id) or {}
    return JSONResponse({
        "status": "success", "release_id": release_id, "series_name": target,
        "missing_volumes": missing, "release_status": status,
        "alias_saved": alias_saved is not None,
        "download_state": release_download_state(updated), "release": updated,
    })


@app.post("/api/series/{series_id}/match-calibre", dependencies=[Depends(require_admin)])
async def api_match_series_to_calibre(series_id: int, payload: Dict[str, Any]):
    """Manually link a tracked series to an existing Calibre series (task-62)."""
    try:
        calibre_id = int(payload.get("calibre_series_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="calibre_series_id is required")
    trackers = await asyncio.to_thread(db.get_all_series)
    tracker = next((row for row in trackers if int(row.get("id") or 0) == series_id), None)
    if not tracker:
        raise HTTPException(status_code=404, detail="Tracked series not found")
    snapshot = await asyncio.to_thread(_calibre_series_snapshot)
    if not snapshot:
        snapshot = await asyncio.to_thread(db.get_all_series)
    target = next(
        (row for row in snapshot or []
         if int(row.get("series_id") or row.get("id") or row.get("calibre_series_id") or 0) == calibre_id),
        None,
    )
    if not target:
        raise HTTPException(status_code=404, detail="Calibre series not found")
    try:
        result = await asyncio.to_thread(
            db.link_tracked_series, str(tracker.get("name") or ""), target,
            auth_module.get_current_actor(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Refresh cached grouped views/series rows in place.
    try:
        db._invalidate_grouped_cache()
    except Exception:
        pass
    _invalidate_calibre_snapshot()
    return JSONResponse(result)


@app.get("/api/series/link-audit")
async def api_series_link_audit():
    """Read-only preview of tracked series that can be linked/merged to Calibre."""
    snapshot = await asyncio.to_thread(_calibre_series_snapshot)
    if not snapshot:
        snapshot = await asyncio.to_thread(db.get_all_series)
    trackers = await asyncio.to_thread(db.get_all_series)
    proposed = []
    for tracker in trackers:
        if tracker.get("calibre_series_id") is not None:
            continue
        from app.parser import match_manga_series_in_calibre
        matched = None
        try:
            matched = match_manga_series_in_calibre(str(tracker.get("name") or ""), snapshot or [], threshold=0.90)
        except Exception:
            matched = None
        if matched:
            proposed.append({
                "series_id": tracker.get("id"), "source": tracker.get("name"),
                "target": matched.get("series_name") or matched.get("name"),
                "calibre_series_id": matched.get("series_id") or matched.get("id"),
                "book_count": matched.get("book_count") or 0,
                "confidence": "fuzzy_or_candidate",
            })
    return JSONResponse({"status": "success", "proposed": proposed, "count": len(proposed)})


@app.post("/api/series/link-repair", dependencies=[Depends(require_admin)])
async def api_series_link_repair(payload: Optional[Dict[str, Any]] = None):
    """Merge/link tracked series to Calibre (previewable, idempotent, task-62)."""
    body = payload or {}
    dry_run = body.get("dry_run") is True or body.get("confirm") is not True
    snapshot = await asyncio.to_thread(_calibre_series_snapshot)
    if not snapshot:
        snapshot = await asyncio.to_thread(db.get_all_series)
    if dry_run:
        audit_response = await api_series_link_audit()
        try:
            audit_payload = json.loads(getattr(audit_response, "body", b"{}") or b"{}")
        except (TypeError, ValueError):
            audit_payload = {}
        return JSONResponse({"status": "success", "dry_run": True, **audit_payload})
    results = await asyncio.to_thread(db.auto_link_tracked_series, snapshot, None, auth_module.get_current_actor())
    try:
        db._invalidate_grouped_cache()
    except Exception:
        pass
    _invalidate_calibre_snapshot()
    return JSONResponse({"status": "success", "dry_run": False, "linked": results, "count": len(results)})


@app.get("/api/series/{series_id}/management")
async def api_series_management(series_id: int):
    """Series row + releases, synonyms and AniList titles for the Manage panel."""
    payload = await asyncio.to_thread(db.get_series_management, series_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Tracked series not found")
    return JSONResponse({"status": "success", **payload})


@app.post("/api/series/{series_id}/identity", dependencies=[Depends(require_admin)])
async def api_update_series_identity(series_id: int, payload: Optional[Dict[str, Any]] = None):
    """Edits the cosmetic display title and/or the AniList pin lock."""
    body = payload or {}
    display_title = body.get("display_title")
    if display_title is not None and not isinstance(display_title, str):
        raise HTTPException(status_code=400, detail="display_title must be a string")
    anilist_locked = body.get("anilist_locked")
    if anilist_locked is not None:
        anilist_locked = bool(anilist_locked)
    try:
        updated = await asyncio.to_thread(
            db.update_series_display_identity,
            series_id,
            display_title=display_title,
            clear_anilist=body.get("clear_anilist") is True,
            anilist_locked=anilist_locked,
            actor=auth_module.get_current_actor(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Tracked series not found")
    return JSONResponse({"status": "success", "series": updated})


@app.post("/api/series/{series_id}/anilist-map", dependencies=[Depends(require_admin)])
async def api_pin_series_anilist(series_id: int, payload: Optional[Dict[str, Any]] = None):
    """Pins one series to an exact AniList entry (id/URL) or a chosen search hit."""
    body = payload or {}
    if await asyncio.to_thread(db.get_series_by_id, series_id) is None:
        raise HTTPException(status_code=404, detail="Tracked series not found")
    ref = body.get("anilist_id", body.get("anilist_url"))
    anilist_id = parse_anilist_id(ref)
    meta: Optional[Dict[str, Any]] = None
    if anilist_id is not None:
        meta = await asyncio.to_thread(scheduler.anilist.resolve_by_anilist_id, anilist_id, True)
    else:
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="anilist_id, anilist_url or query is required")
        meta = await asyncio.to_thread(scheduler.anilist.resolve_manga_series, query, True)
        anilist_id = parse_anilist_id((meta or {}).get("anilist_id"))
    if not meta or anilist_id is None:
        raise HTTPException(status_code=404, detail="No AniList entry found for that reference")
    updated = await asyncio.to_thread(
        db.set_series_anilist_mapping, series_id, anilist_id, meta,
        actor=auth_module.get_current_actor(),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Tracked series not found")
    return JSONResponse({
        "status": "success",
        "series": updated,
        "anilist_id": anilist_id,
        "canonical_title": str(meta.get("canonical_title") or ""),
        "romaji_title": str(meta.get("romaji_title") or ""),
    })


@app.post("/api/series/{series_id}/synonyms/spin-out", dependencies=[Depends(require_admin)])
async def api_spin_out_series_synonym(series_id: int, payload: Optional[Dict[str, Any]] = None):
    """Splits one synonym into its own tracked series (aliases retargeted)."""
    body = payload or {}
    alias_id = body.get("alias_id")
    if alias_id is not None:
        try:
            alias_id = int(alias_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="alias_id must be an integer")
    try:
        result = await asyncio.to_thread(
            db.spin_out_series_synonym,
            series_id,
            source_name=body.get("source_name"),
            new_name=body.get("new_name"),
            alias_id=alias_id,
            move_matching_releases=body.get("move_matching_releases", True) is not False,
            actor=auth_module.get_current_actor(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@app.post("/api/series/{series_id}/aliases/{alias_id}/retarget", dependencies=[Depends(require_admin)])
async def api_retarget_series_alias(
    series_id: int, alias_id: int, payload: Optional[Dict[str, Any]] = None,
):
    """Points one synonym mapping at another tracked/Calibre series."""
    body = payload or {}
    calibre_series_id = body.get("calibre_series_id")
    try:
        result = await asyncio.to_thread(
            db.retarget_series_alias,
            series_id, alias_id,
            target_name=body.get("target_name"),
            calibre_series_id=calibre_series_id,
            move_matching_releases=body.get("move_matching_releases") is True,
            actor=auth_module.get_current_actor(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


def _start_backfill_task(series_name: str, max_pages: int) -> tuple:
    """Start the scheduler backfill with a persisted reporter (task-50 counters)."""
    import threading
    import time as _time

    task_id = db.create_background_task(
        "backfill:series", f"Refresh series: {series_name}", scope=series_name,
        actor=auth_module.get_current_actor(),
    )
    reporter = background_tasks.TaskReporter(db, task_id, None)
    reporter.report(0, max_pages, f"Refreshing '{series_name}'…", force=True)
    res = scheduler.run_series_backfill_async(
        series_name, max_pages=max_pages, actor=auth_module.get_current_actor(),
        progress=reporter.report,
    )
    if not getattr(scheduler, "is_backfilling", False):
        db.finish_background_task(task_id, "success", result=res)
    else:
        def _watch():
            while getattr(scheduler, "is_backfilling", False):
                try:
                    message = db.get_setting("backfill_progress", "") or f"Refreshing '{series_name}'…"
                except Exception:
                    message = f"Refreshing '{series_name}'…"
                reporter.report(0, max_pages, message)
                _time.sleep(0.75)
            try:
                message = db.get_setting("backfill_progress", "") or "Series refresh complete"
            except Exception:
                message = "Series refresh complete"
            reporter.report(max_pages, max_pages, message, progress_pct=100.0, force=True)
            db.finish_background_task(task_id, "success", result={"series_name": series_name, "message": message})
        threading.Thread(target=_watch, daemon=True).start()
    return res, background_tasks.serialize_task(db.get_background_task(task_id))


@app.post("/api/series/{series_id}/backfill", dependencies=[Depends(require_admin)])
async def api_series_backfill(series_id: int, payload: Optional[Dict[str, Any]] = None):
    """Refresh one series in the background: metadata, Calibre info, then Nyaa crawl."""
    model = _validated_payload(schemas.SeriesBackfillRequest, payload or {})
    series_list = db.get_all_series()
    target = next((s for s in series_list if s["id"] == series_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Series not found")

    res, task = _start_backfill_task(target["name"], model.max_pages)
    payload = dict(res or {})
    payload["task_id"] = task["id"]
    payload["task"] = task
    return JSONResponse(payload)

@app.post("/api/series/backfill", dependencies=[Depends(require_admin)])
async def api_series_backfill_by_name(payload: Dict[str, Any]):
    """Refresh one series by name in the background: metadata, Calibre info, then Nyaa crawl."""
    model = _validated_payload(schemas.SeriesBackfillByNameRequest, payload)
    res, task = _start_backfill_task(model.series_name, model.max_pages)
    payload = dict(res or {})
    payload["task_id"] = task["id"]
    payload["task"] = task
    return JSONResponse(payload)

@app.post("/api/releases/{release_id}/status", dependencies=[Depends(require_admin)])
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
    user_id = _current_user_id()
    if user_id is not None:
        db.add_user_blacklist(user_id, model.name, model.reason)
    else:
        db.add_to_blacklist(model.name, model.reason)
    db.log_activity("SERIES_BLACKLISTED", f"Blacklisted series: {model.name}", model.reason)
    return JSONResponse({"status": "success"})

@app.delete("/api/blacklist/{blacklist_id}")
async def api_delete_blacklist(blacklist_id: int):
    """Remove a series from the blacklist (per-user under a real login)."""
    user_id = _current_user_id()
    if user_id is not None:
        db.remove_user_blacklist(user_id, blacklist_id)
    else:
        db.remove_from_blacklist(blacklist_id)
    db.log_activity("BLACKLIST_REMOVED", f"Removed series #{blacklist_id} from blacklist")
    return JSONResponse({"status": "success"})

# --- Prioritized Release Groups Endpoints ---

@app.get("/api/prioritized/groups")
async def api_get_prioritized_groups():
    user_id = _current_user_id()
    groups = db.get_user_prioritized_groups(user_id) if user_id is not None else db.get_prioritized_groups()
    return JSONResponse({"status": "success", "groups": groups})

@app.post("/api/prioritized/groups")
async def api_add_prioritized_group(payload: Dict[str, Any]):
    model = _validated_payload(schemas.PrioritizedGroupCreateRequest, payload)
    user_id = _current_user_id()
    if user_id is not None:
        db.add_user_prioritized_group(user_id, model.name, model.notes, model.priority)
    else:
        db.add_prioritized_group(model.name, model.notes, model.priority)
    db.log_activity("GROUP_PRIORITIZED", f"Added prioritized release group: {model.name}", model.notes)
    return JSONResponse({"status": "success"})

@app.delete("/api/prioritized/groups/{group_id}")
async def api_delete_prioritized_group(group_id: int):
    user_id = _current_user_id()
    if user_id is not None:
        db.remove_user_prioritized_group(user_id, group_id)
    else:
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
    user_id = _current_user_id()
    if user_id is not None:
        tracked_row = db.get_series_by_name(stable_name)
        if tracked_row:
            db.add_user_series(user_id, int(tracked_row["id"]), is_monitored=True)
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
    user_id = _current_user_id()
    if user_id is not None:
        db.add_user_blacklist(user_id, model.series_name, model.reason)
    else:
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

@app.post("/api/settings", dependencies=[Depends(require_admin)])
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
    if any(key in provided for key in ("log_to_file", "log_file_max_mb", "log_file_backups")):
        try:
            configure_file_logging(db)
        except Exception as exc:  # pragma: no cover - save must succeed
            logger.debug("File logging reconfigure failed: %s", exc)
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

@app.post("/api/settings/test-webhook", dependencies=[Depends(require_admin)])
async def api_test_webhook(payload: Optional[Dict[str, Any]] = None):
    """Test Discord and/or generic webhook endpoints with immediate feedback."""
    model = _validated_payload(schemas.WebhookTestRequest, payload or {})
    discord_url = model.discord_webhook_url
    generic_url = model.generic_webhook_url or model.apprise_url
    notifier = scheduler.get_notifier()
    res = notifier.test_webhook(discord_url=discord_url, apprise_url=generic_url)
    return JSONResponse(res)

# --- Polite Historical Backfill Endpoints ---

@app.post("/api/backfill/start", dependencies=[Depends(require_admin)])
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

@app.post("/api/backfill/stop", dependencies=[Depends(require_admin)])
async def api_backfill_stop():
    """Signals the running backfiller to stop gracefully."""
    scheduler.stop_backfill()
    return JSONResponse({"status": "stopping", "message": "Backfill stopping"})

@app.get("/api/backfill/status")
async def api_backfill_status():
    """Returns current backfill progress and state."""
    return JSONResponse(scheduler.get_backfill_status())

@app.post("/api/maintenance/clear", dependencies=[Depends(require_admin)])
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


@app.post("/api/debug/action", dependencies=[Depends(require_admin)])
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
