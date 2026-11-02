#!/usr/bin/env python3
"""task-11 suite: multiple release groups per tag — parse, persist, match as a set.

A release can be tagged with several scanlation groups: ``(1r0n, Ushi)``,
``(1r0n & Ushi)``, ``[1r0n] [Ushi]``.  The defect was inconsistency: comma/ampersand
tags were stored as one literal string (matching NEITHER prioritized group) and the
second bracket was silently lost.  Everything is now set-based:

* parser returns ``release_groups`` (all valid groups, primary first) while
  ``release_group`` stays the primary for display/back-compat;
* the full set is persisted in ``releases.release_groups_json`` (guarded migration +
  idempotent backfill from legacy ``release_group``);
* every prioritized comparison is MEMBERSHIP ("any stored group is prioritized"),
  in SQL (``json_each`` join) and in memory; priority ordering uses the user's
  highest-ranked matching group; dedupe uses the sorted group set; webhooks/search/
  UI carry the full list plus the primary.

Run through the assert-aware runner (assertions compiled live, optimize=0):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import sqlite3
import sys
import tempfile
import shutil
import traceback
from unittest.mock import MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
for _path in (BACKEND_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.database import (
    MangaDatabase,
    release_dedupe_signature,
    release_groups_of,
    release_prioritized_priority,
)
from app.parser import (
    extract_manga_bundle_and_volumes,
    extract_release_group,
    extract_release_groups,
    split_release_group_value,
)

SISTER_TITLE = (
    "Sister and Giant - A Young Lady Is Reborn in Another World v01-06 "
    "(2024-2026) (Digital) (1r0n, Ushi)"
)


class _Ctx:
    """Isolated DB (seeded prioritized groups removed) for membership tests."""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mangatrax_multigroup_")
        self.db = MangaDatabase(os.path.join(self.tmpdir, "mangatrax.db"))
        self.db.set_setting("calibre_path", os.path.join(self.tmpdir, "calibre-none"))
        for group in self.db.get_prioritized_groups():
            self.db.remove_prioritized_group(group["id"])

    def upsert(self, guid, title, series_name, release_group, **extra):
        parsed = extract_manga_bundle_and_volumes(title)
        rel = {
            "guid": guid,
            "title": title,
            "series_name": series_name,
            "release_group": release_group,
            "release_groups": parsed.get("release_groups"),
            "status": extra.pop("status", "DISCOVERY"),
        }
        rel.update(extra)
        self.db.upsert_release(rel)
        return rel

    def row(self, guid):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM releases WHERE guid = ?;", (guid,))
            found = cur.fetchone()
            return dict(found) if found else None

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# 1. Parser: split every joint-tag style, primary = first, format words excluded
# ===========================================================================
def test_parser_splits_joint_tags():
    print("--> Testing parser joint-tag splitting...")
    # The reported title: series/volumes/bundle already correct; groups now split.
    parsed = extract_manga_bundle_and_volumes(SISTER_TITLE)
    assert parsed["series_name"] == "Sister and Giant - A Young Lady Is Reborn in Another World"
    assert parsed["volumes"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], parsed["volumes"]
    assert parsed["is_bundle"] is True
    assert parsed["release_groups"] == ["1r0n", "Ushi"], parsed["release_groups"]
    assert parsed["release_group"] == "1r0n", parsed["release_group"]

    for title, expected in [
        ("X v01 (1r0n, Ushi)", ["1r0n", "Ushi"]),
        ("X v01 (1r0n & Ushi)", ["1r0n", "Ushi"]),
        ("X v01 (1r0n/Ushi)", ["1r0n", "Ushi"]),
        ("X v01 (1r0n + Ushi)", ["1r0n", "Ushi"]),
        ("X v01 (1r0n and Ushi)", ["1r0n", "Ushi"]),
        # Multiple brackets: BOTH are captured (regression: the second was lost).
        ("X v01 [1r0n] [Ushi]", ["1r0n", "Ushi"]),
        ("X v01 (1r0n) (Ushi)", ["1r0n", "Ushi"]),
        # Format tags / publishers never enter a multi-group list.
        ("X v01 (Digital-Compilation) (1r0n, Ushi)", ["1r0n", "Ushi"]),
        ("X v01 (1r0n, Square Enix)", ["1r0n"]),
        ("X v01 (1r0n, Digital)", ["1r0n"]),
        # Single groups unchanged.
        ("X v01 [danke-Empire]", ["danke-Empire"]),
        ("X v01 (Lu自主规制)", ["Lu自主规制"]),
    ]:
        groups = extract_release_groups(title)
        assert groups == expected, (title, groups)
        assert extract_release_group(title) == expected[0], (title, extract_release_group(title))
    assert split_release_group_value("1r0n, Ushi") == ["1r0n", "Ushi"]
    assert split_release_group_value("danke-Empire") == ["danke-Empire"]
    assert split_release_group_value("Completed") == []
    assert split_release_group_value("1r0n, Digital") == ["1r0n"]
    print("    [PASSED] all group separators split; primary first; format words excluded.")


# ===========================================================================
# 2. Persistence + guarded migration/backfill (idempotent)
# ===========================================================================
def test_persistence_and_backfill():
    print("--> Testing release_groups_json persistence + idempotent backfill...")
    ctx = _Ctx()
    try:
        legacy_path = os.path.join(ctx.tmpdir, "legacy.db")
        # Simulate a pre-task-11 database: no release_groups_json column, rows with
        # the joint tag stored as one literal string (the old broken behaviour).
        conn = sqlite3.connect(legacy_path)
        conn.executescript("""
            CREATE TABLE releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                series_name TEXT, release_group TEXT DEFAULT '', description TEXT DEFAULT '',
                mangaupdates_url TEXT DEFAULT '', is_bundle INTEGER DEFAULT 0, volume_start REAL,
                volume_end REAL, volumes_json TEXT DEFAULT '[]', missing_vols_json TEXT DEFAULT '[]',
                download_url TEXT, link TEXT, cover_url TEXT, source_feed_name TEXT,
                status TEXT DEFAULT 'REQUESTED', download_status TEXT DEFAULT 'IDLE', torrent_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute(
            "INSERT INTO releases (guid, title, series_name, release_group) VALUES (?, ?, ?, ?);",
            ("legacy-joint", SISTER_TITLE, "Sister and Giant", "1r0n, Ushi"),
        )
        conn.execute(
            "INSERT INTO releases (guid, title, series_name, release_group) VALUES (?, ?, ?, ?);",
            ("legacy-single", "X v01 [danke-Empire]", "X", "danke-Empire"),
        )
        conn.commit()
        conn.close()

        db = MangaDatabase(legacy_path)
        row = dict(db.get_release_by_id(1))
        assert json.loads(row["release_groups_json"]) == ["1r0n", "Ushi"], row["release_groups_json"]
        assert row["release_group"] == "1r0n", row["release_group"]
        row2 = dict(db.get_release_by_id(2))
        assert json.loads(row2["release_groups_json"]) == ["danke-Empire"], row2["release_groups_json"]
        # Idempotent on repeat runs: values unchanged, no duplicates/errors.
        db.init_db()
        db.init_db()
        assert json.loads(dict(db.get_release_by_id(1))["release_groups_json"]) == ["1r0n", "Ushi"]
        assert json.loads(dict(db.get_release_by_id(2))["release_groups_json"]) == ["danke-Empire"]
        with db.get_connection() as c:
            cur = c.cursor()
            cur.execute("PRAGMA table_info(releases);")
            cols = {r["name"] for r in cur.fetchall()}
        assert "release_groups_json" in cols, sorted(cols)

        # New writes persist the full set; an explicit list wins over the split.
        ctx.upsert("new-joint", SISTER_TITLE, "Sister and Giant", "1r0n")
        stored = ctx.row("new-joint")
        assert json.loads(stored["release_groups_json"]) == ["1r0n", "Ushi"], stored["release_groups_json"]
        ctx.upsert("new-single", "Y v01 [Stick]", "Y", "Stick")
        assert json.loads(ctx.row("new-single")["release_groups_json"]) == ["Stick"]
        print("    [PASSED] joint tags persisted as a JSON set; backfill idempotent.")
    finally:
        ctx.close()


