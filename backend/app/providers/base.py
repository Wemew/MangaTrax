import hashlib
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict, Any

class ReleaseItem:
    def __init__(
        self,
        title: str,
        link: str,
        download_url: Optional[str] = None,
        series_name: Optional[str] = None,
        is_bundle: bool = False,
        volume_start: Optional[float] = None,
        volume_end: Optional[float] = None,
        volumes: Optional[List[float]] = None,
        pub_date: Optional[str] = None,
        cover_url: Optional[str] = None,
        description: Optional[str] = None,
        guid: Optional[str] = None,
        source_feed_name: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None
    ):
        self.title = title.strip()
        self.link = link.strip()
        self.download_url = download_url.strip() if download_url else self.link
        self.series_name = series_name
        self.is_bundle = is_bundle
        self.volume_start = volume_start
        self.volume_end = volume_end
        self.volumes = volumes or []
        self.pub_date = pub_date or datetime.utcnow().isoformat()
        self.cover_url = cover_url
        self.description = description or ""
        self.source_feed_name = source_feed_name or ""
        self.raw_data = raw_data or {}

        # Consistent GUID if not provided
        if not guid:
            unique_str = f"{self.title}:{self.series_name}:{self.volume_start}:{self.volume_end}:{self.download_url}"
            self.guid = hashlib.sha256(unique_str.encode("utf-8")).hexdigest()[:16]
        else:
            self.guid = guid

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guid": self.guid,
            "title": self.title,
            "link": self.link,
            "download_url": self.download_url,
            "series_name": self.series_name,
            "is_bundle": self.is_bundle,
            "volume_start": self.volume_start,
            "volume_end": self.volume_end,
            "volumes": self.volumes,
            "pub_date": self.pub_date,
            "cover_url": self.cover_url,
            "description": self.description,
            "source_feed_name": self.source_feed_name,
            "raw_data": self.raw_data
        }

class BaseFeedProvider(ABC):
    def __init__(self, name: str, url: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.url = url
        self.config = config or {}

    @abstractmethod
    def fetch_releases(self) -> List[ReleaseItem]:
        """Fetch and return list of ReleaseItem instances from the feed."""
        pass
