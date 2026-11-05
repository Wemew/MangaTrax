import json
from typing import List, Dict, Any, Optional, Set
from app.parser import (
    CalibreSeriesIndex,
    normalize_text,
    match_manga_series_in_calibre,
    split_series_candidate_parts,
)

class MangaCalibreMatcher:
    def __init__(self, calibre_series: List[Dict[str, Any]], volumes_map: Dict[str, Set[float]], metadata_cache: Optional[Dict[str, Dict[str, Any]]] = None, staged_volumes_map: Optional[Dict[str, Set[float]]] = None):
        self.calibre_series = calibre_series
        self.volumes_map = volumes_map
        self.metadata_cache = metadata_cache or {}
        # task-61: volumes present in staging count as owned for wanted/missing.
        self.staged_volumes_map = staged_volumes_map or {}
        # Build the normalised lookup index ONCE per matcher (i.e. once per Calibre
        # sync / feed poll) instead of re-normalising the whole library for every
        # release.  Without this the reconcile loop benchmarked at ~40-120 ms per
        # release against a 3000-series library.
        self._index = CalibreSeriesIndex(calibre_series)

    def _match_series(self, series_name: str) -> Optional[Dict[str, Any]]:
        """Index-backed match (exact -> substring -> fuzzy), same rules as the parser."""
        if not series_name:
            return None
        return self._index.match(series_name)

    def find_series(self, series_name: str, alt_names: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Matches a raw or extracted series name to an existing Calibre series.
        Cross-references canonical English titles and Romaji aliases.
        """
        if not series_name:
            return None

        # 1. Direct match
        match = self._match_series(series_name)
        if match:
            return match

        # 2. Check explicit alt names (e.g. English canonical title or Romaji)
        if alt_names:
            for alt in alt_names:
                if alt:
                    alt_match = self._match_series(alt)
                    if alt_match:
                        return alt_match

        # 3. Check metadata cache for English <-> Romaji translation
        clean_key = series_name.strip().lower()
        cached = self.metadata_cache.get(clean_key)
        if cached:
            for field in ["canonical_title", "romaji_title"]:
                candidate = (cached.get(field) or "").strip()
                if candidate and candidate.lower() != clean_key:
                    m = self._match_series(candidate)
                    if m:
                        return m

        return None

    def get_owned_volumes(self, matched_series: Dict[str, Any], alt_names: Optional[List[str]] = None) -> Set[float]:
        """Retrieves all owned volume numbers for a matched Calibre series across all indexing keys."""
        owned: Set[float] = set()
        keys_to_try = []

        # 1. By Calibre series ID
        s_id = matched_series.get("series_id") or matched_series.get("id") or matched_series.get("calibre_series_id")
        if s_id is not None:
            keys_to_try.extend([str(s_id), s_id])

        # 2. By series names
        s_name = matched_series.get("series_name") or matched_series.get("name") or ""
        if s_name:
            keys_to_try.extend([s_name.lower(), normalize_text(s_name)])
            parts = split_series_candidate_parts(s_name)
            if len(parts) > 1:
                for p in parts:
                    keys_to_try.extend([p.lower(), normalize_text(p)])

        # 3. By alt names
        if alt_names:
            for alt in alt_names:
                if alt:
                    keys_to_try.extend([alt.lower(), normalize_text(alt)])
                    alt_parts = split_series_candidate_parts(alt)
                    if len(alt_parts) > 1:
                        for ap in alt_parts:
                            keys_to_try.extend([ap.lower(), normalize_text(ap)])

        for k in keys_to_try:
            v_set = self.volumes_map.get(k)
            if v_set:
                owned.update(v_set)

        # 4. If matched_series already carries owned_volumes_json from database
        if matched_series.get("owned_volumes_json"):
            try:
                ov = json.loads(matched_series["owned_volumes_json"])
                owned.update([float(v) for v in ov])
            except Exception:
                pass

        return owned

    def get_staged_volumes(self, matched_series: Dict[str, Any], alt_names: Optional[List[str]] = None) -> Set[float]:
        """Staged volumes for a matched series (alias/normalised keys), never Calibre-only."""
        if not self.staged_volumes_map:
            return set()
        names = []
        s_name = matched_series.get("series_name") or matched_series.get("name") or ""
        if s_name:
            names.append(s_name)
            names.extend(split_series_candidate_parts(s_name))
        for alt in alt_names or []:
            if alt:
                names.append(alt)
                names.extend(split_series_candidate_parts(alt))
        volumes: Set[float] = set()
        for name in names:
            clean = normalize_text(name)
            if clean:
                volumes.update(self.staged_volumes_map.get(clean, set()))
        return volumes

    def get_combined_owned_volumes(self, matched_series: Dict[str, Any], alt_names: Optional[List[str]] = None) -> Set[float]:
        """Calibre-owned ∪ staged-owned volumes (acquisition semantics)."""
        return set(self.get_owned_volumes(matched_series, alt_names=alt_names)) | set(
            self.get_staged_volumes(matched_series, alt_names=alt_names)
        )

    def check_release(self, release: Dict[str, Any], auto_request_monitored: bool = True, alt_series_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Evaluates a single manga volume or multi-volume bundle against Calibre.
        """
        series_name = release.get("series_name")
        volumes = release.get("volumes") or []
        is_bundle = release.get("is_bundle", False)
        title = release.get("title", "")

        matched_series = None
        if series_name:
            matched_series = self.find_series(series_name, alt_names=alt_series_names)

        if matched_series:
            matched_name = matched_series.get("series_name") or matched_series.get("name", "")
            matched_series["series_name"] = matched_name
            owned = self.get_owned_volumes(matched_series, alt_names=alt_series_names)
            staged = self.get_staged_volumes(matched_series, alt_names=alt_series_names)
            combined = set(owned) | set(staged)

            if volumes:
                missing = [v for v in volumes if round(float(v), 2) not in combined]
                if not missing:
                    all_in_calibre = all(round(float(v), 2) in owned for v in volumes)
                    if all_in_calibre:
                        return {
                            "status": "IN_CALIBRE",
                            "matched_series": matched_series,
                            "missing_volumes": [],
                            "reason": f"All volumes in bundle ({volumes}) already in Calibre library"
                        }
                    return {
                        "status": "IN_STAGING",
                        "matched_series": matched_series,
                        "missing_volumes": [],
                        "reason": (
                            f"All volumes in bundle ({volumes}) already staged, waiting for "
                            "Calibre import"
                        )
                    }
                else:
                    staged_missing = [v for v in missing if round(float(v), 2) in staged]
                    reason = f"Bundle provides missing volumes: {missing}"
                    if owned:
                        reason += f" (Owned in Calibre: {len(owned)} volumes)"
                    if staged_missing:
                        reason += f" (staged/awaiting import: {staged_missing})"
                    return {
                        "status": "REQUESTED" if auto_request_monitored else "NEW",
                        "matched_series": matched_series,
                        "missing_volumes": missing,
                        "reason": reason
                    }
            else:
                return {
                    "status": "REQUESTED" if auto_request_monitored else "NEW",
                    "matched_series": matched_series,
                    "missing_volumes": [],
                    "reason": f"Release detected for monitored series '{matched_name}'"
                }

        # Not in Calibre
        # If Volume 1, complete compilation, or unnumbered -> DISCOVERY candidate
        if (
            volumes == [1.0]
            or not volumes
            or release.get("is_complete")
            or (release.get("volume_start") == 1.0 and is_bundle)
        ):
            return {
                "status": "DISCOVERY",
                "matched_series": None,
                "missing_volumes": volumes,
                "reason": "Potential new manga series (Vol. 1, complete compilation, or standalone)"
            }

        return {
            "status": "NEW",
            "matched_series": None,
            "missing_volumes": volumes,
            "reason": "Not in Calibre library and not currently tracked"
        }

    def resolve_pending_requests(self, pending_requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Checks if previously requested volumes have now been added to Calibre."""
        resolved = []
        for req in pending_requests:
            res = self.check_release(req, auto_request_monitored=True)
            if res["status"] in ("IN_CALIBRE", "IN_STAGING"):
                resolved.append({
                    "release_id": req["id"],
                    "title": req["title"],
                    "series_name": req.get("series_name"),
                    "status": res["status"],
                    "reason": res["reason"]
                })
        return resolved