# ===========================================================================
# 3. Set membership: prioritizing EITHER group marks the release prioritized
# ===========================================================================
def test_prioritized_membership_any_group():
    print("--> Testing set-membership prioritized matching...")
    ctx = _Ctx()
    try:
        ctx.upsert("joint", SISTER_TITLE, "Sister and Giant", "1r0n", status="DISCOVERY")

        # Not prioritized when neither group is configured.
        assert ctx.db.get_releases()[0]["is_prioritized"] is False
        assert ctx.db.get_prioritized_releases() == []
        assert ctx.db.get_releases_grouped_by_series(prioritized_only=True) == []

        # Prioritize ONLY the secondary group (Ushi): the joint release matches.
        ctx.db.add_prioritized_group("Ushi", priority=3)
        flat = ctx.db.get_releases()[0]
        assert flat["is_prioritized"] is True, flat
        assert flat["prioritized_priority"] == 3, flat
        prioritized = ctx.db.get_prioritized_releases()
        assert [r["guid"] for r in prioritized] == ["joint"], prioritized
        grouped = ctx.db.get_releases_grouped_by_series(prioritized_only=True)
        assert len(grouped) == 1, grouped
        assert grouped[0]["has_prioritized"] is True, grouped[0]
        assert grouped[0]["prioritized_count"] == 1, grouped[0]
        assert ctx.db.is_group_prioritized("Ushi") is True
        assert ctx.db.is_group_prioritized("1r0n, Ushi") is True
        counts = {g["name"]: g["release_count"] for g in ctx.db.get_prioritized_groups()}
        assert counts.get("Ushi") == 1, counts

        # Switch to ONLY the primary group (1r0n): still prioritized.
        for group in ctx.db.get_prioritized_groups():
            ctx.db.remove_prioritized_group(group["id"])
        ctx.db.add_prioritized_group("1r0n", priority=2)
        assert ctx.db.get_releases()[0]["is_prioritized"] is True
        assert [r["guid"] for r in ctx.db.get_prioritized_releases()] == ["joint"]
        counts = {g["name"]: g["release_count"] for g in ctx.db.get_prioritized_groups()}
        assert counts.get("1r0n") == 1, counts

        # Neither configured -> not prioritized again.
        for group in ctx.db.get_prioritized_groups():
            ctx.db.remove_prioritized_group(group["id"])
        assert ctx.db.get_releases()[0]["is_prioritized"] is False
        assert ctx.db.get_prioritized_releases() == []
        assert ctx.db.is_group_prioritized("Ushi") is False
        print("    [PASSED] either group qualifies; neither -> not prioritized.")
    finally:
        ctx.close()


