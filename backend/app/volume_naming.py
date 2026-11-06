"""Previous-volume filename/title inheritance (task-69).

Pure helpers: locate a volume marker in a previous volume's filename or ComicInfo
Title and replace only the number, preserving zero-padding, prefixes and subtitle
suffixes (``Series v01.cbz`` → ``Series v02.cbz``,
``Series Vol. 01 - Subtitle.cbz`` → ``Series Vol. 02 - Subtitle.cbz``).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mangatrax.volume_naming")

# Marker forms: v01, V01, vol. 1, Vol 01, volume 01, vols 01.
_VOLUME_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?P<prefix>vol(?:ume)?s?\.?|v)(?![A-Za-z])"
    r"(?P<space>\s*\.?\s*)(?P<number>[0-9]{1,4})(?![0-9])"
)


def find_volume_tokens(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    tokens = []
    for match in _VOLUME_TOKEN_RE.finditer(str(text)):
        number = int(match.group("number"))
        tokens.append({
            "start": match.start(), "end": match.end(),
            "prefix": match.group("prefix"), "space": match.group("space"),
            "number": number, "number_text": match.group("number"),
        })
    return tokens


def _format_with_padding(new_number: int, width: int) -> str:
    return str(int(new_number)).zfill(max(1, width))


def replace_volume_number(text: str, previous_volume: float, target_volume: float,
                          require_previous: bool = True) -> Optional[str]:
    """Replace the marker's number in *text*; None when no safe unique marker exists."""
    if not text:
        return None
    tokens = find_volume_tokens(text)
    if len(tokens) != 1:
        return None
    target_int = int(round(float(target_volume)))
    try:
        previous_int = int(round(float(previous_volume)))
    except (TypeError, ValueError):
        return None
    matches = [token for token in tokens if token["number"] == previous_int]
    if len(matches) > 1:
        return None
    if not matches:
        if require_previous or len(tokens) != 1:
            return None
        matches = tokens
    token = matches[0]
    replacement = (
        token["prefix"] + token["space"]
        + _format_with_padding(target_int, len(token["number_text"]))
    )
    return text[:token["start"]] + replacement + text[token["end"]:]


def inherit_previous_name(previous_name: str, previous_volume: float,
                          target_volume: float) -> Optional[str]:
    """Filename template: keep extension and replace only the volume number."""
    name = str(previous_name or "").strip()
    if not name:
        return None
    stem, extension = os.path.splitext(name)
    replaced = replace_volume_number(stem, previous_volume, target_volume)
    if not replaced:
        return None
    return replaced + (extension or ".cbz")


def inherit_previous_title(previous_title: str, previous_volume: float,
                           target_volume: float) -> Optional[str]:
    title = str(previous_title or "").strip()
    if not title:
        return None
    return replace_volume_number(title, previous_volume, target_volume)


def assess_volume_naming(filename: str, parsed: Dict[str, Any],
                         comicinfo_title: str = "") -> Dict[str, Any]:
    """Return ``{needs_intervention, reason, suggested_*}`` for one staged file."""
    parsed = parsed or {}
    series = str(parsed.get("series_name") or "").strip()
    volumes = [value for value in (parsed.get("volumes") or []) if value]
    volume = parsed.get("volume_start")
    if volume is None and len(volumes) == 1:
        volume = volumes[0]
    reason = ""
    if not series:
        reason = "Series could not be parsed from the filename"
    elif volume is None or not volumes:
        reason = "Volume could not be parsed reliably from the filename"
    elif len(volumes) > 1:
        reason = "Multiple volumes/bundle detected — volume marker needs manual confirmation"
    else:
        stem = os.path.splitext(str(filename or ""))[0]
        tokens = find_volume_tokens(stem or "")
        matching = [token for token in tokens if token["number"] == int(round(float(volume)))]
        if not tokens:
            reason = "No volume marker in the filename (subtitle/mid-title marker needs manual naming)"
        elif len(tokens) > 1 and len({token['number'] for token in tokens}) > 1:
            reason = "Multiple volume markers in the filename — cannot choose safely"
        elif not matching:
            reason = "Filename volume marker does not match the parsed volume"
        elif comicinfo_title:
            title_tokens = find_volume_tokens(comicinfo_title)
            if len(title_tokens) > 1 and len({token['number'] for token in title_tokens}) > 1:
                reason = "Multiple volume markers in the ComicInfo title"
    return {
        "needs_intervention": bool(reason),
        "reason": reason,
        "parsed_series": series,
        "parsed_volume": volume,
        "suggested_volume": (
            _format_with_padding(int(round(float(volume))), 2) if volume is not None else ""
        ),
    }


def build_previous_volume_index(staging_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    """Read-only index of staged CBZ volumes per normalised series."""
    from app.tagger import cbz_tagger
    from app.parser import extract_manga_bundle_and_volumes, normalize_text

    index: Dict[str, List[Dict[str, Any]]] = {}
    if not staging_dir or not os.path.isdir(staging_dir):
        return index
    for root, dirs, files in os.walk(staging_dir):
        dirs.sort()
        for filename in sorted(files):
            if not filename.lower().endswith(".cbz"):
                continue
            path = os.path.join(root, filename)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            try:
                parsed = extract_manga_bundle_and_volumes(filename)
                title = ""
                tags = {}
                try:
                    tags = cbz_tagger.read_comicinfo(path) or {}
                    title = str(tags.get("Title") or "")
                except Exception:
                    tags = {}
                series = str(parsed.get("series_name") or tags.get("Series") or "").strip()
                volumes = [value for value in (parsed.get("volumes") or []) if value]
                if not volumes:
                    try:
                        volumes = [float(tags.get("Volume"))] if tags.get("Volume") else []
                    except (TypeError, ValueError):
                        volumes = []
                if not series or len(volumes) != 1:
                    continue
                key = normalize_text(series)
                index.setdefault(key, []).append({
                    "volume": float(volumes[0]), "filename": filename,
                    "title": title, "path": path,
                })
            except Exception:
                continue
    for rows in index.values():
        rows.sort(key=lambda item: item["volume"])
    return index


def find_previous_volume(index: Dict[str, List[Dict[str, Any]]], series: str,
                         volume: float) -> Optional[Dict[str, Any]]:
    from app.parser import normalize_text

    rows = index.get(normalize_text(series or "")) or []
    if not rows:
        return None
    target = float(volume)
    lower = [row for row in rows if float(row["volume"]) < target]
    if not lower:
        return None
    return max(lower, key=lambda row: float(row["volume"]))


def inherit_for_file(index: Dict[str, List[Dict[str, Any]]], parsed: Dict[str, Any]):
    """Return (previous, inherited_filename, inherited_title) or (None, None, None)."""
    series = str((parsed or {}).get("series_name") or "").strip()
    volumes = [value for value in ((parsed or {}).get("volumes") or []) if value]
    if not series or len(volumes) != 1:
        return None, None, None
    previous = find_previous_volume(index, series, float(volumes[0]))
    if not previous:
        return None, None, None
    filename = inherit_previous_name(previous["filename"], previous["volume"], volumes[0])
    title = inherit_previous_title(previous.get("title") or "", previous["volume"], volumes[0])
    return previous, filename, title
