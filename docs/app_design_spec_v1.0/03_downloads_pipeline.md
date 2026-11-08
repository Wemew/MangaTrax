# MangaTrax — Domain 03: Downloads, Downloaders, Staging/Inventory, Failed Documents, CBZ Tagger & Manual Pickup

## 1. Scope

**Scope.** This document specifies the acquisition half of MangaTrax: release → download engine (qBittorrent or Torbox) → raw `downloads_dir` → tag/move into flat `staging_dir` → Calibre import, plus everything the Downloads page and Manual Pickup page expose (queues, inventory, failed documents, staging groups, duplicates, ComicInfo audit/repair, manual tagging, per-file delete).
Every statement below is derived from the current implementation (`backend/app/**`, `backend/app/templates/**`, `tests/**`) at commit `86a7bd8`; where `PROJECT.md`/`README.md` disagree the code wins.
Feature IDs are `DL-001` … `DL-081`; each ID appears once in the inventory table (§2) and once as a checkable claim in the consensus checklist (§9).

---

## 2. Feature inventory

Lifecycle legend: **ACTIVE** = wired into a live user path; **PARTIAL** = works but with a known limitation/dead sub-path documented in §3/§8; **VESTIGIAL** = code present but no live caller.

| ID | Feature | State | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| DL-001 | Download-engine selection (`qbit` vs `torbox`) + client factory | ACTIVE | Settings chooses which engine downloads; unknown values fall back to `qbit` with a warning | `backend/app/downloader/__init__.py:12-78` | Settings save; every submit/status build | `app_settings.downloader_engine` |
| DL-002 | qBittorrent connection test/login (8 s result cache) | ACTIVE | "Test Downloader Connection" reports version or a specific auth/HTTP error | `backend/app/downloader/qbit.py:33-140` | `POST /api/downloader/test`, page render, status API | None (in-memory `_last_test_res`) |
| DL-003 | qBittorrent `add_torrent` submit | ACTIVE | Release is queued in qBittorrent under category `manga`, tag `mangatrax`, savepath = `downloads_dir` | `backend/app/downloader/qbit.py:142-189` | auto-download, `/api/releases/{id}/download`, dedupe sweep | `releases.downloader`, `torrent_hash`, `download_status=DOWNLOADING` |
| DL-004 | qBittorrent pause/resume/delete queue controls | ACTIVE | Per-row pause/resume/delete buttons in the queue table | `backend/app/downloader/qbit.py:220-237`; `backend/app/main.py:3585-3601` | Row buttons | qBittorrent state only |
| DL-005 | qBittorrent selective per-file download (`filePrio`) | ACTIVE | Bundle torrents only download missing volumes; ancillary `.txt/.nfo/.url/.html/.lnk` files are always skipped | `backend/app/downloader/qbit.py:269-368` | Manual download, auto-download, 30 s filter pass | qBittorrent file priorities |
| DL-006 | 30 s qBittorrent selective-filter pass | ACTIVE | Owned volumes inside an active bundle stop downloading automatically once metadata resolves | `backend/app/scheduler.py:4912-4974` | Worker loop, every 30 s | Audit `SELECTIVE_DOWNLOAD` log |
| DL-007 | `DownloaderClient` protocol + `QBitDownloader` adapter | VESTIGIAL | None (no UI); only `test_torbox_downloader.py` exercises it | `backend/app/downloader/base.py:90-110`; `backend/app/downloader/qbit_adapter.py:20-81`; unused getter `backend/app/scheduler.py:999-1003` | — | None |
| DL-008 | In-app downloader configure/test endpoints | ACTIVE | Save qBittorrent URL/user/password/enabled/category/tags and immediately test | `backend/app/main.py:3516-3576`; `backend/app/templates/downloads.html:86-114` | "Save & Test Connection" | `app_settings.qbit_*` |
| DL-009 | Torbox client construction + secret handling | ACTIVE | Torbox engine with masked API key; `__repr__`/errors never include the key | `backend/app/downloader/torbox.py:186-215,218-318`; `backend/app/main.py:1878-1889,4211-4217` | Settings save; every poll/fetch | `app_settings.torbox_api_key` |
| DL-010 | Torbox typed HTTP error taxonomy | ACTIVE | Auth 401/403, rate-limit 429 (+Retry-After), 404, slot/concurrency 409/402 map to typed errors shown in UI | `backend/app/downloader/base.py:20-46`; `backend/app/downloader/torbox.py:271-318` | Every Torbox call | `releases.download_error` on failure |
| DL-011 | Torbox submission identifier resolution | ACTIVE | A release with only a Nyaa `/view/<id>` link still submits by deriving `/download/<id>.torrent`; HTML/JSON bodies are rejected | `backend/app/downloader/torbox.py:464-543` | `submit()` | None (resolution is transient) |
| DL-012 | Torbox `checkcached` probe | ACTIVE | Cached torrents are flagged "cached" and skipped straight to ready | `backend/app/downloader/torbox.py:369-381,554-562` | `submit()` | `releases.torbox_progress_json`/cached flag in handle |
| DL-013 | Torbox `createtorrent` submit (magnet multipart / `.torrent` upload) | ACTIVE | Magnet is posted as multipart `magnet` field; otherwise validated bencoded bytes are uploaded | `backend/app/downloader/torbox.py:320-366,564-576` | `submit()` | `releases.downloader_id` (`<id>` or `queued:<id>`) |
| DL-014 | Queued-slot handle encoding + promotion | ACTIVE | When the account has no free slot the row shows "queued: waiting for a free Torbox slot" and is polled until Torbox assigns a real `torrent_id` | `backend/app/downloader/base.py:48-68`; `backend/app/downloader/torbox.py:599-610,667-737`; `backend/app/scheduler.py:2989-3006` | Poller | `releases.downloader_id='queued:<n>'` then promoted to `<id>` |
| DL-015 | Torbox state normalisation (`ready`/`processing`/`downloading`/`stalled`/`queued`/`failed`) | ACTIVE | `download_present` gating prevents "Ready" being shown (and 404 fetch attempts) while Torbox is still finalising | `backend/app/downloader/torbox.py:68-76,617-637` | Every status() call | `releases.torbox_progress_json.phase` |
| DL-016 | Torbox adaptive poller thread + wake + "Check now" | ACTIVE | Polls every `max(5, torbox_poll_seconds)` s while busy, backs off to 15-30 s idle; "Check now" forces a pass | `backend/app/scheduler.py:2815-2867,2837-2849`; `backend/app/main.py:2126-2143` | Thread start; submit; button | `app_settings.torbox_last_poll_at` |
| DL-017 | Torbox submit concurrency limit + hard timeout | ACTIVE | Extra submissions are refused above `torbox_max_concurrent`; rows older than `torbox_timeout_hours` are failed with an explanation | `backend/app/scheduler.py:1495-1510,2907-2949` | Submit / poll | `releases.download_error`, `download_status=FAILED` |
| DL-018 | Strict Torbox volume-selection planner | ACTIVE | Decides `skip` / `zip` / `files`; refuses to download a whole torrent when names are unmappable or wanted volumes are absent | `backend/app/scheduler.py:2087-2251` | Fetch when ready; strict retry | Audit `SELECTIVE_DOWNLOAD` log |
| DL-019 | Torrent file-name → volume parser | ACTIVE | Handles `v01`, `Vol. 3`, `[Vol 1-3]`, `(v1-v3)`, strips `Part 02`; last bare number as fallback, years/resolutions excluded | `backend/app/scheduler.py:1972-2034` | Planner | None |
| DL-020 | Legacy permissive selection (`torbox_strict_volume_selection=false`) | ACTIVE | Falls back to whole-torrent zip when names cannot be mapped or when the subset would select everything | `backend/app/scheduler.py:2036-2062,2116-2129` | Fetch when setting disabled | None |
| DL-021 | Per-file `requestdl` fetch with fresh-link retries | ACTIVE | 403/404/410/416/429/503 retried 3× (1/2/4 s) with a new `requestdl` URL each attempt | `backend/app/downloader/torbox.py:791-804,1231-1297` | Fetch | `releases.torbox_progress_json` |
| DL-022 | Whole-torrent zip fetch + zip-slip-safe extraction | ACTIVE | Single zip download when every file is needed and small; absolute/`..`/symlink entries rejected, partial extraction removed | `backend/app/downloader/torbox.py:1299-1338,1149-1195` | Fetch (`action=zip` / no file list) | Files in `downloads_dir/<safe title>/` |
| DL-023 | Parallel file fetches + HTTP-Range multi-connection download | ACTIVE | Files fetched concurrently (`torbox_download_concurrency`); files > 16 MiB fetched as N ranges, reassembled and atomically renamed | `backend/app/downloader/torbox.py:999-1103,1299-1382` | Fetch | `.part`/`.partN` temp files, then final file |
| DL-024 | Torbox size caps | ACTIVE | Files/whole-torrent archives above `torbox_max_download_gb` are rejected with the exact size and cap before streaming | `backend/app/downloader/torbox.py:836-841,885-891,1344-1353`; `backend/app/scheduler.py:2064-2085` | Planner/fetch/stream | `releases.download_error` |
| DL-025 | Fetch failure containment | ACTIVE | Partial files removed, row FAILED with reason + attempt count + UTC timestamp, remote job deleted when `torbox_delete_on_failure` | `backend/app/scheduler.py:1920-1958,2529-2548`; `backend/app/downloader/torbox.py:1202-1229` | Any fetch/poll error | `download_error`, `download_attempts`, `torbox_progress_json.phase` |
| DL-026 | Manual-pickup hold on unmappable torrent | ACTIVE | Instead of failing, the torrent is kept on Torbox and a Manual Pickup record is created (when enabled and under cap) | `backend/app/scheduler.py:1640-1682,2564-2581` | Strict planner raises on fetch | `torbox_manual_pickups` row; `download_status=MANUAL_PICKUP` |
| DL-027 | Post-download delete-after + routing + latency accounting | ACTIVE | After a successful fetch the release is COMPLETED, files routed, inventory refreshed, remote torrent optionally deleted, latency logged | `backend/app/scheduler.py:2646-2756` | Fetch success | `download_files_json`, `downloaded_at`, `torbox_progress_json` |
| DL-028 | Bounded fetch executor + per-release single-flight | ACTIVE | Ready releases are handed to a `torbox-fetch` thread pool; the same release never fetches twice concurrently | `backend/app/scheduler.py:2475-2484,2770-2813` | Poller (async mode) | In-memory `_torbox_fetching`/futures |
| DL-029 | Torbox retry endpoint (rate-limited) | ACTIVE | "Retry" re-submits with strict rules, cancels the stale remote job first, 5 s rate limit | `backend/app/main.py:3604-3663` | Retry button | `releases.download_status` |
| DL-030 | Torbox remove endpoint | ACTIVE | "Remove" drops the row from the queue; FAILED rows are cleared locally only, others get best-effort remote delete | `backend/app/main.py:3666-3701` | Remove button | `clear_downloader_submission` resets all submission columns |
| DL-031 | Clear all failed Torbox items | ACTIVE | "Clear failed" clears every FAILED Torbox row locally with progress reporting | `backend/app/main.py:3704-3728`; `backend/app/templates/downloads.html:840-870` | Button | Same reset as DL-030 |
| DL-032 | Manual release download route | ACTIVE | "Download" on a release sends it to the configured engine (Torbox or qBittorrent) using freshly resolved missing volumes | `backend/app/main.py:3738-3824` | Release page | Submission columns + audit `MANUAL_DOWNLOAD` |
| DL-033 | Derived release download-state payload | ACTIVE | Releases card/buttons show Downloading / Downloaded (awaiting staging) / In staging / In Calibre / Failed | `backend/app/main.py:1303-1361`; `/api/releases/{id}/state` `backend/app/main.py:3730-3735` | Release page / API | Derived from `download_status`+`routed_at` |
| DL-034 | `/downloads/manual-pickup` page | ACTIVE | Lists held torrents, file tables, volume inputs, per-pickup actions | `backend/app/main.py:1622-1660`; `backend/app/templates/manual_pickup.html:1-282` | Navigation/banner | Renders `torbox_manual_pickups` |
| DL-035 | Manual-pickup list API | ACTIVE | `GET /api/torbox/manual-pickups?include_finished=0/1` returns pickups + active count | `backend/app/main.py:1663-1675` | Page/JS | — |
| DL-036 | Manual file→volume mapping API | ACTIVE | Edits a file's suggested volume (or clears it) and persists the mapping | `backend/app/main.py:1678-1687`; `backend/app/scheduler.py:1727-1755` | Volume input `onchange` | `torbox_manual_pickups.files_json` |
| DL-037 | Corrected wanted-volumes API | ACTIVE | "Save volumes" rewrites release volumes, missing volumes, `download_wanted_json` and pickup `wanted_json` | `backend/app/main.py:1690-1701`; `backend/app/scheduler.py:1757-1776` | "Save volumes" | `releases.volumes_json/missing_vols_json/download_wanted_json`, pickup `wanted_json` |
| DL-038 | Manual-pickup fetch API (selected or all) | ACTIVE | "Fetch selected" / "Fetch whole torrent" runs as a background task with per-file progress | `backend/app/main.py:1704-1734`; `backend/app/scheduler.py:1778-1872` | Buttons | `torbox_manual_pickups.status/files_json`; release files |
| DL-039 | Strict retry from a pickup | ACTIVE | "Retry strict mapping" re-plans against corrected volumes and fetches only matching files | `backend/app/main.py:1737-1757`; `backend/app/scheduler.py:1874-1899` | Button | `torbox_manual_pickups.status` |
| DL-040 | Discard pickup | ACTIVE | Deletes the remote torrent and marks the pickup + release failed | `backend/app/main.py:1760-1768`; `backend/app/scheduler.py:1901-1918` | Button | pickup `status='discarded'`; release FAILED |
| DL-041 | Pickup cap + expiry sweep | ACTIVE | Above `torbox_manual_pickup_max_items` new holds fall back to FAIL; expired holds are removed from Torbox and failed | `backend/app/scheduler.py:1612-1622,1648-1657,1684-1718`; sweep every 300 s `backend/app/scheduler.py:5235-5242` | Hold creation; 5-min timer | pickup `status='expired'`; release FAILED |
| DL-042 | `/api/downloads/status` | ACTIVE | Always-200 normalized queue payload (qBittorrent rows or Torbox release rows), degradation flags, totals, attached inventory; 1.5 s default / 5 s max socket budget | `backend/app/main.py:2104-2123,473-632,916-1034` | Poller JS / page | Reads persisted state |
| DL-043 | Torbox status last-good cache, stale serving, speed history | ACTIVE | A Torbox API blip shows the last known queue (marked stale) instead of an empty table; sparkline speed history | `backend/app/main.py:52-70,814-913` | Status API | In-memory cache only |
| DL-044 | Torbox "Check now" poll API | ACTIVE | Runs one poll pass and returns fresh counters; 409 when Torbox is not the active engine | `backend/app/main.py:2126-2143`; `backend/app/scheduler.py:2858-2867` | Button | `torbox_last_poll_at` |
| DL-045 | Auto-download diagnostics | ACTIVE | Read-only card explains why nothing auto-downloads (checklist, recent decisions, last-poll hint, limitations) | `backend/app/main.py:2213-2217`; `backend/app/scheduler.py:1128-1246`; `backend/app/templates/downloads.html:6-19` | Page load / Refresh | Reads `activity_logs`, feed-check state |
| DL-046 | Tag+move routing rule (release → staging) | ACTIVE | Files whose series resolves to a tracked/Calibre template are tagged and moved; everything else stays untouched in downloads | `backend/app/tagger/pipeline.py:493-793` | "Process Downloads", 45 s pass, post-fetch, post-match | Moves files; audit `TAGGED_AND_MOVED_TO_STAGING` |
| DL-047 | Flat staging + collision policy | ACTIVE | Staging is always the flat root; same name+size = skip, same name different size = `<stem> (2)<ext>` | `backend/app/tagger/pipeline.py:393-424,668-689` | Routing/flatten | Filesystem |
| DL-048 | Flatten staging action | ACTIVE | Moves every nested staged file to the staging root, drops same-size duplicates, prunes emptied dirs | `backend/app/tagger/pipeline.py:431-490`; `backend/app/main.py:2162-2170` | "Flatten staging" | Filesystem; returns counts |
| DL-049 | Prune-empty-folders action | ACTIVE | Removes EMPTY leftover dirs only, never `rmtree`, skipping in-flight download dirs and recently modified dirs | `backend/app/tagger/pipeline.py:294-390,796-814`; `backend/app/main.py:2146-2159` | "Prune empty folders" | Audit `DOWNLOADS_PRUNED` |
| DL-050 | Opt-in archive→CBZ conversion during routing | ACTIVE | With `convert_archives_to_cbz=true`, `.cbr/.rar/.7z/.cb7/.zip` are converted then routed; otherwise they stay failed | `backend/app/tagger/pipeline.py:550-579`; `backend/app/archive_conversion.py:137-185` | Routing pass | New `.cbz`; audit `ARCHIVE_CONVERTED_TO_CBZ` |
| DL-051 | Previous-volume naming inheritance + intervention flag | ACTIVE | `Series v01.cbz` → `Series v02.cbz` naming/title inheritance; files that cannot be named safely are flagged "needs naming intervention" | `backend/app/volume_naming.py:19-204`; `backend/app/tagger/pipeline.py:603-646` | Routing; staging groups | Audit `VOLUME_NAME_INHERITED`/`VOLUME_NAME_INTERVENTION_REQUIRED` |
| DL-052 | Awaiting-Calibre scan/grouping | ACTIVE | Downloads page lists series with no Calibre/tracked match and their files | `backend/app/tagger/pipeline.py:190-235`; `backend/app/main.py:1817-1826` | Page render | Read-only |
| DL-053 | Manual awaiting-series match | ACTIVE | "Match series" maps an unknown series to a Calibre series or a new tracked title, registers aliases, re-keys releases and immediately re-routes | `backend/app/main.py:2328-2458` | Match modal | `series_alias_overrides`, `series_tracking`, `releases.series_name`; audit |
| DL-054 | "Process Downloads" endpoint | ACTIVE | Runs the routing pass plus the legacy staging auto-tag and reports counts | `backend/app/main.py:2076-2099`; `backend/app/templates/base.html:594-617` | Button | Audit + task |
| DL-055 | Legacy staging auto-tagger | PARTIAL | Tags already-staged CBZs from Calibre templates, but never overwrites an existing ComicInfo.xml | `backend/app/scheduler.py:3826-3849`; `backend/app/tagger/cbz_tagger.py:260-334` | 5-min timer, Process Downloads | ComicInfo.xml inside CBZ |
| DL-056 | Periodic routing/inventory/tag timers | ACTIVE | Finished downloads become visible and get routed without pressing anything (45 s inventory+route, 5-min auto-tag, 5-min stale reconcile) | `backend/app/scheduler.py:5252-5260,5211-5219,5221-5232`; `backend/app/scheduler.py:2281-2294` | Worker loop | `downloads_inventory_json`, files moved |
| DL-057 | Series-template resolution (exact → cleaned → fuzzy) | ACTIVE | "Known" means exact normalized match, cleaned-name match, alias override, or a unique difflib ≥ 0.86 match | `backend/app/tagger/pipeline.py:44-157` | Routing, inventory state, diagnostics | None |
| DL-058 | Raw downloads inventory scan + persistence | ACTIVE | "Downloads Folder (raw)" table refreshes on page load and on Rescan, with age/size/series/volumes/state/matched release | `backend/app/downloads_inventory.py:159-263`; `backend/app/main.py:1771-1849` | Page render, 45 s timer, Rescan | `app_settings.downloads_inventory_json`, `downloads_scanned_at` |
| DL-059 | Inventory entry states | ACTIVE | Partial / Unmatched / Ready to move / Awaiting Calibre badges | `backend/app/downloads_inventory.py:145-156` | Scan | In the persisted JSON |
| DL-060 | Raw inventory cleanup audit | ACTIVE | "Clean up raw duplicates" previews duplicate entries, copies already in staging, and copies provably owned in Calibre | `backend/app/downloads_inventory_cleanup.py:127-211`; `backend/app/main.py:2957-2964` | Button | Read-only preview |
| DL-061 | Raw inventory clear (duplicates/already_present/all) | ACTIVE | Deletes revalidated raw entries as a background task; never deletes the only copy | `backend/app/downloads_inventory_cleanup.py:273-325`; `backend/app/main.py:2967-2994` | Confirm buttons | Deletes files; audit events |
| DL-062 | Per-row raw inventory delete | ACTIVE | Deletes one current inventory entry after revalidation, prunes empty parents, refreshes inventory | `backend/app/downloads_inventory_cleanup.py:328-349`; `backend/app/main.py:2997-3017` | Row Delete | Filesystem; audit `DOWNLOAD_INVENTORY_ENTRY_DELETED` |
| DL-063 | Failed documents list | ACTIVE | Table of stuck raw files with detected type, reason, matched release, size, attempts, retry/convert/delete actions | `backend/app/download_failures.py:160-228`; `backend/app/main.py:3020-3024` | Page JS | `download_failures` rows |
| DL-064 | Failed documents scan | ACTIVE | Walks every raw file and records anything that cannot become a staged CBZ (magic-byte detection) | `backend/app/download_failures.py:23-157`; `backend/app/main.py:3027-3044` | "Scan for failed files" | Upserts `download_failures` |
| DL-065 | Failed document retry | ACTIVE | Retries the matched release's download when it has a URL and is not Calibre/ignored; bumps attempts | `backend/app/main.py:3047-3075` | Row Retry | `download_failures.status='retried'`, `attempts+1` |
| DL-066 | Failed document convert-to-CBZ | ACTIVE | Converts one convertible archive, clears the failure row, re-runs routing, all as a background task | `backend/app/main.py:3078-3123` | Row "Convert to CBZ" | New `.cbz`; failure row deleted |
| DL-067 | Failed document delete / delete-all | ACTIVE | Deletes the file(s), prunes empty parents, then reconciles releases; bulk delete requires an explicit confirm | `backend/app/main.py:3126-3197` | Row Delete / Delete all | Filesystem; failure row `status='deleted'` |
| DL-068 | Archive→CBZ converter safety contract | ACTIVE | Validates entries (traversal/symlink/size caps), writes temp zip, validates, atomic rename, deletes source only on success | `backend/app/archive_conversion.py:20-185` | Convert button; routing (DL-050) | New `.cbz` |
| DL-069 | Staging grouped by ComicInfo Series + master retag/link | ACTIVE | Staging table collapses into per-series sections with counts, tagged/untagged, intervention flags, "Match series" retag | `backend/app/staging_groups.py:16-183`; `backend/app/main.py:3200-3247` | Page render; retag button (background task) | Rewrites ComicInfo `<Series>`; aliases + tracking link |
| DL-070 | Staging duplicates audit (already in Calibre) | ACTIVE | Read-only list of staged files whose actual owned volumes all exist in Calibre | `backend/app/staging_duplicates.py:86-139`; `backend/app/main.py:3334-3341` | "Audit staging duplicates" | Read-only |
| DL-071 | Staging duplicates purge | ACTIVE | Purges verified staging copies only; collapses release to IN_CALIBRE when nothing remains | `backend/app/staging_duplicates.py:142-269`; `backend/app/main.py:3344-3367` | Purge buttons (confirm) | Deletes files; `releases.status=IN_CALIBRE` |
| DL-072 | ComicInfo `<Series>` audit classification | ACTIVE | Classifies every staged CBZ: ok/repairable/ambiguous/unknown_series/missing_series/untagged/invalid/unreadable with reason | `backend/app/tagger/series_audit.py:95-435,486-531`; `backend/app/main.py:3370-3384` | "Audit Series Tags" | Read-only |
| DL-073 | ComicInfo repair for explicitly selected files | ACTIVE | Rewrites only provably-safe Series/Writer/Penciller/Publisher/Genre/Number/Volume/Title for named files (max 200) | `backend/app/main.py:3387-3493`; `backend/app/tagger/series_audit.py:438-483` | "Repair Selected" | ComicInfo.xml inside CBZ; audit `CBZ_SERIES_REPAIRED` |
| DL-074 | Tagger preview + manual tag | ACTIVE | Modal shows current ComicInfo + parsed metadata; saves user metadata, optional rename, defaults `Title`/`Manga`, replaces corrupt XML | `backend/app/main.py:2677-2850`; `backend/app/templates/downloads.html:616-670` | "Preview / Tag", "Fix naming" | ComicInfo.xml; audit `MANUAL_TAG_CBZ`, `STAGING_FILE_RENAMED` |
| DL-075 | Per-file delete (staging or awaiting downloads) | ACTIVE | Two-step inline delete of exactly one verified file, then reconcile + cache invalidation | `backend/app/main.py:3250-3331`; `backend/app/templates/downloads.html:1592-1675` | Row Delete | Filesystem; audit `DOWNLOAD_FILE_DELETED` |
| DL-076 | Atomic ComicInfo rewrite primitive | ACTIVE | Rebuilds the zip beside the original, preserves every other entry byte-for-byte, swaps atomically, collapses duplicate ComicInfo entries | `backend/app/tagger/cbz_tagger.py:131-172` | Manual tag, repair, group retag | CBZ rewritten in place |
| DL-077 | Calibre-format ComicInfo formatters | ACTIVE | Title `Series vNN`, authors `A & B`, tags `A, B`, volume `28`/`1.5`, bundle `1-3` | `backend/app/tagger/naming.py:65-249`; `backend/app/tagger/cbz_tagger.py:211-240` | Tagging/audit | ComicInfo element values |
| DL-078 | Staged-volume ownership map (staging counts as owned) | ACTIVE | Volumes sitting in staging (or COMPLETED+routed releases) count as owned for wanted/missing/discovery/auto-download | `backend/app/staged_ownership.py:54-152`; `backend/app/scheduler.py:226-236` | Every release match | 60 s in-memory cache |
| DL-079 | In-flight dedupe + PENDING_DEDUPE sweep | ACTIVE | A second release for a volume already downloading/staged is not auto-submitted; it is retried when the reservation clears | `backend/app/scheduler.py:1248-1304,3703-3715,2329-2455`; `backend/app/database.py:2252-2311` | Feed poll; 60 s sweep | `releases.download_status='PENDING_DEDUPE'`; audit `IN_FLIGHT_DEDUPE` |
| DL-080 | Auto-download submission on feed poll | ACTIVE | New/transitioning REQUESTED releases are submitted automatically when global+feed toggles, engine, URL and missing volumes all agree | `backend/app/scheduler.py:3676-3781` | Feed poll | Submission columns; audit `AUTO_DOWNLOAD_SUBMITTED`/`SKIPPED` |
| DL-081 | Stale-release reconcile (files vanished) | ACTIVE | COMPLETED releases whose recorded files are gone are re-pointed at a found copy, confirmed in Calibre, or reset to IDLE | `backend/app/downloads_reconcile.py:108-195`; `backend/app/scheduler.py:5139-5150` | 5-min timer, Rescan, deletes | `releases.download_status/download_files_json/download_error` |

