"""Shared provider-tag helpers: storage normalisation + per-view display policy."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

DEFAULT_LIMITS = {
    "discovery": 5,
    "series": 4,
    "releases": 3,
    "dashboard": 3,
}


def normalise_tag(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def tags_text_for(tags: List[Dict[str, Any]]) -> str:
    names = []
    for tag in tags or []:
        norm = normalise_tag(tag.get("name") if isinstance(tag, dict) else tag)
        if norm and norm not in names:
            names.append(norm)
    return "|" + "|".join(names) + "|" if names else ""


def tags_from_metadata(meta: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Provider metadata -> ordered display/filter tags (genres first, spoilers kept!)."""
    meta = meta or {}
    ordered: List[Dict[str, Any]] = []
    seen = set()
    for genre in meta.get("genres") or []:
        name = str(genre or "").strip()
        norm = normalise_tag(name)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        ordered.append({"name": name, "category": "Genre", "rank": 100000, "spoiler": False})
    raw_tags = meta.get("tags") or []
    if isinstance(raw_tags, str):
        try:
            raw_tags = json.loads(raw_tags)
        except (TypeError, ValueError):
            raw_tags = []
    for tag in raw_tags:
        if not isinstance(tag, dict):
            continue
        name = str(tag.get("name") or "").strip()
        norm = normalise_tag(name)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        ordered.append({
            "name": name,
            "category": str(tag.get("category") or ""),
            "rank": int(tag.get("rank") or 0),
            "spoiler": bool(tag.get("spoiler")),
        })
    return ordered


def encode_tags(tags: List[Dict[str, Any]]) -> Dict[str, str]:
    return {"tags_json": json.dumps(tags or []), "tags_text": tags_text_for(tags or [])}


def decode_tags(raw_json: Any, raw_text: Optional[str] = None) -> List[Dict[str, Any]]:
    tags: List[Dict[str, Any]] = []
    try:
        parsed = json.loads(raw_json or "[]") if not isinstance(raw_json, list) else raw_json
        if isinstance(parsed, list):
            tags = [tag for tag in parsed if isinstance(tag, dict)]
    except (TypeError, ValueError):
        tags = []
    if not tags and raw_text:
        for value in str(raw_text).strip("|").split("|"):
            if value.strip():
                tags.append({"name": value.strip(), "category": "", "rank": 0, "spoiler": False})
    return tags


def display_tags(tags: Any, limit: Optional[int] = None, view: str = "releases") -> Dict[str, Any]:
    """Top-N chips for a view: genres first, then rank order; spoilers hidden."""
    if limit is None:
        limit = DEFAULT_LIMITS.get(view, 3)
    ordered = tags if isinstance(tags, list) else decode_tags(tags)
    visible = [tag for tag in ordered if isinstance(tag, dict) and not tag.get("spoiler")]
    visible.sort(key=lambda tag: (
        0 if str(tag.get("category") or "").lower() == "genre" else 1,
        -int(tag.get("rank") or 0),
    ))
    deduped = []
    seen = set()
    for tag in visible:
        norm = normalise_tag(tag.get("name"))
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(tag["name"])
    return {
        "chips": deduped[:limit],
        "overflow": max(0, len(deduped) - limit),
        "all": deduped,
        "tooltip": ", ".join(deduped),
    }
