import os
import re
import zipfile
import logging
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from typing import Dict, Any, Optional, List
from app.parser import extract_manga_bundle_and_volumes, normalize_text

logger = logging.getLogger("mangatrax.tagger")

_COMICINFO_CACHE: Dict[Any, bool] = {}

# ComicRack / Calibre / Komga / Kavita all read ComicInfo.xml from the archive
# root.  The comparison is case-insensitive (some taggers emit "comicinfo.xml").
COMICINFO_FILENAME = "ComicInfo.xml"

_NAMESPACE_RE = re.compile(r"\{[^}]*\}")


class ComicInfoError(Exception):
    """Base class for a ComicInfo.xml that could not be read."""


class UnreadableArchiveError(ComicInfoError):
    """The .cbz is not a readable ZIP archive."""


class MalformedComicInfoError(ComicInfoError):
    """The archive carries a ComicInfo.xml that is not well-formed XML."""


def _local_tag(tag: str) -> str:
    """Strip an XML namespace from a tag name (``{ns}Series`` -> ``Series``)."""
    return _NAMESPACE_RE.sub("", tag or "")


def _comicinfo_entry_names(names: List[str]) -> List[str]:
    return [name for name in names if name.lower() == COMICINFO_FILENAME.lower()]


def _read_raw_comicinfo(cbz_path: str) -> Optional[bytes]:
    """Return the raw ComicInfo.xml bytes, ``None`` when absent, raise when unreadable."""
    try:
        with zipfile.ZipFile(cbz_path, "r") as archive:
            names = _comicinfo_entry_names(archive.namelist())
            if not names:
                return None
            return archive.read(names[0])
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise UnreadableArchiveError(f"unreadable .cbz archive: {exc}") from exc


def read_comicinfo_xml(cbz_path: str) -> Optional[str]:
    """Return the raw ``ComicInfo.xml`` text stored in *cbz_path*.

    ``None`` means the archive simply has no ComicInfo.xml.  A missing/corrupt
    archive raises :class:`UnreadableArchiveError`; an unparseable document is
    left to :func:`read_comicinfo` / :func:`build_comicinfo_xml`.
    """
    raw = _read_raw_comicinfo(cbz_path)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def read_comicinfo(cbz_path: str) -> Optional[Dict[str, str]]:
    """Parse a CBZ's ``ComicInfo.xml`` into ``{element: text}``.

    Read-only: the archive is never modified.  Returns ``None`` when the file has
    no ComicInfo.xml; raises :class:`UnreadableArchiveError` for a broken archive
    and :class:`MalformedComicInfoError` for broken XML.
    """
    raw = _read_raw_comicinfo(cbz_path)
    if raw is None:
        return None

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise MalformedComicInfoError(f"malformed ComicInfo.xml: {exc}") from exc

    fields: Dict[str, str] = {}
    for child in list(root):
        if not isinstance(child.tag, str):
            continue  # comments / processing instructions
        fields[_local_tag(child.tag)] = (child.text or "").strip()
    return fields


def build_comicinfo_xml(existing_xml: Optional[str], updates: Dict[str, Any]) -> str:
    """Return a ComicInfo.xml document with *updates* applied element-wise.

    Only the named elements are replaced/added; every other element, its order and
    its value are preserved.  Passing ``existing_xml=None`` builds a fresh
    document containing just the supplied elements (plus nothing else, so callers
    decide the defaults).  Raises :class:`MalformedComicInfoError` when the
    existing document cannot be parsed instead of silently discarding its fields.
    """
    if existing_xml and existing_xml.strip():
        try:
            root = ET.fromstring(existing_xml.encode("utf-8"))
        except ET.ParseError as exc:
            raise MalformedComicInfoError(f"malformed ComicInfo.xml: {exc}") from exc
    else:
        root = ET.Element("ComicInfo")

    for field_name, value in updates.items():
        if value is None:
            continue
        text = str(value)
        nodes = [child for child in list(root) if isinstance(child.tag, str) and _local_tag(child.tag) == field_name]
        if nodes:
            nodes[0].text = text
            for duplicate in nodes[1:]:
                root.remove(duplicate)
        else:
            ET.SubElement(root, field_name).text = text

    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body


