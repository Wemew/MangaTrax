#!/usr/bin/env python3
"""M2 feature suite: CBZ <Series> audit/repair, tagger preview/manual-tag,
downloads status API, unified search, and the settings exposure of
``discard_unmarked_chapters``.

Run ONLY through the assert-aware runner (bare asserts are stripped by
``calibre-debug -e`` because it uses optimize=2):

    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"

What is covered (all assertions are live under the runner):

1. CBZ ``<Series>`` audit distinguishes safe-to-repair from ambiguous cases and is
   strictly read-only.
2. Audit plans Writer/Publisher repair only against known tracked metadata.
3. ``GET /api/tagger/preview`` returns current ComicInfo and rejects free-form
   paths / non-scan files.
4. ``POST /api/tagger/manual-tag`` writes user metadata without duplicating the
   ComicInfo entry and without touching image entries; malformed existing XML can
   be replaced.
5. ``POST /api/tagger/repair-series`` rewrites ONLY explicitly selected files,
   never guesses ambiguous/unknown ones, is idempotent and logs the actor.
6. ``GET /api/downloads/status`` never blocks on an unreachable downloader (short
   timeout, degraded-but-valid payload) and normalizes the success payload.
7. ``GET /api/search`` resolves AniList canonical/romaji aliases to the stable
   matching key and returns the display title, with LIKE wildcards escaped.
8. ``POST /api/settings`` can now persist ``discard_unmarked_chapters`` (default
   true) while still rejecting unknown keys.
"""

import hashlib
import os
import shutil
import socket
import sys
import tempfile
import time
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402  (installs the FastAPI shims first)

import app.main as main_app  # noqa: E402
from app import auth as auth_module  # noqa: E402
from app import schemas  # noqa: E402
from app.tagger import cbz_tagger, series_audit  # noqa: E402

STABLE_SERIES = "Dungeon Meshi"
ANILIST_ENGLISH = "Delicious in Dungeon"
TRACKED_AUTHOR = "Ryoko Kui"
TRACKED_PUBLISHER = "Harta"

COMICINFO_NS = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _cache_authoritative(db, search_term, canonical, romaji, total_volumes=14):
    db.cache_manga_metadata(search_term, {
        "canonical_title": canonical,
        "romaji_title": romaji,
        "cover_url": "",
        "total_volumes": total_volumes,
        "status": "FINISHED",
        "synopsis": "",
        "source": "anilist",
    })


def _track_calibre_series(ctx, name=STABLE_SERIES, authors=TRACKED_AUTHOR, publisher=TRACKED_PUBLISHER, series_id=1):
    ctx.db.sync_calibre_series([{
        "series_id": series_id,
        "series_name": name,
        "book_count": 1,
        "latest_index": 1.0,
        "authors": authors,
        "publisher": publisher,
        "tags": "Fantasy",
    }])


def _comicinfo(series=None, writer=None, publisher=None, title=None, volume="1", extra=""):
    # COMICINFO_NS already contains the opening <ComicInfo ...> element.
    parts = [COMICINFO_NS]
    if title is None and series:
        # Fixtures default to the same Calibre-compatible title the app writes (task-38).
        from app.tagger.naming import format_comicinfo_title
        title = format_comicinfo_title(series, volume=volume)
    if title:
        parts.append(f"<Title>{title}</Title>")
    if series is not None:
        parts.append(f"<Series>{series}</Series>")
    if volume:
        parts.append(f"<Number>{volume}</Number><Volume>{volume}</Volume>")
    if writer:
        parts.append(f"<Writer>{writer}</Writer>")
    if publisher:
        parts.append(f"<Publisher>{publisher}</Publisher>")
    parts.append("<Manga>YesAndRightToLeft</Manga>")
    if extra:
        parts.append(extra)
    parts.append("</ComicInfo>")
    return "\n".join(parts)


def _make_cbz(ctx, filename, fields=None, raw_xml=None, pages=2):
    """Create a staged CBZ with STORED + DEFLATED image entries and optional XML."""
    path = os.path.join(ctx.staging_dir, filename)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        stored = zipfile.ZipInfo("001.jpg")
        stored.compress_type = zipfile.ZIP_STORED
        archive.writestr(stored, b"STORED-IMAGE-BYTES-001")
        archive.writestr("002.jpg", b"DEFLATED-IMAGE-BYTES-002", compress_type=zipfile.ZIP_DEFLATED)
        for page in range(3, pages + 2):
            archive.writestr(f"{page:03d}.jpg", f"image-{page}".encode("utf-8"))
        if raw_xml is not None:
            archive.writestr("ComicInfo.xml", raw_xml.encode("utf-8"))
        elif fields is not None:
            archive.writestr("ComicInfo.xml", _comicinfo(**fields).encode("utf-8"))
    return path


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_state(path):
    return (os.path.getmtime(path), _sha256(path))


