#!/usr/bin/env python3
"""task-67 suite: all stuck raw files in Failed documents + archive→CBZ conversion."""

import json
import os
import sys
import time
import traceback
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
from app import archive_conversion, download_failures  # noqa: E402
from app.tagger import pipeline  # noqa: E402


def _zip(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def test_scan_all_files():
    print("--> Testing Failed documents scan for every stuck raw file...")
    ctx = E2ETestContext(prefix="mangatrax_t67_scan_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        os.makedirs(root, exist_ok=True)
        _zip(os.path.join(root, "Valid v01.cbz"), {"page.jpg": b"x"})
        _zip(os.path.join(root, "Good Awaiting v02.cbz"), {"page.jpg": b"x"})
        with open(os.path.join(root, "book.epub"), "wb") as handle:
            handle.write(b"PK\x03\x04ebook")
        with open(os.path.join(root, "book.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4")
        with open(os.path.join(root, "archive.cbr"), "wb") as handle:
            handle.write(b"Rar!\x1a\x07\x00")
        with open(os.path.join(root, "archive.7z"), "wb") as handle:
            handle.write(b"7z\xbc\xaf\x27\x1c")
        with open(os.path.join(root, "mystery.bin"), "wb") as handle:
            handle.write(b"\x00\x01")
        with open(os.path.join(root, "partial.part"), "wb") as handle:
            handle.write(b"partial")
        os.makedirs(os.path.join(root, ".hidden"))
        with open(os.path.join(root, ".hidden", "secret.epub"), "wb") as handle:
            handle.write(b"hidden")
        stats = download_failures.scan_failures(ctx.db, root)
        rows = download_failures.list_failures(ctx.db, root)
        by_name = {row["name"]: row for row in rows}
        assert "Valid v01.cbz" not in by_name and "Good Awaiting v02.cbz" not in by_name, by_name
        assert "book.epub" in by_name and "Ebook/document" in by_name["book.epub"]["reason"], by_name
        assert "book.pdf" in by_name and "Ebook/document" in by_name["book.pdf"]["reason"]
        assert "archive.cbr" in by_name and "Unsupported archive format (CBR)" in by_name["archive.cbr"]["reason"]
        assert by_name["archive.cbr"]["can_convert"] is True
        assert "archive.7z" in by_name and by_name["archive.7z"]["can_convert"] is True
        assert "mystery.bin" in by_name and "Unrecognised file" in by_name["mystery.bin"]["reason"]
        assert "partial.part" not in by_name and "secret.epub" not in by_name
        assert stats["checked"] >= 6 and stats["failed"] >= 5
        listed = ctx.client.get("/api/downloads/failures").json()
        assert listed["count"] == len(rows), listed
        print("    [PASSED] ebooks/archives/unknown recorded with reasons, valid + awaiting CBZ untouched.")
    finally:
        ctx.close()


def test_conversion_safety():
    print("--> Testing ZIP conversion, traversal/bomb/symlink rejection, atomicity...")
    ctx = E2ETestContext(prefix="mangatrax_t67_conv_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        os.makedirs(root, exist_ok=True)
        source = os.path.join(root, "Good Archive.zip")
        _zip(source, {"ComicInfo.xml": "<ComicInfo><Series>X</Series></ComicInfo>", "pages/001.jpg": b"img"})
        result = archive_conversion.convert_archive_to_cbz(source)
        assert result["status"] == "success", result
        target = result["target"]
        assert target.endswith(".cbz") and os.path.isfile(target) and not os.path.exists(source)
        with zipfile.ZipFile(target) as archive:
            assert set(archive.namelist()) == {"ComicInfo.xml", "pages/001.jpg"}
            assert archive.testzip() is None
        # traversal
        bad = os.path.join(root, "Bad.zip")
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("../evil.txt", "evil")
        before = os.path.getsize(bad)
        result = archive_conversion.convert_archive_to_cbz(bad)
        assert result["status"] == "error" and os.path.exists(bad) and os.path.getsize(bad) == before, result
        assert not os.path.exists(os.path.join(root, "Bad.cbz"))
        # symlink entry
        link_zip = os.path.join(root, "Link.zip")
        with zipfile.ZipFile(link_zip, "w") as archive:
            info = zipfile.ZipInfo("link.jpg")
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, "target")
        assert archive_conversion.convert_archive_to_cbz(link_zip)["status"] == "error"
        # bomb via lowered cap
        bomb = os.path.join(root, "Bomb.zip")
        _zip(bomb, {"a.bin": b"a" * 20, "b.bin": b"b" * 20})
        old = archive_conversion.MAX_TOTAL_BYTES
        archive_conversion.MAX_TOTAL_BYTES = 10
        try:
            result = archive_conversion.convert_archive_to_cbz(bomb)
        finally:
            archive_conversion.MAX_TOTAL_BYTES = old
        assert result["status"] == "error" and os.path.exists(bomb), result
        # .7z path runs through the (optionally missing) extractor without crashing.
        seven = os.path.join(root, "Book.7z")
        with open(seven, "wb") as handle:
            handle.write(b"7z\xbc\xaf\x27\x1c")
        result = archive_conversion.convert_archive_to_cbz(seven)
        assert result["status"] == "error" and result.get("reason"), result
        print("    [PASSED] ZIP entries preserved/validated; traversal, symlink and cap failures keep originals.")
    finally:
        ctx.close()


def test_api_conversion_and_pipeline_toggle():
    print("--> Testing per-row convert endpoint + pipeline toggle on/off...")
    ctx = E2ETestContext(prefix="mangatrax_t67_pipe_")
    try:
        root = os.path.join(ctx.temp_dir, "downloads")
        os.makedirs(root, exist_ok=True)
        ctx.db.set_setting("downloads_dir", root)
        source = os.path.join(root, "Manual Archive.zip")
        _zip(source, {"ComicInfo.xml": "<ComicInfo><Series>Manual</Series><Volume>1</Volume></ComicInfo>", "p.jpg": b"x"})
        download_failures.scan_failures(ctx.db, root)
        row = next(r for r in download_failures.list_failures(ctx.db, root) if r["name"] == "Manual Archive.zip")
        response = ctx.client.post(f"/api/downloads/failures/{row['id']}/convert")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data.get("task_id"), data
        converted = os.path.join(root, "Manual Archive.cbz")
        deadline = time.time() + 5
        while time.time() < deadline and not os.path.isfile(converted):
            time.sleep(0.05)
        assert os.path.isfile(converted) and not os.path.exists(source), os.listdir(root)
        assert not any(r["name"] == "Manual Archive.zip" for r in download_failures.list_failures(ctx.db, root))
        # Pipeline toggle off leaves archive untouched; on converts.
        ctx.db.set_setting("convert_archives_to_cbz", "false")
        off_zip = os.path.join(root, "Off Archive.zip")
        _zip(off_zip, {"p.jpg": b"x"})
        pipeline.process_downloads_to_staging(root, os.path.join(ctx.temp_dir, "staging_off"), {}, ctx.db)
        assert os.path.exists(off_zip) and not os.path.exists(os.path.join(root, "Off Archive.cbz"))
        ctx.db.set_setting("convert_archives_to_cbz", "true")
        on_zip = os.path.join(root, "On Archive.zip")
        _zip(on_zip, {"p.jpg": b"x"})
        result = pipeline.process_downloads_to_staging(root, os.path.join(ctx.temp_dir, "staging_on"), {}, ctx.db)
        assert result.get("converted", 0) >= 1, result
        assert not os.path.exists(on_zip) and os.path.exists(os.path.join(root, "On Archive.cbz")), os.listdir(root)
        print("    [PASSED] per-row endpoint converts/clears row; toggle off keeps archive, on converts in pipeline.")
    finally:
        ctx.close()


def test_ui_settings_deps():
    print("--> Testing Failed-doc actions, settings toggle, deps wiring...")
    page = open(os.path.join(BACKEND_DIR, "app", "templates", "downloads.html"), encoding="utf-8").read()
    settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    main = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    requirements = open(os.path.join(BACKEND_DIR, "requirements.txt"), encoding="utf-8").read()
    docker = open(os.path.join(BASE_DIR, "Dockerfile"), encoding="utf-8").read()
    for needle in ("item.can_convert", "Convert to CBZ", "convertFailedDocument", "/convert`", "Ebooks cannot be tagged"):
        assert needle in page, needle
    for needle in ("setting-convert-archives", "convert_archives_to_cbz", "Convert CBR/RAR/7Z/CB7/ZIP archives to CBZ"):
        assert needle in settings, needle
    assert "downloads/failures/{failure_id}/convert" in main
    assert "py7zr" in requirements and "rarfile" in requirements
    assert "p7zip-full" in docker and "unar" in docker
    print("    [PASSED] Convert buttons/reasons, settings toggle, endpoint and extractor deps wired.")


def main():
    print("=" * 78)
    print("Running stuck files + archive conversion suite (task-67)")
    print("=" * 78)
    tests = [test_scan_all_files, test_conversion_safety, test_api_conversion_and_pipeline_toggle, test_ui_settings_deps]
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
        print(f"STUCK FILES/CONVERSION SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL STUCK FILE/CONVERSION TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
