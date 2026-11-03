"""Non-throwing storage writability diagnostics (task-28).

Two folders matter for the download pipeline: ``downloads_dir`` (raw engine output)
and ``staging_dir`` (tagged output ready for manual Calibre import).  Both are usually
bind mounts in the container; if the host mount is owned by a different uid/gid the app
fails later with ``[Errno 13] Permission denied`` in the middle of a routing pass.  This
module probes them up front with a real temp-file create/delete and formats an
actionable hint (path, uid, errno, PUID/PGID) for logs, /health and the UI.
"""

import errno as errno_module
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("mangatrax.storage")

_WRITE_ERROR_CODES = {
    errno_module.EACCES,
    errno_module.EPERM,
    errno_module.EROFS,
    errno_module.ENOSPC,
    errno_module.EDQUOT,
}


def probe_path(path: str) -> Dict[str, Any]:
    """Probe one directory for existence/type/writability.  Never raises.

    Returns ``{path, exists, is_dir, writable, uid, gid, errno, error, free_bytes}``.
    Writability is proven by creating and deleting a temp file inside the directory.
    """
    result: Dict[str, Any] = {
        "path": str(path or ""),
        "exists": False,
        "is_dir": False,
        "writable": False,
        "uid": None,
        "gid": None,
        "errno": None,
        "error": "",
        "free_bytes": None,
    }
    # Best-effort process uid/gid even when the path is missing/not stat-able.
    try:
        if hasattr(os, "getuid"):
            result["uid"] = os.getuid()
        if hasattr(os, "getgid"):
            result["gid"] = os.getgid()
    except OSError:
        pass
    if not result["path"]:
        result["error"] = "path is not configured"
        return result
    try:
        result["exists"] = os.path.exists(result["path"])
        result["is_dir"] = os.path.isdir(result["path"])
        if not result["exists"]:
            result["error"] = "path does not exist"
            return result
        if not result["is_dir"]:
            result["error"] = "path exists but is not a directory"
            return result
        try:
            stat = os.stat(result["path"])
            result["uid"] = getattr(stat, "st_uid", None)
            result["gid"] = getattr(stat, "st_gid", None)
        except OSError:
            pass
        try:
            result["free_bytes"] = int(shutil.disk_usage(result["path"]).free)
        except OSError:
            pass
        handle = None
        temp_name = ""
        try:
            handle, temp_name = tempfile.mkstemp(prefix=".mangatrax-write-test-", dir=result["path"])
            os.write(handle, b"ok")
        finally:
            if handle is not None:
                try:
                    os.close(handle)
                except OSError:
                    pass
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
        result["writable"] = True
        return result
    except OSError as exc:
        result["errno"] = getattr(exc, "errno", None)
        result["error"] = getattr(exc, "strerror", None) or str(exc)
    except Exception as exc:  # pragma: no cover - defensive: diagnostics must never raise
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def probe_storage_paths(paths: Dict[str, str]) -> Dict[str, Any]:
    """Probe every ``label -> path`` entry, returning ``label -> probe result``."""
    return {str(label): probe_path(path) for label, path in (paths or {}).items()}


def build_storage_health(paths: Dict[str, str]) -> Dict[str, Any]:
    """Probe the configured paths and return a JSON-safe health block for settings/UI."""
    probes = probe_storage_paths(paths)
    health: Dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": all(bool(probe.get("writable")) for probe in probes.values()),
        "paths": probes,
    }
    # Convenience keys for the two well-known folders (health/UI/templates).
    for label, probe in probes.items():
        health[label] = probe
    return health


def _probe_for_hint(path: str, exc: Optional[BaseException] = None) -> Dict[str, Any]:
    probe = probe_path(path)
    if isinstance(exc, OSError) and getattr(exc, "errno", None):
        # A transient/other errno (ENOSPC etc.) matters even when the temp probe worked.
        probe["errno"] = probe.get("errno") or getattr(exc, "errno", None)
        probe["error"] = probe.get("error") or (getattr(exc, "strerror", None) or str(exc))
    return probe


def format_hint(path: str, exc: Optional[BaseException] = None) -> str:
    """Actionable one-line hint for a failed move/route/write.

    Example: ``storage path '/staging' is not writable by uid 568: Permission denied
    (errno 13); check mount ownership/ACL (PUID/PGID)``.  Returns ``""`` when the path
    probes writable and the exception carries no filesystem errno.
    """
    probe = _probe_for_hint(path, exc)
    errno_value = probe.get("errno")
    error = str(probe.get("error") or "").strip()
    uid = probe.get("uid")
    if probe.get("writable") and not errno_value:
        return ""
    parts = [f"storage path '{probe.get('path') or path}'"]
    if uid is not None:
        parts.append(f"is not writable by uid {uid}" if not probe.get("writable") else f"(uid {uid})")
    if error:
        parts.append(f": {error}")
    if errno_value is not None:
        code = errno_module.errorcode.get(errno_value, "")
        parts.append(f" (errno {errno_value}{' ' + code if code else ''})")
    free_bytes = probe.get("free_bytes")
    if errno_value in (errno_module.ENOSPC, errno_module.EDQUOT) and free_bytes is not None:
        parts.append(f"; free space: {free_bytes / (1024 ** 3):.2f} GiB")
    parts.append("; check mount ownership/ACL (PUID/PGID)")
    return "".join(parts)


def warning_message(label: str, probe: Dict[str, Any]) -> str:
    """Warning used at startup/settings-save for one unwritable folder."""
    errno_value = probe.get("errno")
    error = str(probe.get("error") or "").strip()
    if errno_value:
        reason = f"errno {errno_value} {error}".strip()
    else:
        # Missing folder / not a directory: no errno, but the reason is known.
        reason = error or "path does not exist"
    return (
        f"Storage check: '{probe.get('path')}' ({label}) is NOT writable by uid "
        f"{probe.get('uid')} ({reason}); "
        "move/route operations will fail. Fix the host mount ownership/ACL (PUID/PGID)."
    )
