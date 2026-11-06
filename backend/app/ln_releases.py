"""Light-novel release audit/cleanup (task-70).

Flagged rows are hidden from normal lists; this read-only audit + explicit cleanup
gives the user a way to see legacy rows and ignore/delete them.  Cleanup never runs
automatically and never deletes files from disk.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger("mangatrax.ln_releases")


def audit_ln_releases(db, limit: int = 500) -> Dict[str, Any]:
    with db.get_connection() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT id, title, series_name, status, created_at, volume_start "
            "FROM releases WHERE COALESCE(is_light_novel, 0) = 1 "
            "ORDER BY id DESC LIMIT ?;",
            (max(1, int(limit)),),
        ).fetchall()]
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM releases WHERE COALESCE(is_light_novel, 0) = 1;"
        ).fetchone()
    return {"status": "success", "releases": rows, "count": int(total["c"] if total else 0)}


def cleanup_ln_releases(db, release_ids: List[int], action: str, actor: str = "user") -> Dict[str, Any]:
    action = str(action or "").strip().lower()
    if action not in ("ignore", "delete"):
        raise ValueError("action must be 'ignore' or 'delete'")
    ids = [int(value) for value in (release_ids or [])]
    changed = skipped = 0
    for release_id in ids:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT id, title, series_name, COALESCE(is_light_novel, 0) AS ln "
                "FROM releases WHERE id = ?;", (release_id,),
            ).fetchone()
            if not row or not int(row["ln"] or 0):
                skipped += 1
                continue
            if action == "ignore":
                conn.execute(
                    "UPDATE releases SET status = 'IGNORED', updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (release_id,),
                )
            else:
                conn.execute("DELETE FROM releases WHERE id = ?;", (release_id,))
            conn.commit()
        changed += 1
        try:
            db.log_activity(
                "LIGHT_NOVEL_RELEASE_" + ("IGNORED" if action == "ignore" else "DELETED"),
                f"{'Ignored' if action == 'ignore' else 'Deleted'} light-novel release #{release_id} "
                f"'{row['title']}'",
                json.dumps({"series": row["series_name"], "action": action}),
                actor=actor,
            )
        except Exception:
            pass
    try:
        db._invalidate_grouped_cache()
    except Exception:
        pass
    return {"status": "success", "action": action, "changed": changed, "skipped": skipped}
