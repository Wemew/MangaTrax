# Torbox integration — implementation plan (proposal)

Status: **proposal only, awaiting go-ahead.** No code changes yet.

## 1. Goal

Add Torbox as an alternative download engine so a requested release can be fetched through the Torbox
debrid API into the staging folder, auto-tagged (existing pipeline), and then routed onward:

```
REQUESTED release
   └─ Torbox engine: create torrent → (cached?) → download selected files → staging/<torrent>/
        └─ existing auto-tag pass (ComicInfo.xml)
             └─ NEW routing pass:
                  • volumes ≤ keep_first_n   → stay in staging (manual review / BookOrbit)
                  • everything else          → moved to IMPORT_DIR/<series>/, ready for Calibre
```

Torbox is treated as a *downloader*, not a source: feed crawling, matching, volume selection and tagging
are reused unchanged.

## 2. Torbox API surface used (v1)

Base `https://api.torbox.app/v1/api`; auth `Authorization: Bearer <API_KEY>` (the download-link endpoint
takes `token=` as a query parameter instead). Sources: [Torbox SDK services docs](https://github.com/TorBox-App/torbox-sdk-py/tree/main/documentation/services),
[Python wrapper](https://github.com/eliasbenb/TorBox.py).

| Purpose | Endpoint | Notes |
|---|---|---|
| Submit | `POST /torrents/createtorrent` | multipart; `magnet` **or** torrent file; we prefer magnet (Nyaa RSS already gives it) |
| Cache check | `GET /torrents/checkcached?hash=…&format=list&list_files=true` | batch up to ~100 hashes, ~1 s |
| Poll state | `GET /torrents/mylist?id=<torrent_id>&bypass_cache=true` | list data only refreshes every **600 s**; polling needs `bypass_cache` |
| File list | same response `files[]` | `id`, `name` (path), `size` |
| Download link | `GET /torrents/requestdl?token=…&torrent_id=…&file_id=…` | link valid ~3 h to *start*; per-file, or `zip_link=true` for the whole torrent |
| Control | `POST /torrents/controltorrent` | `{"torrent_id":…, "operation":"delete"}` to free slots |
| Account | `GET /user/me` | plan / slot / quota display |

Download states: `downloading`, `uploading`, `stalled (no seeds)`, `paused`, `completed`, `cached`,
`metaDL`, `checkingResumeData`. Treat `completed` **and** `cached` as ready.

## 3. Architecture

### 3.1 Downloader abstraction (small refactor, big payoff)

```python
class DownloaderClient(Protocol):
    name: str
    enabled: bool
    def test_connection(self) -> dict: ...
    def submit(self, release, savepath, wanted_volumes) -> dict:   # {handle, state, cached}
    def status(self, handle) -> dict:                              # {state, progress, files: [...]}
    def fetch_files(self, handle, files, dest_dir, progress_cb) -> list[dict]
    def cancel(self, handle, delete_remote=False) -> bool
```

- `QBitClient` keeps its API; a thin `QBitDownloader` adapter implements the protocol (qBit writes into
  staging itself, so `fetch_files` is a no-op + completion poll).
- New `TorboxDownloader` in `backend/app/downloader/torbox.py` (stdlib `urllib` only — no new dependency).
- Selection via setting `downloader_engine` = `qbit` | `torbox` (default `qbit`, so upgrades are inert).

### 3.2 Torbox flow

1. `submit`: extract infohash from the release magnet (reuse `QBitClient.extract_torrent_hash` logic,
   move it to a shared util). `checkcached` → if cached, mark instantly available.
2. `createtorrent` (magnet; fall back to uploading the `.torrent` if only a URL exists).
   Response gives `torrent_id` + `hash`.
3. Persist `download_status=DOWNLOADING`, `downloader="torbox"`, `downloader_id=<torrent_id>`.
4. Worker polls `mylist?bypass_cache=true` every ~10 s (setting), backoff on errors, hard timeout (~6 h).
   `stalled (no seeds)` / slot errors are surfaced, not silently retried forever.
5. Once ready, choose files:
   - **All volumes wanted** → `requestdl?zip_link=true`, one streamed download, `zipfile` extract.
   - **Subset** → per-file `requestdl` for the missing volumes only.
   - File→volume mapping: run the existing parser on each `files[].name` basename. **If any file cannot
     be mapped, download everything** (safe default, logged) rather than risk a missing volume.
6. Stream each link into `staging/<safe-torrent-name>/` with the existing hardened downloader pattern
   (SSRF validation, redirect-hop checks, explicit timeouts, `.part` + atomic rename, size sanity).
7. Optional `controltorrent delete` after success to free the active slot (setting, default on).
8. Update `download_status=COMPLETED`; activity events `TORBOX_SUBMITTED` / `TORBOX_CACHED` /
   `TORBOX_DOWNLOADED` / `TORBOX_FAILED`.

### 3.3 Why not aria2

Torbox hands back plain HTTPS CDN links; one sequential stream per file is enough (cached debrid downloads
are line-rate). aria2 would add a binary/dependency, config surface and failure modes for no gain.
Rejected unless the user wants multi-connection downloads for huge bundles (noted as an option).

## 4. Post-tagging routing (the "ready for Calibre" folder)

New settings and a routing pass that runs **immediately after each auto-tag pass** (and on manual Tag CBZ):

| Setting | Default | Meaning |
|---|---|---|
| `import_dir` | `""` (disabled) | destination folder for ready-to-import files |
| `auto_move_to_import` | `true` | enable the routing pass when `import_dir` is set |
| `keep_first_volumes_in_staging` | `1` | files whose lowest volume ≤ N stay in staging |

Rules:
1. Only **successfully tagged** files are eligible — anything untagged stays in staging with a warning.
2. Volume number comes from the tag result (`volume`, e.g. `"3"`, or `"1-5"` for bundles → lowest = 1).
3. File stays in staging if lowest volume ≤ N; otherwise move to `IMPORT_DIR/<series>/<original filename>`
   (series = the stable matching key, sanitised for the filesystem).
4. Destination folder created as needed; **never overwrite** an existing file (skip + log unless identical
   size). Cross-filesystem move handled by `shutil.move`. Empty source subfolders pruned.
5. `import_dir` validated with the existing path-containment/security helpers; NULs, `..`, symlink
   escapes rejected; root containment when `MANGATRAX_PATH_ROOTS` is set.
6. Every move/log is an audit event (`MOVED_TO_IMPORT`, `KEPT_IN_STAGING`) with actor.
7. Idempotent: files already moved are gone from staging; reruns are no-ops.

## 5. Persistence & settings

Schema (guarded `PRAGMA` migrations, same pattern as earlier additions):
- `releases.downloader TEXT DEFAULT ''` — `qbit` | `torbox`
- `releases.downloader_id TEXT DEFAULT ''` — torbox `torrent_id` (qbit keeps using `torrent_hash`)
- `releases.imported_at TEXT DEFAULT ''` — set when a file is moved to `import_dir`

Settings (DB keys + strict `/api/settings` schema + env seeds):
`downloader_engine`, `torbox_enabled`, `torbox_api_key` (secret, masked in GET responses),
`torbox_delete_after_download`, `torbox_poll_seconds`, `torbox_cached_only`, plus the three routing keys.

## 6. UI

- **Settings → Torbox** card: enable toggle, API key (password input, masked), engine selector
  (qBittorrent / Torbox), "Test connection" (calls `/user/me`, shows plan + active slots), delete-after toggle.
- **Settings → Staging & Tagging** card: `import_dir`, `auto_move_to_import`, `keep_first_volumes_in_staging`.
- **Downloads page**: `/api/downloads/status` becomes engine-aware — same payload shape with
  `engine`, `state`, `progress`, `files`, `cached`; Torbox queue listed alongside qBittorrent.
- Dashboard "Active Downloader Queue" unchanged (reads the same endpoint).

## 7. Security & reliability

- API key stored in `app_settings`; **never** written to logs, activity details or `/api/settings` GET output.
- All Torbox/CDN URLs pass the SSRF guard (public http(s), DNS re-check, redirect-hop validation).
- Bounded polling (min 5–10 s, `bypass_cache` only for actively-waiting items), exponential backoff on
  429/5xx, honours `Retry-After`, caps concurrent jobs (setting).
- Downloads are streamed with timeouts and a configurable max size; partial files never treated as complete.
- Failure surfaces on the Downloads page and in activity logs; release stays REQUESTED for retry.

## 8. Phasing

| Phase | Content | Rough size |
|---|---|---|
| 1 | Downloader protocol + `TorboxDownloader` (submit, cached check, poll, whole-torrent download, delete-after) + settings/schema/UI + `/api/downloads/status` integration + mocked-API tests | large |
| 2 | Selective per-file volume downloads (subset of volumes) + file→volume mapping/fallback + tests | medium |
| 3 | Tag→route-to-import (import_dir, keep N, audit, path containment) + tests | medium |
| 4 | Polish: engine-aware downloads table, webhook events, README/docs, end-to-end mock flow | small |

## 9. Open decisions (need user input)

1. **Engine model** — replace qBittorrent, or selectable per install (recommended: selectable, default qbit)?
2. **Import folder** — exact path on your host, and flat vs `<series>/<file>` layout (recommended: per-series)?
   Is BookOrbit watching that folder?
3. **Keep-in-staging rule** — keep exactly volume 1, or first N configurable? And for a *bundle* file that
   contains volume 1 among others, keep the whole file in staging (recommended) or move it?
4. **Delete from Torbox after successful download** — default on (frees slots)?
5. **Cached-only mode** — provide a switch that only accepts cached torrents (instant) and leaves uncached
   ones for Torbox to fetch later?
6. **aria2** — skip it (recommended) or include as optional fetcher?
