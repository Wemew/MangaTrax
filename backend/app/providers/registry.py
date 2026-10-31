import json
from typing import Dict, Any, Optional
from app.providers.base import BaseFeedProvider
from app.providers.rss import RSSFeedProvider
from app.providers.html_scraper import HtmlScraperProvider

PROVIDER_CLASSES = {
    "rss": RSSFeedProvider,
    "html": HtmlScraperProvider,
    "html_scraper": HtmlScraperProvider,
}

def create_provider(feed_dict: Dict[str, Any]) -> Optional[BaseFeedProvider]:
    feed_type = feed_dict.get("feed_type", "").lower()
    cls = PROVIDER_CLASSES.get(feed_type)
    if not cls:
        return None

    config = {}
    config_raw = feed_dict.get("config_json")
    if config_raw:
        try:
            if isinstance(config_raw, str):
                config = json.loads(config_raw)
            elif isinstance(config_raw, dict):
                config = config_raw
        except Exception:
            config = {}

    return cls(
        name=feed_dict.get("name", "Unknown Feed"),
        url=feed_dict.get("url", ""),
        config=config
    )
