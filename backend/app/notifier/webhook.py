import logging
import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from app import security

logger = logging.getLogger("mangatrax.notifier")

class SimpleHttpResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

def _post_json(url: str, json_data: dict, timeout: float = 10.0) -> SimpleHttpResponse:
    # M3 SSRF guard at the outbound transport boundary: rejects non-http(s)
    # schemes, loopback/RFC1918/link-local/metadata literals and intranet
    # single-label hostnames.  Structural (non-DNS) validation is used here so
    # self-hosted Apprise endpoints on the LAN keep working; user-probed URLs
    # (POST /api/settings/test-webhook) are additionally validated structurally
    # by the Pydantic schema before reaching this function.
    security.validate_outbound_url(url)
    req = urllib.request.Request(
        url,
        data=json.dumps(json_data).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "MangaTrax/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status if hasattr(resp, "status") else resp.getcode()
            text = resp.read().decode("utf-8", errors="ignore")
            return SimpleHttpResponse(status, text)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="ignore")
        return SimpleHttpResponse(e.code, text)

class NotificationService:
    def __init__(self, discord_webhook: Optional[str] = None, generic_webhook: Optional[str] = None):
        self.discord_webhook = (discord_webhook or "").strip()
        self.generic_webhook = (generic_webhook or "").strip()

    def notify_new_request(self, release: Dict[str, Any]):
        """Trigger notification when a new manga release is requested."""
        title = release.get("title", "")
        series = release.get("series_name", "Unknown Manga")
        v_start = release.get("volume_start")
        v_end = release.get("volume_end")
        is_bundle = release.get("is_bundle", False)
        link = release.get("link") or release.get("download_url") or ""
        cover = release.get("cover_url")
        feed = release.get("source_feed_name", "")
        group = release.get("release_group", "")

        if is_bundle and v_start is not None and v_end is not None:
            vol_str = f"Bundle (Vol {int(v_start) if v_start == int(v_start) else v_start}-{int(v_end) if v_end == int(v_end) else v_end})"
        elif v_start is not None:
            vol_str = f"Vol {int(v_start) if v_start == int(v_start) else v_start}"
        else:
            vol_str = "Release"

        fields = [
            {"name": "Series", "value": series, "inline": True},
            {"name": "Volume", "value": vol_str, "inline": True}
        ]
        if group:
            fields.append({"name": "Group", "value": group, "inline": True})
        if feed:
            fields.append({"name": "Feed", "value": feed, "inline": True})

        self._send_discord(
            title=f"📖 New Manga Requested: {series} ({vol_str})",
            description=f"**{title}** has been added to your download queue.",
            url=link if link.startswith("http") else None,
            image_url=cover if (cover and cover.startswith("http")) else None,
            fields=fields,
            color=0xf59e0b # Amber
        )
        self._send_generic({"event": "NEW_REQUEST", "release": release})

    def notify_acquired(self, release: Dict[str, Any]):
        """Trigger notification when a requested manga is added to Calibre."""
        title = release.get("title", "")
        series = release.get("series_name", "Unknown Manga")
        v_start = release.get("volume_start")

        vol_str = f"Vol {int(v_start) if v_start == int(v_start) else v_start}" if v_start is not None else ""

        fields = [
            {"name": "Series", "value": series, "inline": True}
        ]
        if vol_str:
            fields.append({"name": "Volume", "value": vol_str, "inline": True})

        self._send_discord(
            title=f"✅ Added to Calibre: {series} {vol_str}".strip(),
            description=f"**{title}** was detected in your Calibre library and resolved!",
            fields=fields,
            color=0x10b981 # Emerald
        )
        self._send_generic({"event": "ACQUIRED", "release": release})

    def _send_discord(self, title: str, description: str, url: Optional[str] = None, image_url: Optional[str] = None, fields: Optional[list] = None, color: int = 0xf59e0b):
        if not self.discord_webhook:
            return

        embed = {
            "title": title,
            "description": description,
            "color": color
        }
        if url:
            embed["url"] = url
        if image_url:
            embed["thumbnail"] = {"url": image_url}
        if fields:
            embed["fields"] = fields

        payload = {
            "username": "MangaTrax",
            "avatar_url": "https://raw.githubusercontent.com/feathericons/feather/master/icons/book-open.svg",
            "embeds": [embed]
        }

        try:
            _post_json(self.discord_webhook, payload, timeout=5.0)
        except Exception as e:
            logger.error(f"Failed to deliver Discord notification: {e}")

    def _send_generic(self, payload: Dict[str, Any]):
        if not self.generic_webhook:
            return

        try:
            _post_json(self.generic_webhook, payload, timeout=5.0)
        except Exception as e:
            logger.error(f"Failed to deliver generic webhook notification: {e}")

    def test_webhook(self, discord_url: Optional[str] = None, apprise_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Sends a test notification to verify Discord and/or generic webhook endpoints.
        Returns detailed status and response messages for user diagnostics.
        """
        d_url = (discord_url if discord_url is not None else self.discord_webhook).strip()
        g_url = (apprise_url if apprise_url is not None else self.generic_webhook).strip()

        if not d_url and not g_url:
            return {
                "status": "error",
                "message": "No webhook URL provided. Please enter a Discord Webhook URL or Generic Webhook URL."
            }

        results = {}
        all_succeeded = True

        if d_url:
            payload = {
                "username": "MangaTrax",
                "avatar_url": "https://raw.githubusercontent.com/feathericons/feather/master/icons/book-open.svg",
                "embeds": [{
                    "title": "🔔 MangaTrax Notification Test",
                    "description": "If you are reading this, your Discord webhook integration in MangaTrax is functioning properly!",
                    "color": 0xf59e0b,
                    "fields": [
                        {"name": "Status", "value": "✅ Connected", "inline": True},
                        {"name": "Application", "value": "MangaTrax", "inline": True}
                    ]
                }]
            }
            try:
                resp = _post_json(d_url, payload, timeout=10.0)
                if resp.status_code in (200, 204):
                    results["discord"] = {
                        "success": True,
                        "message": f"Discord notification delivered successfully (HTTP {resp.status_code})!"
                    }
                else:
                    all_succeeded = False
                    results["discord"] = {
                        "success": False,
                        "message": f"Discord returned HTTP {resp.status_code}: {resp.text[:200]}"
                    }
            except Exception as e:
                all_succeeded = False
                results["discord"] = {
                    "success": False,
                    "message": f"Connection error: {str(e)}"
                }

        if g_url:
            payload = {
                "event": "TEST",
                "title": "MangaTrax Notification Test",
                "message": "Your generic/Apprise webhook endpoint is reachable and configured properly!"
            }
            try:
                resp = _post_json(g_url, payload, timeout=10.0)
                if 200 <= resp.status_code < 300:
                    results["generic"] = {
                        "success": True,
                        "message": f"Generic webhook delivered successfully (HTTP {resp.status_code})!"
                    }
                else:
                    all_succeeded = False
                    results["generic"] = {
                        "success": False,
                        "message": f"Generic webhook returned HTTP {resp.status_code}: {resp.text[:200]}"
                    }
            except Exception as e:
                all_succeeded = False
                results["generic"] = {
                    "success": False,
                    "message": f"Connection error: {str(e)}"
                }

        messages = [r["message"] for r in results.values()]
        return {
            "status": "success" if all_succeeded else "error",
            "message": " | ".join(messages),
            "results": results
        }
