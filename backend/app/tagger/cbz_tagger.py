import os
import zipfile
import logging
from xml.sax.saxutils import escape
from typing import Dict, Any, Optional, List
from app.parser import extract_manga_bundle_and_volumes, normalize_text

logger = logging.getLogger("mangatrax.tagger")

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
        with zipfile.ZipFile(cbz_path, "r") as z:
            names = [n.lower() for n in z.namelist()]
            return "comicinfo.xml" in names
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

                # Find series template in Calibre
                norm_name = normalize_text(s_name)
                template = series_templates.get(norm_name)

                if template:
                    metadata = {
                        "title": f"{template['series_name']} Vol. {vol}" if vol else template['series_name'],
                        "series_name": template["series_name"],
                        "volume": vol,
                        "authors": template.get("authors", ""),
                        "publisher": template.get("publisher", ""),
                        "tags": template.get("tags", "")
                    }
                    if inject_comicinfo(cbz_path, metadata):
                        tagged_files.append({
                            "filename": f,
                            "filepath": cbz_path,
                            "series_name": template["series_name"],
                            "volume": vol
                        })

    return tagged_files