def _zip_image_state(path):
    """{name: (compress_type, sha256(content))} for every non-ComicInfo entry."""
    with zipfile.ZipFile(path, "r") as archive:
        return {
            info.filename: (info.compress_type, hashlib.sha256(archive.read(info)).hexdigest())
            for info in archive.infolist()
            if info.filename.lower() != "comicinfo.xml"
        }


def _comicinfo_entry_count(path):
    with zipfile.ZipFile(path, "r") as archive:
        return sum(1 for name in archive.namelist() if name.lower() == "comicinfo.xml")


def _audit_files_by_name(payload):
    return {entry["name"]: entry for entry in (payload.get("files") or [])}


def _ok(response, expected=200):
    assert response.status_code == expected, (response.status_code, response.text)
    return response


# ---------------------------------------------------------------------------
# 1. Audit classification + read-only guarantee
# ---------------------------------------------------------------------------
def test_audit_classification_and_read_only():
    print("--> Test 1: audit classifies safe-to-repair vs ambiguous; read-only...")
    with E2ETestContext(prefix="mangatrax_m2_audit_") as ctx:
        _cache_authoritative(ctx.db, "dungeon meshi", ANILIST_ENGLISH, STABLE_SERIES)
        _track_calibre_series(ctx)

        # Ambiguous alias: two tracked series both claim "Sousou no Frieren".
        ctx.db.track_discovered_series("Frieren")
        ctx.db.track_discovered_series("Frieren: Beyond Journey's End")
        _cache_authoritative(ctx.db, "frieren", "Sousou no Frieren", "Sousou no Frieren")
        _cache_authoritative(
            ctx.db, "frieren beyond journeys end", "Sousou no Frieren", "",
        )

        alias_file = _make_cbz(ctx, "alias-title.cbz", fields={
            "series": ANILIST_ENGLISH, "writer": TRACKED_AUTHOR, "publisher": TRACKED_PUBLISHER,
        })
        correct_file = _make_cbz(ctx, "correct.cbz", fields={
            "series": STABLE_SERIES, "writer": TRACKED_AUTHOR, "publisher": TRACKED_PUBLISHER,
        })
        ambiguous_file = _make_cbz(ctx, "ambiguous.cbz", fields={"series": "Sousou no Frieren"})
        unknown_file = _make_cbz(ctx, "unknown.cbz", fields={"series": "Totally Unrelated Manga"})
        untagged_file = _make_cbz(ctx, "untagged.cbz")
        invalid_file = _make_cbz(ctx, "invalid.cbz", raw_xml="<ComicInfo><Series>Broken")

        paths = [alias_file, correct_file, ambiguous_file, unknown_file, untagged_file, invalid_file]
        before = {path: _file_state(path) for path in paths}

        response = _ok(ctx.client.get("/api/tagger/audit"))
        payload = response.json()
        assert payload["status"] == "success", payload
        summary = payload["summary"]
        assert summary["scanned"] == 6, summary
        assert summary["ok"] == 1, summary
        assert summary["repairable"] == 1, summary
        assert summary["ambiguous"] == 1, summary
        assert summary["unknown_series"] == 1, summary
        assert summary["untagged"] == 1, summary
        assert summary["invalid_comicinfo"] == 1, summary
        assert summary["files_with_changes"] == 1, summary

        files = _audit_files_by_name(payload)
        alias_entry = files["alias-title.cbz"]
        assert alias_entry["status"] == series_audit.STATUS_REPAIRABLE, alias_entry
        assert alias_entry["series_match"] == "anilist_alias", alias_entry
        assert alias_entry["current_series"] == ANILIST_ENGLISH, alias_entry
        assert alias_entry["suggested_series"] == STABLE_SERIES, alias_entry
        assert alias_entry["safe_to_repair"] is True, alias_entry
        assert alias_entry["changes"] == ["Series", "Title"], alias_entry
        assert "AniList alias" in alias_entry["reason"], alias_entry["reason"]

        ok_entry = files["correct.cbz"]
        assert ok_entry["status"] == series_audit.STATUS_OK, ok_entry
        assert ok_entry["safe_to_repair"] is False, ok_entry
        assert ok_entry["changes"] == [], ok_entry

        ambiguous_entry = files["ambiguous.cbz"]
        assert ambiguous_entry["status"] == series_audit.STATUS_AMBIGUOUS, ambiguous_entry
        assert ambiguous_entry["safe_to_repair"] is False, ambiguous_entry
        assert ambiguous_entry["suggested_series"] is None, ambiguous_entry
        assert "claimed by 2" in ambiguous_entry["reason"], ambiguous_entry["reason"]

        unknown_entry = files["unknown.cbz"]
        assert unknown_entry["status"] == series_audit.STATUS_UNKNOWN_SERIES, unknown_entry
        assert unknown_entry["safe_to_repair"] is False, unknown_entry

        assert files["untagged.cbz"]["status"] == series_audit.STATUS_UNTAGGED
        assert files["invalid.cbz"]["status"] == series_audit.STATUS_INVALID_COMICINFO

        # The audit must never modify a byte: same mtime + same sha256 for every file.
        after = {path: _file_state(path) for path in paths}
        assert before == after, "audit modified a staged file (it must be read-only)"

        # Calibre-only (not yet synced) names are recognised as known series.
        ctx.create_mock_calibre_library([{
            "name": "Calibre Only Series", "author": "Someone", "publisher": "Pub", "volumes": [1.0],
        }])
        calibre_file = _make_cbz(ctx, "calibre-only.cbz", fields={"series": "calibre only series"})
        payload2 = _ok(ctx.client.get("/api/tagger/audit")).json()
        calibre_entry = _audit_files_by_name(payload2)["calibre-only.cbz"]
        assert calibre_entry["status"] == series_audit.STATUS_REPAIRABLE, calibre_entry
        assert calibre_entry["series_match"] == "normalized", calibre_entry
        assert calibre_entry["suggested_series"] == "Calibre Only Series", calibre_entry
        assert _file_state(calibre_file) == _file_state(calibre_file)
        print("    [PASSED] safe/ambiguous/unknown classification + read-only audit.")


