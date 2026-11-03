#!/usr/bin/env python3
"""task-12 suite: author credits are never release groups; combined-bracket Complete.

Two defects on the canonical Nyaa layout:

    Shiki (Ryu Fujisaki/Fuyumi Ono) [Manga] [CBZ] [vol.1-11 Complete] (Aku-Tenshi & SnoopyCool)

A) The `/` joint-group separator also split the author credit block, so the authors
   became phantom release groups (wrong attribution, wrong dedupe identity).
   Credits are now extracted positionally into ``authors`` and excluded from the
   group set: release groups come ONLY from the trailing cluster of group-eligible
   blocks (scan right-to-left; stop at the first format/volume/year/publisher/status
   block; trailing revision markers are skipped).

B) ``[vol.1-11 Complete]`` did not set ``is_complete`` (only standalone complete
   tags did).  A bracket carrying a complete-word TOGETHER WITH a volume reference
   now counts, without fabricating volumes for a bare ``[Completed]``.

Run through the assert-aware runner (assertions compiled live, optimize=0):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import sys
import tempfile
import shutil
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
for _path in (BACKEND_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.database import MangaDatabase, release_dedupe_signature
from app.parser import (
    extract_authors,
    extract_manga_bundle_and_volumes,
    extract_release_group,
    extract_release_groups,
    is_complete_series_release,
)

SHIKI = (
    "Shiki (Ryu Fujisaki/Fuyumi Ono) [Manga] [CBZ] [vol.1-11 Complete] "
    "(Aku-Tenshi & SnoopyCool)"
)


# ===========================================================================
# 1. The exact reported title: both defects fixed end-to-end
# ===========================================================================
def test_shiki_title_full_contract():
    print("--> Testing the reported Shiki title contract...")
    parsed = extract_manga_bundle_and_volumes(SHIKI)
    assert parsed["series_name"] == "Shiki", parsed["series_name"]
    assert parsed["volumes"] == [float(v) for v in range(1, 12)], parsed["volumes"]
    assert parsed["is_bundle"] is True, parsed["is_bundle"]
    assert parsed["is_complete"] is True, parsed["is_complete"]
    assert parsed["release_groups"] == ["Aku-Tenshi", "SnoopyCool"], parsed["release_groups"]
    assert parsed["release_group"] == "Aku-Tenshi", parsed["release_group"]
    assert parsed["authors"] == ["Ryu Fujisaki", "Fuyumi Ono"], parsed["authors"]
    assert parsed["is_light_novel"] is False, parsed["is_light_novel"]
    assert parsed["publisher"] is None, parsed["publisher"]
    # Credits must never leak into the group set, even as substrings.
    for author in parsed["authors"]:
        assert author not in parsed["release_groups"], parsed["release_groups"]
        assert author.lower() not in (parsed["release_group"] or "").lower()
    print("    [PASSED] series/volumes/bundle/complete/groups/authors all correct.")


# ===========================================================================
# 2. Author block variants: without credits, single author, spaced/comma/&
# ===========================================================================
def test_author_block_variants():
    print("--> Testing author-credit block variants...")
    no_credit = (
        "Shiki [Manga] [CBZ] [vol.1-11 Complete] (Aku-Tenshi & SnoopyCool)"
    )
    parsed = extract_manga_bundle_and_volumes(no_credit)
    assert parsed["release_groups"] == ["Aku-Tenshi", "SnoopyCool"], parsed["release_groups"]
    assert parsed["authors"] == [], parsed["authors"]
    assert parsed["is_complete"] is True

    for credit, expected in [
        ("(Ryu Fujisaki)", ["Ryu Fujisaki"]),
        ("(Ryu Fujisaki / Fuyumi Ono)", ["Ryu Fujisaki", "Fuyumi Ono"]),
        ("(Ryu Fujisaki/Fuyumi Ono)", ["Ryu Fujisaki", "Fuyumi Ono"]),
        ("(Ryu Fujisaki, Fuyumi Ono)", ["Ryu Fujisaki", "Fuyumi Ono"]),
        ("(Ryu Fujisaki & Fuyumi Ono)", ["Ryu Fujisaki", "Fuyumi Ono"]),
        ("(Ryu Fujisaki and Fuyumi Ono)", ["Ryu Fujisaki", "Fuyumi Ono"]),
    ]:
        title = (
            "Shiki " + credit + " [Manga] [CBZ] [vol.1-11 Complete] "
            "(Aku-Tenshi & SnoopyCool)"
        )
        parsed = extract_manga_bundle_and_volumes(title)
        assert parsed["series_name"] == "Shiki", (credit, parsed["series_name"])
        assert parsed["authors"] == expected, (credit, parsed["authors"])
        assert parsed["release_groups"] == ["Aku-Tenshi", "SnoopyCool"], (
            credit, parsed["release_groups"],
        )
    print("    [PASSED] credits split on / & , and 'and'; groups stay clean.")


# ===========================================================================
# 3. Format/volume/status blocks are never groups; credits with no group
# ===========================================================================
def test_format_and_volume_blocks_never_groups():
    print("--> Testing format/volume/status blocks never become groups...")
    parsed = extract_manga_bundle_and_volumes(
        "Shiki (Ryu Fujisaki/Fuyumi Ono) [Manga] [CBZ] [vol.1-11 Complete]"
    )
    assert parsed["release_groups"] == [], parsed["release_groups"]
    assert parsed["release_group"] is None, parsed["release_group"]
    assert parsed["authors"] == ["Ryu Fujisaki", "Fuyumi Ono"], parsed["authors"]
    assert parsed["is_complete"] is True

    groups = extract_release_groups(
        "Shiki (Ryu Fujisaki) [Manga] [CBZ] [vol.1-11 Complete] (Aku-Tenshi & SnoopyCool)"
    )
    for forbidden in ("Manga", "CBZ", "Complete", "vol.1-11 Complete", "Ryu Fujisaki"):
        assert forbidden not in groups, (forbidden, groups)
    print("    [PASSED] [Manga]/[CBZ]/[vol...Complete]/credits excluded from groups.")


# ===========================================================================
# 4. Defect B: complete-word + volume reference inside one bracket
# ===========================================================================
def test_complete_inside_combined_bracket():
    print("--> Testing Complete inside a combined volume bracket...")
    for title in [
        "[vol.1-11 Complete]",
        "Shiki [Vol 1-11 complete]",
        "Shiki (Vol 1-3 full)",
        "Shiki [vols 1-11 Completed]",
    ]:
        assert is_complete_series_release(title) is True, title
    # Existing standalone forms keep working.
    for title in ["[Complete]", "(complete series)", "[all volumes]", "(full series)"]:
        assert is_complete_series_release(title) is True, title
    # Negatives: a plain volume range is NOT complete; no fabricated volumes.
    assert is_complete_series_release("[Vol 1-3]") is False
    assert is_complete_series_release("Shiki v01-11") is False
    parsed = extract_manga_bundle_and_volumes("[Completed]")
    assert parsed["is_complete"] is True
    assert parsed["volumes"] == [], parsed["volumes"]
    parsed = extract_manga_bundle_and_volumes("Shiki [Completed]")
    assert parsed["volumes"] == [], parsed["volumes"]
    # The combined bracket still yields the real volume set + bundle flag.
    parsed = extract_manga_bundle_and_volumes("Shiki [Manga] [vol.1-11 Complete]")
    assert parsed["volumes"] == [float(v) for v in range(1, 12)], parsed["volumes"]
    assert parsed["is_bundle"] is True
    assert parsed["is_complete"] is True
    print("    [PASSED] combined-bracket Complete detected; negatives preserved.")


# ===========================================================================
# 5. Dedupe identity no longer depends on the credit block
# ===========================================================================
def test_dedupe_ignores_author_credits():
    print("--> Testing dedupe identity ignores author credits...")
    with_credit = extract_manga_bundle_and_volumes(SHIKI)
    without_credit = extract_manga_bundle_and_volumes(
        "Shiki [Manga] [CBZ] [vol.1-11 Complete] (Aku-Tenshi & SnoopyCool)"
    )
    download = "magnet:?xt=urn:btih:same"
    sig_with = release_dedupe_signature({
        "download_url": download,
        "volumes": with_credit["volumes"],
        "release_group": with_credit["release_group"],
        "release_groups": with_credit["release_groups"],
    })
    sig_without = release_dedupe_signature({
        "download_url": download,
        "volumes": without_credit["volumes"],
        "release_group": without_credit["release_group"],
        "release_groups": without_credit["release_groups"],
    })
    assert sig_with == sig_without, (sig_with, sig_without)
    print("    [PASSED] same release with/without credits dedupes to one identity.")


# ===========================================================================
# 6. Persistence: authors never enter the stored group set / VIP matching
# ===========================================================================
def test_authors_never_persisted_as_groups():
    print("--> Testing authors never persisted or matched as groups...")
    tmpdir = tempfile.mkdtemp(prefix="mangatrax_authors_")
    try:
        db = MangaDatabase(os.path.join(tmpdir, "mangatrax.db"))
        for group in db.get_prioritized_groups():
            db.remove_prioritized_group(group["id"])
        parsed = extract_manga_bundle_and_volumes(SHIKI)
        db.upsert_release({
            "guid": "guid-shiki",
            "title": SHIKI,
            "series_name": parsed["series_name"],
            "release_group": parsed["release_group"],
            "release_groups": parsed["release_groups"],
            "authors": parsed["authors"],
            "is_bundle": parsed["is_bundle"],
            "volumes": parsed["volumes"],
            "volume_start": 1.0,
            "volume_end": 11.0,
            "status": "DISCOVERY",
        })
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT release_group, release_groups_json FROM releases WHERE guid = 'guid-shiki';")
            row = cur.fetchone()
        assert row["release_group"] == "Aku-Tenshi", row["release_group"]
        assert json.loads(row["release_groups_json"]) == ["Aku-Tenshi", "SnoopyCool"], row["release_groups_json"]
        # Author names as prioritized groups must NOT match this release.
        db.add_prioritized_group("Ryu Fujisaki", priority=9)
        assert db.get_releases()[0]["is_prioritized"] is False
        assert db.get_prioritized_releases() == []
        for group in db.get_prioritized_groups():
            db.remove_prioritized_group(group["id"])
        db.add_prioritized_group("SnoopyCool", priority=1)
        assert db.get_releases()[0]["is_prioritized"] is True
        print("    [PASSED] stored group set excludes credits; VIP matches real groups only.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# 7. Propagation: ReleaseItem + webhook carry authors, not as groups
# ===========================================================================
def test_authors_propagation():
    print("--> Testing authors propagation (provider item + webhook)...")
    from app.providers.base import ReleaseItem
    from app.notifier.webhook import NotificationService

    parsed = extract_manga_bundle_and_volumes(SHIKI)
    item = ReleaseItem(
        title=SHIKI,
        link="https://example.com/shiki",
        series_name=parsed["series_name"],
        release_group=parsed["release_group"],
        release_groups=parsed["release_groups"],
        authors=parsed["authors"],
        is_bundle=parsed["is_bundle"],
        volumes=parsed["volumes"],
    )
    data = item.to_dict()
    assert data["authors"] == ["Ryu Fujisaki", "Fuyumi Ono"], data["authors"]
    assert data["release_groups"] == ["Aku-Tenshi", "SnoopyCool"], data["release_groups"]

    notifier = NotificationService(discord_webhook="https://discord.example/webhook")
    captured = {}
    notifier._send_discord = lambda **kwargs: captured.update(kwargs)
    generic = {}
    notifier._send_generic = lambda payload: generic.update(payload)
    notifier.notify_new_request({
        "title": SHIKI,
        "series_name": "Shiki",
        "release_group": "Aku-Tenshi",
        "release_groups": ["Aku-Tenshi", "SnoopyCool"],
        "authors": ["Ryu Fujisaki", "Fuyumi Ono"],
        "volume_start": 1.0,
        "volume_end": 11.0,
        "is_bundle": True,
        "link": "https://example.com/shiki",
    })
    values = {field["name"]: field["value"] for field in captured.get("fields", [])}
    assert values.get("Authors") == "Ryu Fujisaki, Fuyumi Ono", values
    assert values.get("Groups") == "Aku-Tenshi, SnoopyCool", values
    release_payload = generic.get("release", {})
    assert release_payload.get("authors") == ["Ryu Fujisaki", "Fuyumi Ono"], release_payload
    assert "Ryu Fujisaki" not in (release_payload.get("release_groups") or []), release_payload
    print("    [PASSED] ReleaseItem + webhook expose authors; groups stay clean.")


# ===========================================================================
# 8. Regression: every previously working group form stays green
# ===========================================================================
def test_group_regressions():
    print("--> Testing group/revision/publisher regressions...")
    for title, expected in [
        # Trailing joint groups still split (task-11 behaviour).
        ("X v01 (GroupA/GroupB)", ["GroupA", "GroupB"]),
        ("X v01 (1r0n, Anon)", ["1r0n", "Anon"]),
        ("X v01 [1r0n] [Ushi]", ["1r0n", "Ushi"]),
        # Hyphenated and non-ASCII groups survive.
        ("[danke-Empire] Bleach v01-v20 [Digital]", ["danke-Empire"]),
        ("Shiki ... (Aku-Tenshi & SnoopyCool)", ["Aku-Tenshi", "SnoopyCool"]),
        ("[Lu自主规制] Chainsaw Man Vol. 01-11 [Digital] [HD]", ["Lu自主规制"]),
        # [Publisher] [Group] pairs.
        ("[Seven Seas] [Stick] The Tale of a Little Alchemist v01-04 [Digital]", ["Stick"]),
        # Prefer-last for unknown blocks; revision markers skipped, not groups.
        ("Some Manga v01 (BogusPublisherCo) (RealGroup)", ["RealGroup", "BogusPublisherCo"]),
        ("X v01 (DigitalMangaFan) (f2)", ["DigitalMangaFan"]),
        # Scene-style and single-group releases unchanged.
        ("Manga.Million.SiteRip.1600p.AVIF.2026-TenmaLand (Multi)", ["TenmaLand"]),
        ("Title-Stick (Digital)", ["Stick"]),
        ("X v01 [CustomVIPGroup]", ["CustomVIPGroup"]),
        ("[Manga] Frieren Vol. 01-10 [danke-Empire] [Completed]", ["danke-Empire"]),
    ]:
        groups = extract_release_groups(title)
        assert groups == expected, (title, groups)
        assert extract_release_group(title) == expected[0], (title, extract_release_group(title))
    # Publishers / format tags / revision markers alone never become groups.
    for title in ["(Square Enix)", "(K-MANGA)", "(Digital-Compilation)", "(f2)", "[Manga]", "[CBZ]"]:
        assert extract_release_groups(title) == [], (title, extract_release_groups(title))
    # Credit blocks are authors even in a title with no group at all.
    assert extract_authors("Shiki (Ryu Fujisaki) [Manga] [CBZ]") == ["Ryu Fujisaki"]
    assert extract_authors("Shiki [Manga] [CBZ] (Aku-Tenshi & SnoopyCool)") == []
    print("    [PASSED] all group/revision/publisher forms unchanged.")


def main():
    print("=" * 78)
    print("Running author-credit / combined-complete suite (task-12)")
    print("=" * 78)
    tests = [
        test_shiki_title_full_contract,
        test_author_block_variants,
        test_format_and_volume_blocks_never_groups,
        test_complete_inside_combined_bracket,
        test_dedupe_ignores_author_credits,
        test_authors_never_persisted_as_groups,
        test_authors_propagation,
        test_group_regressions,
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
        print(f"AUTHOR-CREDIT SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL AUTHOR-CREDIT TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
