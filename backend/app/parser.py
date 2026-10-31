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

def extract_release_group(raw_title: str) -> Optional[str]:
    """
    Extracts the scanlation or release group tag from raw release titles.
    e.g., '[danke-Empire] Bleach v01-v20' -> 'danke-Empire'
          '[Lu自主规制] Chainsaw Man Vol. 01-11' -> 'Lu自主规制'
          '[1R0N] One Piece Vol. 108' -> '1R0N'
          'Bleach v01-v20 (danke-Empire)' -> 'danke-Empire'
    """
    if not raw_title:
        return None

    buzzwords = {
        "cbz", "cbr", "pdf", "epub", "zip", "rar",
        "1080p", "720p", "480p", "2160p", "4k", "hd", "sd", "webrip",
        "digital", "retail", "scan", "scanlation", "complete", "batch",
        "manga", "raw", "translated", "english", "eng", "repack"
    }

    def is_valid_group(name: str) -> bool:
        clean = name.strip()
        if not clean or len(clean) < 2 or len(clean) > 40:
            return False
        # If it's a year or year range like 2020 or 2020-2022
        if re.match(r"^(?:19|20)\d{2}(?:\s*[-–—~to]\s*(?:19|20)\d{2})?$", clean):
            return False
        # If it's a volume or chapter tag like v01, Vol 1, Ch. 10
        if re.match(r"^(?:vol\.?|volume|v|ch\.?|chapter|c)\s*\d+", clean, re.IGNORECASE):
            return False
        # If it's purely buzzwords
        lower = clean.lower()
        if lower in buzzwords:
            return False
        # If all words in it are buzzwords
        tokens = [t.strip().lower() for t in re.split(r"[\s\-_]+", clean) if t.strip()]
        if tokens and all(t in buzzwords for t in tokens):
            return False
        return True

    # 1. Leading bracket [Group] or (Group)
    m_lead = re.match(r"^\s*\[([^\]]+)\]", raw_title)
    if not m_lead:
        m_lead = re.match(r"^\s*\(([^\)]+)\)", raw_title)
    if m_lead:
        candidate = m_lead.group(1).strip()
        if is_valid_group(candidate):
            return candidate

    # 2. Brackets inside or at end of title e.g. "Bleach v01 (danke-Empire)"
    all_brackets = re.findall(r"\[([^\]]+)\]|\(([^\)]+)\)", raw_title)
    for b1, b2 in reversed(all_brackets):
        cand = (b1 or b2).strip()
        if is_valid_group(cand):
            return cand

    return None

def is_chapter_only(raw_title: str) -> bool:
    """
    Returns True if the release is purely a chapter update with no volume information.
    MangaTrax only tracks volumes and bundles; pure chapter releases are discarded.
    """
    if not raw_title:
        return False

    title = raw_title.lower()

    # Check for volume indicators
    # e.g., vol 1, volume 1, vol. 1, v01, v1-v7, volumes 1-5, (v01-v07)
    has_volume = bool(
        re.search(r"\b(?:volumes?|vol)\b\.?\s*\d+", title) or
        re.search(r"\bv\d+", title) or
        re.search(r"\(v\d+", title) or
        re.search(r"\[v\d+", title)
    )

    if has_volume:
        return False

    # Check for chapter indicators:
    # e.g., ch 100, ch. 100, chapter 100, chapters 1-5, c100, c01-c10
    has_chapter = bool(
        re.search(r"\b(?:chapters?|ch)\b\.?\s*\d+", title) or
        re.search(r"\bc\d+\b", title) or
        re.search(r"\(c\d+", title) or
        re.search(r"\[c\d+", title)
    )

    return has_chapter