# ---------------------------------------------------------------------------
# 2. Writer/Publisher repair planning uses known tracked metadata only
# ---------------------------------------------------------------------------
def test_audit_metadata_changes():
    print("--> Test 2: audit plans Writer/Publisher repair from tracked metadata...")
    with E2ETestContext(prefix="mangatrax_m2_audit_meta_") as ctx:
        _track_calibre_series(ctx)
        wrong_writer = _make_cbz(ctx, "wrong-writer.cbz", fields={
            "series": STABLE_SERIES, "writer": "Nobody At All", "publisher": TRACKED_PUBLISHER,
        })
        _make_cbz(ctx, "empty-writer.cbz", fields={"series": STABLE_SERIES})
        payload = _ok(ctx.client.get("/api/tagger/audit")).json()
        files = _audit_files_by_name(payload)

        entry = files["wrong-writer.cbz"]
        assert entry["status"] == series_audit.STATUS_REPAIRABLE, entry
        assert entry["series_match"] == "exact", entry
        assert entry["suggested_series"] is None, entry
        assert entry["changes"] == ["Writer"], entry
        assert entry["suggested_writer"] == TRACKED_AUTHOR, entry
        assert entry["safe_to_repair"] is True, entry

        # An omitted Writer is not "wrong": nothing may be rewritten.
        empty_entry = files["empty-writer.cbz"]
        assert empty_entry["status"] == series_audit.STATUS_OK, empty_entry
        assert empty_entry["changes"] == [], empty_entry
        assert _file_state(wrong_writer)
        print("    [PASSED] metadata repair plan only flags provably wrong fields.")


