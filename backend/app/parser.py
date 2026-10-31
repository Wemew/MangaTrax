import re
import unicodedata
from typing import Optional, List, Dict, Any, Tuple
from difflib import SequenceMatcher

def normalize_text(text: str) -> str:
    """Normalize text for consistent comparison: lowercase, unicode NFKD, strip punctuation."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    lowered = normalized.lower()
    replaced = re.sub(r"[-–—:_/\\]+", " ", lowered)
    cleaned = re.sub(r"[^\w\s]", "", replaced)
    return " ".join(cleaned.split())

def string_similarity(a: str, b: str) -> float:
    norm_a = normalize_text(a)
    norm_b = normalize_text(b)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0
    return SequenceMatcher(None, norm_a, norm_b).ratio()

def clean_manga_title(raw_title: str) -> str:
    """Strips release group tags like [Group], formats [CBZ], [Digital], resolutions, etc."""
    if not raw_title:
        return ""

    cleaned = raw_title.strip()

    # Strip file extensions if present
    cleaned = re.sub(r"\.(?:cbz|cbr|zip|rar|epub|pdf)$", "", cleaned, flags=re.IGNORECASE)

    # Remove brackets like [1080p], [Digital], [CBZ], [CBR], [Scanlation], (2020-2022)
    # But preserve volume ranges or numbers if inside brackets e.g. [Vol 1-5], (v01-v07), [v01-v20]
    def bracket_replacer(match):
        inner = match.group(0)
        unbracketed = inner[1:-1].strip()

        # Check if it's purely a year or year range like (2020), (2020-2022)
        if re.match(r"^(?:19|20)\d{2}(?:\s*[-–—~to]+\s*(?:19|20)\d{2})?$", unbracketed):
            return " "

        # Check if contains volume indicators like v01, vol 1, volume 1, or volume ranges like 01-07
        has_vol = re.search(r"(?:vol\.?|volume|v)\s*\d+", unbracketed, re.IGNORECASE)
        has_range = re.search(r"\b\d+\s*[-–—~to]+\s*\d+\b", unbracketed, re.IGNORECASE)

        if has_vol or (has_range and not re.search(r"(?:19|20)\d{2}", unbracketed)):
            return f" {unbracketed} "
        return " "

    cleaned = re.sub(r"\[.*?\]", bracket_replacer, cleaned)
    cleaned = re.sub(r"\(.*?\)", bracket_replacer, cleaned)

    # Strip format buzzwords
    buzzwords = [
        r"\bcbz\b", r"\bcbr\b", r"\bpdf\b", r"\bepub\b", r"\bzip\b", r"\brar\b",
        r"\bdigital\b", r"\bretail\b", r"\bscan\b", r"\bscanlation\b",
        r"\b1080p\b", r"\b720p\b", r"\bwebrip\b", r"\bmanga\b",
        r"\btranslated\b", r"\benglish\b", r"\beng\b", r"\bcomplete\b"
    ]
    for bw in buzzwords:
        cleaned = re.sub(bw, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"^[\s\-–—:;,|.]+|[\s\-–—:;,|.]+$", "", cleaned)
    return " ".join(cleaned.split())

def extract_manga_bundle_and_volumes(raw_title: str) -> Dict[str, Any]:
    """
    Parses manga titles for series name, bundle ranges (e.g. Vol 01-05), or single volumes.
    Always generates ALL individual volumes in bundle ranges (e.g., 1 to 7 for v1-v7).
    Returns:
    {
        "series_name": str,
        "is_bundle": bool,
        "volume_start": float,
        "volume_end": float,
        "volumes": List[float],
        "clean_title": str,
        "confidence": float
    }
    """
    cleaned = clean_manga_title(raw_title)

    result = {
        "raw_title": raw_title,
        "clean_title": cleaned,
        "series_name": cleaned,
        "is_bundle": False,
        "volume_start": None,
        "volume_end": None,
        "volumes": [],
        "confidence": 0.0
    }

    if not cleaned:
        return result

    # Pattern 1: Multi-volume bundle range:
    # Handles:
    # "Bleach v1-v7", "Bleach v01-v07", "Bleach v1-7", "Bleach Vol 1-7", "Bleach Vol. 1-7"
    # "Bleach Volumes 1 to 7", "Bleach Vol 1~7", "Bleach Vol. 01 - Vol. 07"
    bundle_pattern = re.compile(
        r"^(?P<series>.+?)\s*(?:[-–—:]\s*)?"
        r"(?:\b(?:volumes|volume|vol)\b\.?|\bv\.?)\s*(?P<start>\d+(?:\.\d+)?)"
        r"\s*[-–—~to]+\s*"
        r"(?:(?:volumes|volume|vol)\b\.?|\bv\.?)?\s*(?P<end>\d+(?:\.\d+)?)"
        r"(?:\s*(?:[:\-–—]|\b).*)?$",
        re.IGNORECASE
    )
    m_bundle = bundle_pattern.match(cleaned)
    if m_bundle:
        series = m_bundle.group("series").strip(" -–—:,|.")
        series = re.sub(r"(?i)\b(?:volumes|volume|vol\.?|book)\b$", "", series).strip(" -–—:,|.")
        try:
            start_vol = float(m_bundle.group("start"))
            end_vol = float(m_bundle.group("end"))
            if start_vol > end_vol:
                start_vol, end_vol = end_vol, start_vol

            # Generate all individual volumes from start to end (e.g. 1, 2, 3, 4, 5, 6, 7)
            vols = []
            curr = int(start_vol)
            while curr <= int(end_vol):
                vols.append(float(curr))
                curr += 1

            return {
                "raw_title": raw_title,
                "clean_title": cleaned,
                "series_name": series,
                "is_bundle": True,
                "volume_start": start_vol,
                "volume_end": end_vol,
                "volumes": vols,
                "confidence": 0.95
            }
        except ValueError:
            pass

    # Pattern 1b: Comma or plus separated bundle: e.g. "Bleach v01, v02, v03" or "Bleach Vol 1 + 2 + 3"
    list_bundle_pattern = re.compile(
        r"^(?P<series>.+?)\s*(?:[-–—:]\s*)?"
        r"(?:\b(?:volumes|volume|vol)\b\.?|\bv\.?)\s*(?P<vols_str>\d+(?:\s*[,+&]\s*(?:(?:volumes|volume|vol)\b\.?|\bv\.?)?\s*\d+)+)"
        r"(?:\s*(?:[:\-–—]|\b).*)?$",
        re.IGNORECASE
    )
    m_list = list_bundle_pattern.match(cleaned)
    if m_list:
        series = m_list.group("series").strip(" -–—:,|.")
        series = re.sub(r"(?i)\b(?:volumes|volume|vol\.?|book)\b$", "", series).strip(" -–—:,|.")
        raw_nums = re.findall(r"\d+", m_list.group("vols_str"))
        if len(raw_nums) >= 2:
            parsed_vols = sorted(list(set(float(n) for n in raw_nums)))
            return {
                "raw_title": raw_title,
                "clean_title": cleaned,
                "series_name": series,
                "is_bundle": True,
                "volume_start": parsed_vols[0],
                "volume_end": parsed_vols[-1],
                "volumes": parsed_vols,
                "confidence": 0.95
            }

    # Pattern 2: Single Volume:
    # e.g., "One Piece Vol 108", "Chainsaw Man Volume 16", "Jujutsu Kaisen v1", "Bleach Vol. 5"
    single_pattern = re.compile(
        r"^(?P<series>.+?)\s*(?:[-–—:,]\s*)?"
        r"(?:\b(?:volume|vol|book)\b\.?|\bv\.?)\s*(?P<vol>\d+(?:\.\d+)?)"
        r"(?:\s*(?:[:\-–—]|\b).*)?$",
        re.IGNORECASE
    )
    m_single = single_pattern.match(cleaned)
    if m_single:
        series = m_single.group("series").strip(" -–—:,|.")
        series = re.sub(r"(?i)\b(?:volume|vol\.?|book)\b$", "", series).strip(" -–—:,|.")
        try:
            vol = float(m_single.group("vol"))
            return {
                "raw_title": raw_title,
                "clean_title": cleaned,
                "series_name": series,
                "is_bundle": False,
                "volume_start": vol,
                "volume_end": vol,
                "volumes": [vol],
                "confidence": 0.95
            }
        except ValueError:
            pass

    # Pattern 3: Series + Number direct: e.g., "One Piece 108"
    p3 = re.compile(
        r"^(?P<series>[A-Za-z0-9\s\'\"?!,\.]+?)\s+[-–—]?\s*(?P<vol>\d+(?:\.\d+)?)"
        r"(?:\s*(?:[:\-–—]|\b).*)?$",
        re.IGNORECASE
    )
    m3 = p3.match(cleaned)
    if m3:
        series = m3.group("series").strip(" -–—:,|.")
        try:
            vol = float(m3.group("vol"))
            if not (1900 <= vol <= 2099): # filter out years
                return {
                    "raw_title": raw_title,
                    "clean_title": cleaned,
                    "series_name": series,
                    "is_bundle": False,
                    "volume_start": vol,
                    "volume_end": vol,
                    "volumes": [vol],
                    "confidence": 0.80
                }
        except ValueError:
            pass

    return result

def match_manga_series_in_calibre(extracted_series: str, calibre_series_list: list, threshold: float = 0.80) -> Optional[dict]:
    """Matches an extracted series title against known Calibre series tracking."""
    if not extracted_series or not calibre_series_list:
        return None

    norm_extracted = normalize_text(extracted_series)

    # 1. Exact match
    for c_ser in calibre_series_list:
        s_name = c_ser.get("series_name") or c_ser.get("name", "")
        if normalize_text(s_name) == norm_extracted:
            return c_ser

    # 2. Substring containment match
    for c_ser in calibre_series_list:
        s_name = c_ser.get("series_name") or c_ser.get("name", "")
        norm_c = normalize_text(s_name)
        if len(norm_c) >= 4 and len(norm_extracted) >= 4:
            if norm_c in norm_extracted or norm_extracted in norm_c:
                return c_ser

    # 3. Fuzzy similarity
    best_match = None
    best_score = threshold
    for c_ser in calibre_series_list:
        s_name = c_ser.get("series_name") or c_ser.get("name", "")
        score = string_similarity(extracted_series, s_name)
        if score > best_score:
            best_score = score
            best_match = c_ser

    return best_match
