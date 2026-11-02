"""
tests/test_status_precedence.py
Regression suite for the release-status precedence model (P1 data-loss fix).

THE BUG (pre-fix):  every automated re-upsert of a release overwrote a status that
had been set deliberately.  ``upsert_release()`` used

    status = CASE WHEN status = 'IGNORED' THEN status ELSE COALESCE(?, status) END

so only IGNORED was shielded.  A background feed re-poll (or a Calibre sync) that
recomputed a release as DISCOVERY/NEW silently reverted REQUESTED / IN_CALIBRE /
BLACKLISTED releases, which lost tracked requests within one feed interval.

THE MODEL:  ``app.database.resolve_status(existing, incoming)`` is the single
source of truth for automated status writes:
  * an incoming blank/None status NEVER clears an existing status;
  * RECOMPUTABLE statuses (DISCOVERY, NEW) are owned by automation and are
    freely recomputed when the stored value is also recomputable/unset;
  * STICKY statuses (REQUESTED, IN_CALIBRE, IGNORED, BLACKLISTED) are never
    downgraded to a recomputable status by automation;
  * the user-veto tier (IGNORED, BLACKLISTED) is never changed by an automated
    re-upsert at all -- only an explicit user action (status endpoint, unignore,
    unblacklist, resume) may lift it;
  * deliberate API transitions still write verbatim via update_release_status().

Note: ``calibre-debug.exe -e`` executes with optimise=2, so bare ``assert`` is
compiled out.  Every check below goes through the explicit ``_check`` helper.

Run with:
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e tests\\test_status_precedence.py
"""

import os
import sys
import json
import sqlite3
import tempfile
import shutil
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.database import MangaDatabase, resolve_status
from app.covers import CoverCache
from app.scheduler import MangaScheduler
from app.providers.base import ReleaseItem

PASSED = []


def _check(cond, msg=None):
    if not cond:
        raise AssertionError(msg if msg is not None else "check failed")