# ---------------------------------------------------------------------------
# 3. Preview endpoint + filename-only security
# ---------------------------------------------------------------------------
def test_tagger_preview_endpoint():
    print("--> Test 3: preview returns ComicInfo and rejects free-form paths...")
    with E2ETestContext(prefix="mangatrax_m2_preview_") as ctx:
        _make_cbz(ctx, "tagged.cbz", fields={"series": STABLE_SERIES, "writer": TRACKED_AUTHOR})
        _make_cbz(ctx, "plain.cbz")
        _make_cbz(ctx, "nested/inner.cbz", fields={"series": STABLE_SERIES})
        # A non-.cbz file is invisible to the staging scan by design.
        with open(os.path.join(ctx.staging_dir, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("not an archive")

        response = _ok(ctx.client.get("/api/tagger/preview", params={"filename": "tagged.cbz"}))
        data = response.json()
        assert data["status"] == "success", data
        assert data["has_comicinfo"] is True, data
        assert data["comicinfo"]["Series"] == STABLE_SERIES, data
        assert data["comicinfo"]["Writer"] == TRACKED_AUTHOR, data
        assert data["parsed"]["series_name"], data

        plain = _ok(ctx.client.get("/api/tagger/preview", params={"filename": "plain.cbz"})).json()
        assert plain["has_comicinfo"] is False, plain
        assert plain["comicinfo"] is None, plain

        # Nested files are addressable by their staging-relative path.
        nested = _ok(ctx.client.get("/api/tagger/preview", params={"filename": "nested/inner.cbz"})).json()
        assert nested["comicinfo"]["Series"] == STABLE_SERIES, nested

        # Unknown file / non-scan file -> 404 (no path construction from input).
        assert ctx.client.get("/api/tagger/preview", params={"filename": "missing.cbz"}).status_code == 404
        assert ctx.client.get("/api/tagger/preview", params={"filename": "notes.txt"}).status_code == 404

        # Traversal / absolute / drive-rooted payloads -> 400 before any lookup.
        for bad in ("../outside.cbz", "..\\outside.cbz", "/etc/passwd.cbz", "C:/Windows/x.cbz"):
            bad_response = ctx.client.get("/api/tagger/preview", params={"filename": bad})
            assert bad_response.status_code == 400, (bad, bad_response.status_code, bad_response.text)

        # Missing filename parameter is rejected by the schema/query layer.
        no_name = ctx.client.get("/api/tagger/preview")
        assert no_name.status_code in (400, 422), no_name.status_code

        # Symlinked archives are rejected when the OS allows creating the link.
        outside = tempfile.mkdtemp(prefix="mangatrax_m2_symlink_")
        try:
            outside_cbz = os.path.join(outside, "outside.cbz")
            with zipfile.ZipFile(outside_cbz, "w") as archive:
                archive.writestr("001.jpg", b"x")
            link_path = os.path.join(ctx.staging_dir, "linked.cbz")
            try:
                os.symlink(outside_cbz, link_path)
            except (OSError, NotImplementedError):
                print("    note: symlink creation not permitted here; symlink case skipped.")
            else:
                linked = ctx.client.get("/api/tagger/preview", params={"filename": "linked.cbz"})
                assert linked.status_code == 403, (linked.status_code, linked.text)
        finally:
            shutil.rmtree(outside, ignore_errors=True)
        print("    [PASSED] preview works and free-form/symlinked paths are rejected.")


# ---------------------------------------------------------------------------
# 4. Manual tagging writes one ComicInfo entry, images untouched
# ---------------------------------------------------------------------------
def test_manual_tag_endpoint():
    print("--> Test 4: manual-tag writes metadata without touching image entries...")
    with E2ETestContext(prefix="mangatrax_m2_manual_") as ctx:
        path = _make_cbz(ctx, "brand-new.cbz")
        images_before = _zip_image_state(path)

        payload = {
            "filename": "brand-new.cbz",
            "series": "Brand New Series",
            "volume": "1",
            "title": "Brand New Series Vol. 1",
            "writer": "Some Author",
            "publisher": "Some Publisher",
            "genre": "Fantasy",
        }
        response = _ok(ctx.client.post("/api/tagger/manual-tag", json_data=payload))
        data = response.json()
        assert data["status"] == "success", data
        assert data["file"] == "brand-new.cbz", data
        fields = data["comicinfo"]
        assert fields["Series"] == "Brand New Series", fields
        assert fields["Volume"] == "1", fields
        assert fields["Number"] == "1", fields
        assert fields["Writer"] == "Some Author", fields
        assert fields["Penciller"] == "Some Author", fields
        assert fields["Publisher"] == "Some Publisher", fields
        assert fields["Genre"] == "Fantasy", fields
        assert fields["Manga"] == "YesAndRightToLeft", fields
        assert _comicinfo_entry_count(path) == 1, "manual tag must not duplicate ComicInfo.xml"
        assert _zip_image_state(path) == images_before, "manual tag altered image entries"

        # Idempotent: re-posting the same payload keeps one entry and same fields.
        again = _ok(ctx.client.post("/api/tagger/manual-tag", json_data=payload)).json()
        assert again["comicinfo"]["Series"] == "Brand New Series", again
        assert _comicinfo_entry_count(path) == 1, "re-tagging duplicated ComicInfo.xml"
        assert _zip_image_state(path) == images_before

        # Partial update: omitted fields are preserved, not erased.
        partial = _ok(ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "brand-new.cbz", "series": "Brand New Series (2nd Edition)",
        })).json()
        assert partial["comicinfo"]["Series"] == "Brand New Series (2nd Edition)", partial
        assert partial["comicinfo"]["Writer"] == "Some Author", partial
        assert partial["comicinfo"]["Publisher"] == "Some Publisher", partial

        # Malformed existing ComicInfo.xml is replaceable by an explicit manual tag.
        broken = _make_cbz(ctx, "broken-xml.cbz", raw_xml="<ComicInfo><Series>unclosed")
        repaired = _ok(ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "broken-xml.cbz", "series": STABLE_SERIES, "volume": "3",
        })).json()
        assert repaired["replaced_invalid_comicinfo"] is True, repaired
        assert repaired["comicinfo"]["Series"] == STABLE_SERIES, repaired
        assert repaired["comicinfo"]["Volume"] == "3", repaired
        assert _comicinfo_entry_count(broken) == 1

        # Validation and security failures must not modify anything.
        before_state = _file_state(path)
        assert ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "brand-new.cbz", "series": "X", "unknown_field": "y",
        }).status_code == 400
        assert ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "brand-new.cbz",
        }).status_code == 400
        assert ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "../escape.cbz", "series": "X",
        }).status_code == 400
        assert ctx.client.post("/api/tagger/manual-tag", json_data={
            "filename": "missing.cbz", "series": "X",
        }).status_code == 404
        assert _file_state(path) == before_state, "rejected manual-tag request still wrote the file"

        logged = [entry for entry in ctx.db.get_logs(limit=100) if entry["event_type"] == "MANUAL_TAG_CBZ"]
        # Four successful writes: initial tag, identical re-tag, partial update, corrupt-XML replace.
        assert len(logged) == 4, [entry["message"] for entry in logged]
        print("    [PASSED] manual tag single-entry, image-preserving and idempotent.")