---

## 3. How it works

### 3.1 The two-engine model and the end-to-end pipeline

MangaTrax has exactly two download engines behind one small contract. `DOWNLOADER_ENGINES = ("qbit", "torbox")` and `DEFAULT_DOWNLOADER_ENGINE = "qbit"` (`backend/app/downloader/__init__.py:12-13`). `resolve_downloader_engine(db)` reads `app_settings.downloader_engine`, lowercases/trims it, logs a warning and falls back to `qbit` for unknown values (`backend/app/downloader/__init__.py:32-39`). `build_downloader(db, qbit_client=None, engine=None)` constructs a `TorboxDownloader` from settings (clamping `torbox_max_download_gb` to 1-10240 and passing concurrency/chunk settings) or a `QBitClient` wrapper (`backend/app/downloader/__init__.py:42-78`). Note that the scheduler normally does **not** go through the factory for qBittorrent: `MangaScheduler.get_qbit_client()` builds a `QBitClient` directly from `qbit_*` settings (`backend/app/scheduler.py:984-993`) and every qBittorrent call site uses it; `MangaScheduler.get_downloader()` (`backend/app/scheduler.py:999-1003`) is never called anywhere in the app (grep: no call sites), and the `QBitDownloader` adapter it would return is exercised only by `tests/test_torbox_downloader.py:332-346`. That is why DL-007 is marked VESTIGIAL.

The pipeline is a **two-folder model** with one deliberate rule (module docstring, `backend/app/tagger/pipeline.py:1-17`):

1. `downloads_dir` (setting; scheduler default `/downloads`, `backend/app/tagger/pipeline.py:40`, `backend/app/scheduler.py:1321-1329`) receives raw files from both engines. qBittorrent writes straight into it (savepath = `downloads_dir`, `backend/app/main.py:3792-3797`); Torbox fetches into a per-release subfolder `downloads_dir/<sanitised title>` (`backend/app/scheduler.py:1005-1010,1513-1514,2559`).
2. `staging_dir` (setting, `backend/app/main.py:353-354`) is the FLAT hand-off to Calibre/BookOrbit. Only tagged CBZs move there; `MangaTrax never writes into the Calibre library` (`backend/app/templates/downloads.html:450-455`).
3. A `.cbz` whose parsed series resolves to a tracked/Calibre template is tagged (ComicInfo.xml) and moved to the staging root; anything else stays untouched in downloads and is either reported as "awaiting Calibre" or as a failed document.
4. Calibre (external) imports staging; the next Calibre sync flips release status to `IN_CALIBRE`, and `staged_ownership` stops counting those volumes (DL-078).

```text
feed poll / manual click / dedupe sweep
        │  submit (engine selected by downloader_engine)
        ▼
qBittorrent ── writes files ──────────────┐
Torbox ── poller → strict selection → fetch ┘
        ▼
downloads_dir (raw; per-torrent folders for Torbox, torrent folders for qbit)
        │  routing pass (45 s / Process Downloads / post-fetch / post-match)
        │  parse series → resolve template → [convert] → tag ComicInfo → collision check → move
        ▼
staging_dir (flat)  ── external Calibre/BookOrbit import ──▶ Calibre library
        │                                                        │
        └─ release: download_status=COMPLETED, routed_at set ────┘ Calibre sync → status IN_CALIBRE
```

Storage-protection policy that spans the whole pipeline: `STORAGE` probes are persisted by `storage_health` and rendered as a warning partial (`backend/app/main.py:357-394`, `backend/app/templates/downloads.html:54`); `main.py` re-probes the two mounts whenever `staging_dir`/`downloads_dir` change and immediately runs an inventory+routing pass (`backend/app/main.py:4269-4278`).

### 3.2 qBittorrent cluster

**Client and authentication.** `QBitClient` is a tiny stdlib Web-API client (`backend/app/downloader/qbit.py:11-31`): cookie jar, `login()` posting `username/password` to `/api/v2/auth/login` and accepting either `Ok.` or a `SID` cookie (`:33-69`), lazy `_ensure_auth()` (`:71-74`), and a 3 s default timeout. `test_connection(force=False, max_age=8.0)` caches the last result for 8 s and returns a structured `{success, version, message}` for disabled/unconfigured/auth-failed/HTTP-error/connection-error cases (`:76-140`). `enabled=false` short-circuits every call to a no-op `False`/empty list, so a disabled engine can never leak a request.

**Submit.** `add_torrent(url_or_magnet, savepath, category="manga", tags="mangatrax", paused=False)` posts `urls/category/tags/paused[/savepath]`; success is `status==200 and "Fails" not in body` (`backend/app/downloader/qbit.py:142-189`). The scheduler-level submit path is the release row itself: on success `db.update_download_handle(release_id, "qbit", downloader_id=<infohash>, torrent_hash=<infohash>, wanted_volumes=...)` writes `download_status='DOWNLOADING'`, `downloader='qbit'`, `downloader_started_at=CURRENT_TIMESTAMP`, clears error/attempts (`backend/app/database.py:2655-2686`). The hash is extracted by `extract_torrent_hash` which accepts 40-char hex and 32-char base32 `btih` (`backend/app/downloader/base.py:70-88`).

**Selective volume download.** `apply_selective_volume_download(torrent_hash, wanted_volumes)` (`backend/app/downloader/qbit.py:285-368`) is the qBittorrent analogue of Torbox strict selection, with weaker guarantees:

* If no file list is available yet it returns `{"status": "pending_metadata"}` (metadata not resolved) — the caller may retry later.
* It only considers archive files (`.cbz,.zip,.cbr,.rar,.pdf,.tar,.7z`); `len(manga_files) <= 1` returns `single_archive` ("full archive will be downloaded and split in staging") — i.e. no per-volume selection is possible for a single big archive.
* Volume numbers come from a regex over the file name (`vol|v` + number, `backend/app/downloader/qbit.py:316-320`). Files whose volumes are not wanted get `priority=0` (`set_file_priorities`, `:269-283`); non-volume ancillary files (`.txt/.nfo/.url/.html/.lnk`) are always skipped; unparseable non-ancillary files are **kept** (`:328-348`).
* Callers only invoke it for bundles with a partial missing set: manual download (`backend/app/main.py:3807-3813`), auto-download (`backend/app/scheduler.py:3769-3775`), dedupe sweep (`backend/app/scheduler.py:2431-2436`), and the 30 s pass below.

**30 s filter pass.** `check_and_filter_active_downloads()` (`backend/app/scheduler.py:4912-4974`) selects `download_status='DOWNLOADING' AND is_bundle=1 AND (downloader IS NULL OR downloader != 'torbox')`, resolves a missing torrent hash from `download_url` when absent (writing it back via `update_download_status`), skips rows with no/complete missing list (`if not missing`; `if vols and len(missing) >= len(vols)`), then applies the selective filter and audits `SELECTIVE_DOWNLOAD`. This is the mechanism that makes a qBittorrent bundle stop downloading already-owned volumes after metadata arrives (`backend/app/scheduler.py:5262-5264`).

**Queue controls.** `POST /api/downloader/pause/{hash}`, `/resume/{hash}`, `DELETE /api/downloader/delete/{hash}` (`backend/app/main.py:3585-3601`) call the corresponding client methods and return `{"status": "success"|"error"}`; the template reloads the page 800 ms after a control (`backend/app/templates/downloads.html:721-750`). `GET /api/downloader/queue` (`backend/app/main.py:3578-3583`) exists but the page does not call it — the page renders from `/api/downloads/status` instead. Delete passes `delete_files=False`, i.e. it removes the torrent from qBittorrent but leaves downloaded files on disk (`backend/app/main.py:3600`).

**Configure/test.** `POST /api/downloader/test` accepts transient `qbit_url/username/password` (or uses the saved client) and returns the test payload (`backend/app/main.py:3516-3535`). `POST /api/downloader/configure` persists `qbit_url` (only when non-empty), `qbit_username`, `qbit_password` (only when supplied), `qbit_enabled`, `qbit_category` (default `manga`) and `qbit_tags` (default `mangatrax`), audits `DOWNLOADER_CONFIGURED` and immediately tests (`backend/app/main.py:3548-3576`). The inline panel is shown automatically when disconnected and can be toggled (`backend/app/templates/downloads.html:86-114`).

**Route presence note.** Pause/resume/delete exist only as qBittorrent routes (`POST /api/downloader/pause/{hash}`, `POST /api/downloader/resume/{hash}`, `DELETE /api/downloader/delete/{hash}`); there is **no** `/api/downloader/torbox/queue|pause|resume|delete` route. Torbox queue control is `POST /api/downloads/torbox/{release_id}/retry`, `POST /api/downloads/torbox/{release_id}/remove`, `POST /api/downloads/torbox/clear-failed` and `POST /api/downloads/torbox/poll`. `GET /api/downloader/queue` is qBittorrent-only and unused by the UI.

### 3.3 Torbox submission and polling

**Construction and secrets.** `TorboxDownloader(api_key, base_url, enabled, delete_after_download, timeout, max_file_bytes, max_concurrency, parallel_chunks)` (`backend/app/downloader/torbox.py:186-205`); `enabled` requires both the flag and a non-empty key. `__repr__`/`__str__` mask the key (`:207-215`) and `_request` never echoes the key: `requestdl` passes `token=` in the query but errors are labelled with the path only (`_safe_label`, `:218-219,791-804`). The API base URL is always SSRF-validated (`security.validate_public_url`, `:248-251`). `POST /api/downloader/torbox/test` accepts an unsaved key (mask placeholder ignored) and returns plan/active-slot info from `/user/me` (`backend/app/main.py:3537-3546`; `backend/app/downloader/torbox.py:1406-1439`).

**Error taxonomy.** `_request` maps HTTP 401/403 → `DownloaderAuthError`, 429 → `DownloaderRateLimitError(retry_after)` (Retry-After parsed from seconds or HTTP-date, clamped 0-600 s, `:91-108`), 404 → `DownloaderNotFoundError`, 409/402 or "slot"/"concurrent" in the detail → `DownloaderSlotError`, everything else → `DownloaderError` (`:271-318`). A `{"success": false, ...}` body is re-classified the same way by message keywords (`:308-315`). All messages are secret-free (tests assert the `token=` value never appears, `tests/test_torbox_notready.py:183`).

**Identifier resolution.** `submit()` never guesses: `_resolve_submission` (`backend/app/downloader/torbox.py:491-543`) tries candidates from `download_url/link/guid/magnet/torrent_url/torrent_hash` in that order. A `magnet:` wins; otherwise any candidate that yields an infohash (40-hex or 32-base32) becomes a synthesized magnet; otherwise `/view/<id>` URLs are rewritten to `<base>/download/<id>.torrent`, plus any `*.torrent` URL, and each is fetched with redirect-by-redirect validation, a 10 MiB cap and `_looks_like_torrent()` (bencode must start with `d` and contain `4:info`; HTML/JSON rejected) (`:383-462,454-462`). Failure raises an actionable error listing candidates, derived URLs and up to 3 fetch errors (`:537-543`).

**Submit.** Before creating the torrent, `_check_cached(infohash)` calls `/torrents/checkcached` (`:369-381`); auth/rate-limit errors surface, other errors are only debug-logged (`:554-562`). Magnets are posted as **multipart/form-data** with a `magnet` field because the API rejects query-param magnets (`:564-568`); torrent bytes go through the multipart file path (`:569-576`). The returned handle is `{engine, torrent_id, hash, savepath, wanted_volumes, cached}` and the state is `"ready"` when cached else `"queued"` (`:588-597`). If Torbox answers with a `queued_id` instead of a `torrent_id`, the handle carries `torrent_id: None, queued_id: n` and `state: "queued_slot"` (`:599-610`); the scheduler persists `downloader_id = "queued:<n>"` so the poller can tell the two apart (`backend/app/downloader/base.py:48-68`; `backend/app/scheduler.py:1534-1546`). Nothing returned at all raises `DownloaderError` (`:612-614`).

**Scheduler-level submit.** `submit_torbox_download(...)` (`backend/app/scheduler.py:1453-1585`) is the single write path used by auto-download, manual download, retry and the dedupe sweep:

1. Strict mode and an empty wanted list ⇒ **skip** ("nothing missing (all volumes already owned); no Torbox slot consumed") with `success=True, skipped=True` and an audit `SELECTIVE_DOWNLOAD` (`:1472-1494`).
2. `torbox_max_concurrent` (default 2): active rows counted from `get_active_torbox_downloads()` **excluding** `queued:` handles; at/above the limit the submit fails with a retryable message and audits `TORBOX_FAILED` (`:1495-1510`).
3. `savepath = downloads_dir/<safe_torrent_dirname(title)>` (`:1512-1514`), handle persisted via `update_download_handle` (`:1534-1546`).
4. A queued-slot result audits `TORBOX_SUBMITTED` with `queued_id=…` and wakes the poller (`:1548-1568`); a normal result audits `TORBOX_CACHED`/`TORBOX_SUBMITTED` with `torrent_id`, hash, wanted volumes, source and wakes the poller (`:1570-1585`).

**State normalisation.** `_normalise_state(raw_state, row)` (`backend/app/downloader/torbox.py:617-637`) is the single most important correctness rule in this domain:

```
download_present is True            -> ready
state in FAILED_STATES              -> failed
state in PROCESSING_STATES
   or row.download_finished
   or download_present is False      -> processing
state in READY_STATES               -> ready
state in STALLED_STATES             -> stalled
state in DOWNLOADING_STATES         -> downloading
otherwise                           -> queued
```

`ready` therefore requires Torbox to state the files are *present*; a finished-but-uploading torrent is `processing`, and the poller must never call `requestdl` for it (this was the "404 after 1.3 s" regression; test matrix in `tests/test_torbox_notready.py:40-88`). `status()` also normalises progress (`>1.0` treated as percent), clamps it to 0..1 and exposes `size/download_speed/eta/seeds/files[ {id,name,size} ]/cached/raw_state` (`:751-788`).

**Queued-slot status.** `_queued_status` (`:699-737`) looks up the queued row (`GET /torrents/getqueued`), or — when the queued row disappeared — falls back to `mylist` matched by infohash; if a real `torrent_id` appears it is returned as `promoted_torrent_id` and the normal `status()` payload is produced (`:667-697,708-717`). Otherwise it reports `state: queued_slot, progress 0, queue_position, active_limit`. The scheduler promotes the persisted handle (`set_downloader_id`, keeping the original start time), audits `TORBOX_PROMOTED` once and continues in the same cycle (`backend/app/scheduler.py:2989-3006`; `backend/app/database.py:2843-2856`).

**Poller thread.** `MangaScheduler.start()` starts a daemon `MangaTraxTorboxPoller` thread in addition to the main worker (`backend/app/scheduler.py:5281-5298`). `_torbox_poller_loop` waits on `_torbox_poll_wakeup` for `_torbox_next_poll_interval()` seconds, clears the event and runs `poll_torbox_downloads()` (`:2837-2849`). Interval = `max(5.0, torbox_poll_seconds)` when a fetch is in flight or any active row's `torbox_progress_json.phase` is `ready|fetching|routing`, else `min(30, max(15, floor*3))` (`:2815-2835`). `request_torbox_poll()` sets the wakeup event (`:2851-2856`); it is called after every submit (`:1559,1584`) and by "Check now".

