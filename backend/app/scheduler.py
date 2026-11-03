import time
import logging
import os
import threading
import re
import json
import shutil
from datetime import datetime
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
from app.downloader import as_bool, build_downloader, resolve_downloader_engine
from app.downloader.base import (
    DownloaderAuthError,
    DownloaderError,
    DownloaderFileError,
    DownloaderNotFoundError,
    DownloaderRateLimitError,
    DownloaderSlotError,
)
from app.tagger.cbz_tagger import auto_tag_staging_cbz_files
from app.providers.registry import create_provider
from app.providers.nyaa import NyaaMangaProvider
from app.providers.base import ProviderFetchError
from app.metadata.anilist import AniListMetadataService
from app.metadata.mangaupdates import MangaUpdatesService
from app.parser import (
    is_chapter_only,
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
        self._feed_check_state: Dict[str, Any] = {
            "running": False,
            "started_at": None,
            "finished_at": None,
            "result": None,
        }

    def _log_activity(self, event_type: str, message: str, details: str = ""):
        """Audit helper for background work: always stamps ``actor='scheduler'``.

        The scheduler thread has no HTTP request context, so without an explicit
        actor every automated event would be indistinguishable from a user
        action in the audit trail.
        """
        self.db.log_activity(event_type, message, details, actor="scheduler")

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
            series_list, volumes_map, self.db.get_all_manga_metadata_cache()
        )
        return series_list, matcher

    # --- Marker-less chapter guard ---------------------------------------
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
        variants = self._name_variants(raw)

        # Metadata is resolved up-front (and cached) exactly as before: it still
        # drives covers / volume counts / display, it just never drives the key.
        if meta is None:
            try:
                meta = self.anilist.resolve_manga_series(raw)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Metadata lookup failed for '%s': %s", raw, exc)
                meta = {}
        meta = meta or {}
        authoritative = is_authoritative_metadata(meta)
        canonical = (meta.get("canonical_title") or "").strip() if authoritative else ""
        romaji = (meta.get("romaji_title") or "").strip()

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
            display = display_title_from_meta(stable, meta if authoritative else None) or stable
            alt_names: List[str] = []
            seen = {normalize_text(stable)}
            for value in list(variants) + ([canonical] if canonical else []) + ([romaji] if romaji else []):
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
                "calibre_match": None,
            }

        # (1) Calibre-tracked name: the parsed name directly names a Calibre series.
        for candidate in variants:
            hit = _match_calibre(candidate)
            if hit:
                return _make(hit, "calibre")

        # (1b) Calibre-origin tracked rows: covers deployments where the scanner is
        #      temporarily unavailable but the library was already synced to the DB.
        for candidate in variants:
            hit = calibre_tracked.get(normalize_text(candidate))
            if hit:
                return _make(hit, "calibre_db")

        # (2) user-typed tracked name.
        for candidate in variants:
            hit = typed_tracked.get(normalize_text(candidate))
            if hit:
                return _make(hit, "typed")

        # (3) AniList canonical English, then (4) AniList romaji, as lookup bridges.
        variant_norms = {normalize_text(v) for v in variants}
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
        for candidate in variants:
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

    def submit_torbox_download(
        self,
        release_id: int,
        title: str,
        download_url: str,
        link: str = "",
        missing_volumes: Optional[List[float]] = None,
        source: str = "",
    ) -> Dict[str, Any]:
        """Submit a release to Torbox and persist its handle (used by all entry points)."""
        engine = "torbox"
        try:
            max_concurrent = int(self.db.get_setting("torbox_max_concurrent", "2") or 2)
        except ValueError:
            max_concurrent = 2
        active = self.db.get_active_torbox_downloads()
        if len(active) >= max(1, max_concurrent):
            message = (
                f"Torbox concurrency limit reached ({max_concurrent} active download(s)); "
                f"'{title}' was not submitted."
            )
            logger.warning(message)
            self._log_activity("TORBOX_FAILED", message, f"torbox_max_concurrent={max_concurrent}")
            return {"success": False, "engine": engine, "message": message, "retryable": True}

        downloader = build_downloader(self.db, engine=engine)
        staging_dir = self.db.get_setting("staging_dir", "/staging")
        savepath = os.path.join(staging_dir, self._safe_torrent_dirname(title))
        release = {"download_url": download_url, "link": link, "title": title}
        wanted = [float(v) for v in (missing_volumes or [])]
        try:
            result = downloader.submit(release, savepath, wanted_volumes=wanted)
        except DownloaderError as exc:
            message = f"Torbox submit failed for '{title}': {exc}"
            logger.warning(message)
            self._log_activity("TORBOX_FAILED", message, f"Source: {source}" if source else "")
            return {"success": False, "engine": engine, "message": str(exc), "retryable": True}

        handle = result.get("handle") or {}
        self.db.update_download_handle(
            release_id,
            engine,
            downloader_id=str(handle.get("torrent_id") or ""),
            torrent_hash=handle.get("hash"),
        )
        cached = bool(result.get("cached"))
        event = "TORBOX_CACHED" if cached else "TORBOX_SUBMITTED"
        detail_parts = [
            f"torrent_id={handle.get('torrent_id')}",
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
        return {"success": True, "engine": engine, "handle": handle, "cached": cached}

    # --- Torbox polling worker (task-20, Phase 1+2) ---
    def _torbox_fail_release(self, release_id: int, title: str, message: str) -> None:
        """Mark a Torbox release FAILED and audit it (surfaced, not silently retried)."""
        self.db.update_downloader_state(release_id, "FAILED", "torbox")
        self._log_activity("TORBOX_FAILED", f"Torbox download failed: '{title}'", message)
        logger.warning("Torbox download failed for '%s': %s", title, message)

    def _torbox_surface_once(self, release_id: int, message: str, title: str) -> None:
        """Audit a non-fatal Torbox problem once per distinct message (no log spam)."""
        previous = self._torbox_notified.get(release_id)
        if previous == message:
            return
        self._torbox_notified[release_id] = message
        self._log_activity("TORBOX_FAILED", f"Torbox download issue: '{title}'", message)
        logger.warning("Torbox download issue for '%s': %s", title, message)

    def _select_torbox_files(self, release: Dict[str, Any], files: List[Dict[str, Any]], wanted_volumes: List[float]):
        """Choose the Torbox files for a volume subset.

        Returns ``(selection, reason)`` where ``selection is None`` means "download the
        whole torrent as a zip".  If ANY file name cannot be mapped to volumes, the
        whole torrent is downloaded (logged) instead of risking a missing volume.
        """
        if not wanted_volumes:
            return None, "all volumes wanted: whole-torrent zip"
        if not files:
            return None, "Torbox returned no file list: whole-torrent zip fallback"
        wanted = {float(value) for value in wanted_volumes}
        file_volumes: Dict[int, Set[float]] = {}
        unmapped: List[str] = []
        for index, item in enumerate(files):
            name = str((item or {}).get("name") or "")
            try:
                parsed = extract_manga_bundle_and_volumes(name)
                volumes = {float(value) for value in (parsed.get("volumes") or [])}
                if not volumes and parsed.get("volume_start") is not None:
                    volumes = {float(parsed["volume_start"])}
            except Exception:
                volumes = set()
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

    def poll_torbox_downloads(self) -> Dict[str, Any]:
        """Advance Torbox releases: poll → fetch when ready → complete → optional delete.

        Bounded by ``torbox_max_concurrent`` at submission time and by
        ``torbox_timeout_hours`` here; stalled/no-seed torrents are surfaced as
        activity events instead of being retried silently forever.
        """
        downloader = build_downloader(self.db, engine="torbox")
        if not getattr(downloader, "enabled", False):
            return {"status": "disabled", "processed": 0, "completed": 0, "failed": 0}

        try:
            timeout_hours = float(self.db.get_setting("torbox_timeout_hours", "6") or 6)
        except ValueError:
            timeout_hours = 6.0
        rows = self.db.get_active_torbox_downloads()
        if not rows:
            return {"status": "idle", "processed": 0, "completed": 0, "failed": 0}

        staging_dir = self.db.get_setting("staging_dir", "/staging")
        delete_after = as_bool(self.db.get_setting("torbox_delete_after_download", "true"), default=True)
        processed = completed = failed = 0
        rate_limited = False

        for release in rows:
            release_id = int(release.get("id"))
            title = str(release.get("title") or "")
            started = self._parse_db_timestamp(release.get("downloader_started_at"))
            if started is not None and timeout_hours > 0:
                age_hours = (datetime.utcnow() - started).total_seconds() / 3600.0
                if age_hours > timeout_hours:
                    self._torbox_fail_release(
                        release_id, title,
                        f"Timed out after {age_hours:.1f}h (limit {timeout_hours:g}h); "
                        "the release stays REQUESTED for a retry.",
                    )
                    failed += 1
                    continue

            handle = {
                "torrent_id": int(release.get("downloader_id") or 0),
                "hash": str(release.get("torrent_hash") or ""),
            }
            if not handle["torrent_id"]:
                self._torbox_fail_release(release_id, title, "Missing Torbox torrent_id on the release row.")
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
            if state == "failed":
                self._torbox_fail_release(
                    release_id, title,
                    f"Torbox reported state '{info.get('raw_state') or 'failed'}'.",
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
            if state != "ready":
                continue

            files = info.get("files") or []
            wanted = self._release_missing_volumes(release)
            selection, reason = self._select_torbox_files(release, files, wanted)
            dest_dir = os.path.join(staging_dir, self._safe_torrent_dirname(title))
            try:
                saved = downloader.fetch_files(handle, selection, dest_dir)
            except (DownloaderFileError, DownloaderError) as exc:
                self._torbox_fail_release(release_id, title, f"Fetch failed ({reason}): {exc}")
                failed += 1
                continue

            self.db.update_downloader_state(release_id, "COMPLETED", "torbox")
            try:
                rel_row = self.db.get_release_by_id(release_id)
                if rel_row:
                    self.get_notifier().notify_acquired(rel_row)
            except Exception as e:
                logger.debug(f"Webhook notification failed for acquired release: {e}")
            detail = f"Saved {len(saved)} file(s) to {dest_dir} ({reason})."
            deleted = ""
            if delete_after:
                deleted = " Torbox torrent deleted." if downloader.cancel(handle) else " Torbox delete failed."
            self._log_activity("TORBOX_DOWNLOADED", f"Torbox download complete: '{title}'", detail + deleted)
            completed += 1
            self._torbox_notified.pop(release_id, None)

        return {
            "status": "rate_limited" if rate_limited else "ok",
            "processed": processed,
            "completed": completed,
            "failed": failed,
        }


    # --- Calibre Sync Task ---
    def sync_calibre_library(self) -> Dict[str, Any]:
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
                series_list = scanner.get_all_series()
                volumes_map = scanner.get_series_volumes_map()
                synced_count = self.db.sync_calibre_series(series_list, volumes_map=volumes_map)

                # Build matcher
                meta_cache = self.db.get_all_manga_metadata_cache()
                matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

                # Check pending requests against Calibre
                pending = self.db.get_pending_requests()
                resolved = matcher.resolve_pending_requests(pending)

                notifier = self.get_notifier()
                for item in resolved:
                    # Automated write: the precedence model decides (promotion to
                    # IN_CALIBRE is allowed, a downgrade is not).
                    self.db.update_release_status(item["release_id"], "IN_CALIBRE", enforce_precedence=True)
                    self._log_activity(
                        "REQUEST_RESOLVED",
                        f"Resolved: '{item['title']}' is now available in your Calibre library.",
                        item["reason"]
                    )
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

                summary = f"Synced {synced_count} manga series from Calibre; auto-resolved {len(resolved)} acquired requests."
                logger.info(summary)
                self._log_activity("CALIBRE_SYNC", summary)
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
    ) -> Dict[str, Any]:
        """Uniform feed-poll result (counts + per-feed failure detail)."""
        return {
            "success": success,
            "message": message,
            "releases_found": releases_found,
            "requested": requested,
            "sent_to_downloader": sent_to_downloader,
            "feeds_failed": feeds_failed,
            "errors": list(errors or []),
            "skipped": skipped,
        }

    def check_all_feeds(self) -> Dict[str, Any]:
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
            return self._check_all_feeds_locked()
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
            result = self.check_all_feeds()
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
        matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

        # Marker-less chapter guard + numbered-series context: loaded once per
        # feed-poll pass.  The guard discards a chapter only when its clean series
        # name is one we already know (Calibre library or tracked row); the same
        # context keeps a real numbered series name ("Mob Psycho 100") as the
        # matching key when a chapter release is retained.
        discard_unmarked_chapters = self._discard_unmarked_chapters_enabled()
        known_series_keys = self._known_series_keys(matcher)

        global_auto_download = self.db.get_setting("auto_download", "true").lower() == "true"
        staging_dir = self.db.get_setting("staging_dir", "/staging")
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
                    # Discard pure chapter updates and light novels.  Explicit chapter
                    # markers ("Ch1-50", "Chapter 180", "#12") are always discarded
                    # (existing policy); chapter RANGES of a known series are audited
                    # like the context-guarded marker-less path, single chapters keep
                    # their previous silent path.
                    if is_chapter_only(rel.title):
                        range_reason = self._explicit_chapter_range_reason(rel, known_series_keys)
                        if range_reason:
                            self._log_activity(
                                "CHAPTER_DISCARDED",
                                f"Discarded chapter range release: '{rel.title}'",
                                range_reason,
                            )
                        continue
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

                        if (is_new or is_transition) and global_auto_download and feed.get("auto_download"):
                            if downloader_engine == "torbox":
                                # Torbox engine: submit + persist handle; the poll task
                                # fetches files (volume-scoped) into staging.
                                result = self.submit_torbox_download(
                                    rel_id,
                                    rel.title,
                                    rel.download_url,
                                    link=rel.link,
                                    missing_volumes=[float(v) for v in (missing_vols or [])],
                                    source=feed_name,
                                )
                                if result.get("success"):
                                    total_sent_to_downloader += 1
                            else:
                                success = qbit.add_torrent(
                                    url_or_magnet=rel.download_url,
                                    savepath=staging_dir,
                                    category="manga",
                                    tags="mangatrax"
                                )
                                if success:
                                    total_sent_to_downloader += 1
                                    torrent_h = qbit.extract_torrent_hash(rel.download_url) if hasattr(qbit, "extract_torrent_hash") else None
                                    if not isinstance(torrent_h, str):
                                        torrent_h = None
                                    self.db.update_download_handle(rel_id, "qbit", downloader_id=torrent_h or "", torrent_hash=torrent_h)
                                    self._log_activity(
                                        "AUTO_DOWNLOAD",
                                        f"Sent to downloader: '{rel.title}' (Missing: {missing_vols})",
                                        f"Source: {feed_name}"
                                    )
                                    if torrent_h and rel.is_bundle and missing_vols and len(missing_vols) < len(rel.volumes):
                                        sel_res = qbit.apply_selective_volume_download(torrent_h, [float(v) for v in missing_vols])
                                        if sel_res.get("status") == "filtered":
                                            self._log_activity(
                                                "SELECTIVE_DOWNLOAD",
                                                f"Selective download applied for '{rel.title}': skipped {len(sel_res.get('skipped_volumes', []))} owned volumes; downloading {len(sel_res.get('kept_volumes', []))} missing volumes."
                                            )

                self.db.update_feed_status(feed_id, "OK", None)
            except Exception as e:
                err_msg = str(e)
                feeds_failed += 1
                feed_errors.append({"feed": feed_name, "error": err_msg})
                logger.error(f"Error checking feed '{feed_name}': {err_msg}")
                self.db.update_feed_status(feed_id, "ERROR", err_msg)
                self._log_activity("FEED_ERROR", f"Feed error in '{feed_name}': {err_msg}")

        summary = f"Feeds checked: {total_found} releases found, {total_requested} requested, {total_sent_to_downloader} sent to downloader."
        if feeds_failed:
            # Never report a failed provider lookup as a silent success.
            detail = "; ".join(f"{item['feed']}: {item['error']}" for item in feed_errors[:5])
            summary += f" {feeds_failed} feed(s) failed: {detail}"
        logger.info(summary)
        self._log_activity(
            "FEED_POLL_COMPLETED",
            summary,
            f"feeds_failed={feeds_failed}; errors={feed_errors}",
        )
        return self._feed_check_payload(
            success=feeds_failed == 0,
            message=summary,
            releases_found=total_found,
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
        if is_chapter_only(rel.title):
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
            "status": status,
            "download_status": "IDLE"
        }

        is_new, rel_id = self.db.upsert_release(rel_dict)
        return (is_new, status, missing_vols, rel_id, series_key)

    def detect_series_titles(self, series_name: str) -> List[str]:
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

        # 3. Inspect existing releases in database under this series
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                prefix = clean_name[:25].strip()
                from app.security import like_contains
                prefix_pattern = like_contains(prefix)
                cur.execute("""
                    SELECT title, series_name FROM releases 
                    WHERE lower(series_name) = lower(?)
                       OR lower(series_name) LIKE ? ESCAPE '\\'
                       OR lower(title) LIKE ? ESCAPE '\\'
                    LIMIT 20;
                """, (clean_name, prefix_pattern, prefix_pattern))
                for row in cur.fetchall():
                    for field in [row["series_name"], row["title"]]:
                        if not field:
                            continue
                        sub_parts = split_series_candidate_parts(field)
                        if len(sub_parts) > 1:
                            candidates.extend(sub_parts)
        except Exception as e:
            logger.debug(f"Failed to inspect DB releases for dual titles: {e}")

        # 4. Check AniList metadata cache or query AniList
        initial_check = list(candidates) if candidates else [clean_name]
        for term in initial_check[:3]:
            try:
                meta = self.anilist.resolve_manga_series(term)
                if meta:
                    canon = (meta.get("canonical_title") or meta.get("english_title") or "").strip()
                    romaji = (meta.get("romaji_title") or "").strip()
                    if canon:
                        candidates.append(canon)
                    if romaji:
                        candidates.append(romaji)
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

    def backfill_series(self, series_name: str, max_pages: int = 3) -> Dict[str, Any]:
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
        self.db.set_setting("backfill_progress", f"Backfilling {title_display} on Nyaa (up to {max_pages} pages each)...")
        logger.info(f"Starting targeted backfill for {title_display} on Nyaa (max_pages={max_pages})")

        # 2. Build Calibre matcher context
        calibre_path = self.db.get_setting("calibre_path", "/calibre")
        scanner = CalibreMangaScanner(calibre_path)
        series_list = scanner.get_all_series() if scanner.is_available() else self.db.get_all_series()
        volumes_map = scanner.get_series_volumes_map() if scanner.is_available() else {}
        meta_cache = self.db.get_all_manga_metadata_cache()
        matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

        # Context for the whole backfill run: the chapter guard and the
        # numbered-series key protection both need the known-series set.
        known_series_keys = self._known_series_keys(matcher)

        global_auto_download = self.db.get_setting("auto_download", "true").lower() == "true"
        staging_dir = self.db.get_setting("staging_dir", "/staging")
        qbit = self.get_qbit_client()
        downloader_engine = self.get_downloader_engine()

        # 3. Query Nyaa targeted series search across primary and alternate titles
        nyaa = NyaaMangaProvider(name=f"Nyaa: {primary_title}", config={"delay_seconds": 1.0, "base_url": self._nyaa_base_url()})
        releases = nyaa.search_series(
            primary_title,
            max_pages=max_pages,
            alt_names=alt_titles,
            stop_check=lambda: self.stop_backfill_requested
        )

        total_found = 0
        total_requested = 0
        total_sent_to_downloader = 0

        for rel in releases:
            if self.stop_backfill_requested:
                break
            is_new, status, missing_vols, rel_id, _ = self._process_release(
                rel, f"Nyaa: {primary_title}", matcher, known_series_keys=known_series_keys
            )
            if status in ("CHAPTER_ONLY", "LIGHT_NOVEL"):
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
                    if downloader_engine == "torbox":
                        result = self.submit_torbox_download(
                            rel_id,
                            rel.title,
                            rel.download_url,
                            link=rel.link,
                            missing_volumes=[float(v) for v in (missing_vols or [])],
                            source=f"Nyaa: {primary_title}",
                        )
                        if result.get("success"):
                            total_sent_to_downloader += 1
                    else:
                        success = qbit.add_torrent(
                            url_or_magnet=rel.download_url,
                            savepath=staging_dir,
                            category="manga",
                            tags="mangatrax"
                        )
                        if success:
                            total_sent_to_downloader += 1
                            torrent_h = qbit.extract_torrent_hash(rel.download_url) if hasattr(qbit, "extract_torrent_hash") else None
                            if not isinstance(torrent_h, str):
                                torrent_h = None
                            self.db.update_download_handle(rel_id, "qbit", downloader_id=torrent_h or "", torrent_hash=torrent_h)
                            if torrent_h and rel.is_bundle and missing_vols and len(missing_vols) < len(rel.volumes):
                                qbit.apply_selective_volume_download(torrent_h, [float(v) for v in missing_vols])

        summary = f"Backfilled {title_display}: found {total_found} releases ({total_requested} wanted/missing volumes, {total_sent_to_downloader} sent to downloader)."
        self._log_activity("SERIES_BACKFILLED", summary)
        return {
            "status": "success",
            "message": summary,
            "series_name": primary_title,
            "searched_titles": detected_titles,
            "releases_found": total_found,
            "requested": total_requested,
            "sent_to_downloader": total_sent_to_downloader
        }

    def run_series_backfill_async(self, series_name: str, max_pages: int = 3) -> Dict[str, Any]:
        """
        Launches targeted series backfill in a background daemon thread,
        allowing the user to navigate pages while tracking live progress globally.
        """
        if self.is_backfilling:
            return {"status": "in_progress", "message": "Another backfill is already running"}

        self.is_backfilling = True
        self.stop_backfill_requested = False

        def _worker():
            self.db.set_setting("backfill_status", "running")
            self.db.set_setting("backfill_progress", f"Backfilling '{series_name}' on Nyaa (up to {max_pages} pages)...")
            try:
                res = self.backfill_series(series_name, max_pages=max_pages)
                self.db.set_setting("backfill_status", "idle")
                self.db.set_setting("backfill_progress", res.get("message", f"Backfill for '{series_name}' complete."))
            except Exception as e:
                logger.error(f"Error in async series backfill: {e}")
                self.db.set_setting("backfill_status", "error")
                self.db.set_setting("backfill_progress", f"Backfill failed: {e}")
            finally:
                self.is_backfilling = False

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return {"status": "started", "message": f"Historical backfill for '{series_name}' started in background."}

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
            matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

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
                    if status in ("CHAPTER_ONLY", "LIGHT_NOVEL"):
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
                SELECT id, title, series_name, torrent_hash, download_url, volumes_json AS volumes, missing_vols_json AS missing_volumes, is_bundle
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

            missing = r.get("missing_volumes")
            if isinstance(missing, str):
                import json
                try:
                    missing = json.loads(missing)
                except Exception:
                    missing = []
            if not missing or not isinstance(missing, list):
                continue

            vols = r.get("volumes")
            if isinstance(vols, str):
                import json
                try:
                    vols = json.loads(vols)
                except Exception:
                    vols = []

            # Only need filtering if we already own some volumes in this bundle
            if vols and len(missing) >= len(vols):
                continue

            res = qbit.apply_selective_volume_download(h, [float(v) for v in missing])
            if res.get("status") == "filtered":
                filtered_count += 1
                skipped_vols = res.get("skipped_volumes", [])
                self._log_activity(
                    "SELECTIVE_DOWNLOAD",
                    f"Optimized torrent for '{r['title']}': downloading {len(res.get('kept_volumes', []))} missing volumes; skipped {len(skipped_vols)} volumes already in Calibre ({skipped_vols}).",
                    f"Torrent Hash: {h[:10]}..."
                )
        return filtered_count

    def expand_unresolved_complete_releases(self, matcher: Optional[MangaCalibreMatcher] = None) -> int:
        """
        Scans releases in the database that are complete series compilations
        but have no parsed volume numbers. Queries AniList/MangaUpdates for total volume count,
        expands into individual volumes [1.0 .. N], and matches against Calibre to calculate
        missing volumes and accurate status (e.g. IN_CALIBRE, REQUESTED, DISCOVERY).
        """
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, guid, title, series_name, volumes_json, missing_vols_json, status, download_url, link, cover_url
                    FROM releases
                    WHERE is_complete = 1 OR lower(title) LIKE '%(complete)%' OR lower(title) LIKE '%[complete]%' OR lower(title) LIKE '%complete series%';
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
            if not v_list or (v_list and len(m_list) == len(v_list)):
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
            matcher = MangaCalibreMatcher(series_list, volumes_map, meta_cache)

        expanded_count = 0
        for r in unexpanded:
            rel_id = r["id"]
            title = r["title"]
            s_name = (r.get("series_name") or "").strip()

            candidates = []
            if s_name:
                candidates.append(s_name)
                detected = self.detect_series_titles(s_name)
                for d in detected:
                    if d not in candidates:
                        candidates.append(d)
            candidates.append(title)

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
        last_torbox_poll = 0
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
                    self.check_all_feeds()
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
                    last_tag_check = time.time()

                # Check and optimize bundle downloads every 30 seconds
                if now - last_dl_check >= 30:
                    self.check_and_filter_active_downloads()
                    last_dl_check = time.time()

                # Advance Torbox downloads (cadence: torbox_poll_seconds, min 5s)
                try:
                    torbox_poll_seconds = float(self.db.get_setting("torbox_poll_seconds", "10") or 10)
                except ValueError:
                    torbox_poll_seconds = 10.0
                torbox_poll_seconds = max(5.0, torbox_poll_seconds)
                if now - last_torbox_poll >= torbox_poll_seconds:
                    self.poll_torbox_downloads()
                    last_torbox_poll = time.time()

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
            self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="MangaTraxScheduler")
            self.thread.start()

    def stop(self):
        with self._lock:
            self.running = False
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=2.0)
