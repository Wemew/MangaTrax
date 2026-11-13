import time
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import re
import json
import shutil
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Set

from app.database import (
    MangaDatabase,
    resolve_status,
    display_title_from_meta,
    is_authoritative_metadata,
)
from app.covers import CoverCache
from app.nyaa_source import DEFAULT_NYAA_BASE_URL, NyaaBaseURLError, normalize_nyaa_base_url
from app.calibre.scanner import CalibreMangaScanner
from app.calibre.matcher import MangaCalibreMatcher
from app.downloader.qbit import QBitClient
from app import downloads_inventory
from app.downloader import as_bool, build_downloader, resolve_downloader_engine
from app.downloader.torbox import TORBOX_ZIP_MAX_FILES, TORBOX_ZIP_SAFETY_RATIO
from app.downloader.base import (
    DownloaderAuthError,
    DownloaderError,
    DownloaderFileError,
    DownloaderNotFoundError,
    DownloaderRateLimitError,
    DownloaderSlotError,
    encode_queued_handle_id,
    parse_queued_handle_id,
)
from app.tagger.cbz_tagger import auto_tag_staging_cbz_files
from app.tagger import pipeline as downloads_pipeline
from app.providers.registry import create_provider
from app.providers.nyaa import NyaaMangaProvider
from app.providers.base import ProviderFetchError
from app.metadata.anilist import AniListMetadataService
from app.metadata.mangaupdates import MangaUpdatesService
from app.parser import (
    is_chapter_only,
    is_jpegxl_release,
    is_light_novel,
    is_complete_series_release,
    is_collected_release_candidate,
    normalize_text,
    string_similarity,
    extract_manga_bundle_and_volumes,
    match_manga_series_in_calibre,
    split_series_candidate_parts,
)
from app.notifier.webhook import NotificationService

logger = logging.getLogger("mangatrax.scheduler")

#: Hard wall-clock budget for one attribution audit request. The audit is a read-only
#: diagnostic: it must answer the UI (and any reverse proxy) instead of scanning an
#: unbounded release table until the proxy turns the response into an HTML error page.
ATTRIBUTION_TIME_BUDGET_SECONDS = 8.0

#: A complete-series volume lookup that found no count is retried after this delay
#: (or immediately from an explicit series refresh).
COMPLETE_EXPAND_RETRY_SECONDS = 24 * 60 * 60

#: Tokens that, when they are the ONLY thing following the stored series name in a
#: release title, cannot change the attribution (volume/format noise). Anything else
#: (e.g. "short story collection", a spin-off suffix) forces the parser path.
_ATTRIBUTION_SUFFIX_NOISE_TOKENS = frozenset({
    "vol", "volume", "vols", "v", "book", "books", "part", "parts",
    "epub", "pdf", "mobi", "azw3", "azw", "fb2", "txt", "html",
    "complete", "completed", "series", "edition", "digital", "official",
    "retail", "rip", "batch", "pack", "box", "set", "omnibus", "all", "the", "a", "of",
})


def _attribution_title_noise_only(title_norm: str, stored_norm: str) -> bool:
    """True when the stored spelling is present and only volume/format noise remains.

    Cheap fast path: a title that is just ``<stored series> Vol N`` / format tags
    cannot parse to a different series, so the parser + identity resolver are skipped.
    """
    if not title_norm or not stored_norm or stored_norm not in title_norm:
        return False
    remainder = title_norm.replace(stored_norm, " ", 1)
    for token in remainder.split():
        if token.isdigit() or token in _ATTRIBUTION_SUFFIX_NOISE_TOKENS:
            continue
        alpha = token.rstrip("0123456789")
        if alpha and alpha != token and alpha in _ATTRIBUTION_SUFFIX_NOISE_TOKENS:
            continue  # v03 / vol1 / book2
        return False
    return True


# --- Marker-less chapter guard (context-aware, NOT a blind regex) -------------
#
# A release titled "Chainsaw Man 180" carries no "v"/"vol" marker, so the pure
# parser historically could not classify it and it was ingested as a volume-less
# junk release.  Treating *any* trailing bare number as a chapter would destroy
# legitimate titles whose number belongs to the series name ("Mob Psycho 100",
# "20th Century Boys", "7th Time Loop", "Isekai Ojisan 2"), and the parser is a
# pure function with no library context.  The decision therefore happens at
# ingestion time in the scheduler, where the Calibre matcher and the tracked
# series rows are available.  The parser reports the chapter signal as data
# (``is_chapter`` / ``chapter_number`` / ``numbered_series_name``); this guard
# consumes it and applies the library context:
#
#   * parsed as a chapter (no volume/bundle), AND
#   * the clean series name exactly matches a series we already know
#     (Calibre library or series_tracking row)
#
# Only then is the release a chapter of a known series.  For "Mob Psycho 100" the
# parser yields the clean series "Mob Psycho" + the numbered full title
# "Mob Psycho 100"; the numbered title is itself a known series, so the release is
# never discarded.  With no context (base series unknown) the release is retained
# and grouped under the clean series name -- inconclusive means kept.