**Poll pass.** `poll_torbox_downloads(async_fetch=None)` (`backend/app/scheduler.py:2886-3111`):

* stamps `torbox_last_poll_at` with `time.time()` (`:2899-2902`);
* returns `{"status": "disabled"}` when the engine is disabled, `{"status": "idle"}` when there are no `DOWNLOADING`+`torbox` rows (`:2903-2913`);
* `async_fetch` defaults to True only when the poller thread is alive (tests/direct calls stay synchronous) (`:2897-2898`);
* for each row: hard timeout check first (age > `torbox_timeout_hours` ⇒ FAILED with "Timed out after …h" or, for queued rows, "No free Torbox active slot for …h"; the release stays REQUESTED for retry) (`:2931-2949`);
* builds the handle (`queued:` id or plain id), missing id ⇒ FAILED (`:2951-2970`);
* `status()` maps `DownloaderRateLimitError` → audit-once + break the whole pass (`rate_limited`), auth/not-found → FAIL, other `DownloaderError` → audit-once and continue (`:2972-2984`);
* `queued_slot` → audit-once message with queue position/active limit, keep waiting (`:3008-3019`);
* `failed` → `_torbox_fail_release`; `stalled` → audit-once, never auto-retried; `processing` → persist `phase=processing` and audit-once `TORBOX_PROCESSING` (`:3021-3051`);
* `ready` → persist `phase=ready, ready_detected_at` once; in async mode hand to the fetch executor (`queued_fetch += 1`), otherwise fetch inline and count completed/failed (`:3055-3072`);
* any unexpected exception is contained per release: partials cleaned, row failed, audit, continue (`:3074-3102`);
* returns `{status, processed, completed, failed, queued_fetch, fetching}` (`:3104-3111`).

`torbox_poll_now()` (`:2858-2867`) additionally wakes the poller and appends `last_checked_at` and `fetching`. Note the concurrency nuance in §8 (Q2).

### 3.4 Torbox strict volume selection and fetch mechanics

**Planner.** `_plan_torbox_selection(release, files, wanted_volumes, total_size=0)` (`backend/app/scheduler.py:2087-2251`) is the decision core and is called from `_fetch_torbox_release` (`:2565-2567`) and `retry_manual_pickup_strict` (`:1889`). Decision table (strict mode default true via `torbox_strict_volume_selection`):

| Condition | Action | Evidence |
|---|---|---|
| strict disabled | delegate to legacy selection (`zip` or `files`) | `:2116-2129,2036-2062` |
| wanted set empty | `skip` ("nothing missing: all volumes already owned") | `:2130-2142` |
| no file list | raise `DownloaderFileError` ("torrent returned no file list; refusing to download the whole torrent") | `:2143-2147` |
| any file name unmappable | raise `DownloaderFileError` listing up to 5 names | `:2148-2167` |
| any wanted volume absent from the torrent | raise `DownloaderFileError` listing wanted + available volumes | `:2168-2178` |
| every file needed, ≤20 files, estimated total ≤90 % of cap | `zip` (whole-torrent requestdl) | `:2186-2216` |
| every file needed but big/many files | `files` (per-file), each size-checked | `:2217-2236` |
| subset | `files` = files whose parsed volumes intersect wanted, each size-checked | `:2237-2251` |

The plan also returns `mapping` (index/id/name/volumes per file), `wanted_volumes`, `selected_volumes`, `skipped_volumes`, `selected_count`, `total_files`, `strict`. `_torbox_max_download_bytes()` clamps `torbox_max_download_gb` to 1-10240 GiB (`:2064-2070`) and `_check_torbox_file_size` raises before any download with size + cap in the message (`:2072-2085`). This is why a too-large torrent yields an actionable failure instead of a half-downloaded staging folder.

**File→volume parsing.** `_parse_torbox_file_volumes(name)` (`:1978-2034`): strips `Part 02`/`Pt. 2` arc markers, runs the shared release parser, adds explicit range patterns (`[Vol 1-3]`, `(v1-v3)`, `v01-v03`) expanding integer ranges ≤200, and only if nothing matched falls back to the **last** bare/bracketed number after excluding years (1900-2099) and resolutions (480/720/1080/2160). Unparseable names return `[]`, which strict selection treats as fatal.

**Fetch pipeline (`_fetch_torbox_release`).** Runs in its own thread (`:2550-2768`):

1. `dest_dir = downloads_dir/<safe_torrent_dirname(title)>`; `total_bytes = info.size` (`:2554-2559`).
2. Resolve `wanted` via `resolve_release_missing_volumes(release)` (`:2563`; see §3.13).
3. Plan. `DownloaderFileError` ⇒ try `_hold_torbox_for_manual_pickup` (MANUAL_PICKUP + `phase=manual_pickup`), else FAILED + `phase=failed` (`:2568-2581`).
4. `skip` ⇒ delete the remote job, `download_status=IDLE`, `phase=done`, audit `SELECTIVE_DOWNLOAD` (`:2582-2594`).
5. Otherwise persist `phase=fetching, ready_detected_at, fetch_started_at, total_bytes` and call `downloader.fetch_files(handle, selection|None, dest_dir, progress_cb)`; `selection is None` means the whole-torrent zip path (`:2619-2639`).
6. On stream failure: `_torbox_fail_contained` (partials cleaned + FAILED) and `phase=failed` (`:2640-2645`).
7. On success: compute downloaded bytes/speed, persist `download_files_json`, `phase=routing, progress_pct=100`, clear error, `download_status=COMPLETED`, notify `notify_acquired`, run a routing pass, persist `phase=done, routed_at`, `mark_release_routed` when every saved path is gone, refresh inventory, log latency (`ready→fetch`, `fetch`, `route`, per-file bytes/seconds/connections), delete the remote torrent when `torbox_delete_after_download` (`:2646-2756`).

Progress is persisted by `_torbox_progress_callback` which aggregates per-label maxima, throttles writes to ≤1 per 2 s / 1 % (final write always) and stores `phase=fetching, downloaded_bytes, total_bytes, speed_bps, progress_pct` (`:2486-2527`).

**Transfer primitives.** `fetch_files(handle, files, dest_dir)` (`backend/app/downloader/torbox.py:1299-1382`):

* `files in (None,"all") or []` ⇒ one `requestdl(zip_link=true)`, streamed to `dest_dir/.torbox-download.zip.part`, extracted by `_safe_extract_zip`, temp zip removed in `finally` (`:1320-1338`). The zip path pre-checks the estimated whole-torrent size (`:1321-1326`).
* A list ⇒ per-file `requestdl(torrent_id, file_id)`; each file's size is pre-checked before any link is requested (`:1344-1353`); then either a sequential loop (`max_concurrency<=1` or one file) or a bounded `ThreadPoolExecutor` (`max_concurrency` workers) with `_download_item_with_retry` per file; any exception triggers `_cleanup_fetch_outputs` (deletes files written by this call plus `.part`/`.torbox-*` remnants in that release folder) and re-raises (`:1354-1382`).
* `_download_item_with_retry` retries only throttle (429/503/"rate limit") and 403/404/410/416 responses, 3 attempts with 1/2/4 s backoff, **re-requesting the requestdl link each attempt** because Torbox download tokens rotate/expire (`:1266-1297`).
* `_download_to` decides between ranged and single-stream: files > 16 MiB (`TORBOX_RANGE_THRESHOLD_BYTES`) with `parallel_chunks>1` try `_download_ranged`; otherwise the stream goes to `<final>.part` and is atomically `os.replace`d (`:1105-1147`). `_download_ranged` probes `Range: bytes=0-0`, computes chunk count `max(2, min(parallel_chunks, total//4MiB or 2))`, downloads each range with 2 attempts into `.partN`, concatenates to `.part`, verifies the exact byte count, renames, and falls back to single-stream on `_RangeUnsupported` (200 response or 403/404/405/416/429) (`:999-1103,943-997`). Failures clean every `.part*`.
* `_stream_to_file` validates every redirect hop against `security.validate_public_url`, caps redirects at 5, enforces `Content-Length`/streamed size against `max_file_bytes`, detects partial downloads (`written < expected`) and empty bodies, and wraps `OSError` into `DownloaderFileError` including path, errno and free disk space (`:806-883,137-160`).
* `_safe_extract_zip` rejects absolute/drive-rooted names, `..`, NUL bytes and symlink entries, guarantees containment via `realpath`, removes partial entries on failure and raises when the archive is empty (`:1149-1195`).

**Failure containment.** `_torbox_fail_release` (`backend/app/scheduler.py:1920-1958`) increments `download_attempts`, stores `"<reason> (attempt N, YYYY-MM-DD HH:MM:SS UTC)"` in `download_error`, sets `download_status='FAILED'`, and — when `torbox_delete_on_failure` (default true) — cancels the remote job so it does not hold an active slot (`:1944-1952`). `_torbox_surface_once` de-duplicates non-fatal audit messages per release+message (`:1960-1970`).

### 3.5 Queue-control endpoints, retry and removal semantics

All four Torbox queue-control routes live next to each other in `main.py`:

* **`POST /api/downloads/torbox/{release_id}/retry`** (`backend/app/main.py:3608-3663`). Guards in order: release exists (404); `release.status == IN_STAGING` ⇒ 409 "Volumes are already staged and waiting for Calibre import"; engine must be `torbox` (409); 5 s per-release rate limit ⇒ 429 (`_TORBOX_RETRY_MIN_SECONDS`, `_TORBOX_RETRY_LAST` dict, `:3604-3605`). Then it reconstructs the old handle (`scheduler._torbox_handle_for_release`), best-effort cancels it (frees the slot), `clear_download_error`, `download_status='IDLE'`, recomputes missing volumes via `resolve_release_missing_volumes`, and calls `submit_torbox_download(source="retry")`. `skipped` ⇒ `{"status":"skipped"}`; success ⇒ `{"status":"success","engine":"torbox","message":…}`; failure ⇒ `set_download_error("… (retry rejected)")`, `download_status='FAILED'`, `{"status":"error"}` (HTTP 200).
* **`POST /api/downloads/torbox/{release_id}/remove`** (`backend/app/main.py:3666-3701`). FAILED rows never re-cancel remotely (that was "Remove appears dead" because Torbox timeouts blocked the response); they only get the local reset. Non-failed rows attempt `cancel(handle)` and report `remote_deleted`/`remote_error`. `clear_downloader_submission(release_id,"torbox")` resets `download_status='IDLE'`, `download_error=''`, `download_attempts=0`, `downloader_id/torrent_hash/downloader_started_at=NULL`, `download_wanted_json='[]'`, `torbox_progress_json='{}'`, `download_files_json='[]'`, `downloaded_at/routed_at=NULL` (`backend/app/database.py:2819-2841`). The status cache is invalidated so the row cannot be re-rendered from a 1 s-old payload (`backend/app/main.py:62-70,3691`).
* **`POST /api/downloads/torbox/clear-failed`** (`backend/app/main.py:3704-3728`). Reads `get_downloader_releases("torbox", limit=500)`, keeps FAILED rows, resets each locally with per-item progress in a persisted background task, returns `{failed_entries, removed, remote_deleted: 0}` — it deliberately never waits on the network. The template additionally drops any DOM rows that look failed and falls back to per-row removes on a 404/405 (older backend) (`backend/app/templates/downloads.html:819-870`).
* **`POST /api/downloads/torbox/poll`** (check now) returns 409 when the engine is not torbox; see DL-044.

On the UI side the queue buttons use a **two-step confirmation** (`_queueConfirm`, arm for 4 s, second click performs) instead of `window.confirm()`, because suppressed blocking dialogs made the queue buttons look dead (`backend/app/templates/downloads.html:765-778`). Retry is only shown when `t.failed or t.can_retry`; Remove is always shown for Torbox (`backend/app/templates/downloads.html:293-301`).

### 3.6 Manual Pickup

**Why it exists.** When strict selection cannot map a torrent's files (unmappable names, wanted volumes absent, no file list) and `torbox_manual_pickup_enabled` (default true) with a free cap slot, the torrent is *held* instead of failed: `_hold_torbox_for_manual_pickup` snapshots the file list (each with parsed `volumes`, `suggested_volume`, `mapping_source`, `fetched=false`), resolves the wanted volumes, computes `expires_at` when `torbox_manual_pickup_expiry_days > 0`, inserts a `torbox_manual_pickups` row, sets `download_error` to a hint pointing at Downloads → Manual Pickup, and sets `download_status='MANUAL_PICKUP'` (`backend/app/scheduler.py:1640-1682`). Because `get_active_torbox_downloads()` only selects `download_status='DOWNLOADING'` (`backend/app/database.py:2874-2882`) and `get_downloader_releases()` only selects `DOWNLOADING`/`FAILED` (`:2858-2872`), a held pickup is **not polled and not listed in the Torbox queue** — that is intended (the poller must not fight the user) but it also means the queue "items" count on the downloads page excludes held pickups; the page shows a separate amber banner with `manual_pickup_count` instead (`backend/app/templates/downloads.html:21-26`).

**Page.** `GET /downloads/manual-pickup` (`backend/app/main.py:1622-1660`) loads active pickups (`pending/fetching/partial`), parses `files_json`/`wanted_json`, attaches the release row, computes `total_bytes`, and passes `pickup_cap`, `pickup_expiry_days`, `pickup_enabled` to `manual_pickup.html`. The template renders one card per pickup with title, release id, series, GiB, wanted volumes, reason, status, expiry, four actions ("Fetch whole torrent", "Retry strict mapping", "Edit series", "Discard"), a wanted-volumes text input with "Save volumes", a per-file table (checkbox, name, GiB, suggested-volume number input, fetched flag) and an "Select all files" toggle (`backend/app/templates/manual_pickup.html:26-84`). Note `pickup_enabled` is passed but never used by the template (only the banner count is conditional on the setting elsewhere).

**APIs.**

| Endpoint | Params | Behavior | Code |
|---|---|---|---|
| `GET /api/torbox/manual-pickups` | `include_finished` 0/1 | list pickups (`files` decoded) + `count`/`active_count` | `backend/app/main.py:1663-1675` |
| `POST /api/torbox/manual-pickups/{id}/map` | `{"mapping":[{index, volume, may be null}]}` (400 non-list, 404 unknown) | sets/clears a file's `suggested_volume` + `volumes`, `mapping_source="manual"`, persists `files_json` | `backend/app/main.py:1678-1687`; `backend/app/scheduler.py:1727-1755` |
| `POST /api/torbox/manual-pickups/{id}/volumes` | `{"volumes":[…]} `(400 empty) | rewrites `releases.volumes_json`, `missing_vols_json`, **and** `download_wanted_json`, plus pickup `wanted_json` (the correction must reach strict retry) | `backend/app/main.py:1690-1701`; `backend/app/scheduler.py:1757-1776`; regression test `tests/test_manual_pickup_volume_correction.py:62-96` |
| `POST /api/torbox/manual-pickups/{id}/fetch` | `{"file_indexes":[…]} `or `{"all":true}` (400 when neither) | background task `torbox:pickup:fetch` with per-file progress | `backend/app/main.py:1704-1734` |
| `POST /api/torbox/manual-pickups/{id}/retry` | — | background task `torbox:pickup:retry`, re-plans against corrected wanted volumes | `backend/app/main.py:1737-1757` |
| `POST /api/torbox/manual-pickups/{id}/discard` | — | remote delete (best effort) + `status='discarded'` + release FAILED | `backend/app/main.py:1760-1768`; `backend/app/scheduler.py:1901-1918` |

**Fetch semantics (`_fetch_manual_pickup_files`).** Validates the pickup state (`discarded|expired|completed` ⇒ ValueError), decodes files, chooses selected indexes when given, else all not-yet-`fetched` files; rejects empty selections. Per-file and total sizes are checked against `torbox_max_download_gb` before downloading (raises `DownloaderFileError`). Marking: `status='fetching'` before work; **whole-torrent** (`fetch_files(None)`) is only used when there is no file list at all (`whole_torrent = not descriptors and not selected_indexes and not files`, `backend/app/scheduler.py:1803`), otherwise files are fetched individually (per-file progress branch when a progress reporter exists, single batch otherwise). After the fetch: files whose name appears in the fetched set are marked `fetched=true`; `status = "completed" if not remaining else "partial"`; the release gets `download_files_json`, `clear_download_error`, `download_status='COMPLETED'`, and — only when the pickup completed — a routing pass; audit `TORBOX_MANUAL_PICKUP_FETCHED` (`backend/app/scheduler.py:1845-1872`). Returns `{pickup_id, fetched, failed, remaining, pickup_status}`; the UI surfaces `failed` inline as "mapping kept — adjust and retry" (`backend/app/templates/manual_pickup.html:136-156`).

**Strict retry.** `retry_manual_pickup_strict` prefers the pickup's `wanted_json` (the user's latest correction), falling back to `resolve_release_missing_volumes`; it re-plans, maps selected file names back to list indexes and delegates to the fetch above; a `skip` plan raises "Nothing to fetch" (`backend/app/scheduler.py:1874-1899`).

### 3.7 Downloads status API and auto-download diagnostics

**`GET /api/downloads/status`** (`backend/app/main.py:2104-2123`) always returns HTTP 200 with `Cache-Control: no-store`, built by `_build_downloader_status(timeout)` in a worker thread. Timeout is clamped: default 1.5 s, max 5 s (`:473-474,543-546`). Payload shape:

```
{status: "success"|"disabled"|"degraded", degraded: bool, engine: "qbit"|"torbox",
 generated_at, timeout_seconds, live_state_available, cache_age_seconds?, stale?, stale_age_seconds?,
 downloader: {enabled, connected, version, message, last_checked_at?, last_checked_seconds_ago?, plan?, active_slots?},
 torrents: [...], active: [...], count, totals: {download_speed, upload_speed, progress, eta_seconds},
 inventory: {entries, scanned_at, exists, ...}, last_scanned_at}
```

* **Torbox branch** (`_torbox_status_cached` → `_build_torbox_downloader_status`, `:850-1019`): 1 s TTL cache, single-flight `building` flag, last-good stale serving up to 30 s, and DB-derived rows when nothing else is available (`:52-70,804-913`). When connected it iterates `db.get_downloader_releases("torbox", limit=20)` (DOWNLOADING then FAILED) and calls live `status()` only for `DOWNLOADING` rows, decoding `queued:` handles (`:970-990`). Endpoint/DB disagreement is resolved in favour of the DB for failures: a row whose `download_status == 'FAILED'` is always reported `failed` even if Torbox says Ready (`:653-659`). Local fetch phases override live state while `phase in (fetching, routing, processing)` and local byte counters win while fetching (`:659-696`). Each row carries `selection` ("missing volumes 1, 2" / "nothing missing" / "legacy whole-torrent fallback allowed"), `wanted_volumes`, `strict_volume_selection`, `can_retry`, `attempts`, `error`, `strict_hint` (mapped to remediation text for suspicious chapter-numbers-as-volumes, absent volumes, unmappable names, missing file list — `:727-749`), `last_checked_*`, `download_files`, `raw_state`, `cached`, `files`, `queued`, `queue_position`, `active_limit`, `release_id`, plus qBittorrent-compatible aliases (`size/progress/eta/dlspeed/…`) so one template renders both engines (`:750-801`).
* **qBittorrent branch** (`:574-632`): disabled ⇒ `status="disabled"`; unreachable ⇒ `downloader.connected=false` **and `payload["torrents"]` is filled with `_torbox_db_rows()`** (Torbox release rows) while `live_state_available=false`; connected ⇒ `get_torrents(category=qbit_category)` normalised by `_torrent_status_view` (`:490-532`, active-state set at `:475-478`, ETA 8640000 treated as unknown) and aggregate totals/ETA computed over active rows only (`:613-631,1022-1034`).
* **Speed history/sparkline** — Torbox rows get `speed_history` (last 60 samples, capped store of 120) and `speed_avg_bps` over the last 10 samples, only appended for live builds (`:835-847`).
* **Inventory attach** — the endpoint loads the persisted inventory (`downloads_inventory.load_inventory`) rather than scanning, and adds `last_scanned_at` (`:2115-2122`).

**Client polling.** The page's `refreshDownloadsNow()` fetches `/api/downloads/status`, re-renders the Torbox queue body and the inventory table, updates the "Last checked …s ago" header, and re-schedules itself after **1 s while any row is active/`ready`/`fetching`/`routing`, else 5 s** (`backend/app/templates/downloads.html:1143-1159`). It is only auto-started when the active engine is Torbox (`{% if engine == 'torbox' %} refreshDownloadsNow(); {% endif %}`, `:1209-1211`) — for qBittorrent the table is server-rendered and refreshed only by full page reload / manual actions. `GET /api/downloader/queue` is unused by the template.