def rewrite_comicinfo(cbz_path: str, xml_content: str) -> bool:
    """Replace a CBZ's ``ComicInfo.xml`` without touching its image entries.

    ZIP has no in-place entry update, so the container is rebuilt in a temporary
    file next to the archive and atomically swapped in.  Every non-ComicInfo
    entry is copied with its original :class:`zipfile.ZipInfo` (name, timestamp,
    CRC and ``compress_type`` preserved), therefore:
      * image/archive payloads are never decoded, resized or re-encoded;
      * ``STORED`` entries stay byte-identical, and ``DEFLATED`` entries keep
        their original compression method;
      * duplicate ComicInfo.xml entries emitted by older appends are collapsed to
        exactly one entry, which is what makes the repair idempotent.
    Returns ``False`` (and leaves the original untouched) on any failure.
    """
    if not os.path.isfile(cbz_path):
        return False

    temp_path = f"{cbz_path}.mangatrax-rewrite-tmp"
    try:
        with zipfile.ZipFile(cbz_path, "r") as source:
            infos = [
                info for info in source.infolist()
                if info.filename.lower() != COMICINFO_FILENAME.lower()
            ]
            with zipfile.ZipFile(temp_path, "w") as destination:
                for info in infos:
                    destination.writestr(info, source.read(info))
                destination.writestr(COMICINFO_FILENAME, xml_content.encode("utf-8"))
        os.replace(temp_path, cbz_path)
        # The cache is keyed on (path, mtime); the rewritten file has a new mtime,
        # but drop everything anyway so no stale entry can survive a race.
        _COMICINFO_CACHE.clear()
        logger.info("Rewrote %s ComicInfo.xml in %s", COMICINFO_FILENAME, os.path.basename(cbz_path))
        return True
    except Exception as exc:  # noqa: BLE001 - a failed rewrite must never raise upward
        logger.error("Failed to rewrite ComicInfo.xml in %s: %s", cbz_path, exc)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False


def scan_staging_cbz_files(staging_dir: str) -> List[Dict[str, Any]]:
    """Enumerate the ``.cbz`` archives under *staging_dir* (read-only).

    Returns a deterministic list of ``{"name", "relative", "path", "size_bytes",
    "size_mb"}`` records.  ``name`` is the basename and ``relative`` is the
    staging-relative POSIX path — callers accept a client filename ONLY by
    matching it against this list, never by joining it into a filesystem path.
    Symlinked archives are deliberately listed (the resolver rejects them with a
    clear error instead of silently hiding a file the user can see on disk).
    """
    if not staging_dir or not os.path.isdir(staging_dir):
        return []

    entries: List[Dict[str, Any]] = []
    for root, dirs, files in os.walk(staging_dir):
        dirs.sort()
        for filename in sorted(files):
            if not filename.lower().endswith(".cbz"):
                continue
            full_path = os.path.join(root, filename)
            relative = os.path.relpath(full_path, staging_dir).replace(os.sep, "/")
            try:
                size_bytes = os.path.getsize(full_path)
            except OSError:
                size_bytes = 0
            entries.append({
                "name": filename,
                "relative": relative,
                "path": full_path,
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 * 1024), 2),
            })
    entries.sort(key=lambda entry: entry["relative"].lower())
    return entries