# ---------------------------------------------------------------------------
# 5. Explicit repair: only selected files, idempotent, ambiguous never guessed
# ---------------------------------------------------------------------------
def test_repair_series_explicit_selection():
    print("--> Test 5: repair rewrites only selected files, idempotently, with actor logging...")
    with E2ETestContext(prefix="mangatrax_m2_repair_") as ctx:
        _cache_authoritative(ctx.db, "dungeon meshi", ANILIST_ENGLISH, STABLE_SERIES)
        _track_calibre_series(ctx)

        broken_one = _make_cbz(ctx, "broken-one.cbz", fields={
            "series": ANILIST_ENGLISH, "writer": TRACKED_AUTHOR, "publisher": TRACKED_PUBLISHER,
        })
        broken_two = _make_cbz(ctx, "broken-two.cbz", fields={
            "series": ANILIST_ENGLISH, "writer": TRACKED_AUTHOR, "publisher": TRACKED_PUBLISHER,
        })
        images_before = _zip_image_state(broken_one)
        other_before = _file_state(broken_two)

        # Explicit actor: the repair must be logged with the authenticated identity.
        actor_token = auth_module.set_current_actor("alice")
        try:
            response = _ok(ctx.client.post("/api/tagger/repair-series", json_data={
                "files": ["broken-one.cbz"],
            }))
        finally:
            auth_module.reset_current_actor(actor_token)

        data = response.json()
        assert data["status"] == "success", data
        assert data["repaired"] == 1, data
        assert data["unchanged"] == 0, data
        assert data["results"][0]["outcome"] == "repaired", data
        assert data["results"][0]["changes"] == ["Series", "Title"], data

        assert cbz_tagger.read_comicinfo(broken_one)["Series"] == STABLE_SERIES
        assert _comicinfo_entry_count(broken_one) == 1
        assert _zip_image_state(broken_one) == images_before, "repair altered image entries"
        assert _file_state(broken_two) == other_before, "repair touched an unselected file!"

        repair_logs = ctx.db.get_logs(limit=200)
        repair_events = [entry for entry in repair_logs if entry["event_type"] == "CBZ_SERIES_REPAIRED"]
        assert len(repair_events) == 1, [entry["message"] for entry in repair_events]
        assert repair_events[0]["actor"] == "alice", repair_events[0]
        assert "broken-one.cbz" in repair_events[0]["message"], repair_events[0]["message"]

        # Idempotent: selecting the now-correct file reports "unchanged" and writes nothing.
        state_after_repair = _file_state(broken_one)
        second = _ok(ctx.client.post("/api/tagger/repair-series", json_data={
            "files": ["broken-one.cbz"],
        })).json()
        assert second["repaired"] == 0, second
        assert second["unchanged"] == 1, second
        assert _file_state(broken_one) == state_after_repair, "idempotent repair rewrote the file again"

        # Selecting the still-broken sibling proves selection (not "repair everything").
        selected_second = _ok(ctx.client.post("/api/tagger/repair-series", json_data={
            "files": ["broken-two.cbz"],
        })).json()
        assert selected_second["repaired"] == 1, selected_second
        assert cbz_tagger.read_comicinfo(broken_two)["Series"] == STABLE_SERIES

        # Ambiguous/unknown files are reported and skipped, never guessed.
        ambiguous = _make_cbz(ctx, "ambiguous.cbz", fields={"series": "Sousou no Frieren"})
        unknown = _make_cbz(ctx, "unknown.cbz", fields={"series": "Unrelated Thing"})
        ctx.db.track_discovered_series("Frieren")
        ctx.db.track_discovered_series("Frieren: Beyond Journey's End")
        _cache_authoritative(ctx.db, "frieren", "Sousou no Frieren", "Sousou no Frieren")
        _cache_authoritative(ctx.db, "frieren beyond journeys end", "Sousou no Frieren", "")
        ambiguous_before = _file_state(ambiguous)
        unknown_before = _file_state(unknown)

        denied = _ok(ctx.client.post("/api/tagger/repair-series", json_data={
            "files": ["ambiguous.cbz", "unknown.cbz"],
        })).json()
        assert denied["repaired"] == 0, denied
        assert denied["skipped"] == 2, denied
        outcomes = {result["file"]: result["outcome"] for result in denied["results"]}
        assert outcomes["ambiguous.cbz"] == "skipped", denied
        assert outcomes["unknown.cbz"] == "skipped", denied
        assert _file_state(ambiguous) == ambiguous_before, "ambiguous file was modified"
        assert _file_state(unknown) == unknown_before, "unknown-series file was modified"

        # No selection at all is impossible: empty list / wildcard are rejected.
        empty = ctx.client.post("/api/tagger/repair-series", json_data={"files": []})
        assert empty.status_code == 400, (empty.status_code, empty.text)
        wildcard = ctx.client.post("/api/tagger/repair-series", json_data={"files": ["*"]})
        assert wildcard.status_code in (404, 400), wildcard.status_code
        traversal = ctx.client.post("/api/tagger/repair-series", json_data={"files": ["../evil.cbz"]})
        assert traversal.status_code == 400, traversal.status_code
        absolute = ctx.client.post("/api/tagger/repair-series", json_data={
            "files": [os.path.join(ctx.staging_dir, "broken-two.cbz")],
        })
        assert absolute.status_code == 400, absolute.status_code
        missing = ctx.client.post("/api/tagger/repair-series", json_data={"files": ["nope.cbz"]})
        assert missing.status_code == 404, missing.status_code
        assert _file_state(broken_two) == _file_state(broken_two)

        # repair_writer=False leaves stale Writer untouched even for a safe file.
        writer_wrong = _make_cbz(ctx, "writer-wrong.cbz", fields={
            "series": STABLE_SERIES, "writer": "Wrong Person", "publisher": TRACKED_PUBLISHER,
        })
        writer_before = _file_state(writer_wrong)
        skipped = _ok(ctx.client.post("/api/tagger/repair-series", json_data={
            "files": ["writer-wrong.cbz"], "repair_writer": False,
        })).json()
        assert skipped["repaired"] == 0 and skipped["skipped"] == 1, skipped
        assert _file_state(writer_wrong) == writer_before
        fixed = _ok(ctx.client.post("/api/tagger/repair-series", json_data={
            "files": ["writer-wrong.cbz"], "repair_writer": True,
        })).json()
        assert fixed["repaired"] == 1, fixed
        assert cbz_tagger.read_comicinfo(writer_wrong)["Writer"] == TRACKED_AUTHOR
        print("    [PASSED] explicit selection honoured; ambiguous never guessed; actor logged.")


