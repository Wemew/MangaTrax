#!/usr/bin/env python3
"""Story-arc "Part NN" markers must not be parsed as volumes.

Real-world regression: ``Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02
v01-11`` — the parser treated the part number (2) as the volume when no range won,
and Torbox strict selection mapped every ``Part 02 v01.cbz`` file to volume 2 (or
failed to map ``Part 2 - 03.cbz`` at all), producing
"wanted volume(s) [...] are absent from the torrent" / "cannot be mapped" failures.

Rules verified here:
* with an explicit volume marker, "Part NN" is dropped before volume parsing;
* without a volume marker ("Series Part 3"), existing part-as-volume behaviour holds;
* torrent file names with arc markers map to their real volume, including
  ``Part 02 v01``, ``Part 02 Volume 1``, ``Part 02 - 01``, ``Part 02 [04]``,
  ``Part 2 - 03 (1080p)`` and bare ``03``;
* strict selection now selects only the wanted files instead of erroring.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

from app.parser import extract_manga_bundle_and_volumes  # noqa: E402
from app.scheduler import MangaScheduler  # noqa: E402


def test_title_parsing_ignores_part_number():
    cases = {
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 v01 (2021)": [1.0],
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 Volume 1": [1.0],
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 v01-11": [float(v) for v in range(1, 12)],
        "Ascendance of a Bookworm - Part 2 Vol. 4": [4.0],
    }
    for title, expected in cases.items():
        parsed = extract_manga_bundle_and_volumes(title)
        assert parsed.get("volumes") == expected, f"{title!r} -> {parsed.get('volumes')} != {expected}"
        series = str(parsed.get("series_name") or "").lower()
        assert "part 02" not in series and "part 2" not in series, f"part token leaked into series: {series!r}"

    # No explicit volume marker: "Part 3" remains a volume (light-novel convention).
    legacy = extract_manga_bundle_and_volumes("Series Part 3")
    assert legacy.get("volumes") == [3.0], legacy.get("volumes")
    print("    [PASSED] titles: part numbers dropped only when a real volume marker exists.")


def test_torrent_file_mapping_with_part_markers():
    expected = {
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 v01.cbz": [1.0],
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 v01-11.cbz": [float(v) for v in range(1, 12)],
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 Volume 1.cbz": [1.0],
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 2 Vol. 3.cbz": [3.0],
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 - 01.cbz": [1.0],
        "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 [11].cbz": [11.0],
        "Ascendance of a Bookworm Part 2 - 03 (1080p).cbz": [3.0],
        "Ascendance of a Bookworm - Part 2 - 07 (Digital).cbz": [7.0],
        "Honzuki no Gekokujou Part 02 01.cbz": [1.0],
        "Bookworm Part 02 v05-07 (2024).cbz": [5.0, 6.0, 7.0],
        "Bookworm 10.5.cbz": [10.5],
    }
    for name, volumes in expected.items():
        actual = MangaScheduler._parse_torbox_file_volumes(name)
        assert actual == volumes, f"{name!r} -> {actual} != {volumes}"

    # Years/metadata alone are not volumes (unmappable by design).
    assert MangaScheduler._parse_torbox_file_volumes("Bookworm 2024.cbz") == []
    print("    [PASSED] torrent file names map through Part markers, separators, brackets and bare numbers.")


class _FakeDB:
    def get_setting(self, key, default=""):
        return default

    def log_activity(self, *args, **kwargs):
        return None


def test_strict_selection_succeeds_for_bookworm_part_two():
    scheduler = object.__new__(MangaScheduler)
    scheduler.db = _FakeDB()
    release = {"id": 1, "title": "Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 v01-11"}
    files = [
        {"id": 100 + i, "name": f"Honzuki no Gekokujou | Ascendance of a Bookworm - Part 02 v{i:02d}.cbz", "size": 1024}
        for i in range(1, 12)
    ]
    # Subset: only volumes 1, 3, 5 are wanted -> exact files selected, no error.
    plan = MangaScheduler._plan_torbox_selection(scheduler, release, files, [1.0, 3.0, 5.0], total_size=11 * 1024)
    assert plan["action"] == "files", plan
    assert plan["selected_count"] == 3, plan
    assert [entry["id"] for entry in plan["files"]] == [101, 103, 105], plan["files"]
    assert all(entry["volumes"] for entry in plan["mapping"]), plan["mapping"]

    # Whole range wanted -> every file selected (zip or per-file, never an error).
    plan_all = MangaScheduler._plan_torbox_selection(
        scheduler, release, files, [float(v) for v in range(1, 12)], total_size=11 * 1024
    )
    assert plan_all["selected_count"] == 11, plan_all
    assert plan_all["action"] in ("files", "zip"), plan_all

    # Arc-marker files that need the bare-number fallback ("Part 02 - 03.cbz").
    dashed = [
        {"id": 200 + i, "name": f"Ascendance of a Bookworm Part 2 - {i:02d} (Digital).cbz", "size": 1024}
        for i in range(1, 12)
    ]
    plan_dashed = MangaScheduler._plan_torbox_selection(
        scheduler, release, dashed, [float(v) for v in range(1, 12)], total_size=11 * 1024
    )
    assert plan_dashed["selected_count"] == 11, plan_dashed
    print("    [PASSED] strict selection maps Part-marker torrents and selects only wanted files.")


def run():
    test_title_parsing_ignores_part_number()
    test_torrent_file_mapping_with_part_markers()
    test_strict_selection_succeeds_for_bookworm_part_two()
    print("ALL PART-VOLUME PARSING TESTS PASSED")


if __name__ == "__main__":
    run()
