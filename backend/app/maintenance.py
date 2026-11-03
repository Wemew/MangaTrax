"""Safe maintenance / clear actions (task-13).

MangaTrax's Settings page historically hid *all* maintenance buttons behind
``MANGATRAX_DEBUG_ACTIONS=1``.  That flag exists to protect genuinely destructive
or rare operations (``purge-non-manga`` deletes rows, ``normalize-english-titles``
rewrites series keys, ``reset-all-data`` wipes everything), so it must stay opt-in.
The *safe* cleanups are different: they only drop re-fetchable caches or unreviewed
Discovery rows and can never touch tracked series, wishlist requests or the Calibre
library.  Those live here and are served by ``POST /api/maintenance/clear``, which
is behind the normal auth/CSRF middleware but NOT behind the debug flag.

This module is deliberately free of FastAPI imports: the endpoint validates the
payload with a strict Pydantic model and then delegates here, so the actions can be
unit-tested without an HTTP stack.
"""

from typing import Any, Callable, Dict, Tuple

MAINTENANCE_EVENT = "MAINTENANCE_CLEARED"


class UnknownMaintenanceAction(ValueError):
    """Raised when a caller asks for an action that is not a safe maintenance one."""


#: Safe actions exposed by the UI and ``POST /api/maintenance/clear``.  Everything
#: destructive stays in ``/api/debug/action`` behind ``MANGATRAX_DEBUG_ACTIONS``.
SAFE_ACTIONS: Dict[str, Dict[str, str]] = {
    "clear-cover-cache": {
        "title": "Purge Cover Cache",
        "description": "Deletes cached cover thumbnails from disk. Images are re-downloaded safely when viewed.",
        "icon": "image",
    },
    "clear-discovery": {
        "title": "Clear Unreviewed Discovery",
        "description": "Removes unreviewed Discovery entries. Wishlist requests, ignored releases and Calibre tracked series are untouched.",
        "icon": "compass",
    },
    "clear-descriptions": {
        "title": "Reset Descriptions",
        "description": "Blanks stored descriptions/synopses so providers can re-fetch them later. No releases or series are deleted.",
        "icon": "file-text",
    },
}


def _clear_cover_cache(db, cover_cache) -> Tuple[int, str]:
    count = cover_cache.clear_cache()
    return count, f"Purged {count} cached cover image(s) from disk."


def _clear_discovery(db, cover_cache) -> Tuple[int, str]:
    count = db.clear_discovered_series()
    return count, f"Cleared {count} unreviewed manga series from Discovery."


def _clear_descriptions(db, cover_cache) -> Tuple[int, str]:
    count = db.clear_all_descriptions()
    return count, f"Cleared descriptions across {count} item(s)."


_HANDLERS: Dict[str, Callable[[Any, Any], Tuple[int, str]]] = {
    "clear-cover-cache": _clear_cover_cache,
    "clear-discovery": _clear_discovery,
    "clear-descriptions": _clear_descriptions,
}


def is_safe_action(action: str) -> bool:
    """True when *action* is a safe maintenance action (never debug-gated)."""
    return action in SAFE_ACTIONS


def perform(action: str, db, cover_cache) -> Dict[str, Any]:
    """Run a safe maintenance action and audit it.

    Returns ``{"status": "success", "message": ..., "action": ..., "count": ...}``.
    Raises :class:`UnknownMaintenanceAction` for anything that is not in
    :data:`SAFE_ACTIONS` (destructive actions must go through ``/api/debug/action``).

    The audit row is written via :meth:`MangaDatabase.log_activity`, which resolves
    the authenticated actor from the request-scoped ContextVar (``system`` when no
    actor is bound, e.g. CLI use), so "who cleared what" is always answerable.
    """
    handler = _HANDLERS.get(action)
    if handler is None:
        raise UnknownMaintenanceAction(
            f"Unknown maintenance action: {action!r}. Allowed: {', '.join(sorted(SAFE_ACTIONS))}."
        )
    count, message = handler(db, cover_cache)
    db.log_activity(
        MAINTENANCE_EVENT,
        f"Maintenance action '{action}' executed",
        f"action={action}; count={count}",
    )
    return {
        "status": "success",
        "message": message,
        "action": action,
        "count": count,
    }