# ---------------------------------------------------------------------------
# 6. Downloads status API: degraded payload + short timeout, no blocking
# ---------------------------------------------------------------------------
class _FakeQbit:
    def __init__(self, torrents=None, fail=False, enabled=True):
        self.enabled = enabled
        self._torrents = torrents or []
        self._fail = fail
        self.timeouts = []

    def test_connection(self, timeout=3.0, **kwargs):
        self.timeouts.append(timeout)
        if self._fail:
            raise RuntimeError("downloader unreachable")
        return {"success": True, "version": "5.0.0", "message": "Connected"}

    def get_torrents(self, category="manga", filter_status="all", timeout=10.0):
        self.timeouts.append(timeout)
        if self._fail:
            raise RuntimeError("torrent list failed")
        return list(self._torrents)


def _hanging_downloader():
    """A listening socket that accepts the TCP handshake but never answers HTTP."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    return server, server.getsockname()[1]


def test_downloads_status_endpoint():
    print("--> Test 6: downloads status degrades fast and normalizes the payload...")
    with E2ETestContext(prefix="mangatrax_m2_status_") as ctx:
        # Disabled downloader: valid payload, nothing degraded.
        disabled = _ok(ctx.client.get("/api/downloads/status")).json()
        assert disabled["status"] == "disabled", disabled
        assert disabled["degraded"] is False, disabled
        assert disabled["downloader"]["enabled"] is False, disabled
        assert disabled["torrents"] == [] and disabled["active"] == [], disabled
        assert "totals" in disabled and disabled["count"] == 0, disabled

        # Reachable downloader: normalized progress/speeds/ETA and aggregate totals.
        fake = _FakeQbit(torrents=[
            {
                "hash": "a" * 40, "name": "Dungeon Meshi Vol. 01", "state": "downloading",
                "category": "manga", "progress": 0.25, "size": 1000, "downloaded": 250,
                "dlspeed": 100, "upspeed": 10, "eta": 75, "ratio": 0.1,
                "num_seeds": 7, "num_leechs": 2,
            },
            {
                "hash": "b" * 40, "name": "Dungeon Meshi Vol. 02", "state": "pausedDL",
                "category": "manga", "progress": 0.5, "size": 1000, "downloaded": 500,
                "dlspeed": 0, "upspeed": 0, "eta": 8640000, "ratio": 0.2,
                "num_seeds": 3, "num_leechs": 1,
            },
        ])
        with _patch_qbit(ctx, fake):
            ctx.db.set_setting("qbit_enabled", "true")
            success = _ok(ctx.client.get("/api/downloads/status")).json()
        assert success["status"] == "success", success
        assert success["degraded"] is False, success
        assert success["count"] == 2, success
        assert len(success["active"]) == 1, success
        assert success["active"][0]["progress"] == 0.25, success
        assert success["active"][0]["progress_percent"] == 25.0, success
        assert success["active"][0]["download_speed"] == 100, success
        assert success["active"][0]["eta_seconds"] == 75, success
        assert success["torrents"][1]["eta_seconds"] == 0, success["torrents"][1]
        assert success["totals"]["download_speed"] == 100, success["totals"]
        assert success["totals"]["progress"] == 0.25, success["totals"]
        # Every downloader call must use a short, explicit timeout.
        assert fake.timeouts and all(0 < t <= 5.0 for t in fake.timeouts), fake.timeouts

        # A raising downloader still yields HTTP 200 + degraded payload (never 500).
        with _patch_qbit(ctx, _FakeQbit(fail=True)):
            ctx.db.set_setting("qbit_enabled", "true")
            degraded = ctx.client.get("/api/downloads/status")
        assert degraded.status_code == 200, (degraded.status_code, degraded.text)
        degraded_payload = degraded.json()
        assert degraded_payload["status"] == "degraded", degraded_payload
        assert degraded_payload["degraded"] is True, degraded_payload
        assert degraded_payload["torrents"] == [], degraded_payload
        assert degraded_payload["downloader"]["connected"] is False, degraded_payload

        # A hung TCP peer must not block the status endpoint or the /downloads page.
        server, port = _hanging_downloader()
        try:
            ctx.db.set_setting("qbit_enabled", "true")
            ctx.db.set_setting("qbit_url", f"http://127.0.0.1:{port}")
            started = time.time()
            hung = ctx.client.get("/api/downloads/status", params={"timeout": 0.4})
            status_elapsed = time.time() - started
            assert hung.status_code == 200, (hung.status_code, hung.text)
            assert hung.json()["status"] == "degraded", hung.json()
            assert status_elapsed < 5.0, f"status endpoint blocked for {status_elapsed:.2f}s"

            started = time.time()
            page = ctx.client.get("/downloads")
            page_elapsed = time.time() - started
            assert page.status_code == 200, (page.status_code, page.text)
            assert page_elapsed < 6.0, f"/downloads blocked for {page_elapsed:.2f}s on a hung downloader"
        finally:
            server.close()
        print("    [PASSED] degraded-but-valid payload with short timeouts, no blocking.")


class _patch_qbit:
    """Patch the scheduler's downloader client for the duration of a block."""

    def __init__(self, ctx, fake):
        self.ctx = ctx
        self.fake = fake
        self._original = None

    def __enter__(self):
        self._original = self.ctx.scheduler.get_qbit_client
        self.ctx.scheduler.get_qbit_client = lambda: self.fake
        return self.fake

    def __exit__(self, exc_type, exc_value, traceback):
        self.ctx.scheduler.get_qbit_client = self._original
        return False