**Auto-download diagnostics.** `GET /api/downloads/auto-download/diagnostics` (`backend/app/main.py:2213-2217`) returns `scheduler.get_auto_download_diagnostics()` (`backend/app/scheduler.py:1128-1246`): global/feed/engine/monitored-series readiness, `engine_reason`, last poll timestamp/result/errors, a 5-item `checklist`, `recent_decisions` parsed from the last 100 activity rows for `AUTO_DOWNLOAD_SUBMITTED|AUTO_DOWNLOAD_SKIPPED|IN_FLIGHT_DEDUPE`, `limitations`, `prerequisites`, and `staging_dedupe_volumes` (the current staging reservation map). The card renders the summary line, "Why nothing fires" checklist and "Recent decisions" and refreshes on DOMContentLoaded (`backend/app/templates/downloads.html:6-19,1555-1590`).

### 3.8 Routing: raw downloads → tagged staging

`process_downloads_to_staging(downloads_dir, staging_dir, series_templates, db, actor)` (`backend/app/tagger/pipeline.py:493-793`) is idempotent and per-file failure-isolated.

1. **Flatten first** — `flatten_staging_dir` runs before and after routing (cheap no-op when flat) and its counters merge into the result (`:528-531,728-729`).
2. Walk downloads recursively (sorted for determinism). Skip temp suffixes `.part/.tmp/.mangatrax-part/.mangatrax-rewrite-tmp` (`:41,553-554`).
3. Non-`.cbz` files: if `convert_archives_to_cbz` (default **false**) and the extension is convertible (`.cbr/.rar/.7z/.cb7/.zip`), convert (`archive_conversion.convert_archive_to_cbz`), audit `ARCHIVE_CONVERTED_TO_CBZ`, and continue with the produced `.cbz`; on conversion error the file is counted failed and a failure row is recorded. Otherwise the file is silently skipped (it will show up under Failed documents via DL-064) (`:556-579`).
4. Resolve the series template; if unresolved, bucket the file under `series_awaiting` (`series`, `count`, `volumes`), count `awaiting_calibre`, and leave the file untouched (`:588-601`).
5. Naming assessment + previous-volume inheritance (`volume_naming.assess_volume_naming` / `inherit_for_file`); inheritance applies only when no intervention is needed; when the previous volume's template has no replaceable marker the file is forced to "needs intervention". Audits `VOLUME_NAME_INHERITED` / `VOLUME_NAME_INTERVENTION_REQUIRED` (`:603-646`).
6. Build metadata: `Title` from `format_comicinfo_title` (Calibre style `Series vNN`), `Series`, `Number`/`Volume` from `format_volume`, `Writer`/`Penciller` from `format_authors(template.authors)`, `Publisher`, `Genre` from `format_tags` (`:169-187`; `backend/app/tagger/naming.py`).
7. Validate readability **before** mutating: `read_comicinfo_xml` on the source; `UnreadableArchiveError` ⇒ failed + `record_failure` and the file stays in downloads untouched. If there is no ComicInfo.xml, inject one (`inject_comicinfo`, append mode) and verify with `has_comicinfo` (`:647-666`). Existing ComicInfo.xml is left as-is by this path (that is the domain of DL-074/DL-073).
8. Staging destination is always the **flat root**, `_safe_staging_basename(filename)` (basename only, control chars/`<>:"/\|?*` replaced, trailing dots stripped, fallback `file.cbz`). Collision policy `_staging_collision_path`: not present ⇒ `new`; present with the **same size** ⇒ `skip` (source left in downloads!); present with a different size ⇒ `<stem> (2)<ext>` (`:668-689,393-424`).
9. Containment guard: the resolved destination must be inside `staging_root` else the file is failed (`:670-675`).
10. Move: `_move_with_preference` — same `st_dev` ⇒ `os.replace`; cross-device ⇒ copy to `<dest>.mangatrax-part`, atomic rename, delete source, and log a warning (the deployment skipped the same-drive recommendation) (`:238-270`). Audit `TAGGED_AND_MOVED_TO_STAGING`.
11. Post-pass: `series_awaiting` summary stored in `downloads_awaiting_signature` and audited `AWAITING_CALIBRE_SERIES` **only when the signature changes** (no log spam); the failed-file signature similarly gates `DOWNLOADS_ROUTING_FAILED`; then `prune_empty_dirs(downloads_dir)` runs (`:731-793`, `:294-390`).

The pass returns `{scanned, tagged_and_moved, awaiting_calibre, failed, skipped_existing, conflicts_renamed, series_awaiting, processed[], converted, flattened, flatten_skipped_existing, flatten_conflicts_renamed, flatten_dirs_removed, removed, pruned_count, pruned_dirs, skipped_busy}` (`:506-520,780-793`); `processed[]` entries carry source/destination/filename/series/volume/volumes/move. `MangaScheduler.process_downloads_to_staging` wraps it with `get_downloads_dir(create=False)`, `staging_dir`, `get_series_templates_map()`, persists `downloads_routing_summary`, calls `notify_processed_batches`, and swallows every exception into an `{error: …}` result so a routing failure can never break the worker or an HTTP call (`backend/app/scheduler.py:1421-1451`).

**Triggers of the routing pass:** the "Process Downloads" button (`POST /api/staging/tag`, `backend/app/main.py:2076-2099` — also runs the legacy `tag_staging_files` and reports `tagged_count = routed + legacy`), the 45 s `periodic_downloads_inventory_and_route` timer (`backend/app/scheduler.py:5252-5259,2281-2294`), the 5-min auto-tag timer when `auto_tag=true` (`:5211-5219`), immediately after every Torbox fetch completion (`:2679`), after a manual download (`backend/app/main.py:3776,3815`), after a manual awaiting-series match (`backend/app/main.py:2414-2424`), after failed-document conversion (`backend/app/main.py:3109-3113`), and after a settings save that changes `downloads_dir`/`staging_dir` (`backend/app/main.py:4275-4278`).

**Flatten (`flatten_staging_dir`).** Walks staging (skipping symlinked dirs), moves every non-temp file to the root using the same collision helper; `skip` (same name+size) **deletes the nested copy** (never overwrites the root file); temp `*.part`/`*.mangatrax-part` are left in place; emptied dirs are pruned with `prune_empty_dirs(..., busy_names=set(), label="staging")`; returns `{flattened, skipped_existing, conflicts_renamed, dirs_removed}` (`backend/app/tagger/pipeline.py:431-490`). Endpoint: `POST /api/downloads/flatten-staging` wrapped in a persisted task `downloads:flatten` (`backend/app/main.py:2162-2170`).

**Prune-empty (`prune_empty_dirs`).** Bottom-up `os.walk(topdown=False, followlinks=False)`; skips the root itself, symlinked dirs, anything not contained under the root, directories in the busy set, non-empty dirs, and (only when the busy lookup is unusable, e.g. no DB) dirs touched within `min_age_seconds` (default 60 s). Uses `os.rmdir` only — never `rmtree`. Emits one `DOWNLOADS_PRUNED` audit per pass with up to 20 paths (`backend/app/tagger/pipeline.py:294-390`). `prune_empty_folders(...)` composes downloads (+ optional staging) counters (`:796-814`); endpoint `POST /api/downloads/prune-empty` with `{"include_staging": bool}` defaults to downloads-only in the UI (`backend/app/main.py:2146-2159`; `backend/app/templates/downloads.html:1177-1189`). Busy detection uses `SELECT title FROM releases WHERE download_status='DOWNLOADING'` mapped through `_safe_target_dirname` (same sanitiser as the Torbox folder name) (`backend/app/tagger/pipeline.py:273-291`).

**Awaiting-Calibre scan / manual match.** `scan_awaiting_series(downloads_dir, series_templates)` is a read-only recursive walk of `.cbz` files whose parsed series has no template; it groups by series with `count`, `volumes` and per-file `relative/name/size_mb` (`backend/app/tagger/pipeline.py:190-235`). The page renders it with per-series "Match series" and per-file Delete buttons (`backend/app/templates/downloads.html:117-160`). `POST /api/downloads/awaiting-series/match` (`backend/app/main.py:2448-2458`) delegates to `_match_awaiting_series` (`:2328-2445`), which:

* requires `calibre_series_id` **or** `new_series_title`;
* for a Calibre id: verifies it against the live Calibre snapshot (falling back to synced `series_tracking` rows) and calls `db.sync_calibre_series` to ensure tracking (`:2342-2376`);
* registers `add_series_alias_override(source, target, calibre_id)` **and** an override for every parse-shape candidate from `series_name_candidates(source)` so future files with a different imperfect primary also auto-match (`:2385-2393`);
* re-keys `releases.series_name` for every release whose normalized series equals the source or its cleaned form (`:2394-2412`);
* invalidates caches, runs a routing pass immediately, notifies processed batches, audits `SERIES_MANUALLY_MATCHED`, and returns `{target, source_series, override, retagged_files, moved_files, release_ids}` (`:2413-2445`).

Because `get_series_templates_map()` merges alias overrides into the template map (`backend/app/database.py:2313-2339`), the same mapping benefits routing, await-scan and inventory with no extra code.

### 3.9 Raw downloads inventory, cleanup and per-row delete

**Inventory scan.** `scan_downloads(downloads_dir, series_templates, releases, max_entries=200)` (`backend/app/downloads_inventory.py:159-226`) uses one `os.scandir` on the root; directory entries are summarised from their **direct** files only (depth ≤ 2 in practice; `MAX_DEPTH=2` is defined but never used, `:21`) and file entries are summarised alone. Per entry it stores `name`, `relative`, `is_dir`, `size_bytes`, `file_count`, `cbz_count`, `has_partials`, `oldest_mtime`, `newest_mtime`, `age_seconds`, `series_name`, `volumes`, `state`, `release`. Empty/junk-only directories (no CBZ, no partials) are dropped (`:206-209`). Partial suffixes are `.part`, `.mangatrax-part`, `.torbox-download.zip.part` (`:22`). Series/volumes come from the first parseable `.cbz` name (`_parse_names`, `:38-57`). Release matching first compares the normalized absolute path against every release's `download_files_json` (either direction prefix match), then falls back to a normalized title substring match (`:98-128`) — the fallback is a heuristic (see §8 Q7).

State (`_state_for`, `:145-156`): `partial` if any partial file exists; `unmatched` if no CBZ or no parsed series; `ready` if `resolve_series_template` matches (same resolver as routing); otherwise `awaiting_calibre`.

**Persistence.** `refresh_inventory(db, downloads_dir, max_entries)` loads `get_series_templates_map()` and `get_tracked_downloads(limit=500)`, scans, and stores the whole JSON in `app_settings.downloads_inventory_json` plus `downloads_scanned_at`; persistence failure is only debug-logged ("diagnostics must never break the worker") (`:229-248`). `load_inventory` returns a safe empty structure when never scanned (`:251-263`). The downloads page scans synchronously in a worker thread on every render and falls back to the persisted copy on failure (`backend/app/main.py:1776-1785`); the status API only reads the persisted copy; a 45 s scheduler pass refreshes it (`backend/app/scheduler.py:2273-2294`); Rescan (`POST /api/downloads/scan`, `backend/app/main.py:2173-2210`) refreshes and additionally runs `downloads_reconcile`, returning `{inventory, last_scanned_at, entries, reconciled, task_id, task}`. The `truncated` flag and `entry_count` are persisted but **never rendered** (and truncation itself is silent in the UI) — see §8.

**Cleanup audit.** `POST /api/downloads/inventory/audit` (`backend/app/main.py:2957-2964`) returns the read-only preview from `audit_inventory_cleanup` (`backend/app/downloads_inventory_cleanup.py:127-211`):

* duplicates = entries whose CBZ-basename+size signature is identical to another entry; the kept copy prefers a directory over a file and the lexicographically first relative path; every other entry is a `duplicate` candidate with `alternate` = kept relative (`:134-161`);
* `in_staging` = every CBZ in the entry exists in staging by basename with a size within `max(1024, size/100)` bytes (`_all_in_staging`, `:88-102`);
* `in_calibre` = every CBZ parses to series+volumes, matches a Calibre series, and its volume set is a subset of the Calibre **owned volumes** (`_calibre_match_for_files` → `staging_duplicates._calibre_volumes`, `:105-124`);
* response: `entries`, `candidates`, `duplicates{count,bytes,candidates}`, `already_present{...}`, `totals` (`:191-211`).

**Clear.** `POST /api/downloads/inventory/clear` requires `confirm: true` and `mode in (duplicates, already_present, all)`; optional `relatives` restricts the selection. It runs as background task `downloads:inventory-clear` and internally **re-audits** before deleting (`backend/app/main.py:2967-2994`; `clear_inventory` `backend/app/downloads_inventory_cleanup.py:273-325`). Every path is revalidated: `_resolve_entry` rejects absent/`..`/absolute/drive-rooted/symlinked/escaping paths (`:214-225`), `_delete_entry` deletes files without following symlinks and `os.rmdir`s emptied directories, `_prune_parents` removes now-empty parents up to the root (`:228-270`). Each deletion audits `DOWNLOAD_INVENTORY_DUPLICATE_CLEARED` or `DOWNLOAD_RAW_COPY_CLEARED` with files/bytes/alternate; the result returns deleted/files_deleted/bytes_freed/skipped/failed/remaining.

**Per-row delete.** `POST /api/downloads/inventory/delete` (`backend/app/main.py:2997-3017`) requires `confirm: true` + `relative`, calls `delete_inventory_entry`, maps `ValueError` to 404 when the row is not in the current inventory / no longer exists, 400 otherwise, then refreshes the inventory and returns counts (`backend/app/downloads_inventory_cleanup.py:328-349`). The UI arms the button for one click and confirms on the second (two-step pattern, `backend/app/templates/downloads.html:1940-1975`).

### 3.10 Failed documents, detection and archive→CBZ conversion

**Recording.** `download_failures` has one row per downloads-relative path (`file_relative TEXT NOT NULL UNIQUE`, `backend/app/database.py:960-979`). `record_download_failure` upserts: on conflict it rewrites `reason`, `detected_type`, `release_id`, `series_name`, `volumes_json`, sets `status='failed'` and increments `attempts` (`backend/app/database.py:1410-1429`). `record_failure(db, downloads_dir, path, reason, detected_type=None)` derives the relative path, parses the basename for series/volumes, tries to match a release with `staging_duplicates._release_for` (normalized series + volume intersection, newest release wins, `backend/app/staging_duplicates.py:66-83`) and stores the matched id (`backend/app/download_failures.py:59-73`). `record_pipeline_failure`/`clear_pipeline_success` swallow every exception ("failure recording must never break routing", `:75-86`).

**Where the routing pipeline records failures** (`backend/app/tagger/pipeline.py`): symlinked download file (`:582-587`), unreadable archive (`:653-658`), tagging produced no ComicInfo (`:660-666`), conversion failure (`:573-577`), and any unexpected exception with a `storage_health.format_hint` appended to the log (`:714-726`). A successful tag/move clears the row (`clear_pipeline_success`, `:652,703`); a later routing success also clears it.

**Detection.** `detect_document_type` reads the first 64 bytes and matches magic numbers (`PK\x03\x04`/`PK\x05\x06` ⇒ zip, `Rar!` ⇒ rar, `7z\xbc\xaf\x27\x1c` ⇒ 7z, `%PDF-` ⇒ pdf, `BOOKMOBI` ⇒ mobi), then HTML hints (`<!doctype`, `<html`, `<?xml`, `<head`, `<body`) ⇒ html, else unknown (`backend/app/download_failures.py:23-42`). `is_valid_cbz` is a cheap `.cbz` + `zipfile.is_zipfile` central-directory check that never reads archive contents (`:45-52`).

**Scan.** `scan_failures(db, downloads_dir)` (`backend/app/download_failures.py:89-157`) walks every raw file, skipping hidden names, symlinked dirs/files and temp suffixes `.part/.tmp/.mangatrax-part/.mangatrax-rewrite-tmp/.crdownload/.download`, then classifies by extension:

| Extension class | Behaviour | Reason text |
|---|---|---|
| `.cbz` (`NATIVE_EXTENSIONS`) | valid ⇒ clear any failure row (`cleared`); invalid ⇒ record | `File is not a zip file` / `… (detected CBR)` |
| `.cbr/.rar/.7z/.cb7/.zip` (`CONVERTIBLE_EXTENSIONS`) | always recorded with `detected_type` | `Unsupported archive format (CBR) — convert to CBZ; it cannot be tagged or routed until converted` |
| `.epub/.pdf/.mobi/.azw3/.txt/.doc/.docx` | recorded | `Ebook/document — not a CBZ comic archive; cannot be tagged or routed` |
| anything else | recorded | `Unrecognised file — cannot route to staging` |

Returns `{checked, failed, cleared}`; the endpoint wraps it in a persisted task `downloads:failures-scan` and returns the refreshed list (`backend/app/main.py:3027-3044`).

**Serialisation.** `serialize_failure` (`backend/app/download_failures.py:160-224`) returns the row id, `file_relative`, `name`, current on-disk `size`, `reason`, `detected_type`, parsed `series_name`/`volumes`, `attempts`, `status`, matched `release_id/release_title/release_status/release_volumes`, derived `can_retry`, `retry_reason`, `created_at`, `extension`, `can_convert`. Retryability requires a matched release **and** a `download_url`/`link` **and** release status not in `IN_CALIBRE/IGNORED/BLACKLISTED`; the reason strings are "matched release is in Calibre/ignored", "matched release has no download URL", "no matching release". If the stored `release_id` is stale/missing it re-matches and persists the new id (`:178-191`). `list_failures` serialises only rows with `status='failed'` (ordered by `created_at, id`, `backend/app/database.py:1451-1457`).

**Retry.** `POST /api/downloads/failures/{id}/retry` (`backend/app/main.py:3047-3075`): 404 unless the row exists and is `failed`; 409 when no matched release ("No matching release for this file; correct the series/volume mapping first") or when `can_retry` is false; otherwise it calls the normal release-download route (`api_release_download`), converts a ≥400 response into 409 with the downloader message, then marks the failure `retried` with `attempts+1`, audits `DOWNLOAD_FAILURE_RETRIED` and returns `{failure_id, release_id, download, state}`. Note that a **successful-but-skipped** release download (nothing missing) still marks the failure `retried` (§8 Q9).

**Convert.** `POST /api/downloads/failures/{id}/convert` (`backend/app/main.py:3078-3123`) requires `can_convert`, resolves the file through `_failure_file_path` (downloads-relative validation, symlink 403, containment, 404 when gone, `:2940-2954`), then runs a persisted `downloads:archive-convert` task: `convert_archive_to_cbz`, on success clear the failure row, audit `ARCHIVE_CONVERTED_TO_CBZ`, and immediately run a routing pass so the new CBZ is tagged/moved; on failure returns `{status:"error", reason}` with the task failed. Tests: `tests/test_stuck_files_archive_conversion.py:123-158`.

**Delete.** `_delete_failure_rows` (`backend/app/main.py:3126-3158`) deletes files whose path validates; a path that no longer exists is counted `skipped` and the row is marked `deleted`; success marks the row `deleted`, audits `DOWNLOAD_FAILURE_DELETED` and prunes empty parents. Single delete pre-validates the path (so traversal/symlink errors surface as 4xx, `:3169`), then reconciles releases and reports `remaining`. Delete-all requires `confirm: true` (`:3182-3197`). Rows with status `retried`/`deleted` are **not** listed and never pruned (§8 Q9).

**Converter contract** (`backend/app/archive_conversion.py`). Supported inputs: `.cbr/.rar` (rarfile + external extractor), `.7z/.cb7` (py7zr), `.zip` (zipfile); `NATIVE_EXTENSIONS={".cbz"}` (`:20-24`). Caps: `MAX_ENTRIES=10000`, `MAX_TOTAL_BYTES=2 GiB`, `MAX_ENTRY_BYTES=512 MiB` (`:22-24`). Every entry is validated: empty/absolute/drive-rooted names rejected, `.`/`..` rejected, symlink entries (mode `0o120000`) rejected, per-entry size cap; zip/rar readers pull bytes into memory, 7z uses `readall()` after a `reset()` (`:31-109`). `_write_entries` writes a DEFLATED zip enforcing the entry-count and total-size caps, then re-opens and runs `testzip()` (`:112-134`). `convert_archive_to_cbz` refuses symlinked sources and same-path targets and an already-existing target, writes `tempfile.mkstemp(prefix=".mangatrax-convert-", suffix=".cbz.tmp", dir=<destination dir>)`, atomically `os.replace`s it, deletes the source **only after success**, removes the temp on any failure, and returns `{status:"success", source, target, entries, bytes}` or `{status:"error", reason}` (`:137-185`). Runtime dependencies are declared in `backend/requirements.txt` (`py7zr`, `rarfile`) and installed in the image (`Dockerfile:21-22` `p7zip-full`, `unar`); a missing module yields a clear `RuntimeError` reason instead of a crash (`:60-64,92-96`).

### 3.11 Staging groups, duplicates, ComicInfo audit/repair, manual tag, per-file delete

