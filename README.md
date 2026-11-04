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
      - TZ=UTC
      # Note: BitTorrent downloader (qBittorrent, Flood, Torbox, etc.)
      # is configured 100% in-app via the MangaTrax Web UI (Settings & Downloads tab).
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

For TrueNAS SCALE users, MangaTrax includes a hardened `docker-compose.truenas.yml` configured for the `apps` user (`PUID=568`, `PGID=568`):

```yaml
services:
  mangatrax:
    build:
      context: https://github.com/Wemew/MangaTrax.git#main
      dockerfile: Dockerfile
    image: mangatrax:latest
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
      - PUID=568
      - PGID=568
      - TZ=Etc/UTC
      # BitTorrent downloader is configured 100% in-app via Settings/Downloads tabs
    volumes:
      - /mnt/tank/media/calibre:/calibre:ro
      - /mnt/tank/media/manga/staging:/staging
      - ./data:/app/data
```
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
| `nyaa_base_url` | `MANGATRAX_NYAA_BASE_URL` | `https://nyaa.si` | Nyaa/tracker base URL, including a self-hosted mirror/proxy |

### Nyaa mirror / proxy (`nyaa_base_url`)

If `nyaa.si` is unreachable from your network (e.g. DNS-blocked by your ISP), run your
own passthrough mirror and point MangaTrax at it with `nyaa_base_url` (Settings →
Nyaa / Tracker Source, or `MANGATRAX_NYAA_BASE_URL`). Everything that used to hardcode
`nyaa.si` now uses this base: feed polling, **polite backfill pagination**, post view
URLs, `.torrent` links and relative-href joins. No code path requires `nyaa.si` once a
mirror is configured.

* Accepted values: a bare base (`https://wemew-nyaa.vercel.app`) **or** a full
  Nyaa-style URL (`https://mirror/?page=rss&c=3_1&f=0`). It is normalised to
  `scheme://host[:port][/path]`: query string and fragment are dropped, the host is
  lower-cased, default ports are removed and a path prefix is preserved.
* Credentials-in-URL, non-http(s), empty values, malformed ports and
  loopback/private/metadata hosts are rejected.
* The proxy must forward Nyaa's query API:
  * RSS: `GET /?page=rss&c=3_1&f=0`
  * HTML paging / backfill: `GET /?p=<N>&c=3_1&f=0&s=id&o=desc`
  * torrents: `GET /download/<id>.torrent`
* On startup (and when the setting is saved) the built-in seeded feed is repointed to
  the mirror, preserving its query string; user-created feeds are never modified.
* A per-feed `config.base_url` overrides the global setting. Broken sources can be
  switched off from the Feeds table (`POST /api/feeds/{id}/toggle`) instead of deleted.

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
- `POST /api/feeds/{id}/toggle` — Enable/disable a feed without deleting it
- `POST /api/staging/tag` — Trigger immediate ComicInfo.xml tagging scan of staging directory
- `POST /api/downloader/test` — Test connectivity to qBittorrent Web API
- `POST /api/releases/{id}/download` — Manually push a release to qBittorrent
- `POST /api/blacklist` — Blacklist a series name
- `DELETE /api/blacklist/{id}` — Unblock a series

---

## Download throughput & troubleshooting (Torbox / 1 Gbps)

MangaTrax now downloads selected Torbox files in parallel and splits large single files
into HTTP Range chunks over multiple connections:

- `torbox_download_concurrency` (Settings -> Downloader, default 4, 1-16): concurrent
  file downloads. `1` = sequential diagnostic mode.
- `torbox_parallel_chunks` (default 4, max 8): connections per file > 16 MiB when the
  server supports `Accept-Ranges`/206. Falls back to a single stream on 200/416.
- Per-file INFO: `Torbox fetch '<file>': 612 MB in 41.2 s = 14.9 MB/s (4 conns)`.
  Per-release aggregate and `connections`/`per_file` metrics are in the
  `TORBOX_DOWNLOADED` audit details.

A 1 Gbps link is 125 MB/s **aggregate**, but one TCP stream over the WAN usually tops
out far below that (per-connection CDN/Torbox caps, RTT/congestion window). Sequential
per-file fetching therefore cannot fill the link; parallel files + ranged chunks are
what raise aggregate throughput. Watch the per-file numbers: if a single stream is
already fast, more connections will not help and may cause 429 throttling (the
downloader retries with backoff and logs it).

Verify the two ceilings before blaming MangaTrax/Torbox:

```bash
# WAN speed to a nearby CDN (expect close to the link's aggregate capacity)
curl -o /dev/null -s -w '%{speed_download} B/s\n' \
  'https://speed.cloudflare.com/__down?bytes=500000000'

# Disk write speed on the downloads mount (run inside the container/host path;
# for mergerfs/FUSE include the same options and watch for the FUSE overhead)
dd if=/dev/zero of=/downloads/.speedtest bs=1M count=2048 oflag=direct && rm /downloads/.speedtest
```

