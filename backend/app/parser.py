import re
import math
import unicodedata
from typing import Optional, List, Dict, Any, Tuple, Iterable, Set
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

# ---------------------------------------------------------------------------
# Canonical dual/alternate-title delimiter (SINGLE source of truth).
# ---------------------------------------------------------------------------
# Series titles such as "Hataraku Maou-sama! | The Devil is a Part-Timer!" or
# "Sousou no Frieren / Frieren: Beyond Journey's End" carry several alternate
# spellings separated by "|", "/", en/em dashes, " - ", "//" or ";".  Modules
# that need candidate spellings must call split_series_candidate_parts() rather
# than re-declaring this character class: the pattern used to be copy-pasted in
# the database, scheduler, Calibre scanner/matcher, parser and metadata clients.
#
# ``{extra}`` is appended to the delimiter character class, and ``{alt}`` adds
# the optional "//" and ";" separators that some (but not every) caller wants.
_CANDIDATE_DELIMITER_TEMPLATE = r"\s*[|/–—{extra}]\s*|\s+-\s+{alt}"
_CANDIDATE_ALT_SEPARATORS = r"|//|;"


def split_series_candidate_parts(
    text: str,
    extra_delimiters: str = "",
    alt_separators: bool = True,
) -> List[str]:
    """Split a series title into its candidate alternate-title parts.

    Returns the non-empty, whitespace-stripped parts.  A title without any
    delimiter yields a single-element list (the whole input); an empty/None
    input yields ``[]``.

    The exact delimiter set is defined once above and must not be duplicated
    elsewhere in the codebase.
    """
    if not text:
        return []
    pattern = _CANDIDATE_DELIMITER_TEMPLATE.format(
        extra=re.escape(extra_delimiters) if extra_delimiters else "",
        alt=_CANDIDATE_ALT_SEPARATORS if alt_separators else "",
    )
    return [part.strip() for part in re.split(pattern, text) if part.strip()]

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
    'seven seas siren', 'monogatari',
    # Official digital platforms / manga publishers that show up in Nyaa release
    # titles and are NOT scanlation groups.  Hyphen/space variants are listed so
    # ("K-MANGA"), "(K MANGA)" and "(KManga)" all normalise to a publisher.
    'k-manga', 'k manga', 'kmanga', 'manga up', 'manga up!', 'shueisha',
    'shueisha manga', 'bookwalker', 'comikey', 'azuki', 'mangamo', 'inkr',
    'coolmic', 'tappytoon', 'tapas', 'manta', 'yen plus', 'kodansha digital',
}

DEFAULT_KNOWN_GROUPS = [
    "danke-Empire", "LuCirAn", "1stKind", "TenmaLand", "Stick",
    "danke", "Empire", "1R0N", "NaBi", "aKraa", "LostYears",
    "Shinigami", "FlameScans", "AsuraScans", "ReaperScans", "ZeroScans",
    "Lu自主规制", "a-sloth"
]

# Imprints that exclusively publish light novels.  Only these may decide the
# "[Publisher] [ReleaseGroup] -> light novel" rule; the wider KNOWN_PUBLISHERS set
# also contains manga publishers/platforms (K-MANGA, Manga UP!, Viz, ...) and must
# never make a manga release look like a light novel.
LN_IMPRINT_PUBLISHERS = {
    'seven seas', 'seven seas entertainment', 'seven seas siren', 'airship', 'ghost ship',
    'yen press', 'yen on', 'j-novel club', 'j-novel heart', 'one peace books',
    'cross infinite world', 'tentai books', 'hanashi media', 'sol press', 'vertical',
}

MAX_BUNDLE_SPAN = 1000

# Common scanlation/format tag vocabulary that is sometimes reused as a release
# group by accident.  "compilation" was missing, so the format tag
# "(Digital-Compilation)" was accepted as a group and shadowed the real one
# ("(Oak)").  Rejecting these compound format tags never affects publishers or
# real groups: only candidates made *entirely* of these words are rejected.
COMPOUND_FORMAT_TAGS = {
    "compilation", "compilations", "digital compilation", "digital-compilation",
    "digital+digital-compilation", "digital + digital-compilation", "colored", "coloured",
}
BUZZWORDS |= COMPOUND_FORMAT_TAGS

# ---------------------------------------------------------------------------
# Ported NovelTrax guards (RELEASE_DETECTION_SPEC.md §2, §5, §6 — MangaTrax D3)
# ---------------------------------------------------------------------------
# Leading site prefixes: "download"/"free download" always, but "read"/"get" ONLY
# when followed by a separator.  The naive rule turned
# "Reading Our Way to a Happy New World" into "ing Our Way to a Happy New World".
_SITE_PREFIX_RE = re.compile(
    r"^(?:\s*(?:free\s+)?download\b\s*[:\-–—]?\s*|\s*(?:read|get)\s*[:\-–—]\s*)",
    re.IGNORECASE,
)

# Spec §5 Pattern 0: a parenthesised volume range after an edition number, e.g.
# "DanMachi Omnibus 1 (Vols 1-3)" -> "DanMachi Omnibus Vols 1-3", so the range
# expands while the edition number stays out of the series name.
_PAREN_OMNIBUS_RE = re.compile(
    r"^(?P<series>.+?)\s+\d+\s*\(\s*"
    r"(?P<range>(?:volumes?|vols?|books?|book)\.?\s*\d+(?:\.\d+)?\s*[-–—~to]+\s*"
    r"(?:(?:volumes?|vols?|books?|book)\.?\s*)?\d+(?:\.\d+)?)"
    r"\s*\)(?P<rest>.*)$",
    re.IGNORECASE,
)

# Spec §5 Pattern 1b: a BARE numeric range is a multi-volume bundle, never a
# chapter ("a range cannot be a chapter").  Zero-padded ranges ("01-02") are
# chapter notation in manga feeds and are deliberately NOT treated as volumes --
# they stay under the existing chapter policy ("Series 001-009" must not invent
# a volume mapping).
_BARE_VOLUME_RANGE_RE = re.compile(
    r"^(?P<series>.+?)\s*(?:[-–—:,]\s*)?"
    r"(?P<start>\d{1,3}(?:\.\d+)?)\s*[-–—~]\s*(?P<end>\d{1,3}(?:\.\d+)?)$"
)

# "<chapters A-B> as v<X>-<Y>" idiom.  MangaTrax takes the volumes from the
# "v<X>-<Y>" half (chapters 1-9 are volumes 1-2, not volumes 1-9) and keeps the
# chapter ranges as data.  Requires an explicit "as" AND an explicit volume token
# preceded by a chapter range, so it cannot eat an ordinary title.
_CHAPTER_AS_VOLUME_RE = re.compile(
    r"^(?P<series>.+?)\s+"
    r"(?P<chapter_start>\d{1,4}(?:\.\d+)?)\s*[-–—~]\s*(?P<chapter_end>\d{1,4}(?:\.\d+)?)"
    r"\s+as\s+"
    r"(?:volumes?|vols?|books?|v)\.?\s*"
    r"(?P<volume_start>\d{1,3}(?:\.\d+)?)"
    r"(?:\s*[-–—~]\s*(?P<volume_end>\d{1,3}(?:\.\d+)?))?"
    r"(?P<tail>.*)$",
    re.IGNORECASE,
)
# Trailing "+ <chapters C-D>" clauses are chapter data only, never extra volumes.
_CHAPTER_RANGE_TAIL_RE = re.compile(
    r"\+\s*(\d{1,4}(?:\.\d+)?)\s*[-–—~]\s*(\d{1,4}(?:\.\d+)?)"
)

# Explicit chapter markers, e.g. "Chapter 250", "Ch. 250", "Ch250", "c374", "#01".
# The marker may be followed IMMEDIATELY by the number ("Ch1-50") -- requiring a
# separator made "Ch1-50" fall through to the bare volume-range bundle path and
# produce 50 phantom volumes.  The trailing number requirement keeps ordinary words
# such as "Chronicles" safe.
_EXPLICIT_CHAPTER_NUMBER_RE = re.compile(
    r"(?i)(?:\b(?:chapters?|chap|ch)\.?\s*[-–—:]?\s*(\d{1,4}(?:\.\d+)?)"
    r"|\bc\.?\s*[-–—:]?\s*(\d{1,4}(?:\.\d+)?)"
    r"|#\s*(\d{1,4}(?:\.\d+)?))"
)
# Explicit chapter RANGE, same marker forms, e.g. "Ch1-50", "Ch. 1-50",
# "Chapter1-50", "c1-50", "#1-50", "Ch 1-50".  Used to record chapter_ranges
# (defect B: spaced forms previously dropped the range and kept only chapter_number).
_EXPLICIT_CHAPTER_RANGE_RE = re.compile(
    r"(?i)(?:\b(?:chapters?|chap|ch)\.?\s*[-–—:]?\s*|\bc\.?\s*[-–—:]?\s*|#\s*)"
    r"(?P<start>\d{1,4}(?:\.\d+)?)\s*[-–—~]\s*(?P<end>\d{1,4}(?:\.\d+)?)"
)
# A trailing standalone FORMAT descriptor in a series key ("Solo Leveling Manga",
# "... Webtoon", "... Comic").  It is a format word, not part of the name, and it
# breaks Calibre matching ("Solo Leveling Manga" scores ~0.65 against the library's
# "Solo Leveling").  "Novel" is deliberately NOT included: spec §9.2 keeps it
# ("Some Brand New Novel" is a real series name).
_TRAILING_FORMAT_WORD_RE = re.compile(
    r"(?i)\s*\b(?:manga|manhwa|manhua|webtoon|comic|comics)\s*$"
)


def strip_trailing_format_word(name: str) -> str:
    """Remove a trailing standalone format descriptor from a series key."""
    if not name:
        return name
    return _TRAILING_FORMAT_WORD_RE.sub("", str(name)).strip(" -–—:,|.")
