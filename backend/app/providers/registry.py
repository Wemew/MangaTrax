import json
from typing import Dict, Any, Optional
from app.providers.base import BaseFeedProvider
from app.providers.rss import RSSFeedProvider
from app.providers.html_scraper import HtmlScraperProvider
from app.providers.nyaa import NyaaMangaProvider

PROVIDER_CLASSES = {
    "rss": RSSFeedProvider,
    "html": HtmlScraperProvider,
    "html_scraper": HtmlScraperProvider,
    "nyaa": NyaaMangaProvider,
    "nyaa_scraper": NyaaMangaProvider,
    "nyaa_manga": NyaaMangaProvider,
}

def create_provider(feed_dict: Dict[str, Any], nyaa_base_url: Optional[str] = None) -> Optional[BaseFeedProvider]:
    """Build a provider for *feed_dict*.

    ``nyaa_base_url`` is the resolved ``nyaa_base_url`` setting; it is injected as
    the per-feed default so the Nyaa provider (and only it) uses the configured
    mirror.  A feed's own ``config["base_url"]`` still wins.
    """
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
    config = dict(config or {})
    if nyaa_base_url and not config.get("base_url"):
        config["base_url"] = nyaa_base_url

    return cls(
        name=feed_dict.get("name", "Unknown Feed"),
        url=feed_dict.get("url", ""),
        config=config
    )
