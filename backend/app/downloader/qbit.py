import logging
from typing import List, Dict, Any, Optional
import httpx

logger = logging.getLogger("mangatrax.qbit")

class QBitClient:
    """
    Client for qBittorrent v2 Web API (compatible with qBittorrent, Flood, Torbox, and qBit emulators).
    """
    def __init__(self, base_url: str = "http://localhost:8080", username: str = "admin", password: str = "adminadmin"):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.client = httpx.Client(timeout=10.0, follow_redirects=True)
        self.authenticated = False

    def login(self) -> bool:
        login_url = f"{self.base_url}/api/v2/auth/login"
        try:
            resp = self.client.post(login_url, data={"username": self.username, "password": self.password})
            if resp.status_code == 200 and ("Ok." in resp.text or resp.cookies.get("SID")):
                self.authenticated = True
                return True
            else:
                logger.warning(f"qBittorrent login failed: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Error connecting to qBittorrent at {login_url}: {e}")
            return False

    def _ensure_auth(self):
        if not self.authenticated:
            self.login()

    def test_connection(self) -> Dict[str, Any]:
        """Verifies connectivity and returns qBittorrent app version."""
        try:
            if not self.login():
                return {"success": False, "message": "Authentication failed (invalid credentials)"}

            resp = self.client.get(f"{self.base_url}/api/v2/app/version")
            if resp.status_code == 200:
                version = resp.text.strip()
                return {"success": True, "version": version, "message": f"Connected to qBittorrent {version}"}
            return {"success": False, "message": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def add_torrent(
        self,
        url_or_magnet: str,
        savepath: Optional[str] = None,
        category: str = "manga",
        tags: str = "mangatrax",
        paused: bool = False
    ) -> bool:
        """Sends a magnet link or .torrent URL to the downloader."""
        self._ensure_auth()
        add_url = f"{self.base_url}/api/v2/torrents/add"
        data = {
            "urls": url_or_magnet,
            "category": category,
            "tags": tags,
            "paused": "true" if paused else "false"
        }
        if savepath:
            data["savepath"] = savepath

        try:
            resp = self.client.post(add_url, data=data)
            if resp.status_code == 200 and "Fails" not in resp.text:
                logger.info(f"Successfully added torrent to qBittorrent: {url_or_magnet[:60]}...")
                return True
            logger.warning(f"Failed to add torrent: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"Error calling qBittorrent add_torrent: {e}")
            return False

    def get_torrents(self, category: str = "manga", filter_status: str = "all") -> List[Dict[str, Any]]:
        """Retrieves list of active torrents."""
        self._ensure_auth()
        list_url = f"{self.base_url}/api/v2/torrents/info"
        params = {"filter": filter_status}
        if category:
            params["category"] = category

        try:
            resp = self.client.get(list_url, params=params)
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            logger.error(f"Error fetching torrent list: {e}")
            return []

    def pause_torrent(self, torrent_hash: str) -> bool:
        self._ensure_auth()
        try:
            resp = self.client.post(f"{self.base_url}/api/v2/torrents/pause", data={"hashes": torrent_hash})
            return resp.status_code == 200
        except Exception:
            return False

    def resume_torrent(self, torrent_hash: str) -> bool:
        self._ensure_auth()
        try:
            resp = self.client.post(f"{self.base_url}/api/v2/torrents/resume", data={"hashes": torrent_hash})
            return resp.status_code == 200
        except Exception:
            return False

    def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        self._ensure_auth()
        try:
            resp = self.client.post(
                f"{self.base_url}/api/v2/torrents/delete",
                data={"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"}
            )
            return resp.status_code == 200
        except Exception:
            return False