def _write_calibre_library(db_path, series_map):
    """Writes a minimal, scanner-complete Calibre metadata.db from {series_name: [volumes]}."""
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, sort TEXT,
            author_sort TEXT, series_index REAL DEFAULT 1.0, path TEXT DEFAULT '', has_cover INTEGER DEFAULT 0);
        CREATE TABLE series (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, series INTEGER NOT NULL);
        CREATE TABLE authors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT, link TEXT DEFAULT '');
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, author INTEGER NOT NULL);
        CREATE TABLE publishers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, sort TEXT);
        CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, publisher INTEGER NOT NULL);
        CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, tag INTEGER NOT NULL);
    """)
    for sid, (name, vols) in enumerate(series_map.items(), start=1):
        cur.execute("INSERT INTO series (id, name) VALUES (?, ?);", (sid, name))
        cur.execute("INSERT OR IGNORE INTO authors (id, name) VALUES (?, ?);", (sid, "Author"))
        cur.execute("INSERT OR IGNORE INTO publishers (id, name) VALUES (?, ?);", (sid, "Publisher"))
        for v in vols:
            cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"{name} Vol. {int(v)}", float(v)))
            bid = cur.lastrowid
            cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?);", (bid, sid))
            cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, ?);", (bid, sid))
            cur.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, ?);", (bid, sid))
    conn.commit()
    conn.close()


class _Env:
    """Hermetic DB + scheduler environment with one deterministic mocked feed."""

    def __init__(self, prefix, calibre_map=None):
        self.temp_dir = tempfile.mkdtemp(prefix=prefix)
        self.db_file = os.path.join(self.temp_dir, "mangatrax.db")
        self.calibre_db = os.path.join(self.temp_dir, "metadata.db")
        _write_calibre_library(self.calibre_db, calibre_map or {})

        self.db = MangaDatabase(self.db_file)
        self.db.set_setting("calibre_path", self.calibre_db)
        self.db.set_setting("staging_dir", os.path.join(self.temp_dir, "staging"))
        self.db.set_setting("qbit_enabled", "false")
        self.db.set_setting("auto_download", "true")

        self.cover_cache = CoverCache(self.temp_dir)
        self.sched = MangaScheduler(self.db, self.cover_cache)
        self.anilist_map = {}

        def _resolve(raw):
            canonical = self.anilist_map.get(raw, raw)
            return {"canonical_title": canonical, "romaji_title": raw, "english_title": canonical}

        self.sched.anilist.resolve_manga_series = MagicMock(side_effect=_resolve)
        self.sched.mangaupdates.get_series_info = MagicMock(return_value=None)

        self.qbit = MagicMock()
        self.qbit.add_torrent.return_value = True
        self.qbit.extract_torrent_hash.return_value = "deadbeefcafe0001"
        self.qbit.apply_selective_volume_download.return_value = {
            "status": "filtered", "skipped_volumes": [1.0], "kept_volumes": [2.0, 3.0]}
        self.sched.get_qbit_client = MagicMock(return_value=self.qbit)

        # Exactly one deterministic feed.
        for seeded in self.db.get_all_feeds():
            self.db.delete_feed(seeded["id"])
        self.db.add_feed(name="Test Feed", feed_type="nyaa",
                         url="https://nyaa.si/?page=rss", interval=60, auto_download=True)

        self.provider = MagicMock()
        self.provider.fetch_releases.return_value = []

    def set_releases(self, items):
        self.provider.fetch_releases.return_value = list(items)

    def set_calibre(self, series_map):
        _write_calibre_library(self.calibre_db, series_map)

    def poll(self):
        with patch("app.scheduler.create_provider", return_value=self.provider):
            return self.sched.check_all_feeds()

    def close(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def _release(guid, title, series_name, volumes=None, start=None, end=None,
             is_bundle=False, download_url=None, is_complete=False):
    return ReleaseItem(
        guid=guid, title=title, series_name=series_name, release_group="Group",
        is_bundle=is_bundle, is_complete=is_complete,
        volume_start=start, volume_end=end, volumes=volumes or [],
        download_url=download_url or f"magnet:?xt=urn:btih:{guid}",
        link=f"https://nyaa.si/view/{guid}")


def _only_release_id(env):
    rows = env.db.get_releases(limit=100)
    _check(len(rows) == 1, f"expected exactly one active release, got {len(rows)}")
    return rows[0]["id"]


def _sole_release_id_all_statuses(env):
    """ID of the only release row, regardless of status (BLACKLISTED/IGNORED are filtered out of get_releases)."""
    with env.db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM releases ORDER BY id;")
        ids = [row["id"] for row in cur.fetchall()]
    _check(len(ids) == 1, f"expected exactly one release row, got {len(ids)}")
    return ids[0]


def _status(env, rel_id):
    rel = env.db.get_release_by_id(rel_id)
    _check(rel is not None, f"release {rel_id} disappeared")
    return rel["status"]


# ===========================================================================
# Unit-level: the precedence matrix
# ===========================================================================
def test_resolve_status_matrix():
    print("--> Testing resolve_status() precedence matrix...")

    # Incoming blank/None must NEVER clear an existing status.
    for blank in (None, "", "   "):
        for existing in ("REQUESTED", "IGNORED", "BLACKLISTED", "IN_CALIBRE", "DISCOVERY", "NEW"):
            _check(resolve_status(existing, blank) == existing,
                   f"blank incoming {blank!r} cleared {existing!r}")

    # Unset existing adopts the incoming status.
    _check(resolve_status(None, "DISCOVERY") == "DISCOVERY")
    _check(resolve_status("", "NEW") == "NEW")
    _check(resolve_status("  ", "REQUESTED") == "REQUESTED")

    # STICKY is never downgraded to a RECOMPUTABLE classification.
    for sticky in ("REQUESTED", "IGNORED", "BLACKLISTED", "IN_CALIBRE"):
        for recomputable in ("DISCOVERY", "NEW"):
            _check(resolve_status(sticky, recomputable) == sticky,
                   f"automation clobbered {sticky!r} with {recomputable!r}")

    # RECOMPUTABLE states are owned by automation and freely recomputed.
    _check(resolve_status("DISCOVERY", "NEW") == "NEW")
    _check(resolve_status("NEW", "DISCOVERY") == "DISCOVERY")
    _check(resolve_status("DISCOVERY", "DISCOVERY") == "DISCOVERY")

    # Legitimate ownership/request transitions (M1 Feature 7/8 behavior).
    _check(resolve_status("DISCOVERY", "REQUESTED") == "REQUESTED")
    _check(resolve_status("NEW", "REQUESTED") == "REQUESTED")
    _check(resolve_status("REQUESTED", "IN_CALIBRE") == "IN_CALIBRE")
    _check(resolve_status("IN_CALIBRE", "REQUESTED") == "REQUESTED")

    # User-veto tier is absolute against automated re-upserts.
    for veto in ("IGNORED", "BLACKLISTED"):
        for incoming in ("DISCOVERY", "NEW", "REQUESTED", "IN_CALIBRE"):
            _check(resolve_status(veto, incoming) == veto,
                   f"user-veto {veto!r} was clobbered by {incoming!r}")

    # Normalisation: case/whitespace tolerant.
    _check(resolve_status(" requested ", "discovery") == "REQUESTED")
    _check(resolve_status("Discovery", "requested") == "REQUESTED")

    print("    [PASSED] resolve_status() precedence matrix.")


# ===========================================================================
# Acceptance 1: track -> check_all_feeds() -> still REQUESTED + pending
# ===========================================================================
def test_acceptance_1_track_survives_feed_poll():
    print("--> Testing acceptance 1: tracked request survives a feed poll...")
    env = _Env("mangatrax_prec_a1_")
    try:
        env.set_releases([_release("guid-a1", "[Group] Dandadan Vol. 01 [Digital]",
                                   "Dandadan", volumes=[1.0], start=1.0, end=1.0)])
        res1 = env.poll()
        _check(res1["requested"] == 0, f"expected discovery poll, got {res1}")
        rel_id = _only_release_id(env)
        _check(_status(env, rel_id) == "DISCOVERY", f"expected DISCOVERY, got {_status(env, rel_id)}")

        # POST /api/discovery/track {series_name, release_id} -> database.py:track_discovered_series
        env.db.track_discovered_series("Dandadan", rel_id)
        _check(_status(env, rel_id) == "REQUESTED", "track did not set REQUESTED")
        _check(len(env.db.get_pending_requests()) == 1, "tracked request not pending")

        # One background feed re-poll must NOT undo the user's tracking decision.
        res2 = env.poll()
        _check(_status(env, rel_id) == "REQUESTED",
               f"REGRESSION: feed poll reverted tracked release to {_status(env, rel_id)}")
        _check(len(env.db.get_pending_requests()) == 1,
               f"REGRESSION: pending request count fell to {len(env.db.get_pending_requests())}")
        _check(res2["releases_found"] == 1)
        print("    [PASSED] acceptance 1.")
    finally:
        env.close()


# ===========================================================================
# Acceptance 2: manual POST /status REQUESTED survives a feed poll
# ===========================================================================
def test_acceptance_2_manual_status_survives_feed_poll():
    print("--> Testing acceptance 2: manual status endpoint survives a feed poll...")
    env = _Env("mangatrax_prec_a2_")
    try:
        env.set_releases([_release("guid-a2", "[Group] Dandadan Vol. 01 [Digital]",
                                   "Dandadan", volumes=[1.0], start=1.0, end=1.0)])
        env.poll()
        rel_id = _only_release_id(env)
        _check(_status(env, rel_id) == "DISCOVERY")

        # main.py:641 -> POST /api/releases/{id}/status {status: REQUESTED}
        env.db.update_release_status(rel_id, "REQUESTED")
        _check(_status(env, rel_id) == "REQUESTED")

        env.poll()
        _check(_status(env, rel_id) == "REQUESTED",
               f"REGRESSION: feed poll reverted manual status to {_status(env, rel_id)}")
        _check(len(env.db.get_pending_requests()) == 1)
        print("    [PASSED] acceptance 2.")
    finally:
        env.close()


# ===========================================================================
# Acceptance 3: IGNORED survives a feed poll (existing M1 shield preserved)
# ===========================================================================
def test_acceptance_3_ignored_survives_feed_poll():
    print("--> Testing acceptance 3: IGNORED survives a feed poll...")
    env = _Env("mangatrax_prec_a3_")
    try:
        env.set_releases([_release("guid-a3", "[Group] Dandadan Vol. 01 [Digital]",
                                   "Dandadan", volumes=[1.0], start=1.0, end=1.0)])
        env.poll()
        rel_id = _only_release_id(env)

        # POST /api/releases/{id}/ignore
        env.db.update_release_status(rel_id, "IGNORED")
        env.poll()
        _check(_status(env, rel_id) == "IGNORED",
               f"REGRESSION: feed poll reverted IGNORED to {_status(env, rel_id)}")

        # Full automated assault matrix over an IGNORED release (upsert layer).
        for i, incoming in enumerate(["DISCOVERY", "NEW", "REQUESTED", "IN_CALIBRE", "BLACKLISTED", None]):
            env.db.upsert_release({
                "guid": "guid-a3", "title": f"Shield Hero Vol. 01 [Attack {i}]",
                "series_name": "Dandadan", "status": incoming})
            _check(_status(env, rel_id) == "IGNORED",
                   f"user-veto IGNORED was clobbered by automated upsert {incoming!r}")
        print("    [PASSED] acceptance 3.")
    finally:
        env.close()


# ===========================================================================
# Acceptance 4: BLACKLISTED survives a feed poll despite canonical/blacklist
#               series-name divergence
# ===========================================================================
def test_acceptance_4_blacklisted_survives_name_divergence():
    print("--> Testing acceptance 4: BLACKLISTED survives name-divergent feed poll...")
    env = _Env("mangatrax_prec_a4_")
    try:
        env.db.add_to_blacklist("Shingeki no Kyojin", "Dropped series")
        env.set_releases([_release("guid-a4", "[Group] Shingeki no Kyojin Vol. 01 [Digital]",
                                   "Shingeki no Kyojin", volumes=[1.0], start=1.0, end=1.0)])
        # Poll 1: AniList has no canonical English title yet -> blacklist matches -> BLACKLISTED.
        env.poll()
        rel_id = _sole_release_id_all_statuses(env)
        _check(_status(env, rel_id) == "BLACKLISTED",
               f"expected BLACKLISTED after blacklisted poll, got {_status(env, rel_id)}")

        # Poll 2: metadata resolution now yields canonical English "Attack on Titan".
        # Under the P3a naming contract the STABLE matching key must not be rewritten:
        # releases.series_name stays "Shingeki no Kyojin" while the display title
        # becomes the AniList canonical one.  The stored BLACKLISTED status must still
        # win, because the stable key still matches the blacklist entry.
        env.anilist_map["Shingeki no Kyojin"] = "Attack on Titan"
        env.poll()
        _check(env.db.get_release_by_id(rel_id)["series_name"] == "Shingeki no Kyojin",
               "REGRESSION: feed poll renamed the stable matching key to the AniList display title")
        _check(_status(env, rel_id) == "BLACKLISTED",
               f"REGRESSION: BLACKLISTED reverted to {_status(env, rel_id)} on name divergence")
        _check(env.db.is_series_blacklisted("Shingeki no Kyojin"))
        print("    [PASSED] acceptance 4.")
    finally:
        env.close()


# ===========================================================================
# Acceptance 5: IN_CALIBRE survives a feed poll
# ===========================================================================
def test_acceptance_5_in_calibre_survives_feed_poll():
    print("--> Testing acceptance 5: IN_CALIBRE survives a feed poll...")
    env = _Env("mangatrax_prec_a5_")
    try:
        env.set_releases([_release("guid-a5", "[Group] Dandadan Vol. 01 [Digital]",
                                   "Dandadan", volumes=[1.0], start=1.0, end=1.0)])
        env.poll()
        rel_id = _only_release_id(env)

        # POST /api/releases/{id}/status {status: IN_CALIBRE} (manually marked acquired).
        env.db.update_release_status(rel_id, "IN_CALIBRE")
        env.poll()
        _check(_status(env, rel_id) == "IN_CALIBRE",
               f"REGRESSION: feed poll reverted IN_CALIBRE to {_status(env, rel_id)}")

        # Upsert-layer matrix: IN_CALIBRE must not be downgraded to a classification.
        for incoming in ("DISCOVERY", "NEW"):
            env.db.upsert_release({
                "guid": "guid-a5", "title": f"Dandadan Vol. 01 [{incoming}]",
                "series_name": "Dandadan", "status": incoming})
            _check(_status(env, rel_id) == "IN_CALIBRE",
                   f"IN_CALIBRE was downgraded to {incoming!r}")
        print("    [PASSED] acceptance 5.")
    finally:
        env.close()


# ===========================================================================
# Acceptance 5b: Calibre-sync reconciliation must not clobber REQUESTED
# ===========================================================================
def test_acceptance_5b_calibre_sync_does_not_clobber_requested():
    print("--> Testing acceptance 5b: Calibre sync reconcile must not clobber REQUESTED...")
    env = _Env("mangatrax_prec_a5b_")
    try:
        _, rel_id = env.db.upsert_release({
            "guid": "guid-a5b", "title": "Dandadan Vol. 01",
            "series_name": "Dandadan", "is_bundle": False,
            "volume_start": 1.0, "volumes": [1.0], "status": "REQUESTED"})
        _check(_status(env, rel_id) == "REQUESTED")

        # The reconcile path (scheduler.py) recomputes DISCOVERY for a series that is
        # absent from Calibre; the request must not be lost.  update_release_volumes_and_status
        # is the automation writer used there.
        env.db.update_release_volumes_and_status(rel_id, [1.0], [1.0], "DISCOVERY")
        _check(_status(env, rel_id) == "REQUESTED",
               f"update_release_volumes_and_status clobbered REQUESTED with {_status(env, rel_id)}")

        # A full Calibre sync over a library that does not contain the series must be a no-op
        # for the user's request.
        env.sched.sync_calibre_library()
        _check(_status(env, rel_id) == "REQUESTED",
               f"REGRESSION: sync_calibre_library reverted REQUESTED to {_status(env, rel_id)}")
        _check(len(env.db.get_pending_requests()) == 1)
        print("    [PASSED] acceptance 5b.")
    finally:
        env.close()


# ===========================================================================
# Acceptance 6: Feature 8 transition + auto-download + selective + dedupe intact
# ===========================================================================
def test_acceptance_6_feature8_transition_intact():
    print("--> Testing acceptance 6: DISCOVERY -> REQUESTED transition, auto-download, dedupe...")
    env = _Env("mangatrax_prec_a6_")
    try:
        env.set_releases([_release("guid-a6", "[Group] Dandadan Vol. 01-03 [Digital]", "Dandadan",
                                   volumes=[1.0, 2.0, 3.0], start=1.0, end=3.0, is_bundle=True,
                                   download_url="magnet:?xt=urn:btih:dandadan_prec_0001")])

        # Scenario 1: series absent from Calibre -> DISCOVERY, nothing downloaded.
        res1 = env.poll()
        rel_id = _only_release_id(env)
        _check(res1["requested"] == 0, f"expected 0 requested, got {res1}")
        _check(res1["sent_to_downloader"] == 0)
        _check(env.qbit.add_torrent.call_count == 0)
        _check(_status(env, rel_id) == "DISCOVERY")
        _check(env.db.get_release_by_id(rel_id)["download_status"] == "IDLE")

        # Scenario 2: Calibre newly gains Vol 1 -> automation computes REQUESTED for missing 2,3.
        # The transition must persist, auto-download once and apply selective priorities.
        env.set_calibre({"Dandadan": [1.0]})
        res2 = env.poll()
        _check(res2["requested"] == 1, f"expected 1 requested, got {res2}")
        _check(res2["sent_to_downloader"] == 1, "AUTO-DOWNLOAD MUST FIRE ON DISCOVERY->REQUESTED")
        _check(env.qbit.add_torrent.call_count == 1)
        rel = env.db.get_release_by_id(rel_id)
        _check(rel["status"] == "REQUESTED", f"expected REQUESTED, got {rel['status']}")
        _check(rel["download_status"] == "DOWNLOADING")
        _check(rel["torrent_hash"] == "deadbeefcafe0001")
        _check(env.qbit.apply_selective_volume_download.call_args is not None,
               "selective volume filtering must be applied")
        _check(env.qbit.apply_selective_volume_download.call_args[0][1] == [2.0, 3.0],
               f"expected missing volumes [2.0, 3.0], got {env.qbit.apply_selective_volume_download.call_args[0][1]}")

        # Scenario 3: reproducible poll -> no duplicate download, status still REQUESTED.
        res3 = env.poll()
        _check(res3["sent_to_downloader"] == 0)
        _check(env.qbit.add_torrent.call_count == 1)
        _check(_status(env, rel_id) == "REQUESTED")
        print("    [PASSED] acceptance 6.")
    finally:
        env.close()


# ===========================================================================
# Acceptance 7: blank incoming status never clears a stored status (upsert layer)
# ===========================================================================
def test_acceptance_7_blank_incoming_never_clears():
    print("--> Testing acceptance 7: blank incoming status never clears stored status...")
    env = _Env("mangatrax_prec_a7_")
    try:
        _, rel_id = env.db.upsert_release({
            "guid": "guid-a7", "title": "Dandadan Vol. 01", "series_name": "Dandadan",
            "volume_start": 1.0, "volumes": [1.0], "status": "REQUESTED"})
        env.db.update_release_status(rel_id, "IN_CALIBRE")
        for blank in (None, "", "   "):
            env.db.upsert_release({
                "guid": "guid-a7", "title": "Dandadan Vol. 01 (updated)",
                "series_name": "Dandadan", "status": blank})
            _check(_status(env, rel_id) == "IN_CALIBRE",
                   f"blank incoming {blank!r} cleared IN_CALIBRE -> {_status(env, rel_id)}")
        # Omitted key entirely: metadata still refreshes, status persists.
        env.db.upsert_release({"guid": "guid-a7", "title": "Dandadan Vol. 01 (v2)",
                               "series_name": "Dandadan"})
        _check(_status(env, rel_id) == "IN_CALIBRE")
        _check(env.db.get_release_by_id(rel_id)["title"] == "Dandadan Vol. 01 (v2)")
        print("    [PASSED] acceptance 7.")
    finally:
        env.close()


# ===========================================================================
# Additional writer audits: expand_unresolved_complete_releases, unblacklist
# ===========================================================================
def test_expand_unresolved_complete_releases_preserves_sticky():
    print("--> Testing expand_unresolved_complete_releases() preserves sticky status...")
    env = _Env("mangatrax_prec_expand_")
    try:
        def _resolve(raw):
            return {"canonical_title": raw, "romaji_title": raw,
                    "english_title": raw, "total_volumes": 5}
        env.sched.anilist.resolve_manga_series = MagicMock(side_effect=_resolve)

        _, rel_id = env.db.upsert_release({
            "guid": "guid-expand", "title": "Complete Saga (Complete)",
            "series_name": "Complete Saga", "is_complete": 1, "status": "REQUESTED"})
        _check(_status(env, rel_id) == "REQUESTED")

        expanded = env.sched.expand_unresolved_complete_releases()
        _check(expanded >= 1, f"expected the complete release to be expanded, got {expanded}")
        rel = env.db.get_release_by_id(rel_id)
        _check(len(rel["volumes"]) == 5, f"expected 5 expanded volumes, got {rel['volumes']}")
        _check(rel["status"] == "REQUESTED",
               f"REGRESSION: expansion clobbered REQUESTED with {rel['status']}")
        print("    [PASSED] expand_unresolved_complete_releases preserves sticky status.")
    finally:
        env.close()


def test_unblacklist_lifts_veto_to_recomputable_status():
    print("--> Testing explicit unblacklist lifts the veto to a recomputable status...")
    env = _Env("mangatrax_prec_unbl_")
    try:
        _, rel_id = env.db.upsert_release({
            "guid": "guid-unbl", "title": "Berserk Vol. 01", "series_name": "Berserk",
            "volume_start": 1.0, "volumes": [1.0], "status": "REQUESTED"})
        env.db.add_to_blacklist("Berserk", "Dropped")
        _check(_status(env, rel_id) == "BLACKLISTED")

        # Explicit user action (DELETE /api/blacklist/{id}) must clear the veto so that
        # automation can reclassify the release on the next poll.
        bl = env.db.get_blacklisted_series()
        _check(len(bl) == 1)
        env.db.remove_from_blacklist(bl[0]["id"])
        _check(_status(env, rel_id) == "DISCOVERY",
               f"expected BLACKLISTED -> DISCOVERY on unblacklist, got {_status(env, rel_id)}")
        print("    [PASSED] unblacklist lifts the veto.")
    finally:
        env.close()


def test_feed_poll_summary_counts_reflect_stored_status():
    """check_all_feeds() must report what actually happened, not what the matcher computed.

    Regression (previously deferred): a release stored as IGNORED/BLACKLISTED was
    still counted in the poll's ``requested`` figure because the summary used the
    matcher-computed REQUESTED.  No download can fire for a vetoed release, so the
    count has to reflect the status the precedence model actually persisted.
    """
    print("--> Testing feed-poll summary counts reflect the stored (post-precedence) status...")
    env = _Env("mangatrax_prec_counts_", {"Dandadan": [1.0]})
    try:
        # Vol. 02 is missing from Calibre -> the matcher computes REQUESTED.
        env.set_releases([_release("guid-cnt", "[Group] Dandadan Vol. 02 [Digital]",
                                   "Dandadan", volumes=[2.0], start=2.0, end=2.0)])
        first = env.poll()
        rel_id = _only_release_id(env)
        _check(first["requested"] == 1, f"expected the genuinely requested release to count, got {first}")
        _check(first["sent_to_downloader"] == 1, f"expected an auto-download, got {first}")

        for veto in ("IGNORED", "BLACKLISTED"):
            # Explicit user action (POST /ignore, POST /blacklist) sets the veto.
            env.db.update_release_status(rel_id, veto)
            res = env.poll()
            _check(_status(env, rel_id) == veto,
                   f"{veto} was clobbered by the feed poll: {_status(env, rel_id)}")
            _check(res["requested"] == 0,
                   f"a stored {veto} release must not be reported as requested, got {res}")
            _check(res["sent_to_downloader"] == 0,
                   f"a stored {veto} release must never trigger a download, got {res}")
            _check(res["releases_found"] == 1,
                   f"the release is still found and should be counted as such, got {res}")

        # A release the user explicitly re-requests is counted again.
        env.db.update_release_status(rel_id, "REQUESTED")
        res_req = env.poll()
        _check(_status(env, rel_id) == "REQUESTED")
        _check(res_req["requested"] == 1,
               f"a genuinely REQUESTED release must be counted, got {res_req}")
        print("    [PASSED] poll summary counts match the persisted status for IGNORED/BLACKLISTED.")
    finally:
        env.close()


# ===========================================================================
# Runner
# ===========================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("MangaTrax Status Precedence Regression Suite")
    print("=" * 70)
    tests = [
        test_resolve_status_matrix,
        test_acceptance_1_track_survives_feed_poll,
        test_acceptance_2_manual_status_survives_feed_poll,
        test_acceptance_3_ignored_survives_feed_poll,
        test_acceptance_4_blacklisted_survives_name_divergence,
        test_acceptance_5_in_calibre_survives_feed_poll,
        test_acceptance_5b_calibre_sync_does_not_clobber_requested,
        test_acceptance_6_feature8_transition_intact,
        test_acceptance_7_blank_incoming_never_clears,
        test_expand_unresolved_complete_releases_preserves_sticky,
        test_unblacklist_lifts_veto_to_recomputable_status,
        test_feed_poll_summary_counts_reflect_stored_status,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            PASSED.append(test.__name__)
        except Exception as exc:
            failed += 1
            print(f"    [FAILED] {test.__name__}: {type(exc).__name__}: {exc}")
    print("=" * 70)
    print(f"RESULT: {len(PASSED)} passed, {failed} failed (of {len(tests)})")
    print("=" * 70)
    if failed:
        sys.exit(1)
