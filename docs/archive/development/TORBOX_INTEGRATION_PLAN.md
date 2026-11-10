# Torbox integration — implementation plan (proposal)

Status: **proposal only, awaiting go-ahead.** No code changes yet.

## 1. Goal

Add Torbox as an alternative download engine so a requested release can be fetched through the Torbox
debrid API into `DOWNLOADS_DIR`, and — when its series already exists in Calibre — auto-tagged
(ComicInfo.xml) and moved to `STAGING_DIR`:

```
REQUESTED release
   └─ engine (qBittorrent or Torbox): download into DOWNLOADS_DIR/<torrent>/
        └─ routing pass (series-level):
             • series in Calibre   → tag ComicInfo.xml, then move to STAGING_DIR
             • series not in Calibre → keep ALL volumes untouched in DOWNLOADS_DIR
                                       (until a Calibre scan registers the series)
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

## 4. Post-download routing (DOWNLOADS_DIR → STAGING_DIR)

Two directories on the **same drive** (user-approved model; supersedes the earlier
per-volume import-folder idea):

| Setting | Default | Meaning |
|---|---|---|
| `downloads_dir` | `/downloads` | raw downloads from **both** engines; files sit here untouched |
| `staging_dir` | `/staging` | final destination; only tagged files reach it, ready for a manual Calibre import |

The rule is **series-level**, not volume-level:

1. Series **not yet in Calibre** (no series template) → keep **all** volumes of that
   series in `DOWNLOADS_DIR`; do not tag or move them. A later Calibre scan that
   registers the series unlocks them.
2. Series **is in Calibre** (a template exists) → auto-tag each `.cbz` with
   ComicInfo.xml and then move it to `STAGING_DIR`, preserving its relative subpath.
3. A file that cannot be tagged (corrupt archive) stays in `DOWNLOADS_DIR`; untagged
   files are never moved.

Moves prefer an atomic `os.replace` (same device); when `st_dev` differs the pass
copies to a temporary sibling, renames it into place and deletes the source, logging
a warning because the same-drive recommendation was not met. Destinations are never
overwritten (existing file → skip + count). The pass runs after each completed
download, after every successful Calibre sync, on the periodic tag cadence and from
the manual **Process Downloads** action. Audit events:
`TAGGED_AND_MOVED_TO_STAGING`, `AWAITING_CALIBRE_SERIES` (one summary per pass), and
`DOWNLOADS_ROUTING_FAILED`. The Downloads page lists series awaiting Calibre.

## 5. Persistence & settings

Schema (guarded `PRAGMA` migrations, same pattern as earlier additions):
- `releases.downloader TEXT DEFAULT ''` — `qbit` | `torbox`
- `releases.downloader_id TEXT DEFAULT ''` — torbox `torrent_id` (qbit keeps using `torrent_hash`)

Settings (DB keys + strict `/api/settings` schema + env seeds):
`downloader_engine`, `torbox_enabled`, `torbox_api_key` (secret, masked in GET responses),
`torbox_delete_after_download`, `torbox_poll_seconds`, `torbox_max_concurrent`,
`torbox_timeout_hours`, plus `downloads_dir` (raw downloads, `DOWNLOADS_DIR` env, default
`/downloads`) next to the existing `staging_dir`. No cached-only switch (user decision).

## 6. UI

- **Settings → Torbox** card: enable toggle, API key (password input, masked), engine selector
  (qBittorrent / Torbox), "Test connection" (calls `/user/me`, shows plan + active slots), delete-after toggle.
- **Settings → Staging & Tagging** card: `downloads_dir` (raw) + `staging_dir` (final) with the
  two-folder/same-drive/series-must-exist-in-Calibre help text.
- **Downloads page**: engine-aware `/api/downloads/status` (`engine`, `state`, `progress`, `files`,
  `cached`) plus an "Awaiting Calibre Series" summary of downloads blocked on a Calibre scan, and a
  **Process Downloads** action that runs the tag+move pass.
- Dashboard "Active Downloader Queue" unchanged (reads the same endpoint).

## 7. Security & reliability

- API key stored in `app_settings`; **never** written to logs, activity details or `/api/settings` GET output.
- All Torbox/CDN URLs pass the SSRF guard (public http(s), DNS re-check, redirect-hop validation).
- Bounded polling (min 5–10 s, `bypass_cache` only for actively-waiting items), exponential backoff on
  429/5xx, honours `Retry-After`, caps concurrent jobs (setting).
- Downloads are streamed with timeouts and a configurable max size; partial files never treated as complete.
- Failure surfaces on the Downloads page and in activity logs; release stays REQUESTED for retry.
- `downloads_dir`/`staging_dir` use the existing path validation (absolute, no NUL/`..`/symlink escape,
  root containment) and must differ from each other.

## 8. Phasing

| Phase | Content | Rough size |
|---|---|---|
| 1 | Downloader protocol + `TorboxDownloader` (submit, cached check, poll, whole-torrent download, delete-after) + settings/schema/UI + `/api/downloads/status` integration + mocked-API tests | large |
| 2 | Selective per-file volume downloads (subset of volumes) + file→volume mapping/fallback + tests | medium |
| 3 | Downloads routing: `DOWNLOADS_DIR` → tag when series is in Calibre → move to `STAGING_DIR`, awaiting-series summary, audit + tests | medium |
| 4 | Polish: engine-aware downloads table, webhook events, README/docs, end-to-end mock flow | small |

## 9. Open decisions (resolved)

1. **Engine model** — selectable per install, qBittorrent default. Resolved.
2. **Download/routing folders** — raw downloads in `DOWNLOADS_DIR`, final tagged files in `STAGING_DIR`
   (same drive, series-level rule). Resolved.
3. **Keep-in-staging rule** — replaced by the series-level rule (series unknown → all volumes stay in
   downloads). Resolved.
4. **Delete from Torbox after successful download** — default on. Resolved.
5. **Cached-only mode** — not implemented (user decision). Resolved.
6. **aria2** — not used; stdlib urllib streaming only. Resolved.
