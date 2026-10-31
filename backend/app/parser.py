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

BUZZWORDS = {
    # Format & container
    "cbz", "cbr", "pdf", "epub", "zip", "rar", "7z", "tar", "azw3", "mobi",
    "cbz ver", "cbz version", "cbr ver", "pdf ver", "epub ver", "ver", "version",
    # Image types & resolution
    "1080p", "720p", "480p", "2160p", "1600p", "4k", "8k", "hd", "sd", "hq", "lq",
    "avif", "webp", "png", "jpg", "jpeg",
    # Source & rip
    "digital", "retail", "scan", "scans", "scanlation", "scanlations",
    "webrip", "siterip", "site-rip", "site rip", "rip", "web-rip", "web",
    "raw", "raws", "tankoubon", "tankobon", "magazine", "doujinshi", "artbook",
    # Status
    "complete", "completed", "complete series", "completed series",
    "ongoing", "dropped", "hiatus", "end", "finished",
    "batch", "pack", "repack", "v2", "v3", "fixed", "remaster", "remastered",
    "uncensored", "censored", "r18", "r-18", "18+", "mature",
    # Media & type
    "manga", "manhwa", "manhua", "webtoon", "comic", "comics", "novel", "light novel", "ln",
    # Language
    "translated", "translation", "tl", "english", "eng", "en", "multi", "all languages",
    "all language", "japanese", "jp", "french", "fr", "spanish", "esp",
    "german", "ger", "italian", "ita", "portuguese", "pt", "russian", "ru",
    "chinese", "chi", "korean", "kor", "official", "official release", "series"
}

KNOWN_PUBLISHERS = {
    'seven seas', 'seven seas entertainment', 'airship', 'ghost ship',
    'yen press', 'yen on', 'j-novel club', 'j-novel heart',
    'one peace books', 'cross infinite world', 'tentai books', 'hanashi media',
    'square enix', 'square enix manga & books', 'kodansha', 'kodansha comics',
    'kodansha usa', 'viz', 'viz media', 'tokyopop', 'dark horse', 'dark horse comics',
    'titan', 'titan comics', 'titan manga', 'kaiten books', 'denpa', 'vertical',
    'seven seas siren', 'monogatari'
}

DEFAULT_KNOWN_GROUPS = [
    "danke-Empire", "LuCirAn", "1stKind", "TenmaLand", "Stick",
    "danke", "Empire", "1R0N", "NaBi", "aKraa", "LostYears",
    "Shinigami", "FlameScans", "AsuraScans", "ReaperScans", "ZeroScans",
    "Lu自主规制", "a-sloth"
]

def is_valid_group_candidate(clean: str) -> bool:
    clean = clean.strip()
    if not clean or len(clean) < 2 or len(clean) > 40:
        return False
    # If it's a year or year range like 2020 or 2012-2018
    if re.match(r"^(?:19|20)\d{2}(?:\s*[-–—~to/]\s*(?:19|20)\d{2})?$", clean):
        return False
    # If it's a volume or chapter tag like v01, Vol 1, Ch. 10
    if re.match(r"^(?:vol\.?|volume|v|ch\.?|chapter|c)\s*\d+", clean, re.IGNORECASE):
        return False
    # Pure numbers or ranges
    if re.match(r"^\d+(?:\s*[-–—~to/]\s*\d+)?$", clean):
        return False
    lower = clean.lower()
    if lower in BUZZWORDS:
        return False
    # If all tokens in it are buzzwords or technical markers
    tokens = [t.strip().lower() for t in re.split(r"[\s\-_.]+", clean) if t.strip()]
    if tokens and all(t in BUZZWORDS or re.match(r"^\d+p?$", t) or re.match(r"^(?:19|20)\d{2}$", t) for t in tokens):
        return False
    return True