def clean_manga_title(raw_title: str) -> str:
    """Strips release group tags like [Group], formats [CBZ], [Digital], resolutions, etc."""
    if not raw_title:
        return ""

    cleaned = raw_title.strip()

    # Strip file extensions if present
    cleaned = re.sub(r"\.(?:cbz|cbr|zip|rar|epub|pdf)$", "", cleaned, flags=re.IGNORECASE)

    # Remove brackets like [1080p], [Digital], [CBZ], [CBR], [Scanlation], (2020-2022)
    # But preserve volume ranges or numbers if inside brackets e.g. [Vol 1-5], (v01-v07), [v01-v20]
    # Discard brackets that are purely chapter markers like (Ch. 1-7) or (Chapters 001-007)
    def bracket_replacer(match):
        inner = match.group(0)
        unbracketed = inner[1:-1].strip()

        # Check if it's purely a year or year range like (2020), (2020-2022)
        if re.match(r"^(?:19|20)\d{2}(?:\s*[-–—~to]+\s*(?:19|20)\d{2})?$", unbracketed):
            return " "

        has_chap = re.search(r"(?:chapters?|ch|c)\.?\s*\d+", unbracketed, re.IGNORECASE)
        has_vol = re.search(r"(?:vol\.?|volume|v)\s*\d+", unbracketed, re.IGNORECASE)
        has_range = re.search(r"\b\d+\s*[-–—~to]+\s*\d+\b", unbracketed, re.IGNORECASE)

        # Pure chapter bracket -> discard
        if has_chap and not has_vol:
            return " "

        # Volume or volume range bracket -> preserve
        if has_vol or (has_range and not re.search(r"(?:19|20)\d{2}", unbracketed)):
            return f" {unbracketed} "
        return " "

    cleaned = re.sub(r"\[.*?\]", bracket_replacer, cleaned)
    cleaned = re.sub(r"\(.*?\)", bracket_replacer, cleaned)

    # For mixed updates: strip chapter notations outside brackets so only volumes remain
    cleaned = re.sub(r"(?i)\b(?:chapters?|ch)\b\.?\s*\d+(?:\s*[-–—~to]+\s*\d+)?(?:\s*\+\s*extra)?", " ", cleaned)
    cleaned = re.sub(r"(?i)\bc\d+(?:\s*[-–—~to]+\s*c?\d+)?\b", " ", cleaned)

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
    Extracts release group and handles mixed volume/chapter updates by focusing on volumes.
    """
    cleaned = clean_manga_title(raw_title)
    release_group = extract_release_group(raw_title)

    result = {
        "raw_title": raw_title,
        "clean_title": cleaned,
        "series_name": cleaned,
        "release_group": release_group,
        "is_bundle": False,
        "volume_start": None,
        "volume_end": None,
        "volumes": [],
        "confidence": 0.0
    }

    if not cleaned:
        return result

    # Pattern 1: Multi-volume bundle range:
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

            vols = []
            curr = int(start_vol)
            while curr <= int(end_vol):
                vols.append(float(curr))
                curr += 1

            return {
                "raw_title": raw_title,
                "clean_title": cleaned,
                "series_name": series,
                "release_group": release_group,
                "is_bundle": True,
                "volume_start": start_vol,
                "volume_end": end_vol,
                "volumes": vols,
                "confidence": 0.95
            }
        except ValueError:
            pass

    # Pattern 1b: Comma or plus separated bundle
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
                "release_group": release_group,
                "is_bundle": True,
                "volume_start": parsed_vols[0],
                "volume_end": parsed_vols[-1],
                "volumes": parsed_vols,
                "confidence": 0.95
            }

    # Pattern 2: Single Volume:
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
                "release_group": release_group,
                "is_bundle": False,
                "volume_start": vol,
                "volume_end": vol,
                "volumes": [vol],
                "confidence": 0.95
            }
        except ValueError:
            pass

    # Pattern 3: Series + Number direct
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
            if not (1900 <= vol <= 2099):
                return {
                    "raw_title": raw_title,
                    "clean_title": cleaned,
                    "series_name": series,
                    "release_group": release_group,
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
