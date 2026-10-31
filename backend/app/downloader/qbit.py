import logging
import json
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
from typing import List, Dict, Any, Optional

logger = logging.getLogger("mangatrax.qbit")

class QBitClient:
    """
    In-App client for qBittorrent v2 Web API (compatible with qBittorrent, Flood, Torbox, and qBit emulators).
    Fully configured in-app without requiring docker-compose environment variables.
    """
    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        username: str = "admin",
        password: str = "adminadmin",
        enabled: bool = True
    ):
        self.base_url = (base_url or "http://localhost:8080").rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.enabled = bool(enabled)
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.authenticated = False

    def login(self) -> bool:
        if not self.enabled:
            logger.info("Downloader is disabled in settings. Skipping login.")
            return False

        login_url = f"{self.base_url}/api/v2/auth/login"
        post_data = urllib.parse.urlencode({
            "username": self.username,
            "password": self.password
        }).encode("utf-8")

        req = urllib.request.Request(
            login_url,
            data=post_data,
            headers={
                "User-Agent": "MangaTrax/1.0",
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )

        try:
            with self.opener.open(req, timeout=10.0) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                # qBittorrent returns 'Ok.' and sets a SID cookie upon successful authentication
                has_cookie = any(c.name == "SID" for c in self.cj)
                if resp.status == 200 and ("Ok." in text or has_cookie):
                    self.authenticated = True
                    return True
                else:
                    logger.warning(f"qBittorrent login failed: {text.strip()}")
                    return False
        except urllib.error.HTTPError as e:
            logger.warning(f"qBittorrent login HTTP {e.code}: {e.reason}")
            return False
        except Exception as e:
            logger.error(f"Error connecting to qBittorrent at {login_url}: {e}")
            return False

    def _ensure_auth(self) -> bool:
        if not self.authenticated:
            return self.login()
        return True

    def test_connection(self) -> Dict[str, Any]:
        """Verifies connectivity and returns downloader app version and state."""
        if not self.enabled:
            return {
                "success": False,
                "version": "",
                "message": "Downloader is currently disabled in MangaTrax settings."
            }

        if not self.base_url:
            return {
                "success": False,
                "version": "",
                "message": "Downloader URL is not configured."
            }

        try:
            if not self.login():
                return {
                    "success": False,
                    "version": "",
                    "message": f"Authentication failed connecting to {self.base_url}. Verify username and password."
                }

            req = urllib.request.Request(
                f"{self.base_url}/api/v2/app/version",
                headers={"User-Agent": "MangaTrax/1.0"}
            )
            with self.opener.open(req, timeout=8.0) as resp:
                if resp.status == 200:
                    version = resp.read().decode("utf-8", errors="replace").strip()
                    return {
                        "success": True,
                        "version": version,
                        "message": f"Connected to qBittorrent {version} at {self.base_url}"
                    }
                return {"success": False, "version": "", "message": f"HTTP {resp.status} from downloader"}
        except urllib.error.HTTPError as e:
            return {"success": False, "version": "", "message": f"HTTP {e.code}: {e.reason}"}
        except Exception as e:
            return {"success": False, "version": "", "message": f"Connection error: {e}"}

    def add_torrent(
        self,
        url_or_magnet: str,
        savepath: Optional[str] = None,
        category: str = "manga",
        tags: str = "mangatrax",
        paused: bool = False
    ) -> bool:
        """Sends a magnet link or .torrent URL to the downloader."""
        if not self.enabled:
            logger.info("Downloader is disabled in settings. Skipping add_torrent.")
            return False

        if not self._ensure_auth():
            logger.warning("Downloader authentication not available; cannot add torrent.")
            return False

        add_url = f"{self.base_url}/api/v2/torrents/add"
        form_fields = {
            "urls": url_or_magnet,
            "category": category or "manga",
            "tags": tags or "mangatrax",
            "paused": "true" if paused else "false"
        }
        if savepath:
            form_fields["savepath"] = savepath

        post_data = urllib.parse.urlencode(form_fields).encode("utf-8")
        req = urllib.request.Request(
            add_url,
            data=post_data,
            headers={
                "User-Agent": "MangaTrax/1.0",
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )

        try:
            with self.opener.open(req, timeout=12.0) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200 and "Fails" not in text:
                    logger.info(f"Successfully added torrent to downloader: {url_or_magnet[:60]}...")
                    return True
                logger.warning(f"Failed to add torrent to downloader: {text}")
                return False
        except Exception as e:
            logger.error(f"Error calling downloader add_torrent: {e}")
            return False

    def get_torrents(self, category: str = "manga", filter_status: str = "all") -> List[Dict[str, Any]]:
        """Retrieves list of active torrents."""
        if not self.enabled or not self._ensure_auth():
            return []

        params = {"filter": filter_status}
        if category:
            params["category"] = category

        list_url = f"{self.base_url}/api/v2/torrents/info?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            list_url,
            headers={"User-Agent": "MangaTrax/1.0", "Accept": "application/json"}
        )

        try:
            with self.opener.open(req, timeout=10.0) as resp:
                if resp.status == 200:
                    data = resp.read().decode("utf-8", errors="replace")
                    return json.loads(data)
                return []
        except Exception as e:
            logger.error(f"Error fetching torrent list: {e}")
            return []

    def pause_torrent(self, torrent_hash: str) -> bool:
        if not self.enabled or not self._ensure_auth():
            return False
        return self._post_hashes("/api/v2/torrents/pause", torrent_hash)

    def resume_torrent(self, torrent_hash: str) -> bool:
        if not self.enabled or not self._ensure_auth():
            return False
        return self._post_hashes("/api/v2/torrents/resume", torrent_hash)

    def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        if not self.enabled or not self._ensure_auth():
            return False
        data = {
            "hashes": torrent_hash,
            "deleteFiles": "true" if delete_files else "false"
        }
        return self._post_data("/api/v2/torrents/delete", data)

    def _post_hashes(self, path: str, torrent_hash: str) -> bool:
        return self._post_data(path, {"hashes": torrent_hash})

    def _post_data(self, path: str, form_dict: Dict[str, str]) -> bool:
        post_data = urllib.parse.urlencode(form_dict).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=post_data,
            headers={
                "User-Agent": "MangaTrax/1.0",
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )
        try:
            with self.opener.open(req, timeout=10.0) as resp:
                return resp.status == 200
        except Exception as e:
            logger.warning(f"Error calling {path}: {e}")
            return False