If both are fast but a single Torbox stream is slow, keep the defaults/raise
`torbox_download_concurrency`; if the disk is the bottleneck (mergerfs/FUSE, network
mount), parallel connections will not help. Check the task-28 storage-health banner and
`GET /health` for unwritable/missing downloads/staging mounts first.

---

## Network diagnostics: why is the container slower than the host?

MangaTrax downloads run **inside** the container, so any container networking overhead
directly caps download speed. Settings -> Maintenance has a "Network diagnostics" card
(with **Run speed test** and **Copy JSON**) and the same snapshot is available from
`GET /api/diagnostics/network` plus `GET /health` (`network` block). The speed test uses
a **fixed Cloudflare endpoint only** (no arbitrary URL) and is rate-limited to once a
minute.

Common host-vs-container gaps and fixes:

- **Bridge/veth NAT overhead** — default `bridge` networking sends every packet through
  the host's veth/NAT; high pps can be CPU-bound. Try `network_mode: host` (then publish
  ports differently) if you can give up isolation.
- **MTU mismatch** — Docker defaults to 1500; if the host/jumbo path uses 9000, TLS
  stalls/fragments. Align with `com.docker.network.driver.mtu=1500` (or the host MTU).
- **Missing sysctls in the container netns** — the container may not inherit BBR/buffer
  tuning:
  ```yaml
  services:
    mangatrax:
      sysctls:
        - net.ipv4.tcp_congestion_control=bbr
        - net.core.default_qdisc=fq
        - net.core.rmem_max=33554432
        - net.core.wmem_max=33554432
        - net.ipv4.tcp_rmem=4096 87380 33554432
        - net.ipv4.tcp_wmem=4096 65536 33554432
  ```
  (Some sysctls are namespaced; the card's values are what the container actually sees.)
- **CPU quota throttling** — a low `cpu.max` quota makes TLS/copy work CPU-bound; check
  the card's quota/throttled counters and raise the limit.
- **conntrack limits** — many parallel connections can exhaust `nf_conntrack` on the
  host (look for drops in `dmesg`); raise `net.netfilter.nf_conntrack_max`.
- **Docker DNS on parallel connections** — `127.0.0.11` can serialize/latency-spike
  resolutions; cache/avoid repeated lookups.

### Speed-test byte limit (important)

Cloudflare's `__down` endpoint returns **HTTP 403 with a 1-byte body** for requests above
**90 MB** — that is why a "1 GB" test looks like 0 MB/s. MangaTrax caps each request at
90,000,000 bytes and splits a larger total into sequential rounds per connection
(`bytes=360000000, connections=4` = one round of 90 MB per connection;
`bytes=1000000000, connections=4` = 3 rounds per connection). The response reports actual
bytes, per-connection MB/s and aggregate MB/s; a 403/short body is reported as an error
naming the endpoint limit, never as a fast download.

Equivalent shell recipe for a 1 GB-equivalent test (repeat rounds, sum bytes/time):

```bash
# 10 rounds x 90 MB, one connection (adjust -x/parallel for multiple connections)
for i in $(seq 1 10); do
  curl -s -o /dev/null -w '%{size_download} %{time_total}\n' \
    'https://speed.cloudflare.com/__down?bytes=90000000'
done
```

If the WAN test and `dd` to `/downloads` are fast but Torbox single streams are slow,
raise `torbox_download_concurrency` / `torbox_parallel_chunks` in Settings -> Downloader.

---

## Staging layout (flat)

Staging is **flat**: every completed `.cbz` is moved directly to `/staging/<file>.cbz`;
MangaTrax never creates per-torrent subfolders there. The downloads folder keeps its
per-torrent structure (each torrent owns its directory), and both folders should live on
the same drive so routing is an atomic rename.

If two different torrents produce the same filename:

- same name and same size -> treated as already staged (skipped, never overwritten);
- same name and different size -> stored as `<stem> (2).cbz`, `(3)`, ... (never
  overwritten).

Existing deployments with nested staging folders are flattened automatically at the
start/end of each routing pass, or on demand with **Flatten staging** on the Downloads
page (`POST /api/downloads/flatten-staging`). Temporary `.part` / `.mangatrax-part`
files are left alone and emptied folders are pruned safely.

---

## Tag display (AniList provider tags)

AniList genres and tags are stored ranked (rank descending, provider order on ties) and
denormalised to `releases.tags_text`/`series_tracking.tags_text` as exact tokens
(`|action|comedy|`). Spoiler-tagged provider tags are stored with a flag but never shown
or used for filtering by default. Genres are treated as the highest-priority tags.

Display policy (`app/tag_display.py`):

| View | Chips | Rule |
|---|---|---|
| Discovery cards | 5 | genres first (provider order), then highest-rank tags, `+N` overflow tooltip |
| Series page cards/rows | 4 | same order, tooltip has full list |
| Releases grouped/flat rows | 3 | compact; nothing rendered when empty |
| Dashboard recent items | 3 | compact |

Discovery toolbar filters server-side with exact-token matching:
`include_tags`, `exclude_tags`, `tag_match=any|all`, `sort=relevance|newest|series`
(relevance = count of included tags first, then recency). The tag picker (`/api/tags?q=`)
is an autocomplete over the known tag universe (union of the metadata cache, release tags and
series tags) ordered exact → prefix → substring → count.
Selections live in the URL and are reload-safe/shareable.

---

## Torbox manual pickup (unmappable volumes)

When strict volume selection cannot map a torrent's files (or Torbox returns no file
list), the torrent is **kept on Torbox** and a record is written to
`torbox_manual_pickups`; the release moves to `download_status='MANUAL_PICKUP'` and leaves
the normal retry queue. Open **Downloads → Manual Pickup** to review the file snapshot
(name/size/suggested volume), correct volumes, edit the series, fetch selected files or the
whole torrent, retry strict mapping, or discard. Fetching goes through the normal
requestdl → staging → ComicInfo → routing pipeline using the task framework, so progress
survives reloads.

