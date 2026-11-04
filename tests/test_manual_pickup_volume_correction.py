#!/usr/bin/env python3
"""Manual Pickup volume corrections must actually drive Retry-with-corrections.

Regression: "Edit volumes" wrote the release's volume columns and the pickup's
``wanted_json``, but left ``download_wanted_json`` stale.  ``retry_manual_pickup_strict``
resolved the wanted list via ``resolve_release_missing_volumes``, which prefers
``download_wanted_json`` — so the user's correction was silently ignored and the old
(possibly wrong) volumes were fetched again.
"""

import json
import os
import sqlite3
import sys
import uuid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "tests"))

import e2e.harness as H  # noqa: E402 - installs FastAPI/Jinja shims + temp DATA_DIR
from app import main as M  # noqa: E402


def _call(coro):
    resp = H.run_coroutine_sync(coro)
    if hasattr(resp, "json"):
        try:
            return resp.json()
        except Exception:
            pass
    return json.loads(getattr(resp, "content", b"{}").decode())


def _seed_release_and_pickup():
    con = sqlite3.connect(M.db.db_path)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO releases (guid,title,series_name,download_url,link,status,download_status,downloader,"
        "volumes_json,missing_vols_json,download_wanted_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "pickup-corr-" + uuid.uuid4().hex, "Test Series v01-05", "Test Series", "https://example.invalid/t.torrent",
            "https://example.invalid/p", "REQUESTED", "MANUAL_PICKUP", "torbox",
            json.dumps([1, 2, 3, 4, 5]), json.dumps([1, 2, 3, 4, 5]), json.dumps([1, 2, 3, 4, 5]),
        ),
    )
    con.commit()
    release_id = cur.lastrowid
    con.close()

    files = [
        {"id": 10 + i, "name": f"Test Series - Part 02 v{i:02d}.cbz", "size": 1024,
         "suggested_volume": float(i), "volumes": [float(i)], "mapping_source": "parsed", "fetched": False}
        for i in range(1, 6)
    ]
    pickup_id = M.db.create_torbox_manual_pickup(
        release_id, "999", "Test Series v01-05", "Test Series", files, [1, 2, 3, 4, 5],
        "strict mapping failed (wanted volumes absent)",
    )
    return release_id, pickup_id


def test_volume_correction_propagates_to_wanted_and_retry():
    release_id, pickup_id = _seed_release_and_pickup()

    response = _call(M.api_manual_pickup_volumes(pickup_id, {"volumes": [2, 4]}))
    assert response.get("status") == "success", response
    assert response.get("wanted_volumes") == [2.0, 4.0], response

    release = M.db.get_release_by_id(release_id)
    assert json.loads(release.get("volumes_json") or "[]") == [2.0, 4.0], release.get("volumes_json")
    assert json.loads(release.get("missing_vols_json") or "[]") == [2.0, 4.0], release.get("missing_vols_json")
    # The strict-selection resolver prefers this column; it MUST be corrected too.
    assert json.loads(release.get("download_wanted_json") or "[]") == [2.0, 4.0], release.get("download_wanted_json")
    pickup = M.db.get_torbox_manual_pickup(pickup_id)
    assert json.loads(pickup.get("wanted_json") or "[]") == [2.0, 4.0], pickup.get("wanted_json")

    # Retry must plan against the corrected volumes (files v02 and v04 -> indexes 1,3).
    captured = {}
    original = M.scheduler._fetch_manual_pickup_files

    def _capture(pickup_id_arg, selected_indexes, actor="user", progress=None):
        captured["indexes"] = list(selected_indexes or [])
        return {"status": "success", "pickup_id": pickup_id_arg, "fetched": captured["indexes"]}

    M.scheduler._fetch_manual_pickup_files = _capture
    try:
        result = M.scheduler.retry_manual_pickup_strict(pickup_id)
    finally:
        M.scheduler._fetch_manual_pickup_files = original
    assert result.get("status") == "success", result
    assert captured.get("indexes") == [1, 3], f"retry used stale volumes: {captured}"

    # resolve_release_missing_volumes now reports the correction, not the old list.
    resolved = M.scheduler.resolve_release_missing_volumes(M.db.get_release_by_id(release_id))
    assert resolved == [2.0, 4.0], resolved
    print("    [PASSED] edited volumes propagate to download_wanted_json, pickup wanted, and retry planning.")


def test_volume_correction_rejects_empty():
    _, pickup_id = _seed_release_and_pickup()
    try:
        M.scheduler.update_manual_pickup_release_volumes(pickup_id, [])
    except ValueError as exc:
        assert "volume" in str(exc).lower()
    else:
        raise AssertionError("empty volume correction must be rejected")
    print("    [PASSED] empty volume corrections are rejected.")


def run():
    test_volume_correction_propagates_to_wanted_and_retry()
    test_volume_correction_rejects_empty()
    print("ALL MANUAL PICKUP VOLUME CORRECTION TESTS PASSED")


if __name__ == "__main__":
    run()