# ===========================================================================
# 4. Multiple brackets: both groups captured and matched
# ===========================================================================
def test_multi_bracket_groups_both_captured():
    print("--> Testing '[1r0n] [Ushi]' captures both groups (regression)...")
    ctx = _Ctx()
    try:
        title = "X v01 [1r0n] [Ushi]"
        assert extract_release_groups(title) == ["1r0n", "Ushi"]
        ctx.upsert("bracketed", title, "X", extract_release_group(title))
        stored = ctx.row("bracketed")
        assert json.loads(stored["release_groups_json"]) == ["1r0n", "Ushi"], stored["release_groups_json"]
        ctx.db.add_prioritized_group("Ushi", priority=1)
        assert ctx.db.get_releases()[0]["is_prioritized"] is True
        assert len(ctx.db.get_prioritized_releases()) == 1
        print("    [PASSED] second bracket is no longer lost and matches the VIP view.")
    finally:
        ctx.close()


# ===========================================================================
# 5. Format words never enter the group set
# ===========================================================================
def test_format_words_never_enter_groups():
    print("--> Testing format words never enter release_groups...")
    title = "X v01 (Digital-Compilation) (1r0n, Ushi) (1080p) (Digital)"
    groups = extract_release_groups(title)
    assert groups == ["1r0n", "Ushi"], groups
    for forbidden in ("Digital", "Digital-Compilation", "1080p", "CBZ"):
        assert forbidden not in groups, groups
    ctx = _Ctx()
    try:
        ctx.upsert("formats", title, "X", extract_release_group(title))
        stored = json.loads(ctx.row("formats")["release_groups_json"])
        assert stored == ["1r0n", "Ushi"], stored
        ctx.db.add_prioritized_group("Digital-Compilation", priority=5)
        assert ctx.db.get_releases()[0]["is_prioritized"] is False
        print("    [PASSED] format tags are not groups and cannot match VIP entries.")
    finally:
        ctx.close()