Settings → Torbox:

| Setting | Default | Semantics |
|---|---|---|
| `torbox_manual_pickup_enabled` | on | Hold instead of delete+FAILED on mapping failures |
| `torbox_manual_pickup_max_items` | 10 | **0 = unlimited**; finite cap reached falls back to FAILED |
| `torbox_manual_pickup_expiry_days` | 7 | **0 = never expires**; past-due holds get a best-effort remote delete and are marked expired |

The scheduler sweeps expiry every 5 minutes. `torbox_max_download_gb` still applies per
selected file and to the selected total.

---

## Long-running actions (progress convention)

Every button that can take more than ~2 seconds must start a **persisted background
task** instead of blocking the request, so progress survives page reloads:

```python
from app import background_tasks

def worker(report):
    report(done=0, total=len(files), message="Starting…", force=True)
    for index, item in enumerate(files, 1):
        process(item)
        report(done=index, total=len(files), message=f"Processing {item.name}")

info = background_tasks.run_in_background(
    db, "kind:action", "Human title", worker, actor="user", scope="optional-scope",
)
# return {"status": "started", "task_id": info["task_id"], "task": info["task"]}
```

- Tasks are rows in `background_tasks` (`GET /api/tasks?active=1`,
  `GET /api/tasks/{id}`, `POST /api/tasks/{id}/cancel` for cancellable tasks).
- In JS call `MangaTasks.bindButton(button, "kind:action", scope)` (or mark the button
  with `data-task-kind`/`data-task-scope`); the global Tasks panel polls every 1.5 s,
  disables/reattaches matching buttons after reload, shows percent/message/elapsed and
  toasts completion. Do **not** use `location.reload()` for progress UI.
- Quick actions may use the foreground recorded-task helper so they still show up in the
  panel; long network work must use the threaded runner. Never delete files or rows as
  part of progress reporting.

---

## Log rotation & activity retention

MangaTrax logs in two places:

1. **Container stdout/stderr** — written by the app (no file handler by default) and
   stored by Docker's logging driver. The bundled compose files now cap it:
   ```yaml
   logging:
     driver: json-file
     options:
       max-size: "10m"
       max-file: "5"
   ```
   Docker's host file is
   `/var/lib/docker/containers/<container-id>/<container-id>-json.log` (need root).
   Check what a running container actually uses:
   ```bash
   docker inspect -f '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}' mangatrax
   ```
   Existing deployments (TrueNAS apps, plain `docker run`) must be **recreated** with
   the block above (or start with `--log-opt max-size=10m --log-file=5`); otherwise they
   keep growing. A host-wide default can be set in `/etc/docker/daemon.json`:
   ```json
   {"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "5"}}
   ```
   and requires a Docker daemon restart.

2. **In-app Logs page** — SQLite `activity_logs`. It is pruned automatically at startup
   and at most every 6 h with the Settings → Maintenance values:
   - `activity_retention_days` (default 90, **0 = keep forever**),
   - `activity_max_rows` (default 50000, **0 = uncapped**).
   Pruning deletes old rows plus the oldest excess and runs
   `PRAGMA wal_checkpoint(TRUNCATE)` so the WAL does not stay inflated. Manual **Prune
   now**: Logs page button or `POST /api/logs/prune` (also in Settings → Maintenance).
   `/health` exposes a `logs` block (row count, oldest age, DB/WAL sizes, settings).

3. **Optional rotating app file** — enable `log_to_file` in Settings → Maintenance to
   write `${DATA_DIR}/logs/mangatrax.log` via `RotatingFileHandler`
   (`log_file_max_mb` default 10, `log_file_backups` default 5; total ≈ max ×
   (backups+1)). It attaches idempotently on startup/save and is skipped with a warning
   if the directory cannot be created.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