class MangaScheduler:
    def __init__(self, db: MangaDatabase, cover_cache: CoverCache):
        self.db = db
        self.cover_cache = cover_cache
        self.anilist = AniListMetadataService(db=self.db)
        self.mangaupdates = MangaUpdatesService(db=self.db)
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._calibre_sync_lock = threading.Lock()
        self.last_calibre_sync: float = 0.0
        # Torbox non-fatal issue de-duplication (release id -> last surfaced message).
        self._torbox_notified: Dict[int, str] = {}
        # JPEG XL throw-away filter: audit once per release guid (feeds re-poll often).
        self._jpegxl_discard_notified: set = set()
        # task-29: separate Torbox poller + bounded fetch executor (off the main loop).
        self._torbox_poll_thread: Optional[threading.Thread] = None
        self._torbox_poll_stop = threading.Event()
        self._torbox_poll_wakeup = threading.Event()
        self._torbox_poll_lock = threading.Lock()
        self._torbox_fetch_lock = threading.Lock()
        self._torbox_fetch_locks: Dict[int, threading.Lock] = {}
        self._torbox_fetching: set = set()
        self._torbox_fetch_futures: Dict[int, Any] = {}
        self._torbox_fetch_executor: Optional[ThreadPoolExecutor] = None
        self._torbox_last_poll_monotonic: float = 0.0
        # Startup work (legacy key repair + initial Calibre sync) runs as the FIRST
        # action of the worker thread so the web UI can serve immediately; see
        # perform_initial_sync().
        self._initial_sync_done = False
        # Incremental reconcile: already-owned rows are skipped and the remaining
        # work is bounded per pass (continuing on the next tick).
        self._reconcile_cursor = 0
        try:
            self.reconcile_batch_size = max(50, int(os.getenv("MANGATRAX_RECONCILE_BATCH_SIZE", "2000") or 2000))
        except (TypeError, ValueError):
            self.reconcile_batch_size = 2000
        self.is_backfilling = False
        self.stop_backfill_requested = False
        # Feed polling (task: "Poll Feeds" froze the UI).  The poll is network-heavy
        # and must never run on the event loop; a request starts a background thread
        # and the UI polls get_feed_check_status().  The lock also prevents a manual
        # poll and the interval poll from overlapping.
        self._feed_check_lock = threading.Lock()
        self._feed_check_state_lock = threading.Lock()
        self._staging_volume_cache: Optional[tuple] = None
        # qBittorrent file-priority pass: (release_id, torrent_hash, wanted tuple)
        # signatures already resolved. Torrent metadata can take a few seconds to
        # arrive, so the 30 s pass retries until it can map files, then never
        # re-calls filePrio (or re-logs the same decision) for that wanted set.
        self._selective_filter_done: set = set()
        self._feed_check_state: Dict[str, Any] = {
            "running": False,
            "started_at": None,
            "finished_at": None,
            "result": None,
        }

    def _log_activity(self, event_type: str, message: str, details: str = "", actor: Optional[str] = "scheduler"):
        """Audit helper for background work: stamps ``actor='scheduler'`` by default.

        The scheduler thread has no HTTP request context, so without an explicit
        actor every automated event would be indistinguishable from a user
        action in the audit trail.  Callers that represent a user-triggered job can
        pass the request actor explicitly.
        """
        self.db.log_activity(event_type, message, details, actor=actor)

    def _log_jpegxl_discard(self, rel) -> None:
        """Audit a discarded JPEG XL release once per guid, marked ``[JPEGXL]``."""
        guid = str(getattr(rel, "guid", "") or getattr(rel, "title", "") or "")
        if guid and guid in self._jpegxl_discard_notified:
            return
        if guid:
            self._jpegxl_discard_notified.add(guid)
        message = f"[JPEGXL] Discarded JPEG XL release: '{getattr(rel, 'title', '')}'"
        logger.info(message)
        self._log_activity(
            "JPEGXL_DISCARDED", message,
            "JPEG XL format is in the throw-away filter: never auto-downloaded or stored.",
        )

    # --- Naming model: STABLE MATCHING KEY vs. DISPLAY TITLE ---
    #
    # The stable key (``series_tracking.name`` / ``releases.series_name``) is the
    # Calibre library name when the series exists in Calibre, otherwise the user's
    # typed name.  AniList metadata NEVER rewrites it.  The AniList canonical
    # English title is only the cosmetic display title, resolved at render time
    # (see database.display_title_from_meta) and cached for later display.
    #
    # Matching builds candidate identities in this priority order and uses the
    # first hit:
    #   (1) Calibre-tracked name           (direct Calibre library match)
    #   (2) user-typed tracked name
    #   (3) AniList canonical English      (as a lookup bridge only)
    #   (4) AniList romaji                 (as a lookup bridge only)
    # A Calibre hit always wins over an AniList-derived one, and the AniList
    # titles are never persisted as the matching key.

    @staticmethod
    def _name_variants(raw_name: str) -> List[str]:
        """Candidate spellings of a parsed series name (raw, delimiter parts, parens)."""
        raw = (raw_name or "").strip()
        variants: List[str] = []
        seen = set()

        def _add(value: str):
            value = (value or "").strip(" -–—:,|./")
            if not value:
                return
            norm = normalize_text(value)
            if not norm or norm in seen:
                return
            seen.add(norm)
            variants.append(value)

        if not raw:
            return variants
        _add(raw)
        parts = split_series_candidate_parts(raw)
        if len(parts) > 1:
            for part in parts:
                _add(part)
        if "(" in raw and ")" in raw:
            base = re.sub(r"\(.*?\)", "", raw).strip()
            _add(base)
            for part in re.findall(r"\((.*?)\)", raw):
                _add(part)
        return variants

    def _build_calibre_context(self):
        """Load the Calibre library (or the synced tracking rows) + matcher."""
        calibre_path = self.db.get_setting("calibre_path", "/calibre")
        scanner = CalibreMangaScanner(calibre_path)
        if scanner.is_available():
            series_list = scanner.get_all_series()
            volumes_map = scanner.get_series_volumes_map()
        else:
            # Calibre not mounted here: the DB still knows the library names from the
            # last successful sync (plus user-typed tracked series).
            series_list = self.db.get_all_series()
            volumes_map = {}
        matcher = MangaCalibreMatcher(
            series_list, volumes_map, self.db.get_all_manga_metadata_cache(),
            staged_volumes_map=self._staged_volumes_map(),
        )
        return series_list, matcher

    # --- Marker-less chapter guard ---------------------------------------
    def _staged_volumes_map(self) -> Dict[str, set]:
        """Cached staged series→volumes ownership map (task-61)."""
        try:
            from app.staged_ownership import get_staged_volumes_map
            return get_staged_volumes_map(
                self.db, self.db.get_setting("staging_dir", "/staging"),
            )
        except Exception as exc:
            logger.debug("Could not build staged ownership map: %s", exc)
            return {}

    def _known_series_keys(self, matcher: Optional[MangaCalibreMatcher] = None) -> Set[str]:
        """Normalized names of every series MangaTrax can already name.

        Sources: the ``series_tracking`` rows (Calibre-synced *and* user-typed)
        plus the Calibre series list the matcher was built from.  This is the
        library context the pure parser cannot have, and it is what makes the
        trailing-number guard safe: a number is only a chapter when the title
        without it names a series we already know.
        """
        keys: Set[str] = set()
        names: List[str] = []
        try:
            for row in self.db.get_all_series():
                names.append(row.get("name") or "")
        except Exception as exc:  # pragma: no cover - guard must never break ingestion
            logger.debug("Could not load tracked series for the chapter guard: %s", exc)
        for series in getattr(matcher, "calibre_series", None) or []:
            names.append(series.get("series_name") or series.get("name") or "")

        for name in names:
            for variant in self._name_variants(name):
                key = normalize_text(variant)
                if key:
                    keys.add(key)
        return keys

    def _discard_unmarked_chapters_enabled(self) -> bool:
        """Whether marker-less chapter releases are discarded (default: true)."""
        value = str(self.db.get_setting("discard_unmarked_chapters", "true")).strip().lower()
        return value not in ("false", "0", "no", "off")

    def _unmarked_chapter_reason(self, rel, known_series_keys: Set[str]) -> str:
        """Return the audit reason when *rel* is a chapter of a KNOWN series.

        Consumes the parser's chapter diagnostics instead of re-deriving a trailing
        number from ``rel.series_name`` (the parser strips bare chapter numbers out
        of the series key so chapters group under the series).  All must hold:

        * not a complete-series release or light novel (classified before this);
        * the parser reports ``is_chapter`` and it is not a volume/bundle release;
        * the numbered full title is NOT itself a known series -- this is the
          explicit "Mob Psycho 100" protection: the number belongs to the name, so
          the release is never discarded;
        * the clean series name IS a known series (Calibre library or tracked row);
          with no context the release is retained under the clean series name
          (inconclusive means kept).
        """
        title = (getattr(rel, "title", "") or "").strip()
        if not title:
            return ""
        if is_complete_series_release(title) or is_light_novel(title) or is_chapter_only(title):
            return ""
        # Volume bundles are volume releases, never marker-less chapters.
        if getattr(rel, "volume_start", None) is not None or getattr(rel, "volumes", None):
            return ""
        try:
            parsed = extract_manga_bundle_and_volumes(title)
        except Exception:  # pragma: no cover - guard must never break ingestion
            return ""
        if not parsed.get("is_chapter") or parsed.get("is_bundle") or parsed.get("volumes"):
            return ""

        series = (getattr(rel, "series_name", None) or parsed.get("series_name") or "").strip()
        if not series:
            return ""
        series_key = normalize_text(series)
        if not series_key:
            return ""

        # Explicit protection: the numbered full title is itself a known series
        # ("Mob Psycho 100"), so its trailing number is part of the name.
        numbered = (parsed.get("numbered_series_name") or "").strip()
        numbered_key = normalize_text(numbered) if numbered else ""
        if numbered_key and numbered_key in known_series_keys:
            return ""
        if series_key not in known_series_keys:
            # No library context proves this is a chapter: keep the release, grouped
            # under the clean series name.
            return ""

        chapter = parsed.get("chapter_number")
        if isinstance(chapter, float):
            suffix = f" (chapter {chapter:g})"
        else:
            suffix = ""
        return f"parsed chapter{suffix} of known series '{series}'"

    def _resolve_release_series_input(self, rel, known_series_keys: Optional[Set[str]]) -> str:
        """Series name to resolve for *rel*, preserving numbered series names.

        The parser returns the clean series for bare-number chapters ("Dandadan"),
        which is what makes retained chapters group together.  If the numbered full
        title is itself a known series ("Mob Psycho 100"), that full title stays the
        matching key so a real numbered series is not split into chapters.
        """
        series = (getattr(rel, "series_name", None) or "").strip()
        if not known_series_keys or not series:
            return series
        if normalize_text(series) in known_series_keys:
            return series
        try:
            parsed = extract_manga_bundle_and_volumes(getattr(rel, "title", "") or "")
        except Exception:  # pragma: no cover - fall back to the parsed series name
            return series
        numbered = (parsed.get("numbered_series_name") or "").strip()
        if parsed.get("is_chapter") and numbered and normalize_text(numbered) in known_series_keys:
            return numbered
        return series

    def _explicit_chapter_range_reason(self, rel, known_series_keys: Set[str]) -> str:
        """Audit reason for an explicit chapter-RANGE release of a known series.

        Explicit chapter markers are always discarded (pre-existing policy).  Chapter
        RANGES ("Solo Leveling Ch1-50", "Ch 1-50") are batches of the same policy and
        deserve the same audit trail as the marker-less guard: when the clean series
        is in the Calibre library / tracked rows, log ``CHAPTER_DISCARDED`` with the
        range.  Single explicit chapters ("Chainsaw Man Ch. 180") and unknown series
        keep their previous silent path, so the existing behaviour is unchanged.
        """
        title = (getattr(rel, "title", "") or "").strip()
        if not title or not known_series_keys:
            return ""
        try:
            parsed = extract_manga_bundle_and_volumes(title)
        except Exception:  # pragma: no cover - guard must never break ingestion
            return ""
        ranges = parsed.get("chapter_ranges") or []
        if not ranges:
            return ""
        series = (parsed.get("series_name") or "").strip()
        series_key = normalize_text(series)
        if not series_key or series_key not in known_series_keys:
            return ""
        start, end = ranges[0][0], ranges[0][1]
        return (
            f"explicit chapter range {start:g}-{end:g} of known series '{series}'"
        )

    def _discard_unmarked_chapter(self, rel, known_series_keys: Set[str]) -> bool:
        """Discard and audit-log a context-proven marker-less chapter release.

        The activity event is written through the actor-aware scheduler helper, so
        every discard is attributed to ``scheduler`` and stays auditable (and
        therefore reversible: the message names the exact release title).
        """
        reason = self._unmarked_chapter_reason(rel, known_series_keys)
        if not reason:
            return False
        title = getattr(rel, "title", "") or ""
        logger.info("Discarding marker-less chapter release '%s': %s", title, reason)
        self._log_activity(
            "CHAPTER_DISCARDED",
            f"Discarded marker-less chapter release: '{title}'",
            reason,
        )
        return True

    # --- Collected-batch volume inference (metadata only, never a payload) -----
    #
    # A "collected release" (year-range title such as
    # "Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)", no volume marker)
    # is a full digital batch whose volume count is not in the title.  The count may
    # be resolved from AUTHORITATIVE metadata (AniList, then the MangaUpdates
    # fallback, both cache-aware) -- never by fetching a torrent or any payload --
    # and only when the series is finished and the count is known.  Otherwise the
    # release is kept volume-less and visible (inconclusive means kept) and retried
    # on the next poll.  Explicit volume markers always win.
    INFERENCE_FINISHED_STATUSES = ("FINISHED", "PUBLISHED")
    INFERENCE_MAX_VOLUMES = 300
    INFERENCE_MIN_NAME_SIMILARITY = 0.85

    def _infer_collected_releases_enabled(self) -> bool:
        """Whether collected-batch volume inference is enabled (default: true)."""
        value = str(self.db.get_setting("infer_collected_releases", "true")).strip().lower()
        return value not in ("false", "0", "no", "off")

    def _metadata_name_matches(self, candidate_name: str, series_key: str) -> bool:
        """True when metadata belongs to *series_key* (franchise-parent guard).

        Only exact normalized equality or a very high similarity counts: the
        franchise parent ("Blue Lock") must never satisfy the identity test for its
        spin-off ("Blue Lock - Episode Nagi"), even if the parent's metadata were to
        carry a volume count.
        """
        norm_candidate = normalize_text(candidate_name or "")
        norm_key = normalize_text(series_key or "")
        if not norm_candidate or not norm_key:
            return False
        if norm_candidate == norm_key:
            return True
        return string_similarity(norm_candidate, norm_key) >= self.INFERENCE_MIN_NAME_SIMILARITY

    def _infer_collected_release_volumes(self, rel, series_key: str, meta, alt_names):
        """Resolve a collected batch's full volume count from authoritative metadata.

        Returns ``(count, info)`` with ``info = {"source", "status", "canonical"}``,
        or ``(None, {})`` when no authoritative source reports a finished volume
        count for THIS series.  Metadata only -- never fetches a torrent/payload.
        """
        candidates = []

        def _consider(candidate, source_label: str):
            if not isinstance(candidate, dict):
                return
            source = str(candidate.get("source") or "").strip().lower()
            if source in ("", "fallback", "cleaned"):
                return
            total = candidate.get("total_volumes")
            try:
                count = int(total) if total not in (None, "") else 0
            except (TypeError, ValueError):
                count = 0
            if count <= 0 or count > self.INFERENCE_MAX_VOLUMES:
                return
            names = [
                candidate.get("canonical_title"),
                candidate.get("romaji_title"),
                candidate.get("english_title"),
                candidate.get("title"),
            ]
            if not any(
                isinstance(name, str) and self._metadata_name_matches(name, series_key)
                for name in names
            ):
                return
            candidates.append((count, {
                "source": source or source_label,
                "status": str(candidate.get("status") or "").upper(),
                "canonical": str(
                    candidate.get("canonical_title") or candidate.get("title") or ""
                ).strip(),
            }))

        _consider(meta, "anilist")
        for alt in (alt_names or []):
            if not alt:
                continue
            try:
                _consider(self.anilist.resolve_manga_series(alt), "anilist")
            except Exception as exc:  # pragma: no cover - metadata must never break ingestion
                logger.debug("Inference metadata lookup failed for alias '%s': %s", alt, exc)
        # MangaUpdates fallback (only when AniList has no finished count).
        if not any(
            info["status"] in self.INFERENCE_FINISHED_STATUSES for _count, info in candidates
        ):
            fallback_name = series_key or getattr(rel, "series_name", "") or ""
            try:
                _consider(self.mangaupdates.get_series_info(fallback_name), "mangaupdates")
            except Exception as exc:  # pragma: no cover - metadata must never break ingestion
                logger.debug("Inference MangaUpdates lookup failed for '%s': %s", fallback_name, exc)

        for count, info in candidates:
            if info["status"] in self.INFERENCE_FINISHED_STATUSES:
                return count, info
        return None, {}

    def _apply_collected_release_inference(self, rel, series_key: str, meta, alt_names) -> int:
        """Expand a collected batch when authoritative metadata knows its full set.

        Applies the inference contract and returns the inferred volume count (0 when
        nothing was inferred):
        * only for year-range titles with no volume/chapter markers;
        * never when the release already carries volumes or an explicit complete marker
          (explicit markers always win);
        * only when `total_volumes` is known AND the status is FINISHED/PUBLISHED;
        * otherwise the release stays volume-less and visible, and is retried later.
        """
        if getattr(rel, "volumes", None):
            return 0
        title = getattr(rel, "title", "") or ""
        if getattr(rel, "is_complete", False) or is_complete_series_release(title):
            return 0
        if not self._infer_collected_releases_enabled():
            return 0
        if not is_collected_release_candidate(title):
            return 0
        count, info = self._infer_collected_release_volumes(rel, series_key, meta, alt_names)
        if not count:
            logger.info(
                "Collected release '%s' kept volume-less: no authoritative finished "
                "volume count yet (retried on the next poll).", title,
            )
            return 0
        rel.volume_start = 1.0
        rel.volume_end = float(count)
        rel.volumes = [float(v) for v in range(1, count + 1)]
        rel.is_bundle = True
        rel.inferred_volumes = True
        self._log_activity(
            "COLLECTED_RELEASE_INFERRED",
            f"Inferred full volume set for '{series_key}': {count} volumes",
            f"Source: {info['source']}; status: {info['status']}; "
            f"canonical: '{info['canonical']}'; release: '{title}'",
        )
        return count

    @staticmethod
    def _metadata_alias_values(meta: Dict[str, Any], raw: str) -> List[str]:
        """Aliases belonging to ONE AniList entry (raw + canonical/romaji/synonyms)."""
        from app.parser import normalize_text

        values: List[str] = [str(raw or "").strip()]
        for key in ("canonical_title", "english_title", "romaji_title"):
            value = str(meta.get(key) or "").strip()
            if value:
                values.append(value)
        for synonym in meta.get("synonyms") or []:
            value = str(synonym or "").strip()
            if value:
                values.append(value)
        seen = set()
        aliases: List[str] = []
        for value in values:
            norm = normalize_text(value)
            if norm and norm not in seen:
                seen.add(norm)
                aliases.append(value)
        return aliases

    def _identity_entry_aliases(
        self, raw: str, variants: List[str], meta: Dict[str, Any],
    ) -> tuple:
        """Resolve the release's OWN AniList entry and its genuine aliases only.

        Returns ``(entry_meta, alias_values, anilist_id)``.  A metadata entry that
        was resolved via a delimiter/parent fallback (matched_term differs from raw
        and raw is not an alias of that entry) is rejected: a ``Parent: Spinoff``
        title must never inherit the parent's identity.  Delimiter parts are used
        only when they resolve to the SAME AniList id (e.g. ``English | Romaji``).
        """
        from app.parser import normalize_text

        raw = str(raw or "").strip()
        authoritative = bool(meta) and is_authoritative_metadata(meta)
        if authoritative:
            aliases = self._metadata_alias_values(meta, raw)
            alias_norms = {normalize_text(value) for value in aliases}
            raw_norm = normalize_text(raw)
            matched_norm = normalize_text(meta.get("matched_term") or "")
            entry_id = meta.get("anilist_id")
            direct_entry = (
                not matched_norm
                or matched_norm == raw_norm
                or raw_norm in alias_norms
            )
            if direct_entry:
                for variant in variants:
                    norm = normalize_text(variant)
                    if norm and norm in alias_norms and variant not in aliases:
                        aliases.append(variant)
                return meta, aliases, entry_id

        # The resolver fell back to a delimiter part (or returned no authoritative
        # entry for the combined spelling).  Only accept a part when the parts that DO
        # resolve authoritatively agree on ONE AniList entry.
        raw_norm = normalize_text(raw)
        resolved: Dict[Any, Dict[str, Any]] = {}
        for part in variants:
            norm = normalize_text(part)
            if not norm or norm == raw_norm:
                continue
            try:
                part_meta = self.anilist.resolve_manga_series(part)
            except Exception:
                continue
            if not part_meta or not is_authoritative_metadata(part_meta):
                continue
            part_aliases = self._metadata_alias_values(part_meta, part)
            part_alias_norms = {normalize_text(value) for value in part_aliases}
            part_matched = normalize_text(part_meta.get("matched_term") or "")
            if part_matched and part_matched != norm and norm not in part_alias_norms:
                continue  # this part's lookup also fell back elsewhere
            key = part_meta.get("anilist_id") or f"term:{normalize_text(part_meta.get('matched_term') or part)}"
            resolved.setdefault(key, part_meta)
        if len(resolved) != 1:
            return {}, [raw] if raw else [], None
        entry = next(iter(resolved.values()))
        aliases = self._metadata_alias_values(entry, raw)
        alias_norms = {normalize_text(value) for value in aliases}
        for part in variants:
            norm = normalize_text(part)
            if norm and norm in alias_norms and part not in aliases:
                aliases.append(part)
        return entry, aliases, entry.get("anilist_id")

    def resolve_series_identity(
        self,
        raw_series_name: str,
        matcher: Optional[MangaCalibreMatcher] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve a release's STABLE matching key and cosmetic DISPLAY title.

        Returns a dict with ``raw_name``, ``stable_name``, ``display_name``,
        ``source`` (which priority step matched), ``meta`` (AniList metadata, if
        any), ``alt_names`` (aliases for the Calibre matcher) and
        ``calibre_match``.  The AniList canonical/romaji titles are used only as
        lookup bridges; the fallback stable key is always the parsed name.
        """
        raw = (raw_series_name or "").strip()
        if not raw:
            return {
                "raw_name": raw, "stable_name": raw, "display_name": raw,
                "source": "empty", "meta": meta or {}, "alt_names": [],
                "calibre_match": None,
            }

        if matcher is None:
            _, matcher = self._build_calibre_context()
        calibre_series = list(getattr(matcher, "calibre_series", []) or [])

        # Metadata is resolved up-front (and cached) exactly as before: it still
        # drives covers / volume counts / display, it just never drives the key.
        # A pinned series resolves its STORED AniList id: automatic title search
        # must never replace a manual mapping with a fuzzy match.
        if meta is None:
            pinned = None
            try:
                series_row = self.db.get_series_by_name(raw)
            except Exception:  # pragma: no cover - defensive
                series_row = None
            if series_row and series_row.get("anilist_locked"):
                pinned = series_row.get("anilist_id")
            if pinned:
                try:
                    meta = self.anilist.resolve_by_anilist_id(pinned) or {}
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Pinned AniList lookup failed for '%s' (id %s): %s", raw, pinned, exc)
                    meta = {}
            elif series_row and series_row.get("anilist_locked"):
                # Locked without an id: never fall back to a title search.
                meta = {}
            else:
                try:
                    meta = self.anilist.resolve_manga_series(raw)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Metadata lookup failed for '%s': %s", raw, exc)
                    meta = {}
        meta = meta or {}
        variants = self._name_variants(raw)
        meta, aliases, anilist_id = self._identity_entry_aliases(raw, variants, meta)
        authoritative = is_authoritative_metadata(meta)
        canonical = (meta.get("canonical_title") or "").strip() if authoritative else ""
        romaji = (meta.get("romaji_title") or "").strip()
        aliases = aliases or [raw]
        try:
            from app.parser import clean_series_for_matching
            cleaned_alias = clean_series_for_matching(raw)
            if cleaned_alias and normalize_text(cleaned_alias) not in {normalize_text(v) for v in aliases}:
                aliases.append(cleaned_alias)
        except Exception:
            pass

        tracked_rows = self.db.get_all_series()
        calibre_tracked: Dict[str, str] = {}  # normalized -> tracked name (Calibre-origin)
        typed_tracked: Dict[str, str] = {}    # normalized -> user-typed tracked name
        for row in tracked_rows:
            name = (row.get("name") or "").strip()
            key = normalize_text(name)
            if not name or not key:
                continue
            if row.get("calibre_series_id") is not None:
                calibre_tracked.setdefault(key, name)
            else:
                typed_tracked.setdefault(key, name)

        def _match_calibre(name: str) -> str:
            if not calibre_series:
                return ""
            hit = match_manga_series_in_calibre(name, calibre_series)
            if not hit:
                return ""
            return (hit.get("series_name") or hit.get("name") or "").strip()

        def _make(stable: str, source: str) -> Dict[str, Any]:
            stable = (stable or "").strip() or raw
            explicit_display = ""
            try:
                explicit_display = self.db.get_series_display_override(stable)
            except Exception:  # pragma: no cover - display must never break matching
                explicit_display = ""
            display = (
                explicit_display
                or display_title_from_meta(stable, meta if authoritative else None)
                or stable
            )
            alt_names: List[str] = []
            seen = {normalize_text(stable)}
            for value in list(aliases) + ([canonical] if canonical else []) + ([romaji] if romaji else []):
                norm = normalize_text(value)
                if norm and norm not in seen:
                    seen.add(norm)
                    alt_names.append(value)
            return {
                "raw_name": raw,
                "stable_name": stable,
                "display_name": display,
                "source": source,
                "meta": meta,
                "alt_names": alt_names,
                "anilist_id": anilist_id,
                "calibre_match": None,
            }

        # (0) Explicit user alias override from a manual Awaiting-Calibre match
        #     (task-42).  This wins over AniList/Calibre identity resolution.
        try:
            from app.parser import clean_series_for_matching, series_name_candidates
            override = self.db.get_series_alias_override(normalize_text(raw))
            if not override:
                candidate_values = list(series_name_candidates(raw))
                cleaned_source = clean_series_for_matching(raw)
                if cleaned_source:
                    candidate_values.append(cleaned_source)
                for candidate in candidate_values:
                    norm = normalize_text(candidate)
                    if norm and norm != normalize_text(raw):
                        override = self.db.get_series_alias_override(norm)
                        if override:
                            break
        except Exception as exc:  # pragma: no cover - override lookup must not break identity
            logger.debug("Series alias override lookup failed for '%s': %s", raw, exc)
            override = None
        if override and override.get("target_name"):
            return _make(override["target_name"], "user_alias")

        # (1) Calibre-tracked name: only the release entry's OWN aliases may match.
        for candidate in aliases:
            hit = _match_calibre(candidate)
            if hit:
                return _make(hit, "calibre")

        # (1b) Calibre-origin tracked rows: covers deployments where the scanner is
        #      temporarily unavailable but the library was already synced to the DB.
        for candidate in aliases:
            hit = calibre_tracked.get(normalize_text(candidate))
            if hit:
                return _make(hit, "calibre_db")

        # (2) user-typed tracked name.
        for candidate in aliases:
            hit = typed_tracked.get(normalize_text(candidate))
            if hit:
                return _make(hit, "typed")

        # (3) AniList canonical English, then (4) AniList romaji, as lookup bridges.
        variant_norms = {normalize_text(v) for v in aliases}
        for bridge_source, bridge in (("anilist_canonical", canonical), ("anilist_romaji", romaji)):
            if not bridge or normalize_text(bridge) in variant_norms:
                continue  # same spelling as the raw name: already tried above
            hit = _match_calibre(bridge)
            if hit:
                return _make(hit, bridge_source)
            hit = calibre_tracked.get(normalize_text(bridge))
            if hit:
                return _make(hit, bridge_source)
            hit = typed_tracked.get(normalize_text(bridge))
            if hit:
                return _make(hit, bridge_source)

        # (5) Reverse alias resolution: the raw name may itself be an AniList alias
        #     of an already tracked series (romaji feed title, English tracker).
        alias_claims = self.db.get_authoritative_alias_index()
        for candidate in aliases:
            claims = alias_claims.get(normalize_text(candidate)) or set()
            if not claims:
                continue
            calibre_claims = [c for c in claims if normalize_text(c) in calibre_tracked]
            if len(calibre_claims) == 1:
                return _make(calibre_claims[0], "anilist_alias_calibre")
            typed_claims = [c for c in claims if c not in calibre_claims]
            if len(typed_claims) == 1:
                return _make(typed_claims[0], "anilist_alias_typed")
            logger.debug(
                "Ambiguous AniList alias '%s' claimed by %s; keeping the parsed name as the key.",
                candidate, sorted(claims),
            )

        # (6) Unknown series: the parsed name IS the stable key (never the AniList title).
        return _make(raw, "raw")

    def refresh_provider_tags(self, progress=None, cancel_check=None) -> Dict[str, Any]:
        """Backfill provider tags for releases lacking them (task-49, rate-limited)."""
        from app.tag_display import tags_from_metadata

        rows = self.db.get_releases(limit=1000, offset=0)
        series_names = []
        seen = set()
        for row in rows:
            if row.get("tags_json") and row["tags_json"] not in ("", "[]"):
                continue
            name = (row.get("series_name") or "").strip()
            key = normalize_text(name)
            if name and key and key not in seen:
                seen.add(key)
                series_names.append(name)
        updated = skipped = failed = 0
        total = len(series_names)
        for index, name in enumerate(series_names, 1):
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled by user")
            if progress:
                progress(index - 1, total, f"Fetching tags for '{name}'")
            try:
                meta = self.anilist.resolve_manga_series(name)
                tags = tags_from_metadata(meta)
                if not tags:
                    skipped += 1
                    continue
                self.db.cache_manga_metadata(name.lower(), meta)
                self.db.update_release_tags_for_series(name, tags)
                self.db.update_series_tags(name, tags)
                updated += 1
            except Exception as exc:
                logger.debug("Provider tag refresh failed for '%s': %s", name, exc)
                failed += 1
        if progress:
            progress(total, total, "Provider tags refreshed")
        return {"updated": updated, "skipped": skipped, "failed": failed, "total": total}

    def audit_series_attribution(self, progress=None) -> Dict[str, Any]:
        """Read-only audit: tracked releases whose own title parses to another series.

        Used by Settings -> Maintenance to repair rows that a previous spinoff/
        alternate-title bug stamped under the parent.  Suggestions are only offered
        when the release's own identity is unambiguous; ambiguous multi-part names are
        reported and never repaired automatically.
        """
        matcher = self._series_refresh_matcher()
        alias_index = self.db.get_authoritative_alias_index()
        started = time.monotonic()
        deadline = started + ATTRIBUTION_TIME_BUDGET_SECONDS
        resolution_cache: Dict[str, Dict[str, Any]] = {}
        truncated = False
        tracked: Dict[str, str] = {}
        for row in self.db.get_all_series() or []:
            name = str(row.get("name") or "").strip()
            norm = normalize_text(name)
            if norm:
                tracked.setdefault(norm, name)
        records: List[Dict[str, Any]] = []
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, title, series_name, status FROM releases "
                "WHERE series_name IS NOT NULL AND trim(series_name) != '' "
                "ORDER BY id ASC;"
            ).fetchall()
        total_rows = len(rows)
        for index, row in enumerate(rows, 1):
            if time.monotonic() >= deadline:
                truncated = True
                break
            if progress:
                progress(index - 1, total_rows, f"Auditing release #{row['id']}")
            current = str(row["series_name"] or "").strip()
            current_norm = normalize_text(current)
            if not current_norm or current_norm not in tracked:
                continue
            title = str(row["title"] or "").strip()
            # Cheap fast path: the title only appends volume/format noise to the
            # stored spelling, so parsing cannot reveal a different attribution.
            if _attribution_title_noise_only(normalize_text(title), current_norm):
                continue
            try:
                parsed = extract_manga_bundle_and_volumes(title)
                parsed_series = str(parsed.get("series_name") or "").strip()
            except Exception:
                parsed_series = ""
            parsed_norm = normalize_text(parsed_series)
            if not parsed_norm or parsed_norm == current_norm:
                continue
            # A genuine alias of the current tracked series is not a mis-attribution.
            claims = alias_index.get(parsed_norm) or set()
            if current_norm in {normalize_text(claim) for claim in claims}:
                continue
            if parsed_norm in resolution_cache:
                identity = resolution_cache[parsed_norm]
            else:
                try:
                    identity = self.resolve_series_identity(parsed_series, matcher=matcher)
                except Exception as exc:  # pragma: no cover - audit must never crash
                    logger.debug("Attribution audit identity lookup failed for %r: %s", parsed_series, exc)
                    continue
                resolution_cache[parsed_norm] = identity
            suggested = str(identity.get("stable_name") or "").strip() or parsed_series
            suggested_norm = normalize_text(suggested)
            if not suggested_norm or suggested_norm == current_norm:
                continue
            if suggested_norm == parsed_norm and suggested_norm in tracked:
                pass
            parts = [part for part in split_series_candidate_parts(parsed_series) if normalize_text(part)]
            ambiguous = len(parts) > 1 and (identity.get("source") == "raw")
            records.append({
                "id": int(row["id"]),
                "title": title,
                "current_series": current,
                "suggested_series": suggested,
                "identity_source": identity.get("source"),
                "anilist_id": identity.get("anilist_id"),
                "safe_to_repair": not ambiguous,
                "status": "ambiguous" if ambiguous else "repairable",
                "reason": (
                    "title parses to multiple possible series; not guessed"
                    if ambiguous else
                    f"release title parses to '{suggested}', not tracked '{current}'"
                ),
            })
        if progress:
            message = "Attribution audit complete" if not truncated else (
                f"Attribution audit hit its {ATTRIBUTION_TIME_BUDGET_SECONDS:g}s budget — results are partial"
            )
            progress(total_rows, total_rows, message)
        return {
            "status": "success",
            "truncated": truncated,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "summary": {
                "scanned": len(rows),
                "repairable": sum(1 for item in records if item["safe_to_repair"]),
                "ambiguous": sum(1 for item in records if not item["safe_to_repair"]),
                "truncated": truncated,
            },
            "files": records,
        }

    def repair_series_attribution(self, release_ids: List[int], actor: str = "user",
                                  progress=None) -> Dict[str, Any]:
        """Re-key explicitly selected, provably mis-attributed releases (idempotent)."""
        audit = self.audit_series_attribution(progress=progress)
        by_id = {item["id"]: item for item in audit.get("files") or []}
        repaired = 0
        skipped = 0
        results: List[Dict[str, Any]] = []
        with self.db.get_connection() as conn:
            for release_id in release_ids:
                item = by_id.get(int(release_id))
                if not item or not item.get("safe_to_repair"):
                    skipped += 1
                    results.append({"id": release_id, "outcome": "skipped"})
                    continue
                conn.execute(
                    "UPDATE releases SET series_name = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?;",
                    (item["suggested_series"], int(release_id)),
                )
                repaired += 1
                if progress:
                    progress(repaired, len(release_ids or []), f"Re-keyed release #{release_id}")
                results.append({
                    "id": release_id, "outcome": "repaired",
                    "current_series": item["current_series"],
                    "suggested_series": item["suggested_series"],
                })
            conn.commit()
        if repaired:
            self.db._invalidate_grouped_cache()
            message = f"Re-keyed {repaired} mis-attributed release(s) to their own series"
            logger.info(message)
            self._log_activity(
                "SERIES_ATTRIBUTION_REPAIRED", message,
                json.dumps({"repaired": repaired, "skipped": skipped}),
                actor=actor,
            )
        return {
            "status": "success", "repaired": repaired, "skipped": skipped,
            "results": results,
        }

    def repair_series_matching_keys(self, actor: Optional[str] = "scheduler") -> int:
        """Run the guarded, idempotent legacy re-key repair (safe on every startup)."""
        try:
            return self.db.repair_rekeyed_series_names(actor=actor)
        except Exception as exc:  # pragma: no cover - repair must never block startup
            logger.error("Series key repair failed: %s", exc)
            return 0

    def _nyaa_base_url(self) -> str:
        """Resolved Nyaa/tracker base URL for every provider construction.

        ``nyaa_base_url`` setting -> default ``https://nyaa.si``; an invalid stored
        value falls back to the default (and is logged) rather than taking feeds or
        backfill offline.
        """
        try:
            return normalize_nyaa_base_url(self.db.get_setting("nyaa_base_url", DEFAULT_NYAA_BASE_URL))
        except NyaaBaseURLError as exc:
            logger.warning(
                "Invalid nyaa_base_url setting (%s); falling back to %s", exc, DEFAULT_NYAA_BASE_URL,
            )
            return DEFAULT_NYAA_BASE_URL

    def get_notifier(self) -> NotificationService:
        discord = self.db.get_setting("discord_webhook_url", "")
        generic = self.db.get_setting("apprise_url", "") or self.db.get_setting("generic_webhook_url", "")
        return NotificationService(discord_webhook=discord, generic_webhook=generic)

    def get_qbit_client(self) -> QBitClient:
        settings = self.db.get_all_settings()
        enabled_str = str(settings.get("qbit_enabled", "true")).lower()
        is_enabled = enabled_str not in ("false", "0", "no", "off")
        return QBitClient(
            base_url=settings.get("qbit_url", "http://localhost:8080"),
            username=settings.get("qbit_username", "admin"),
            password=settings.get("qbit_password", "adminadmin"),
            enabled=is_enabled
        )

    # --- Download engine selection (task-20) ---
    def get_downloader_engine(self) -> str:
        return resolve_downloader_engine(self.db)

    def get_downloader(self):
        """Build the configured engine (Torbox does not need a qBittorrent client)."""
        engine = self.get_downloader_engine()
        qbit = self.get_qbit_client() if engine == "qbit" else None
        return build_downloader(self.db, qbit_client=qbit, engine=engine)

    @staticmethod
    def _safe_torrent_dirname(name: str) -> str:
        """Filesystem-safe folder name for a Torbox torrent's staging files."""
        cleaned = re.sub(r"[^\w\s.()\[\]-]+", "_", str(name or "")).strip().strip(".")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return (cleaned[:120] or "torbox-download")

    @staticmethod
    def _release_missing_volumes(release: Dict[str, Any]) -> List[float]:
        missing = release.get("missing_volumes")
        if missing is None:
            raw = release.get("missing_vols_json") or "[]"
            try:
                missing = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                missing = []
        volumes: List[float] = []
        for value in missing or []:
            try:
                volumes.append(float(value))
            except (TypeError, ValueError):
                continue
        return volumes

    def resolve_release_missing_volumes(self, release: Dict[str, Any]) -> List[float]:
        """Accurate wanted volumes for a release (stored values, else the matcher).

        Entry points that may not have a freshly-computed missing set (the manual
        download route, a polled Torbox row) use this so strict selection never
        defaults to "everything" just because the stored list is empty.
        """
        persisted = release.get("download_wanted_json")
        if persisted:
            try:
                values = [float(value) for value in json.loads(persisted)]
                if values:
                    return values
            except (TypeError, ValueError):
                pass
        stored = self._release_missing_volumes(release)
        if stored:
            return stored
        try:
            matcher = self._series_refresh_matcher()
            result = matcher.check_release(release, auto_request_monitored=False)
            return [float(value) for value in (result.get("missing_volumes") or [])]
        except Exception as exc:
            logger.debug("Could not compute missing volumes for '%s': %s", release.get("title"), exc)
            return stored

    def _auto_download_engine_ready(self) -> tuple:
        """Cheap, no-network readiness check for the configured download engine."""
        try:
            engine = self.get_downloader_engine()
        except Exception:
            engine = "qbit"
        if engine == "torbox":
            enabled = as_bool(self.db.get_setting("torbox_enabled", "false"), default=False)
            has_key = bool(str(self.db.get_setting("torbox_api_key", "") or "").strip())
            return (enabled and has_key, "torbox" if enabled and has_key else "torbox_disabled_or_no_api_key")
        try:
            client = self.get_qbit_client()
        except Exception:
            client = None
        return (client is not None, "qbit_connected" if client is not None else "qbit_unavailable")

    def _log_auto_download_decision(self, submitted: bool, reason: str, feed_name: str,
                                    rel, series: str, missing: List[float],
                                    wanted: Optional[List[float]] = None) -> None:
        event = "AUTO_DOWNLOAD_SUBMITTED" if submitted else "AUTO_DOWNLOAD_SKIPPED"
        title = str(getattr(rel, "title", "") or "")
        series_name = str(series or getattr(rel, "series_name", "") or "")
        detail = {
            "reason": reason,
            "feed": feed_name,
            "series": series_name,
            "title": title,
            "missing_volumes": [round(float(value), 2) for value in (missing or [])],
            "wanted_volumes": [round(float(value), 2) for value in (wanted if wanted is not None else missing or [])],
        }
        label = "submitted" if submitted else f"skipped ({reason})"
        try:
            self._log_activity(
                event, f"Auto-download {label}: '{title}'", json.dumps(detail),
            )
        except Exception as exc:
            logger.debug("Could not audit auto-download decision: %s", exc)

    def _staging_reserved_volumes(self) -> Dict[str, set]:
        """Volumes already present in staging without needing a tracked-release row."""
        now = time.time()
        cached = getattr(self, "_staging_volume_cache", None)
        if cached and now - cached[0] < 60:
            return cached[1]
        result: Dict[str, set] = {}
        root = self.db.get_setting("staging_dir", "/staging")
        try:
            if root and os.path.isdir(root):
                for current, dirs, names in os.walk(root):
                    dirs[:] = [name for name in dirs if not name.startswith(".")]
                    for name in names:
                        if not name.lower().endswith((".cbz", ".cbr", ".zip", ".epub")):
                            continue
                        if name.lower().endswith((".part", ".tmp")) or name.startswith("."):
                            continue
                        try:
                            parsed = extract_manga_bundle_and_volumes(name)
                        except Exception:
                            continue
                        series = normalize_text(str(parsed.get("series_name") or ""))
                        if not series:
                            continue
                        volumes = result.setdefault(series, set())
                        for value in parsed.get("volumes") or []:
                            try:
                                volumes.add(float(value))
                            except (TypeError, ValueError):
                                continue
        except Exception as exc:
            logger.debug("Could not scan staging volumes for dedupe: %s", exc)
        self._staging_volume_cache = (now, result)
        return result

    def get_auto_download_diagnostics(self) -> Dict[str, Any]:
        """Read-only explanation of auto-download state and recent decisions."""
        settings = self.db.get_all_settings() or {}
        feeds = self.db.get_all_feeds() or []
        engine_ready, engine_reason = self._auto_download_engine_ready()
        try:
            engine = self.get_downloader_engine()
        except Exception:
            engine = "qbit"
        monitored = [series for series in (self.db.get_all_series() or []) if series.get("is_monitored")]
        last_result = None
        feed_poll_state: Dict[str, Any] = {}
        try:
            feed_poll_state = self.get_feed_check_status() or {}
            last_result = feed_poll_state.get("result")
        except Exception:
            last_result = None
        logs = self.db.get_logs(limit=100) or []
        decisions = []
        for row in logs:
            event = str(row.get("event_type") or "")
            if event not in ("AUTO_DOWNLOAD_SUBMITTED", "AUTO_DOWNLOAD_SKIPPED", "IN_FLIGHT_DEDUPE"):
                continue
            details = {}
            try:
                details = json.loads(row.get("details") or "{}")
            except (TypeError, ValueError):
                details = {}
            decisions.append({
                "event": event,
                "message": row.get("message") or "",
                "reason": details.get("reason") or ("all_volumes_inflight" if event == "IN_FLIGHT_DEDUPE" else ""),
                "feed": details.get("feed") or "",
                "series": details.get("series") or "",
                "missing_volumes": details.get("missing_volumes") or [],
                "wanted_volumes": details.get("wanted_volumes") or [],
                "created_at": row.get("created_at") or "",
            })
        global_enabled = str(settings.get("auto_download", "true")).lower() == "true"
        auto_feeds = [feed for feed in feeds if feed.get("enabled") and feed.get("auto_download")]
        checklist = [
            {"key": "global_enabled", "ok": global_enabled,
             "label": "Global Auto-download is enabled (Settings → General)."},
            {"key": "feeds_enabled", "ok": len(auto_feeds) > 0,
             "label": "At least one enabled feed has Auto-download on."},
            {"key": "monitored_series", "ok": len(monitored) > 0,
             "label": "At least one monitored/Calibre series can translate a release to REQUESTED."},
            {"key": "engine_ready", "ok": engine_ready,
             "label": f"Download engine ({engine}) is ready."},
            {"key": "new_or_transition_releases", "ok": bool(last_result and (last_result.get("releases_found") or 0) > 0),
             "label": "The last feed poll found releases; only new/transitioning ones auto-download."},
        ]
        last_poll_hint = ""
        if isinstance(last_result, dict):
            found = int(last_result.get("releases_found") or 0)
            failed_feeds = int(last_result.get("feeds_failed") or 0)
            if found == 0 and failed_feeds:
                errs = [
                    str(item.get("error") or "") for item in (last_result.get("errors") or [])[:2]
                    if isinstance(item, dict) and item.get("error")
                ]
                last_poll_hint = (
                    f"The last poll failed for {failed_feeds} feed(s)"
                    + (f": {'; '.join(errs)}" if errs else "")
                    + ". No releases could be seen, so nothing can auto-download; fix/retry the feed and "
                      "check the activity log for the provider error."
                )
            elif found == 0:
                last_poll_hint = (
                    "The last poll returned no parseable manga releases: the feed may have had no items, or "
                    "every item was filtered as a chapter/light novel. Check the Feeds page status and the "
                    "activity log; the next poll containing a new volume release will show found/new counts here."
                )
        elif feed_poll_state.get("running"):
            last_poll_hint = "A feed poll is running now; its result will appear when it finishes."
        else:
            last_poll_hint = (
                "No feed poll result is available in this session yet. Run Poll Feeds (or wait for the "
                "interval) so this card can report what the feeds returned."
            )
        return {
            "status": "success",
            "global_enabled": global_enabled,
            "engine": engine,
            "engine_ready": engine_ready,
            "engine_reason": engine_reason,
            "last_poll_at": feed_poll_state.get("finished_at") or feed_poll_state.get("started_at") or "",
            "feed_poll_running": bool(feed_poll_state.get("running")),
            "last_poll_success": last_result.get("success") if isinstance(last_result, dict) else None,
            "last_poll_message": (last_result or {}).get("message") or "" if isinstance(last_result, dict) else "",
            "last_poll_errors": (last_result or {}).get("errors") or [] if isinstance(last_result, dict) else [],
            "last_poll_hint": last_poll_hint,
            "feeds": [
                {"id": feed.get("id"), "name": feed.get("name"), "enabled": bool(feed.get("enabled")),
                 "auto_download": bool(feed.get("auto_download"))}
                for feed in feeds
            ],
            "feeds_total": len(feeds),
            "feeds_auto_download": len(auto_feeds),
            "monitored_series": len(monitored),
            "tracked_releases": len(self.db.get_releases(limit=5000, offset=0) or []),
            "last_poll": last_result or {},
            "recent_decisions": decisions[:20],
            "checklist": checklist,
            "limitations": [
                "qBittorrent cannot select individual files: a partial overlap downloads the "
                "whole torrent, then the existing selective-volume filter applies when supported.",
                "Staging-only volumes are detected by a bounded parsed-name scan; files with "
                "unparseable names cannot reserve volumes.",
            ],
            "prerequisites": [
                "Global setting auto_download=true and the feed’s Auto-download toggle on.",
                "A monitored series in Calibre: releases are effectively REQUESTED (not DISCOVERY/IGNORED).",
                "The release is new or transitions DISCOVERY/NEW → REQUESTED during a feed poll.",
                "At least one missing volume remains after in-flight/staging dedupe.",
                "A magnet/HTTP download URL and a ready downloader engine are available.",
            ],
            "staging_dedupe_volumes": {key: sorted(value) for key, value in self._staging_reserved_volumes().items()},
        }

    def _auto_download_wanted(self, rel, stable_series: str, missing_vols: List[float]):
        """Auto-download only: exclude volumes already in flight/awaiting Calibre.

        Returns ``(wanted_volumes, reserved_volumes)``.  A volume is reserved when
        another release of the same series is DOWNLOADING or COMPLETED (files fetched
        but not yet imported into Calibre), so a second group's release for the same
        volume is not auto-downloaded.  Failed/IDLE rows release their reservation;
        manual downloads intentionally bypass this filter.
        """
        missing = [float(value) for value in (missing_vols or [])]
        if not missing:
            return missing, set()
        series_names: List[str] = []
        for candidate in (stable_series, getattr(rel, "series_name", None)):
            text = str(candidate or "").strip()
            if text and text not in series_names:
                series_names.append(text)
        try:
            reserved = self.db.get_inflight_reserved_volumes(
                series_names, exclude_guid=getattr(rel, "guid", None),
            )
        except Exception as exc:
            logger.debug("In-flight reservation lookup failed for '%s': %s", getattr(rel, "title", ""), exc)
            reserved = set()
        # Also reserve volumes whose files are already in staging but whose release row
        # is missing/IDLE (external placement or old reset): bounded once-per-minute scan.
        try:
            staged = self._staging_reserved_volumes()
            for candidate in series_names:
                reserved |= staged.get(normalize_text(candidate), set())
        except Exception as exc:
            logger.debug("Staging reservation lookup failed for '%s': %s", getattr(rel, "title", ""), exc)
        wanted = [value for value in missing if round(value, 2) not in reserved]
        if len(wanted) != len(missing):
            in_flight = sorted({round(value, 2) for value in missing} & reserved)
            title = str(getattr(rel, "title", "") or "")
            if wanted:
                message = (
                    f"Auto-download dedupe for '{title}': volume(s) {in_flight} already in "
                    f"flight/downloaded and awaiting Calibre; downloading only {wanted}."
                )
            else:
                message = (
                    f"Auto-download suppressed for '{title}': volume(s) {in_flight} already "
                    "in flight/downloaded and awaiting Calibre; no duplicate submitted."
                )
            logger.info(message)
            self._log_activity(
                "IN_FLIGHT_DEDUPE", message,
                json.dumps({
                    "series": series_names,
                    "missing_volumes": missing,
                    "reserved_volumes": sorted(reserved),
                    "wanted_volumes": wanted,
                }),
            )
        return wanted, reserved

    @staticmethod
    def _parse_db_timestamp(value: Any) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    def get_downloads_dir(self, create: bool = True) -> str:
        """Raw download destination for both engines (task-22 routing model)."""
        path = self.db.get_setting("downloads_dir", downloads_pipeline.DEFAULT_DOWNLOADS_DIR) or downloads_pipeline.DEFAULT_DOWNLOADS_DIR
        if create:
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as exc:
                logger.warning("Could not create downloads_dir '%s': %s", path, exc)
        return path

    @staticmethod
    def _feed_scan_has_activity(result: Dict[str, Any]) -> bool:
        """True only when a poll found new releases or requested/sent/processed something.

        ``releases_found`` alone is not activity: a poll that only re-sees known
        releases (or failed providers with no entries) must not notify.
        """
        payload = result or {}
        for key in ("new_releases", "transitions", "requested", "sent_to_downloader"):
            try:
                if int(payload.get(key) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        for key in ("processed", "newly_processed"):
            value = payload.get(key)
            if isinstance(value, (list, tuple, set)):
                if len(value) > 0:
                    return True
            else:
                try:
                    if int(value or 0) > 0:
                        return True
                except (TypeError, ValueError):
                    continue
        return False

    def _notify_feed_scan_result(self, result: Dict[str, Any]) -> None:
        try:
            if str(self.db.get_setting("webhook_feed_scan_notifications", "true")).lower() == "false":
                return
            if not self._feed_scan_has_activity(result):
                logger.debug(
                    "Suppressed automatic feed-scan notification: no new/requested/sent activity "
                    "(found=%s, feeds_failed=%s).",
                    (result or {}).get("releases_found"), (result or {}).get("feeds_failed"),
                )
                return
            from datetime import datetime as _datetime
            self.get_notifier().notify_feed_scan(
                result or {}, trigger="auto",
                finished_at=_datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            )
        except Exception as exc:
            logger.debug("Feed scan notification failed: %s", exc)

    def notify_processed_batches(self, result: Dict[str, Any]) -> None:
        """Send one notification per series+volume group after tag/move."""
        try:
            if str(self.db.get_setting("webhook_processed_notifications", "true")).lower() == "false":
                return
            items = (result or {}).get("processed") or []
            if not items:
                return
            tracked = []
            try:
                tracked = self.db.get_tracked_downloads(limit=500)
            except Exception:
                tracked = []
            groups: Dict[Any, Dict[str, Any]] = {}
            for item in items:
                series = str(item.get("series_name") or "Unknown Series")
                volumes = sorted({round(float(value), 2) for value in (item.get("volumes") or [])})
                key = (normalize_text(series), tuple(volumes))
                group = groups.setdefault(key, {
                    "series_name": series, "volumes": volumes, "files": [], "destination": "",
                    "release_id": None, "title": "",
                })
                if item.get("filename"):
                    group["files"].append(str(item["filename"]))
                group["destination"] = group["destination"] or str(item.get("destination") or "")
                if not group["release_id"] and tracked:
                    matched = downloads_inventory._match_release(
                        str(item.get("source") or ""), str(item.get("filename") or ""), tracked,
                    )
                    if matched:
                        for release in tracked:
                            if str(release.get("title") or "") == str(matched.get("title") or ""):
                                group["release_id"] = release.get("id")
                                group["title"] = str(release.get("title") or "")
                                break
            notifier = self.get_notifier()
            for group in groups.values():
                try:
                    notifier.notify_download_processed(group)
                except Exception as exc:
                    logger.debug("Processed-group notification failed: %s", exc)
        except Exception as exc:
            logger.debug("Processed notifications failed: %s", exc)

    def _user_staging_folders(self) -> set:
        """Registered per-user staging folder names (never flattened/routed globally)."""
        try:
            return {
                str(user.get("staging_folder") or "").strip().lower()
                for user in self.db.list_users()
                if str(user.get("staging_folder") or "").strip()
            }
        except Exception:
            return set()

    def process_downloads_to_staging(self, actor: Optional[str] = "scheduler") -> Dict[str, Any]:
        """Tag+move downloads whose series exists in Calibre into staging.

        Series not in Calibre are left untouched in downloads_dir (summarised via
        AWAITING_CALIBRE_SERIES).  Never raises: a routing failure must not break the
        worker or an API call.
        """
        try:
            downloads_dir = self.get_downloads_dir(create=False)
            staging_dir = self.db.get_setting("staging_dir", "/staging")
            series_templates = self.db.get_series_templates_map()
            result = downloads_pipeline.process_downloads_to_staging(
                downloads_dir, staging_dir, series_templates, self.db, actor=actor,
                exclude_dirs=self._user_staging_folders(),
            )
            self.notify_processed_batches(result)
            try:
                self.db.set_setting("downloads_routing_summary", json.dumps(result))
            except Exception:
                pass
            return result
        except Exception as exc:
            logger.error("Downloads routing pass failed: %s", exc)
            return {
                "scanned": 0,
                "tagged_and_moved": 0,
                "awaiting_calibre": 0,
                "failed": 0,
                "skipped_existing": 0,
                "series_awaiting": [],
                "error": str(exc),
            }

    def submit_torbox_download(
        self,
        release_id: int,
        title: str,
        download_url: str,
        link: str = "",
        missing_volumes: Optional[List[float]] = None,
        source: str = "",
        guid: str = "",
        torrent_hash: str = "",
    ) -> Dict[str, Any]:
        """Submit a release to Torbox and persist its handle (used by all entry points).

        All known identifiers are passed to the engine (download_url/link/guid plus the
        stored torrent_hash) so a release without a magnet can still be submitted via
        an infohash-derived magnet or a derived ``/download/<id>.torrent`` URL.
        """
        engine = "torbox"
        wanted = [float(v) for v in (missing_volumes or [])]
        # Strict exact-volume selection: never submit a torrent when nothing is missing.
        strict_selection = as_bool(
            self.db.get_setting("torbox_strict_volume_selection", "true"), default=True,
        )
        if strict_selection and not wanted:
            message = (
                f"Torbox submit skipped for '{title}': nothing missing (all volumes already owned); "
                "no Torbox slot consumed."
            )
            logger.info(message)
            self._log_activity(
                "SELECTIVE_DOWNLOAD", message,
                f"wantedVolumes=[]; Source: {source}" if source else "wantedVolumes=[]",
            )
            return {
                "success": True,
                "skipped": True,
                "engine": engine,
                "handle": {},
                "queued": False,
                "cached": False,
                "message": message,
            }
        try:
            max_concurrent = int(self.db.get_setting("torbox_max_concurrent", "2") or 2)
        except ValueError:
            max_concurrent = 2
        active = [
            row for row in self.db.get_active_torbox_downloads()
            if parse_queued_handle_id(row.get("downloader_id")) is None
        ]
        if len(active) >= max(1, max_concurrent):
            message = (
                f"Torbox concurrency limit reached ({max_concurrent} active download(s)); "
                f"'{title}' was not submitted."
            )
            logger.warning(message)
            self._log_activity("TORBOX_FAILED", message, f"torbox_max_concurrent={max_concurrent}")
            return {"success": False, "engine": engine, "message": message, "retryable": True}

        downloader = build_downloader(self.db, engine=engine)
        downloads_dir = self.get_downloads_dir()
        savepath = os.path.join(downloads_dir, self._safe_torrent_dirname(title))
        try:
            release_row = self.db.get_release_by_id(release_id) or {}
        except Exception:
            release_row = {}
        release = {
            "download_url": download_url,
            "link": link,
            "title": title,
            "guid": guid or release_row.get("guid") or "",
            "torrent_hash": torrent_hash or release_row.get("torrent_hash") or "",
        }
        try:
            result = downloader.submit(release, savepath, wanted_volumes=wanted)
        except DownloaderError as exc:
            message = f"Torbox submit failed for '{title}': {exc}"
            logger.warning(message)
            self._log_activity("TORBOX_FAILED", message, f"Source: {source}" if source else "")
            return {"success": False, "engine": engine, "message": str(exc), "retryable": True}

        handle = result.get("handle") or {}
        torrent_id = handle.get("torrent_id")
        queued_id = handle.get("queued_id")
        persisted_id = str(torrent_id) if torrent_id else (
            encode_queued_handle_id(queued_id) if queued_id else ""
        )
        self.db.update_download_handle(
            release_id,
            engine,
            downloader_id=persisted_id,
            torrent_hash=handle.get("hash"),
            wanted_volumes=wanted,
        )
        cached = bool(result.get("cached"))
        if queued_id and not torrent_id:
            # No free Torbox active slot: the submission is queued, not failed.  It is
            # polled on the normal cadence (and does not consume max-concurrent slots).
            message = "queued: waiting for a free Torbox slot"
            self._log_activity(
                "TORBOX_SUBMITTED",
                f"Sent to Torbox (queued): '{title}'",
                f"queued_id={queued_id}; {message}"
                + (f"; Source: {source}" if source else "")
                + (f"; wantedVolumes={wanted}" if wanted else ""),
            )
            self.request_torbox_poll()
            return {
                "success": True,
                "engine": engine,
                "handle": handle,
                "cached": False,
                "queued": True,
                "state": "queued_slot",
                "message": message,
            }

        event = "TORBOX_CACHED" if cached else "TORBOX_SUBMITTED"
        detail_parts = [
            f"torrent_id={torrent_id}",
            f"hash={handle.get('hash') or ''}",
        ]
        if wanted:
            detail_parts.append(f"wantedVolumes={wanted}")
        if source:
            detail_parts.append(f"Source: {source}")
        self._log_activity(
            event,
            f"Sent to Torbox{' (cached)' if cached else ''}: '{title}'",
            "; ".join(detail_parts),
        )
        self.request_torbox_poll()
        return {"success": True, "engine": engine, "handle": handle, "cached": cached, "queued": False}

    # --- Torbox polling worker (task-20, Phase 1+2; queued slots task-21) ---
    def _torbox_handle_for_release(self, release_id: int) -> Dict[str, Any]:
        """Reconstruct a Torbox handle from the release row (for failure cleanup/retry)."""
        try:
            row = self.db.get_release_by_id(release_id) or {}
        except Exception:
            return {}
        raw = row.get("downloader_id")
        queued_id = parse_queued_handle_id(raw)
        if queued_id is not None:
            return {
                "engine": "torbox", "queued_id": queued_id, "torrent_id": None,
                "hash": str(row.get("torrent_hash") or ""),
            }
        try:
            torrent_id = int(raw or 0)
        except (TypeError, ValueError):
            torrent_id = 0
        if torrent_id:
            return {
                "engine": "torbox", "torrent_id": torrent_id,
                "hash": str(row.get("torrent_hash") or ""),
            }
        return {}

    def _manual_pickup_settings(self) -> Dict[str, Any]:
        enabled = as_bool(self.db.get_setting("torbox_manual_pickup_enabled", "true"), default=True)
        try:
            max_items = max(0, int(self.db.get_setting("torbox_manual_pickup_max_items", "10") or 10))
        except (TypeError, ValueError):
            max_items = 10
        try:
            expiry_days = max(0, int(self.db.get_setting("torbox_manual_pickup_expiry_days", "7") or 7))
        except (TypeError, ValueError):
            expiry_days = 7
        return {"enabled": enabled, "max_items": max_items, "expiry_days": expiry_days}

    def _manual_pickup_file_snapshot(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        snapshot = []
        for item in files or []:
            name = str(item.get("name") or item.get("path") or "")
            volumes = self._parse_torbox_file_volumes(name)
            snapshot.append({
                "id": item.get("id"),
                "name": name,
                "size": int(item.get("size") or 0),
                "suggested_volume": volumes[0] if len(volumes) == 1 else None,
                "volumes": volumes,
                "mapping_source": "parsed" if volumes else "none",
                "fetched": False,
            })
        return snapshot

    def _hold_torbox_for_manual_pickup(self, release_id: int, title: str, reason: str,
                                       downloader=None, handle: Optional[Dict[str, Any]] = None,
                                       info: Optional[Dict[str, Any]] = None) -> bool:
        """Keep a Torbox torrent whose volume mapping failed for manual pickup.

        Returns False when pickup is disabled/capped, in which case the caller falls
        back to the legacy FAILED + remote-delete behaviour.
        """
        settings = self._manual_pickup_settings()
        if not settings["enabled"]:
            return False
        if settings["max_items"] > 0 and self.db.count_active_manual_pickups() >= settings["max_items"]:
            self._log_activity(
                "TORBOX_MANUAL_PICKUP_CAP",
                f"Manual pickup cap reached for '{title}'",
                f"cap={settings['max_items']}; remote torrent will be removed",
            )
            return False
        info = info or {}
        files = self._manual_pickup_file_snapshot(info.get("files") or [])
        release = self.db.get_release_by_id(release_id) or {}
        wanted = self.resolve_release_missing_volumes(release)
        expires_at = None
        if settings["expiry_days"] > 0:
            expires_at = (datetime.utcnow() + timedelta(days=settings["expiry_days"])).strftime("%Y-%m-%d %H:%M:%S")
        torrent_id = str(handle.get("torrent_id") if handle else (info.get("torrent_id") or release.get("downloader_id") or "") or "")
        pickup_id = self.db.create_torbox_manual_pickup(
            release_id, torrent_id, title, str(release.get("series_name") or ""),
            files, wanted, reason, expires_at=expires_at,
        )
        hint = (
            f"{reason} Held for manual pickup #{pickup_id}: choose volumes/files on "
            "Downloads → Manual Pickup (torrent kept on Torbox)."
        )
        self.db.set_download_error(release_id, hint)
        self.db.update_downloader_state(release_id, "MANUAL_PICKUP", "torbox")
        self._log_activity(
            "TORBOX_MANUAL_PICKUP",
            f"Held Torbox torrent for manual pickup: '{title}'",
            f"pickup={pickup_id}; files={len(files)}; wanted={wanted}; expires={expires_at or 'never'}",
        )
        logger.warning("Torbox manual pickup #%s for '%s': %s", pickup_id, title, reason)
        return True

    def sweep_torbox_manual_pickups(self, downloader=None) -> int:
        """Expire past-due holds (best-effort remote delete); never-expire stays."""
        expired = self.db.expired_torbox_manual_pickups()
        count = 0
        for pickup in expired:
            pickup_id = int(pickup["id"])
            handle = {
                "engine": "torbox",
                "torrent_id": int(pickup.get("torrent_id") or 0) or None,
                "hash": "",
            }
            remote = ""
            try:
                engine = downloader or build_downloader(self.db, engine="torbox")
                if handle["torrent_id"] and engine.cancel(handle):
                    remote = " Removed from Torbox."
            except Exception as exc:
                logger.debug("Could not remove expired pickup #%s remote: %s", pickup_id, exc)
            self.db.update_torbox_manual_pickup(
                pickup_id, status="expired", error=f"Expired{remote}".strip(),
            )
            release_id = int(pickup.get("release_id") or 0)
            if release_id:
                self.db.set_download_error(
                    release_id,
                    f"Manual pickup #{pickup_id} expired; torrent removed from Torbox.",
                )
                self.db.update_downloader_state(release_id, "FAILED", "torbox")
            self._log_activity(
                "TORBOX_MANUAL_PICKUP_EXPIRED",
                f"Manual pickup #{pickup_id} expired",
                f"release={release_id}{remote}",
            )
            count += 1
        return count

    def _pickup_handle(self, pickup: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "engine": "torbox",
            "torrent_id": int(pickup.get("torrent_id") or 0) or None,
            "hash": "",
        }

    def update_manual_pickup_mapping(self, pickup_id: int, mapping: List[Dict[str, Any]]) -> Dict[str, Any]:
        pickup = self.db.get_torbox_manual_pickup(pickup_id)
        if not pickup:
            raise ValueError("Manual pickup not found")
        files = json.loads(pickup.get("files_json") or "[]")
        by_index = {int(item.get("index", index)): item for index, item in enumerate(files)}
        for change in mapping or []:
            try:
                index = int(change.get("index"))
            except (TypeError, ValueError):
                continue
            item = by_index.get(index)
            if item is None:
                continue
            volume = change.get("volume")
            if volume in (None, ""):
                item["suggested_volume"] = None
                item["volumes"] = []
                item["mapping_source"] = "manual"
            else:
                try:
                    value = float(volume)
                except (TypeError, ValueError):
                    continue
                item["suggested_volume"] = value
                item["volumes"] = [value]
                item["mapping_source"] = "manual"
        self.db.update_torbox_manual_pickup(pickup_id, files=files)
        return {"status": "success", "pickup_id": pickup_id, "files": files}

    def update_manual_pickup_release_volumes(self, pickup_id: int,
                                             volumes: List[float]) -> Dict[str, Any]:
        pickup = self.db.get_torbox_manual_pickup(pickup_id)
        if not pickup:
            raise ValueError("Manual pickup not found")
        clean = []
        for value in volumes or []:
            try:
                clean.append(float(value))
            except (TypeError, ValueError):
                continue
        if not clean:
            raise ValueError("At least one volume is required")
        release_id = int(pickup.get("release_id") or 0)
        self.db.update_release_volumes_and_status(release_id, clean, clean)
        # resolve_release_missing_volumes() prefers download_wanted_json; without this
        # a corrected volume list would be ignored by Retry-with-corrections.
        self.db.set_release_download_wanted(release_id, clean)
        self.db.update_torbox_manual_pickup(pickup_id, wanted_volumes=clean)
        return {"status": "success", "pickup_id": pickup_id, "wanted_volumes": clean}

    def _fetch_manual_pickup_files(self, pickup_id: int, selected_indexes: Optional[List[int]],
                                   actor: str = "user", progress=None) -> Dict[str, Any]:
        pickup = self.db.get_torbox_manual_pickup(pickup_id)
        if not pickup:
            raise ValueError("Manual pickup not found")
        if str(pickup.get("status")) in ("discarded", "expired", "completed"):
            raise ValueError(f"Manual pickup is {pickup.get('status')}")
        files = json.loads(pickup.get("files_json") or "[]")
        if selected_indexes:
            chosen = [item for index, item in enumerate(files) if index in set(selected_indexes)]
        else:
            chosen = [item for index, item in enumerate(files) if not item.get("fetched")]
        if not chosen and selected_indexes:
            raise ValueError("No files selected")
        max_gb = float(self.db.get_setting("torbox_max_download_gb", "64") or 64)
        total_size = 0
        descriptors = []
        for item in chosen:
            size = int(item.get("size") or 0)
            if size and size > max_gb * 1024 ** 3:
                raise DownloaderFileError(
                    f"File '{item.get('name')}' is above the {max_gb:g} GiB cap."
                )
            total_size += size
            descriptors.append({"id": item.get("id"), "name": item.get("name"), "size": size})
        whole_torrent = not descriptors and not selected_indexes and not files
        if not descriptors and not whole_torrent:
            raise ValueError("No files selected")
        if total_size and total_size > max_gb * 1024 ** 3:
            raise DownloaderFileError(
                f"Selected files total {total_size / 1024 ** 3:.1f} GiB, above the "
                f"{max_gb:g} GiB cap (torbox_max_download_gb)."
            )
        downloader = build_downloader(self.db, engine="torbox")
        release = self.db.get_release_by_id(int(pickup.get("release_id") or 0)) or {}
        title = str(pickup.get("title") or release.get("title") or "manual-pickup")
        dest_dir = os.path.join(self.get_downloads_dir(), self._safe_torrent_dirname(title))
        self.db.update_torbox_manual_pickup(pickup_id, status="fetching")
        handle = self._pickup_handle(pickup)
        handle["size"] = total_size
        saved = []
        failed_files = 0
        if progress and not whole_torrent:
            for index, descriptor in enumerate(descriptors, 1):
                try:
                    progress(index - 1, len(descriptors), f"Fetching {descriptor.get('name')}…")
                    saved.extend(downloader.fetch_files(
                        handle, [descriptor], dest_dir,
                        progress_cb=self._torbox_progress_callback(int(pickup.get("release_id") or 0), total_size),
                    ) or [])
                    progress(index, len(descriptors), f"Fetched {descriptor.get('name')}",
                             force=True)
                except Exception as exc:
                    failed_files += 1
                    logger.warning("Manual pickup file failed (%s): %s", descriptor.get("name"), exc)
                    progress(index, len(descriptors), f"Failed {descriptor.get('name')}: {exc}")
            if not saved and failed_files:
                raise DownloaderFileError(f"All selected files failed to fetch (last error above).")
        else:
            if progress:
                progress(0, 1, "Fetching whole torrent…" if whole_torrent else "Fetching selected files…", force=True)
            saved = downloader.fetch_files(
                handle, descriptors if not whole_torrent else None, dest_dir,
                progress_cb=(self._torbox_progress_callback(int(pickup.get("release_id") or 0), total_size) if progress is None else None),
            ) or []
            if progress:
                progress(1, 1, f"Fetched {len(saved)} file(s)", progress_pct=100.0, force=True)
        fetched_names = {str(item.get("name") or "") for item in (descriptors or [])}
        for index, item in enumerate(files):
            if str(item.get("name") or "") in fetched_names:
                item["fetched"] = True
        remaining = [item for index, item in enumerate(files) if not item.get("fetched")]
        status = "completed" if not remaining else "partial"
        self.db.update_torbox_manual_pickup(pickup_id, files=files, status=status)
        release_id = int(pickup.get("release_id") or 0)
        if release_id:
            paths = [str((item or {}).get("path") or "") for item in (saved or [])]
            self.db.set_download_files(release_id, paths)
            self.db.clear_download_error(release_id)
            self.db.update_downloader_state(release_id, "COMPLETED", "torbox")
            if status == "completed":
                try:
                    self.process_downloads_to_staging(actor=actor)
                except Exception as exc:
                    logger.debug("Routing after manual pickup failed: %s", exc)
            self._log_activity(
                "TORBOX_MANUAL_PICKUP_FETCHED",
                f"Fetched {len(descriptors)} file(s) from manual pickup #{pickup_id}",
                f"release={release_id}; status={status}",
            )
        return {
            "status": "success", "pickup_id": pickup_id,
            "fetched": len(saved or descriptors or []), "failed": failed_files,
            "remaining": len(remaining), "pickup_status": status,
        }

    def retry_manual_pickup_strict(self, pickup_id: int, actor: str = "user",
                                   progress=None) -> Dict[str, Any]:
        pickup = self.db.get_torbox_manual_pickup(pickup_id)
        if not pickup:
            raise ValueError("Manual pickup not found")
        release = self.db.get_release_by_id(int(pickup.get("release_id") or 0)) or {}
        files = json.loads(pickup.get("files_json") or "[]")
        # The pickup's wanted list is the user's latest correction; only fall back to
        # the release/matcher when it is empty.
        try:
            wanted = [float(value) for value in json.loads(pickup.get("wanted_json") or "[]")]
        except (TypeError, ValueError):
            wanted = []
        if not wanted:
            wanted = self.resolve_release_missing_volumes(release)
        plan = self._plan_torbox_selection(release, files, wanted, total_size=0)
        if plan.get("action") == "skip":
            raise ValueError(plan.get("reason") or "Nothing to fetch")
        selection = plan.get("files") if plan.get("action") == "files" else None
        indexes = []
        if selection:
            selected_names = {str(item.get("name") or "") for item in selection}
            indexes = [index for index, item in enumerate(files) if str(item.get("name") or "") in selected_names]
        else:
            indexes = [index for index, item in enumerate(files) if not item.get("fetched")]
        return self._fetch_manual_pickup_files(pickup_id, indexes, actor=actor, progress=progress)

    def discard_manual_pickup(self, pickup_id: int, actor: str = "user") -> Dict[str, Any]:
        pickup = self.db.get_torbox_manual_pickup(pickup_id)
        if not pickup:
            raise ValueError("Manual pickup not found")
        remote = ""
        try:
            downloader = build_downloader(self.db, engine="torbox")
            handle = self._pickup_handle(pickup)
            if handle.get("torrent_id") and downloader.cancel(handle):
                remote = " Removed from Torbox."
        except Exception as exc:
            logger.debug("Could not delete discarded pickup remote: %s", exc)
        self.db.update_torbox_manual_pickup(pickup_id, status="discarded", error=f"Discarded by user.{remote}")
        release_id = int(pickup.get("release_id") or 0)
        if release_id:
            self.db.set_download_error(release_id, f"Manual pickup discarded.{remote}")
            self.db.update_downloader_state(release_id, "FAILED", "torbox")
        return {"status": "success", "pickup_id": pickup_id, "discarded": True}

    def _torbox_fail_release(
        self,
        release_id: int,
        title: str,
        message: str,
        downloader=None,
        handle: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a Torbox release FAILED, persist the visible reason and free the slot.

        ``download_error`` (with the attempt count + poll timestamp) is what the
        Downloads page shows instead of Torbox's misleading live ``Ready`` state.
        With ``torbox_delete_on_failure`` (default true) the remote torrent is deleted
        so it does not occupy an active slot forever.
        """
        reason = str(message or "Torbox download failed.")
        attempts = self.db.increment_download_attempt(release_id)
        stored_reason = (
            f"{reason} (attempt {attempts}, "
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC)"
        )
        self.db.set_download_error(release_id, stored_reason)
        self.db.update_downloader_state(release_id, "FAILED", "torbox")
        remote = ""
        if as_bool(self.db.get_setting("torbox_delete_on_failure", "true"), default=True):
            target_handle = handle or self._torbox_handle_for_release(release_id)
            if target_handle and (target_handle.get("torrent_id") or target_handle.get("queued_id")):
                try:
                    engine = downloader or build_downloader(self.db, engine="torbox")
                    remote = " Removed from Torbox." if engine.cancel(target_handle) else " Torbox remove failed."
                except Exception as exc:
                    logger.debug("Could not delete failed Torbox job %s: %s", release_id, exc)
                    remote = " Torbox remove failed."
        self._log_activity(
            "TORBOX_FAILED",
            f"Torbox download failed: '{title}'",
            f"{reason}{remote}",
        )
        logger.warning("Torbox download failed for '%s': %s%s", title, reason, remote)

    def _torbox_surface_once(
        self, release_id: int, message: str, title: str, event_type: str = "TORBOX_FAILED",
    ) -> None:
        """Audit a non-fatal Torbox problem once per distinct message (no log spam)."""
        previous = self._torbox_notified.get(release_id)
        if previous == message:
            return
        self._torbox_notified[release_id] = message
        label = {"TORBOX_QUEUED": "queued", "TORBOX_PROMOTED": "activated"}.get(event_type, "download issue")
        self._log_activity(event_type, f"Torbox {label}: '{title}'", message)
        logger.warning("Torbox %s for '%s': %s", label, title, message)

    _TORBOX_FILE_RANGE_RES = (
        re.compile(r"(?i)(?:vol(?:ume)?s?\.?\s*|(?<![a-z])v\s*)(\d{1,3}(?:\.\d+)?)\s*[-–—~]\s*(?:v\s*)?(\d{1,3}(?:\.\d+)?)"),
        re.compile(r"(?i)\[\s*(?:vol(?:ume)?s?\.?\s*|v\s*)(\d{1,3}(?:\.\d+)?)\s*[-–—~]\s*(?:v\s*)?(\d{1,3}(?:\.\d+)?)\s*\]"),
        re.compile(r"(?i)\(\s*(?:v\s*)?(\d{1,3}(?:\.\d+)?)\s*[-–—~]\s*(?:v\s*)?(\d{1,3}(?:\.\d+)?)\s*\)"),
    )

    @staticmethod
    def _parse_torbox_file_volumes(file_name: str) -> List[float]:
        """Map one Torbox file name to its volume numbers (ranges expanded).

        The release parser covers ``v01``/``Vol. 3``/``v01-03``; explicit range
        patterns additionally cover bracket/parenthesised forms such as
        ``[Vol 1-3]`` and ``(v1-v3)``.  Torrent file names frequently carry a
        story-arc marker (``... Part 02 v01``, ``... Part 2 - 03``): the part number
        is NEVER a file volume, so it is removed before parsing.  Bare trailing or
        bracketed numbers are used as a final fallback (torrent file names are
        usually just ``<series> 03.cbz``) after excluding years and resolutions.
        Returns ``[]`` when nothing is parseable, which strict selection treats as
        an unmappable file.
        """
        base = os.path.basename(str(file_name or "").replace("\\", "/").strip())
        if not base:
            return []
        # "Part 02" / "Pt. 2" is an arc marker, never a volume in a file name.
        volume_base = re.sub(r"(?i)\b(?:parts?|pts?)\.?\s*\d{1,3}(?:\.\d+)?\b", " ", base)
        volumes: Set[float] = set()
        try:
            parsed = extract_manga_bundle_and_volumes(volume_base)
            volumes.update(float(value) for value in (parsed.get("volumes") or []))
            start = parsed.get("volume_start")
            end = parsed.get("volume_end")
            if start is not None:
                volumes.add(float(start))
                if end is not None and float(end) > float(start) and float(end) - float(start) <= 200:
                    if float(start).is_integer() and float(end).is_integer():
                        volumes.update(float(v) for v in range(int(start), int(end) + 1))
                    else:
                        volumes.add(float(end))
        except Exception:
            pass
        for pattern in MangaScheduler._TORBOX_FILE_RANGE_RES:
            for match in pattern.finditer(volume_base):
                try:
                    first = float(match.group(1))
                    last = float(match.group(2))
                except (TypeError, ValueError):
                    continue
                if last < first or last - first > 200:
                    continue
                if first.is_integer() and last.is_integer():
                    volumes.update(float(v) for v in range(int(first), int(last) + 1))
                else:
                    volumes.update({first, last})
        if not volumes:
            # Fallback: the last bare/bracketed number is the volume for torrent file
            # names without a v/vol marker ("Bookworm Part 2 - 03 (1080p).cbz",
            # "... [04].cbz").  Years/resolutions are metadata, not volumes.
            stem = os.path.splitext(volume_base)[0]
            numbers = [float(m.group(0)) for m in re.finditer(r"(?<!\d)\d{1,4}(?:\.\d+)?(?!\d)", stem)]
            numbers = [n for n in numbers if not (1900 <= n <= 2099) and n not in (480.0, 720.0, 1080.0, 2160.0)]
            if numbers:
                volumes.add(numbers[-1])
        return sorted(volumes)

    def _legacy_torbox_selection(self, files: List[Dict[str, Any]], wanted: Set[float]):
        """Old permissive behaviour used when torbox_strict_volume_selection=false."""
        if not wanted:
            return None, "all volumes wanted: whole-torrent zip"
        if not files:
            return None, "Torbox returned no file list: whole-torrent zip fallback"
        file_volumes: Dict[int, Set[float]] = {}
        unmapped: List[str] = []
        for index, item in enumerate(files):
            name = str((item or {}).get("name") or (item or {}).get("short_name") or "")
            volumes = set(self._parse_torbox_file_volumes(name))
            if not volumes:
                unmapped.append(name or f"file #{index}")
                continue
            file_volumes[index] = volumes
        if unmapped:
            logger.info(
                "Torbox: %s file(s) could not be mapped to volumes (%s); downloading the whole torrent.",
                len(unmapped), ", ".join(unmapped[:3]),
            )
            return None, f"unmappable file name(s): {len(unmapped)} -> whole-torrent zip"
        selected = [item for index, item in enumerate(files) if file_volumes.get(index, set()) & wanted]
        if not selected:
            return None, "volume subset matched no files: whole-torrent zip fallback"
        if len(selected) == len(files):
            return None, "subset selects every file: whole-torrent zip"
        return selected, f"subset: {len(selected)}/{len(files)} file(s) for volumes {sorted(wanted)}"

    def _torbox_max_download_bytes(self) -> int:
        try:
            max_gb = float(self.db.get_setting("torbox_max_download_gb", "64") or 64)
        except (TypeError, ValueError):
            max_gb = 64.0
        max_gb = max(1.0, min(max_gb, 10240.0))
        return int(max_gb * 1024 * 1024 * 1024)

    def _check_torbox_file_size(self, item: Dict[str, Any], max_bytes: int) -> None:
        item = item or {}
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > max_bytes:
            name = str(item.get("name") or item.get("short_name") or f"file {item.get('id')}")
            size_gb = size / (1024 ** 3)
            cap_gb = max_bytes / (1024 ** 3)
            raise DownloaderFileError(
                f"Torbox file '{name}' is {size_gb:.1f} GiB ({size} bytes), above the "
                f"configured cap of {cap_gb:g} GiB (torbox_max_download_gb); refusing to download it."
            )

    def _plan_torbox_selection(
        self,
        release: Dict[str, Any],
        files: List[Dict[str, Any]],
        wanted_volumes: List[float],
        total_size: int = 0,
    ) -> Dict[str, Any]:
        """Decide exactly which Torbox files to fetch (strict by default).

        Decision rule (strict=true, setting ``torbox_strict_volume_selection``):
          * no wanted volumes -> ``skip`` (do not fetch/submit; nothing to download);
          * no file list, any unmappable file name, or any wanted volume absent from
            the torrent -> typed DownloaderFileError (never download everything);
          * every file needed and all wanted covered -> ``zip`` (equivalent) only when
            the estimated archive is comfortably under the configured cap and the file
            count is small (<= TORBOX_ZIP_MAX_FILES); otherwise the exact files are
            downloaded per-file so each one is checked individually;
          * otherwise -> ``files`` with only the files whose parsed volume set
            intersects the wanted set (per-file requestdl).
        A single wanted file larger than ``torbox_max_download_gb`` raises a typed
        DownloaderFileError naming the file, its size and the cap BEFORE any download.
        strict=false restores the legacy whole-torrent fallback.
        """
        title = str(release.get("title") or "")
        strict = as_bool(self.db.get_setting("torbox_strict_volume_selection", "true"), default=True)
        wanted = {float(value) for value in (wanted_volumes or [])}
        logger.debug(
            "Torbox selection for '%s': strict=%s wanted volumes %s", title, strict, sorted(wanted),
        )
        if not strict:
            selection, reason = self._legacy_torbox_selection(files, wanted)
            return {
                "action": "zip" if selection is None else "files",
                "files": selection,
                "reason": reason,
                "mapping": [],
                "strict": False,
                "wanted_volumes": sorted(wanted),
                "selected_volumes": [],
                "skipped_volumes": [],
                "selected_count": len(selection or []),
                "total_files": len(files or []),
            }
        if not wanted:
            return {
                "action": "skip",
                "files": None,
                "reason": "nothing missing: all volumes already owned",
                "mapping": [],
                "strict": True,
                "wanted_volumes": [],
                "selected_volumes": [],
                "skipped_volumes": [],
                "selected_count": 0,
                "total_files": len(files or []),
            }
        if not files:
            raise DownloaderFileError(
                f"Torbox strict volume selection for '{title}' failed: the torrent returned no "
                "file list; refusing to download the whole torrent."
            )
        mapping: List[Dict[str, Any]] = []
        unmapped: List[str] = []
        for index, item in enumerate(files):
            item = item if isinstance(item, dict) else {}
            name = str(item.get("name") or item.get("short_name") or "")
            volumes = self._parse_torbox_file_volumes(name)
            mapping.append({"index": index, "id": item.get("id"), "name": name, "volumes": volumes})
            if not volumes:
                unmapped.append(name or f"file #{index}")
            logger.debug(
                "Torbox selection for '%s': file '%s' (id=%s) -> volumes %s",
                title, name or f"file #{index}", item.get("id"), volumes,
            )
        if unmapped:
            message = (
                f"Torbox strict volume selection for '{title}' failed: {len(unmapped)} file name(s) "
                f"cannot be mapped to volumes ({', '.join(unmapped[:5])}); refusing to download the whole torrent."
            )
            logger.error("%s file->volume mapping: %s", message, json.dumps(mapping))
            raise DownloaderFileError(message)
        available: Set[float] = set()
        for entry in mapping:
            available.update(entry["volumes"])
        absent = sorted(wanted - available)
        if absent:
            message = (
                f"Torbox strict volume selection for '{title}' failed: wanted volume(s) {absent} "
                f"are absent from the torrent (available volumes: {sorted(available)})."
            )
            logger.error("%s file->volume mapping: %s", message, json.dumps(mapping))
            raise DownloaderFileError(message)
        selected_indices = [
            entry["index"] for entry in mapping if set(entry["volumes"]) & wanted
        ]
        selected_files = [files[index] for index in selected_indices]
        covered: Set[float] = set()
        for index in selected_indices:
            covered.update(mapping[index]["volumes"])
        all_needed = bool(selected_indices) and len(selected_indices) == len(files) and all(
            set(entry["volumes"]) <= wanted for entry in mapping
        ) and (covered & wanted) == wanted
        skipped_volumes = sorted(available - wanted)
        result = {
            "mapping": mapping,
            "strict": True,
            "wanted_volumes": sorted(wanted),
            "skipped_volumes": skipped_volumes,
            "selected_volumes": sorted(covered & wanted),
            "selected_count": len(selected_files),
            "total_files": len(files),
        }
        max_bytes = self._torbox_max_download_bytes()
        estimated_total = int(total_size or 0)
        if not estimated_total:
            estimated_total = sum(
                int((item or {}).get("size") or 0) for item in files if isinstance(item, dict)
            )
        if all_needed:
            zip_ok = (
                len(files) <= TORBOX_ZIP_MAX_FILES
                and (not estimated_total or estimated_total <= int(max_bytes * TORBOX_ZIP_SAFETY_RATIO))
            )
            if zip_ok:
                return {
                    "action": "zip",
                    "files": None,
                    "reason": f"all {len(files)} file(s) needed: whole-torrent zip",
                    **result,
                }
            # The archive would be huge (or the bundle has many files): fall back to
            # exact per-file downloads so each file is individually size-checked.  This
            # is NOT a strictness regression: every needed file is still selected.
            try:
                for item in selected_files:
                    self._check_torbox_file_size(item, max_bytes)
            except DownloaderFileError as exc:
                logger.error("%s file->volume mapping: %s", exc, json.dumps(mapping))
                raise
            return {
                "action": "files",
                "files": selected_files,
                "reason": (
                    f"all {len(files)} file(s) needed but the whole-torrent archive is "
                    f"{estimated_total / (1024 ** 3):.1f} GiB / {len(files)} file(s) (safe zip "
                    f"limit: {TORBOX_ZIP_MAX_FILES} files and <{TORBOX_ZIP_SAFETY_RATIO:.0%} of "
                    "torbox_max_download_gb); downloading the exact files per-file"
                ),
                **result,
            }
        try:
            for item in selected_files:
                self._check_torbox_file_size(item, max_bytes)
        except DownloaderFileError as exc:
            logger.error("%s file->volume mapping: %s", exc, json.dumps(mapping))
            raise
        return {
            "action": "files",
            "files": selected_files,
            "reason": (
                f"strict subset: {len(selected_files)}/{len(files)} file(s) selected, "
                f"{len(skipped_volumes)} owned volume(s) skipped"
            ),
            **result,
        }

    def _select_torbox_files(self, release: Dict[str, Any], files: List[Dict[str, Any]], wanted_volumes: List[float]):
        """Backwards-compatible wrapper: ``(selection, reason)`` with None = zip."""
        plan = self._plan_torbox_selection(release, files, wanted_volumes)
        if plan["action"] in ("zip", "skip"):
            return None, plan["reason"]
        return plan["files"], plan["reason"]

    @staticmethod
    def _cleanup_torbox_partials(dest_dir: str) -> None:
        """Remove temp/partial files from a failed strict-selection job (never user files)."""
        if not dest_dir or not os.path.isdir(dest_dir):
            return
        for root, _dirs, files in os.walk(dest_dir):
            for filename in files:
                if filename.lower().endswith((".part", ".mangatrax-part")):
                    try:
                        os.remove(os.path.join(root, filename))
                    except OSError:
                        pass

    def refresh_downloads_inventory(self) -> Dict[str, Any]:
        """Scan the raw downloads folder and persist the result (task-30)."""
        try:
            return downloads_inventory.refresh_inventory(self.db, self.get_downloads_dir(create=False))
        except Exception as exc:
            logger.debug("Downloads inventory scan failed: %s", exc)
            return {}

    def process_downloads_to_staging_for_users(self) -> Dict[str, Any]:
        """Route each user's ``downloads/<folder>/`` into their own staging folder.

        User-initiated downloads are submitted with ``savepath=downloads/<folder>``
        (see the download endpoint), so provenance survives the qBittorrent hop and
        files can never land in another user's staging directory.
        """
        root_downloads = self.get_downloads_dir(create=False)
        staging_root = self.db.get_setting("staging_dir", "/staging")
        results: Dict[str, Any] = {}
        if not root_downloads or not staging_root:
            return results
        try:
            users = self.db.list_users()
        except Exception as exc:
            logger.debug("Per-user routing could not list users: %s", exc)
            return results
        for user in users:
            folder = str(user.get("staging_folder") or "").strip()
            if not folder:
                continue
            user_downloads = os.path.join(root_downloads, folder)
            if not os.path.isdir(user_downloads):
                continue
            user_staging = os.path.join(staging_root, folder)
            try:
                os.makedirs(user_staging, exist_ok=True)
                results[folder] = downloads_pipeline.process_downloads_to_staging(
                    user_downloads, user_staging, self.db.get_series_templates_map(),
                    self.db, actor="scheduler",
                )
            except Exception as exc:
                logger.debug("Per-user routing failed for '%s': %s", folder, exc)
                results[folder] = {"error": str(exc)[:200]}
        return results

    def periodic_downloads_inventory_and_route(self) -> Dict[str, Any]:
        """Cheap frequent pass: inventory + tag/move eligible raw downloads."""
        inventory = self.refresh_downloads_inventory()
        routed = {}
        try:
            routed = self.process_downloads_to_staging(actor="scheduler")
        except Exception as exc:
            logger.debug("Frequent downloads routing failed: %s", exc)
        try:
            user_routes = self.process_downloads_to_staging_for_users()
            if user_routes:
                routed = {"shared": routed, "users": user_routes}
        except Exception as exc:
            logger.debug("Per-user downloads routing failed: %s", exc)
        moved = int((routed or {}).get("tagged_and_moved") or 0)
        if moved:
            logger.info(
                "Frequent routing pass: moved %s file(s) from downloads to staging.", moved,
            )
        return {"inventory": inventory, "routing": routed}

    def prune_activity_logs(self, actor: str = "scheduler", force: bool = False) -> Dict[str, Any]:
        """Retention prune for activity_logs (startup + ≤6h), WAL checkpointed."""
        try:
            retention_days = int(self.db.get_setting("activity_retention_days", "90") or 90)
        except (TypeError, ValueError):
            retention_days = 90
        try:
            max_rows = int(self.db.get_setting("activity_max_rows", "50000") or 50000)
        except (TypeError, ValueError):
            max_rows = 50000
        result = self.db.prune_activity_logs(retention_days, max_rows, checkpoint=True)
        try:
            self.db.set_setting("activity_last_prune_at", str(time.time()))
        except Exception:
            pass
        deleted = int(result.get("deleted_old", 0)) + int(result.get("deleted_excess", 0))
        if deleted:
            message = (
                f"Activity log pruned: deleted_old={result.get('deleted_old', 0)}, "
                f"deleted_excess={result.get('deleted_excess', 0)}, "
                f"remaining={result.get('remaining', 0)}"
            )
            logger.info(message)
            try:
                self._log_activity(
                    "ACTIVITY_PRUNED", message,
                    json.dumps({**result, "retention_days": retention_days, "max_rows": max_rows}),
                    actor=actor,
                )
            except Exception as exc:  # pragma: no cover - audit must not fail the prune
                logger.debug("Could not audit activity prune: %s", exc)
        return result

    def sweep_pending_dedupe(self, limit: int = 50) -> Dict[str, Any]:
        """Retry auto-downloads that were suppressed by in-flight dedupe.

        Only rows marked ``PENDING_DEDUPE`` are considered, at most ``limit`` per
        cycle, and nothing is logged while a reservation still stands.  When the
        reservation clears (the first release failed/was cancelled) the duplicate is
        submitted with its full missing set and the state is cleared.
        """
        summary = {"checked": 0, "submitted": 0, "still_reserved": 0, "cleared": 0}
        rows = self.db.get_pending_dedupe_releases(limit=limit)
        if not rows:
            return summary
        downloads_dir = self.get_downloads_dir()
        engine = self.get_downloader_engine()
        qbit = None
        try:
            matcher = self._series_refresh_matcher()
        except Exception as exc:
            logger.debug("Dedupe sweep could not build the Calibre matcher: %s", exc)
            matcher = None
        for row in rows:
            summary["checked"] += 1
            release_id = int(row.get("id") or 0)
            title = str(row.get("title") or "")
            if not release_id:
                continue
            if str(row.get("status") or "") != "REQUESTED":
                # Ignored/blacklisted/IN_CALIBRE: never resurrect these.
                self.db.update_downloader_state(release_id, "IDLE")
                summary["cleared"] += 1
                continue
            missing = self.resolve_release_missing_volumes(row)
            if not missing:
                self.db.update_downloader_state(release_id, "IDLE")
                summary["cleared"] += 1
                continue
            if matcher is not None:
                # Never resurrect a volume that landed in Calibre while the duplicate
                # waited: let the normal status/reconcile path own that decision.
                try:
                    payload = dict(row)
                    payload["volumes"] = [
                        float(value) for value in json.loads(row.get("volumes_json") or "[]")
                    ]
                    fresh = matcher.check_release(payload, auto_request_monitored=False)
                    if fresh.get("status") == "IN_CALIBRE":
                        self.db.update_downloader_state(release_id, "IDLE")
                        summary["cleared"] += 1
                        continue
                    fresh_missing = [float(value) for value in (fresh.get("missing_volumes") or [])]
                    if fresh_missing:
                        missing = fresh_missing
                except Exception as exc:
                    logger.debug("Dedupe sweep ownership check failed for '%s': %s", title, exc)
            try:
                reserved = self.db.get_inflight_reserved_volumes(
                    [row.get("series_name") or ""],
                    exclude_guid=str(row.get("guid") or ""),
                )
            except Exception as exc:
                logger.debug("Dedupe sweep reservation lookup failed for '%s': %s", title, exc)
                reserved = set()
            wanted = [value for value in missing if round(value, 2) not in reserved]
            if not wanted:
                summary["still_reserved"] += 1
                continue
            success = False
            try:
                if engine == "torbox":
                    result = self.submit_torbox_download(
                        release_id,
                        title,
                        row.get("download_url") or row.get("link") or "",
                        link=row.get("link") or "",
                        missing_volumes=wanted,
                        source="dedupe sweep",
                        guid=str(row.get("guid") or ""),
                        torrent_hash=str(row.get("torrent_hash") or ""),
                    )
                    if result.get("skipped"):
                        self.db.update_downloader_state(release_id, "IDLE")
                        summary["cleared"] += 1
                        continue
                    success = bool(result.get("success"))
                else:
                    if qbit is None:
                        qbit = self.get_qbit_client()
                    url = row.get("download_url") or row.get("link") or ""
                    success = bool(qbit.add_torrent(
                        url_or_magnet=url,
                        savepath=downloads_dir,
                        category="manga",
                        tags="mangatrax",
                    ))
                    if success:
                        torrent_h = qbit.extract_torrent_hash(url) if hasattr(qbit, "extract_torrent_hash") else None
                        if not isinstance(torrent_h, str):
                            torrent_h = None
                        self.db.update_download_handle(
                            release_id, "qbit", downloader_id=torrent_h or "",
                            torrent_hash=torrent_h, wanted_volumes=wanted,
                        )
                        try:
                            bundle_volumes = json.loads(row.get("volumes_json") or "[]")
                        except (TypeError, ValueError):
                            bundle_volumes = []
                        if torrent_h and int(row.get("is_bundle") or 0) and len(wanted) < len(bundle_volumes):
                            qbit.apply_selective_volume_download(torrent_h, wanted)
            except Exception as exc:
                logger.warning("Dedupe retry submit failed for '%s': %s", title, exc)
                success = False
            if success:
                summary["submitted"] += 1
                self._log_activity(
                    "AUTO_DOWNLOAD",
                    f"Dedupe retry submitted: '{title}' (Missing: {wanted})",
                    "Source: in-flight dedupe sweep",
                )
            else:
                self.db.update_downloader_state(release_id, "IDLE")
                summary["cleared"] += 1
        if summary["submitted"]:
            logger.info(
                "Pending-dedupe sweep: checked %s, submitted %s, still reserved %s, cleared %s",
                summary["checked"], summary["submitted"], summary["still_reserved"], summary["cleared"],
            )
        return summary


    # --- Torbox poller/fetch threads (task-29) ------------------------------
    @staticmethod
    def _utc_iso() -> str:
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _seconds_between(start_iso: Optional[str], end_iso: Optional[str]) -> float:
        """Seconds between two stored UTC timestamps (0.0 when unparseable)."""
        if not start_iso or not end_iso:
            return 0.0
        try:
            start = datetime.fromisoformat(str(start_iso))
            end = datetime.fromisoformat(str(end_iso))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, (end - start).total_seconds())

    def _ensure_torbox_fetch_executor(self) -> ThreadPoolExecutor:
        if self._torbox_fetch_executor is None:
            try:
                max_workers = int(self.db.get_setting("torbox_max_concurrent", "2") or 2)
            except (TypeError, ValueError):
                max_workers = 2
            self._torbox_fetch_executor = ThreadPoolExecutor(
                max_workers=max(1, max_workers), thread_name_prefix="torbox-fetch",
            )
        return self._torbox_fetch_executor

    def _torbox_progress_callback(self, release_id: int, total_bytes: int):
        """Throttled progress callback: persists at most every 2 s / 1 %."""
        tracker = {
            "files": {}, "lock": threading.Lock(),
            "started": time.monotonic(), "last_save": 0.0, "last_pct": -1.0,
        }

        def _callback(label, written):
            now = time.monotonic()
            try:
                written = int(written or 0)
            except (TypeError, ValueError):
                return
            name = str(label or "")
            with tracker["lock"]:
                tracker["files"][name] = max(tracker["files"].get(name, 0), written)
                downloaded = sum(tracker["files"].values())
            total = int(total_bytes or 0)
            if total:
                downloaded = min(downloaded, total)
            pct = (downloaded / total * 100.0) if total else 0.0
            final = bool(total) and downloaded >= total
            if (not final and now - tracker["last_save"] < 2.0
                    and (not total or pct - tracker["last_pct"] < 1.0)):
                return
            tracker["last_save"] = now
            tracker["last_pct"] = pct
            elapsed = max(0.001, now - tracker["started"])
            speed = int(downloaded / elapsed) if downloaded else 0
            try:
                self.db.update_torbox_progress(
                    release_id,
                    phase="fetching",
                    downloaded_bytes=downloaded,
                    total_bytes=total or None,
                    speed_bps=speed,
                    progress_pct=round(pct, 1),
                )
            except Exception as exc:  # progress must never abort a download
                logger.debug("Could not persist Torbox progress for release %s: %s", release_id, exc)

        return _callback

    def _torbox_fail_contained(self, release_id: int, title: str, message: str, downloader, handle) -> None:
        """Contain one release's failure: cleanup, persist, audit, fallback state."""
        try:
            self._cleanup_torbox_partials(
                os.path.join(self.get_downloads_dir(), self._safe_torrent_dirname(title))
            )
        except Exception:
            pass
        try:
            self._torbox_fail_release(
                release_id, title, message, downloader=downloader, handle=handle,
            )
        except Exception as fail_exc:
            logger.error("Could not persist Torbox failure for release %s: %s", release_id, fail_exc)
            try:
                self.db.set_download_error(release_id, message)
                self.db.update_downloader_state(release_id, "FAILED", "torbox")
            except Exception:
                pass
        self._torbox_notified.pop(release_id, None)

    def _fetch_torbox_release(self, release: Dict[str, Any], downloader, handle: Dict[str, Any], info: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch/complete/route ONE ready release (runs in its own fetch thread)."""
        release_id = int(release.get("id") or 0)
        title = str(release.get("title") or "")
        downloads_dir = self.get_downloads_dir()
        delete_after = as_bool(self.db.get_setting("torbox_delete_after_download", "true"), default=True)
        progress = self.db.get_torbox_progress(release_id)
        ready_at = progress.get("ready_detected_at") or self._utc_iso()
        total_bytes = int(info.get("size") or 0)
        dest_dir = os.path.join(downloads_dir, self._safe_torrent_dirname(title))
        reason = ""
        try:
            files = info.get("files") or []
            wanted = self.resolve_release_missing_volumes(release)
            try:
                plan = self._plan_torbox_selection(
                    release, files, wanted, total_size=total_bytes,
                )
            except DownloaderFileError as exc:
                held = False
                try:
                    held = self._hold_torbox_for_manual_pickup(
                        release_id, title, str(exc), downloader=downloader, handle=handle, info=info,
                    )
                except Exception as hold_exc:
                    logger.warning("Could not hold Torbox release %s for manual pickup: %s", release_id, hold_exc)
                if held:
                    self.db.update_torbox_progress(release_id, phase="manual_pickup")
                    return {"status": "manual_pickup", "error": str(exc)}
                self._torbox_fail_contained(release_id, title, str(exc), downloader, handle)
                self.db.update_torbox_progress(release_id, phase="failed")
                return {"status": "failed", "error": str(exc)}
            if plan["action"] == "skip":
                try:
                    downloader.cancel(handle)
                except Exception as exc:
                    logger.debug("Could not cancel skipped Torbox job: %s", exc)
                self.db.update_downloader_state(release_id, "IDLE", "torbox")
                self.db.update_torbox_progress(release_id, phase="done")
                self._log_activity(
                    "SELECTIVE_DOWNLOAD",
                    f"Torbox skipped '{title}': {plan['reason']}.",
                    json.dumps({"wanted_volumes": [], "torrent_id": handle.get("torrent_id")}),
                )
                return {"status": "skipped"}
            selection = plan["files"] if plan["action"] == "files" else None
            reason = plan["reason"]
            if plan.get("strict"):
                logger.info(
                    "Torbox selection for '%s' (torrent %s): wanted %s volume(s), selected %s/%s file(s), "
                    "skipped %s volume(s).",
                    title, handle.get("torrent_id"), len(plan.get("wanted_volumes") or []),
                    plan.get("selected_count"), plan.get("total_files"),
                    len(plan.get("skipped_volumes") or []),
                )
                logger.debug(
                    "Torbox file->volume mapping for '%s': %s",
                    title, json.dumps(plan.get("mapping") or []),
                )
                self._log_activity(
                    "SELECTIVE_DOWNLOAD",
                    f"Torbox selective download for '{title}': {reason}.",
                    json.dumps({
                        "wanted_count": len(plan.get("wanted_volumes") or []),
                        "selected_count": plan.get("selected_count"),
                        "skipped_count": len(plan.get("skipped_volumes") or []),
                        "torrent_id": handle.get("torrent_id"),
                    }),
                )
            if selection:
                try:
                    selected_total = sum(int((item or {}).get("size") or 0) for item in selection)
                except (TypeError, ValueError):
                    selected_total = 0
                if selected_total:
                    total_bytes = selected_total
            fetch_started_mono = time.monotonic()
            fetch_started_iso = self._utc_iso()
            self.db.update_torbox_progress(
                release_id, phase="fetching", ready_detected_at=ready_at,
                fetch_started_at=fetch_started_iso, total_bytes=total_bytes or None,
                downloaded_bytes=0, speed_bps=0, progress_pct=0.0,
            )
            fetch_handle = dict(handle)
            fetch_handle["size"] = int(total_bytes or 0)
            try:
                saved = downloader.fetch_files(
                    fetch_handle, selection, dest_dir,
                    progress_cb=self._torbox_progress_callback(release_id, total_bytes),
                )
            except (DownloaderFileError, DownloaderError) as exc:
                self._torbox_fail_contained(
                    release_id, title, f"Fetch failed ({reason}): {exc}", downloader, handle,
                )
                self.db.update_torbox_progress(release_id, phase="failed")
                return {"status": "failed", "error": str(exc)}
            fetch_finished_mono = time.monotonic()
            fetch_finished_iso = self._utc_iso()
            fetch_seconds = max(0.001, fetch_finished_mono - fetch_started_mono)
            try:
                downloaded_bytes = sum(int(item.get("size") or 0) for item in (saved or []))
            except (TypeError, ValueError):
                downloaded_bytes = 0
            if not downloaded_bytes:
                downloaded_bytes = total_bytes
            total_bytes = total_bytes or downloaded_bytes
            speed_bps = int(downloaded_bytes / fetch_seconds) if downloaded_bytes else 0
            try:
                self.db.set_download_files(
                    release_id, [str(item.get("path") or "") for item in (saved or [])],
                )
            except Exception as exc:
                logger.debug("Could not persist download files for release %s: %s", release_id, exc)
            self.db.update_torbox_progress(
                release_id, phase="routing", downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes or None, speed_bps=speed_bps,
                fetch_finished_at=fetch_finished_iso, progress_pct=100.0,
            )
            self.db.clear_download_error(release_id)
            self.db.update_downloader_state(release_id, "COMPLETED", "torbox")
            try:
                rel_row = self.db.get_release_by_id(release_id)
                if rel_row:
                    self.get_notifier().notify_acquired(rel_row)
            except Exception as exc:
                logger.debug("Webhook notification failed for acquired release: %s", exc)
            routing_started_mono = time.monotonic()
            routing_error = ""
            try:
                self.process_downloads_to_staging(actor="scheduler")
            except Exception as exc:
                routing_error = str(exc)
                logger.debug("Downloads routing after Torbox completion failed: %s", exc)
            routed_mono = time.monotonic()
            routed_iso = self._utc_iso()
            self.db.update_torbox_progress(release_id, phase="done", routed_at=routed_iso)
            try:
                saved_paths = [str(item.get("path") or "") for item in (saved or [])]
                if saved_paths and all(not os.path.exists(path) for path in saved_paths if path):
                    self.db.mark_release_routed(release_id)
            except Exception as exc:
                logger.debug("Could not mark release %s routed: %s", release_id, exc)
            try:
                self.refresh_downloads_inventory()
            except Exception as exc:
                logger.debug("Inventory refresh after download failed: %s", exc)
            try:
                connections = max([int(item.get("connections") or 1) for item in (saved or [])] or [1])
            except (TypeError, ValueError):
                connections = 1
            per_file = [
                {
                    "name": item.get("name"), "bytes": int(item.get("size") or 0),
                    "seconds": round(float(item.get("seconds") or 0.0), 1),
                    "connections": int(item.get("connections") or 1),
                }
                for item in (saved or [])
            ]
            logger.info(
                "Torbox fetch aggregate for '%s': %s file(s), %.2f GiB in %.1fs = %.1f MiB/s (%s conns max)",
                title, len(saved or []), downloaded_bytes / (1024 ** 3),
                fetch_seconds, speed_bps / (1024 ** 2), connections,
            )
            latency = {
                "ready_detected_at": ready_at,
                "fetch_started_at": fetch_started_iso,
                "fetch_finished_at": fetch_finished_iso,
                "routed_at": routed_iso,
                "ready_to_fetch_seconds": round(self._seconds_between(ready_at, fetch_started_iso), 1),
                "fetch_seconds": round(fetch_seconds, 1),
                "route_seconds": round(max(0.0, routed_mono - routing_started_mono), 1),
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
                "speed_bps": speed_bps,
                "connections": connections,
                "per_file": per_file,
            }
            logger.info(
                "Torbox latency for '%s': ready→fetch %.1fs, fetch %.1fs (%.2f GiB at %.1f MiB/s), route %.1fs",
                title,
                latency["ready_to_fetch_seconds"],
                latency["fetch_seconds"],
                downloaded_bytes / (1024 ** 3),
                speed_bps / (1024 ** 2),
                latency["route_seconds"],
            )
            detail = (
                f"Saved {len(saved or [])} file(s) to {dest_dir} ({reason}); "
                f"latency: ready→fetch {latency['ready_to_fetch_seconds']:.1f}s, "
                f"fetch {latency['fetch_seconds']:.1f}s "
                f"({downloaded_bytes / (1024 ** 3):.2f} GiB at {speed_bps / (1024 ** 2):.1f} MiB/s), "
                f"route {latency['route_seconds']:.1f}s"
            )
            if routing_error:
                detail += f"; routing error: {routing_error}"
            deleted = ""
            if delete_after:
                deleted = " Torbox torrent deleted." if downloader.cancel(handle) else " Torbox delete failed."
            self._log_activity(
                "TORBOX_DOWNLOADED", f"Torbox download complete: '{title}'",
                json.dumps({
                    "message": detail + deleted,
                    "latency": latency,
                    "files": len(saved or []),
                }),
            )
            return {"status": "completed", "latency": latency}
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Torbox fetch failed for release %s (%s): %s",
                release_id, title, message, exc_info=True,
            )
            self._torbox_fail_contained(release_id, title, message, downloader, handle)
            try:
                self.db.update_torbox_progress(release_id, phase="failed")
            except Exception:
                pass
            return {"status": "failed", "error": message}

    def _submit_torbox_fetch(self, release: Dict[str, Any], downloader, handle: Dict[str, Any], info: Dict[str, Any]) -> bool:
        """Schedule one release on the bounded fetch executor (deduplicated)."""
        release_id = int(release.get("id") or 0)
        if not release_id:
            return False
        with self._torbox_fetch_lock:
            if release_id in self._torbox_fetching:
                return False
            lock = self._torbox_fetch_locks.setdefault(release_id, threading.Lock())
            if not lock.acquire(blocking=False):
                return False
            self._torbox_fetching.add(release_id)
        try:
            executor = self._ensure_torbox_fetch_executor()
            future = executor.submit(self._torbox_fetch_thread, release, downloader, handle, info, lock)
        except Exception:
            with self._torbox_fetch_lock:
                self._torbox_fetching.discard(release_id)
            try:
                lock.release()
            except RuntimeError:
                pass
            raise
        with self._torbox_fetch_lock:
            self._torbox_fetch_futures[release_id] = future
        return True

    def _torbox_fetch_thread(self, release: Dict[str, Any], downloader, handle: Dict[str, Any], info: Dict[str, Any], lock) -> None:
        release_id = int(release.get("id") or 0)
        try:
            self._fetch_torbox_release(release, downloader, handle, info)
        except Exception as exc:  # _fetch_torbox_release contains its own failures
            logger.warning("Torbox fetch thread crashed for release %s: %s", release_id, exc, exc_info=True)
            self._torbox_fail_contained(
                release_id, str(release.get("title") or ""), f"{type(exc).__name__}: {exc}", downloader, handle,
            )
        finally:
            with self._torbox_fetch_lock:
                self._torbox_fetching.discard(release_id)
                self._torbox_fetch_futures.pop(release_id, None)
            try:
                lock.release()
            except RuntimeError:
                pass

    def _torbox_next_poll_interval(self) -> float:
        """Adaptive poll cadence: short while ready/fetching/routing, backed off when idle."""
        try:
            floor = max(5.0, float(self.db.get_setting("torbox_poll_seconds", "5") or 5))
        except (TypeError, ValueError):
            floor = 5.0
        busy = bool(self._torbox_fetching)
        if not busy:
            try:
                rows = self.db.get_active_torbox_downloads()
            except Exception:
                rows = []
            for row in rows:
                try:
                    data = json.loads(row.get("torbox_progress_json") or "{}")
                except (TypeError, ValueError):
                    data = {}
                if isinstance(data, dict) and data.get("phase") in ("ready", "fetching", "routing"):
                    busy = True
                    break
        return floor if busy else min(30.0, max(15.0, floor * 3))

    def _torbox_poller_loop(self) -> None:
        logger.info("Torbox poller thread started.")
        while self.running:
            interval = self._torbox_next_poll_interval()
            self._torbox_poll_wakeup.wait(interval)
            self._torbox_poll_wakeup.clear()
            if not self.running:
                break
            try:
                self.poll_torbox_downloads()
            except Exception as exc:
                logger.warning("Torbox poller pass failed: %s", exc, exc_info=True)
        logger.info("Torbox poller thread stopped.")

    def request_torbox_poll(self) -> None:
        """Wake the Torbox poller immediately (e.g. right after a submit)."""
        try:
            self._torbox_poll_wakeup.set()
        except Exception:
            pass

    def torbox_poll_now(self) -> Dict[str, Any]:
        """Check-now action: run one poll pass and return fresh counters."""
        self.request_torbox_poll()
        result = self.poll_torbox_downloads()
        try:
            result["last_checked_at"] = self.db.get_setting("torbox_last_poll_at", "")
        except Exception:
            result["last_checked_at"] = ""
        result["fetching"] = len(self._torbox_fetching)
        return result

    def wait_for_torbox_fetches(self, timeout: float = 10.0) -> None:
        """Test/API helper: wait for in-flight fetch futures to finish."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            with self._torbox_fetch_lock:
                futures = list(self._torbox_fetch_futures.values())
            if not futures:
                return
            for future in futures:
                try:
                    future.result(timeout=max(0.01, deadline - time.monotonic()))
                except Exception:
                    pass
            if not futures:
                return
            time.sleep(0.01)

    def poll_torbox_downloads(self, async_fetch: Optional[bool] = None) -> Dict[str, Any]:
        """Advance Torbox releases: poll → fetch when ready → complete → route.

        ``async_fetch`` defaults to True when the dedicated poller thread is running
        (real runs): ready releases are handed to the bounded fetch executor so a slow
        download never blocks polling/Calibre/feeds.  Direct callers/tests without the
        poller thread keep the historical synchronous behaviour.
        Bounded by ``torbox_max_concurrent`` at submission time and by
        ``torbox_timeout_hours`` here; stalled/no-seed torrents are surfaced as
        activity events instead of being retried silently forever.
        """
        if async_fetch is None:
            async_fetch = bool(self._torbox_poll_thread and self._torbox_poll_thread.is_alive())
        try:
            self.db.set_setting("torbox_last_poll_at", str(time.time()))
        except Exception:
            pass
        downloader = build_downloader(self.db, engine="torbox")
        if not getattr(downloader, "enabled", False):
            return {"status": "disabled", "processed": 0, "completed": 0, "failed": 0, "queued_fetch": 0}

        try:
            timeout_hours = float(self.db.get_setting("torbox_timeout_hours", "6") or 6)
        except ValueError:
            timeout_hours = 6.0
        rows = self.db.get_active_torbox_downloads()
        if not rows:
            return {"status": "idle", "processed": 0, "completed": 0, "failed": 0, "queued_fetch": 0}

        downloads_dir = self.get_downloads_dir()
        delete_after = as_bool(self.db.get_setting("torbox_delete_after_download", "true"), default=True)

        processed = completed = failed = queued_fetch = 0
        rate_limited = False

        for release in rows:
            # A single bad release must never abort the poll cycle or leave the row in
            # DOWNLOADING: contain it, persist the reason, clean partials and continue.
            release_id = 0
            title = str(release.get("title") or "")
            handle: Dict[str, Any] = {}
            try:
                release_id = int(release.get("id"))
                title = str(release.get("title") or "")
                queued_id = parse_queued_handle_id(release.get("downloader_id"))
                started = self._parse_db_timestamp(release.get("downloader_started_at"))
                if started is not None and timeout_hours > 0:
                    age_hours = (datetime.utcnow() - started).total_seconds() / 3600.0
                    if age_hours > timeout_hours:
                        if queued_id is not None:
                            message = (
                                f"No free Torbox active slot for {age_hours:.1f}h "
                                f"(limit {timeout_hours:g}h); the release stays REQUESTED for a retry."
                            )
                        else:
                            message = (
                                f"Timed out after {age_hours:.1f}h (limit {timeout_hours:g}h); "
                                "the release stays REQUESTED for a retry."
                            )
                        self._torbox_fail_release(
                            release_id, title, message, downloader=downloader,
                        )
                        failed += 1
                        continue

                if queued_id is not None:
                    handle = {
                        "engine": "torbox",
                        "queued_id": queued_id,
                        "torrent_id": None,
                        "hash": str(release.get("torrent_hash") or ""),
                    }
                else:
                    try:
                        torrent_id = int(release.get("downloader_id") or 0)
                    except (TypeError, ValueError):
                        torrent_id = 0
                    handle = {
                        "torrent_id": torrent_id,
                        "hash": str(release.get("torrent_hash") or ""),
                    }
                    if not torrent_id:
                        self._torbox_fail_release(release_id, title, "Missing Torbox torrent_id/queued_id on the release row.")
                        failed += 1
                        continue

                try:
                    info = downloader.status(handle)
                except DownloaderRateLimitError as exc:
                    self._torbox_surface_once(release_id, str(exc), title)
                    rate_limited = True
                    break
                except (DownloaderAuthError, DownloaderNotFoundError) as exc:
                    self._torbox_fail_release(release_id, title, str(exc))
                    failed += 1
                    continue
                except DownloaderError as exc:
                    self._torbox_surface_once(release_id, str(exc), title)
                    continue

                processed += 1
                state = str(info.get("state") or "queued")

                # Queued submission just activated: capture the real torrent id and keep
                # going with the normal flow in this same poll cycle.
                promoted = info.get("promoted_torrent_id")
                if promoted:
                    self.db.set_downloader_id(release_id, str(promoted))
                    self._torbox_surface_once(
                        release_id,
                        f"Activated from queued_id={handle.get('queued_id')} as Torbox torrent {promoted}.",
                        title,
                        event_type="TORBOX_PROMOTED",
                    )
                    handle = {
                        "engine": "torbox",
                        "torrent_id": int(promoted),
                        "queued_id": handle.get("queued_id"),
                        "hash": handle.get("hash") or "",
                    }
                    self._torbox_notified.pop(release_id, None)

                if state == "queued_slot":
                    message = "queued: waiting for a free Torbox slot"
                    position = info.get("queue_position")
                    active_limit = info.get("active_limit")
                    if position is not None:
                        message += f" (position {position})"
                    if active_limit is not None:
                        message += f" [active limit {active_limit}]"
                    self._torbox_surface_once(
                        release_id, message, title, event_type="TORBOX_QUEUED",
                    )
                    continue

                if state == "failed":
                    self._torbox_fail_release(
                        release_id, title,
                        f"Torbox reported state '{info.get('raw_state') or 'failed'}'.",
                        downloader=downloader, handle=handle,
                    )
                    failed += 1
                    continue
                if state == "stalled":
                    self._torbox_surface_once(
                        release_id,
                        f"Torbox reports '{info.get('raw_state') or 'stalled'}' (no seeds/progress); "
                        "it is not retried automatically.",
                        title,
                    )
                    continue
                if state == "processing":
                    # Torrent finished but Torbox has not published the files yet:
                    # poll/keep waiting (timeout still applies) but never requestdl.
                    try:
                        self.db.update_torbox_progress(release_id, phase="processing")
                    except Exception as exc:
                        logger.debug("Could not persist processing phase: %s", exc)
                    self._torbox_surface_once(
                        release_id,
                        "Torbox is still finalizing the download; files are not ready yet "
                        "(waiting before fetching).",
                        title,
                        event_type="TORBOX_PROCESSING",
                    )
                    continue
                if state != "ready":
                    continue

                files = info.get("files") or []
                wanted = self.resolve_release_missing_volumes(release)
                progress = self.db.get_torbox_progress(release_id)
                if not progress.get("ready_detected_at"):
                    self.db.update_torbox_progress(
                        release_id, phase="ready", ready_detected_at=self._utc_iso(),
                        total_bytes=int(info.get("size") or 0), progress_pct=0.0,
                    )
                if async_fetch:
                    if self._submit_torbox_fetch(release, downloader, handle, info):
                        queued_fetch += 1
                    continue
                outcome = self._fetch_torbox_release(release, downloader, handle, info)
                if outcome.get("status") == "completed":
                    completed += 1
                elif outcome.get("status") == "failed":
                    failed += 1
                continue

            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Torbox poll failed for release %s (%s): %s",
                    release_id, title, message, exc_info=True,
                )
                try:
                    self._cleanup_torbox_partials(
                        os.path.join(downloads_dir, self._safe_torrent_dirname(title))
                    )
                except Exception:
                    pass
                try:
                    self._torbox_fail_release(
                        release_id, title, message,
                        downloader=downloader or None, handle=handle or None,
                    )
                except Exception as fail_exc:
                    logger.error(
                        "Could not persist Torbox failure for release %s: %s", release_id, fail_exc,
                    )
                    try:
                        self.db.set_download_error(release_id, message)
                        self.db.update_downloader_state(release_id, "FAILED", "torbox")
                    except Exception:
                        pass
                failed += 1
                self._torbox_notified.pop(release_id, None)
                continue

        return {
            "status": "rate_limited" if rate_limited else "ok",
            "processed": processed,
            "completed": completed,
            "failed": failed,
            "queued_fetch": queued_fetch,
            "fetching": len(self._torbox_fetching),
        }


    # --- Calibre Sync Task ---
    def sync_calibre_library(self, progress=None) -> Dict[str, Any]:
        """
        Scans the Calibre metadata.db in zero-lock read-only mode, updates series tracking,
        and auto-resolves any requests for volumes that have now been added to Calibre.
        """
        if not self._calibre_sync_lock.acquire(blocking=False):
            logger.info("Calibre sync is already in progress in another thread; skipping concurrent execution.")
            return {
                "success": True,
                "message": "Calibre sync already in progress",
                "synced_series": 0,
                "resolved_requests": 0
            }

        try:
            self.last_calibre_sync = time.time()
            calibre_path = self.db.get_setting("calibre_path", "/calibre")
            scanner = CalibreMangaScanner(calibre_path)
            if not scanner.is_available():
                msg = f"Calibre library not accessible at: {calibre_path}"
                logger.warning(msg)
                return {"success": False, "message": msg, "synced_series": 0, "resolved_requests": 0}

            try:
                if progress:
                    progress(0, 0, "Scanning Calibre library…")
                series_list = scanner.get_all_series()
                volumes_map = scanner.get_series_volumes_map()
                if progress:
                    progress(0, len(series_list), f"Updating {len(series_list)} tracked series…")
                synced_count = self.db.sync_calibre_series(series_list, volumes_map=volumes_map)
                if progress:
                    progress(len(series_list), len(series_list), f"Synced {synced_count} Calibre series")

                # Build matcher
                meta_cache = self.db.get_all_manga_metadata_cache()
                matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache, staged_volumes_map=self._staged_volumes_map())

                # Check pending requests against Calibre
                pending = self.db.get_pending_requests()
                resolved = matcher.resolve_pending_requests(pending)

                notifier = self.get_notifier()
                for item in resolved:
                    # Automated write: the precedence model decides (promotion to
                    # IN_CALIBRE/IN_STAGING is allowed, a downgrade is not).
                    target_status = "IN_CALIBRE" if item.get("status") == "IN_CALIBRE" else "IN_STAGING"
                    self.db.update_release_status(item["release_id"], target_status, enforce_precedence=True)
                    self._log_activity(
                        "REQUEST_RESOLVED",
                        f"Resolved: '{item['title']}' is now available in your Calibre library.",
                        item["reason"]
                    )
                    if target_status == "IN_CALIBRE":
                        try:
                            rel_row = self.db.get_release_by_id(item["release_id"])
                            if rel_row:
                                notifier.notify_acquired(rel_row)
                        except Exception as e:
                            logger.debug(f"Webhook notification failed for acquired release: {e}")

                # Reconcile active releases against Calibre: updates missing_volumes
                # and statuses.  Incremental + bounded (see the method) so a large
                # library cannot stall the worker.
                self._reconcile_releases_with_calibre(matcher)

                # Auto-expand any unexpanded complete releases against freshly synced Calibre library
                try:
                    self.expand_unresolved_complete_releases(matcher=matcher)
                except Exception as e:
                    logger.debug(f"Complete releases expansion error during Calibre sync: {e}")

                if progress:
                    progress(len(series_list), len(series_list), f"Resolved {len(resolved)} requests; reconciling releases")
                summary = f"Synced {synced_count} manga series from Calibre; auto-resolved {len(resolved)} acquired requests."
                logger.info(summary)
                self._log_activity("CALIBRE_SYNC", summary)
                # A newly registered series immediately unlocks its waiting downloads.
                try:
                    self.process_downloads_to_staging(actor="scheduler")
                except Exception as exc:
                    logger.debug("Downloads routing after Calibre sync failed: %s", exc)
                return {
                    "success": True,
                    "message": summary,
                    "synced_series": synced_count,
                    "resolved_requests": len(resolved)
                }
            except Exception as e:
                err = f"Error syncing Calibre library: {e}"
                logger.error(err)
                self._log_activity("CALIBRE_SYNC_ERROR", err)
                return {"success": False, "message": err, "synced_series": 0, "resolved_requests": 0}
        finally:
            self._calibre_sync_lock.release()

    # --- Startup work (runs in the worker thread, never before the app serves) ---
    def perform_initial_sync(self) -> Dict[str, Any]:
        """First worker action on start: legacy key repair + initial Calibre sync.

        The web server used to run this inside ``lifespan`` *before* ``yield``, which
        made the UI unavailable for the whole duration (~2 min on a real library).
        The lifespan now only starts the worker; this method runs on the worker
        thread and is called from an exception-shielded block so a failing sync can
        never kill the worker loop.
        """
        results: Dict[str, Any] = {"repair": 0, "sync": None}
        try:
            results["repair"] = self.repair_series_matching_keys(actor="scheduler")
        except Exception as exc:
            logger.error("Initial series key repair failed (worker continues): %s", exc)
        try:
            calibre_path = self.db.get_setting("calibre_path", "/calibre")
            scanner = CalibreMangaScanner(calibre_path)
            if scanner.is_available():
                logger.info("Found Calibre library metadata.db on startup! Running initial manga scan...")
                results["sync"] = self.sync_calibre_library()
            else:
                logger.info(
                    "Calibre library metadata.db not yet found at %s. Will re-check periodically.",
                    scanner.db_path,
                )
        except Exception as exc:
            logger.error("Initial Calibre sync failed (worker continues): %s", exc)
        finally:
            self._initial_sync_done = True
        return results

    def get_calibre_sync_status(self) -> Dict[str, Any]:
        """Cheap "is a sync running?" state for the UI (never blocks)."""
        return {
            "in_progress": self._calibre_sync_lock.locked(),
            "initial_sync_done": bool(self._initial_sync_done),
            "last_sync": self.last_calibre_sync or None,
        }

    @staticmethod
    def _reconcile_release_is_stable(release: Dict[str, Any]) -> bool:
        """True when re-checking this release against Calibre cannot change anything.

        Already-owned rows with a full explicit volume set and no missing volumes are
        stable: the only event that can change them is a new Calibre sync (which is
        this pass), and their stored volume set is unchanged.
        """
        if release.get("status") != "IN_CALIBRE":
            return False
        if release.get("missing_volumes"):
            return False
        if not release.get("volumes"):
            return False
        if release.get("is_inferred"):
            # Inferred full sets may still be refined by a concrete resolution.
            return False
        return True

    def _reconcile_releases_with_calibre(self, matcher: MangaCalibreMatcher) -> int:
        """Incremental, bounded reconcile of active releases against Calibre.

        Skips stable ``IN_CALIBRE`` rows and processes at most
        ``reconcile_batch_size`` candidates per pass, rotating the window so the
        remaining work continues on the next tick.  Returns the number of rows whose
        status/volume state actually changed.
        """
        try:
            all_active = self.db.get_releases(limit=5000)
        except Exception as exc:
            logger.error("Calibre reconcile: could not load releases: %s", exc)
            return 0
        candidates = [
            release for release in all_active
            if release.get("status") in ("REQUESTED", "DISCOVERY", "IN_CALIBRE", "NEW")
            and not self._reconcile_release_is_stable(release)
        ]
        if not candidates:
            self._reconcile_cursor = 0
            return 0

        batch_size = max(1, int(self.reconcile_batch_size))
        if len(candidates) <= batch_size:
            window = candidates
            self._reconcile_cursor = 0
        else:
            start = self._reconcile_cursor % len(candidates)
            window = candidates[start:start + batch_size]
            if len(window) < batch_size:
                window = window + candidates[:batch_size - len(window)]
            self._reconcile_cursor = (start + len(window)) % len(candidates)
            logger.info(
                "Calibre reconcile pass: %d of %d active release(s); the rest continue on the next tick.",
                len(window), len(candidates),
            )

        changed = 0
        for release in window:
            result = matcher.check_release(release, auto_request_monitored=True)
            new_missing = result.get("missing_volumes", [])
            new_status = result.get("status")
            old_missing = release.get("missing_volumes", [])
            old_status = release.get("status")
            if new_missing != old_missing or new_status != old_status:
                self.db.update_release_volumes_and_status(
                    release["id"],
                    release.get("volumes", []),
                    new_missing,
                    new_status,
                )
                changed += 1
        return changed

    # --- Feed Check Task ---
    @staticmethod
    def _feed_check_payload(
        success: bool,
        message: str,
        releases_found: int = 0,
        requested: int = 0,
        sent_to_downloader: int = 0,
        feeds_failed: int = 0,
        errors: Optional[List[Dict[str, str]]] = None,
        skipped: bool = False,
        new_releases: int = 0,
        transitions: int = 0,
    ) -> Dict[str, Any]:
        """Uniform feed-poll result (counts + per-feed failure detail)."""
        return {
            "success": success,
            "message": message,
            "releases_found": releases_found,
            "new_releases": new_releases,
            "transitions": transitions,
            "requested": requested,
            "sent_to_downloader": sent_to_downloader,
            "feeds_failed": feeds_failed,
            "errors": list(errors or []),
            "skipped": skipped,
        }

    def check_all_feeds(self, trigger: str = "manual") -> Dict[str, Any]:
        """Poll all enabled feeds (BLOCKING; call from a worker thread, never the event loop).

        A concurrent call (manual "Poll Feeds" while the interval poll runs, or a
        second click) is skipped with an explicit ``skipped`` flag instead of running
        twice or raising.
        """
        if not self._feed_check_lock.acquire(blocking=False):
            message = "Feed check already in progress"
            logger.info(message)
            return self._feed_check_payload(True, message, skipped=True)
        try:
            result = self._check_all_feeds_locked()
            if trigger == "auto":
                self._notify_feed_scan_result(result)
            return result
        finally:
            self._feed_check_lock.release()

    # --- Async feed poll (UI availability) ---
    def start_feed_check(self) -> Dict[str, Any]:
        """Start a background feed poll and return immediately.

        The web handler used to await ``check_all_feeds()`` inline, which blocked the
        uvicorn event loop while the providers fetched (the whole UI froze, and a
        Nyaa failure looked like a silent success).  The poll now runs on a daemon
        thread; the UI polls :meth:`get_feed_check_status` for the result.
        """
        with self._feed_check_state_lock:
            if self._feed_check_state["running"]:
                return {"success": True, "started": False, "message": "Feed check already in progress"}
            self._feed_check_state.update(
                running=True, started_at=time.time(), finished_at=None, result=None,
            )
        thread = threading.Thread(
            target=self._feed_check_thread, daemon=True, name="MangaTraxFeedCheck",
        )
        thread.start()
        logger.info("Background feed check started.")
        return {"success": True, "started": True, "message": "Feed check started in the background."}

    def _feed_check_thread(self):
        """Run the blocking poll, shield crashes, and publish the result for the UI."""
        result: Dict[str, Any]
        try:
            # Manual UI polls stay silent (notifications are for automatic scans only).
            result = self.check_all_feeds(trigger="manual")
        except Exception as exc:  # pragma: no cover - defensive outer shield
            logger.error("Background feed check crashed: %s", exc)
            self._log_activity("FEED_ERROR", f"Background feed check crashed: {exc}")
            result = self._feed_check_payload(False, f"Feed check crashed: {exc}")
        finally:
            with self._feed_check_state_lock:
                self._feed_check_state.update(
                    running=False, finished_at=time.time(), result=result,
                )

    def get_feed_check_status(self) -> Dict[str, Any]:
        """Non-blocking feed-poll state + last result (for the Settings/topbar UI)."""
        with self._feed_check_state_lock:
            result = self._feed_check_state["result"]
            return {
                "running": bool(self._feed_check_state["running"]),
                "started_at": self._feed_check_state["started_at"],
                "finished_at": self._feed_check_state["finished_at"],
                "result": dict(result) if result else None,
            }

    def _check_all_feeds_locked(self) -> Dict[str, Any]:
        """
        Polls all enabled feeds, parses manga bundles/volumes, matches against Calibre,
        caches cover images, and auto-sends downloads to qBittorrent if enabled.
        """
        feeds = self.db.get_all_feeds()
        total_found = 0
        total_new = 0
        total_transitions = 0
        total_requested = 0
        total_sent_to_downloader = 0
        feeds_failed = 0
        feed_errors: List[Dict[str, str]] = []

        # Load Calibre context for matching
        calibre_path = self.db.get_setting("calibre_path", "/calibre")
        scanner = CalibreMangaScanner(calibre_path)
        series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
        volumes_map = scanner.get_series_volumes_map() if scanner.is_available() else {}
        meta_cache = self.db.get_all_manga_metadata_cache()
        matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache, staged_volumes_map=self._staged_volumes_map())

        # Marker-less chapter guard + numbered-series context: loaded once per
        # feed-poll pass.  The guard discards a chapter only when its clean series
        # name is one we already know (Calibre library or tracked row); the same
        # context keeps a real numbered series name ("Mob Psycho 100") as the
        # matching key when a chapter release is retained.
        discard_unmarked_chapters = self._discard_unmarked_chapters_enabled()
        known_series_keys = self._known_series_keys(matcher)

        global_auto_download = self.db.get_setting("auto_download", "true").lower() == "true"
        downloads_dir = self.get_downloads_dir()
        qbit = self.get_qbit_client()
        downloader_engine = self.get_downloader_engine()

        for feed in feeds:
            if not feed.get("enabled"):
                continue

            feed_id = feed["id"]
            feed_name = feed["name"]
            try:
                provider = create_provider(feed, nyaa_base_url=self._nyaa_base_url())
            except Exception as exc:
                # A broken feed configuration must fail that feed, not crash the poll.
                feeds_failed += 1
                feed_errors.append({"feed": feed_name, "error": f"Provider configuration error: {exc}"})
                logger.error(f"Error creating provider for feed '{feed_name}': {exc}")
                self.db.update_feed_status(feed_id, "ERROR", str(exc))
                self._log_activity("FEED_ERROR", f"Feed error in '{feed_name}': {exc}")
                continue
            if not provider:
                feeds_failed += 1
                feed_errors.append({"feed": feed_name, "error": "Unsupported feed provider"})
                self.db.update_feed_status(feed_id, "ERROR", "Unsupported feed provider")
                continue

            try:
                releases = provider.fetch_releases()
                feed_requested = 0

                for rel in releases:
                    # JPEG XL images are in the user's throw-away filter: never ingested
                    # or downloaded; the log line marks the release as [JPEGXL].
                    if is_jpegxl_release(rel.title):
                        self._log_jpegxl_discard(rel)
                        continue
                    # Discard pure chapter updates and light novels.  Explicit chapter
                    # markers ("Ch1-50", "Chapter 180", "#12") are always discarded
                    # (existing policy); chapter RANGES of a known series are audited
                    # like the context-guarded marker-less path, single chapters keep
                    # their previous silent path.
                    if is_chapter_only(rel.title):
                        from app.parser import _parse_bare_chapter_number, extract_manga_bundle_and_volumes
                        bare_chapter = None
                        chapter_series = ""
                        try:
                            bare_chapter = _parse_bare_chapter_number(rel.title)
                            parsed_chapter = extract_manga_bundle_and_volumes(rel.title)
                            chapter_series = (parsed_chapter.get("series_name") or "").strip()
                        except Exception:
                            pass
                        numbered_known = (
                            bare_chapter is not None and (
                                normalize_text(rel.title) in known_series_keys
                                or (chapter_series and normalize_text(chapter_series) in known_series_keys
                                    and normalize_text(rel.title) == normalize_text(chapter_series))
                            )
                        )
                        if bare_chapter is not None and normalize_text(rel.title) in known_series_keys:
                            numbered_known = True
                        if not numbered_known and bare_chapter is not None and not discard_unmarked_chapters:
                            # Respect the existing opt-out for marker-less chapters; the
                            # explicit Ch./Chapter markers below remain always-discarded.
                            numbered_known = True
                        if not numbered_known:
                            range_reason = self._explicit_chapter_range_reason(rel, known_series_keys)
                            if bare_chapter is not None:
                                series_known = chapter_series and normalize_text(chapter_series) in known_series_keys
                                reason = (
                                    f"bare 3-digit chapter number {bare_chapter['number']:g} of "
                                    f"{'known ' if series_known else ''}series '{chapter_series}'"
                                )
                                self._log_activity(
                                    "CHAPTER_DISCARDED",
                                    f"Discarded chapter release: '{rel.title}'",
                                    reason,
                                )
                            elif range_reason:
                                self._log_activity(
                                    "CHAPTER_DISCARDED",
                                    f"Discarded chapter range release: '{rel.title}'",
                                    range_reason,
                                )
                            continue
                        # A real numbered series name ("Mob Psycho 100") is not a chapter;
                        # fall through to normal ingestion/grouping.
                    if is_light_novel(rel.title):
                        continue
                    # Marker-less chapters ("Chainsaw Man 180"): discarded only when the
                    # title without its trailing number names a series we already know,
                    # so titles like "Mob Psycho 100" keep their number as part of the name.
                    if discard_unmarked_chapters and self._discard_unmarked_chapter(rel, known_series_keys):
                        continue

                    total_found += 1

                    rel_group = getattr(rel, "release_group", None) or rel.to_dict().get("release_group")

                    # Resolve the STABLE matching key (Calibre name / typed name) and
                    # the cosmetic AniList display title.  The AniList title is never
                    # persisted as the series key.  Retained chapter releases resolve
                    # under their clean series name (or their numbered full title when
                    # that is itself a known series).
                    identity = self.resolve_series_identity(
                        self._resolve_release_series_input(rel, known_series_keys),
                        matcher=matcher,
                    )
                    series_key = identity["stable_name"]
                    meta = identity.get("meta") or {}
                    cover_url = rel.cover_url or meta.get("cover_url") or ""
                    alt_names = identity["alt_names"]

                    # Collected batch (year-range title, no volume markers): infer the
                    # full volume set from authoritative metadata only (never a
                    # torrent/payload) BEFORE matching, so the missing-volume
                    # reconciliation below requests only what is actually missing.
                    self._apply_collected_release_inference(rel, series_key, meta, alt_names)

                    # SSRF safe sync cover caching
                    if cover_url:
                        self.cover_cache.cache_sync(cover_url)

                    # Check blacklist against the stable key
                    test_name = series_key or rel.series_name
                    if test_name and self.db.is_series_blacklisted(test_name):
                        status = "BLACKLISTED"
                        missing_vols = []
                    else:
                        match_payload = rel.to_dict()
                        match_payload["series_name"] = series_key
                        match_result = matcher.check_release(match_payload, alt_series_names=alt_names)
                        status = match_result["status"]
                        missing_vols = match_result.get("missing_volumes", [])

                    desc = ""
                    mu_url = ""
                    if status == "DISCOVERY" and series_key:
                        try:
                            mu_info = self.mangaupdates.get_series_info(series_key)
                            if mu_info:
                                desc = mu_info.get("description", "")
                                mu_url = mu_info.get("url", "")
                                if not cover_url and mu_info.get("cover_url"):
                                    cover_url = mu_info["cover_url"]
                                    self.cover_cache.cache_sync(cover_url)
                        except Exception as e:
                            logger.debug(f"Failed to fetch MangaUpdates info for '{series_key}': {e}")

                    rel_dict = {
                        "guid": rel.guid,
                        "title": rel.title,
                        "series_name": series_key,
                        "release_group": rel_group,
                        "release_groups": list(getattr(rel, "release_groups", None) or ([rel_group] if rel_group else [])),
                        "authors": list(getattr(rel, "authors", None) or []),
                        "revision": getattr(rel, "revision", None),
                        "inferred_volumes": 1 if getattr(rel, "inferred_volumes", False) else 0,
                        "publisher": getattr(rel, "publisher", "") or "",
                        "is_bundle": rel.is_bundle,
                        "volume_start": rel.volume_start,
                        "volume_end": rel.volume_end,
                        "volumes": rel.volumes,
                        "missing_volumes": missing_vols,
                        "download_url": rel.download_url,
                        "link": rel.link,
                        "cover_url": cover_url,
                        "description": desc,
                        "mangaupdates_url": mu_url,
                        "source_feed_name": feed_name,
                        "published_at": getattr(rel, "pub_date", None),
                        "status": status,
                        "download_status": "IDLE"
                    }

                    # Check existing release state before upsert to detect transitions
                    existing_row = None
                    try:
                        with self.db.get_connection() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                "SELECT id, status, download_status FROM releases WHERE guid = ?;",
                                (rel.guid,)
                            )
                            existing_row = cur.fetchone()
                    except Exception as e:
                        logger.debug(f"Error checking existing release guid {rel.guid}: {e}")

                    is_new, rel_id = self.db.upsert_release(rel_dict)

                    # Determine if an existing release is transitioning from DISCOVERY or NEW to REQUESTED
                    prev_status = existing_row["status"] if existing_row else None
                    prev_dl_status = existing_row["download_status"] if existing_row else "IDLE"
                    is_transition = (
                        not is_new
                        and prev_status in ("DISCOVERY", "NEW")
                        and prev_dl_status == "IDLE"
                    )
                    if is_new:
                        total_new += 1
                    if is_transition:
                        total_transitions += 1

                    # What the database ACTUALLY holds after the precedence model ran.
                    # The matcher may compute REQUESTED while a stored user veto
                    # (IGNORED/BLACKLISTED) or IN_CALIBRE wins; no download can fire
                    # in that case, so the poll summary must not claim it as requested.
                    effective_status = resolve_status(prev_status, status)

                    # Auto-download trigger for REQUESTED releases
                    if effective_status == "REQUESTED":
                        feed_requested += 1
                        total_requested += 1

                        if is_transition:
                            # Ensure database status reflects REQUESTED even if the upsert
                            # preserved a contradictory state.  Precedence-aware so an
                            # explicit user decision can never be overridden here.
                            self.db.update_release_status(rel_id, "REQUESTED", enforce_precedence=True)

                        if is_new or is_transition:
                            try:
                                self.get_notifier().notify_new_request(rel_dict)
                            except Exception as e:
                                logger.debug(f"Webhook notification failed for new request: {e}")

                        if is_new or is_transition:
                            if not global_auto_download:
                                self._log_auto_download_decision(
                                    False, "global_disabled", feed_name, rel, series_key, missing_vols,
                                )
                            elif not feed.get("auto_download"):
                                self._log_auto_download_decision(
                                    False, "feed_disabled", feed_name, rel, series_key, missing_vols,
                                )
                        if (is_new or is_transition) and global_auto_download and feed.get("auto_download"):
                            engine_ready, engine_reason = self._auto_download_engine_ready()
                            if not missing_vols:
                                self._log_auto_download_decision(
                                    False, "no_missing_volumes", feed_name, rel, series_key, missing_vols,
                                )
                                continue
                            if not engine_ready:
                                self._log_auto_download_decision(
                                    False, f"engine_unavailable:{engine_reason}", feed_name, rel,
                                    series_key, missing_vols,
                                )
                                continue
                            if not (rel.download_url or getattr(rel, "link", None)):
                                self._log_auto_download_decision(
                                    False, "no_download_url", feed_name, rel, series_key, missing_vols,
                                )
                                continue
                            # In-flight dedupe (auto-download only): volumes already
                            # DOWNLOADING/COMPLETED for this series (not yet in Calibre)
                            # are never requested twice from a second release.
                            auto_wanted, _reserved = self._auto_download_wanted(rel, series_key, missing_vols)
                            if missing_vols and not auto_wanted:
                                self._log_auto_download_decision(
                                    False, "all_volumes_inflight", feed_name, rel, series_key,
                                    missing_vols, auto_wanted,
                                )
                                # Remember the duplicate: the sweep retries it once the
                                # in-flight reservation clears (it will never be "new").
                                if rel_id:
                                    self.db.update_downloader_state(rel_id, "PENDING_DEDUPE")
                            elif downloader_engine == "torbox":
                                # Torbox engine: submit + persist handle; the poll task
                                # fetches files (volume-scoped) into staging.
                                result = self.submit_torbox_download(
                                    rel_id,
                                    rel.title,
                                    rel.download_url,
                                    link=rel.link,
                                    missing_volumes=auto_wanted,
                                    source=feed_name,
                                    guid=getattr(rel, "guid", "") or "",
                                )
                                if result.get("success") and not result.get("skipped"):
                                    total_sent_to_downloader += 1
                                    self._log_auto_download_decision(
                                        True, "submitted", feed_name, rel, series_key,
                                        missing_vols, auto_wanted,
                                    )
                                elif result.get("skipped"):
                                    self._log_auto_download_decision(
                                        False, f"strict_skip:{result.get('message') or 'skipped'}",
                                        feed_name, rel, series_key, missing_vols, auto_wanted,
                                    )
                                else:
                                    self._log_auto_download_decision(
                                        False, f"submit_error:{result.get('error') or result.get('message') or 'unknown'}",
                                        feed_name, rel, series_key, missing_vols, auto_wanted,
                                    )
                            else:
                                success = qbit.add_torrent(
                                    url_or_magnet=rel.download_url,
                                    savepath=downloads_dir,
                                    category="manga",
                                    tags="mangatrax"
                                )
                                if success:
                                    total_sent_to_downloader += 1
                                    self._log_auto_download_decision(
                                        True, "submitted", feed_name, rel, series_key,
                                        missing_vols, auto_wanted,
                                    )
                                    torrent_h = qbit.extract_torrent_hash(rel.download_url) if hasattr(qbit, "extract_torrent_hash") else None
                                    if not isinstance(torrent_h, str):
                                        torrent_h = None
                                    self.db.update_download_handle(
                                        rel_id, "qbit", downloader_id=torrent_h or "",
                                        torrent_hash=torrent_h, wanted_volumes=auto_wanted,
                                    )
                                    self._log_activity(
                                        "AUTO_DOWNLOAD",
                                        f"Sent to downloader: '{rel.title}' (Missing: {auto_wanted})",
                                        f"Source: {feed_name}"
                                    )
                                    if torrent_h and rel.is_bundle and auto_wanted and len(auto_wanted) < len(rel.volumes):
                                        sel_res = qbit.apply_selective_volume_download(torrent_h, auto_wanted)
                                        if sel_res.get("status") == "filtered":
                                            self._log_activity(
                                                "SELECTIVE_DOWNLOAD",
                                                f"Selective download applied for '{rel.title}': skipped {len(sel_res.get('skipped_volumes', []))} owned/in-flight volumes; downloading {len(sel_res.get('kept_volumes', []))} missing volumes."
                                            )
                                else:
                                    self._log_auto_download_decision(
                                        False, "submit_error:qbit_add_failed", feed_name, rel,
                                        series_key, missing_vols, auto_wanted,
                                    )

                self.db.update_feed_status(feed_id, "OK", None)
            except Exception as e:
                err_msg = str(e)
                feeds_failed += 1
                feed_errors.append({"feed": feed_name, "error": err_msg})
                logger.error(f"Error checking feed '{feed_name}': {err_msg}")
                self.db.update_feed_status(feed_id, "ERROR", err_msg)
                self._log_activity("FEED_ERROR", f"Feed error in '{feed_name}': {err_msg}")

        summary = (
            f"Feeds checked: {total_found} releases found "
            f"({total_new} new, {total_transitions} transitions), "
            f"{total_requested} requested, {total_sent_to_downloader} sent to downloader."
        )
        if feeds_failed:
            # Never report a failed provider lookup as a silent success.
            detail = "; ".join(f"{item['feed']}: {item['error']}" for item in feed_errors[:5])
            summary += f" {feeds_failed} feed(s) failed: {detail}"
        logger.info(summary)
        if total_sent_to_downloader:
            # New downloads may already be present (e.g. cached Torbox jobs) and can be
            # tagged/routed right away; the 5-minute cadence covers the rest.
            try:
                self.process_downloads_to_staging(actor="scheduler")
            except Exception as exc:
                logger.debug("Downloads routing after feed poll failed: %s", exc)
        self._log_activity(
            "FEED_POLL_COMPLETED",
            summary,
            f"feeds_failed={feeds_failed}; errors={feed_errors}",
        )
        return self._feed_check_payload(
            success=feeds_failed == 0,
            message=summary,
            releases_found=total_found,
            new_releases=total_new,
            transitions=total_transitions,
            requested=total_requested,
            sent_to_downloader=total_sent_to_downloader,
            feeds_failed=feeds_failed,
            errors=feed_errors,
        )

    # --- Staging Auto-Tag Task ---
    def tag_staging_files(self) -> Dict[str, Any]:
        """
        Scans staging directory for .cbz archives, matches series against existing Calibre series templates,
        and injects standard ComicInfo.xml metadata.
        """
        staging_dir = self.db.get_setting("staging_dir", "/staging")
        series_templates = self.db.get_series_templates_map()

        try:
            tagged = auto_tag_staging_cbz_files(staging_dir, series_templates)
            for item in tagged:
                self._log_activity(
                    "AUTO_TAG_CBZ",
                    f"Injected ComicInfo.xml into '{item['filename']}' for series '{item['series_name']}' Vol {item['volume']}",
                    item["filepath"]
                )

            summary = f"Scanned staging directory: {len(tagged)} .cbz files tagged with ComicInfo.xml."
            logger.info(summary)
            return {"success": True, "message": summary, "tagged_count": len(tagged), "tagged_files": tagged}
        except Exception as e:
            err = f"Error during staging auto-tagging: {e}"
            logger.error(err)
            return {"success": False, "message": err, "tagged_count": 0, "tagged_files": []}

    # --- Historical Backfill Tasks ---
    def stop_backfill(self):
        """Signals the running backfiller to gracefully stop."""
        self.stop_backfill_requested = True
        self.db.set_setting("backfill_status", "stopping")
        self.db.set_setting("backfill_progress", "Stop requested, finishing current item...")

    def get_backfill_status(self) -> Dict[str, Any]:
        try:
            last_p = int(self.db.get_setting("backfill_last_page", "1"))
        except (ValueError, TypeError):
            last_p = 1
        return {
            "running": self.is_backfilling,
            "status": self.db.get_setting("backfill_status", "idle"),
            "progress": self.db.get_setting("backfill_progress", "Idle"),
            "last_page": last_p
        }

    def _process_release(
        self,
        rel,
        feed_name: str,
        matcher: MangaCalibreMatcher,
        known_series_keys: Optional[Set[str]] = None,
        target_identity: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """
        Resolves the STABLE matching key (Calibre name / typed name) while keeping
        AniList's canonical English title as display-only metadata, checks the
        Calibre matcher with aliases, pulls MangaUpdates synopsis if Discovery,
        and upserts release into SQLite.
        Returns (is_new, status, missing_volumes, rel_id, stable_series).

        ``known_series_keys`` is the marker-less chapter guard context (Calibre +
        tracked series); callers that process many releases pass it once instead of
        letting every release reload it.
        """
        if is_jpegxl_release(rel.title):
            self._log_jpegxl_discard(rel)
            return (False, "JPEGXL", [], None, None)
        # Load the known-series context early: a bare 3-digit trailing number is
        # chapter-only under task-44, but a real numbered series name ("Mob Psycho 100")
        # that is tracked must still be ingested rather than discarded.
        if known_series_keys is None:
            try:
                known_series_keys = self._known_series_keys(matcher)
            except Exception:
                known_series_keys = set()
        if is_chapter_only(rel.title):
            try:
                from app.parser import _parse_bare_chapter_number, extract_manga_bundle_and_volumes
                bare_chapter = _parse_bare_chapter_number(rel.title)
                parsed_numbered = extract_manga_bundle_and_volumes(rel.title)
                numbered_title = (parsed_numbered.get("numbered_series_name")
                                  or parsed_numbered.get("series_name") or "")
            except Exception:
                bare_chapter = None
                numbered_title = ""
            # Only a REAL numbered series name ("Mob Psycho 100") is protected from
            # the bare-number chapter rule; explicit Ch./Chapter markers must discard.
            full_title_known = normalize_text(rel.title) in known_series_keys
            if not (bare_chapter and (full_title_known or (
                numbered_title and normalize_text(numbered_title) in known_series_keys
            ))):
                # Keep chapter discards auditable (CHAPTER_DISCARDED) like the
                # context-guarded path, so users can see why a release was skipped.
                try:
                    parsed_chapter = extract_manga_bundle_and_volumes(rel.title)
                    chapter_series = (parsed_chapter.get("series_name") or "").strip()
                    chapter_number = (bare_chapter or {}).get("number")
                    series_known = bool(chapter_series) and normalize_text(chapter_series) in known_series_keys
                    reason = (
                        f"bare 3-digit chapter number {chapter_number:g} of "
                        f"{'known ' if series_known else ''}series '{chapter_series}'"
                        if chapter_number is not None else
                        f"chapter-only release of {'known ' if series_known else ''}series '{chapter_series}'"
                    )
                except Exception:
                    reason = "chapter-only release"
                self._log_activity(
                    "CHAPTER_DISCARDED",
                    f"Discarded chapter release: '{rel.title}'",
                    reason,
                )
                return (False, "CHAPTER_ONLY", [], None, None)
        if is_light_novel(rel.title):
            return (False, "LIGHT_NOVEL", [], None, None)
        # Context is needed for the chapter guard AND for keeping numbered series
        # names ("Mob Psycho 100") as matching keys, so load it regardless of the
        # discard setting (callers that process many releases pass it in).
        if known_series_keys is None:
            try:
                known_series_keys = self._known_series_keys(matcher)
            except Exception:  # pragma: no cover - context load must never break ingestion
                known_series_keys = set()
        if self._discard_unmarked_chapters_enabled():
            if self._discard_unmarked_chapter(rel, known_series_keys):
                return (False, "CHAPTER_ONLY", [], None, None)

        rel_group = getattr(rel, "release_group", None) or rel.to_dict().get("release_group")
        is_comp = getattr(rel, "is_complete", False) or is_complete_series_release(rel.title)
        if is_comp:
            rel.is_complete = True
            rel.is_bundle = True

        # 1. Resolve the stable matching key + cosmetic display title.
        identity = self.resolve_series_identity(
            self._resolve_release_series_input(rel, known_series_keys),
            matcher=matcher,
            meta=getattr(rel, "meta", None),
        )
        series_key = identity["stable_name"]
        meta = identity.get("meta") or {}
        cover_url = rel.cover_url or meta.get("cover_url") or ""
        alt_names = identity["alt_names"]

        # task-40 spinoff guard: during a targeted parent backfill, never ingest a
        # release whose own parsed/AniList identity is a different series.  Such rows
        # are reported as SPINOFF_SKIPPED and never auto-downloaded.
        if target_identity:
            parsed_release_series = str(getattr(rel, "series_name", "") or "").strip()
            parsed_norm = normalize_text(parsed_release_series)
            stable_norm = normalize_text(series_key)
            release_anilist_id = identity.get("anilist_id")
            target_anilist_id = target_identity.get("anilist_id")
            target_alias_norms = set(target_identity.get("alias_norms") or ())
            different_entry = bool(
                release_anilist_id and target_anilist_id and release_anilist_id != target_anilist_id
            )
            different_name = bool(
                parsed_norm and parsed_norm not in target_alias_norms
                and stable_norm not in target_alias_norms
            )
            if different_entry or different_name:
                self._log_activity(
                    "SPINOFF_SKIPPED",
                    f"Skipped spinoff release '{rel.title}' during backfill of "
                    f"'{target_identity.get('primary_name') or ''}'",
                    json.dumps({
                        "title": rel.title,
                        "parsed_series": parsed_release_series,
                        "resolved_series": series_key,
                        "target_series": target_identity.get("primary_name"),
                        "release_anilist_id": release_anilist_id,
                        "target_anilist_id": target_anilist_id,
                    }),
                )
                return (False, "SPINOFF_SKIPPED", [], None, series_key)

        # Collected batch (year-range title, no volume markers): infer the full set
        # from authoritative metadata only.  Explicit volume markers and complete
        # markers always win and are handled below, untouched.
        self._apply_collected_release_inference(rel, series_key, meta, alt_names)

        # If release is marked (complete) and has no volume labels, fetch total volume count from external sources
        if is_comp and not rel.volumes:
            total_vols = meta.get("total_volumes") if meta else None
            # If not yet resolved, query AniList with candidate alternate titles
            if not total_vols and alt_names:
                for alt in alt_names:
                    alt_meta = self.anilist.resolve_manga_series(alt)
                    if alt_meta and alt_meta.get("total_volumes"):
                        total_vols = alt_meta["total_volumes"]
                        if not meta or not meta.get("canonical_title"):
                            meta = alt_meta
                        break
            # If still not resolved, query MangaUpdates
            if not total_vols and series_key:
                try:
                    mu_info = self.mangaupdates.get_series_info(series_key)
                    if mu_info and mu_info.get("total_volumes"):
                        total_vols = mu_info["total_volumes"]
                except Exception as e:
                    logger.debug(f"MangaUpdates volume count lookup failed for '{series_key}': {e}")

            if total_vols and int(total_vols) > 0:
                v_count = int(total_vols)
                rel.volume_start = 1.0
                rel.volume_end = float(v_count)
                rel.volumes = [float(v) for v in range(1, v_count + 1)]
                logger.info(f"Resolved complete series '{series_key}' to {v_count} volumes from external metadata.")

        if cover_url:
            self.cover_cache.cache_sync(cover_url)

        # 2. Blacklist check against the stable key
        test_name = series_key or rel.series_name
        if test_name and self.db.is_series_blacklisted(test_name):
            status = "BLACKLISTED"
            missing_vols = []
        else:
            match_payload = rel.to_dict()
            match_payload["series_name"] = series_key
            match_payload["is_complete"] = is_comp
            match_res = matcher.check_release(match_payload, alt_series_names=alt_names)
            status = match_res["status"]
            missing_vols = match_res.get("missing_volumes", [])

        desc = getattr(rel, "description", "") or ""
        mu_url = ""
        if status == "DISCOVERY" and series_key:
            try:
                mu_info = self.mangaupdates.get_series_info(series_key)
                if mu_info:
                    if mu_info.get("description"):
                        desc = mu_info["description"]
                    mu_url = mu_info.get("url", "")
                    if not cover_url and mu_info.get("cover_url"):
                        cover_url = mu_info["cover_url"]
                        self.cover_cache.cache_sync(cover_url)
            except Exception as e:
                logger.debug(f"Backfill MangaUpdates fetch failed for '{series_key}': {e}")

        rel_dict = {
            "guid": rel.guid,
            "title": rel.title,
            "series_name": series_key,
            "release_group": rel_group,
                        "release_groups": list(getattr(rel, "release_groups", None) or ([rel_group] if rel_group else [])),
                        "authors": list(getattr(rel, "authors", None) or []),
            "revision": getattr(rel, "revision", None),
            "inferred_volumes": 1 if getattr(rel, "inferred_volumes", False) else 0,
            "publisher": getattr(rel, "publisher", "") or "",
            "is_bundle": rel.is_bundle,
            "is_complete": 1 if is_comp else 0,
            "volume_start": rel.volume_start,
            "volume_end": rel.volume_end,
            "volumes": rel.volumes,
            "missing_volumes": missing_vols,
            "download_url": rel.download_url,
            "link": rel.link,
            "cover_url": cover_url,
            "description": desc,
            "mangaupdates_url": mu_url,
            "source_feed_name": feed_name,
            "published_at": getattr(rel, "pub_date", None),
            "status": status,
            "download_status": "IDLE"
        }

        is_new, rel_id = self.db.upsert_release(rel_dict)
        if rel_id:
            try:
                from app.tag_display import tags_from_metadata
                provider_tags = tags_from_metadata(meta)
                if provider_tags:
                    self.db.update_release_tags(rel_id, provider_tags)
                    self.db.update_series_tags(series_key, provider_tags)
            except Exception as exc:
                logger.debug("Could not persist provider tags for release %s: %s", rel_id, exc)
        return (is_new, status, missing_vols, rel_id, series_key)

    def detect_series_titles(self, series_name: str, allow_anilist: bool = True) -> List[str]:
        """
        Detects and extracts all distinct titles for a series (e.g. English canonical title,
        Romaji alias, parts separated by delimiters like | or /, and titles from DB releases).
        """
        from app.parser import normalize_text

        clean_name = series_name.strip()
        if not clean_name:
            return []

        candidates = []

        # 1. Delimiters in the provided series name (| , / , // , - , — , ;)
        parts = split_series_candidate_parts(clean_name)
        if len(parts) > 1:
            candidates.extend(parts)

        # 2. Parentheses e.g. "English Title (Romaji Title)"
        if "(" in clean_name and ")" in clean_name:
            m_paren = re.findall(r"\((.*?)\)", clean_name)
            base = re.sub(r"\(.*?\)", "", clean_name).strip()
            if base:
                candidates.append(base)
            candidates.extend(m_paren)

        # 3. Deliberately DO NOT harvest titles from existing releases: a
        #    mis-attributed spinoff row stored under this series would otherwise
        #    become an "alternate title" and feed a self-amplifying backfill loop
        #    (task-40).  Only the requested name, its delimiter/parenthesis parts
        #    and the AniList entry's own aliases are searched.

        # 4. Check AniList metadata cache or query AniList. Background expansion
        # passes call with allow_anilist=False: they resolve each candidate later,
        # and doing it here too doubled/tripled AniList traffic per release.
        initial_check = list(candidates) if candidates else [clean_name]
        for term in initial_check[:3] if allow_anilist else []:
            try:
                meta = self.anilist.resolve_manga_series(term)
                if meta:
                    canon = (meta.get("canonical_title") or meta.get("english_title") or "").strip()
                    romaji = (meta.get("romaji_title") or "").strip()
                    if canon:
                        candidates.append(canon)
                    if romaji:
                        candidates.append(romaji)
                    for synonym in meta.get("synonyms") or []:
                        synonym = str(synonym or "").strip()
                        if synonym:
                            candidates.append(synonym)
            except Exception:
                pass

        # 5. Clean, strip volume keywords, and deduplicate
        search_titles: List[str] = []
        seen_norms = set()
        for c in candidates or [clean_name]:
            c_clean = re.sub(r"\[.*?\]|\(.*?\)", "", c).strip(" -–—:,|./")
            c_clean = re.sub(r"\b(?:volumes?|volume|vol|book|v)\b\.?\s*\d+.*$", "", c_clean, flags=re.IGNORECASE).strip(" -–—:,|./")
            norm = normalize_text(c_clean)
            if norm and len(norm) >= 3 and norm not in seen_norms:
                seen_norms.add(norm)
                search_titles.append(c_clean)

        # If multiple distinct sub-titles exist, drop any giant combined concatenation string
        if len(search_titles) > 1:
            filtered = []
            for t in search_titles:
                t_norm = normalize_text(t)
                is_combined = False
                for other in search_titles:
                    if t != other and len(t) > len(other) and normalize_text(other) in t_norm:
                        if len(t) >= len(other) + 15:
                            is_combined = True
                            break
                if not is_combined:
                    filtered.append(t)
            if filtered:
                search_titles = filtered

        return search_titles or [clean_name]

    # --- Per-series refresh (task-24): provider metadata + Calibre + Nyaa crawl ---
    @staticmethod
    def _coerce_total_volumes(value: Any) -> Optional[int]:
        try:
            if value is None or value == "":
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _series_refresh_matcher(self) -> MangaCalibreMatcher:
        """Fresh matcher built after the metadata/Calibre phases."""
        calibre_path = self.db.get_setting("calibre_path", "/calibre")
        scanner = CalibreMangaScanner(calibre_path)
        series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
        volumes_map = scanner.get_series_volumes_map() if scanner.is_available() else {}
        meta_cache = self.db.get_all_manga_metadata_cache()
        return MangaCalibreMatcher(series_list, volumes_map, meta_cache, staged_volumes_map=self._staged_volumes_map())

    def refresh_series_provider_metadata(self, series_name: str, actor: str = "scheduler") -> Dict[str, Any]:
        """Phase 1: force-refresh AniList (MangaUpdates fallback) enrichment data.

        Display/enrichment only: the stable ``series_tracking.name`` (and therefore
        release matching keys) is never changed.  Provider data is written through
        the existing metadata cache; if the provider is unavailable the cached values
        are kept and the phase reports a skip.
        """
        series_row = self.db.get_series_by_name(series_name)
        stable_name = str((series_row or {}).get("name") or series_name).strip()
        if not stable_name:
            return {"updated": False, "skipped": True, "reason": "series name required", "changes": []}
        try:
            previous = self.db.get_cached_manga_metadata(stable_name) or {}
        except Exception:
            previous = {}

        provider_error = ""
        try:
            fetched = self.anilist.resolve_manga_series(stable_name, force_refresh=True) or {}
        except Exception as exc:  # provider down must never fail the phase/job
            fetched = {}
            provider_error = str(exc)
        authoritative = bool(fetched) and fetched.get("source") not in ("fallback", "cleaned", None)

        fallback_info: Dict[str, Any] = {}
        if not authoritative:
            try:
                fallback_info = self.mangaupdates.get_series_info(stable_name) or {}
            except Exception as exc:
                fallback_info = {}
                provider_error = provider_error or str(exc)

        def pick(primary_key: str, fallback_key: str = "") -> Any:
            value = fetched.get(primary_key)
            if value not in (None, "", []):
                return value
            if fallback_key:
                value = fallback_info.get(fallback_key)
                if value not in (None, "", []):
                    return value
            return previous.get(primary_key)

        canonical = pick("canonical_title") or stable_name
        romaji = pick("romaji_title") or ""
        total_volumes = (
            self._coerce_total_volumes(pick("total_volumes")) if authoritative
            else self._coerce_total_volumes(previous.get("total_volumes"))
        )
        status = pick("status") or previous.get("status") or "UNKNOWN"
        cover_url = pick("cover_url", "cover_url") or ""
        synopsis = pick("synopsis", "description") or ""
        provider_authors = str(fetched.get("authors") or fallback_info.get("authors") or "").strip()

        changes: List[str] = []
        if authoritative:
            prev_canonical = str(previous.get("canonical_title") or "")
            if canonical and prev_canonical and canonical != prev_canonical:
                changes.append(f"title '{prev_canonical}' -> '{canonical}'")
            prev_romaji = str(previous.get("romaji_title") or "")
            if romaji and prev_romaji and romaji != prev_romaji:
                changes.append(f"romaji '{prev_romaji}' -> '{romaji}'")
            prev_total = self._coerce_total_volumes(previous.get("total_volumes"))
            if total_volumes is not None and total_volumes != prev_total:
                changes.append(f"total volumes {prev_total if prev_total is not None else '?'} -> {total_volumes}")
            prev_status = str(previous.get("status") or "UNKNOWN")
            if status and status != prev_status:
                changes.append(f"status {prev_status} -> {status}")
            prev_cover = str(previous.get("cover_url") or "")
            if cover_url and cover_url != prev_cover:
                changes.append("cover updated" if prev_cover else "cover added")
            prev_synopsis = str(previous.get("synopsis") or "")
            if synopsis and prev_synopsis and synopsis != prev_synopsis:
                changes.append("synopsis updated")

        update_fields: Dict[str, Any] = {}
        provider_metadata_available = authoritative or bool(fallback_info.get("description"))
        if series_row and provider_metadata_available:
            if synopsis and synopsis != str(series_row.get("description") or ""):
                update_fields["description"] = synopsis
            if provider_authors and provider_authors != str(series_row.get("authors") or ""):
                update_fields["authors"] = provider_authors
            fallback_url = str(fallback_info.get("url") or "").strip()
            if fallback_url and not str(series_row.get("website_url") or "").strip():
                update_fields["website_url"] = fallback_url
        if update_fields and series_row:
            try:
                self.db.update_series_metadata(series_row["id"], update_fields)
            except Exception as exc:
                logger.debug("Could not update series display metadata for '%s': %s", stable_name, exc)

        if cover_url and (not previous.get("cover_url") or cover_url != previous.get("cover_url")):
            try:
                self.cover_cache.cache_sync(cover_url)
            except Exception as exc:  # cover failures are tolerated
                logger.debug("Cover refresh failed for '%s': %s", stable_name, exc)
            try:
                self.db.update_series_cover_on_releases(stable_name, cover_url)
            except Exception as exc:
                logger.debug("Could not update release cover URLs for '%s': %s", stable_name, exc)

        updated = bool(changes or update_fields)
        if authoritative:
            message = (
                f"Metadata refreshed for '{stable_name}': " + ", ".join(changes)
                if changes else f"Metadata refreshed for '{stable_name}': no changes"
            )
            skipped = False
        else:
            message = (
                f"Metadata refresh for '{stable_name}' skipped (provider unavailable"
                + (f": {provider_error}" if provider_error else "")
                + "); kept cached values."
            )
            skipped = True
        details = json.dumps({
            "canonical_title": canonical,
            "romaji_title": romaji,
            "total_volumes": total_volumes,
            "status": status,
            "cover_url": cover_url,
            "authors": provider_authors,
            "changes": changes,
            "source": fetched.get("source") or ("mangaupdates" if fallback_info else "cache"),
            "skipped": skipped,
        })
        self._log_activity("SERIES_METADATA_REFRESHED", message, details, actor=actor)
        return {
            "updated": updated,
            "skipped": skipped,
            "changes": changes,
            "source": fetched.get("source") or ("mangaupdates" if fallback_info else "cache"),
            "total_volumes": total_volumes,
            "status": status,
            "cover_url": cover_url,
        }

    def refresh_series_calibre_info(self, series_name: str, actor: str = "scheduler") -> Dict[str, Any]:
        """Phase 2: re-read one series from Calibre (zero-lock) and update its row."""
        series_row = self.db.get_series_by_name(series_name)
        if not series_row:
            return {"updated": False, "skipped": True, "reason": "series is not tracked", "changes": []}
        stable_name = str(series_row.get("name") or series_name)
        calibre_path = self.db.get_setting("calibre_path", "/calibre")
        scanner = CalibreMangaScanner(calibre_path)
        if not scanner.is_available():
            message = f"Calibre refresh for '{stable_name}' skipped: library unavailable at {calibre_path}."
            self._log_activity("SERIES_CALIBRE_REFRESHED", message, json.dumps({"skipped": True}), actor=actor)
            return {"updated": False, "skipped": True, "reason": "Calibre unavailable", "changes": []}
        try:
            entries = scanner.get_all_series() or []
            volumes_map = scanner.get_series_volumes_map() or {}
        except Exception as exc:
            message = f"Calibre refresh for '{stable_name}' failed: {exc}"
            self._log_activity("SERIES_CALIBRE_REFRESHED", message, json.dumps({"error": str(exc)}), actor=actor)
            return {"updated": False, "skipped": True, "reason": str(exc), "changes": []}

        target = None
        norm_target = normalize_text(stable_name)
        for entry in entries:
            entry_name = str(entry.get("series_name") or "")
            if entry_name.lower() == stable_name.lower() or normalize_text(entry_name) == norm_target:
                target = entry
                break
        if not target:
            message = f"Calibre refresh for '{stable_name}' skipped: series not found in the library."
            self._log_activity("SERIES_CALIBRE_REFRESHED", message, json.dumps({"skipped": True}), actor=actor)
            return {"updated": False, "skipped": True, "reason": "series not found in Calibre", "changes": []}

        owned_volumes = sorted(volumes_map.get(str(target.get("series_name") or "").lower(), set()))
        updates: Dict[str, Any] = {
            "calibre_series_id": target.get("series_id"),
            "book_count": int(target.get("book_count") or 0),
            "latest_index": float(target.get("latest_index") or 0.0),
            "owned_volumes_json": json.dumps(owned_volumes),
        }
        for field in ("authors", "publisher", "tags", "description", "website_url"):
            value = str(target.get(field) or "").strip()
            if value:
                updates[field] = value

        changes: List[str] = []
        for field in ("authors", "publisher", "tags", "description", "website_url"):
            if field not in updates:
                continue
            new_value = updates[field]
            old_value = str(series_row.get(field) or "")
            if new_value != old_value:
                changes.append(f"{field} '{old_value}' -> '{new_value}'")
        if int(series_row.get("book_count") or 0) != updates["book_count"]:
            changes.append(f"book count {int(series_row.get('book_count') or 0)} -> {updates['book_count']}")
        if float(series_row.get("latest_index") or 0.0) != updates["latest_index"]:
            changes.append(f"latest index {float(series_row.get('latest_index') or 0.0):g} -> {updates['latest_index']:g}")
        try:
            previous_owned = sorted(json.loads(series_row.get("owned_volumes_json") or "[]"))
        except Exception:
            previous_owned = []
        if previous_owned != owned_volumes:
            changes.append(f"owned volumes {previous_owned} -> {owned_volumes}")
        try:
            self.db.update_series_metadata(series_row["id"], updates)
        except Exception as exc:
            logger.debug("Could not update Calibre info for '%s': %s", stable_name, exc)
            return {"updated": False, "skipped": True, "reason": str(exc), "changes": []}

        # Refresh the cached cover when the series' cover URL changed (tolerate failure).
        cover_url = str(target.get("cover_url") or "").strip()
        if not cover_url:
            try:
                live_series = self.db.get_all_series(search=stable_name)
                cover_url = next(
                    (str(item.get("cover_url") or "").strip() for item in live_series
                     if str(item.get("name") or "").lower() == stable_name.lower()),
                    "",
                )
            except Exception:
                cover_url = ""
        if cover_url:
            try:
                self.cover_cache.cache_sync(cover_url)
            except Exception as exc:
                logger.debug("Cover refresh failed for '%s': %s", stable_name, exc)

        message = (
            f"Calibre info refreshed for '{stable_name}': " + ", ".join(changes)
            if changes else f"Calibre info refreshed for '{stable_name}': no changes"
        )
        self._log_activity(
            "SERIES_CALIBRE_REFRESHED", message,
            json.dumps({"changes": changes, "book_count": updates["book_count"], "owned_volumes": owned_volumes}),
            actor=actor,
        )
        return {"updated": bool(changes), "skipped": False, "changes": changes,
                "book_count": updates["book_count"], "owned_volumes": owned_volumes}

    def _reconcile_series_releases(self, series_name: str, matcher: MangaCalibreMatcher) -> int:
        """Recompute missing volumes/status for one series' releases only."""
        changed = 0
        try:
            releases = self.db.get_releases_for_series(series_name)
        except Exception as exc:
            logger.error("Series reconcile: could not load releases for '%s': %s", series_name, exc)
            return 0
        for release in releases:
            try:
                result = matcher.check_release(release, auto_request_monitored=True)
                new_missing = result.get("missing_volumes", [])
                new_status = result.get("status")
                if new_missing != release.get("missing_volumes", []) or new_status != release.get("status"):
                    self.db.update_release_volumes_and_status(
                        release["id"], release.get("volumes", []), new_missing, new_status,
                    )
                    changed += 1
            except Exception as exc:
                logger.debug("Series reconcile skipped release %s: %s", release.get("id"), exc)
        return changed

    def backfill_series(self, series_name: str, max_pages: int = 3, actor: Optional[str] = None,
                         progress=None) -> Dict[str, Any]:
        """
        Targeted historical backfill for an individual manga series on Nyaa.
        Queries all past volume releases & bundles on Nyaa for the series and resolves them against Calibre.
        When multiple titles are detected (e.g. Romaji and English), searches both titles on Nyaa.
        """
        clean_name = series_name.strip()
        if not clean_name:
            return {"status": "error", "message": "Series name required"}

        # 1. Detect all distinct series titles (e.g. English title, Romaji title, delimiter parts)
        detected_titles = self.detect_series_titles(clean_name)
        primary_title = detected_titles[0]
        alt_titles = detected_titles[1:]

        title_display = " & ".join(f"'{t}'" for t in detected_titles)
        refresh_actor = actor or "scheduler"
        logger.info(f"Starting series refresh + targeted backfill for {title_display} (max_pages={max_pages})")

        if progress:
            progress(0, 4, "Provider metadata phase")

        # Phase 1: provider metadata (display/enrichment only; never the stable key).
        self.db.set_setting("backfill_progress", f"Refreshing metadata for {title_display}...")
        try:
            metadata_summary = self.refresh_series_provider_metadata(clean_name, actor=refresh_actor)
        except Exception as exc:  # phase isolation: never abort the job
            logger.error("Metadata refresh phase failed for '%s': %s", clean_name, exc)
            metadata_summary = {"updated": False, "skipped": True, "reason": str(exc), "changes": []}
            self._log_activity(
                "SERIES_METADATA_REFRESHED",
                f"Metadata refresh failed for '{clean_name}': {exc}",
                json.dumps({"error": str(exc)}),
                actor=refresh_actor,
            )

        if progress:
            progress(1, 4, "Calibre info phase")

        # Phase 2: Calibre info for this one series (skips cleanly when unavailable).
        self.db.set_setting("backfill_progress", f"Refreshing Calibre info for {title_display}...")
        try:
            calibre_summary = self.refresh_series_calibre_info(clean_name, actor=refresh_actor)
        except Exception as exc:
            logger.error("Calibre refresh phase failed for '%s': %s", clean_name, exc)
            calibre_summary = {"updated": False, "skipped": True, "reason": str(exc), "changes": []}
            self._log_activity(
                "SERIES_CALIBRE_REFRESHED",
                f"Calibre refresh failed for '{clean_name}': {exc}",
                json.dumps({"error": str(exc)}),
                actor=refresh_actor,
            )

        if progress:
            progress(2, 4, "Reconciling releases phase")

        # Reconcile only this series' releases against the refreshed metadata/ownership.
        self.db.set_setting("backfill_progress", f"Reconciling releases for {title_display}...")
        matcher = self._series_refresh_matcher()
        try:
            reconciled = self._reconcile_series_releases(clean_name, matcher)
        except Exception as exc:
            logger.error("Series reconcile phase failed for '%s': %s", clean_name, exc)
            reconciled = 0
        total_volume_changed = any(
            str(change).startswith("total volumes") for change in (metadata_summary.get("changes") or [])
        ) or any(
            str(change).startswith("book count") for change in (calibre_summary.get("changes") or [])
        )
        expanded = 0
        if total_volume_changed:
            try:
                expanded = self.expand_unresolved_complete_releases(matcher=matcher, series_name=clean_name)
            except Exception as exc:
                logger.debug("Complete-series expansion after refresh failed for '%s': %s", clean_name, exc)

        # Context for the whole backfill run: the chapter guard and the
        # numbered-series key protection both need the known-series set.
        known_series_keys = self._known_series_keys(matcher)

        # task-40: identity context for this refresh target.  Crawled releases are
        # only accepted when their own parsed name (or AniList entry) belongs to it.
        try:
            target_matches = self.resolve_series_identity(clean_name, matcher=matcher)
        except Exception:
            target_matches = {"stable_name": clean_name, "alt_names": [], "anilist_id": None}
        target_alias_norms = set()
        for value in [clean_name, target_matches.get("stable_name")] + list(detected_titles):
            norm = normalize_text(value or "")
            if norm:
                target_alias_norms.add(norm)
        for value in target_matches.get("alt_names") or []:
            norm = normalize_text(value or "")
            if norm:
                target_alias_norms.add(norm)
        target_identity = {
            "primary_name": clean_name,
            "alias_norms": target_alias_norms,
            "anilist_id": target_matches.get("anilist_id"),
        }

        global_auto_download = self.db.get_setting("auto_download", "true").lower() == "true"
        downloads_dir = self.get_downloads_dir()
        qbit = self.get_qbit_client()
        downloader_engine = self.get_downloader_engine()

        # 3. Query Nyaa targeted series search across primary and alternate titles
        def _crawl_progress(page, _max_pages, _url):
            self.db.set_setting(
                "backfill_progress",
                f"Crawling Nyaa page {page} for {title_display} (up to {max_pages} pages each)...",
            )
            if progress:
                progress(min(int(page or 0), int(max_pages)), int(max_pages), f"Nyaa page {page}/{max_pages}")

        nyaa = NyaaMangaProvider(name=f"Nyaa: {primary_title}", config={"delay_seconds": 1.0, "base_url": self._nyaa_base_url()})
        crawl_error = ""
        try:
            releases = nyaa.search_series(
                primary_title,
                max_pages=max_pages,
                alt_names=alt_titles,
                stop_check=lambda: self.stop_backfill_requested,
                progress_cb=_crawl_progress,
            )
        except Exception as exc:
            # Phase isolation: metadata/Calibre updates stay applied even if the crawl fails.
            crawl_error = str(exc)
            releases = []
            logger.error("Nyaa crawl phase failed for '%s': %s", primary_title, exc)

        total_found = 0
        total_requested = 0
        total_sent_to_downloader = 0

        total_releases = len(releases)
        for release_index, rel in enumerate(releases, 1):
            if self.stop_backfill_requested:
                break
            if progress:
                progress(release_index - 1, total_releases,
                         f"Crawled {min(max_pages, max_pages)} pages; {release_index}/{total_releases} releases processed")
            is_new, status, missing_vols, rel_id, stable_series = self._process_release(
                rel, f"Nyaa: {primary_title}", matcher, known_series_keys=known_series_keys,
                target_identity=target_identity,
            )
            if status == "SPINOFF_SKIPPED":
                continue
            if status in ("CHAPTER_ONLY", "LIGHT_NOVEL", "JPEGXL"):
                continue

            total_found += 1
            if status == "REQUESTED":
                total_requested += 1
                if is_new:
                    try:
                        rel_data = self.db.get_release_by_id(rel_id) if rel_id else None
                        if rel_data:
                            self.get_notifier().notify_new_request(rel_data)
                    except Exception as e:
                        logger.debug(f"Webhook notification failed for backfill request: {e}")
                if is_new and global_auto_download and rel_id:
                    # In-flight dedupe (auto-download only): don't fetch a volume
                    # another release of this series is already downloading/awaiting
                    # Calibre with.
                    auto_wanted, _reserved = self._auto_download_wanted(rel, stable_series, missing_vols)
                    if missing_vols and not auto_wanted:
                        # Remember the duplicate for the periodic dedupe sweep.
                        if rel_id:
                            self.db.update_downloader_state(rel_id, "PENDING_DEDUPE")
                    elif downloader_engine == "torbox":
                        result = self.submit_torbox_download(
                            rel_id,
                            rel.title,
                            rel.download_url,
                            link=rel.link,
                            missing_volumes=auto_wanted,
                            source=f"Nyaa: {primary_title}",
                            guid=getattr(rel, "guid", "") or "",
                        )
                        if result.get("success") and not result.get("skipped"):
                            total_sent_to_downloader += 1
                    else:
                        success = qbit.add_torrent(
                            url_or_magnet=rel.download_url,
                            savepath=downloads_dir,
                            category="manga",
                            tags="mangatrax"
                        )
                        if success:
                            total_sent_to_downloader += 1
                            torrent_h = qbit.extract_torrent_hash(rel.download_url) if hasattr(qbit, "extract_torrent_hash") else None
                            if not isinstance(torrent_h, str):
                                torrent_h = None
                            self.db.update_download_handle(
                                rel_id, "qbit", downloader_id=torrent_h or "",
                                torrent_hash=torrent_h, wanted_volumes=auto_wanted,
                            )
                            if torrent_h and rel.is_bundle and auto_wanted and len(auto_wanted) < len(rel.volumes):
                                qbit.apply_selective_volume_download(torrent_h, auto_wanted)

        if metadata_summary.get("skipped"):
            metadata_text = "metadata skipped (provider unavailable)"
        elif metadata_summary.get("updated"):
            metadata_text = "metadata updated (" + ", ".join(metadata_summary.get("changes") or []) + ")"
        else:
            metadata_text = "metadata up to date"
        if calibre_summary.get("skipped"):
            calibre_text = "Calibre skipped (" + str(calibre_summary.get("reason") or "unavailable") + ")"
        elif calibre_summary.get("updated"):
            calibre_text = "Calibre updated (" + ", ".join(calibre_summary.get("changes") or []) + ")"
        else:
            calibre_text = "Calibre up to date"
        summary = (
            f"Series refresh for {title_display}: {metadata_text}; {calibre_text}; "
            f"backfilled {total_found} releases ({total_requested} wanted/missing volumes, "
            f"{total_sent_to_downloader} sent to downloader; {reconciled} release(s) reconciled"
            + (f", {expanded} complete release(s) expanded" if expanded else "")
            + ")."
        )
        if crawl_error:
            summary += f" Nyaa crawl failed: {crawl_error}"
        logger.info(summary)
        self._log_activity("SERIES_BACKFILLED", summary)
        return {
            "status": "partial" if crawl_error else "success",
            "message": summary,
            "series_name": primary_title,
            "searched_titles": detected_titles,
            "releases_found": total_found,
            "requested": total_requested,
            "sent_to_downloader": total_sent_to_downloader,
            "reconciled": reconciled,
            "expanded": expanded,
            "summary": {
                "metadata": metadata_summary,
                "calibre": calibre_summary,
                "backfill": {
                    "status": "error" if crawl_error else "success",
                    "error": crawl_error,
                    "releases_found": total_found,
                    "requested": total_requested,
                    "sent_to_downloader": total_sent_to_downloader,
                    "reconciled": reconciled,
                    "expanded": expanded,
                },
            },
        }

    def run_series_backfill_async(self, series_name: str, max_pages: int = 3, actor: Optional[str] = None,
                                  progress=None) -> Dict[str, Any]:
        """
        Launches the three-phase series refresh + targeted backfill in a background
        daemon thread, allowing the user to navigate pages while tracking live progress
        globally: metadata refresh -> Calibre info -> Nyaa crawl.
        """
        if self.is_backfilling:
            return {"status": "in_progress", "message": "Another backfill is already running"}

        self.is_backfilling = True
        self.stop_backfill_requested = False

        def _worker():
            self.db.set_setting("backfill_status", "running")
            self.db.set_setting(
                "backfill_progress",
                f"Refreshing metadata and Calibre info for '{series_name}', then crawling Nyaa...",
            )
            try:
                res = self.backfill_series(series_name, max_pages=max_pages, actor=actor, progress=progress)
                self.db.set_setting("backfill_status", "idle")
                self.db.set_setting("backfill_progress", res.get("message", f"Series refresh for '{series_name}' complete."))
            except Exception as e:
                logger.error(f"Error in async series refresh/backfill: {e}")
                self.db.set_setting("backfill_status", "error")
                self.db.set_setting("backfill_progress", f"Series refresh failed: {e}")
            finally:
                self.is_backfilling = False

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return {
            "status": "started",
            "message": (
                f"Series refresh for '{series_name}' started in background "
                "(metadata, Calibre info, Nyaa backfill)."
            ),
            "summary": {
                "metadata": {"status": "pending"},
                "calibre": {"status": "pending"},
                "backfill": {"status": "pending"},
            },
        }

    def run_polite_backfill(self, max_pages: Optional[int] = None, delay_seconds: Optional[float] = None, start_page: int = 1) -> Dict[str, Any]:
        """
        Runs a polite historical backfill in a background daemon thread:
        Paginates backwards through Nyaa Manga releases (starting from start_page up to max_pages) and configured feeds,
        populating newly discovered series (Vol. 1 & standalones) and missing volumes for tracked series.
        """
        if self.is_backfilling:
            return {"status": "in_progress", "message": "Backfill already running"}

        start_p = max(1, int(start_page or 1))
        self.is_backfilling = True
        self.stop_backfill_requested = False
        t = threading.Thread(
            target=self._run_polite_backfill_worker,
            args=(max_pages, delay_seconds, start_p),
            daemon=True
        )
        t.start()
        return {"status": "started", "message": f"Polite backfill started from page {start_p} in background"}

    def _run_polite_backfill_worker(self, max_pages: Optional[int] = None, delay_seconds: Optional[float] = None, start_page: int = 1):
        if max_pages is None:
            try:
                max_pages = int(self.db.get_setting("backfill_max_pages", "15"))
            except ValueError:
                max_pages = 15

        if delay_seconds is None:
            try:
                delay_seconds = float(self.db.get_setting("backfill_delay_seconds", "2.0"))
            except ValueError:
                delay_seconds = 2.0

        effective_max = 9999 if max_pages <= 0 else max_pages
        start_p = max(1, int(start_page or 1))
        end_p = (start_p + effective_max - 1) if max_pages > 0 else 9999
        target_pages_text = f"{max_pages} pages (pages {start_p} to {end_p})" if max_pages > 0 else f"unlimited pages starting from page {start_p}"

        self.db.set_setting("backfill_status", "running")
        self.db.set_setting("backfill_progress", f"Starting polite manga historical backfill ({target_pages_text})...")

        try:
            total_discovered = 0
            total_processed = 0

            calibre_path = self.db.get_setting("calibre_path", "/calibre")
            scanner = CalibreMangaScanner(calibre_path)
            series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
            volumes_map = scanner.get_series_volumes_map() if scanner.is_available() else {}
            meta_cache = self.db.get_all_manga_metadata_cache()
            matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache, staged_volumes_map=self._staged_volumes_map())

            # Context for the whole backfill run: the chapter guard and the
            # numbered-series key protection both need the known-series set.
            known_series_keys = self._known_series_keys(matcher)

            nyaa_provider = NyaaMangaProvider(name="Nyaa Manga Backfill", config={"delay_seconds": delay_seconds, "base_url": self._nyaa_base_url()})

            # Backfill bookkeeping (task-19): every stop must state WHY, and a failed
            # page must never be reported as a successful completion.
            stop_reason = ""
            last_crawled_page = 0

            for page_num in range(start_p, end_p + 1):
                if self.stop_backfill_requested:
                    self.db.set_setting("backfill_last_page", str(page_num))
                    stop_reason = f"stopped by user (page {page_num})"
                    break

                self.db.set_setting("backfill_last_page", str(page_num))
                self.db.set_setting(
                    "backfill_progress",
                    f"Crawling Nyaa Manga Page {page_num} of {end_p} (Found {total_discovered} discoveries, {total_processed} processed)..."
                )

                # One page per call so every page gets its own retry/stop reporting;
                # hard-fail on exhausted retries (never mistake flakiness for an end).
                page_releases = nyaa_provider.crawl_pages(
                    max_pages=1,
                    start_page=page_num,
                    delay=delay_seconds,
                    stop_check=lambda: self.stop_backfill_requested,
                    raise_on_fetch_error=True,
                )
                page_info = (nyaa_provider.last_crawl_pages or [{}])[-1]
                page_kind = page_info.get("kind", "")
                logger.info(
                    "Nyaa backfill page %s: HTTP %s, %s rows, %s item(s) parsed "
                    "(processed=%s, discoveries=%s).",
                    page_num, page_info.get("status"), page_info.get("rows", 0),
                    page_info.get("items", 0), total_processed, total_discovered,
                )
                if page_kind == "failed":
                    # raise_on_fetch_error=True should have raised; be explicit anyway.
                    reason = nyaa_provider.last_crawl_reason or f"failed on page {page_num}"
                    raise ProviderFetchError(f"Backfill {reason}")
                if page_kind == "end_of_results":
                    stop_reason = nyaa_provider.last_crawl_reason or (
                        f"reached end of results (page {page_num})"
                    )
                    break
                if page_kind == "stopped":
                    stop_reason = nyaa_provider.last_crawl_reason or f"stopped by user (page {page_num})"
                    break
                if not page_releases and page_kind == "ok":
                    # The page existed but every row was filtered out (chapters /
                    # light novels) -- this is NOT end-of-results.
                    logger.info(
                        "Nyaa backfill page %s had rows but no ingestable releases; continuing.",
                        page_num,
                    )

                for rel in page_releases:
                    if self.stop_backfill_requested:
                        break

                    is_new, status, missing_vols, _, _ = self._process_release(
                        rel, "Nyaa Manga", matcher, known_series_keys=known_series_keys
                    )
                    if status in ("CHAPTER_ONLY", "LIGHT_NOVEL", "JPEGXL"):
                        continue

                    total_processed += 1
                    if is_new and status == "DISCOVERY":
                        total_discovered += 1

                last_crawled_page = page_num
                if self.stop_backfill_requested:
                    stop_reason = f"stopped by user (page {page_num})"
                    break

            if not stop_reason:
                stop_reason = (
                    f"max pages reached ({max_pages})" if max_pages > 0
                    else "max pages reached (unlimited)"
                )

            crawled_upto = last_crawled_page or start_p
            summary = (
                f"Historical backfill complete: stop reason: {stop_reason}; "
                f"crawled pages {start_p}-{crawled_upto}, processed {total_processed} releases, "
                f"found {total_discovered} new discoveries."
            )
            logger.info(summary)
            self.db.set_setting("backfill_status", "idle")
            self.db.set_setting("backfill_progress", summary)
            self._log_activity("BACKFILL_COMPLETED", summary)

        except Exception as e:
            err = f"Backfill error: {e}"
            logger.error(err)
            self.db.set_setting("backfill_status", "error")
            self.db.set_setting("backfill_progress", err)
            self._log_activity("BACKFILL_ERROR", err)
        finally:
            self.is_backfilling = False

    def check_and_filter_active_downloads(self) -> int:
        """
        Scans active DOWNLOADING releases that are bundles with missing_volumes.
        If qBittorrent has resolved the metadata, adjusts file priorities so that
        only the missing volumes are downloaded, saving bandwidth and disk space.
        """
        qbit = self.get_qbit_client()
        if not qbit.enabled:
            return 0

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, series_name, torrent_hash, download_url,
                       volumes_json AS volumes, missing_vols_json AS missing_volumes,
                       download_wanted_json AS download_wanted, is_bundle
                FROM releases
                WHERE download_status = 'DOWNLOADING' AND is_bundle = 1
                  AND (downloader IS NULL OR downloader != 'torbox');
            """)
            rows = [dict(r) for r in cur.fetchall()]

        filtered_count = 0
        for r in rows:
            h = r.get("torrent_hash")
            if not h and r.get("download_url"):
                h = qbit.extract_torrent_hash(r["download_url"])
                if h:
                    self.db.update_download_status(r["id"], "DOWNLOADING", h)

            if not h:
                continue

            def _vols(raw):
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        return []
                if not isinstance(raw, list):
                    return []
                clean = []
                for value in raw:
                    try:
                        clean.append(round(float(value), 2))
                    except (TypeError, ValueError):
                        continue
                return sorted(set(clean))

            # The set chosen at submit time is authoritative; missing_vols is a
            # recomputable fallback for rows submitted before it was persisted.
            wanted = _vols(r.get("download_wanted")) or _vols(r.get("missing_volumes"))
            if not wanted:
                continue

            vols = _vols(r.get("volumes"))

            # Only need filtering if we already own some volumes in this bundle
            if vols and len(wanted) >= len(vols):
                continue

            signature = (int(r["id"]), str(h), tuple(wanted))
            if signature in self._selective_filter_done:
                continue

            res = qbit.apply_selective_volume_download(h, wanted)
            status = str(res.get("status") or "")
            if status == "pending_metadata":
                # Metadata is not loaded yet; retry on the next pass.
                continue
            if status == "filtered":
                filtered_count += 1
                skipped_vols = res.get("skipped_volumes", [])
                self._log_activity(
                    "SELECTIVE_DOWNLOAD",
                    f"Optimized torrent for '{r['title']}': downloading {len(res.get('kept_volumes', []))} missing volumes; skipped {len(skipped_vols)} volumes already in Calibre ({skipped_vols}).",
                    f"Torrent Hash: {h[:10]}..."
                )
                self._selective_filter_done.add(signature)
            elif status in ("all_owned", "single_archive", "disabled"):
                # A single archive cannot be split and a disabled engine will not
                # change on retry: stop asking for this signature.
                self._selective_filter_done.add(signature)
                if status == "disabled":
                    break
        return filtered_count

    def expand_unresolved_complete_releases(
        self, matcher: Optional[MangaCalibreMatcher] = None, series_name: Optional[str] = None,
    ) -> int:
        """
        Scans releases in the database that are complete series compilations
        but have no parsed volume numbers. Queries AniList/MangaUpdates for total volume count,
        expands into individual volumes [1.0 .. N], and matches against Calibre to calculate
        missing volumes and accurate status (e.g. IN_CALIBRE, REQUESTED, DISCOVERY).

        ``series_name`` limits the pass to one series (used by the per-series refresh).
        """
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                complete_clause = (
                    "(is_complete = 1 OR lower(title) LIKE '%(complete)%' "
                    "OR lower(title) LIKE '%[complete]%' OR lower(title) LIKE '%complete series%')"
                )
                if series_name:
                    cur.execute(f"""
                        SELECT id, guid, title, series_name, volumes_json, missing_vols_json, status, download_url, link, cover_url,
                               complete_expand_attempted_at
                        FROM releases
                        WHERE {complete_clause}
                          AND lower(series_name) = lower(?);
                    """, (str(series_name).strip(),))
                else:
                    cur.execute(f"""
                        SELECT id, guid, title, series_name, volumes_json, missing_vols_json, status, download_url, link, cover_url,
                               complete_expand_attempted_at
                        FROM releases
                        WHERE {complete_clause};
                    """)
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"Error querying unexpanded complete releases: {e}")
            return 0

        unexpanded = []
        for r in rows:
            v_raw = r.get("volumes_json")
            m_raw = r.get("missing_vols_json")
            v_list = []
            m_list = []
            if v_raw:
                try:
                    v_list = json.loads(v_raw)
                except Exception:
                    v_list = []
            if m_raw:
                try:
                    m_list = json.loads(m_raw)
                except Exception:
                    m_list = []
            # Only releases without resolved volumes need a lookup. Once expanded
            # (even when every volume is still missing) Calibre reconciliation owns
            # the state; re-expanding would re-query AniList for the same series on
            # every background pass.
            if not v_list:
                attempted_raw = str(r.get("complete_expand_attempted_at") or "").strip()
                attempted_at = 0.0
                if attempted_raw:
                    try:
                        attempted_at = datetime.strptime(
                            attempted_raw[:19], "%Y-%m-%d %H:%M:%S"
                        ).replace(tzinfo=timezone.utc).timestamp()
                    except (TypeError, ValueError):
                        attempted_at = 0.0
                # Explicit series refreshes bypass the throttle; background passes do not.
                if not series_name and attempted_at and (time.time() - attempted_at) < COMPLETE_EXPAND_RETRY_SECONDS:
                    continue
                unexpanded.append(r)

        if not unexpanded:
            return 0

        logger.info(f"Checking {len(unexpanded)} complete series release(s) for volume expansion in background...")

        if not matcher:
            calibre_path = self.db.get_setting("calibre_path", "/calibre")
            scanner = CalibreMangaScanner(calibre_path)
            series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
            volumes_map = scanner.get_series_volumes_map() if scanner.is_available() else {}
            meta_cache = self.db.get_all_manga_metadata_cache()
            matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache, staged_volumes_map=self._staged_volumes_map())

        expanded_count = 0
        for r in unexpanded:
            rel_id = r["id"]
            title = r["title"]
            s_name = (r.get("series_name") or "").strip()

            candidates = []
            seen_candidate_norms = set()

            def _add_candidate(value):
                clean = str(value or "").strip()
                norm = normalize_text(clean)
                if not clean or not norm or norm in seen_candidate_norms:
                    return
                seen_candidate_norms.add(norm)
                candidates.append(clean)

            if s_name:
                _add_candidate(s_name)
                for detected in self.detect_series_titles(s_name, allow_anilist=False):
                    _add_candidate(detected)
            _add_candidate(title)

            tot = None
            resolved_meta = None

            # If volumes were already resolved, we only need to reconcile against Calibre
            v_existing = []
            if r.get("volumes_json"):
                try:
                    v_existing = json.loads(r["volumes_json"])
                except Exception:
                    v_existing = []
            if v_existing:
                tot = len(v_existing)

            if not tot:
                for cand in candidates:
                    try:
                        m = self.anilist.resolve_manga_series(cand)
                        if m and m.get("total_volumes"):
                            tot = int(m["total_volumes"])
                            resolved_meta = m
                            break
                    except Exception:
                        pass

                if not tot and s_name:
                    try:
                        for cand in candidates:
                            mu = self.mangaupdates.get_series_info(cand)
                            if mu and mu.get("total_volumes"):
                                tot = int(mu["total_volumes"])
                                break
                    except Exception:
                        pass

            if not tot:
                # Persist the unresolved series so an admin can pin an AniList id
                # from Settings instead of waiting for a fuzzy match.
                try:
                    self.db.record_anilist_failure(
                        s_name or title, rel_id, title,
                        "no volume count from AniList or MangaUpdates",
                    )
                except Exception as exc:  # pragma: no cover - ledger is best effort
                    logger.debug("Could not record AniList failure for '%s': %s", s_name or title, exc)
            else:
                try:
                    self.db.clear_anilist_failure(s_name or title)
                except Exception:
                    pass

            try:
                with self.db.get_connection() as conn:
                    conn.execute(
                        "UPDATE releases SET complete_expand_attempted_at = CURRENT_TIMESTAMP WHERE id = ?;",
                        (rel_id,),
                    )
                    conn.commit()
            except Exception as exc:  # pragma: no cover - throttle is best effort
                logger.debug("Could not record complete-expansion attempt for #%s: %s", rel_id, exc)

            if tot and tot > 0:
                vols = [float(v) for v in range(1, tot + 1)]
                v_json = json.dumps(vols)

                # Match against Calibre
                # Matching payload: the stored series_name is the STABLE key; the
                # AniList canonical/romaji titles are passed as matcher aliases only.
                payload = {
                    "series_name": s_name,
                    "title": title,
                    "is_bundle": True,
                    "is_complete": True,
                    "volumes": vols,
                    "volume_start": 1.0,
                    "volume_end": float(tot)
                }
                match_res = matcher.check_release(payload, alt_series_names=candidates)
                candidate_status = match_res.get("status") or r.get("status") or "DISCOVERY"
                # Automated expansion writer: must not downgrade a sticky status
                # (e.g. a complete-series release the user already requested).
                new_status = resolve_status(r.get("status"), candidate_status)
                missing_vols = match_res.get("missing_volumes", vols)
                m_json = json.dumps(missing_vols)

                try:
                    with self.db.get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            UPDATE releases
                            SET is_bundle = 1, is_complete = 1, volume_start = 1.0, volume_end = ?,
                                volumes_json = ?, missing_vols_json = ?, status = ?
                            WHERE id = ?;
                        """, (float(tot), v_json, m_json, new_status, rel_id))
                        conn.commit()

                    expanded_count += 1
                    logger.info(f"Expanded complete series '{title}' to {tot} volumes (Status: {new_status}, Missing: {len(missing_vols)} vols).")
                except Exception as e:
                    logger.error(f"Error updating expanded complete release {rel_id}: {e}")

        if expanded_count > 0:
            self.db._invalidate_grouped_cache()

        return expanded_count

    # --- Background Loop ---
    def reconcile_stale_release_files(self, dry_run: bool = False) -> Dict[str, Any]:
        """Reset COMPLETED releases whose files no longer exist (task-54 self-heal)."""
        from app import downloads_reconcile
        try:
            calibre_series = self.db.get_all_series()
        except Exception:
            calibre_series = None
        return downloads_reconcile.reconcile_missing_release_files(
            self.db, self.get_downloads_dir(create=False),
            self.db.get_setting("staging_dir", "/staging"),
            calibre_series=calibre_series, dry_run=dry_run,
        )

    def _worker_loop(self):
        logger.info("MangaTrax background worker loop started.")
        # FIRST action: the work that used to block the FastAPI lifespan before
        # `yield` (legacy re-key repair + initial Calibre sync).  Shielded so any
        # failure is logged and the worker keeps running on its normal interval.
        try:
            self.perform_initial_sync()
        except Exception as exc:  # pragma: no cover - defensive outer shield
            logger.error("Initial startup sync crashed (worker continues): %s", exc)
        # Asynchronously expand any complete series releases that need volume counts from AniList/MangaUpdates
        try:
            self.expand_unresolved_complete_releases()
        except Exception as e:
            logger.debug(f"Initial complete releases expansion error: {e}")
        # Persist the volume-state reconciliation the grouped releases view computes
        # in memory (GETs are read-only, so maintenance owns the writes).
        try:
            reconciled = self.db.reconcile_release_volume_state()
            if reconciled:
                logger.info(f"Reconciled volume state for {reconciled} release(s) on startup.")
        except Exception as e:
            logger.debug(f"Initial release volume reconciliation error: {e}")

        last_feed_check = 0
        last_tag_check = 0
        last_dl_check = 0
        last_dedupe_sweep = 0
        last_dedupe_inventory = 0
        last_activity_prune = 0
        last_pickup_sweep = 0
        last_stale_reconcile = 0
        last_complete_check = time.time()

        while self.running:
            now = time.time()
            try:
                # Interval settings (in minutes)
                calibre_int = float(self.db.get_setting("calibre_sync_interval_minutes", "15")) * 60
                feed_int = float(self.db.get_setting("feed_check_interval_minutes", "60")) * 60
                auto_tag_enabled = self.db.get_setting("auto_tag", "true").lower() == "true"

                # Check Calibre
                if now - self.last_calibre_sync >= calibre_int:
                    self.sync_calibre_library()

                # Check Feeds
                if now - last_feed_check >= feed_int:
                    self.check_all_feeds(trigger="auto")
                    last_feed_check = time.time()

                # Check complete series volume expansion every 5 minutes
                if now - last_complete_check >= 300:
                    self.expand_unresolved_complete_releases()
                    try:
                        self.db.reconcile_release_volume_state()
                    except Exception as e:
                        logger.debug(f"Release volume reconciliation error: {e}")
                    last_complete_check = time.time()

                # Auto-tag staging every 5 minutes if enabled
                if auto_tag_enabled and (now - last_tag_check >= 300):
                    self.tag_staging_files()
                    # task-22: also route eligible raw downloads into staging.
                    try:
                        self.process_downloads_to_staging(actor="scheduler")
                    except Exception as exc:
                        logger.debug("Periodic downloads routing failed: %s", exc)
                    last_tag_check = time.time()

                # task-54: self-heal releases whose staged/downloaded files vanished.
                if now - last_stale_reconcile >= 300:
                    try:
                        rec = self.reconcile_stale_release_files()
                        if rec.get("reset"):
                            logger.info(
                                "Reconciled stale release files: checked %s, reset %s.",
                                rec.get("checked"), rec.get("reset"),
                            )
                        last_stale_reconcile = time.time()
                    except Exception as exc:
                        logger.debug("Stale release reconciliation failed: %s", exc)

                # task-52: expire past-due Torbox manual pickups (never-expire stays).
                if now - last_pickup_sweep >= 300:
                    try:
                        expired_count = self.sweep_torbox_manual_pickups()
                        if expired_count:
                            logger.info("Expired %s Torbox manual pickup(s).", expired_count)
                    except Exception as exc:
                        logger.debug("Torbox manual pickup sweep failed: %s", exc)
                    last_pickup_sweep = time.time()

                # Activity-log retention: once at startup, then at most every 6 hours.
                if now - last_activity_prune >= 21600:
                    try:
                        self.prune_activity_logs()
                    except Exception as exc:
                        logger.debug("Activity log prune failed: %s", exc)
                    last_activity_prune = time.time()

                # task-30: frequent (45 s) inventory + routing pass so finished files
                # are visible and eligible for staging without pressing anything.
                if now - last_dedupe_inventory >= 45:
                    try:
                        self.periodic_downloads_inventory_and_route()
                    except Exception as exc:
                        logger.debug("Frequent inventory/routing pass failed: %s", exc)
                    last_dedupe_inventory = time.time()

                # Check and optimize bundle downloads every 30 seconds
                if now - last_dl_check >= 30:
                    self.check_and_filter_active_downloads()
                    last_dl_check = time.time()

                # Retry duplicates suppressed by in-flight dedupe (light, batch-capped).
                if now - last_dedupe_sweep >= 60:
                    try:
                        self.sweep_pending_dedupe()
                    except Exception as exc:
                        logger.debug("Pending-dedupe sweep failed: %s", exc)
                    last_dedupe_sweep = time.time()

            except Exception as e:
                logger.error(f"Error in background worker loop: {e}")

            time.sleep(10)

        logger.info("MangaTrax background worker loop stopped.")

    def start(self):
        # Do NOT run the legacy re-key repair (or any Calibre work) synchronously
        # here: this method is called from the FastAPI lifespan before it yields, so
        # blocking here makes the web UI unavailable.  The worker thread performs
        # perform_initial_sync() as its first action instead.
        with self._lock:
            if self.running:
                return
            self.running = True
            self._torbox_poll_stop.clear()
            self._torbox_poll_wakeup.clear()
            self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="MangaTraxScheduler")
            self.thread.start()
            if not (self._torbox_poll_thread and self._torbox_poll_thread.is_alive()):
                self._torbox_poll_thread = threading.Thread(
                    target=self._torbox_poller_loop, daemon=True, name="MangaTraxTorboxPoller",
                )
                self._torbox_poll_thread.start()

    def stop(self):
        with self._lock:
            self.running = False
            self._torbox_poll_stop.set()
            self._torbox_poll_wakeup.set()
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=2.0)
            if self._torbox_poll_thread and self._torbox_poll_thread.is_alive():
                self._torbox_poll_thread.join(timeout=2.0)
            if self._torbox_fetch_executor is not None:
                try:
                    self._torbox_fetch_executor.shutdown(wait=False)
                except Exception:
                    pass
                self._torbox_fetch_executor = None