# ===========================================================================
# 6. Single-group releases unchanged
# ===========================================================================
def test_single_group_unchanged():
    print("--> Testing single-group releases unchanged...")
    ctx = _Ctx()
    try:
        ctx.upsert("single", "X v01 [danke-Empire]", "X", "danke-Empire")
        stored = ctx.row("single")
        assert stored["release_group"] == "danke-Empire", stored["release_group"]
        assert json.loads(stored["release_groups_json"]) == ["danke-Empire"], stored["release_groups_json"]
        ctx.db.add_prioritized_group("danke-Empire", priority=1)
        assert ctx.db.get_releases()[0]["is_prioritized"] is True
        assert len(ctx.db.get_prioritized_releases()) == 1
        for group in ctx.db.get_prioritized_groups():
            ctx.db.remove_prioritized_group(group["id"])
        ctx.db.add_prioritized_group("Unrelated", priority=1)
        assert ctx.db.get_releases()[0]["is_prioritized"] is False
        print("    [PASSED] single-group behaviour is unchanged.")
    finally:
        ctx.close()


# ===========================================================================
# 7. Priority ordering: the highest-ranked matching group wins
# ===========================================================================
def test_priority_ordering_uses_highest_ranked_group():
    print("--> Testing priority ordering with two prioritized groups...")
    ctx = _Ctx()
    try:
        ctx.db.add_prioritized_group("Alpha", priority=1)
        ctx.db.add_prioritized_group("Beta", priority=9)
        ctx.upsert("rel-alpha", "Series A v01 (Alpha)", "Series A", "Alpha")
        ctx.upsert("rel-beta", "Series B v01 (Beta)", "Series B", "Beta")
        ctx.upsert("rel-joint", "Series C v01 (Alpha, Beta)", "Series C", "Alpha")
        # Joint release takes the HIGHER of its groups' priorities.
        joint_row = next(r for r in ctx.db.get_releases() if r["guid"] == "rel-joint")
        assert joint_row["prioritized_priority"] == 9, joint_row
        priority_map = {"alpha": 1, "beta": 9}
        assert release_prioritized_priority({"release_groups": ["Alpha", "Beta"]}, priority_map) == 9
        # Views order by the user's highest-ranked matching group (Beta > Alpha);
        # equal ranks (joint carries Beta's rank) keep a stable order.
        prioritized_rows = ctx.db.get_prioritized_releases()
        ordered = [r["guid"] for r in prioritized_rows]
        assert set(ordered[:2]) == {"rel-joint", "rel-beta"}, ordered
        assert ordered[-1] == "rel-alpha", ordered
        assert [r["prioritized_priority"] for r in prioritized_rows] == [9, 9, 1], prioritized_rows
        groups = ctx.db.get_releases_grouped_by_series(prioritized_only=True)
        group_names = [g["series_name"] for g in groups]
        assert set(group_names[:2]) == {"Series C", "Series B"}, group_names
        assert group_names[-1] == "Series A", group_names
        assert groups[0]["prioritized_priority"] == 9, groups[0]
        print("    [PASSED] higher-ranked matching group governs ordering.")
    finally:
        ctx.close()


# ===========================================================================
# 8. Dedupe signature uses the sorted group set
# ===========================================================================
def test_dedupe_signature_uses_group_set():
    print("--> Testing dedupe signature uses the sorted group set...")
    a = {"download_url": "magnet:1", "volumes": [1.0, 2.0], "release_group": "1r0n, Ushi"}
    b = {"download_url": "magnet:1", "volumes": [1.0, 2.0], "release_group": "Ushi & 1r0n"}
    c = {"download_url": "magnet:1", "volumes": [1.0, 2.0], "release_group": "1r0n"}
    assert release_dedupe_signature(a) == release_dedupe_signature(b), (
        release_dedupe_signature(a), release_dedupe_signature(b)
    )
    assert release_dedupe_signature(a) != release_dedupe_signature(c)
    assert release_dedupe_signature({"guid": "g", "release_group": "1r0n, Ushi"}) == "g"
    assert release_groups_of({"release_group": "1r0n, Ushi"}) == ["1r0n", "Ushi"]
    print("    [PASSED] dedupe identity is order/style independent.")


