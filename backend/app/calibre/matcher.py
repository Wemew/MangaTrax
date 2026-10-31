from typing import List, Dict, Any, Optional, Set
from app.parser import normalize_text, clean_manga_title, match_manga_series_in_calibre

class MangaCalibreMatcher:
    def __init__(self, calibre_series: List[Dict[str, Any]], volumes_map: Dict[str, Set[float]]):
        self.calibre_series = calibre_series
        self.volumes_map = volumes_map

    def check_release(self, release: Dict[str, Any], auto_request_monitored: bool = True) -> Dict[str, Any]:
        """
        Evaluates a single manga volume or multi-volume bundle against Calibre.
        """
        series_name = release.get("series_name")
        volumes = release.get("volumes") or []
        is_bundle = release.get("is_bundle", False)
        title = release.get("title", "")

        matched_series = None
        if series_name:
            matched_series = match_manga_series_in_calibre(series_name, self.calibre_series)

        if matched_series:
            norm_name = matched_series["series_name"].lower()
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
                    "reason": f"Release detected for monitored series '{matched_series['series_name']}'"
                }

        # Not in Calibre
        # If Volume 1 or unnumbered -> DISCOVERY candidate
        if volumes == [1.0] or not volumes:
            return {
                "status": "DISCOVERY",
                "matched_series": None,
                "missing_volumes": volumes,
                "reason": "Potential new manga series (Vol. 1 or standalone)"
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