# Re-release / fix revision marker: "(f)" = 1, "(f2)" = second, "(f10)" = tenth.
# Case-insensitive; bracket style accepted both ways.  It is metadata, never a
# series/volume/group signal.
_REVISION_RE = re.compile(r"[\(\[]\s*f\s*(\d{0,3})\s*[\)\]]", re.IGNORECASE)
# A title ending in a bare number: "... 250", "... - 250", "[250]".
_TRAILING_BARE_NUMBER_RE = re.compile(
    r"(?i)(?:[\s\-–—:(\[])(\d{1,4}(?:\.\d+)?)\s*[\]\)]?\s*$"
)
# A title ending in a bare chapter RANGE: "... 001-009", "... - 01-02".
_TRAILING_CHAPTER_RANGE_RE = re.compile(
    r"^(?P<series>.+?)[\s\-–—:|]+(?P<start>\d{1,4}(?:\.\d+)?)\s*[-–—~]\s*(?P<end>\d{1,4}(?:\.\d+)?)$"
)
# A title ending in a CHAIN of "+"-joined bare chapter ranges without an "as v<X>"
# mapping: "Not Quite Love 001-005 + 006-009" -> series "Not Quite Love", chapter
# ranges [[1,5],[6,9]].  Every range and the "+" separators are chapter data.
_TRAILING_CHAPTER_RANGE_CHAIN_RE = re.compile(
    r"^(?P<series>.+?)[\s\-–—:|]+"
    r"(?P<ranges>\d{1,4}(?:\.\d+)?\s*[-–—~]\s*\d{1,4}(?:\.\d+)?"
    r"(?:\s*\+\s*\d{1,4}(?:\.\d+)?\s*[-–—~]\s*\d{1,4}(?:\.\d+)?)+)$"
)
_RANGE_PAIR_RE = re.compile(
    r"(\d{1,4}(?:\.\d+)?)\s*[-–—~]\s*(\d{1,4}(?:\.\d+)?)"
)
# Composite "+ tail" segments (after a parsed volume range):
#   explicit volume continuation: "+ v10-12", "+ Vol 10", ", v11"
_VOLUME_TAIL_SEGMENT_RE = re.compile(
    r"(?:^|[+&,])\s*(?:\b(?:volumes?|vols?|books?|v)\.?)\s*(\d{1,3}(?:\.\d+)?)"
    r"(?:\s*[-–—~]\s*(?:\b(?:volumes?|vols?|books?|v)\.?\s*)?(\d{1,3}(?:\.\d+)?))?",
    re.IGNORECASE,
)
#   bare/zero-padded numeric continuation: "+ 047-051" -> chapter data only
_BARE_TAIL_RANGE_RE = re.compile(
    r"(?:^|[+&,])\s*(\d{1,4}(?:\.\d+)?)\s*[-–—~]\s*(\d{1,4}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
# A volume keyword anywhere in a title legitimises the number that follows it.
_VOLUME_KEYWORD_RE = re.compile(
    r"(?i)\b(?:volumes?|vols?|books?|parts?|pts?)\.?\s*\d|\bv\.?\s*\d"
)
# Parenthesised year range "(2024-2026)" -- the convention for a collected digital
# batch of a series (the batch is usually the full volume set).
_YEAR_RANGE_PAREN_RE = re.compile(
    r"\(\s*(?:19|20)\d{2}\s*[-–—~]\s*(?:19|20)\d{2}\s*\)"
)


def is_collected_release_candidate(raw_title: str) -> bool:
    """True for a "collected release" batch of a series, e.g.
    ``Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)``.

    Detection rule: a parenthesised YEAR RANGE is present (the convention for a
    full digital batch) and the title carries NO volume marker and NO chapter
    marker.  The word "complete" is not required.  Explicit volume markers always
    win, so a ``Vol 1-3`` release is never a candidate.

    Detection only -- the volume count is resolved later from authoritative
    metadata (the scheduler gates on a finished status and a known count).  A
    candidate that cannot be resolved is kept volume-less and visible.
    """
    if not raw_title:
        return False
    if not _YEAR_RANGE_PAREN_RE.search(raw_title):
        return False
    if _VOLUME_KEYWORD_RE.search(raw_title):
        return False
    if _EXPLICIT_CHAPTER_NUMBER_RE.search(raw_title):
        return False
    if is_chapter_only(raw_title) or _looks_like_chapter_title(raw_title):
        return False
    cleaned = clean_manga_title(raw_title)
    if _VOLUME_KEYWORD_RE.search(cleaned):
        return False
    if _parse_bare_volume_range(cleaned):
        return False
    return True


def _looks_like_chapter_title(raw_title: str) -> bool:
    """Diagnostic chapter flag (spec §3 / §5 Pattern 2) — never a discard decision.

    True when the title carries chapter information only:

    * the existing title-only chapter classifier fires (explicit Chapter/Ch./c/#/
      zero-padded/bracket-number/chapter-range markers); or
    * the CLEANED title ends in a bare SINGLE number with no volume keyword
      anywhere (spec Pattern 2, e.g. "Dandadan 250"), and that number is not an
      edition year.

    The trailing test runs on ``clean_manga_title()`` output, not the raw title:
    virtually every real release ends in metadata ("Dandadan 250 (Digital)
    (1r0n)"), which would otherwise defeat the check.

    MangaTrax's discard policy is unchanged: the scheduler's context-guarded
    chapter discard still decides whether a bare-number release is dropped (only
    when the base series is already known), and ``is_chapter_only()`` itself is
    untouched.
    """
    if not raw_title:
        return False
    if is_chapter_only(raw_title):
        return True
    if is_complete_series_release(raw_title):
        return False
    if _VOLUME_KEYWORD_RE.search(raw_title):
        return False
    cleaned = clean_manga_title(raw_title)
    if _VOLUME_KEYWORD_RE.search(cleaned):
        return False
    # A chain of "+"-joined bare chapter ranges is a chapter batch, never a volume
    # bundle: "Series 001-005 + 006-009" (and unpadded "Series 1-5 + 6-9").
    if _TRAILING_CHAPTER_RANGE_CHAIN_RE.match(cleaned):
        return True
    # A bare numeric range that passes the bundle guards is a volume bundle, and a
    # volume bundle is never a chapter (spec §5 Pattern 1b).
    if _parse_bare_volume_range(cleaned):
        return False
    m = _TRAILING_BARE_NUMBER_RE.search(cleaned.strip())
    if not m:
        return False
    try:
        number = float(m.group(1))
    except (TypeError, ValueError):
        return False
    # Edition years are not chapter numbers (spec §5 Pattern 2 year guard).
    if 1900 <= number <= 2099:
        return False
    return True


def strip_site_prefix(text: str) -> str:
    """Remove a leading download/read/get site prefix (spec §2 step 2)."""
    if not text:
        return text
    return _SITE_PREFIX_RE.sub("", str(text), count=1)


def _expand_volume_span(start: float, end: float) -> List[float]:
    """Integer-stepped expansion of a volume span (start/end may be fractional)."""
    steps = int(round(end - start))
    if steps <= 0:
        return [start]
    return [start + i for i in range(steps + 1)]


def _parse_composite_tail(tail: str) -> Tuple[List[float], List[List[float]]]:
    """Parse the ``+ ...`` continuation of a volume-range release.

    * an EXACT volume continuation ("... v01-09 + v10-12", "+ Vol 10", ", v11")
      extends the volume set -- the release really does continue past volume 9;
    * a bare/zero-padded numeric range ("... + 047-051") is CHAPTER data only and
      must never become volumes (same rule as a bare numeric range on its own).

    Returns ``(volumes, chapter_ranges)``.
    """
    if not tail:
        return [], []
    volumes: List[float] = []
    chapter_ranges: List[List[float]] = []
    for match in _VOLUME_TAIL_SEGMENT_RE.finditer(tail):
        try:
            start = float(match.group(1))
            end = float(match.group(2)) if match.group(2) else start
        except (TypeError, ValueError):
            continue
        if start > end:
            start, end = end, start
        if not (0 < start <= 300) or not (0 < end <= 300) or (end - start) > 100:
            continue
        for volume in _expand_volume_span(start, end):
            if volume not in volumes:
                volumes.append(volume)
    for match in _BARE_TAIL_RANGE_RE.finditer(tail):
        try:
            start = float(match.group(1))
            end = float(match.group(2))
        except (TypeError, ValueError):
            continue
        if start > end:
            start, end = end, start
        # An edition-year range ("(2023-2026)") is not chapter data.
        if 1900 <= start <= 2099 and 1900 <= end <= 2099:
            continue
        chapter_ranges.append([start, end])
    return volumes, chapter_ranges


def _parse_bare_volume_range(title: str) -> Optional[Dict[str, Any]]:
    """Parse a bare numeric range ("DanMachi 1-4") as a volume bundle.

    Returns ``None`` (fall through to the other patterns) unless the spec §5
    Pattern 1b guards hold: the series part is non-empty, ``0 < start <= 300``,
    ``start <= end <= 300`` and the span is at most 100 volumes.  Zero-padded
    numbers are chapter notation, not volumes, so they never match here.
    """
    if not title:
        return None
    if re.search(r"#\s*\d", title):
        return None
    # A chapter marker before the numeric range means this is a CHAPTER range, not
    # a volume bundle ("Solo Leveling Ch1-50"): never let Pattern 1b fire.
    if _EXPLICIT_CHAPTER_NUMBER_RE.search(title):
        return None
    m = _BARE_VOLUME_RANGE_RE.match(title.strip())
    if not m:
        return None
    start_text = m.group("start")
    end_text = m.group("end")
    # Zero-padded ("01-02", "001-009") = chapter notation in manga feeds.
    if re.match(r"^0\d", start_text) or re.match(r"^0\d", end_text):
        return None
    series = m.group("series").strip(" -–—:,|.")
    try:
        start = float(start_text)
        end = float(end_text)
    except ValueError:
        return None
    if not series:
        return None
    if not (0 < start <= 300) or not (start <= end <= 300) or (end - start) > 100:
        return None
    return {
        "series": series,
        "start": start,
        "end": end,
        "volumes": _expand_volume_span(start, end),
    }


def _extract_chapter_as_volume(title: str) -> Optional[Dict[str, Any]]:
    """Parse the "<chapters A-B> as v<X>-<Y> [+ <chapters C-D>]" idiom.

    Returns the volumes from the ``v`` half, the chapter ranges as data, and a
    ``clean_title`` with the whole clause (chapter range + literal "as" + volume
    token, plus any trailing "+ chapters" clause) removed, so the series name is
    everything before the chapter range.  ``None`` when the idiom does not apply
    (an explicit "as" plus an explicit volume token are both required).
    """
    if not title:
        return None
    m = _CHAPTER_AS_VOLUME_RE.match(title.strip())
    if not m:
        return None
    series = (m.group("series") or "").strip()
    if not series:
        return None
    try:
        chapter_start = float(m.group("chapter_start"))
        chapter_end = float(m.group("chapter_end"))
        volume_start = float(m.group("volume_start"))
        volume_end = float(m.group("volume_end")) if m.group("volume_end") else volume_start
    except (TypeError, ValueError):
        return None
    if volume_start > volume_end:
        volume_start, volume_end = volume_end, volume_start
    # Same sanity guards as Pattern 1b: real volume ranges are small.
    if not (0 < volume_start <= 300) or not (0 < volume_end <= 300) or (volume_end - volume_start) > 100:
        return None

    tail = m.group("tail") or ""
    chapter_ranges: List[List[float]] = [[min(chapter_start, chapter_end), max(chapter_start, chapter_end)]]
    for cm in _CHAPTER_RANGE_TAIL_RE.finditer(tail):
        cs = float(cm.group(1))
        ce = float(cm.group(2))
        chapter_ranges.append([min(cs, ce), max(cs, ce)])
    # The trailing "+ chapters" clause is chapter data only -- drop it from the
    # title used for series extraction (metadata brackets are dropped by
    # clean_manga_title as usual).
    tail_without_chapters = _CHAPTER_RANGE_TAIL_RE.sub(" ", tail)
    return {
        "series_prefix": series,
        "clean_title": f"{series} {tail_without_chapters}".strip(),
        "chapter_ranges": chapter_ranges,
        "volumes": _expand_volume_span(volume_start, volume_end),
        "volume_start": volume_start,
        "volume_end": volume_end,
    }


def _chapter_number_from_title(raw_title: str) -> Optional[float]:
    """Explicit chapter marker number (Chapter/Ch./c/NN) when the title has one."""
    if not raw_title:
        return None
    m = _EXPLICIT_CHAPTER_NUMBER_RE.search(raw_title)
    if not m:
        return None
    for group in m.groups():
        if group:
            try:
                return float(group)
            except ValueError:
                return None
    return None


def extract_revision(raw_title: str) -> Optional[int]:
    """Return the re-release/fix revision encoded in the title, or ``None``.

    ``(f)`` is the first re-release of a collection (revision 1), ``(f2)`` the
    second (2), ``(f10)`` the tenth.  Case-insensitive, ``[f2]`` accepted too.
    A revision is metadata: it never influences series, volume or group parsing.
    """
    if not raw_title:
        return None
    m = _REVISION_RE.search(raw_title)
    if not m:
        return None
    digits = (m.group(1) or "").strip()
    if not digits:
        return 1
    try:
        value = int(digits)
    except ValueError:
        return None
    return value if value > 0 else None


def _is_known_publisher(name: str) -> bool:
    return bool(name) and name.strip().lower() in KNOWN_PUBLISHERS


def extract_publisher(raw_title: str) -> Optional[str]:
    """Return the publisher named in a bracket/paren block, or ``None``.

    Publishers are not scanlation groups: they are recognised separately from the
    known-publisher list (``[Square Enix]``, ``(Yen Press)``, ...) and normalised
    by bracket style (either ``[]`` or ``()`` works; the bracket text is
    returned).  A publisher is a quality signal (official release), so it is
    surfaced on the parse result / release row rather than used for filtering.
    """
    if not raw_title:
        return None
    for candidate in re.findall(r"[\(\[]([^\)\]]+)[\)\]]", raw_title):
        candidate = candidate.strip()
        if candidate and candidate.lower() in KNOWN_PUBLISHERS:
            return candidate
    return None


_BRACE_TAG_RE = re.compile(r"\{[^{}]*\}")
_TRAILING_AS_RANGE_RE = re.compile(
    r"\s+(?:\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?)\s+as\s*$",
    re.IGNORECASE,
)
_TRAILING_AS_VOLUME_RE = re.compile(
    r"\s+as\s+(?:v(?:ol(?:ume)?s?)?\.?\s*)?\d+(?:\.\d+)?"
    r"(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*$",
    re.IGNORECASE,
)
_TRAILING_BARE_RANGE_RE = re.compile(
    r"\s+\d{1,4}(?:\.\d+)?\s*[-–]\s*\d{1,4}(?:\.\d+)?\s*$"
)


def clean_series_for_matching(series_name: str) -> str:
    """Conservative cleanup for matching: brace release tags + trailing range clauses.

    ``"[OSHI NO KO] {Plus}"`` -> ``"[OSHI NO KO]"``;
    ``"[OSHI NO KO] 001-130 as v01-13"`` -> ``"[OSHI NO KO]"``.
    Bracketed/parenthesised parts of real titles are preserved here (template
    resolution may additionally try a tag-stripped key).
    """
    text = str(series_name or "").strip()
    if not text:
        return ""
    cleaned = _BRACE_TAG_RE.sub(" ", text)
    for _ in range(3):
        previous = cleaned
        cleaned = _TRAILING_AS_RANGE_RE.sub("", cleaned)
        cleaned = _TRAILING_AS_VOLUME_RE.sub("", cleaned)
        cleaned = _TRAILING_BARE_RANGE_RE.sub("", cleaned)
        if cleaned == previous:
            break
    cleaned = cleaned.replace("{", " ").replace("}", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -–—:,|./")


def _volume_result(
    base: Dict[str, Any],
    series_name: str,
    *,
    is_bundle: bool,
    volume_start: float,
    volume_end: float,
    volumes: List[float],
    confidence: float,
) -> Dict[str, Any]:
    """Copy *base* (which carries the chapter diagnostics) and fill the volume fields."""
    out = dict(base)
    out.update({
        "series_name": series_name,
        "is_bundle": is_bundle,
        "volume_start": volume_start,
        "volume_end": volume_end,
        "volumes": volumes,
        "confidence": confidence,
    })
    return out


def _apply_chapter_series_strip(result: Dict[str, Any]) -> Dict[str, Any]:
    """Move a bare chapter number out of ``series_name`` into the chapter data.

    A chapter-only release parses as "<Series> <chapter>" (or "<Series> - <chapter>").
    Keeping the number in the series key pollutes the series identity: every
    chapter of an untracked series would become its own series ("Dandadan 250",
    "Dandadan 251", ...).  The explicit-marker path already returns the clean
    series ("Dandadan" + chapter 250), so the bare-number path is aligned here.

    ``numbered_series_name`` preserves the pre-strip title: the scheduler uses it
    to protect real numbered series names ("Mob Psycho 100") from being split.
    Volumes/bundles are never stripped.
    """
    if not result.get("is_chapter"):
        return result
    if result.get("volumes") or result.get("is_bundle"):
        return result
    cleaned = (result.get("series_name") or "").strip()
    if not cleaned:
        return result
    # A trailing CHAIN of "+"-joined bare chapter ranges ("Series 001-005 + 006-009"):
    # every range is chapter data and the whole chain (plus separators) is stripped
    # from the series key, so batches group under the real series.  The "as v<X>"
    # mapping path is handled before this helper and is untouched.
    chain_match = _TRAILING_CHAPTER_RANGE_CHAIN_RE.match(cleaned)
    if chain_match:
        stripped = chain_match.group("series").strip(" -–—:,|.")
        if not stripped:
            return result
        ranges = []
        for pair in _RANGE_PAIR_RE.finditer(chain_match.group("ranges")):
            try:
                start = float(pair.group(1))
                end = float(pair.group(2))
            except (TypeError, ValueError):
                continue
            ranges.append([min(start, end), max(start, end)])
        if not ranges:
            return result
        result["numbered_series_name"] = cleaned
        result["series_name"] = stripped
        result["chapter_ranges"] = ranges
        result["chapter_start"] = min(r[0] for r in ranges)
        result["chapter_end"] = max(r[1] for r in ranges)
        # A chain is a range of chapters, not one chapter number.
        result["chapter_number"] = None
        return result
    # A single trailing chapter RANGE ("Series 001-009") keeps the whole range as
    # chapter data and strips it as one unit, so the series key is not mangled into
    # "Series 001".
    range_match = _TRAILING_CHAPTER_RANGE_RE.match(cleaned)
    if range_match:
        stripped = range_match.group("series").strip(" -–—:,|.")
        if not stripped:
            return result
        result["numbered_series_name"] = cleaned
        result["series_name"] = stripped
        try:
            start = float(range_match.group("start"))
            end = float(range_match.group("end"))
        except (TypeError, ValueError):
            return result
        chapter_range = [min(start, end), max(start, end)]
        if chapter_range not in result.get("chapter_ranges", []):
            result["chapter_ranges"] = list(result.get("chapter_ranges") or []) + [chapter_range]
            result["chapter_start"] = min(r[0] for r in result["chapter_ranges"])
            result["chapter_end"] = max(r[1] for r in result["chapter_ranges"])
        return result
    match = _TRAILING_BARE_NUMBER_RE.search(cleaned)
    if not match:
        return result
    stripped = cleaned[: match.start()].strip(" -–—:,|.")
    if not stripped:
        return result
    result["numbered_series_name"] = cleaned
    result["series_name"] = stripped
    if result.get("chapter_number") is None:
        try:
            result["chapter_number"] = float(match.group(1))
        except (TypeError, ValueError):
            pass
    return result

def is_valid_group_candidate(clean: str) -> bool:
    clean = clean.strip()
    if not clean or len(clean) < 2 or len(clean) > 40:
        return False
    # Re-release/fix revision markers ("f", "f2", "f10") are metadata, never groups.
    if re.match(r"^f\s*\d{0,3}$", clean, re.IGNORECASE):
        return False
    # If it's a year or year range like 2020 or 2012-2018
    if re.match(r"^(?:19|20)\d{2}(?:\s*[-–—~to/]\s*(?:19|20)\d{2})?$", clean):
        return False
    # If it's a volume or chapter tag like v01, Vol 1, Ch. 10, Vols 1-3.
    # Plural keywords are included so a preserved bracket range such as
    # "(Vols 1-3)" is never mistaken for a release group.
    if re.match(r"^(?:vols?\.?|volumes?|v|ch\.?|chapters?|chap|c|books?|parts?|pts?)\s*\d+", clean, re.IGNORECASE):
        return False
    # Pure numbers or ranges
    if re.match(r"^\d+(?:\s*[-–—~to/]\s*\d+)?$", clean):
        return False
    lower = clean.lower()
    if lower in BUZZWORDS:
        return False
    # If all tokens in it are buzzwords or technical markers
    tokens = [t.strip().lower() for t in re.split(r"[\s\-_.+,&/]+", clean) if t.strip()]
    if tokens and all(t in BUZZWORDS or re.match(r"^\d+p?$", t) or re.match(r"^(?:19|20)\d{2}$", t) for t in tokens):
        return False
    return True

# Multi-group tag separators: "(1r0n, Ushi)", "(1r0n & Ushi)", "(1r0n/Ushi)",
# "(1r0n + Ushi)", "(1r0n and Ushi)".  Each piece is validated individually so a
# format tag ("Digital", "Digital-Compilation") or a publisher can never enter a
# multi-group list.
_GROUP_SPLIT_RE = re.compile(r"\s*(?:[,&+]|/|\band\b)\s*", re.IGNORECASE)


def split_release_group_value(value: str) -> List[str]:
    """Split a stored/tagged release-group value into validated group names.

    Returns the valid groups in order (deduplicated).  Used by the parser (multi-
    group tags), by the database backfill of legacy ``release_group`` values, and
    by the notifier.  Invalid pieces (format words, publishers, one-character
    tokens) are dropped.
    """
    if not value:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    groups: List[str] = []
    for part in _GROUP_SPLIT_RE.split(raw):
        part = part.strip()
        if not part:
            continue
        if not is_valid_group_candidate(part) or _is_known_publisher(part):
            continue
        if part not in groups:
            groups.append(part)
    return groups


def _split_group_list(candidate: str) -> Optional[List[str]]:
    """Split a multi-group bracket candidate into individually valid groups.

    ``(1r0n, Ushi)`` / ``(1r0n & Ushi)`` / ``(1r0n/Ushi)`` /
    ``(Rillant + Aquila + DigitalMangaFan)`` -> the validated list.  ``None`` when
    the candidate has no group separator (a single group) or nothing valid can be
    extracted from it.
    """
    if not candidate or not _GROUP_SPLIT_RE.search(candidate):
        return None
    groups = split_release_group_value(candidate)
    return groups if groups else None


# ---------------------------------------------------------------------------
# Positional bracket-block layout (canonical Nyaa order)
# ---------------------------------------------------------------------------
# Canonical layout: <Title> (<Author credits>) [Manga] [CBZ] [Vol 1-11 Complete] (Group A & Group B)
# Author credits sit immediately after the title, BEFORE the trailing cluster of
# format/volume/status blocks; release groups sit in that trailing cluster.
# Position (not vocabulary) separates credits from groups: "(Ryu Fujisaki/Fuyumi
# Ono)" splits into valid-looking candidates just like a joint group tag, so it
# must be excluded positionally.
_BRACKET_BLOCK_RE = re.compile(r"\[([^\]]+)\]|\(([^)]+)\)")
_COMPLETE_WORD_RE = re.compile(r"(?i)\b(?:complete|completed|full)\b")
_VOLUME_REFERENCE_RE = re.compile(
    r"(?i)(?:\b(?:volumes?|volume|vols?|vol|v)\b\.?\s*\d+|\b\d+\s*[-–—~]\s*\d+\b)"
)
_YEAR_BLOCK_RE = re.compile(r"^(?:19|20)\d{2}(?:\s*[-–—~]\s*(?:19|20)\d{2})?$")
_REVISION_BLOCK_RE = re.compile(r"(?i)^f\s*\d{0,3}$")
_AUTHOR_SPLIT_RE = re.compile(r"\s*(?:/|&|,|\band\b)\s*", re.IGNORECASE)


def _bracket_blocks(raw_title: str) -> List[Dict[str, Any]]:
    """All bracketed/parenthesised blocks in title order, with their spans."""
    blocks: List[Dict[str, Any]] = []
    for match in _BRACKET_BLOCK_RE.finditer(raw_title or ""):
        text = (match.group(1) or match.group(2) or "").strip()
        blocks.append({"text": text, "start": match.start(), "end": match.end()})
    return blocks


def _is_format_block(text: str) -> bool:
    """True when a block is pure format/buzzword metadata ([Manga], [CBZ], [Digital], ...)."""
    lower = (text or "").strip().lower()
    if not lower:
        return True
    if lower in BUZZWORDS:
        return True
    tokens = [token for token in re.split(r"[\s\-_.]+", lower) if token]
    if tokens and all(
        token in BUZZWORDS
        or re.match(r"^\d+p?$", token)
        or re.match(r"^(?:19|20)\d{2}$", token)
        for token in tokens
    ):
        return True
    return False


def _block_kind(text: str) -> str:
    """Classify one bracket block for the positional layout rule.

    ``group`` blocks are eligible release groups; ``revision`` markers are trailing
    metadata that is skipped while scanning (so ``(Group) (f2)`` still resolves);
    every other kind (format, volume, year, publisher, status/complete, empty,
    invalid) terminates the trailing cluster.
    """
    value = (text or "").strip()
    if not value:
        return "empty"
    if _YEAR_BLOCK_RE.match(value):
        return "year"
    if _REVISION_BLOCK_RE.match(value):
        return "revision"
    if _is_known_publisher(value):
        return "publisher"
    if _COMPLETE_WORD_RE.search(value):
        return "status"
    if _VOLUME_REFERENCE_RE.search(value):
        return "volume"
    if _is_format_block(value):
        return "format"
    if is_valid_group_candidate(value):
        return "group"
    return "invalid"


def _trailing_cluster_start(blocks: List[Dict[str, Any]]) -> int:
    """Index of the first block of the trailing group cluster.

    Scans right-to-left while blocks are group-eligible; revision markers ("(f)",
    "(f2)") are trailing metadata and are skipped rather than ending the cluster.
    """
    start = len(blocks)
    for index in range(len(blocks) - 1, -1, -1):
        kind = _block_kind(blocks[index]["text"])
        if kind == "revision":
            continue
        if kind == "group":
            start = index
            continue
        break
    return start


def _split_author_list(text: str) -> List[str]:
    """Split an author-credit block on ``/``, ``&``, ``,`` and ``and``."""
    authors: List[str] = []
    for part in _AUTHOR_SPLIT_RE.split(text or ""):
        name = part.strip(" .-–—")
        if len(name) < 2 or re.search(r"\d", name):
            continue
        if not re.search(r"[^\W\d_]", name):
            continue
        if _is_format_block(name) or _is_known_publisher(name):
            continue
        if name not in authors:
            authors.append(name)
    return authors


def _looks_like_author_block(text: str) -> bool:
    """True when a block is name-like author credits (no digits/formats/buzzwords).

    Only applied to blocks BEFORE the trailing group cluster, so a legitimate group
    tag can never become an author (see :func:`extract_authors`).
    """
    value = (text or "").strip()
    if not value or len(value) > 100:
        return False
    if re.search(r"\d", value):
        return False
    if re.search(r"[\[\](){}<>]", value):
        return False
    if not re.search(r"[^\W\d_]", value):
        return False
    if not re.fullmatch(r"[\w\s.,'’&/\-–—]+", value):
        return False
    if _is_format_block(value) or _is_known_publisher(value):
        return False
    if _COMPLETE_WORD_RE.search(value):
        return False
    words = [word for word in re.split(r"\s+", value) if word]
    if len(words) < 2:
        return False
    return bool(_split_author_list(value))


def extract_authors(raw_title: str, known_groups: Optional[Iterable[str]] = None) -> List[str]:
    """Extract author credits from the positional pre-cluster credit blocks.

    Credits canonically sit immediately after the title, before the trailing
    cluster of format/volume/status/group blocks; only name-like blocks in that
    region are credits.  Returns ``[]`` when there are none.
    """
    if not raw_title:
        return []
    groups_to_check = sorted(list(known_groups or []) + DEFAULT_KNOWN_GROUPS, key=len, reverse=True)
    blocks = _bracket_blocks(raw_title)
    if not blocks:
        return []
    cluster_start = _trailing_cluster_start(blocks)
    authors: List[str] = []
    for block in blocks[:cluster_start]:
        text = block["text"]
        if any(text.lower() == known.lower() for known in groups_to_check):
            continue
        if not _looks_like_author_block(text):
            continue
        for name in _split_author_list(text):
            if name not in authors:
                authors.append(name)
    return authors


def extract_release_groups(raw_title: str, known_groups: Optional[Iterable[str]] = None) -> List[str]:
    """Extract release groups as an ordered list; the first entry is primary.

    Positional rule (canonical Nyaa layout):

    1. known / prioritized groups win, in bracket order (credit blocks excluded);
    2. the group cluster is the TRAILING suffix of group-eligible blocks: scan
       right-to-left while blocks are group-eligible (skipping trailing revision
       markers) and stop at the first format/volume/year/publisher/status block.
       Only blocks INSIDE that cluster can be groups -- credit blocks before it are
       authors, never groups.  Unknown blocks inside the cluster use prefer-last
       (``... (Bogus) (RealGroup)`` -> RealGroup), while a joint tag keeps its own
       split order ("(GroupA/GroupB)" -> GroupA primary);
    3. a legacy last-valid-block fallback for layouts without a format block before
       the group, then the scene-style ``-GROUP`` suffix, then a bounded
       known-group substring.
    """
    if not raw_title:
        return []

    groups_to_check = sorted(list(known_groups or []) + DEFAULT_KNOWN_GROUPS, key=len, reverse=True)
    blocks = _bracket_blocks(raw_title)
    cluster_start = _trailing_cluster_start(blocks)

    # Credit blocks are positional: name-like, before the trailing cluster, and not
    # naming a known group.  They may never become release groups.
    author_indices: Set[int] = set()
    for index, block in enumerate(blocks[:cluster_start]):
        text = block["text"]
        if any(text.lower() == known.lower() for known in groups_to_check):
            continue
        if _looks_like_author_block(text):
            author_indices.add(index)

    ordered: List[str] = []

    def _add(name: str):
        if name and not _is_known_publisher(name) and name not in ordered:
            ordered.append(name)

    def _add_block(index: int):
        text = blocks[index]["text"].strip()
        if not is_valid_group_candidate(text) or _is_known_publisher(text):
            return
        split = _split_group_list(text)
        if split is None and _GROUP_SPLIT_RE.search(text):
            # A multi-piece block whose pieces are ALL format words/publishers
            # ("Webtoon, CBZ") is metadata, not a group -- never fall back to the
            # whole string.
            return
        for part in (split or [text]):
            _add(part)

    # 1. Known/prioritized groups win (bracket order; credits excluded).
    for index, block in enumerate(blocks):
        if index in author_indices:
            continue
        text = block["text"].strip()
        if not is_valid_group_candidate(text) or _is_known_publisher(text):
            continue
        for known in groups_to_check:
            if text.lower() == known.lower() and not _is_known_publisher(known):
                _add(text)

    # 2. Trailing group cluster, prefer-last for unknown blocks.
    for index in range(len(blocks) - 1, cluster_start - 1, -1):
        if index in author_indices:
            continue
        _add_block(index)
    if ordered:
        return ordered

    # 3. Legacy fallback: last valid non-credit bracket block (layouts without a
    #    format block between the group and the end of the title).
    for index in range(len(blocks) - 1, -1, -1):
        if index in author_indices:
            continue
        _add_block(index)
        if ordered:
            return ordered

    # 4. Scene release group pattern: -GROUP before trailing brackets or end of string
    # e.g., Manga.Million.SiteRip.1600p.AVIF.2026-TenmaLand (Multi)
    m_scene = re.search(r'[-–—]([A-Za-z0-9_]{2,30})(?:\s*[\(\[]|\s*$)', raw_title)
    if m_scene:
        cand = m_scene.group(1).strip()
        if is_valid_group_candidate(cand) and not _is_known_publisher(cand):
            return [cand]

    # 5. Known groups substring search (publishers are not groups)
    for known in groups_to_check:
        if _is_known_publisher(known):
            continue
        escaped = re.escape(known)
        if re.search(r'(?:^|[\s_.\-\(\[])' + escaped + r'(?:$|[\s_.\-\)\]])', raw_title, re.IGNORECASE):
            return [known]

    return []


def extract_release_group(raw_title: str, known_groups: Optional[Iterable[str]] = None) -> Optional[str]:
    """Extract the primary scanlation/release group from a raw release title.

    Thin wrapper over :func:`extract_release_groups` (kept for call-site
    compatibility); the full ordered list is available from that function and is
    exposed as ``release_groups`` in the parse result.
    """
    groups = extract_release_groups(raw_title, known_groups=known_groups)
    return groups[0] if groups else None

def is_jpegxl_release(raw_title: str) -> bool:
    """True when the release is a JPEG XL / JXL image-format release.

    The user explicitly does not want any JPEG XL manga downloaded; these releases
    are discarded by the feed filters and typed ``jpegxl`` (hidden from the manga
    views, marked ``[JPEGXL]`` where stored rows are shown).  Matches the explicit
    tags (``[JPEGXL]``, ``(JPEG XL)``, ``[JXL]``), bare words (``JPEGXL``,
    ``JPEG XL``, ``JXL``) and the ``.jxl`` suffix without matching inside other
    words.
    """
    if not raw_title or not isinstance(raw_title, str):
        return False
    return bool(re.search(
        r"(?i)(?:[\[\(]\s*(?:jpeg\s*-?\s*xl|jxl)\s*[\]\)]"
        r"|\b(?:jpeg\s*-?\s*xl|jxl)\b"
        r"|\.jxl\b)",
        raw_title,
    ))


def is_light_novel(raw_title: str) -> bool:
    """
    Returns True if the release is identified as a Light Novel or Web Novel, not a Manga.
    MangaTrax exclusively tracks Manga releases (CBZ/CBR format).

    Paren/bracket convention (RELEASE_DETECTION_SPEC.md §4.5): manga releases put
    release metadata and groups in **parentheses** -- ``(2025-2026) (Digital)
    (1r0n, Anon)`` -- while light novels put imprint/group tags in **square
    brackets** -- ``[Seven Seas] [Stick]``.  A parenthesised *group or imprint
    name* is therefore manga-style metadata and must never decide "light novel" on
    its own (``Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)`` is a
    genuine manga release).  Media-type words (``(Light Novel)``, ``[LN]``,
    ``(Novel)``, ``[EPUB]``) state the format itself and stay decisive in either
    bracket style.

    Detects:
      1. Explicit LN / Novel / Audiobook media-type tags e.g. [LN], (LN), [Light Novel], (Novel).
      2. Dedicated Light Novel imprints e.g. [Yen On], [Airship], [Seven Seas Siren]
         (square-bracketed or bare, never inside parentheses).
      3. Ebook container formats without manga tags e.g. EPUB, MOBI, AZW3.
      4. Square-bracket [Publisher] [ReleaseGroup] pairs without manga tags:
         e.g. 'The Tale of a Little Alchemist Blessed by the Spirits v01-04 [Seven Seas] [Stick]'
      5. Standalone [Stick] release group tag (square brackets only) without manga tags.
    """
    if not raw_title:
        return False
    t_lower = raw_title.lower()

    # If title explicitly specifies Manga or CBZ/CBR comic format, it's a valid manga release
    has_manga_marker = bool(re.search(r'\b(?:manga|cbz|cbr|comic)\b', t_lower))

    # Parenthesised metadata is removed before the imprint/group rules run: in the
    # manga convention "(Stick)" is a scanlation group tag, not an LN signal.
    t_without_parens = re.sub(r"\([^)]*\)", " ", raw_title).lower()

    # 1. Explicit media-type tags -- these state the format itself, so both bracket
    #    styles are decisive: [LN], (LN), [Light Novel], (Novel), [Audiobook], ...
    if re.search(r'[\[\(]\s*(?:ln|light\s*novel|wn|web\s*novel|novels?|audiobook)\s*[\]\)]', t_lower):
        if not has_manga_marker:
            return True
    if re.search(r'\b(?:light\s*novels?|web\s*novels?|audiobooks?)\b', t_without_parens):
        if not has_manga_marker:
            return True

    # 2. Dedicated Light Novel Imprints (Yen On is exclusively LN, Airship is Seven Seas LN line, etc.)
    #    Checked with parenthesised metadata stripped: only bracket/bare occurrences count.
    if re.search(r'\b(?:yen\s*on|airship|cross\s*infinite\s*world|tentai\s*books|hanashi\s*media|seven\s*seas\s*siren)\b', t_without_parens):
        if not has_manga_marker:
            return True

    # 3. Dedicated ebook extensions without manga marker
    if re.search(r'\b(?:epub|mobi|azw3?)\b|\.(?:epub|mobi|azw3?)$', t_lower):
        if not has_manga_marker:
            return True

    # 4. Pattern: [Publisher] [ReleaseGroup] without manga marker.  Square brackets
    #    only -- the LN imprint convention -- e.g. '[Seven Seas] [Stick]'.
    square_brackets = [b.strip() for b in re.findall(r'\[([^\]]+)\]', raw_title) if b.strip()]
    if len(square_brackets) >= 2 and not has_manga_marker:
        for i in range(len(square_brackets) - 1):
            cand_pub = square_brackets[i].lower()
            cand_grp = square_brackets[i+1].lower()
            if cand_pub in LN_IMPRINT_PUBLISHERS:
                # Ensure the second bracket is not just a format buzzword or volume tag
                if cand_grp not in {'cbz', 'cbr', 'manga'} and not re.match(r'^(?:v|vol|vols?)\.?\s*\d+', cand_grp):
                    return True

    # 5. Standalone [Stick] release group without manga/cbz marker.
    #    'Stick' rips light novels / audiobooks in EPUB format, but only the
    #    square-bracket form is decisive; "(Stick)" is manga-style group metadata.
    if re.search(r'\[(?:stick)\]', t_lower) and not has_manga_marker:
        return True

    return False

def is_complete_series_release(raw_title: str) -> bool:
    """
    Returns True if the title indicates a complete manga series compilation/batch.
    e.g., '(complete)', '[complete]', '(completed)', '[completed]', '(complete series)', '[complete series]',
    '(all volumes)', '[all volumes]', '(full series)', '[full series]'.
    """
    if not raw_title:
        return False
    t_lower = raw_title.lower()
    # A bracket that combines a complete-word with a volume reference, e.g.
    # "[vol.1-11 Complete]" / "(Vol 1-3 complete)" / "[Vol 1-11 full]".  The volume
    # range parser already turns the volume reference into the volume set; this only
    # adds the "complete" flag (a bare "[Vol 1-3]" is NOT complete).
    for block in re.findall(r"\[([^\]]+)\]|\(([^)]+)\)", raw_title):
        text = (block[0] or block[1] or "").strip()
        if _COMPLETE_WORD_RE.search(text) and _VOLUME_REFERENCE_RE.search(text):
            return True
    # Explicit bracketed tags
    if re.search(r'[\(\[]\s*(?:complete|completed|complete\s*series|completed\s*series|all\s*volumes?|full\s*series)\s*[\)\]]', t_lower):
        return True
    # Word boundary complete series or complete collection at end or after separator
    if re.search(r'(?:[-–—|/]\s*|\b)(?:complete\s*series|completed\s*series|full\s*series)\b', t_lower):
        return True
    return False

def is_chapter_only(raw_title: str) -> bool:
    """
    Returns True if the release is purely a chapter update with no volume information.
    MangaTrax only tracks volumes and bundles; pure chapter releases are discarded.
    Releases without a 'v' or 'vol' in front of numbers indicate chapters, not volumes.
    """
    if not raw_title:
        return False

    # Complete series compilations are never chapters
    if is_complete_series_release(raw_title):
        return False

    title = raw_title.lower()

    # Check for volume indicators
    # e.g., vol 1, volume 1, vol. 1, v01, v1-v7, volumes 1-5, (v01-v07), [v01]
    has_volume = bool(
        re.search(r"\b(?:volumes?|volume|vols?|vol|books?|book)\b\.?\s*\d+", title) or
        re.search(r"(?:^|[\s_.\-\(\[])v\d+", title)
    )

    if has_volume:
        return False

    # 1. Explicit chapter keywords: chapter/chap/ch/c/#.  The marker may be followed
    #    immediately by the number ("Ch1-50", "Chapter1-50") -- requiring a separator
    #    sent those titles into the bare volume-range bundle path (50 phantom volumes).
    if (
        re.search(r"(?i)\b(?:chapters?|chap|ch)\.?\s*[-–—:]?\s*\d{1,4}", title)
        or re.search(r"(?i)\bc\.?\s*[-–—:]?\s*\d{1,4}", title)
        or re.search(r"#\s*\d{1,4}", title)
    ):
        return True

    # 2. Explicit chapter symbols: #01, #100
    if re.search(r"#\s*\d+", title):
        return True

    # 2a. A CHAIN of "+"-joined bare chapter ranges is a chapter batch, never a
    # volume bundle ("Series 001-005 + 006-009", also unpadded "Series 1-5 + 6-9").
    if _TRAILING_CHAPTER_RANGE_CHAIN_RE.match(clean_manga_title(raw_title)):
        return True

    # 2b. D3 / spec §5 Pattern 1b: a bare numeric RANGE that passes the bundle
    # sanity guards is a multi-volume release, not a chapter.  Zero-padded ranges
    # ("01-02", "001-009") are chapter notation in manga feeds and do not match
    # here, so the existing chapter policy for them is unchanged.
    if _parse_bare_volume_range(clean_manga_title(raw_title)):
        return False

    # Clean brackets of non-chapter metadata like [Digital], [CBZ], [English], years (2020)
    cleaned = title
    cleaned = re.sub(r"\[(?:digital|cbz|cbr|pdf|epub|scanlation|scans?|raw|retail|multi|english|eng|ita|fra|esp|ger)\]", " ", cleaned)
    cleaned = re.sub(r"\((?:19|20)\d{2}(?:\s*[-–—~to/]+\s*(?:19|20)\d{2})?\)", " ", cleaned)
    cleaned = re.sub(r"\[(?:19|20)\d{2}(?:\s*[-–—~to/]+\s*(?:19|20)\d{2})?\]", " ", cleaned)

    # 3. Bracketed bare numbers without 'v' e.g. [01], [01-02], (01), (01-02)
    m_bracket = re.search(r"[\(\[]\s*(\d+(?:\.\d+)?(?:\s*[-–—~to/]+\s*\d+(?:\.\d+)?)?)\s*[\)\]]", cleaned)
    if m_bracket:
        inner = m_bracket.group(1).strip()
        if not re.match(r"^(?:19|20)\d{2}$", inner):
            return True

    # 4. Trailing separator + number/range: e.g. ' - 01', ' - 01-02', ' - 15.5', ' ~ 01'
    if re.search(r"(?:[-–—~:]|\s+-\s+)\s*\d+(?:\.\d+)?(?:\s*[-–—~to/]+\s*\d+(?:\.\d+)?)?\s*(?:\[|\(|$|\s)", cleaned):
        return True

    # 5. Trailing bare range: e.g. ' 01-02', ' 1-2'
    if re.search(r"\b\d{1,4}(?:\.\d+)?\s*[-–—~to/]+\s*\d{1,4}(?:\.\d+)?\b\s*(?:\[|\(|$|\s)", cleaned):
        return True

    # 6. Zero-padded trailing number: e.g. ' 01', ' 005'
    if re.search(r"\b0\d{1,3}(?:\.\d+)?\b\s*(?:\[|\(|$|\s)", cleaned):
        return True

    return False

def clean_manga_title(raw_title: str) -> str:
    """Strips release group tags like [Group], formats [CBZ], [Digital], resolutions, etc."""
    if not raw_title:
        return ""

    cleaned = raw_title.strip()

    # Strip leading site prefixes ("Download:", "Free Download -", "Read:", "Get -").
    # "read"/"get" require a separator, so "Reading Our Way..." keeps its first word.
    cleaned = strip_site_prefix(cleaned)

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
        # D3: plural keywords/parts count as volume markers too, so "[Vols 1-3]"
        # survives bracket triage and reaches the (plural-aware) bundle parser.
        has_vol = re.search(
            r"(?:volumes?|vols?|books?|parts?|pts?)\.?\s*\d+|\bv\.?\s*\d+",
            unbracketed,
            re.IGNORECASE,
        )
        has_range = re.search(r"\b\d+\s*[-–—~to]+\s*\d+\b", unbracketed, re.IGNORECASE)

        # Pure chapter bracket -> discard
        if has_chap and not has_vol:
            return " "

        # Volume or volume range bracket -> preserve only if it has an explicit volume indicator (v, vol, volume)
        if has_vol:
            return f" {unbracketed} "
        return " "

    cleaned = re.sub(r"\[.*?\]", bracket_replacer, cleaned)
    cleaned = re.sub(r"\(.*?\)", bracket_replacer, cleaned)

    # For mixed updates: strip chapter notations outside brackets so only volumes remain.
    # The marker may be followed immediately by the number ("Ch1-50", "Chapter1-50")
    # and may carry a range; those must not survive into the series key.
    cleaned = re.sub(
        r"(?i)\b(?:chapters?|chap|ch)\.?\s*[-–—:]?\s*\d{1,4}(?:\.\d+)?"
        r"(?:\s*[-–—~]\s*\d{1,4}(?:\.\d+)?)?(?:\s*\+\s*extra)?",
        " ", cleaned,
    )
    cleaned = re.sub(
        r"(?i)\bc\.?\s*\d{1,4}(?:\.\d+)?(?:\s*[-–—~]\s*c?\d{1,4}(?:\.\d+)?)?\b",
        " ", cleaned,
    )
    cleaned = re.sub(
        r"#\s*\d{1,4}(?:\.\d+)?(?:\s*[-–—~]\s*\d{1,4}(?:\.\d+)?)?",
        " ", cleaned,
    )

    # Strip format buzzwords, scene tokens, and status tags
    buzzwords = [
        r"\bcbz\b", r"\bcbr\b", r"\bpdf\b", r"\bepub\b", r"\bzip\b", r"\brar\b", r"\b7z\b",
        r"\bcbz\s*ver(?:sion)?\b", r"\bcbr\s*ver(?:sion)?\b", r"\bver(?:sion)?\b",
        r"\bdigital\b", r"\bretail\b", r"\bscan\b", r"\bscanlation\b", r"\bscans\b",
        r"\bsiterip\b", r"\bsite-rip\b", r"\bwebrip\b", r"\bweb-rip\b", r"\brip\b", r"\braws?\b",
        r"\bdownload\b", r"\bfree\s+download\b",
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
    cleaned = " ".join(cleaned.split())
    # A trailing standalone format descriptor ("Solo Leveling Manga") is not part of
    # the series key -- Calibre stores "Solo Leveling".  "Novel" is preserved.
    return strip_trailing_format_word(cleaned)

def _extract_manga_bundle_and_volumes_impl(raw_title: str, known_groups: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """
    Parses manga titles for series name, bundle ranges (e.g. Vol 01-05), or single volumes.
    Always generates ALL individual volumes in bundle ranges (e.g., 1 to 7 for v1-v7).
    Extracts release group and handles mixed volume/chapter updates by focusing on volumes.

    D3 (RELEASE_DETECTION_SPEC.md §5): also understands the NovelTrax idioms --
    parenthesised omnibus ranges, plural keywords (Vols/Volumes/Books), Oxford-comma
    lists, bare numeric ranges as bundles, and the "<chapters A-B> as v<X>-<Y>"
    chapter-to-volume mapping.  Chapter information is exposed as diagnostics
    (``is_chapter``, ``chapter_number``, ``chapter_ranges``) and must never be used
    to change the scheduler's chapter-discard policy.
    """
    release_groups = extract_release_groups(raw_title, known_groups=known_groups)
    release_group = release_groups[0] if release_groups else None
    is_comp = is_complete_series_release(raw_title)

    # Pattern 0 (spec §5): normalise "DanMachi Omnibus 1 (Vols 1-3)" to
    # "DanMachi Omnibus Vols 1-3" BEFORE bracket triage, so the range survives and
    # the edition number stays out of the series name.
    parse_title = raw_title or ""
    m_paren = _PAREN_OMNIBUS_RE.match(parse_title.strip())
    if m_paren:
        parse_title = f"{m_paren.group('series')} {m_paren.group('range')} {m_paren.group('rest')}"

    # Chapter-range -> volume mapping: "<chapters A-B> as v<X>-<Y> [+ chapters C-D]".
    # The whole clause (chapter range + literal "as" + volume token + trailing
    # chapter clause) is removed from the title used for the series name.
    chapter_meta = _extract_chapter_as_volume(parse_title)
    if chapter_meta:
        parse_title = chapter_meta["clean_title"]

    cleaned = clean_manga_title(parse_title)

    if release_group:
        # Strip extracted release group from cleaned title if present (e.g. from scene format -Group)
        cleaned = re.sub(r'(?i)(?:[-–—]\s*)?' + re.escape(release_group) + r'\b', ' ', cleaned)
        cleaned = " ".join(cleaned.split()).strip(" -–—:,|.")

    revision = extract_revision(raw_title)
    result = {
        "raw_title": raw_title,
        "clean_title": cleaned,
        "series_name": cleaned,
        "release_group": release_group,
        # Ordered multi-group data: primary first (task-11 will surface the rest).
        "release_groups": release_groups,
        # Publisher is a quality signal (official release), surfaced not filtered.
        "publisher": extract_publisher(raw_title),
        # Author credits (positional, pre-cluster blocks): surfaced, never groups.
        "authors": extract_authors(raw_title, known_groups=known_groups),
        # "(f)" / "(f2)" re-release/fix markers: metadata only, never pollution.
        "revision": revision,
        "is_rerelease": revision is not None,
        # Collected batch candidate: parenthesised year range + no volume/chapter
        # markers.  The scheduler infers the count from authoritative metadata only.
        "is_collected_release": is_collected_release_candidate(raw_title),
        "is_light_novel": is_light_novel(raw_title),
        "is_bundle": is_comp,
        "is_complete": is_comp,
        "volume_start": 1.0 if is_comp else None,
        "volume_end": None,
        "volumes": [],
        "confidence": 0.9 if is_comp else 0.0,
        # Diagnostics only.  MangaTrax's discard policy is unchanged: the
        # scheduler's context-guarded chapter discard still owns that decision.
        "is_chapter": _looks_like_chapter_title(raw_title),
        "chapter_number": None,
        "chapter_ranges": [],
        "chapter_start": None,
        "chapter_end": None,
        # Pre-strip title for bare-number chapters (e.g. "Mob Psycho 100"); the
        # scheduler uses it to keep a real numbered series name from being split.
        "numbered_series_name": "",
    }
    if chapter_meta:
        result["chapter_ranges"] = [list(r) for r in chapter_meta["chapter_ranges"]]
        result["chapter_start"] = min(r[0] for r in chapter_meta["chapter_ranges"])
        result["chapter_end"] = max(r[1] for r in chapter_meta["chapter_ranges"])
    else:
        result["chapter_number"] = _chapter_number_from_title(raw_title)
        if result["chapter_number"] is None and result["is_chapter"]:
            trailing = _TRAILING_BARE_NUMBER_RE.search(cleaned.strip())
            if trailing:
                try:
                    result["chapter_number"] = float(trailing.group(1))
                except (TypeError, ValueError):
                    pass

    # Explicit chapter RANGES are chapter data, not volumes: record them for both
    # the no-separator ("Ch1-50") and spaced ("Ch 1-50") forms so chapter_ranges /
    # chapter_start / chapter_end carry the real range.
    chapter_range_match = _EXPLICIT_CHAPTER_RANGE_RE.search(raw_title)
    if chapter_range_match:
        try:
            range_start = float(chapter_range_match.group("start"))
            range_end = float(chapter_range_match.group("end"))
        except (TypeError, ValueError):
            range_start = range_end = None
        if range_start is not None and range_end is not None:
            span = [min(range_start, range_end), max(range_start, range_end)]
            result["chapter_ranges"] = [span]
            result["chapter_start"] = span[0]
            result["chapter_end"] = span[1]
            if result["chapter_number"] is None:
                result["chapter_number"] = span[0]

    # Chapter-as-volume idiom: volumes come from the "v<X>-<Y>" half, NOT from the
    # chapter numbers (chapters 1-9 map to volumes 1-2, never volumes 1-9).
    if chapter_meta:
        start_vol = chapter_meta["volume_start"]
        end_vol = chapter_meta["volume_end"]
        return _volume_result(
            result,
            cleaned,
            is_bundle=(end_vol > start_vol),
            volume_start=start_vol,
            volume_end=end_vol,
            volumes=chapter_meta["volumes"],
            confidence=0.95,
        )

    if not cleaned or is_chapter_only(raw_title):
        return _apply_chapter_series_strip(result)

    # Pattern 1: Multi-volume bundle range (plural-aware: Vol/Vols/Volume/Volumes/Book):
    bundle_pattern = re.compile(
        r"^(?P<series>.+?)\s*(?:[-–—:]\s*)?"
        r"(?:\b(?:volumes?|vols?|books?|book)\b\.?|\bv\.?)\s*(?P<start>\d+(?:\.\d+)?)"
        r"\s*[-–—~to]+\s*"
        r"(?:(?:volumes?|vols?|books?|book)\b\.?|\bv\.?)?\s*(?P<end>\d+(?:\.\d+)?)"
        r"(?:\s*(?:[:\-–—]|\b)(?P<tail>.*))?$",
        re.IGNORECASE
    )
    m_bundle = bundle_pattern.match(cleaned)
    if m_bundle:
        series = m_bundle.group("series").strip(" -–—:,|.")
        series = re.sub(r"(?i)\b(?:volumes?|vols?\.?|books?)\b$", "", series).strip(" -–—:,|.")
        try:
            start_vol = float(m_bundle.group("start"))
            end_vol = float(m_bundle.group("end"))
            if start_vol > end_vol:
                start_vol, end_vol = end_vol, start_vol

            # Clamp span to safe upper limit to prevent algorithmic complexity DoS
            if (end_vol - start_vol) > MAX_BUNDLE_SPAN:
                end_vol = start_vol + MAX_BUNDLE_SPAN

            vols = []
            curr = int(start_vol)
            max_vol = int(end_vol)
            while curr <= max_vol and len(vols) <= MAX_BUNDLE_SPAN:
                vols.append(float(curr))
                curr += 1

            # Composite tail: an explicit volume continuation extends the volumes;
            # a bare numeric range is chapter data only.
            extra_volumes, tail_chapters = _parse_composite_tail(m_bundle.group("tail") or "")
            if extra_volumes:
                vols = sorted(set(vols) | set(extra_volumes))
                end_vol = max(end_vol, max(extra_volumes))

            out = _volume_result(
                result, series, is_bundle=True, volume_start=start_vol,
                volume_end=end_vol, volumes=vols, confidence=0.95,
            )
            if tail_chapters:
                ranges = list(out.get("chapter_ranges") or [])
                for chapter_range in tail_chapters:
                    if chapter_range not in ranges:
                        ranges.append(chapter_range)
                out["chapter_ranges"] = ranges
                out["chapter_start"] = min(r[0] for r in ranges)
                out["chapter_end"] = max(r[1] for r in ranges)
            return out
        except ValueError:
            pass

    # Pattern 1b: Comma/plus/and separated bundle, incl. the Oxford comma
    # ("Vol 1, 2, and 3") from spec §5 Pattern 0b.
    list_bundle_pattern = re.compile(
        r"^(?P<series>.+?)\s*(?:[-–—:,]\s*)?"
        r"(?:\b(?:volumes?|vols?|books?)\b\.?|\bv\.?)\s*"
        r"(?P<vols_str>\d+(?:\s*(?:,\s*(?:and\b|[+&])|[,+&]|\band\b)\s*(?:(?:volumes?|vols?|books?)\b\.?|\bv\.?)?\s*\d+)+)"
        r"(?:\s*(?:[:\-–—]|\b).*)?$",
        re.IGNORECASE
    )
    m_list = list_bundle_pattern.match(cleaned)
    if m_list:
        series = m_list.group("series").strip(" -–—:,|.")
        series = re.sub(r"(?i)\b(?:volumes?|vols?\.?|books?)\b$", "", series).strip(" -–—:,|.")
        raw_nums = re.findall(r"\d+", m_list.group("vols_str"))
        if len(raw_nums) >= 2:
            parsed_vols = sorted(list(set(float(n) for n in raw_nums)))
            return _volume_result(
                result, series, is_bundle=True, volume_start=parsed_vols[0],
                volume_end=parsed_vols[-1], volumes=parsed_vols, confidence=0.95,
            )

    # Pattern 2: Single Volume (explicit keyword, incl. plural keywords/parts):
    single_pattern = re.compile(
        r"^(?P<series>.+?)\s*(?:[-–—:,]\s*)?"
        r"(?:\b(?:volumes?|vols?|books?|parts?|pts?)\b\.?|\bv\.?)\s*(?P<vol>\d+(?:\.\d+)?)"
        r"(?:\s*(?:[:\-–—]|\b).*)?$",
        re.IGNORECASE
    )
    m_single = single_pattern.match(cleaned)
    if m_single:
        series = m_single.group("series").strip(" -–—:,|.")
        series = re.sub(r"(?i)\b(?:volumes?|vols?\.?|books?|parts?|pts?\.?)\b$", "", series).strip(" -–—:,|.")
        try:
            vol = float(m_single.group("vol"))
            return _volume_result(
                result, series, is_bundle=False, volume_start=vol,
                volume_end=vol, volumes=[vol], confidence=0.95,
            )
        except ValueError:
            pass

    # Pattern 3 (spec §5 Pattern 1b): a BARE numeric range is a bundle, not a
    # chapter/volume-1 ("DanMachi 1-4", "86 - 1-4", "Mob Psycho 100 - 1-3").
    bare_range = _parse_bare_volume_range(cleaned)
    if bare_range:
        return _volume_result(
            result, bare_range["series"],
            is_bundle=len(bare_range["volumes"]) > 1,
            volume_start=bare_range["start"],
            volume_end=bare_range["end"],
            volumes=bare_range["volumes"],
            confidence=0.85,
        )

    # Bare-number chapter ("Dandadan 250", "Dandadan 250 (Digital) (1r0n)"): align
    # with the explicit-marker path by moving the chapter number out of the series
    # key and into the chapter data, so chapters group under the series.
    return _apply_chapter_series_strip(result)

def _series_trigrams(norm: str) -> Set[str]:
    """Character 3-grams of a normalised series name (fuzzy-candidate index key).

    Two strings with SequenceMatcher ratio > 0.80 always share a matching block of
    at least 3 characters, so a shared-trigram index cannot miss a real fuzzy match
    while avoiding the O(library) scan for every release.
    """
    if not norm:
        return set()
    if len(norm) <= 3:
        return {norm}
    return {norm[index:index + 3] for index in range(len(norm) - 2)}


class CalibreSeriesIndex:
    """Precomputed lookup index over a Calibre series list (built ONCE per sync).

    The historical matcher re-normalised every Calibre series name for every release
    (and `string_similarity()` re-normalised both sides inside the fuzzy loop), which
    benchmarks at ~40-120 ms per release against a 3000-series library.  The index
    normalises the library once and then answers each lookup with:

    * an exact-normalised dict fast path (O(1));
    * a bounded substring pass over length buckets (same ``len_ratio >= 0.70`` and
      ``len >= 6`` rules, first series in list order wins);
    * fuzzy scoring only for series sharing a 3-gram with the query (same
      ``score > threshold`` / first-in-list-wins semantics as before).

    ``match()`` is deliberately the single implementation of the matching rules;
    :func:`match_manga_series_in_calibre` delegates to it so there cannot be two
    subtly different matchers.
    """

    __slots__ = ("_entries", "_by_norm", "_by_length", "_by_gram", "_by_token")

    #: Tokens shared by more series than this are too generic to narrow a fuzzy match.
    _FUZZY_RARE_TOKEN_BUCKET = 200
    #: Only very small 3-gram buckets are OR-ed in on top of the token candidates.
    _FUZZY_TINY_GRAM_BUCKET = 20

    def __init__(self, calibre_series_list):
        entries: List[Tuple[str, dict]] = []
        by_norm: Dict[str, int] = {}
        by_length: Dict[int, List[int]] = {}
        by_gram: Dict[str, List[int]] = {}
        by_token: Dict[str, List[int]] = {}
        for c_ser in calibre_series_list or []:
            if not isinstance(c_ser, dict):
                continue
            name = c_ser.get("series_name") or c_ser.get("name", "")
            norm = normalize_text(name)
            if not norm:
                continue
            index = len(entries)
            entries.append((norm, c_ser))
            by_norm.setdefault(norm, index)
            by_length.setdefault(len(norm), []).append(index)
            for gram in _series_trigrams(norm):
                by_gram.setdefault(gram, []).append(index)
            for token in set(norm.split()):
                by_token.setdefault(token, []).append(index)
        self._entries = entries
        self._by_norm = by_norm
        self._by_length = by_length
        self._by_gram = by_gram
        self._by_token = by_token

    def __len__(self) -> int:
        return len(self._entries)

    def _fuzzy_candidates(self, norm_cand: str, threshold: float) -> List[int]:
        """Plausible fuzzy candidates (shared rare token/3-gram), in library order.

        A ratio > 0.80 requires a matching block of 3+ characters, so every real
        fuzzy match shares at least one 3-gram; scoring only the rare shared
        token/gram buckets keeps this proportional to the query, not the library.
        The length window is the theoretical bound: ratio <= 2*min/(min+max), so a
        match must have ``min/max > threshold/(2-threshold)``.
        """
        candidate_indices: Set[int] = set()
        tokens = [token for token in norm_cand.split() if token]
        for token in tokens:
            bucket = self._by_token.get(token)
            if bucket and len(bucket) <= self._FUZZY_RARE_TOKEN_BUCKET:
                candidate_indices.update(bucket)
        for gram in _series_trigrams(norm_cand):
            bucket = self._by_gram.get(gram)
            if bucket and len(bucket) <= self._FUZZY_TINY_GRAM_BUCKET:
                candidate_indices.update(bucket)
        if not candidate_indices:
            # Single-token / very short names: fall back to the rarest bucket only.
            fallback = [self._by_token.get(token) for token in tokens]
            fallback += [self._by_gram.get(gram) for gram in _series_trigrams(norm_cand)]
            fallback = [bucket for bucket in fallback if bucket]
            if fallback:
                candidate_indices.update(min(fallback, key=len))
        if not candidate_indices:
            return []
        ratio_floor = threshold / (2.0 - threshold) if threshold < 2.0 else 1.0
        low = int(math.ceil(len(norm_cand) * ratio_floor))
        high = int(len(norm_cand) / ratio_floor)
        return [
            index for index in sorted(candidate_indices)
            if low <= len(self._entries[index][0]) <= high
        ]

    def match(self, extracted_series: str, threshold: float = 0.80) -> Optional[dict]:
        """Match an extracted series title (see the class docstring for the rules)."""
        if not extracted_series or not self._entries:
            return None
        if not normalize_text(extracted_series):
            return None

        # Candidate parts: the whole title first, then any |/delimiter alternatives.
        candidate_parts = [extracted_series]
        parts = split_series_candidate_parts(extracted_series)
        if len(parts) > 1:
            for part in parts:
                if part not in candidate_parts:
                    candidate_parts.append(part)
        norm_parts: List[str] = []
        for cand in candidate_parts:
            norm_cand = normalize_text(cand)
            if norm_cand:
                norm_parts.append(norm_cand)

        # 1. Exact normalised match (dict fast path).
        for norm_cand in norm_parts:
            index = self._by_norm.get(norm_cand)
            if index is not None:
                return self._entries[index][1]

        # 2. Strict substring containment; only length buckets that can satisfy
        #    len_ratio >= 0.70 are visited, and the earliest series in list order wins.
        for norm_cand in norm_parts:
            if len(norm_cand) < 6:
                continue
            low = max(6, int(math.ceil(len(norm_cand) * 0.70)))
            high = int(len(norm_cand) / 0.70)
            matched_indices: List[int] = []
            for length in range(low, high + 1):
                for index in self._by_length.get(length, ()):
                    norm_c = self._entries[index][0]
                    len_ratio = min(len(norm_c), len(norm_cand)) / max(len(norm_c), len(norm_cand))
                    if len_ratio < 0.70:
                        continue
                    if norm_c in norm_cand or norm_cand in norm_c:
                        matched_indices.append(index)
            if matched_indices:
                return self._entries[min(matched_indices)][1]

        # 3. Fuzzy similarity on plausible candidates only (see _fuzzy_candidates).
        best_match = None
        best_score = threshold
        for norm_cand in norm_parts:
            for index in self._fuzzy_candidates(norm_cand, threshold):
                score = SequenceMatcher(None, norm_cand, self._entries[index][0]).ratio()
                if score > best_score:
                    best_score = score
                    best_match = self._entries[index][1]

        return best_match


def match_manga_series_in_calibre(extracted_series: str, calibre_series_list: list, threshold: float = 0.80) -> Optional[dict]:
    """
    Matches an extracted series title against known Calibre series tracking.

    Correctly prioritizes exact matches across candidate parts (handling dual titles
    like 'Hataraku Maou-sama! | The Devil is a Part-Timer!') before substring or fuzzy
    matching.  Delegates to :class:`CalibreSeriesIndex` (the single implementation);
    hot callers such as :class:`MangaCalibreMatcher` build the index once per sync and
    reuse it instead of paying for a library normalisation on every release.
    """
    if not extracted_series or not calibre_series_list:
        return None
    return CalibreSeriesIndex(calibre_series_list).match(extracted_series, threshold)

def parse_calibre_volumes_and_series(
    title: str,
    series_name: Optional[str] = None,
    series_index: Optional[float] = None
) -> Tuple[Optional[str], Set[float]]:
    """
    Extracts all owned volume indices and canonical series name from a Calibre book record.
    Handles volume bundle edge cases (e.g. 'Aharen-san is Indecipherable v01-02' where
    Calibre's series_index only stores 1.0, ensuring both volumes 1.0 and 2.0 are recognized).
    Also supports bundle notations like 'Vol. 1-2', 'v01-v04', 'v01+v02', 'v01, 02',
    as well as standalone books without series tags.

    D3 (RELEASE_DETECTION_SPEC.md §6): the volume parser is NovelTrax's newer copy,
    including both guards that MangaTrax was missing:

    * **reversed-range guard** -- a reversed range (``Vol 3-1``) is only swapped
      when the title carries an explicit volume prefix; a bare ``3-1`` is not a
      range and falls through (it would otherwise truncate a title like
      ``Mob Psycho 100 - 1``);
    * **bracket-delimiter guard** -- ``Title [100 - Vol 1]`` is not a range: a
      bare number before the delimiter inside brackets never pairs with the
      explicit volume tag after it.
    """
    vols: Set[float] = set()
    detected_series: Optional[str] = series_name.strip() if series_name else None

    # 1. Start with Calibre's series_index if present (MangaTrax tracks from vol 1)
    if series_index is not None and series_index > 0:
        vols.add(round(float(series_index), 2))

    if not title:
        return detected_series, vols

    # 2. Series-name detection from the title, with the trailing bare-range guard:
    #    a truncated title like "Mob Psycho 100 - 1" must keep its series number.
    if not detected_series:
        clean_name = re.sub(
            r"(?i)\s*(?:[-–—:]\s*)?(?:(?:\b(?:volumes?|volume|vols?|vol|books?|book|bk)\b\.?|\bv\.?)\s*\d+.*|\[.*?\]|\(.*?\))\s*$",
            "",
            title
        ).strip(" -–—:,|.")
        m_bare_trail = re.search(
            r"(?i)(?:\s*[-–—:]\s*|\s+)(?P<s_bare>\d+(?:\.\d+)?)\s*[-–—~to/]+\s*(?P<e_bare>\d+(?:\.\d+)?)\s*$",
            clean_name
        )
        if m_bare_trail:
            s_b = float(m_bare_trail.group("s_bare"))
            e_b = float(m_bare_trail.group("e_bare"))
            if s_b <= e_b:
                clean_name = clean_name[:m_bare_trail.start()].strip(" -–—:,|.")
        if clean_name:
            detected_series = clean_name

    # Prioritise clean_t (title with the series prefix removed) before the raw
    # title, so numbers inside the series name cannot prematurely match a range.
    candidates: List[str] = []
    if detected_series:
        clean_t = re.sub(r"^" + re.escape(detected_series), "", title, flags=re.IGNORECASE).strip(" -–—:,|.")
        if clean_t:
            candidates.append(clean_t)
    candidates.append(title)
    if detected_series:
        # MangaTrax compatibility: titles that are just a volume notation.
        candidates.append(f"{detected_series} {title}")

    # 3. Extract volumes; ranges first (they win over the single/list patterns).
    extracted_vols: Set[float] = set()
    found_range = False

    for c in candidates:
        if not c:
            continue

        # Bracketed ranges like [1-4], [Vol 1-3], (v01-04), [0-2].
        # Guard: a delimiter before the volume tag inside brackets
        # ("Title [100 - Vol 1]") is not a range.
        m_bracket = re.search(
            r"[\[\(]\s*(?:(?P<p1>\b(?:volumes?|volume|vols?|vol|books?|book|bk)\b\.?|\bv\.?)\s*)?(?P<start>\d+(?:\.\d+)?)"
            r"\s*[-–—~to/]+\s*"
            r"(?:(?P<p2>\b(?:volumes?|volume|vols?|vol|books?|book|bk)\b\.?|\bv\.?)\s*)?(?P<end>\d+(?:\.\d+)?)\s*[\]\)]",
            c,
            re.IGNORECASE
        )
        if m_bracket:
            p1 = m_bracket.group("p1")
            p2 = m_bracket.group("p2")
            if not p1 and p2:
                # Delimiter before the volume tag inside brackets -> not a range.
                pass
            else:
                s_val = float(m_bracket.group("start"))
                e_val = float(m_bracket.group("end"))
                has_marker = bool(p1 or p2)
                if s_val > e_val:
                    if not p1:
                        # Reversed ranges require an explicit volume prefix.
                        pass
                    else:
                        s_val, e_val = e_val, s_val
                if s_val <= e_val and 0 <= s_val <= 2000 and 0 <= e_val <= 2000 and (e_val - s_val) <= 100:
                    if (s_val < 1000 and e_val < 1000) or has_marker:
                        curr = int(s_val)
                        while curr <= int(e_val):
                            if curr >= s_val:
                                extracted_vols.add(float(curr))
                            curr += 1
                        if e_val != int(e_val):
                            extracted_vols.add(round(float(e_val), 2))
                        if s_val != int(s_val):
                            extracted_vols.add(round(float(s_val), 2))
                        found_range = True
                        break

        # Standard range: Vol 1-3, v01-02, Volume 1 to 4, 01-03, Vol 1-2.5, bare 1-4.
        # A reversed range is only swapped when an explicit volume prefix matched.
        m_range = re.search(
            r"(?i)(?:"
            r"(?P<p1>\b(?:volumes?|volume|vols?|vol|books?|book|bk)\b\.?|\bv\.?)\s*(?P<start1>\d+(?:\.\d+)?)\s*[-–—~to/]+\s*(?:(?P<p2>\b(?:volumes?|volume|vols?|vol|books?|book|bk)\b\.?|\bv\.?)\s*)?(?P<end1>\d+(?:\.\d+)?)\b"
            r"|"
            r"(?<![-–—/])\b(?P<start2>\d+(?:\.\d+)?)\s*[-–—~to/]+\s*(?P<end2>\d+(?:\.\d+)?)\b(?!\s*[-–—~to/]+\s*\d)"
            r")",
            c
        )
        if m_range:
            p1 = m_range.group("p1")
            p2 = m_range.group("p2")
            s_val = float(m_range.group("start1") or m_range.group("start2"))
            e_val = float(m_range.group("end1") or m_range.group("end2"))
            has_marker = bool(p1 or p2)
            if s_val > e_val:
                # Reversed ranges (e.g. Vol 3-1) are only valid with a volume prefix.
                if not p1:
                    pass
                else:
                    s_val, e_val = e_val, s_val
            if s_val <= e_val and 0 <= s_val <= 2000 and 0 <= e_val <= 2000 and (e_val - s_val) <= 100:
                if (s_val < 1000 and e_val < 1000) or has_marker:
                    curr = int(s_val)
                    while curr <= int(e_val):
                        if curr >= s_val:
                            extracted_vols.add(float(curr))
                        curr += 1
                    if e_val != int(e_val):
                        extracted_vols.add(round(float(e_val), 2))
                    if s_val != int(s_val):
                        extracted_vols.add(round(float(s_val), 2))
                    found_range = True
                    break

    if not found_range:
        for c in candidates:
            if not c:
                continue
            # Pattern list: v1, 2, 3 or Vol 1 + 2 or v1 & v2, incl. the Oxford comma
            # ("Vol 1, 2, and 3").
            m_list = re.search(
                r"(?i)(?:(?:\b(?:volumes?|volume|vols?|vol|books?|book|bk)\b\.?|\bv\.?)\s*)"
                r"(?P<list>\d+(?:\.\d+)?(?:\s*(?:,\s*(?:and\b|[+&])|[,+&]|\band\b)\s*(?:(?:\b(?:volumes?|volume|vols?|vol|books?|book|bk)\b\.?|\bv\.?)\s*)?\d+(?:\.\d+)?)+)",
                c
            )
            if m_list:
                raw_nums = re.findall(r"\d+(?:\.\d+)?", m_list.group("list"))
                for n in raw_nums:
                    val = round(float(n), 2)
                    if 0 <= val <= 2000:
                        extracted_vols.add(val)
                if extracted_vols:
                    break

            # Single volume pattern: Vol. 23, v05, Volume 12, Book 3, v15, Vol 1.5
            m_single = re.search(
                r"(?i)(?:\b(?:volumes?|volume|vols?|vol|books?|book|bk)\b\.?|\bv\.?)\s*(\d+(?:\.\d+)?)\b",
                c
            )
            if m_single:
                try:
                    s_num = float(m_single.group(1))
                    if 0 <= s_num <= 2000:
                        extracted_vols.add(round(s_num, 2))
                        break
                except ValueError:
                    pass

    # 4. Discard the Calibre default series_index = 1.0 when the title explicitly
    #    names volumes that do not contain 1.0 (omnibus/volume-N book).
    if extracted_vols:
        if series_index == 1.0 and 1.0 not in extracted_vols:
            vols.discard(1.0)
        vols.update(extracted_vols)
    elif detected_series is None:
        # Fallback: derive the series name from a title that only carries volumes.
        clean_name = re.sub(
            r"(?i)\s*(?:[-–—:]\s*)?(?:(?:\b(?:volumes?|volume|vols?|vol|books?|book)\b\.?|\bv\.?)\s*\d+.*|\d+\s*[-–—~to/].*)$",
            "",
            title
        ).strip(" -–—:,|.")
        if clean_name:
            detected_series = clean_name

    return detected_series, vols


_RECOVERY_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_RECOVERY_BRACE_RE = re.compile(r"\{([^}]*)\}")
_TRAILING_VOLUME_TOKEN_RE = re.compile(
    r"\s+v(?:ol(?:ume)?s?)?\.?\s*\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*$",
    re.IGNORECASE,
)
_VOLUME_ONLY_RE = re.compile(r"^v?(?:ol(?:ume)?s?)?\.?\s*\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?$", re.IGNORECASE)


def _clean_series_display(value: str) -> str:
    text = _RECOVERY_BRACE_RE.sub(" ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("_").strip()
    previous = None
    while previous != text:
        previous = text
        text = _TRAILING_VOLUME_TOKEN_RE.sub("", text).strip(" -–—:,|./")
    return text.strip("_").strip()


def _fallback_volume_from_raw(raw_title: str) -> List[float]:
    match = re.search(
        r"\bv(?:ol(?:ume)?s?)?\.?\s*(\d+(?:\.\d+)?)(?:\s*[-–]\s*(\d+(?:\.\d+)?))?",
        str(raw_title or ""), re.IGNORECASE,
    )
    if not match:
        return []
    try:
        start = float(match.group(1))
        end = float(match.group(2)) if match.group(2) else start
    except (TypeError, ValueError):
        return []
    if end < start or end - start > 200:
        end = start
    values = []
    cursor = start
    while cursor <= end + 1e-9:
        values.append(round(cursor, 4))
        cursor += 1.0
    return values


def _recover_series_from_result(raw_title: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Post-process imperfect parses (leading bracket names, brace tags, underscores).

    Fallback rule: when the remaining series text is empty or only a volume token
    (``v12``), use the first ``[...]`` block's content as the series (preserving the
    bracket form for display).  Standard ``[Group] Series v01`` is unchanged because
    a real series name remains after the leading bracket block.
    """
    recovered = dict(result or {})
    raw = str(raw_title or "")
    series = _clean_series_display(recovered.get("series_name") or "")
    brackets = [match.strip() for match in _RECOVERY_BRACKET_RE.findall(raw) if match.strip()]
    bracket_series = f"[{brackets[0]}]" if brackets else ""
    used_bracket_fallback = False
    if not series or _VOLUME_ONLY_RE.fullmatch(series) or not re.search(r"[A-Za-z]{2,}", series):
        if bracket_series:
            series = bracket_series
            used_bracket_fallback = True
    recovered["series_name"] = series
    # Only recover a volume when we also had to recover the series from a bracket
    # block (e.g. "[X] v12").  Otherwise leave empty volumes alone (complete-series
    # releases deliberately keep volumes=[]).
    if used_bracket_fallback and not recovered.get("volumes") and not recovered.get("is_bundle"):
        fallback_volumes = _fallback_volume_from_raw(raw)
        if fallback_volumes:
            recovered["volumes"] = fallback_volumes
            recovered["volume_start"] = fallback_volumes[0]
            recovered["volume_end"] = fallback_volumes[-1]
            if len(fallback_volumes) > 1:
                recovered["is_bundle"] = True
    return recovered


def extract_manga_bundle_and_volumes(
    raw_title: str, known_groups: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Parse a release title; recovers bracket-name/tag/underscore parse shapes."""
    result = _extract_manga_bundle_and_volumes_impl(raw_title, known_groups=known_groups)
    return _recover_series_from_result(raw_title, result)


def series_name_candidates(raw_title_or_filename: str) -> List[str]:
    """Plausible series spellings for matching (bracket/underscore/cleaned variants)."""
    raw = str(raw_title_or_filename or "").strip()
    if not raw:
        return []
    try:
        parsed = extract_manga_bundle_and_volumes(raw)
        primary = str(parsed.get("series_name") or "").strip()
    except Exception:
        primary = ""
    brackets = [match.strip() for match in _RECOVERY_BRACKET_RE.findall(raw) if match.strip()]
    values: List[str] = []
    if primary:
        values.append(primary)
    if brackets:
        values.append(f"[{brackets[0]}]")
        values.append(brackets[0])
    if primary:
        values.append(_clean_series_display(primary))
        values.append(primary.strip("_").strip())
    try:
        values.append(clean_series_for_matching(primary or raw))
    except Exception:
        pass
    seen = set()
    candidates: List[str] = []
    for value in values:
        text = str(value or "").strip()
        norm = normalize_text(text)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        candidates.append(text)
    return candidates