**Groups (`build_staging_groups`).** Scans every staged `.cbz` (`cbz_tagger.scan_staging_cbz_files`: recursive, sorted, symlinks listed but resolved later, `backend/app/tagger/cbz_tagger.py:175-208`), reads ComicInfo `Series` when present else the parsed filename else `"Unknown Series"`, groups by normalized series, and per file computes `tagged`, `volumes`, `assess_volume_naming` (`needs_intervention`, `reason`, `suggested_volume`) plus previous-volume `suggested_filename`/`suggested_title` and `needs_intervention` forced when the previous template has no replaceable marker (`backend/app/staging_groups.py:16-86`). Group totals: `count`, `tagged`, `untagged`, `volumes`, `size_bytes`, `needs_intervention`; files sorted by volumes then relative; groups sorted by series. `POST /api/downloads/staging-series/match` (`backend/app/main.py:3206-3247`) validates that the source series exists, resolves the target (explicit `target_series`/`new_series_title` or a `calibre_series_id` looked up in the live snapshot then synced rows), and runs `retag_staging_group` in a background task `downloads:staging-series-match`: for each file it re-resolves the entry by relative path, re-validates symlink/containment, reads the existing XML, builds a document with only `Series` replaced and atomically rewrites it; failures are collected per file. Afterwards it registers a series alias override, links/merges the tracked series when one exists (`db.link_tracked_series`) or bulk-renames `releases.series_name`, invalidates the ownership/grouped caches and returns `{status, source, target, matched, tagged, failed, skipped, errors, files, linked}` (`backend/app/staging_groups.py:89-183`).

**Staging duplicates.** `audit_staging_duplicates(db, staging_dir, calibre_series)` (`backend/app/staging_duplicates.py:86-139`) walks staging (skipping hidden dirs and temp suffixes), keeps real non-empty files inside the root, parses series+volumes from the filename, matches a Calibre series, and keeps the file **only when the parsed volume set is a subset of the Calibre series' explicit owned volumes** (`_calibre_volumes`, `:38-63` — deliberately never inferred from `book_count`, because "N books" does not mean volumes 1..N). It attaches the linked release (`_release_for`) and returns `{duplicates, total_bytes, files, groups}`. `GET /api/downloads/staging-duplicates` adds `staging_dir` to the payload (`backend/app/main.py:3334-3341`).

`purge_staging_duplicates` (`:142-269`) requires `confirm` for `all`, re-audits, accepts either exact relative paths or a *unique* bare filename (a duplicated basename is rejected/ignored to avoid purging the wrong nested copy), refuses symlinks/escapes, deletes files, audits `STAGING_DUPLICATE_PURGED`, then `_update_release_bookkeeping`: for every affected release, if any recorded staged file still exists or any verified duplicate remains it stops; otherwise it sets the release `IN_CALIBRE` (precedence-aware), clears `routed_at` and strips purged paths from `download_files_json` (`:225-269`). Finally it invalidates the staged-ownership cache. Endpoint: `POST /api/downloads/staging-duplicates/purge` with `{confirm:true, filenames:[…]}` or `{confirm:true, all:true}` (`backend/app/main.py:3344-3367`).

**ComicInfo audit.** `build_series_reference` (`backend/app/tagger/series_audit.py:95-184`) builds authoritative `names` (series_tracking rows, then live Calibre names overriding spelling), per-name `templates` (authors/publisher/tags), and `aliases` (from `get_authoritative_alias_sets()`; for Calibre-only series from the authoritative metadata cache `canonical_title`/`romaji_title`). `classify_series_value` (`:187-272`) yields:

| Situation | status | safe_to_repair |
|---|---|---|
| no `<Series>` | `missing_series` | no |
| value with no comparable characters | `unknown_series` | no |
| exact canonical spelling | `ok` | no |
| normalized-equal (case/punctuation only) | `repairable` (`series_match="normalized"`) | yes |
| authoritative AniList alias of exactly one tracked series | `repairable` (`series_match="anilist_alias"`) | yes |
| alias claimed by several series | `ambiguous` | no |
| no match at all | `unknown_series` | no |

`audit_entry` (`:295-435`) reads the archive (unreadable ⇒ `unreadable`; malformed XML ⇒ `invalid_comicinfo`; no XML ⇒ `untagged`), records current Title/Writer/Penciller/Publisher/Genre/Number/Volume, then proposes changes: `Series` for repairable normalized/alias cases; `Title` from the shared Calibre formatter (`Series vNN`) whenever it differs; `Writer`/`Penciller` only when a non-empty current value disagrees with `format_authors`; `Publisher` only when a non-empty current value disagrees with the exact tracked string; `Genre` when it disagrees with `format_tags`; `Number`/`Volume` only when a current value exists and re-formats differently; an `ok` record that still has changes becomes `repairable` (`:428-435`). `audit_staging_cbz_files` returns per-file records plus a summary (`scanned`, one counter per status, `files_with_changes`, `needs_attention = repairable+ambiguous+unknown_series`, `tracked_series`, `alias_claims`) and reports progress (`:486-531`). It is strictly read-only (archives opened `"r"` only). Endpoint `GET /api/tagger/audit` runs it as task `audit:comicinfo` (`backend/app/main.py:3370-3384`).

**Repair.** `POST /api/tagger/repair-series` (`backend/app/main.py:3474-3493`) validates `SeriesRepairRequest` (`files` 1-200, `repair_writer`, `repair_publisher`, `backend/app/schemas.py:1052-1083`). `_repair_staging_cbz_series` (`:3387-3471`) resolves **all** filenames first (any invalid name rejects the whole request before a single write), builds the reference once, re-audits, calls `plan_repair` (Series only when suggested; Writer/Publisher gated by the flags; Penciller/Genre/Number/Volume/Title always when proposed, `backend/app/tagger/series_audit.py:438-483`), and writes via `build_comicinfo_xml` + `rewrite_comicinfo`. Outcomes: `repaired` (audited `CBZ_SERIES_REPAIRED`), `unchanged` (status ok and no changes — idempotent), `skipped` (not repairable / only disabled fields), `failed` (rewrite exception or `False`). Response includes `requested/repaired/unchanged/skipped/failed`, `field_counts` and per-file `results`; overall `status` is `partial` when any write failed.

**Preview and manual tag.** `GET /api/tagger/preview?filename=…` resolves the entry strictly through the staging scan (`_resolve_staging_cbz`, `backend/app/main.py:402-463`: rejects `..`, absolute/drive paths, control length, unmatched names 404, ambiguous basenames 409, symlinks 403, containment, missing file 404) and returns `{file, name, size_mb, has_comicinfo, comicinfo, parsed{series_name,volume_start,volume_end,volumes,is_bundle}, warning}`; unreadable archives are 422 and malformed XML returns `status="invalid_comicinfo"` with a warning instead of failing (`:2677-2710`). `POST /api/tagger/manual-tag` (`:2837-2850`, model at `backend/app/schemas.py:1016-1049`) requires `series` and writes only the supplied fields: `Series`, `Number`+`Volume` (numeric string normalised), `Title`, `Writer`+`Penciller`, `Publisher`, `Genre` (`_manual_tag_updates`, `:2724-2739`). When creating a fresh document it also defaults `Title` to `"<series> Vol. <volume>"` (or the series name) and `Manga=YesAndRightToLeft` (`:2752-2760`). A malformed existing ComicInfo.xml is replaced by a fresh document and the response reports `replaced_invalid_comicinfo: true` (`:2766-2770`). An optional `new_filename` triggers a rename after validation (plain filename, `.cbz`, no separators, containment, 409 when the target exists) using `os.replace`, audited `STAGING_FILE_RENAMED` (`:2775-2800`). Both staged-ownership and grouped caches are invalidated and `MANUAL_TAG_CBZ` is audited; the response includes the re-read ComicInfo (`:2801-2823`).

**Per-file delete.** `POST /api/downloads/files/delete` (`backend/app/main.py:3250-3331`) with `{area: "staging"|"downloads", relative|filename}`. Staging resolves through `_resolve_staging_cbz`; downloads resolves through `_resolve_awaiting_download_file`, which only accepts files the current awaiting scan returns (exact relative path, else a unique basename; 409 on ambiguity) and validates symlink/containment/existence (`:2886-2923`). Immediately before removal the path is re-validated (symlink 403, containment, exists 404), then `os.remove`, `_prune_empty_parents`, ownership-cache invalidation for staging, audit `DOWNLOAD_FILE_DELETED`, a `downloads_reconcile` pass, and — for downloads — an inventory refresh plus a fresh awaiting-file count. Returns `{area, deleted, relative, remaining_files, series, series_remaining}`.

### 3.12 ComicInfo primitives and the naming contract

**Read.** `_read_raw_comicinfo` finds the first case-insensitive `ComicInfo.xml` entry at the archive root and returns its bytes; a missing/corrupt archive raises `UnreadableArchiveError`; `read_comicinfo` parses via ElementTree, strips namespaces and returns `{element: text}` (comments/PIs skipped); malformed XML raises `MalformedComicInfoError` (`backend/app/tagger/cbz_tagger.py:49-95`). `has_comicinfo` caches `(path, mtime)`–keyed results (`:242-258`).

**Build.** `build_comicinfo_xml(existing_xml, updates)` preserves every unrelated element and its order, replaces the first matching element's text, removes duplicate same-name elements, appends missing ones, and prefixes an XML declaration; an unparseable existing document raises rather than silently discarding fields (`backend/app/tagger/cbz_tagger.py:98-128`).

**Rewrite.** `rewrite_comicinfo(cbz_path, xml_content)` rebuilds the zip next to the source (`<cbz>.mangatrax-rewrite-tmp`), copying every non-ComicInfo entry with its original `ZipInfo` (name/timestamp/CRC/compress_type preserved ⇒ image payloads are never re-encoded, STORED stays stored), writes exactly one ComicInfo.xml, `os.replace`s it into place, clears the ComicInfo cache and returns `True`; any failure deletes the temp file, leaves the original untouched and returns `False` (`:131-172`). This idempotent rewrite is what makes the repair/retag/manual-tag paths safe.

**Legacy generation/injection.** `generate_comicinfo_xml` renders the fixed Calibre/Komga/Kavita-compatible template (`Title/Series/Number/Volume/Writer/Penciller/Publisher/Genre/Manga=YesAndRightToLeft`, `:211-240`) and `inject_comicinfo(..., overwrite=False)` appends it **only when absent** (`zipfile` append mode), which means repeated calls cannot duplicate the entry as long as `has_comicinfo` is respected (`:260-284`). `auto_tag_staging_cbz_files` (the legacy staging auto-tagger) walks staging, resolves each file's series through the template map and injects metadata, returning the tagged file list; it never overwrites existing tags (`:286-334`). That path is DL-055 (PARTIAL): it uses only the *parsed filename* for series/volume, so an already-staged file with an unparseable name is never tagged, and it ignores calibration/intervention flags.

**Naming.** `backend/app/tagger/naming.py` is the single source of Calibre-parity formatting used by both tagging paths and the audit: `format_authors` (`"A,B; C & A"` → `"A & B & C"`, dedupe, first-seen order), `format_tags` (`", "` joined, dedupe), `format_publisher`/`format_series` (whitespace collapse), `format_volume_number` (`1`→`01`, `1.5`→`1.5`, `100`→`100`), `format_volume` (Calibre index `28`/`1.5`, bundle range kept as `1-3`), and `format_comicinfo_title` (`Series v01`, `Series v01-03` for contiguous ranges, `Series v01, v03` for gaps, series name only when no volumes; falls back to the parsed series and then the filename stem) (`:65-249`). `volume_title_parts(parsed)` adapts a parser result to those kwargs. `comicinfo_title.py` is a pure backwards-compatible re-export (`backend/app/tagger/comicinfo_title.py:1-19`).

**Volume naming / inheritance.** `_VOLUME_TOKEN_RE` matches `v01`, `Vol. 1`, `volume 01`, `vols 01` etc. (`backend/app/volume_naming.py:19-22`); `replace_volume_number` only rewrites when exactly one marker exists and it carries the previous volume number (zero-padding width preserved); `inherit_previous_name`/`inherit_previous_title` apply that to the filename stem (keeping the extension) and to the ComicInfo Title (`:43-90`). `assess_volume_naming` returns `needs_intervention` + reason for: unparsed series, unreliable volume, multi-volume bundles, no marker, multiple different markers, marker not matching the parsed volume, or multiple markers in the ComicInfo title — plus `parsed_series`, `parsed_volume`, zero-padded `suggested_volume` (`:92-130`). `build_previous_volume_index` indexes staged `.cbz` files per normalized series (single-volume files only; series from parser then ComicInfo; volume from parser then ComicInfo `Volume`) and `find_previous_volume` returns the highest volume below the target (`:133-190`). `inherit_for_file` returns `(previous, filename, title)` or `(None, None, None)` (`:193-204`). Consumers: routing (`backend/app/tagger/pipeline.py:603-646`) and staging groups (`backend/app/staging_groups.py:22-49`). Tests: `tests/test_previous_volume_naming.py`, `tests/test_naming_split.py`.

### 3.13 Ownership, reconciliation, in-flight dedupe and auto-download

**Staging counts as owned.** `staged_ownership.build_staged_volumes_map(db, staging_dir)` walks staging (skipping hidden/symlinked dirs, temp files, non-archive suffixes), parses each name to series+volumes and maps both the normalized series and its `split_series_candidate_parts` variants to the volume set; for COMPLETED+routed rows whose staged filenames were unparseable it adds a bounded fallback from `download_wanted_json`/`volumes_json`, skipping rows already represented by a parsed file and rows in `IN_CALIBRE/IGNORED/BLACKLISTED/IN_STAGING` (`backend/app/staged_ownership.py:54-113`). `get_staged_volumes_map` caches for `CACHE_TTL_SECONDS = 60` keyed by `(str(db), realpath(staging_dir))`; `invalidate_staged_ownership_cache()` clears it (`:24-28,116-137`). Invalidation happens after purge/retag/manual-tag/per-file staging delete (`backend/app/staging_duplicates.py:207-211`; `backend/app/staging_groups.py:170-174`; `backend/app/main.py:2801-2805,3286-3291`). The map is injected into `MangaCalibreMatcher(staged_volumes_map=…)` (`backend/app/calibre/matcher.py:11-16`) and merged into the owned volume set for `check_release`, so wanted/missing/status/auto-download treat "in staging" as owned while labels still say "In staging" (`backend/app/calibre/matcher.py:107-131,150-205`). The same map is used by the grouped releases view (`backend/app/database.py:4016-4022,4170-4174`).

**Resolution of wanted volumes.** `resolve_release_missing_volumes(release)` (`backend/app/scheduler.py:1029-1053`) precedence: `download_wanted_json` (the effective wanted set persisted at submit/retry/pickup correction) → `missing_vols_json`/`missing_volumes` → a fresh `matcher.check_release(...)` computation → the stored value again. This is why the pickup correction route must also write `download_wanted_json` (DL-037, regression test `tests/test_manual_pickup_volume_correction.py`).

**In-flight dedupe.** `_auto_download_wanted(rel, series, missing)` (`backend/app/scheduler.py:1248-1304`) computes reserved = `db.get_inflight_reserved_volumes([series names], exclude_guid=rel.guid)` ∪ `_staging_reserved_volumes()` for those series, then `wanted = missing - reserved` (rounded to 2 decimals). Any reduction audits `IN_FLIGHT_DEDUPE` with the reserved set. `get_inflight_reserved_volumes` selects releases of those series with `download_status IN ('DOWNLOADING','COMPLETED')` and status not in `IN_CALIBRE/IGNORED/BLACKLISTED`, prefers `download_wanted_json` over the bundle's full `volumes_json` (so a strict subset only reserves the volumes actually being fetched), and excludes the current release by guid (`backend/app/database.py:2252-2296`). `_staging_reserved_volumes()` is a bounded 60 s cached scan of staging (`backend/app/scheduler.py:1093-1126`).

When **all** missing volumes are reserved, the release is parked: `download_status='PENDING_DEDUPE'` (`backend/app/scheduler.py:3706-3715`) and audited `AUTO_DOWNLOAD_SKIPPED` with reason `all_volumes_inflight`. `sweep_pending_dedupe(limit=50)` (every 60 s) re-checks each parked row: non-`REQUESTED` rows or rows with nothing missing are cleared to `IDLE`; a fresh matcher pass can clear rows that became `IN_CALIBRE` or narrow the missing set; rows still reserved stay parked; otherwise the row is submitted with the full missing set (Torbox via `submit_torbox_download`, qBittorrent via `add_torrent` + handle + optional selective filter) and audited (`backend/app/scheduler.py:2329-2455`; timer `:5266-5272`).

**Auto-download.** On a feed poll, a release gets auto-download treatment only when it is new or transitioning **and** becomes `REQUESTED` **and** `auto_download` global (default true) **and** the feed's `auto_download` toggle (`backend/app/scheduler.py:3676-3685`). Guards, each audited `AUTO_DOWNLOAD_SKIPPED` with a machine-readable reason, in order: no missing volumes (`no_missing_volumes`), engine not ready (`engine_unavailable:<reason>`; Torbox readiness = enabled + API key, qBittorrent = client constructible, `:1055-1069`), no download URL (`no_download_url`), all volumes in flight (`all_volumes_inflight` ⇒ PENDING_DEDUPE). On submit: Torbox via `submit_torbox_download(source=<feed name>)` (`:3716-3743`), qBittorrent via `add_torrent` + `update_download_handle(wanted_volumes=auto_wanted)` + `apply_selective_volume_download` for partial bundles (`:3744-3780`). After the poll, if anything was sent, a routing pass runs immediately (`:3801-3807`). The decision audit payload carries `reason`, `feed`, `series`, `title`, `missing_volumes`, `wanted_volumes` (`:1071-1091`).

**Stale-file reconcile.** `downloads_reconcile.reconcile_missing_release_files(db, downloads_dir, staging_dir, calibre_series=None, dry_run=False)` (`backend/app/downloads_reconcile.py:108-195`):

* selects `releases WHERE download_status='COMPLETED' AND status NOT IN ('IN_CALIBRE','IGNORED','BLACKLISTED')` (the extra `EXCLUDED_DOWNLOAD_STATUSES` check is unreachable for `COMPLETED` — §8 Q11);
* indexes downloads and staging by basename; if **any** recorded basename is found, it rewrites `download_files_json` to the single found path and keeps the release;
* otherwise, if a live Calibre match exists and the release's max wanted volume ≤ the matched `book_count`, it promotes the release to `IN_CALIBRE` (precedence-aware);
* otherwise, if a parsed staging file matches the series and (no wanted set or an intersecting volume), it re-points `download_files_json` at that staging path;
* otherwise the release is reset: `download_status='IDLE'`, `routed_at=''`, `downloaded_at=NULL`, `download_files_json='[]'`, `downloader_id=NULL`, `torrent_hash=NULL`, `download_error=RESET_HINT`, audited `DOWNLOAD_FILES_MISSING` (`:92-105`);
* returns `{checked, kept, reset, in_calibre, reset_releases, errors, dry_run}` with per-release exception isolation.

Triggered by the 5-min timer (`backend/app/scheduler.py:5221-5232`), `POST /api/downloads/scan`, and after failed-document/per-file deletes (`backend/app/main.py:3171-3177,3190-3196,3302-3309`).

### 3.14 Consolidated endpoint reference (this domain)

All routes are FastAPI handlers in `backend/app/main.py` and are subject to the application auth middleware (see `backend/app/auth.py`); the table lists request shape and salient response fields only.

