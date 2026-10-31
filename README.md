# MangaTrax 📚✨

[![License: MIT](https://img.shields.io/badge/License-MIT-rose.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Docker: Ready](https://img.shields.io/badge/Docker-Containerized-2496ED.svg)](https://www.docker.com/)

**MangaTrax** is an automated manga release monitor, bundle downloader, and metadata tagger tailored for **Calibre** and **BookOrbit** ecosystems. It checks RSS feeds and web trackers for new manga chapters, volumes, and multi-volume bundle releases (e.g. `Vol 01-05`), compares them against your owned Calibre library using zero-lock read-only access, sends wanted downloads to any qBittorrent-compatible downloader, and automatically injects standard `ComicInfo.xml` metadata into `.cbz` archives based on your existing Volume 1 metadata.

---

## 🌟 Key Features

- **Organized Canonical Series View**: Aggregates messy RSS releases, scanlation group prefixes (`[danke-Empire]`, `[Group]`), and duplicate torrent versions into clean canonical series cards. Includes a view toggle to switch between **Group by Series** and **All Releases (Flat)** with real-time search.
- **Prioritized Release Groups & Dedicated Prioritized Page**: Define trusted digital release groups (e.g. `danke-Empire`, `1R0N`, `Lu自主规制`, `a-sloth`) to feature them on a dedicated **Prioritized** page with VIP badges and higher ranking. 1-click star/prioritize any group directly from the releases list or settings.
- **Chapter-Only Discard & Mixed Volume Isolation**: Automatically discards pure chapter-only feed noise (`Chapter 1110`, `c150`, `Ch. 01-10`) so only true volume releases enter your tracking system. For mixed updates (e.g. `Vol. 1 (Ch. 1-7)` or `v108 (c1080-c1090)`), chapter tags are stripped cleanly, isolating only the volume information.
- **Authoritative Manga Database Integration (AniList)**: Automatically resolves raw releases to canonical titles, high-resolution official cover art, total volume counts, and synopsis data via AniList's public GraphQL API, with full local SQLite caching to operate cleanly without external rate-limiting.
- **Full Volume Extraction from Batch Releases**: When a batch release specifies a volume range (`v1-v7`, `v01-v07`, `Vol. 1-7`, `(v01-v07)`, `[v01-v20]`, or comma/plus lists `Vol 1, 2, 3`), MangaTrax extracts **every individual volume** in the range (`1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0`) instead of only the endpoints, rendering distinct badges for every volume with color-coded owned vs missing indicators.
- **Missing-Volume Range Matching**: When a bundle is detected, MangaTrax checks your Calibre library to determine if *any* volume inside the bundle is missing. If you already own volumes 1–3 and a bundle offers 1–5, MangaTrax identifies volumes 4 and 5 as wanted and requests the release.
- **Zero-Lock Network Share Calibre Reader**: Reads Calibre's `metadata.db` over SMB/NFS using SQLite's `mode=ro&immutable=1` URI mode. Never locks the database, completely preventing write conflicts or corruption while host Calibre is open.
- **qBittorrent Web API Integration**: Directly interfaces with qBittorrent or any downloader exposing the qBittorrent v2 Web API (including Flood, Torbox, and qBit emulators). Automatically tags torrents with category `manga` and pushes downloads to your staging folder.
- **Staging Pipeline & Automatic `ComicInfo.xml` Tagging**: Downloads are saved to a staging directory separate from BookOrbit. MangaTrax matches completed `.cbz` archives to existing series templates in Calibre, injecting a standard `ComicInfo.xml` (author, series name, volume index, publisher, genre tags) without re-compressing or touching image bytes.
- **BookOrbit Two-Way Sync Workflow**: After files in staging are tagged with `ComicInfo.xml`, they can be ingested directly into BookOrbit or Calibre without manual metadata editing.
- **Discovery & Blacklist Panels**: 
  - Discovery panel showcases newly detected manga (Volume 1 or standalone releases) with 1-click **Track Series** or **Blacklist**.
  - Blacklist management table allows proactively barring unwanted titles from ever triggering downloads or requests.
- **Direct Series Links**: Keeps clickable direct links to MangaUpdates, AniList, or release tracker hubs for every tracked series.
- **Local Cover Cache & SSRF Defense**: Locally caches cover thumbnails with strict multi-hop SSRF protection (blocking internal IP ranges) and file magic byte verification.

---

## 🔄 The Calibre + BookOrbit Workflow

```
+-------------------------------------------------------+
|                 MangaTrax Architecture                |
+-------------------------------------------------------+
                           │
      RSS / Web Feeds ────►│ 1. Parse releases & volume bundles
                           │    (e.g., "Vol 01-05")
                           ▼
  Calibre (metadata.db) ──►│ 2. Check owned volumes (Zero-Lock)
   (Read-Only SMB Share)   │    - All owned? -> Mark IN_CALIBRE
                           │    - Missing vols? -> Mark REQUESTED
                           ▼
     qBittorrent / ◄───────│ 3. Auto-push wanted torrents to
     qBit Downloader       │    staging directory (/staging)
                           ▼
     Staging Directory ───►│ 4. Scan completed .cbz files
     (/staging/*.cbz)      │ 5. Inject ComicInfo.xml inherited
                           │    from Calibre's Volume 1 metadata
                           ▼
   BookOrbit / Calibre ◄───┘ 6. Clean two-way merge ingestion
    (Two-Way Sync Plugin)       (No manual metadata tagging needed!)
```

1. **Volume 1 Setup**: You only manually configure Volume 1 of each series once in Calibre (setting series name, author, publisher, and genre tags).
2. **Continuous Monitoring**: MangaTrax periodically scans your Calibre library in zero-lock read-only mode and caches your series templates.
3. **Automated Bundle Ingestion**: When new volumes or multi-volume bundles appear on feeds:
   - MangaTrax determines which volumes in the bundle are missing.
   - Pushes the torrent/magnet to your qBittorrent client with destination `/staging`.
4. **Auto-Tagging**: Completed `.cbz` files in `/staging` are injected with `ComicInfo.xml`.
5. **Ingestion**: Ingest the tagged `.cbz` files into BookOrbit or Calibre. Because the metadata was inherited from Volume 1, your library stays perfectly formatted with zero manual work.

---

## 🚀 Quick Start with Docker

### 1. `docker-compose.yml`

```yaml
version: '3.8'

services:
  mangatrax:
    image: mangatrax:latest
    build: .
    container_name: mangatrax
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    ports:
      - "8082:8080"
    environment:
      - PORT=8080
      - CALIBRE_DIR=/calibre
      - STAGING_DIR=/staging
      - DATA_DIR=/app/data
      - QBIT_URL=http://qbittorrent:8080
      - QBIT_USER=admin
      - QBIT_PASS=adminadmin
      - TZ=UTC
    volumes:
      # Calibre library network share (read-only for zero-lock safety)
      - /mnt/storage/CalibreLibrary:/calibre:ro

      # Staging directory where completed downloads arrive and get tagged
      - /mnt/storage/MangaStaging:/staging

      # Persistent MangaTrax SQLite database and cover cache
      - ./data:/app/data
```

### 2. Run the Stack

```bash
docker compose up -d --build
```

Access the dashboard at `http://your-server-ip:8082`.

### 3. TrueNAS SCALE Setup (`docker-compose.truenas.yml`)

For TrueNAS SCALE users, MangaTrax includes a hardened `docker-compose.truenas.yml` configured for the `apps` user (`user: "568:568"`):

```yaml
services:
  mangatrax:
    build:
      context: https://github.com/Wemew/MangaTrax.git#main
      dockerfile: Dockerfile
    image: mangatrax:latest
    container_name: mangatrax
    restart: unless-stopped
    user: "568:568"
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    ports:
      - "8082:8080"
    environment:
      - PORT=8080
      - CALIBRE_DIR=/calibre
      - STAGING_DIR=/staging
      - DATA_DIR=/app/data
      - QBIT_URL=http://qbittorrent:8080
      - QBIT_USER=admin
      - QBIT_PASS=adminadmin
      - TZ=Etc/UTC
    volumes:
      - /mnt/tank/media/calibre:/calibre:ro
      - /mnt/tank/media/manga/staging:/staging
      - /mnt/tank/apps/mangatrax/data:/app/data
```

---

## 🛠️ Configuration Settings

| Setting Key | Environment Var | Default | Description |
|---|---|---|---|
| `calibre_path` | `CALIBRE_DIR` | `/calibre` | Path to Calibre library containing `metadata.db` |
| `staging_dir` | `STAGING_DIR` | `/staging` | Path to manga staging directory |
| `qbit_url` | `QBIT_URL` | `http://localhost:8080` | Base URL of qBittorrent Web API |
| `qbit_username` | `QBIT_USER` | `admin` | Downloader username |
| `qbit_password` | `QBIT_PASS` | `adminadmin` | Downloader password |
| `auto_download` | - | `true` | Automatically send wanted releases to qBittorrent |
| `auto_tag` | - | `true` | Automatically inject `ComicInfo.xml` into completed `.cbz` files |
| `calibre_sync_interval_minutes` | - | `15` | Minutes between zero-lock Calibre library scans |
| `feed_check_interval_minutes` | - | `60` | Minutes between feed polls |

---

## 📡 REST API Reference

- `GET /` — Overview Dashboard
- `GET /series` — Manga Series Management & Monitoring Toggles
- `GET /discovery` — Volume 1 & Standalone Discovery Panel
- `GET /releases` — All Releases & Bundles with Missing Volume Highlights
- `GET /downloads` — Real-Time qBittorrent Queue & Staging File Inspector
- `GET /feeds` — Manga RSS & HTML Scraper Manager
- `GET /logs` — Activity & ComicInfo Tagging Audit Logs
- `GET /settings` — Application Settings View
- `POST /api/calibre/sync` — Trigger immediate Calibre library rescan
- `POST /api/feeds/check` — Trigger immediate feed poll
- `POST /api/staging/tag` — Trigger immediate ComicInfo.xml tagging scan of staging directory
- `POST /api/downloader/test` — Test connectivity to qBittorrent Web API
- `POST /api/releases/{id}/download` — Manually push a release to qBittorrent
- `POST /api/blacklist` — Blacklist a series name
- `DELETE /api/blacklist/{id}` — Unblock a series

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
