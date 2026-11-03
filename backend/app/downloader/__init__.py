"""Downloader engines: selection + construction (task-20)."""

import logging
from typing import Any, Dict, Optional

from app.downloader.base import DownloaderClient, DownloaderError
from app.downloader.qbit_adapter import QBitDownloader
from app.downloader.torbox import TorboxDownloader

logger = logging.getLogger("mangatrax.downloader")

DOWNLOADER_ENGINES = ("qbit", "torbox")
DEFAULT_DOWNLOADER_ENGINE = "qbit"


def as_bool(value: Any, default: bool = False) -> bool:
    """Truthy setting parser (``"true"``/``"1"``/``"yes"``/``"on"``)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in ("1", "true", "yes", "on", "enabled"):
        return True
    if text in ("0", "false", "no", "off", "disabled"):
        return False
    return default


def resolve_downloader_engine(db) -> str:
    """Return the configured engine (``qbit`` default; unknown values fall back)."""
    raw = (db.get_setting("downloader_engine", DEFAULT_DOWNLOADER_ENGINE) or "").strip().lower()
    if raw in DOWNLOADER_ENGINES:
        return raw
    if raw:
        logger.warning("Unknown downloader_engine %r; using %s.", raw, DEFAULT_DOWNLOADER_ENGINE)
    return DEFAULT_DOWNLOADER_ENGINE


def build_downloader(db, qbit_client=None, engine: Optional[str] = None) -> DownloaderClient:
    """Construct the configured engine (Torbox needs no qBittorrent client)."""
    selected = (engine or resolve_downloader_engine(db)).strip().lower()
    if selected == "torbox":
        try:
            max_download_gb = float(db.get_setting("torbox_max_download_gb", "64") or 64)
        except (TypeError, ValueError):
            max_download_gb = 64.0
        max_download_gb = max(1.0, min(max_download_gb, 10240.0))
        try:
            download_concurrency = int(db.get_setting("torbox_download_concurrency", "4") or 4)
        except (TypeError, ValueError):
            download_concurrency = 4
        try:
            parallel_chunks = int(db.get_setting("torbox_parallel_chunks", "4") or 4)
        except (TypeError, ValueError):
            parallel_chunks = 4
        return TorboxDownloader(
            api_key=db.get_setting("torbox_api_key", "") or "",
            enabled=as_bool(db.get_setting("torbox_enabled", "false"), default=False),
            delete_after_download=as_bool(
                db.get_setting("torbox_delete_after_download", "true"), default=True,
            ),
            max_file_bytes=int(max_download_gb * 1024 * 1024 * 1024),
            max_concurrency=download_concurrency,
            parallel_chunks=parallel_chunks,
        )
    if qbit_client is None:
        from app.downloader.qbit import QBitClient

        qbit_client = QBitClient(
            base_url=db.get_setting("qbit_url", "http://localhost:8080"),
            username=db.get_setting("qbit_username", "admin"),
            password=db.get_setting("qbit_password", "adminadmin"),
            enabled=as_bool(db.get_setting("qbit_enabled", "true"), default=True),
        )
    return QBitDownloader(qbit_client)