| Method + path | Params | Response (salient) |
|---|---|---|
| `GET /downloads` | — | HTML page (§7) |
| `GET /downloads/manual-pickup` | — | HTML page (§7) |
| `POST /api/staging/tag` | — | `{success, message, tagged_count, tagged_files[], …routing result}` |
| `GET /api/downloads/status` | `timeout` 0.2-5.0 | status payload (§3.7) |
| `POST /api/downloads/torbox/poll` | — | `{status, checked, completed, failed, queued_fetch, fetching, last_checked_at, torrents[], downloader}`; 409 if engine≠torbox |
| `POST /api/downloads/prune-empty` | `{include_staging?: bool}` | `{removed, dirs[], skipped_busy, downloads{}, staging{}, task_id, task}` |
| `POST /api/downloads/flatten-staging` | — | `{flattened, skipped_existing, conflicts_renamed, dirs_removed, task_id, task}` |
| `POST /api/downloads/scan` | — | `{inventory, last_scanned_at, entries[], reconciled{}, task_id, task}` |
| `GET /api/downloads/auto-download/diagnostics` | — | diagnostics payload (§3.7) |
| `POST /api/downloads/awaiting-series/match` | `{source_series, calibre_series_id? / new_series_title?}` | `{status, target{name,calibre_series_id}, source_series, override, retagged_files, moved_files, release_ids[]}` |
| `POST /api/downloads/inventory/audit` | — | `{status, entries[], candidates[], duplicates{}, already_present{}, totals{}}` |
| `POST /api/downloads/inventory/clear` | `{confirm:true, mode, relatives?[]}` | `{status, mode, deleted, files_deleted, bytes_freed, skipped, failed, remaining}` (async task) |
| `POST /api/downloads/inventory/delete` | `{confirm:true, relative}` | `{status, deleted, relative, files, bytes_freed, remaining_files}` |
| `GET /api/downloads/failures` | — | `{status, failures[], count}` |
| `POST /api/downloads/failures/scan` | — | `{status, scan{checked,failed,cleared}, failures[], task_id, task}` |
| `POST /api/downloads/failures/{id}/retry` | — | `{status, failure_id, release_id, download, state}` |
| `POST /api/downloads/failures/{id}/convert` | — | `{status, source, target, entries, bytes, routing, task_id, task}` |
| `POST /api/downloads/failures/{id}/delete` | — | `{status, deleted, skipped, failed, bytes_freed, deleted_ids[], remaining}` |
| `POST /api/downloads/failures/delete-all` | `{confirm:true}` | same shape as above |
| `GET /api/downloads/staging-groups` | — | `{status, groups[], count}` |
| `POST /api/downloads/staging-series/match` | `{source_series, target_series / new_series_title / calibre_series_id}` | `{status:"started", source, target, task_id, task}` |
| `POST /api/downloads/files/delete` | `{area:"staging"/"downloads", relative or filename}` | `{status, area, deleted, relative, remaining_files, series, series_remaining}` |
| `GET /api/downloads/staging-duplicates` | — | `{status, duplicates, total_bytes, files[], groups[], staging_dir}` |
| `POST /api/downloads/staging-duplicates/purge` | `{confirm:true, filenames?[] or all:true}` | `{status:"started", task_id, task}` |
| `GET /api/tagger/preview` | `filename` | `{status, file, name, size_mb, has_comicinfo, comicinfo, parsed{}, warning}` |
| `POST /api/tagger/manual-tag` | `{filename, series, volume?, title?, writer?, publisher?, genre?, new_filename?}` | `{status, file, name, renamed_from, replaced_invalid_comicinfo, comicinfo{}}` |
| `GET /api/tagger/audit` | — | `{status, staging_dir, summary{}, files[], task_id, task}` |
| `POST /api/tagger/repair-series` | `{files[1..200], repair_writer, repair_publisher}` | `{status, requested, repaired, unchanged, skipped, failed, field_counts{}, results[], task_id, task}` |
| `POST /api/downloader/test` | `{qbit_url?, qbit_username?, qbit_password?}` | `{success, version, message}` |
| `POST /api/downloader/torbox/test` | `{torbox_api_key?}` | `{success, engine, plan, active_slots, message}` |
| `POST /api/downloader/configure` | `{qbit_url, qbit_username, qbit_password?, qbit_enabled, qbit_category, qbit_tags}` | `{status, message, connection{}}` |
| `GET /api/downloader/queue` | — | `{status, torrents[]}` (unused by the page) |
| `POST /api/downloader/pause/{hash}` / `POST …/resume/{hash}` / `DELETE …/delete/{hash}` | — | `{status: "success"/"error"}` |
| `POST /api/downloads/torbox/{release_id}/retry` | — | `{status, engine, message}` / `{status:"skipped"}` / `{status:"error", message}`; 404/409/429 |
| `POST /api/downloads/torbox/{release_id}/remove` | — | `{status:"success", removed, remote_deleted, remote_error, message}` |
| `POST /api/downloads/torbox/clear-failed` | — | `{status, failed_entries, removed, remote_deleted, task_id, task}` |
| `POST /api/releases/{release_id}/download` | — | `{status, engine, cached?, message, state}`; 409 for IN_STAGING |
| `GET /api/releases/{release_id}/state` | — | `release_download_state` payload |
| `GET /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks/{id}/cancel` | `active`, `limit` | serialized task(s) |

---

## 4. Data model touched

### 4.1 `releases` columns owned/consumed by this domain

Declared in `backend/app/database.py:450-546` (base CREATE TABLE plus incremental `ALTER TABLE` upgrades; every column is added defensively so upgrades are idempotent).

| Column | Type / default | Written by | Meaning / invariant |
|---|---|---|---|
| `download_status` | TEXT `'IDLE'` | this domain | `IDLE`, `DOWNLOADING`, `PENDING_DEDUPE`, `MANUAL_PICKUP`, `COMPLETED`, `FAILED`. `get_active_torbox_downloads` = `DOWNLOADING` only (`backend/app/database.py:2874-2882`) |
| `downloader` | TEXT `''` | this domain | `qbit` or `torbox`; selects provenance and the qBittorrent selective filter's `downloader != 'torbox'` predicate |
| `downloader_id` | TEXT `''` | this domain | qBittorrent infohash or Torbox `torrent_id`; `queued:<id>` encodes a queued Torbox submission (`backend/app/downloader/base.py:48-68`) |
| `torrent_hash` | TEXT | this domain | Nyaa/engine infohash; cleared by remove/reset |
| `downloader_started_at` | TEXT `''` | `update_download_handle` | Timeout clock (`torbox_timeout_hours`); preserved on queue promotion |
| `download_wanted_json` | TEXT `'[]'` | submit/retry/pickup correction/list-match | **Authoritative** wanted volume set (precedence over `missing_vols_json`, `backend/app/scheduler.py:1029-1053`) |
| `download_error` | TEXT `''` | failures/reset | User-visible reason, includes `(attempt N, UTC)`; never contains secrets |
| `download_attempts` | INTEGER `0` | failures/clear | Monotonic while failing; reset by `clear_download_error`/`update_download_handle` |
| `torbox_progress_json` | TEXT `'{}'` | poller/fetch | `{phase, downloaded_bytes, total_bytes, speed_bps, progress_pct, ready_detected_at, fetch_started_at, fetch_finished_at, routed_at, updated_at}`; phases `ready / processing / fetching / routing / manual_pickup / done / failed` |
| `download_files_json` | TEXT `'[]'` | fetch/pickup/reconcile | Absolute paths of fetched files; used by inventory release matching, reconcile and purge bookkeeping |
| `downloaded_at`, `routed_at` | TEXT `''` | fetch/reconcile | `routed_at` set by `mark_release_routed` when every saved path has left downloads; drives "In staging" label and staged-ownership fallback |
| `media_type`, `is_light_novel`, `status`, `volumes_json`, `missing_vols_json`, `is_bundle`, `volume_start/end` | — | Releases domain | consumed read-only here; `volumes_json` is the bundle's full volume list |

### 4.2 `download_failures` (`backend/app/database.py:960-979`)

`id`, `file_relative TEXT NOT NULL UNIQUE` (downloads-relative POSIX path), `reason`, `detected_type`, `release_id`, `series_name`, `volumes_json`, `attempts`, `status` (`failed`|`retried`|`deleted`, default `failed`), `created_at`, `updated_at`; index on `(status, created_at)`.
Invariants: one row per path; every re-record resets `status='failed'` and increments `attempts`; only `status='failed'` rows are listed (`list_download_failures`); rows are deleted only by `clear_download_failure` (which filters `status='failed'`).

### 4.3 `torbox_manual_pickups` (`backend/app/database.py:939-959`)

`id`, `release_id`, `torrent_id TEXT`, `title`, `series_name`, `files_json` (list of `{id,name,size,suggested_volume,volumes,mapping_source,fetched}`), `wanted_json`, `reason`, `status` (`pending|fetching|partial|completed|discarded|expired`, default `pending`), `error`, `created_at`, `expires_at`, `updated_at`; index on `(status, created_at)`.
Invariants: active = `pending|fetching|partial` (`count_active_manual_pickups`, `list_torbox_manual_pickups(False)`); expired = `expires_at < CURRENT_TIMESTAMP` and active status (`expired_torbox_manual_pickups`); a pickup is never deleted by the application (only status-changed).

### 4.4 `background_tasks` (`backend/app/database.py:888-910`)

`id`, `kind`, `title`, `scope`, `status` (`running|success|failed|cancelled`), `progress_pct`, `message`, `items_done`, `items_total`, `started_at`, `finished_at`, `actor`, `error`, `result_json`, `updated_at`.
Task kinds emitted by this domain: `downloads:prune`, `downloads:flatten`, `downloads:rescan`, `downloads:failures-scan`, `downloads:archive-convert`, `downloads:inventory-clear`, `downloads:staging-purge`, `downloads:staging-series-match`, `downloads:torbox-clear-failed`, `torbox:pickup:fetch`, `torbox:pickup:retry`, `audit:comicinfo`, `repair:comicinfo`. Foreground actions also record tasks through `_recorded_task` (`backend/app/main.py:1267-1297`); long/network actions use `background_tasks.run_in_background` (`backend/app/background_tasks.py:158-173`, executor with `MAX_WORKERS=3`, progress writes coalesced to ≤1 per 0.5 s / 1 %).

### 4.5 `app_settings` keys (persisted runtime state)

`downloader_engine`, `qbit_*`, `torbox_*`, `downloads_dir`, `staging_dir`, `auto_tag`, `convert_archives_to_cbz`, plus runtime bookkeeping written by this domain: `downloads_inventory_json`, `downloads_scanned_at`, `downloads_awaiting_signature`, `downloads_routing_failed_signature`, `downloads_routing_summary`, `torbox_last_poll_at`, `storage_health_json` (`backend/app/downloads_inventory.py:243-247`; `backend/app/scheduler.py:1436-1439,2899-2902,759`; `backend/app/main.py:368-370`).

### 4.6 Filesystem invariants

* `staging_dir` is **flat**: no subdirectories are created by routing; legacy nested trees are flattened (`backend/app/tagger/pipeline.py:1-17,431-490`).
* Every move is atomic: same-device `os.replace`, or copy to `*.mangatrax-part` + `os.replace` + delete (`backend/app/tagger/pipeline.py:243-270`).
* Every streaming download writes `*.part` (or `.partN`) and renames only after a byte-count check (`backend/app/downloader/torbox.py:806-883,963-997,1105-1147`).
* Symlinks are never followed for staging/downloads enumeration, deletion, flattening or pruning; containment is checked with `realpath` before any delete/move (`backend/app/downloads_inventory_cleanup.py:214-270`; `backend/app/staging_duplicates.py:29-35`; `backend/app/tagger/pipeline.py:335-369`; `backend/app/security.py:377-420`).
* `downloads_dir != staging_dir`, enforced at settings save (`backend/app/main.py:4232-4245`) and by `MANGATRAX_PATH_ROOTS` allow-listing (`backend/app/main.py:328-338,4224-4231`).
* ComicInfo.xml lives at the archive root, exactly one entry after any rewrite (`backend/app/tagger/cbz_tagger.py:21-23,131-172`).

### 4.7 `activity_logs` event types emitted here

`TAGGED_AND_MOVED_TO_STAGING`, `AWAITING_CALIBRE_SERIES`, `DOWNLOADS_ROUTING_FAILED`, `DOWNLOADS_PRUNED`, `VOLUME_NAME_INHERITED`, `VOLUME_NAME_INTERVENTION_REQUIRED`, `ARCHIVE_CONVERTED_TO_CBZ`, `SELECTIVE_DOWNLOAD`, `TORBOX_SUBMITTED`, `TORBOX_CACHED`, `TORBOX_QUEUED`, `TORBOX_PROMOTED`, `TORBOX_PROCESSING`, `TORBOX_FAILED`, `TORBOX_DOWNLOADED`, `TORBOX_MANUAL_PICKUP`, `TORBOX_MANUAL_PICKUP_CAP`, `TORBOX_MANUAL_PICKUP_EXPIRED`, `TORBOX_MANUAL_PICKUP_FETCHED`, `AUTO_DOWNLOAD`, `AUTO_DOWNLOAD_SUBMITTED`, `AUTO_DOWNLOAD_SKIPPED`, `IN_FLIGHT_DEDUPE`, `MANUAL_DOWNLOAD`, `DOWNLOAD_FAILURE_RETRIED`, `DOWNLOAD_FAILURE_DELETED`, `DOWNLOAD_INVENTORY_DUPLICATE_CLEARED`, `DOWNLOAD_RAW_COPY_CLEARED`, `DOWNLOAD_INVENTORY_ENTRY_DELETED`, `DOWNLOAD_FILE_DELETED`, `DOWNLOAD_FILES_MISSING`, `STAGING_DUPLICATE_PURGED`, `STAGING_FILE_RENAMED`, `MANUAL_TAG_CBZ`, `CBZ_SERIES_REPAIRED`, `SERIES_MANUALLY_MATCHED`, `AUTO_TAG_CBZ`, `DOWNLOADER_CONFIGURED`, `BACKGROUND_TASK_STARTED`, `BACKGROUND_TASK_FINISHED`.

---

## 5. Configuration and tunables

### 5.1 Settings (persisted in `app_settings`, edited via `POST /api/settings`; validated by `backend/app/schemas.py`)

| Key | Default | Effect | Evidence |
|---|---|---|---|
| `downloads_dir` | `/downloads` | raw download destination for both engines; must differ from `staging_dir`; allow-listed by `MANGATRAX_PATH_ROOTS` | `backend/app/tagger/pipeline.py:40`; `backend/app/scheduler.py:1321-1329`; `backend/app/main.py:4232-4245` |
| `staging_dir` | `/staging` | flat hand-off folder; all staging scans/tagging/delete operate here | `backend/app/main.py:353-354` |
| `auto_tag` | `true` | enables the 5-minute legacy staging auto-tag + routing branch | `backend/app/scheduler.py:5191,5211-5219` |
| `convert_archives_to_cbz` | `false` | opt-in archive→CBZ conversion during routing (DL-050) | `backend/app/tagger/pipeline.py:557-579` |
| `downloader_engine` | `qbit` (validator accepts only `qbit`/`torbox`) | selects the engine everywhere | `backend/app/downloader/__init__.py:12-39`; `backend/app/schemas.py:718-726` |
| `qbit_url` | `http://localhost:8080` | qBittorrent Web API base | `backend/app/downloader/__init__.py:73`; `backend/app/scheduler.py:988-989` |
| `qbit_username` / `qbit_password` | `admin` / `adminadmin` | credentials | `backend/app/scheduler.py:990-991` |
| `qbit_enabled` | `true` | disables every qBittorrent call when false | `backend/app/scheduler.py:986-987` |
| `qbit_category` | `manga` | filter for the live queue listing only; release adds hard-code `manga` | `backend/app/main.py:606,3556,3795` |
| `qbit_tags` | `mangatrax` | stored but unused: release adds hard-code `mangatrax` | `backend/app/main.py:3557`; `backend/app/scheduler.py:3749` |
| `torbox_enabled` | `false` | gate; also requires an API key | `backend/app/downloader/__init__.py:61`; `backend/app/downloader/torbox.py:199` |
| `torbox_api_key` | `""` | secret, masked on read, never echoed; blank/mask on save means "unchanged" | `backend/app/main.py:1878-1889,4246-4251` |
| `torbox_delete_after_download` | `true` | delete the remote torrent after a successful fetch | `backend/app/scheduler.py:2555,2745-2747` |
| `torbox_poll_seconds` | `5` (validator 5-3600) | poll cadence floor | `backend/app/scheduler.py:2815-2835`; `backend/app/schemas.py:839-849` |
| `torbox_max_concurrent` | `2` (validator 1-20) | submit gate + fetch executor size | `backend/app/scheduler.py:1496-1503,2478-2482` |
| `torbox_timeout_hours` | `6` (validator 0.25-168; `0` disables) | hard timeout for active/queued downloads | `backend/app/scheduler.py:2907-2949`; `backend/app/schemas.py:865-875` |
| `torbox_strict_volume_selection` | `true` | strict exact-volume selection vs legacy whole-torrent fallback | `backend/app/scheduler.py:2111-2129` |
| `torbox_manual_pickup_enabled` | `true` | hold unmappable torrents instead of failing | `backend/app/scheduler.py:1613,1648-1650` |
| `torbox_manual_pickup_max_items` | `10` (0 = unlimited) | cap on active holds; above it the torrent fails instead | `backend/app/scheduler.py:1614-1617,1651-1657` |
| `torbox_manual_pickup_expiry_days` | `7` (0 = never) | hold lifetime before the sweep removes it | `backend/app/scheduler.py:1618-1621,1662-1664` |
| `torbox_max_download_gb` | `64` (validator 1-10240) | per-file / whole-torrent zip / pickup selection cap | `backend/app/downloader/__init__.py:46-50`; `backend/app/scheduler.py:2064-2085,1792-1810` |
| `torbox_delete_on_failure` | `true` | delete the remote torrent on permanent failure to free a slot | `backend/app/scheduler.py:1944-1952` |
| `torbox_download_concurrency` | `4` (validator 1-16, class cap 16) | parallel per-file fetches | `backend/app/downloader/__init__.py:51-58`; `backend/app/downloader/torbox.py:203,1356-1376` |
| `torbox_parallel_chunks` | `4` (validator 1-8, class cap 8) | HTTP-Range connections for files > 16 MiB | `backend/app/downloader/torbox.py:204,999-1015` |
| `auto_download` | `true` | global auto-download switch (Releases/feeds domain, consumed here) | `backend/app/scheduler.py:1166,3677-3685` |
| `calibre_path` | `/calibre` (env `CALIBRE_DIR`) | live Calibre snapshot for duplicate/audit/ownership decisions | `backend/app/main.py:80,2855-2863`; `backend/app/scheduler.py:209-213` |
| `activity_retention_days` / `activity_max_rows` | `90` / `50000` | bounds the activity log that diagnostics reads | `backend/app/scheduler.py:2296-2318` |

### 5.2 Environment variables

| Variable | Default | Effect |
|---|---|---|
| `DATA_DIR` | `<repo>/backend/data` | DB + cover cache + logs (`backend/app/main.py:77-79`) |
| `CALIBRE_DIR` | `/calibre` | default `calibre_path` (`backend/app/main.py:80`) |
| `MANGATRAX_PATH_ROOTS` | unset | `os.pathsep`-separated allow-list for `calibre_path`/`staging_dir`/`downloads_dir` (`backend/app/main.py:328-338`) |

### 5.3 Code constants / tunables

| Constant | Value | Where |
|---|---|---|
| `MAX_ENTRIES` (inventory) | 200 entries, `truncated` flag | `backend/app/downloads_inventory.py:20,182-184` |
| `MAX_DEPTH` | 2 — **dead constant** (never referenced) | `backend/app/downloads_inventory.py:21` |
| Partial suffixes | `.part`, `.mangatrax-part`, `.torbox-download.zip.part` | `backend/app/downloads_inventory.py:22` |
| `TORBOX_API_TIMEOUT_SECONDS` / `TORBOX_STREAM_TIMEOUT_SECONDS` | 30 s / 60 s | `backend/app/downloader/torbox.py:48-49` |
| `TORBOX_STREAM_CHUNK_BYTES` | 256 KiB | `:50` |
| `TORBOX_MAX_FILE_BYTES` | 64 GiB (factory default; overridden by setting) | `:54` |
| `TORBOX_ZIP_MAX_FILES` / `TORBOX_ZIP_SAFETY_RATIO` | 20 / 0.9 | `:55-56` |
| `TORBOX_DEFAULT_CONCURRENCY` / `MAX_CONCURRENCY` | 4 / 16 | `:58-59` |
| `TORBOX_DEFAULT_PARALLEL_CHUNKS` / `MAX_PARALLEL_CHUNKS` | 4 / 8 | `:60-61` |
| `TORBOX_RANGE_THRESHOLD_BYTES` | 16 MiB | `:62` |
| `TORBOX_MAX_TORRENT_BYTES` / `TORBOX_MAX_REDIRECTS` | 10 MiB / 5 | `:64-65` |
| Range fallback / retryable status codes | 403,404,405,416,429 / 403,404,410,416 | `:163-164` |
| Fetch retry backoff | 3 attempts, 1/2/4 s | `:1266-1297` |
| Per-range retry | 2 attempts, 0.5 s | `:1019-1041` |
| Torrent fetch retry | 2 attempts, 1 s | `:432-452` |
| `_TORBOX_STATUS_TTL_SECONDS` / `_TORBOX_STATUS_STALE_AFTER_SECONDS` | 1.0 s / 30 s | `backend/app/main.py:53-54` |
| Speed history limit / avg samples | 120 / 10 | `backend/app/main.py:55-56` |
| `_DEFAULT_DOWNLOADER_STATUS_TIMEOUT` / `_MAX_…` | 1.5 s / 5 s | `backend/app/main.py:473-474` |
| Progress write throttle | 2 s or 1 % | `backend/app/scheduler.py:2486-2527` |
| Poll intervals (worker) | 45 s inventory+route, 30 s qbit filter, 5 min auto-tag, 5 min stale reconcile, 5 min pickup sweep, 60 s dedupe sweep, 10 s loop sleep | `backend/app/scheduler.py:5175-5277` |
| `_TORBOX_RETRY_MIN_SECONDS` | 5 s | `backend/app/main.py:3604` |
| Staged ownership cache TTL | 60 s | `backend/app/staged_ownership.py:24` |
| `background_tasks.MAX_WORKERS` / write interval / pct epsilon | 3 / 0.5 s / 1.0 | `backend/app/background_tasks.py:24-26` |
| `_STAGING_FILENAME_MAX_LENGTH` | 512 | `backend/app/main.py:350` |
| `archive_conversion` caps | 10 000 entries / 2 GiB total / 512 MiB entry | `backend/app/archive_conversion.py:22-24` |
| `prune_empty_dirs` min age fallback | 60 s | `backend/app/tagger/pipeline.py:298` |
| Repair request cap | 200 files | `backend/app/schemas.py:1064,1075-1076` |
| Fuzzy match thresholds | ratio ≥ 0.86, ambiguity margin 0.05 | `backend/app/tagger/pipeline.py:68-69` |

