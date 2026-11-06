"""Optional archive→CBZ conversion with strict safety guards (task-67).

Supported: .zip/.cbz (native ZIP), .cbr/.rar (rarfile + external extractor), .7z/.cb7
(py7zr).  Ebooks/PDF are not comic archives and are rejected.  Every entry is validated
against path traversal/absolute paths/symlinks, with total-size and entry-count caps;
conversion writes a temporary ZIP beside the target and atomically renames it, deleting
the original only after the archive validates.
"""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mangatrax.archive_conversion")

CONVERTIBLE_EXTENSIONS = {".cbr", ".rar", ".7z", ".cb7", ".zip"}
NATIVE_EXTENSIONS = {".cbz"}
MAX_ENTRIES = 10000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB uncompressed
MAX_ENTRY_BYTES = 512 * 1024 * 1024


def is_convertible(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in CONVERTIBLE_EXTENSIONS


def _safe_relative(name: str) -> Optional[str]:
    text = str(name or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _iter_zip(path: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    with zipfile.ZipFile(path, "r") as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = _safe_relative(info.filename)
            if not relative:
                raise ValueError(f"unsafe archive entry name: {info.filename!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError(f"symlink archive entry rejected: {info.filename!r}")
            if info.file_size > MAX_ENTRY_BYTES:
                raise ValueError(f"archive entry too large: {info.filename!r}")
            data = archive.read(info)
            entries.append({"relative": relative, "size": len(data), "data": data})
    return entries


def _iter_7z(path: str) -> List[Dict[str, Any]]:
    try:
        import py7zr  # type: ignore
    except ImportError as exc:
        raise RuntimeError("7z support is not installed (py7zr missing)") from exc
    entries: List[Dict[str, Any]] = []
    archive = py7zr.SevenZipFile(path, "r")
    try:
        for item in archive.list() or []:
            name = getattr(item, "filename", "")
            if getattr(item, "is_directory", False):
                continue
            relative = _safe_relative(name)
            if not relative:
                raise ValueError(f"unsafe archive entry name: {name!r}")
            size = int(getattr(item, "uncompressed", 0) or 0)
            if size > MAX_ENTRY_BYTES:
                raise ValueError(f"archive entry too large: {name!r}")
            entries.append({"relative": relative, "size": size})
        if not entries:
            return []
        # py7zr extracts by name; read all into memory one by one through its API.
        archive.reset()
        extracted = archive.readall() or {}
        for entry in entries:
            key = next((key for key in extracted if _safe_relative(key) == entry["relative"]), None)
            entry["data"] = extracted[key].read() if key and hasattr(extracted[key], "read") else b""
        return entries
    finally:
        archive.close()


def _iter_rar(path: str) -> List[Dict[str, Any]]:
    try:
        import rarfile  # type: ignore
    except ImportError as exc:
        raise RuntimeError("RAR/CBR support is not installed (rarfile missing)") from exc
    entries: List[Dict[str, Any]] = []
    with rarfile.RarFile(path) as archive:
        for info in archive.infolist():
            if info.isdir():
                continue
            relative = _safe_relative(info.filename)
            if not relative:
                raise ValueError(f"unsafe archive entry name: {info.filename!r}")
            if int(getattr(info, "file_size", 0) or 0) > MAX_ENTRY_BYTES:
                raise ValueError(f"archive entry too large: {info.filename!r}")
            data = archive.open(info).read()
            entries.append({"relative": relative, "size": len(data), "data": data})
    return entries


def _write_entries(entries: List[Dict[str, Any]], target: str) -> Dict[str, Any]:
    total = 0
    count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for entry in entries:
            total += int(entry.get("size") or 0)
            count += 1
            if count > MAX_ENTRIES:
                raise ValueError(f"archive has more than {MAX_ENTRIES} entries")
            if total > MAX_TOTAL_BYTES:
                raise ValueError("archive exceeds the maximum uncompressed size")
            if "open" in entry:
                with entry["open"]() as source:
                    out.writestr(entry["relative"], source.read())
            elif "reader" in entry:
                with entry["reader"]() as source:
                    out.writestr(entry["relative"], source.read())
            else:
                out.writestr(entry["relative"], entry.get("data") or b"")
    with zipfile.ZipFile(target, "r") as check:
        if check.testzip() is not None:
            raise ValueError("converted archive failed ZIP validation")
    return {"entries": count, "bytes": total}


def convert_archive_to_cbz(path: str, target: Optional[str] = None) -> Dict[str, Any]:
    """Convert one archive to CBZ atomically; returns structured success/error.

    The original file is deleted only after the converted ZIP validates.
    """
    source = os.path.abspath(str(path or ""))
    if not os.path.isfile(source) or os.path.islink(source):
        return {"status": "error", "reason": "source archive not found or is a symlink"}
    ext = os.path.splitext(source)[1].lower()
    if ext not in CONVERTIBLE_EXTENSIONS:
        return {"status": "error", "reason": f"unsupported archive extension {ext or '(none)'}"}
    destination = target or (os.path.splitext(source)[0] + ".cbz")
    if os.path.abspath(destination) == source:
        return {"status": "error", "reason": "target would overwrite the source"}
    if os.path.exists(destination):
        return {"status": "error", "reason": "target CBZ already exists"}
    directory = os.path.dirname(destination) or "."
    temp_path = ""
    try:
        entries: List[Dict[str, Any]]
        if ext in (".zip",):
            entries = _iter_zip(source)
        elif ext in (".7z", ".cb7"):
            entries = _iter_7z(source)
        else:
            entries = _iter_rar(source)
        if not entries:
            return {"status": "error", "reason": "archive contains no files"}
        fd, temp_path = tempfile.mkstemp(prefix=".mangatrax-convert-", suffix=".cbz.tmp", dir=directory)
        os.close(fd)
        stats = _write_entries(entries, temp_path)
        os.replace(temp_path, destination)
        temp_path = ""
        try:
            os.remove(source)
        except OSError as exc:
            logger.warning("Converted archive kept source (could not delete %s): %s", source, exc)
        logger.info("Converted archive %s -> %s (%s entries)", source, destination, stats["entries"])
        return {"status": "success", "source": source, "target": destination,
                "entries": stats["entries"], "bytes": stats["bytes"]}
    except Exception as exc:
        logger.warning("Archive conversion failed for %s: %s", source, exc)
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}", "source": source}
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