def extract_release_group(raw_title: str, known_groups: Optional[Iterable[str]] = None) -> Optional[str]:
    """
    Extracts the scanlation or release group tag from raw release titles.
    Correctly ignores buzzwords, status tags like [Complete], [Completed],
    resolutions, and formats, and correctly identifies groups regardless
    of bracket position or scene naming conventions.
    """
    if not raw_title:
        return None

    groups_to_check = sorted(list(known_groups or []) + DEFAULT_KNOWN_GROUPS, key=len, reverse=True)

    # 1. Extract all bracketed candidates [Group] or (Group)
    bracket_pairs = re.findall(r'\[([^\]]+)\]|\(([^\)]+)\)', raw_title)
    all_candidates = []
    for b1, b2 in bracket_pairs:
        cand = (b1 or b2).strip()
        if is_valid_group_candidate(cand):
            all_candidates.append(cand)

    if all_candidates:
        # Match against known groups first
        for cand in all_candidates:
            for kg in groups_to_check:
                if cand.lower() == kg.lower():
                    return cand

        # Separate into non-publishers vs publishers
        non_publishers = [c for c in all_candidates if c.lower() not in KNOWN_PUBLISHERS]
        publishers = [c for c in all_candidates if c.lower() in KNOWN_PUBLISHERS]

        if non_publishers:
            if len(non_publishers) > 1 and all_candidates[0].lower() in KNOWN_PUBLISHERS:
                return non_publishers[-1]
            return non_publishers[0]
        if publishers:
            return publishers[0]

    # 2. Scene release group pattern: -GROUP before trailing brackets or end of string
    # e.g., Manga.Million.SiteRip.1600p.AVIF.2026-TenmaLand (Multi)
    m_scene = re.search(r'[-–—]([A-Za-z0-9_]{2,30})(?:\s*[\(\[]|\s*$)', raw_title)
    if m_scene:
        cand = m_scene.group(1).strip()
        if is_valid_group_candidate(cand) and cand.lower() not in KNOWN_PUBLISHERS:
            return cand

    # 3. Known groups substring search
    for kg in groups_to_check:
        escaped = re.escape(kg)
        if re.search(r'(?:^|[\s_.\-\(\[])' + escaped + r'(?:$|[\s_.\-\)\]])', raw_title, re.IGNORECASE):
            return kg

    return None