---

## 6. Integration contracts

### 6.1 Consumes from / exposes to **Releases**

Consumes: release rows (`id, guid, title, series_name, volumes_json, missing_vols_json, is_bundle, download_url, link, guid, torrent_hash, status, media_type`), the Calibre matcher (`check_release`) and `resolve_release_missing_volumes`.

Exposes:

* submission columns and helpers (`update_download_handle`, `update_download_status`, `update_downloader_state`, `set_downloader_id`, `set_download_files`, `mark_release_routed`, `clear_downloader_submission`, `set_release_download_wanted`, `update_release_volumes_and_status`, `increment_download_attempt`, `set/clear_download_error` — `backend/app/database.py:2596-2882`);
* `tbox_progress_json` phase/latency data rendered by the Releases page (`release_download_state`, `backend/app/main.py:1314-1361`);
* `release_download_state` labels used by both Releases and Downloads (`Downloading`, `Downloaded (awaiting staging)`, `In staging`, `In Calibre`, `Failed`, `Waiting for in-flight download`, `Not downloaded`);
* destructive/terminal guards: both download routes refuse `IN_STAGING` with HTTP 409 (`backend/app/main.py:3614-3618,3744-3748`);
* the "staging counts as owned" contract that changes what `check_release` reports as missing (`backend/app/calibre/matcher.py:107-131,150-205`) — this is co-owned with the Releases expert and must not be broken unilaterally;
* audit events listed in §4.7 that the Releases/UI pages surface.

### 6.2 Consumes from **Series / Calibre**

`get_series_templates_map()` (normalized series → `{series_name, authors, publisher, tags}`, with alias overrides merged — `backend/app/database.py:2313-2339`), `get_all_series()`, `sync_calibre_series`, `link_tracked_series`, `add_series_alias_override`, `get_authoritative_alias_sets`, `get_all_manga_metadata_cache`, and live Calibre snapshots via `CalibreMangaScanner(calibre_path).get_all_series()` (`backend/app/main.py:2855-2863`, `backend/app/scheduler.py:207-223`). Calibre's owned volumes are the authority for duplicate purging; book counts are deliberately not used (`backend/app/staging_duplicates.py:58-63`).

### 6.3 Consumes from **Platform**

* Settings + secret masking (`backend/app/schemas.py`, `backend/app/main.py:4211-4279`).
* Auth actor attribution (`auth_module.get_current_actor()`) on every mutating handler.
* Path/URL safety: `security.ensure_path_within_roots`, `validate_directory_setting`, `validate_public_url`, `UnsafepathError`/`UnsafeURLError` (`backend/app/security.py:377-420,228-260`).
* Persisted background tasks + serialized progress (`backend/app/background_tasks.py`; `/api/tasks` endpoints `backend/app/main.py:1235-1264`).
* Storage health probes and their warning partial (`backend/app/main.py:357-394`; `backend/app/templates/downloads.html:54`).
* Activity log (`db.log_activity`) and its retention pruning (`backend/app/scheduler.py:2296-2318`).
* Notifier: `notify_acquired` after a successful fetch, `notify_processed_batches` after routing (`backend/app/scheduler.py:2670-2675,1377-1420`).
* Network diagnostics surface `torbox_download_concurrency`/`torbox_parallel_chunks` for troubleshooting (`backend/app/network_diagnostics.py:308-309`).

### 6.4 Exposes to **UI**

* Two pages (§7) and their JSON APIs (§3.14).
* Task kinds + progress reporting: foreground actions use `_recorded_task` (kind, title, `items_done/items_total/message/progress_pct`, final `result_json`); long actions use `run_in_background` and return `{status:"started", task_id, task}`. The global task panel polls `/api/tasks?active=0&limit=20` every 1.5 s and re-attaches buttons by `data-task-kind`/`data-task-scope` (`backend/app/templates/base.html:1004-1144`); `MangaTasks.watch(task_id, cb)` polls a specific task every 1.5 s for completion callbacks.
* Button → task-kind mapping used by the Downloads page: `downloads:prune` (prune), `downloads:flatten` (flatten), `downloads:staging-purge` (staging duplicate purge; the audit button carries that kind too), `audit:comicinfo` (audit), `repair:comicinfo` (repair), `downloads:archive-convert` (failed-document convert), `torbox:pickup:fetch`/`torbox:pickup:retry` (Manual Pickup). `downloads:process` on the "Process Downloads" button is decorative: `/api/staging/tag` is a foreground await that creates no background task, so no button re-attachment ever matches it.

### 6.5 Queued-slot / engine handle contract (must be preserved by any refactor)

`downloader_id` is the only persisted handle. `parse_queued_handle_id` returns an int for `queued:<n>` and `None` otherwise (`backend/app/downloader/base.py:59-68`); every consumer that reconstructs a handle (`_torbox_handle_for_release`, poll loop, status view, retry, remove) must use it — otherwise a queued submission is treated as a missing/garbage torrent id.

---

## 7. UI touchpoints and mobile behavior (summary)

### 7.1 `/downloads` (Downloader & Staging Pipeline) — `backend/app/templates/downloads.html` (2 106 lines)

Server-rendered context: `active_page`, `qbit_status{success,message,version,degraded}`, `engine`, `torrents[]`, `torbox_last_checked_seconds_ago`, `staging_dir`, `downloads_dir`, `storage_health`, `downloads_inventory`, `downloads_inventory_scanned_seconds`, `staging_files[]`, `staging_groups[]`, `awaiting_series[]`, `awaiting_total`, `settings`, `manual_pickup_count` (`backend/app/main.py:1828-1849`).

Sections in page order, with controls and state:

1. **Auto-download diagnostics** (always rendered, auto-loads on DOMContentLoaded): summary line (global/engine/feeds/monitored/last poll + hint), "Why nothing fires" checklist, "Recent decisions" scroll box, Refresh button (`:6-19,1555-1590`).
2. **Manual-pickup banner** — only when `manual_pickup_count > 0`; links to `/downloads/manual-pickup` (`:21-26`).
3. **Header** — "Test Downloader Connection" and "Process Downloads" (disabled + spinner while running, page reloads 1.5 s after success) (`:30-52`; `backend/app/templates/base.html:594-617`).
4. **Storage warning partial** (`:54`).
5. **Downloader status/config card** — connection state badge/message; collapsible inline qBittorrent form (URL/user/password/enabled) with "Save & Test Connection"; "Test" button (`:57-115,673-719`).
6. **Awaiting Calibre Series** — per-series rows with file counts/volumes, "Match series" (opens shared modal), per-file Delete (two-step) (`:117-160`).
7. **Failed documents** — count, "Scan for failed files", armed "Delete all" (5 s arm), confirm box, table (File/Size/Type-reason/Matched release/Actions) with Retry, Convert to CBZ (only when `can_convert`), Delete; empty state text (`:162-189,1676-1836`).
8. **Active Queue** — header shows engine name; when Torbox: last-checked, "Updated just now", reconnecting badge, "Clear failed", "Check now"; table columns Name/Size/Progress/Speed/State/Controls. Progress cell switches on `phase` (fetching bar with MB/s, "Ready — fetch queued", "Processing/routing…", "Processing on Torbox…", else percent + ETA); state cell shows Failed + error + `strict_hint`, or the phase-derived label; controls are Retry/Remove (Torbox) or Pause/Resume + Delete (qBittorrent) (`:191-330`).
9. **Raw Downloads Folder (raw)** — scanned-ago hint; buttons "Clean up raw duplicates", "Rescan", "Prune empty folders", "Flatten staging", "Tag & move now"; collapsible raw-cleanup preview with "Delete duplicates"/"Delete already-present copies" + confirm; table Name/Files/Size/Age/Series-Volumes/State/Matched release/Action with state badges (Ready to move / Awaiting Calibre / Partial / Unmatched), Manual match (awaiting only) and Delete (`:332-437`).
10. **Staging Directory** — group sections (`<details>`, open by default) with per-series summary (files, volumes, tagged/untagged, MB, "N need naming intervention"), "Match series", and a per-file table (Name/Size/ComicInfo tag/Status/Metadata) with "Fix naming" (when flagged), "Preview / Tag" and Delete; intervention filter select (All / Needs naming intervention) hides groups, not rows; empty state (`:439-528,2055-2061`).
11. **Staging cleanup / duplicates** — "Audit staging duplicates", "Purge selected (n)", "Purge all found", inline confirm, table with selection checkboxes and Calibre match/owned volumes column (`:530-558,1431-1553`).
12. **ComicInfo `<Series>` Audit & Repair** — "Audit Series Tags", "Repair Selected (n)" (enabled only with selection), summary line, table (checkbox/File/Current Series/Suggested/field diffs/Status/Why); after a repair it re-audits (`:560-612,1213-1359`).
13. **Tag modal** — file name, current ComicInfo (monospace, scroll), editable staged filename, Series, Volume, Title, Writer, Publisher, Genre, Save; "Fix naming" pre-fills suggested filename/title and reloads on save (`:616-670,1361-1429,2064+`).
14. **Manual series match modal** — search Calibre series (`/api/calibre/series`), pick a series or type a new title, Apply (`:2064-2106,1080-1141`).

Client behavior / known gaps:

* **Auto-refresh is Torbox-only.** `refreshDownloadsNow()` is started only when `engine == 'torbox'` (`:1209-1211`) and re-polls at 1 s (active rows) / 5 s (idle) (`:1143-1159`). With the qBittorrent engine the queue/inventory tables are static until a manual action or a full page reload.
* **Two-step confirmations** instead of `window.confirm()` for queue actions (4 s arm) and per-file deletes (4 s arm) because blocked dialogs made buttons look dead (`:765-778,1592-1614`).
* **Delete-all failures / raw clear** use an armed confirm with a 5 s / 6 s auto-cancel (`:1816,1901`).
* After a delete the DOM is patched locally (row/group removal, counts) and then a status/inventory refresh runs (`:1632-1675`).
* `data-task-kind` re-attachment works only for real background tasks (§6.4).
* The persisted `truncated`/`entry_count` inventory fields are never rendered; an inventory larger than 200 entries silently shows only the first 200 (`backend/app/downloads_inventory.py:182-184`; no `truncated` reference in the template).
* No pagination/search on the queue, inventory, failed-documents, staging-group or audit tables; every row is rendered.
* `staging_files` (server list) is only used for the "N CBZ file(s)" counter (`:462`); the table renders `staging_groups`, so a `build_staging_groups` failure shows a count with an empty table (the failure is only debug-logged, `backend/app/main.py:1803-1806`).

### 7.2 `/downloads/manual-pickup` — `backend/app/templates/manual_pickup.html` (282 lines)

Context: `active_page="downloads"`, `pickups[]` (each with decoded `files[]`, `wanted_volumes[]`, `release`, `total_bytes`), `active_pickup_count`, `pickup_cap`, `pickup_expiry_days`, `pickup_enabled` (unused by the template).

Per pickup card: title, `Release #id · series · GiB · Wanted volumes`, reason (amber), inline error slot, status + expiry; actions "Fetch whole torrent", "Retry strict mapping", "Edit series", "Discard"; "Select all files" toggle, wanted-volumes text input + "Save volumes", "Fetch selected (n)"; file table with Use checkbox (disabled when fetched), File, Size GiB, Suggested volume (number input, saved on change), Fetched. Empty state when no pickups.

Client behavior: mapping/volumes save silently with toasts on error; fetch/retry return `task_id` and are watched via `MangaTasks.watch`, updating the status text and an inline error ("N file(s) failed to fetch; mapping kept — adjust and retry"), then `MangaTasks.poll()`; discard removes the card after confirm; "Edit series" opens a shared Calibre-series match modal that posts to `/api/downloads/awaiting-series/match` (see §8 Q1). Mobile: cards stack (`flex-col lg:flex-row`), action buttons wrap (`flex-wrap`), the file table is horizontally scrollable, volume inputs are fixed-width (`w-20`).

### 7.3 Mobile behavior (summary, applies to both pages)

* Layout uses Tailwind responsive prefixes throughout: single-column grids on phones, `sm:`/`lg:` multi-column from small/large breakpoints (e.g. `grid-cols-1 sm:grid-cols-3`, `flex-col sm:flex-row`).
* Navigation collapses behind a hamburger (`#btn-mobile-menu`, `#mobile-menu`) below `md` (`backend/app/templates/base.html:212-221`).
* All wide tables are wrapped in `overflow-x-auto`, so on a phone they are usable only by horizontal scrolling; the queue table has 6 columns, inventory 8, failed documents 5, staging 5, audit 7 — no sticky columns, no card fallback.
* Dialogues are `fixed inset-0` with `p-4`, `max-w-lg`/`max-w-xl`, and internal scroll regions (`max-h-32/max-h-64 overflow-y-auto`).
* Toasts are fixed bottom-right (`#toast-container`, `backend/app/templates/base.html:275-276`) and the task panel floats at the bottom (`#tasks-toggle`).
* Live polling continues while the tab is visible; there is no visibility-based pause, and the 1 s cadence runs for as long as any Torbox row is active.

---

## 8. Open questions / ambiguities

**Q1 — Manual Pickup "Edit series" does not edit the pickup's series.**
`pickupEditSeries(id)` takes the card's `<h2>` (the torrent *title*) as `source_series` and opens the Calibre matcher (`backend/app/templates/manual_pickup.html:180-184,242-259`), which posts to `/api/downloads/awaiting-series/match`. That endpoint re-keys *releases* whose normalized `series_name` equals the source (plus alias/candidate overrides) and creates alias overrides; it never writes `torbox_manual_pickups.series_name` (`backend/app/main.py:2385-2412`). Since the source here is a release title (e.g. `Test Series v01-05`), the normalized equality test will usually fail, so the action can succeed with "Matched to …" while changing nothing that affects the pickup. Evidence: template change handler vs. server re-keying block. Decision needed: should "Edit series" update the pickup + release `series_name` directly?

**Q2 — "Check now" can run two poll passes concurrently.**
`torbox_poll_now()` both sets the poller wakeup event (`request_torbox_poll`) and immediately runs `poll_torbox_downloads()` in a worker thread (`backend/app/scheduler.py:2858-2867`), while the poller thread may simultaneously run its own pass after the wakeup (`:2837-2849`). `self._torbox_poll_lock = threading.Lock()` and `self._torbox_last_poll_monotonic` are initialized (`:101,107`) but **never used** anywhere in the module. Consequences are mostly benign (idempotent per-row work, fetch single-flight via `_torbox_fetching`) but duplicate `status()` API calls, duplicate audit lines and a double-stamped `torbox_last_poll_at` are possible. Should the poll be serialized with `_torbox_poll_lock`?

**Q3 — Manual Pickup "Fetch whole torrent" does not fetch the whole-torrent zip.**
The button posts `{all:true}` (`backend/app/templates/manual_pickup.html:130-135`); the route sets `indexes=None` when `all` (`backend/app/main.py:1705-1715`); the scheduler then selects all **unfetched** files as individual `requestdl` items, and only falls back to the whole-torrent zip when the pickup has **no file list at all** (`whole_torrent = not descriptors and not selected_indexes and not files`, `backend/app/scheduler.py:1786-1805,1839-1842`). The label therefore means "fetch every listed file individually". If every file is already `fetched`, the call raises "No files selected" (`:1804-1805`). Which semantics are intended?

**Q4 — Partial pickup marks the release COMPLETED.**
When only some files are fetched, the pickup becomes `partial` but the release is still set to `plan_status=COMPLETED` with `download_files_json` of the fetched subset, and routing runs only for `completed` (`backend/app/scheduler.py:1849-1867`). A COMPLETED release with missing volumes then depends on the 5-minute stale-reconcile / next poll to be reconsidered; is that intended, or should a partial fetch leave the release DOWNLOADING/MANUAL_PICKUP?

**Q5 — qBittorrent status payload falls back to Torbox rows.**
When the configured engine is qBittorrent but it is unreachable, `_build_downloader_status` fills `payload["torrents"]` from `_torbox_db_rows()` (Torbox release rows) with `live_state_available=false` (`backend/app/main.py:596-603`). The Downloads queue table would then show Torbox release rows under a "qBittorrent queue" heading. Is this an intentional graceful-degradation fallback, or should the qBittorrent branch return an empty list?

**Q6 — Auto-download diagnostics claim/behaviour mismatch about qBittorrent file selection.**
`limitations` says "qBittorrent cannot select individual files: a partial overlap downloads the whole torrent, then the existing selective-volume filter applies when supported" (`backend/app/scheduler.py:1232-1236`), yet `apply_selective_volume_download` *does* set `filePrio=0` per file when the torrent has multiple archives (`backend/app/downloader/qbit.py:285-368`). The limitation is really "not for single-archive torrents and only after metadata". Should the wording be tightened?

**Q7 — Inventory release matching can attach the wrong release.**
`_match_release` falls back to "normalized entry name is a substring of the release title" after the exact `download_files_json` path comparison fails (`backend/app/downloads_inventory.py:119-128`). A torrent folder named `Series 05` would match `Series 05 v01-05` and show that release as "Matched release"; also the substring direction means short folder names match many rows (first hit wins by `get_tracked_downloads` order). Is a fuzzy/title fallback acceptable for display only (it is used for display + `release` payload in the raw inventory and cleanup audit `release` field)?

**Q8 — Staging group volumes can be wrong for ComicInfo-only volume values.**
`build_staging_groups` computes volumes with `for value in (parsed.get("volumes") or tags.get("Volume") or [])` (`backend/app/staging_groups.py:51-55`). If the filename has no parsed volume and `ComicInfo <Volume>` is a multi-character string (e.g. `"12"`), this iterates the string's characters and yields volumes `[1.0, 2.0]`. `volume_naming.build_previous_volume_index` does not have the bug (it converts the value directly, `backend/app/volume_naming.py:159-164`). Confirm the intended fix: wrap the tag value in a one-element list (or parse it) before iteration.

**Q9 — Failure rows `retried`/`deleted` accumulate and a skipped retry still marks "retried".**
`mark_download_failure(..., "retried")` runs after any non-error response from the release-download route, including `{"status":"skipped"}` (nothing missing) (`backend/app/main.py:3058-3068`; `backend/app/main.py:3767-3774`). `list_download_failures` only lists `status='failed'` and `clear_download_failure` only deletes `status='failed'` rows (`backend/app/database.py:1431-1457`), so `retried`/`deleted` rows persist invisibly forever (no prune path exists in this module). Should a skipped submit mark the row retried?

**Q10 — `DELETE /api/downloader/delete/{hash}` leaves files behind, while the Torbox Remove also clears local state.**
`api_downloader_delete` calls `qbit.delete_torrent(hash, delete_files=False)` (`backend/app/main.py:3597-3601`), so qBittorrent removes the torrent but the raw files remain and stay visible in the raw inventory; the Torbox equivalent resets the release row entirely (`:3690`). The two engines therefore behave differently for "delete from queue". Is that asymmetry intended?

**Q11 — Dead/vestigial code paths in this domain.**
* `downloads_reconcile.EXCLUDED_DOWNLOAD_STATUSES` is checked against rows already filtered to `download_status='COMPLETED'` (`backend/app/downloads_reconcile.py:29-33,130-133`) — unreachable.
* `MAX_DEPTH` in `downloads_inventory` is unused (`backend/app/downloads_inventory.py:21`).
* `MangaScheduler.get_downloader()` and the whole `QBitDownloader` adapter have no production callers (§3.1); `QBitDownloader.cancel` also inverts semantics (`delete_files=not delete_remote`, `backend/app/downloader/qbit_adapter.py:77-81`).
* `_torbox_poll_lock` / `_torbox_last_poll_monotonic` unused (§Q2).
* `pickup_enabled` is passed to the Manual Pickup template but never read.
* `GET /api/downloader/queue` is unused by the UI.
Should these be removed in the redesign, or are they intentional seams?

**Q12 — Staging prune busy-set risk for qBittorrent folders.**
`prune_empty_dirs` protects directories whose basename equals a sanitised `DOWNLOADING` release title (`backend/app/tagger/pipeline.py:273-291,343-346`), and only when the DB lookup is unusable does it fall back to a 60 s mtime guard (`:319-330`). qBittorrent creates its own per-torrent folder names, which may not equal MangaTrax's sanitised title; a momentarily-empty qBittorrent folder can therefore be pruned if its name doesn't match the busy set. Does the 45 s routing pass (which calls this with `db` set, so no age guard) create a real race?