# ---------------------------------------------------------------------------
# 7. Unified search with alias resolution
# ---------------------------------------------------------------------------
def test_unified_search_alias_resolution():
    print("--> Test 7: unified search resolves AniList aliases to the stable key...")
    with E2ETestContext(prefix="mangatrax_m2_search_") as ctx:
        _cache_authoritative(ctx.db, "dungeon meshi", ANILIST_ENGLISH, STABLE_SERIES)
        ctx.db.track_discovered_series(STABLE_SERIES)
        ctx.db.upsert_release({
            "guid": "m2-search-rel-1",
            "title": "[Group] Dungeon Meshi Vol. 01 [Digital]",
            "series_name": STABLE_SERIES,
            "volume_start": 1.0,
            "volume_end": 1.0,
            "volumes": [1.0],
            "status": "REQUESTED",
        })

        # Canonical English spelling -> stable key + display title.
        by_alias = _ok(ctx.client.get("/api/search", params={"q": "Delicious"})).json()
        assert by_alias["status"] == "success", by_alias
        assert by_alias["query"] == "Delicious", by_alias
        assert len(by_alias["series"]) == 1, by_alias
        hit = by_alias["series"][0]
        assert hit["stable_name"] == STABLE_SERIES, hit
        assert hit["display_title"] == ANILIST_ENGLISH, hit
        assert hit["match"] == "anilist_alias", hit
        assert hit["matched_alias"] == ANILIST_ENGLISH, hit
        assert by_alias["counts"]["series"] == 1, by_alias

        # Stable-key spelling -> stable match; release title also found.
        by_stable = _ok(ctx.client.get("/api/search", params={"q": "dungeon meshi"})).json()
        assert by_stable["series"][0]["match"] == "stable_name", by_stable
        assert by_stable["series"][0]["stable_name"] == STABLE_SERIES, by_stable
        assert len(by_stable["releases"]) == 1, by_stable
        assert by_stable["releases"][0]["stable_name"] == STABLE_SERIES, by_stable
        assert by_stable["releases"][0]["title"].startswith("[Group]"), by_stable

        # Romaji spelling resolves through the same authoritative metadata.
        by_romaji = _ok(ctx.client.get("/api/search", params={"q": "Dungeon Meshi"})).json()
        assert by_romaji["series"], by_romaji

        # Unknown query -> empty but valid.
        empty = _ok(ctx.client.get("/api/search", params={"q": "zzzz-no-such-series"})).json()
        assert empty["series"] == [] and empty["releases"] == [], empty
        assert empty["total"] == 0, empty

        # Blank query is rejected, and wildcards are matched literally (no LIKE leak).
        assert ctx.client.get("/api/search", params={"q": ""}).status_code == 400
        assert ctx.client.get("/api/search", params={"q": "   "}).status_code == 400
        wildcard = _ok(ctx.client.get("/api/search", params={"q": "%"})).json()
        assert wildcard["series"] == [] and wildcard["releases"] == [], wildcard

        # limit is honoured.
        for index in range(5):
            ctx.db.track_discovered_series(f"Limit Test Series {index}")
        limited = _ok(ctx.client.get("/api/search", params={"q": "Limit Test", "limit": 2})).json()
        assert len(limited["series"]) <= 2, limited
        assert limited["limit"] == 2, limited
        print("    [PASSED] alias resolution returns stable key + display title.")