# ===========================================================================
# 9. Webhook payload carries the list plus the primary
# ===========================================================================
def test_webhook_payload_carries_groups():
    print("--> Testing webhook payload groups list + primary...")
    from app.notifier.webhook import NotificationService

    captured = {}
    notifier = NotificationService(discord_webhook="https://discord.example/webhook")
    notifier._send_discord = lambda **kwargs: captured.update(kwargs)
    generic = {}
    notifier._send_generic = lambda payload: generic.update(payload)
    notifier.notify_new_request({
        "title": SISTER_TITLE,
        "series_name": "Sister and Giant",
        "release_group": "1r0n",
        "release_groups": ["1r0n", "Ushi"],
        "volume_start": 1.0,
        "volume_end": 6.0,
        "is_bundle": True,
        "link": "https://example.com/1",
    })
    values = {field["name"]: field["value"] for field in captured.get("fields", [])}
    assert values.get("Group") == "1r0n", values
    assert values.get("Groups") == "1r0n, Ushi", values
    assert generic.get("release", {}).get("release_groups") == ["1r0n", "Ushi"], generic.get("release")
    # Legacy payload with only the joint string still yields the list.
    captured.clear()
    notifier.notify_new_request({
        "title": "X", "series_name": "X", "release_group": "1r0n, Ushi", "link": "https://example.com/2",
    })
    values = {field["name"]: field["value"] for field in captured.get("fields", [])}
    assert values.get("Groups") == "1r0n, Ushi", values
    print("    [PASSED] webhook carries primary + full group list.")


# ===========================================================================
# 10. Search indexes every group
# ===========================================================================
def test_search_matches_secondary_group():
    print("--> Testing search indexes all release groups...")
    ctx = _Ctx()
    try:
        ctx.upsert("search-joint", SISTER_TITLE, "Sister and Giant", "1r0n", status="REQUESTED")
        by_secondary = ctx.db.search_releases("Ushi")
        assert [r["guid"] for r in by_secondary] == ["search-joint"], by_secondary
        assert by_secondary[0]["release_groups"] == ["1r0n", "Ushi"], by_secondary[0]
        by_primary = ctx.db.search_releases("1r0n")
        assert [r["guid"] for r in by_primary] == ["search-joint"], by_primary
        print("    [PASSED] search finds the release via either group.")
    finally:
        ctx.close()


# ===========================================================================
# 11. UI templates render every group (primary highlighted)
# ===========================================================================
def test_ui_templates_render_all_groups():
    print("--> Testing UI templates render all groups...")
    paths = [
        os.path.join(BACKEND_DIR, "app", "templates", "discovery.html"),
        os.path.join(BACKEND_DIR, "app", "templates", "partials", "flat_release_row_chunk.html"),
    ]
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        assert "release_groups" in source, path
        assert "for grp in" in source, path
    print("    [PASSED] discovery card and flat rows loop over release_groups.")


def main():
    print("=" * 78)
    print("Running multi-group release tag suite (task-11)")
    print("=" * 78)
    tests = [
        test_parser_splits_joint_tags,
        test_persistence_and_backfill,
        test_prioritized_membership_any_group,
        test_multi_bracket_groups_both_captured,
        test_format_words_never_enter_groups,
        test_single_group_unchanged,
        test_priority_ordering_uses_highest_ranked_group,
        test_dedupe_signature_uses_group_set,
        test_webhook_payload_carries_groups,
        test_search_matches_secondary_group,
        test_ui_templates_render_all_groups,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception:
            failures += 1
            print(f"  [FAILED] {test.__name__}")
            traceback.print_exc()
    print("=" * 78)
    if failures:
        print(f"MULTI-GROUP SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL MULTI-GROUP TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