**Q13 — Formatting/**`import`**nuance:** `_manual_tag_updates` writes `Number` and `Volume` but only when a volume string is supplied, and `Manga` defaults only apply when creating a *new* document (existing XML keeps its `Manga` value) (`backend/app/main.py:2724-2760`). Is that intentional asymmetry for legacy files?

---

## 9. Consensus checklist

One checkable claim per feature ID. A claim is TRUE if the quoted behaviour is observable in the current code/tests.

- DL-001 — Selecting `torbox` in settings makes `build_downloader` return a `TorboxDownloader`, and any unknown `downloader_engine` value falls back to `qbit` with a warning.
- DL-002 — `QBitClient.test_connection` returns a structured failure for disabled/unconfigured/auth-failed downloaders and caches a successful/failed result for at most 8 seconds.
- DL-003 — Submitting to qBittorrent posts to `/api/v2/torrents/add` with category `manga`, tag `mangatrax` and the configured savepath, and a body containing `Fails` is treated as failure.
- DL-004 — Pause, resume and delete routes return `{"status":"success"}` only when the underlying qBittorrent call succeeds, and delete uses `deleteFiles=false`.
- DL-005 — qBittorrent selective download skips non-wanted volumes with `filePrio=0`, always skips `.txt/.nfo/.url/.html/.lnk`, keeps unparseable non-ancillary files, and reports `single_archive` instead of splitting a one-archive torrent.
- DL-006 — Every 30 seconds the scheduler applies selective download to bundles that are still DOWNLOADING and whose missing-volume set is smaller than the bundle volume set.
- DL-007 — The `DownloaderClient` protocol and `QBitDownloader` adapter have no production callers and are exercised only by tests.
- DL-008 — `POST /api/downloader/configure` persists `qbit_url/username/password/enabled/category/tags` (password only when supplied) and returns a fresh connection test in the same response.
- DL-009 — A `TorboxDownloader`'s repr/str never contains the API key, and all Torbox error messages are built from the request path only (never the `token=` URL).
- DL-010 — Torbox HTTP 401/403/429/404/409 outcomes map to distinct typed `DownloaderError` subclasses, and 429 honours a Retry-After value clamped to 0-600 seconds.
- DL-011 — A release whose only identifier is a Nyaa `/view/<id>` link is submitted by deriving `<base>/download/<id>.torrent`, and a fetched body is rejected unless it is bencoded with a `4:info` key.
- DL-012 — Before submitting, the client calls `checkcached`; a cached torrent is reported as `ready`/`cached=True`, while auth and rate-limit errors from that probe abort the submit.
- DL-013 — Magnet submissions use multipart/form-data with a `magnet` field, and `.torrent` submissions upload the validated bytes; the resulting `downloader_id` is the torrent id (or `queued:<id>`).
- DL-014 — A queued Torbox submission persists `downloader_id="queued:<id>"` and is promoted to the real torrent id (audited once as `TORBOX_PROMOTED`) when Torbox assigns it.
- DL-015 — A Torbox torrent is only `ready` when `download_present` is explicitly true; `download_finished`/processing states are reported as `processing` and never fetched.
- DL-016 — The Torbox poller waits `max(5, torbox_poll_seconds)` seconds while busy and 15-30 seconds when idle, and a submit wakes it immediately.
- DL-017 — Submission is refused when the number of active (non-queued) Torbox downloads is at or above `torbox_max_concurrent`, and any active row older than `torbox_timeout_hours` is marked FAILED with a message that says the release stays REQUESTED for retry.
- DL-018 — In strict mode the planner returns `skip` for an empty wanted set, returns `zip` for a fully-needed torrent with <=20 files when the estimated total size is unknown or <=90 % of the configured cap (streaming enforces the cap in the unknown-size case), and raises `DownloaderFileError` for a missing file list, any unmappable name, or any absent wanted volume.
- DL-019 — Torrent file names are mapped to volumes using volume/range markers with `Part N` stripped, and only when nothing matches does the last bare number (excluding years and 480/720/1080/2160) become the volume.
- DL-020 — With `torbox_strict_volume_selection=false`, unmappable names or a subset that equals all files fall back to the whole-torrent zip.
- DL-021 — A per-file fetch makes 3 attempts total (re-requesting a fresh `requestdl` link each time and sleeping 1 s then 2 s between attempts) for 429/503 and 403/404/410/416 responses, and persistent failure reports the attempt count.
- DL-022 — A whole-torrent zip fetch extracts with zip-slip/symlink/absolute-path rejection, removes partial output on failure, and raises when the archive contains no files.
- DL-023 — Files larger than 16 MiB are fetched as multiple HTTP ranges (2-8 connections) that are assembled, byte-verified and atomically renamed, with automatic single-stream fallback when the server ignores Range.
- DL-024 — Any file or archive above `torbox_max_download_gb` is rejected with its size and the cap before a download link is requested.
- DL-025 — A permanent Torbox failure stores a reason with attempt count and UTC time, sets FAILED, cleans `.part` remnants, and deletes the remote torrent when `torbox_delete_on_failure` is true.
- DL-026 — A strict-selection failure creates a `torbox_manual_pickups` row and sets the release to MANUAL_PICKUP instead of FAILED, provided manual pickup is enabled and under the cap.
- DL-027 — After a successful fetch the release becomes COMPLETED with `download_files_json`, is routed immediately, has inventory refreshed, and the remote torrent is deleted when `torbox_delete_after_download` is true.
- DL-028 — Ready releases are handed to a bounded `torbox-fetch` executor and the same release is never fetched twice concurrently.
- DL-029 — The Torbox retry route refuses IN_STAGING releases and non-Torbox engines, rate-limits to one retry per 5 seconds per release, and cancels the stale remote job before re-submitting with freshly resolved missing volumes.
- DL-030 — Removing a FAILED Torbox row performs no remote call and only clears local submission state; removing any other row attempts a remote cancel and always clears local state.
- DL-031 — Clear-failed removes every FAILED Torbox row locally with per-item progress and never waits on remote deletes.
- DL-032 — `POST /api/releases/{id}/download` sends the release to the configured engine using `resolve_release_missing_volumes` and refuses IN_STAGING releases with 409.
- DL-033 — `release_download_state` maps download/routed/status columns to downloading, downloaded, in_staging, in_calibre, failed, waiting_inflight or not_downloaded with `can_download`/`can_retry`/`terminal` flags.
- DL-034 — `/downloads/manual-pickup` lists only active pickups with per-file volume mapping inputs, fetch/retry/edit-series/discard actions; it has no control that changes the pickup's own series_name.
- DL-035 — The pickup list API returns decoded `files` plus active count, and `include_finished=1` includes completed/discarded/expired pickups.
- DL-036 — Saving a file's volume updates its `suggested_volume`/`volumes` and `mapping_source="manual"` in the pickup's `files_json`.
- DL-037 — Saving corrected pickup volumes rewrites `releases.volumes_json`, `missing_vols_json` and `download_wanted_json`, and the pickup's `wanted_json`, so the correction is honoured by strict retry.
- DL-038 — A pickup fetch runs as a background task and marks each successfully fetched file `fetched=true`, setting pickup status `completed` (none left) or `partial`.
- DL-039 — Strict retry plans against the pickup's corrected `wanted_json` (falling back to release missing volumes) and fetches only the matching file indexes.
- DL-040 — Discarding a pickup attempts a remote delete, marks the pickup `discarded` and the release FAILED.
- DL-041 — New holds are refused once the number of active pickups is at or above `torbox_manual_pickup_max_items` (0 = unlimited), and the 5-minute sweep expires past-due holds by removing the remote torrent and failing the release.
- DL-042 — `/api/downloads/status` never raises, clamps its socket budget to 1.5 s default / 5 s max, reports `disabled`/`degraded` states, and attaches the persisted inventory copy without scanning on read.
- DL-043 — Torbox status is served from a 1-second last-good cache with single-flight rebuilds, stale serving up to 30 seconds, and a bounded speed history for the sparkline.
- DL-044 — Check-now returns 409 unless Torbox is the active engine; otherwise it runs one poll pass and returns fresh counters.
- DL-045 — The diagnostics endpoint returns a readiness checklist, recent auto-download decisions and a last-poll hint explaining a zero-result poll.
- DL-046 — Routing tags and moves only files whose series resolves to a template; unresolved files stay in downloads and are summarised as awaiting Calibre.
- DL-047 — Staging destinations are always the flat root; an identical name+size is skipped and a same-name different-size file is renamed with a `(2)` suffix.
- DL-048 — Flatten moves every nested staged file to the root, deletes same-name/same-size nested duplicates, leaves `.part` files untouched and prunes emptied directories.
- DL-049 — Prune removes only empty directories, never follows symlinks, and skips directories belonging to in-flight downloads (or recently modified when the busy lookup is unavailable).
- DL-050 — Archive conversion during routing only happens when `convert_archives_to_cbz` is true, and a conversion failure records a failed document instead of moving the file.
- DL-051 — A file with a previous volume can inherit that volume's filename/title pattern with the number replaced, and unsafe naming cases are flagged as needing manual intervention.
- DL-052 — The awaiting-Calibre scan is read-only and groups unparseable/unmatched `.cbz` files by parsed series with counts and volumes.
- DL-053 — Matching an awaiting series creates alias overrides (including parse-shape candidates), re-keys matching releases, and immediately re-runs routing.
- DL-054 — "Process Downloads" runs both the routing pass and the legacy staging auto-tag and reports combined counts; it is a foreground request, not a background task.
- DL-055 — The legacy staging auto-tagger only injects ComicInfo.xml into staged CBZs that lack one and derives series/volume from the parsed filename alone.
- DL-056 — The worker loop runs inventory+routing every 45 seconds, auto-tag+routing every 5 minutes when `auto_tag` is on, and stale-release reconcile every 5 minutes.
- DL-057 — Template resolution order is exact normalized key, parse-shape candidates, cleaned key, then a unique difflib match ≥0.86 that is rejected when two candidates are within 0.05.
- DL-058 — The raw inventory scan is depth-limited to root entries plus their direct files, caps at 200 entries, and persists to `app_settings.downloads_inventory_json` with a scan timestamp.
- DL-059 — Inventory state is `partial` if any partial file exists, else `unmatched` without a parseable CBZ/series, else `ready` when the series resolves to a template, else `awaiting_calibre`.
- DL-060 — The cleanup audit lists duplicate entries (same CBZ name+size signature), entries entirely present in staging, and entries whose volumes are all in Calibre's explicit owned volumes.
- DL-061 — Clearing raw inventory requires `confirm=true` plus a valid mode, re-audits before deleting, never deletes the last copy, and revalidates containment/symlinks for every path.
- DL-062 — Per-row raw delete requires confirm, re-validates the entry against the current inventory, prunes emptied parents and refreshes the inventory.
- DL-063 — The failed-documents list returns only `status='failed'` rows with detected type, matched release, retryability and convertibility.
- DL-064 — The failure scan records invalid `.cbz`, unconvertible/convertible archives, ebooks and unrecognised files, clears rows for now-valid `.cbz`, and skips temp/hidden/symlink entries.
- DL-065 — Failed-document retry requires a matched release with a download URL that is not IN_CALIBRE/IGNORED/BLACKLISTED, and marks the row retried with an incremented attempt count.
- DL-066 — Failed-document convert runs the archive converter in a background task, clears the failure row on success and immediately re-runs routing.
- DL-067 — Failed-document delete validates the path, removes the file, prunes empty parents, marks the row deleted, and then reconciles releases; delete-all requires `confirm=true`.
- DL-068 — The converter rejects traversal/absolute/symlink/oversized entries, caps entries and total size, atomically renames a validated temp zip, and deletes the source only on success.
- DL-069 — Staging groups are keyed by ComicInfo `Series` (falling back to the parsed filename then "Unknown Series") and a group retag rewrites only `Series`, then links/merges or re-keys the series.
- DL-070 — Staging duplicate audit only lists files whose parsed volumes are all in Calibre's explicit owned volume set and never infers ownership from book counts.
- DL-071 — Purging staging duplicates requires confirm (for all), accepts exact relatives or unique bare filenames, and promotes a release to IN_CALIBRE only when no recorded staged copy or verified duplicate remains.
- DL-072 — The ComicInfo audit classifies each staged CBZ as ok/repairable/ambiguous/unknown_series/missing_series/untagged/invalid_comicinfo/unreadable and is strictly read-only.
- DL-073 — Repair resolves every requested file before writing, rewrites only audit-proven changes for the named files (max 200), and reports repaired/unchanged/skipped/failed counts.
- DL-074 — Manual tagging writes only supplied fields, creates `Title`/`Manga` defaults only for a new ComicInfo, replaces malformed XML, and optionally renames the staged file after validation.
- DL-075 — Per-file delete accepts only a verified staging scan entry or an awaiting-downloads file, re-validates path/symlink/existence immediately before removal, prunes empty parents and then reconciles releases.
- DL-076 — ComicInfo rewrite preserves every non-ComicInfo zip entry with its original ZipInfo, writes exactly one ComicInfo.xml, swaps atomically and returns true only on success.
- DL-077 — ComicInfo values are formatted Calibre-style: title `Series vNN` (contiguous bundles `v01-03`, gaps `v01, v03`), authors joined with ` & `, tags with `, `, volume as `28`/`1.5`.
- DL-078 — Volumes present in staging are merged into Calibre-owned volumes for release matching, cached for 60 seconds and invalidated after staging mutations.
- DL-079 — A volume already DOWNLOADING or COMPLETED (not in Calibre) for the same series is excluded from auto-download, the release is parked as PENDING_DEDUPE, and the 60-second sweep retries it when the reservation clears.
- DL-080 — Auto-download only fires for new/transitioning REQUESTED releases when the global and feed toggles are on, the engine is ready, a URL exists and at least one missing volume is not reserved.
- DL-081 — A COMPLETED release whose recorded files are gone is kept if a copy is found (raw/staging), promoted to IN_CALIBRE when the matched Calibre book count covers the highest wanted volume, and reset to IDLE with `DOWNLOAD_FILES_MISSING` only when nothing matches.
- DL-082 — `resolve_release_missing_volumes` prefers a persisted `releases.download_wanted_json` over the stored missing list, which overrides a fresh matcher computation; every correction path (manual-pickup volume edits, retries, dedupe) must write `download_wanted_json`.
- DL-083 — The release-download paths submit qBittorrent torrents with the literal category `manga` and tag `mangatrax` regardless of the `qbit_category`/`qbit_tags` settings; `qbit_category` is only used to filter the live downloader status list and `qbit_tags` is stored but unused.
- DL-084 — `GET /api/downloader/queue` exists but has no template caller (dead API surface); the Downloads page polls `/api/downloads/status` and the engine-specific endpoints instead.

---

## Appendix A — Test coverage map (evidence index, not run here)

This document was produced read-only; the following existing suites were inspected as behavioural evidence. Use them when validating the redesign (they are the executable form of the claims above).

| Test file | Covers |
|---|---|
| `tests/test_torbox_downloader.py` | engine factory, Torbox submit/status/fetch/zip/SSRF/secret masking, qBittorrent adapter smoke (`:332-346`) |
| `tests/test_torbox_notready.py` | state normalisation matrix, processing-never-fetches, range fallback, 404 fresh-link retry (DL-015, DL-021, DL-023) |
| `tests/test_torbox_strict_volumes.py` | strict planner table, unmappable/absent hard failures, legacy fallback, mapping robustness, status selection text (DL-018–DL-020) |
| `tests/test_torbox_queue_management.py`, `tests/test_torbox_large_downloads.py`, `tests/test_torbox_throughput.py`, `tests/test_torbox_latency.py` | queue control, size caps, concurrency/ranged transfer, latency accounting (DL-017, DL-023, DL-024, DL-029–DL-031) |
| `tests/test_torbox_manual_pickup.py`, `tests/test_manual_pickup_polish.py`, `tests/test_manual_pickup_volume_correction.py` | pickup hold/list/map/volumes/fetch/retry/discard, per-file progress, volume correction propagation (DL-026, DL-034–DL-041) |
| `tests/test_downloads_status_cache.py`, `tests/test_downloads_page_views.py` | Torbox last-good/stale/single-flight/speed history, payload numeric defaults, live-UI wiring (DL-042, DL-043) |
| `tests/test_auto_download_diagnostics_hint.py`, `tests/test_auto_download_observability.py` | diagnostics payload/hint/UI markers (DL-045) |
| `tests/test_downloads_routing.py` | routing rule, no-overwrite, move preference, path validation, engine savepaths, idempotence, manual endpoint (DL-046, DL-047, DL-054, DL-057) |
| `tests/test_flat_staging.py` | flat routing, collision policy, flatten idempotence, containment (DL-047, DL-048) |
| `tests/test_prune_empty_download_dirs.py` | prune safety, busy dirs, age fallback (DL-049) |
| `tests/test_stuck_files_archive_conversion.py` | failed-document scan/conversion safety/API/toggle (DL-063–DL-068) |
| `tests/test_failed_documents.py` | failure list/retry/delete semantics (DL-063, DL-065, DL-067) |
| `tests/test_downloads_inventory.py`, `tests/test_raw_inventory_cleanup.py`, `tests/test_downloads_file_delete.py` | inventory scan/state/cleanup/delete and per-file delete (DL-058–DL-062, DL-075) |
| `tests/test_staging_duplicate_purge.py`, `tests/test_staging_duplicate_purge_paths.py`, `tests/test_staging_duplicate_volume_matching.py` | duplicate audit/purge, bare-filename ambiguity, owned-volume matching (DL-070, DL-071) |
| `tests/test_staging_series_groups.py`, `tests/test_staging_counts_as_owned.py` | staging groups/retag and staged ownership (DL-069, DL-078) |
| `tests/test_comicinfo_calibre_format.py`, `tests/test_naming_split.py`, `tests/test_previous_volume_naming.py` | Calibre-format ComicInfo/title/authors/tags, naming split, inheritance (DL-051, DL-076, DL-077) |
| `tests/test_tag_display_gaps.py`, `tests/test_task_progress_counters.py`, `tests/test_task_progress_followups.py` | task progress reporting and UI gaps (DL-038, §6.4) |
| `tests/test_inflight_dedupe_jpegxl.py`, `tests/test_stale_release_reconcile.py`, `tests/test_release_download_state.py` | in-flight dedupe/PENDING_DEDUPE, stale reconcile, release download state (DL-033, DL-079, DL-081) |
| `tests/test_downloads_routing.py::test_path_validation_and_settings`, `tests/test_flat_staging.py::test_containment_and_docs` | downloads_dir ≠ staging_dir, containment/symlink guards (§4.6) |

## Appendix B — Implementation file index for this domain

| File | Lines | Role |
|---|---|---|
| `backend/app/downloader/base.py` | 110 | engine protocol, typed errors, queued-handle codec, magnet hash |
| `backend/app/downloader/__init__.py` | 78 | engine selection/factory, `as_bool` |
| `backend/app/downloader/qbit.py` | 388 | qBittorrent Web API client, selective file priorities |
| `backend/app/downloader/qbit_adapter.py` | 87 | vestigial protocol adapter |
| `backend/app/downloader/torbox.py` | 1439 | Torbox API client, state machine, streaming/ranged fetch, zip extraction, retries |
| `backend/app/scheduler.py` | 5314 | submit/poll/fetch threads, strict planner, manual pickup, dedupe, routing triggers, timers |
| `backend/app/tagger/pipeline.py` | 814 | two-folder routing, flatten, prune, awaiting scan, template resolution |
| `backend/app/tagger/cbz_tagger.py` | 334 | ComicInfo read/build/rewrite/inject, staging scan |
| `backend/app/tagger/naming.py` | 249 | Calibre-format formatters |
| `backend/app/tagger/series_audit.py` | 531 | `<Series>` reference/classification/audit/repair planning |
| `backend/app/tagger/comicinfo_title.py` | 19 | re-export shim |
| `backend/app/downloads_inventory.py` | 263 | raw inventory scan/persist |
| `backend/app/downloads_inventory_cleanup.py` | 349 | duplicate/already-present audit, clear, per-row delete |
| `backend/app/downloads_reconcile.py` | 195 | stale COMPLETED release reset |
| `backend/app/download_failures.py` | 228 | failure detection/list/retry/delete helpers |
| `backend/app/staging_duplicates.py` | 269 | staging duplicate audit/purge |
| `backend/app/staging_groups.py` | 183 | staging series groups + master retag |
| `backend/app/staged_ownership.py` | 152 | staged-volumes ownership map + cache |
| `backend/app/archive_conversion.py` | 185 | safe archive→CBZ converter |
| `backend/app/volume_naming.py` | 204 | volume-marker parsing, inheritance, intervention assessment |
| `backend/app/background_tasks.py` | 199 | persisted task runner + TaskReporter |
| `backend/app/main.py` | 4393 | HTTP routes for all of the above; status payload assembly |
| `backend/app/templates/downloads.html` | 2106 | Downloads pipeline page |
| `backend/app/templates/manual_pickup.html` | 282 | Manual Pickup page |
| `backend/app/templates/base.html` | 1148 | shared nav/mobile menu, toasts, MangaTasks panel, `triggerStagingTag` |



