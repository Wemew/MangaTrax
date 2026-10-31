from typing import List, Dict, Any, Optional, Set
from app.parser import normalize_text, clean_manga_title, match_manga_series_in_calibre

class MangaCalibreMatcher:
    def __init__(self, calibre_series: List[Dict[str, Any]], volumes_map: Dict[str, Set[float]], metadata_cache: Optional[Dict[str, Dict[str, Any]]] = None):
        self.calibre_series = calibre_series
        self.volumes_map = volumes_map
        self.metadata_cache = metadata_cache or {}

    def find_series(self, series_name: str, alt_names: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Matches a raw or extracted series name to an existing Calibre series.
        Cross-references canonical English titles and Romaji aliases.
        """
        if not series_name:
            return None

        # 1. Direct match
        match = match_manga_series_in_calibre(series_name, self.calibre_series)
        if match:
            return match

        # 2. Check explicit alt names (e.g. English canonical title or Romaji)
        if alt_names:
            for alt in alt_names:
                if alt:
                    alt_match = match_manga_series_in_calibre(alt, self.calibre_series)
                    if alt_match:
                        return alt_match

        # 3. Check metadata cache for English <-> Romaji translation
        clean_key = series_name.strip().lower()
        cached = self.metadata_cache.get(clean_key)
        if cached:
            for field in ["canonical_title", "romaji_title"]:
                candidate = (cached.get(field) or "").strip()
                if candidate and candidate.lower() != clean_key:
                    m = match_manga_series_in_calibre(candidate, self.calibre_series)
                    if m:
                        return m

        return None

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
            norm_name = matched_name.lower()
            owned = self.volumes_map.get(norm_name, set())

            if volumes:
                missing = [v for v in volumes if round(float(v), 2) not in owned]
                if not missing:
                    return {
                        "status": "IN_CALIBRE",
                        "matched_series": matched_series,
                        "missing_volumes": [],
                        "reason": f"All volumes in bundle ({volumes}) already in Calibre library"
                    }
                else:
                    return {
                        "status": "REQUESTED" if auto_request_monitored else "NEW",
                        "matched_series": matched_series,
                        "missing_volumes": missing,
                        "reason": f"Bundle provides missing volumes: {missing} (Owned in Calibre: {len(owned)} volumes)"
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
            if res["status"] == "IN_CALIBRE":
                resolved.append({
                    "release_id": req["id"],
                    "title": req["title"],
                    "series_name": req.get("series_name"),
                    "reason": res["reason"]
                })
        return resolved