def generate_comicinfo_xml(metadata: Dict[str, Any]) -> str:
    """
    Generates standard ComicInfo.xml compliant with ComicRack, Calibre, Komga, and Kavita specs.
    """
    title = escape(str(metadata.get("title") or ""))
    series = escape(str(metadata.get("series_name") or ""))
    vol = str(metadata.get("volume") or "")
    if vol.endswith(".0"):
        vol = vol[:-2]

    writer = escape(str(metadata.get("authors") or ""))
    publisher = escape(str(metadata.get("publisher") or ""))
    genre = escape(str(metadata.get("tags") or ""))

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Title>{title}</Title>
  <Series>{series}</Series>
  <Number>{vol}</Number>
  <Volume>{vol}</Volume>
  <Writer>{writer}</Writer>
  <Penciller>{writer}</Penciller>
  <Publisher>{publisher}</Publisher>
  <Genre>{genre}</Genre>
  <Manga>YesAndRightToLeft</Manga>
</ComicInfo>
"""
    return xml.strip()

def has_comicinfo(cbz_path: str) -> bool:
    try:
        mtime = os.path.getmtime(cbz_path)
        cache_key = (cbz_path, mtime)
        if cache_key in _COMICINFO_CACHE:
            return _COMICINFO_CACHE[cache_key]

        with zipfile.ZipFile(cbz_path, "r") as z:
            found = False
            for n in z.namelist():
                if n.lower() == "comicinfo.xml":
                    found = True
                    break
            _COMICINFO_CACHE[cache_key] = found
            return found
    except Exception:
        return False

def inject_comicinfo(cbz_path: str, metadata: Dict[str, Any], overwrite: bool = False) -> bool:
    """
    Safely injects or updates ComicInfo.xml inside a .cbz archive without modifying any image files.
    """
    if not os.path.isfile(cbz_path):
        return False

    if not overwrite and has_comicinfo(cbz_path):
        logger.debug(f"{cbz_path} already contains ComicInfo.xml, skipping.")
        return False

    xml_content = generate_comicinfo_xml(metadata)
    try:
        # Standard ZIP append mode
        with zipfile.ZipFile(cbz_path, "a") as z:
            z.writestr("ComicInfo.xml", xml_content.encode("utf-8"))
        try:
            _COMICINFO_CACHE[(cbz_path, os.path.getmtime(cbz_path))] = True
        except Exception:
            pass
        logger.info(f"Successfully tagged {os.path.basename(cbz_path)} with series '{metadata.get('series_name')}' Vol {metadata.get('volume')}")
        return True
    except Exception as e:
        logger.error(f"Failed to inject ComicInfo into {cbz_path}: {e}")
        return False

def auto_tag_staging_cbz_files(staging_dir: str, series_templates: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Scans the staging directory for any completed .cbz files, matches them to existing
    Calibre series templates, and tags them automatically.
    """
    if not os.path.isdir(staging_dir):
        return []

    tagged_files = []
    # Walk directory to find .cbz files
    for root, _, files in os.walk(staging_dir):
        for f in files:
            if f.lower().endswith(".cbz"):
                cbz_path = os.path.join(root, f)
                # Parse title for series and volume
                parsed = extract_manga_bundle_and_volumes(f)
                s_name = parsed["series_name"]
                vol = parsed["volume_start"]
                if parsed.get("is_bundle") and parsed.get("volume_end") and parsed["volume_end"] != parsed["volume_start"]:
                    vol_display = f"{int(parsed['volume_start'])}-{int(parsed['volume_end'])}"
                elif vol:
                    vol_display = str(int(vol)) if vol == int(vol) else str(vol)
                else:
                    vol_display = ""

                # Find series template in Calibre
                norm_name = normalize_text(s_name)
                template = series_templates.get(norm_name)

                if template:
                    metadata = {
                        "title": f"{template['series_name']} Vol. {vol_display}" if vol_display else template['series_name'],
                        "series_name": template["series_name"],
                        "volume": vol_display or vol,
                        "authors": template.get("authors", ""),
                        "publisher": template.get("publisher", ""),
                        "tags": template.get("tags", "")
                    }
                    if inject_comicinfo(cbz_path, metadata):
                        tagged_files.append({
                            "filename": f,
                            "filepath": cbz_path,
                            "series_name": template["series_name"],
                            "volume": vol_display or vol
                        })

    return tagged_files