def is_light_novel(raw_title: str) -> bool:
    """
    Returns True if the release is identified as a Light Novel or Web Novel, not a Manga.
    MangaTrax exclusively tracks Manga releases (CBZ/CBR format).
    Detects:
      1. Explicit LN / Novel / Audiobook tags e.g. [LN], (LN), [Light Novel], [Novel], (Web Novel).
      2. Dedicated Light Novel imprints e.g. Yen On, Airship, Cross Infinite World.
      3. Ebook container formats without manga tags e.g. EPUB, MOBI, AZW3.
      4. Bracket pattern [Publisher] [ReleaseGroup] without manga tags:
         e.g. 'The Tale of a Little Alchemist Blessed by the Spirits v01-04 [Seven Seas] [Stick]'
      5. Standalone [Stick] release group tag without manga tags.
    """
    if not raw_title:
        return False
    t_lower = raw_title.lower()

    # If title explicitly specifies Manga or CBZ/CBR comic format, it's a valid manga release
    has_manga_marker = bool(re.search(r'\b(?:manga|cbz|cbr|comic)\b', t_lower))

    # 1. Explicit LN / Novel / Audiobook tags
    if re.search(r'\[(?:ln|light\s*novel|wn|web\s*novel|novel|novels|audiobook)\]|\((?:ln|light\s*novel|wn|web\s*novel|novel|novels|audiobook)\)', t_lower):
        if not has_manga_marker:
            return True
    if re.search(r'\b(?:light\s*novels?|web\s*novels?|audiobooks?)\b', t_lower):
        if not has_manga_marker:
            return True

    # 2. Dedicated Light Novel Imprints (Yen On is exclusively LN, Airship is Seven Seas LN line, etc.)
    if re.search(r'\b(?:yen\s*on|airship|cross\s*infinite\s*world|tentai\s*books|hanashi\s*media|seven\s*seas\s*siren)\b', t_lower):
        if not has_manga_marker:
            return True

    # 3. Dedicated ebook extensions without manga marker
    if re.search(r'\b(?:epub|mobi|azw3?)\b|\.(?:epub|mobi|azw3?)$', t_lower):
        if not has_manga_marker:
            return True

    # 4. Pattern: [Publisher] [ReleaseGroup] or (Publisher) [ReleaseGroup] without manga marker
    # e.g., 'The Tale of a Little Alchemist Blessed by the Spirits v01-04 [Seven Seas] [Stick]'
    bracket_pairs = re.findall(r'\[([^\]]+)\]|\(([^\)]+)\)', raw_title)
    clean_brackets = [b[0].strip() if b[0] else b[1].strip() for b in bracket_pairs if (b[0] or b[1])]
    if len(clean_brackets) >= 2 and not has_manga_marker:
        for i in range(len(clean_brackets) - 1):
            cand_pub = clean_brackets[i].lower()
            cand_grp = clean_brackets[i+1].lower()
            if cand_pub in KNOWN_PUBLISHERS:
                # Ensure the second bracket is not just a format buzzword or volume tag
                if cand_grp not in {'cbz', 'cbr', 'manga'} and not re.match(r'^(?:v|vol)\.?\s*\d+', cand_grp):
                    return True

    # 5. Standalone [Stick] release group without manga/cbz marker
    # 'Stick' exclusively rips light novels / audiobooks in EPUB format
    if re.search(r'\[(?:stick)\]|\((?:stick)\)', t_lower) and not has_manga_marker:
        return True

    return False

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

    # For scene dot-separated titles e.g. Manga.Million.SiteRip.1600p.AVIF.2026-TenmaLand
    # Replace dots between letters/digits with spaces while preserving decimal floats (e.g. 1.5)
    cleaned = re.sub(r'(?<=[a-zA-Z])\.(?=[a-zA-Z0-9])|(?<=[0-9])\.(?=[a-zA-Z])', ' ', cleaned)

    # Remove brackets like [1080p], [Digital], [CBZ], [CBR], [Scanlation], (2020-2022)
    # But preserve volume ranges or numbers if inside brackets e.g. [Vol 1-5], (v01-v07), [v01-v20]
    # Discard brackets that are purely chapter markers like (Ch. 1-7) or (Chapters 001-007)
    def bracket_replacer(match):
        inner = match.group(0)
        unbracketed = inner[1:-1].strip()

        # Check if it's purely a year or year range like (2020), (2020-2022)
        if re.match(r"^(?:19|20)\d{2}(?:\s*[-–—~to/]+\s*(?:19|20)\d{2})?$", unbracketed):
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

    # Strip format buzzwords, scene tokens, and status tags
    buzzwords = [
        r"\bcbz\b", r"\bcbr\b", r"\bpdf\b", r"\bepub\b", r"\bzip\b", r"\brar\b", r"\b7z\b",
        r"\bcbz\s*ver(?:sion)?\b", r"\bcbr\s*ver(?:sion)?\b", r"\bver(?:sion)?\b",
        r"\bdigital\b", r"\bretail\b", r"\bscan\b", r"\bscanlation\b", r"\bscans\b",
        r"\bsiterip\b", r"\bsite-rip\b", r"\bwebrip\b", r"\bweb-rip\b", r"\brip\b", r"\braws?\b",
        r"\b1080p\b", r"\b720p\b", r"\b480p\b", r"\b2160p\b", r"\b1600p\b", r"\b4k\b", r"\b8k\b", r"\bhd\b", r"\bsd\b", r"\bhq\b", r"\blq\b",
        r"\bavif\b", r"\bwebp\b",
        r"\bmanga\b", r"\bmanhwa\b", r"\bmanhua\b", r"\bwebtoon\b", r"\bcomic\b", r"\bcomics\b",
        r"\btranslated\b", r"\btranslation\b", r"\benglish\b", r"\beng\b", r"\bmulti\b", r"\ball\s+languages?\b",
        r"\bcompleted?\s*(?:series)?\b", r"\bcomplete\s*(?:series)?\b", r"\bbatch\b", r"\bpack\b", r"\brepack\b"
    ]
    for bw in buzzwords:
        cleaned = re.sub(bw, " ", cleaned, flags=re.IGNORECASE)

    # Strip scene year attached to scene tags or -Group
    scene_kw = r'(?:1080p|720p|480p|2160p|1600p|4k|8k|avif|webp|siterip|webrip|digital|cbz|cbr|raw)'
    cleaned = re.sub(rf'(?i)(?:\b{scene_kw}\b[.\s\-–—]+)\b(?:19|20)\d{{2}}\b', ' ', cleaned)
    cleaned = re.sub(r'\b(?:19|20)\d{2}\b(?=\s*[-–—]\s*[A-Za-z0-9_]+)', ' ', cleaned)

    cleaned = re.sub(r"^[\s\-–—:;,|.]+|[\s\-–—:;,|.]+$", "", cleaned)
    return " ".join(cleaned.split())

def extract_manga_bundle_and_volumes(raw_title: str, known_groups: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """
    Parses manga titles for series name, bundle ranges (e.g. Vol 01-05), or single volumes.
    Always generates ALL individual volumes in bundle ranges (e.g., 1 to 7 for v1-v7).
    Extracts release group and handles mixed volume/chapter updates by focusing on volumes.
    """
    cleaned = clean_manga_title(raw_title)
    release_group = extract_release_group(raw_title, known_groups=known_groups)

    if release_group:
        # Strip extracted release group from cleaned title if present (e.g. from scene format -Group)
        cleaned = re.sub(r'(?i)(?:[-–—]\s*)?' + re.escape(release_group) + r'\b', ' ', cleaned)
        cleaned = " ".join(cleaned.split()).strip(" -–—:,|.")

    result = {
        "raw_title": raw_title,
        "clean_title": cleaned,
        "series_name": cleaned,
        "release_group": release_group,
        "is_light_novel": is_light_novel(raw_title),
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