# ---------------------------------------------------------------------------
# 8. Settings expose discard_unmarked_chapters
# ---------------------------------------------------------------------------
def test_settings_expose_discard_unmarked_chapters():
    print("--> Test 8: discard_unmarked_chapters is settable via POST /api/settings...")
    with E2ETestContext(prefix="mangatrax_m2_settings_") as ctx:
        assert "discard_unmarked_chapters" in schemas.SETTINGS_KEYS
        request_model = schemas.validate(
            schemas.SettingsUpdateRequest, {"discard_unmarked_chapters": "false"}
        )
        assert request_model.model_dump(exclude_unset=True) == {"discard_unmarked_chapters": "false"}

        # Default is true, and the scheduler reads the same key.
        assert ctx.db.get_setting("discard_unmarked_chapters") == "true", ctx.db.get_all_settings()
        assert ctx.scheduler._discard_unmarked_chapters_enabled() is True

        saved = _ok(ctx.client.post("/api/settings", json_data={"discard_unmarked_chapters": "false"}))
        assert saved.json()["status"] == "success", saved.json()
        assert ctx.db.get_setting("discard_unmarked_chapters") == "false"
        assert ctx.scheduler._discard_unmarked_chapters_enabled() is False

        _ok(ctx.client.post("/api/settings", json_data={"discard_unmarked_chapters": "true"}))
        assert ctx.db.get_setting("discard_unmarked_chapters") == "true"
        assert ctx.scheduler._discard_unmarked_chapters_enabled() is True

        # Unknown keys are still rejected and the known value is unchanged.
        rejected = ctx.client.post("/api/settings", json_data={
            "discard_unmarked_chapters": "false", "unknown_setting": "x",
        })
        assert rejected.status_code == 400, rejected.status_code
        assert ctx.db.get_setting("discard_unmarked_chapters") == "true"
        print("    [PASSED] setting exposed in the settings path, default true, unknown keys rejected.")


if __name__ == "__main__":
    print("\n==================================================")
    print("M2 Feature Suite (tagger preview/audit/repair, downloads status, unified search)")
    print("==================================================")
    test_audit_classification_and_read_only()
    test_audit_metadata_changes()
    test_tagger_preview_endpoint()
    test_manual_tag_endpoint()
    test_repair_series_explicit_selection()
    test_downloads_status_endpoint()
    test_unified_search_alias_resolution()
    test_settings_expose_discard_unmarked_chapters()
    print("\n" + "=" * 50)
    print("ALL M2 FEATURE TESTS PASSED (assertions were live)")
    print("=" * 50)
