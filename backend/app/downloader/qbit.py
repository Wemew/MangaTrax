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

    @staticmethod
    def extract_torrent_hash(url_or_magnet: str) -> Optional[str]:
        """Extracts normalized 40-character hex hash from a magnet link or string."""
        if not url_or_magnet:
            return None
        import re
        m = re.search(r"urn:btih:([a-zA-Z0-9]{32,40})", url_or_magnet, re.IGNORECASE)
        if m:
            raw_hash = m.group(1).lower()
            if len(raw_hash) == 32:
                try:
                    import base64
                    raw_bytes = base64.b32decode(raw_hash.upper())
                    return raw_bytes.hex().lower()
                except Exception:
                    return raw_hash
            return raw_hash
        return None

    def get_torrent_files(self, torrent_hash: str) -> List[Dict[str, Any]]:
        """Queries qBittorrent for the list of files in a specific torrent."""
        if not self.enabled or not self._ensure_auth():
            return []

        url = f"{self.base_url}/api/v2/torrents/files?hash={urllib.parse.quote(torrent_hash)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MangaTrax/1.0", "Accept": "application/json"}
        )
        try:
            with self.opener.open(req, timeout=10.0) as resp:
                if resp.status == 200:
                    data = resp.read().decode("utf-8", errors="replace")
                    return json.loads(data)
                return []
        except Exception as e:
            logger.debug(f"Could not retrieve files for torrent {torrent_hash}: {e}")
            return []

    def set_file_priorities(self, torrent_hash: str, file_indices: List[int], priority: int = 0) -> bool:
        """
        Sets priority for a list of file indices in a torrent.
        priority=0 sets 'Do not download' (deselects file in downloader).
        """
        if not self.enabled or not self._ensure_auth() or not file_indices:
            return False

        id_str = "|".join(str(i) for i in file_indices)
        data = {
            "hash": torrent_hash,
            "id": id_str,
            "priority": str(priority)
        }
        return self._post_data("/api/v2/torrents/filePrio", data)

    def apply_selective_volume_download(
        self,
        torrent_hash: str,
        wanted_volumes: List[float]
    ) -> Dict[str, Any]:
        """
        Examines files in a multi-file torrent bundle and sets priority=0 (Do Not Download)
        for any volumes that the user already owns (i.e. NOT in wanted_volumes).
        """
        if not self.enabled:
            return {"status": "disabled", "message": "Downloader disabled"}

        files = self.get_torrent_files(torrent_hash)
        if not files:
            # Metadata might still be downloading from DHT/trackers
            return {"status": "pending_metadata", "message": "Metadata not loaded yet"}

        # Check if the torrent contains individual volume files
        archive_extensions = (".cbz", ".zip", ".cbr", ".rar", ".pdf", ".tar", ".7z")
        manga_files = [
            f for f in files 
            if any(f.get("name", "").lower().endswith(ext) for ext in archive_extensions)
        ]

        # If only 1 archive file in torrent (e.g. single large zip archive), cannot split in BitTorrent
        if len(manga_files) <= 1:
            return {
                "status": "single_archive",
                "message": "Torrent contains a single archive; full archive will be downloaded and split in staging."
            }

        import re
        vol_pattern = re.compile(
            r'(?:^|[\s_\[\(-])(?:vol(?:ume)?|v)[.\s_-]*([0-9]+(?:\.[0-9]+)?)(?:[\s_\]\).-]|$)',
            re.IGNORECASE
        )

        wanted_set = set(wanted_volumes)
        skip_indices = []
        keep_indices = []
        skipped_vols = []
        kept_vols = []

        for idx, f in enumerate(files):
            file_idx = f.get("index", idx)
            fname = f.get("name", "")
            match = vol_pattern.search(fname)
            if match:
                try:
                    vol_num = float(match.group(1))
                    if vol_num in wanted_set:
                        keep_indices.append(file_idx)
                        kept_vols.append(vol_num)
                    else:
                        skip_indices.append(file_idx)
                        skipped_vols.append(vol_num)
                except ValueError:
                    keep_indices.append(file_idx)
            else:
                # Ancillary files (.txt, .nfo, .url, .html): skip them
                if fname.lower().endswith((".txt", ".nfo", ".url", ".html", ".lnk")):
                    skip_indices.append(file_idx)
                else:
                    keep_indices.append(file_idx)

        if skip_indices:
            self.set_file_priorities(torrent_hash, skip_indices, priority=0)
            logger.info(
                f"Selective download applied for {torrent_hash}: "
                f"Skipped {len(skip_indices)} files (Vols: {skipped_vols}); "
                f"Downloading {len(keep_indices)} files (Vols: {kept_vols})."
            )
            return {
                "status": "filtered",
                "skipped_files": len(skip_indices),
                "skipped_volumes": skipped_vols,
                "kept_files": len(keep_indices),
                "kept_volumes": kept_vols
            }
        else:
            return {
                "status": "all_needed",
                "message": "All files in bundle are needed; downloading all."
            }

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
