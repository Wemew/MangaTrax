# MangaTrax — Application Design Specification

**Document:** `APP_DESIGN_SPEC` · **Version:** see the assembled cover page (`v0.10` while under panel review, `v1.0` after unanimous sign-off) · **Date:** 2026-11-08
**Code base:** commit `86a7bd8` (`fix/light-novel-release-filtering`), repo `C:\Users\rasmu\Documents\AI-Workspace\MangaTrax`
**Prepared by:** MangaTrax Expert Panel (5 experts, 5 overlapping domains), consolidated by the Team Lead.

> **Status.** Part 0 (this file), Parts 1–5 (domain specifications), and the Feature Registry are the
> panel-consensus description of what MangaTrax *is and does*. Every claim in the registry was
> authored from the implementation and is voted on by all five experts (see
> `DESIGN_CONSENSUS_LOG.md`). The same document is the baseline for the UI-enhancement work described
> in `UI_ENHANCEMENT_SPEC.md`.
>
> **v0.10 corrections.** Round-1 review found and fixed 38 claim wordings, 12 missing behaviours
> (added as new claims), 5 prose defects and every cross-reference in this Part 0. The corrections are
> recorded in `CORRECTIONS_ROUND1.md`; each reviewer's evidence is in `reviews/round1_*.md`.

---

## 0. How to read this specification

### 0.1 Purpose

This document is the complete, code-grounded description of MangaTrax: every user-visible feature,
every HTTP route, every background job, every setting, every persisted table, and the algorithms that
tie them together. It is written to be sufficient for a maintainer who has never seen the code, and
precise enough that the expert panel can vote on each individual behaviour claim.

### 0.2 Parts

| Part | File | Domain | Claims |
|---|---|---|---|
| 0 | this document | System overview, architecture, data model, end-to-end workflows, cross-cutting rules | — (synthesis) |
| 1 | `01_releases_feeds.md` | Releases, Prioritized, Discovery, Feeds, ingestion, parser, status lifecycle, backfill | REL-001…REL-044 |
| 2 | `02_series_library.md` | Overview dashboard, Series page, Calibre scan/sync/link, identity & matching, metadata/covers/search, audits | SER-001…SER-094 |
| 3 | `03_downloads_pipeline.md` | Downloads, qBittorrent, Torbox, Manual Pickup, staging/inventory/failures, CBZ tagger | DL-001…DL-084 |
| 4 | `04_platform_ops.md` | Deployment, settings, auth/security, scheduler/tasks/logs, diagnostics, maintenance, SQLite schema | OPS-001…OPS-098 |
| 5 | `05_ui_frontend.md` | Route map, every page's UI/JS, responsive/a11y state, usability-gap audit | UI-001…UI-137 |
| Registry | `FEATURE_REGISTRY_v0.10.md` | All 457 checkable feature claims, one line each | 457 |

### 0.3 Evidence conventions

* Paths are relative to the repository root; `backend/app/main.py:1199` means file and line in
  commit `86a7bd8`.
* **Lifecycle state** of a feature is one of:
  * **ACTIVE** — implemented and reachable as designed.
  * **PARTIAL** — implemented but materially incomplete, limited, or misleading versus its label.
  * **VESTIGIAL** — code/route/constant exists but has no effective consumer or effect.
* **Open questions / known issues** are not specification claims. They are collected in the domain
  parts (§8 of each) and indexed in §7 of this document. Their existence does not change the claim
  that describes today's behaviour.
* The specification describes the application **as built**, including defects. Fixes are out of scope
  for this document; the UI-enhancement work may address usability defects, and any behavioural
  change must update the corresponding claim and registry line.

### 0.4 Consensus protocol

1. Each expert author owns one domain part and its claims.
2. The merged document plus the Feature Registry are distributed to the whole panel.
3. Every expert reviews the registry and two foreign domain parts (each part is reviewed by two
   experts other than its author) and votes AGREE / DISAGREE per claim, with evidence.
4. Disagreements are resolved by amending the claim or the implementation description; the round
   repeats until **all five experts** sign off on **all claims**.
5. The signed-off document is copied to `docs/APP_DESIGN_SPEC.md` and
   `docs/archive/v1.0/` with the consensus log.

---

## 1. Product overview

### 1.1 What MangaTrax is

MangaTrax is a self-hosted **manga release monitor, downloader and Calibre library integrator**. It
answers one operational question continuously: *"for the manga series I follow, which volumes do I
not own yet, and where can I get them?"* — and then acts on the answer: it polls release feeds,
classifies and matches releases, downloads selected volumes, tags the resulting CBZ files with
ComicInfo.xml metadata, moves them to a staging folder, watches Calibre for them to appear, and keeps
a wanted/missing list accurate.

It is a single-tenant homelab application (typically one household, deployed on TrueNAS/Docker,
reachable on a LAN or behind a reverse proxy). There are no user roles: every authenticated user is an
administrator (Part 4 R-9).

### 1.2 The user-visible surfaces

Ten HTML pages, all server-rendered and enhanced with page-local JavaScript:

| Page | Route | Primary purpose |
|---|---|---|
| Dashboard | `/` | KPI snapshot, active downloads, wanted releases, recent activity |
| Series | `/series` | Calibre-derived library, per-series monitoring, match/refresh/backfill/link actions |
| Releases | `/releases` | All releases grouped by series or flat, filter by status, search, sort, download/ignore |
| Prioritized | `/prioritized` | Releases belonging to user-defined prioritized release groups |
| Discovery | `/discovery` | Releases not in the library and not tracked; track/blacklist; tag filtering |
| Feeds | `/feeds` | Feed CRUD, enable/disable, auto-download toggle, poll now, feed health |
| Downloads | `/downloads` | Downloader status/queue, staging inventory, failures, staging audit/repair |
| Manual Pickup | `/downloads/manual-pickup` | Map held Torbox torrents to series/volumes and fetch selected files |
| Logs | `/logs` | Activity/audit log with client-side search and prune |
| Settings | `/settings` | 45 writable configuration keys in 9 sections, tests, maintenance tools, reset |

Plus auth pages (`/auth/login`, `/auth/callback`, `/auth/logout`) and a JSON `/health` probe. The
application exposes **125 HTTP routes** in total (10 HTML pages + 3 auth + `/health` + 111 JSON
endpoints) — see Part 5 §2 for the complete map.

### 1.3 Operating model as implemented

* **Server-rendered, no SPA.** Jinja2 templates + inline classic JS; most pages fetch their large
  lists after first paint from JSON endpoints that return HTML fragments (`/api/series/rows`,
  `/api/releases/cards`, `/api/releases/flat_rows`, …). There is no build step, router or state
  library.
* **One process, many threads.** Uvicorn runs a single FastAPI app; one scheduler worker thread, one
  Torbox poller thread, a small task executor (3 workers) and demand threads for feed polls/backfills
  run behind the request handlers. SQLite in WAL mode allows readers while a writer holds the lock.
* **Push only where needed.** The browser polls `/api/tasks` every 1.5 s globally; page-specific
  polls exist for download status (Torbox), release state after a download, backfill and Calibre sync
  banners.
* **State is the database + the filesystem.** SQLite (`/app/data/mangatrax.db`) records releases,
  series, feeds, settings, tasks and logs; the configured downloads/staging directories hold the
  media files; Calibre's `metadata.db` is read but never written.
* **Safety gates over eagerness.** Automated writes go through status precedence (a feed poll can
  never resurrect a user-ignored release), strict volume selection refuses rather than
  over-downloads, and destructive maintenance endpoints require explicit ids and `confirm:true`.

### 1.4 Branding vs. integration reality

The header badge says "Bundles & qBit" and the subtitle "Calibre & BookOrbit Monitor". **BookOrbit is
branding only**: there is no BookOrbit client, API, database or configuration in the codebase
(verified by repository-wide search). The only library integration is Calibre's `metadata.db`
(read-only) plus the staging/output folders consumed by whatever reads those files downstream.

### 1.5 Explicit non-goals / absent capabilities

* No manga reader, viewer, or web reader; CBZ files are metadata-tagged, not displayed.
* No multi-user accounts, roles, per-user libraries, or sharing (auth exists to gate access, and
  `users` is an audit-attribution table only — OPS-023, Part 4 R-9).
* No light theme (`html.dark` only), no localisation, no favicon route.
* No offline-capable frontend: Tailwind and Lucide are loaded from CDNs (UI-023/G-49).
* No backup/restore, schema version or VACUUM tooling (OPS-094/Part 4 R-8).
* No websockets/SSE; polling only.

---

## 2. System architecture

### 2.1 Component view

```
                          ┌──────────────────────────────────────────────────────────┐
   Browser (desktop/mobile)│  Jinja2 pages + inline JS + Tailwind/Lucide CDN          │
                          └───────────────┬──────────────────────────────────────────┘
                                          │ HTTP (session cookie in oidc mode; per-request
                                          │ Basic credentials in basic mode; open in none)
                          ┌───────────────▼──────────────────────────────────────────┐
                          │ FastAPI app (backend/app/main.py)                        │
                          │  middleware: auth gate, CSRF origin, security headers    │
                          │  125 routes: HTML pages, JSON APIs, /health              │
                          │  Jinja2Templates + custom filters (localtime, safe_url)  │
                          └───┬───────────────┬──────────────────┬───────────────────┘
                              │               │                  │
              ┌───────────────▼────┐  ┌───────▼────────┐  ┌──────▼─────────────────┐
              │ MangaDatabase      │  │ MangaScheduler │  │ Background task exec.  │
              │ SQLite WAL         │  │ worker thread  │  │ (3 threads) + Torbox   │
              │ 13 tables          │  │ + Torbox poller│  │ poller thread          │
              └─────────┬──────────┘  └───┬────────┬───┘  └────────────────────────┘
                        │                 │        │
                    ┌───▼─────────────────▼──┐  ┌──▼───────────────────────────────┐
                    │ Calibre metadata.db    │  │ Filesystem: downloads / staging  │
                    │ (read-only, zero-lock) │  │ / data (DB, logs, covers)        │
                    └────────────────────────┘  └──────────────────────────────────┘
                        │                 │
        ┌───────────────▼──┐   ┌──────────▼─────────────┐   ┌──────────────────────┐
        │ qBittorrent Web  │   │ Torbox API (queue +    │   │ Feed providers,      │
        │ API (engine A)   │   │ requestdl + polling)   │   │ AniList, MangaUpdates│
        └──────────────────┘   └────────────────────────┘   │ webhooks (Discord)   │
                                                             └──────────────────────┘
```

### 2.2 Request lifecycle

1. Uvicorn accepts the request; the ASGI middleware in `main.py:234-277` runs (auth gate unless the
   request is `/auth/*`, `/static/*`, `/favicon.ico`, `/health` or `/healthz`; CSRF origin check on
   unsafe methods only when auth is enabled **and** `MANGATRAX_PUBLIC_URL`/`MANGATRAX_CSRF_PROTECT`
   apply; security headers).
2. The route handler runs. HTML views load their context synchronously (settings, dashboard stats,
   Calibre summary cache) and render a Jinja template. Blocking DB/file work in JSON routes is
   wrapped in `asyncio.to_thread`.
3. The HTML response is one self-contained document: the shell (base.html) plus page content plus
   inline JS. The browser then issues the page's own fetches (rows/cards/status) and starts global
   polling (`/api/tasks` every 1.5 s, task-panel progress, Calibre banner, backfill banner).
4. Actions are `fetch()` POSTs to JSON routes; feedback is a toast, an in-place DOM patch, and often
   a `location.reload()` (see Part 5 §5.5 for the exact per-action contract).

### 2.3 Process and threading model

| Thread | Cadence / trigger | Responsibility |
|---|---|---|
| Uvicorn main thread | per request | HTTP handling; `asyncio.to_thread` for blocking route work |
| Scheduler worker (`MangaTraxScheduler`) | 10 s tick, jobs at their own intervals | Initial Calibre sync (first action), Calibre sync (15 min default), feed poll (60 min), complete-series expansion + reconcile (300 s), tag/route (300 s), stale-file reconcile (300 s), manual-pickup sweep (300 s), inventory+route (45 s), active-download filter (30 s), pending-dedupe sweep (60 s), activity prune (first tick, then ≤6 h) |
| Torbox poller (`MangaTraxTorboxPoller`) | busy: `torbox_poll_seconds` (5 s default); idle: 15–30 s | Poll Torbox queue, drive fetch → routing → completion |
| Task executor | on demand, max 3 | Persisted tasks (`background_tasks`) with `TaskReporter` progress |
| Feed-check thread | manual "Poll Feeds" click | One-shot feed poll without blocking the event loop |
| Backfill thread | manual start | Polite Nyaa historical crawl with progress in `app_settings` |
| Torbox fetch executor | on demand, bounded by `torbox_max_concurrent` | Local file downloads from Torbox |

**Shielding.** The scheduler loop wraps *all* jobs of a tick in one `try/except`; a job exception is
logged and aborts the remainder of that tick's jobs, but the loop continues (OPS-061). Individual job
bodies add their own isolation only where noted.

Locks and single-flight guards are documented in OPS-3.4.1; the ones a maintainer must respect are the
Calibre-sync lock, feed-check lock, Torbox poll/fetch locks, task maps lock, grouped-release cache,
and the 1 s downloader-status cache.

### 2.4 Persistence model

* SQLite database at `DATA_DIR/mangatrax.db`, **WAL mode**; every database method opens a new
  connection (`timeout=10 s`, `row_factory=sqlite3.Row`).
* **13 tables**, created and migrated in `init_db()` with `CREATE TABLE IF NOT EXISTS` +
  `PRAGMA table_info`-guarded `ALTER TABLE` upgrades, then one-shot data passes. There is no
  migration version table; the schema work is idempotent but not one atomic transaction (OPS-087).
* **No foreign keys.** Relationships are application-level; `releases.series_name` is the join key to
  `series_tracking.name` and `series_blacklist.name`, compared case-insensitively after trimming.
* Files on disk are authoritative for media; the DB stores the expected state
  (`download_files_json`, `downloaded_at`, `routed_at`) and reconciliation repairs drift.

### 2.5 External dependencies

| Dependency | Direction | Used for | Failure behaviour |
|---|---|---|---|
| Calibre `metadata.db` | read-only | Series, authors, tags, series indices, book counts | Degraded: "Calibre DB Not Found" badge; library features show cached/empty data |
| qBittorrent Web API | read/write | Torrent add, file priority selection, queue control | Connection test + configuration panel; status falls back to DB rows |
| Torbox API | read/write | Cloud torrent queue + `requestdl` file fetch | Dedicated poller, manual-pickup hold, retry/error columns |
| Feed providers | fetch | Nyaa RSS/HTML, arbitrary RSS/HTML feeds | Feed health (`last_status`, `last_error`), per-feed continue |
| AniList / MangaUpdates | fetch | Display titles, covers, metadata tags | Cache-first; failures degrade to Calibre/user titles |
| Discord / generic webhook | outbound | Notifications (new request, acquired, feed scan, downloads processed) | Swallowed after logging; per-channel test endpoint |
| Tailwind / Lucide CDNs | browser fetch | All styling and icons | Unstyled page, no in-app diagnostic (G-49) |

### 2.6 Reliability and degraded-mode patterns

* `/health` never 5xxs on probe failures; each block is independently shielded (OPS-004).
* Calibre sync, feed poll, backfill and refresh isolate phases with per-phase try/except and persist
  progress (`app_settings.backfill_progress`, `background_tasks`/`TaskReporter`).
* Caches: grouped releases 5 s per process; downloader status 1 s; staging-ownership map 60 s;
  cover cache on disk; AniList rows persisted in `manga_metadata_cache`.
* Reconciliation exists precisely because side effects can be lost: stale-file reconcile, ownership
  reconcile every 5 min, complete-series expansion, release-status resolution on Calibre sync.
* Failures that need a human are persisted (`download_failures`, `torbox_manual_pickups`) rather than
  dropped.

---

## 3. Data model at a glance

### 3.1 Tables

| Table | Purpose | Key columns | Owned by |
|---|---|---|---|
| `series_tracking` | Tracked library rows (one per series name) | `calibre_series_id`, `name` (stable key, UNIQUE), `authors`, `publisher`, `tags`, `book_count`, `latest_index`, `is_monitored`, `website_url`, `owned_volumes_json`, `tags_json` | Series |
| `series_blacklist` | Blacklisted names | `name` UNIQUE, `reason` | Releases/Series |
| `releases` | Every ingested release | `guid` UNIQUE, `title`, `series_name`, `volumes_json`, `missing_vols_json`, `status`, `download_status`, `media_type`, `is_light_novel`, `download_*`, `published_at` | Releases (with Downloads columns) |
| `prioritized_groups` | User-defined release-group priorities | `name` UNIQUE, `notes`, `priority` | Releases |
| `feeds` | Configured feeds | `name`, `feed_type`, `url`, `config_json`, `enabled`, `auto_download`, `check_interval_minutes`, `last_status` | Releases |
| `activity_logs` | Audit trail | `event_type`, `message`, `details`, `actor`, `created_at` | Platform |
| `users` | OIDC/basic JIT users (audit attribution only) | `subject` UNIQUE, `username`, `email`, `display_name`, `auth_source`, `first_seen`, `last_seen` | Platform |
| `app_settings` | All runtime configuration + progress blobs | `key` PK, `value` | Platform |
| `manga_metadata_cache` | AniList/MangaUpdates cache | `search_term` PK, `canonical_title`, `romaji_title`, `cover_url`, `total_volumes`, `source` | Series |
| `series_alias_overrides` | Manual series-name aliases | `source_norm` UNIQUE, `source_series`, `target_name`, `target_calibre_series_id` | Series |
| `background_tasks` | Persisted task progress | `kind`, `title`, `scope`, `status`, `progress_pct`, `items_done/total`, `result_json` | Platform |
| `torbox_manual_pickups` | Held torrents awaiting mapping | `release_id`, `torrent_id`, `title`, `series_name`, `files_json`, `wanted_json`, `status`, `expires_at` | Downloads |
| `download_failures` | Files that could not be routed/tagged | `file_relative` UNIQUE, `reason`, `detected_type`, `release_id`, `status` | Downloads |

### 3.2 Core invariants

1. **Stable matching key.** `series_tracking.name` / `releases.series_name` is the identity used for
   matching, grouping, tagging and status. Display titles (AniList canonical/romaji) are resolved at
   render time and must never be written into the matching key (SER-045…SER-047, SER-067).
2. **Status precedence.** Automated ingestion/reconcile writes go through
   `resolve_status(existing, incoming)`: blank incoming never clears; `IGNORED`/`BLACKLISTED` (rank 2)
   are absolute vetoes; an incoming `IN_CALIBRE` always wins and a stored `IN_CALIBRE` is never
   downgraded to `IN_STAGING`; otherwise the higher rank wins. `REQUESTED` and `IN_CALIBRE` share
   rank 1, so an equal-rank incoming `REQUESTED` **can** replace a stored `IN_CALIBRE` (REL-018; `database.py:272-307`). User/support paths bypass precedence deliberately.
3. **Ownership = Calibre-owned ∪ staged volumes**, bundle-expanded per book, with a 60 s cached
   staged-ownership map injected into the Calibre matcher (SER-093, DL-078).
4. **Wanted set authority.** A persisted `releases.download_wanted_json` overrides
   `missing_vols_json`, which overrides a fresh matcher computation; correction paths must write it
   (DL-082).
5. **Media typing is additive.** `media_type` typing never writes `status` and never deletes rows;
   only `unknown` rows are re-typed; tracked views exclude non-manga types, and a separate
   light-novel exclusion is applied in addition (REL-034, REL-033, OPS-091).
6. **Name joins, not ids.** Deleting/renaming a Calibre series does not update trackers or releases;
   audited repair paths rewrite attribution explicitly (Part 4 §4.1, Part 2 Q4).
7. **Task kinds are a UI contract.** A button with `data-task-kind` is disabled while a matching
   active task exists; the backend must emit that kind for the binding to work (Part 4 §6.1, UI-012,
   UI-137). `downloads:process` is declared but never emitted (UI-094/UI-137).
8. **`NEW` is a real stored status.** Feed ingestion/matcher can persist `NEW`; only the user/API
   status endpoint excludes it (REL-018/REL-020, Part 4 §4.4). Do not write it by hand where
   `DISCOVERY` is intended.

---

## 4. End-to-end workflows

The following traces describe the actual code paths. Domain parts contain the full detail; this
section is the map.

### 4.1 First run

1. Container starts; `entrypoint.sh` remaps `appuser` to `PUID/PGID`, `chown`s only offenders, drops
   root via `gosu` (OPS-002).
2. `app.main` imports and constructs `MangaDatabase` (creates/migrates the DB, seeds settings, runs
   one-shot filters and startup backfills), `MangaScheduler`, and `FastAPI` (OPS-006, Part 4 R-20).
3. Lifespan: storage health probe + `scheduler.start()`; the initial Calibre sync is deferred to the
   worker thread so the UI is reachable immediately.
4. Browser opens `/`: dashboard shows "Calibre DB Not Found" until a valid path is configured;
   Settings → Calibre path + downloader + feeds are the three setup steps (G-50).
5. Calibre sync scans `metadata.db`, upserts `series_tracking`, auto-links obvious matches, resolves
   REQUESTED statuses and reconciles owned volumes.

### 4.2 Track a series → acquire a volume (the primary flow)

1. **Trust Calibre.** `sync_calibre_library` reads `metadata.db` (`scanner.py`), producing series
   names, authors, publishers, tags, `book_count`, `latest_index` and per-book series indices.
2. **Reconcile identity.** Startup/key repair maps legacy AniList-renamed rows back to the Calibre
   name; alias overrides win; auto-link/merge links trackers to Calibre series (exact unique first,
   fuzzy) (SER-032, SER-037, SER-045…SER-047).
3. **Compute ownership.** Calibre books ∪ staged files → owned volumes; missing = wanted volumes not
   owned; `resolve_status` applies `IN_CALIBRE` when complete (SER-033, SER-093).
4. **Ingest releases.** Feed poll (manual or every `feed_check_interval_minutes`) fetches enabled
   feeds through providers, parses titles (`parser.py`), classifies media type, discards
   chapter-only/LN/JPEG-XL rows, **stores** releases of blacklisted series with status `BLACKLISTED`,
   and **upserts duplicates by `guid`** rather than dropping them; status is resolved by precedence
   (REL-023…REL-035, REL-016).
5. **Match.** Releases get `series_name` via the candidate ladder (exact normalized name, alias
   overrides, fuzzy guards, spinoff scoping); tracked series' releases become `REQUESTED` when volumes
   are missing; untracked ones become `DISCOVERY` (REL-028…REL-035, SER-045…SER-047).
6. **Decide.** `/releases` shows grouped/flat wanted rows; `/prioritized` promotes prioritized
   release groups; auto-download (when enabled) picks eligible `REQUESTED` rows on the poll, deduped
   against in-flight/staged/owned volumes.
7. **Download.** The configured engine is used: qBittorrent `add_torrent` (with selective file
   priorities where possible) or Torbox submission → poller → `requestdl` fetch of selected volumes.
   `download_status` moves IDLE→DOWNLOADING→COMPLETED/FAILED (plus PENDING_DEDUPE/MANUAL_PICKUP
   parked states); `download_wanted_json` records the selection (DL-020…DL-050, OPS-097).
8. **Stage.** Completed raw files are scanned; matched CBZ → tag with ComicInfo (series, volume,
   title, authors, publisher, tags) → move to flat staging with collision policy; unmatched files
   wait in Awaiting Series / Manual Pickup / Failed Documents (DL-060…DL-078).
9. **Acquire.** The library watcher (human/Calibre) imports staged files; the next Calibre sync sees
   them, ownership grows (`IN_STAGING` is written by this ownership reconciliation, not by the
   downloader), `IN_CALIBRE` is resolved and the wanted list shrinks (SER-033).
10. **Notify.** New request / acquired / feed-scan / downloads-processed webhooks fire when
    configured (OPS-085/086).

### 4.3 Release status lifecycle

```
DISCOVERY ──track──▶ REQUESTED ──download completes (download_status=COMPLETED)──▶
        (staging/ownership reconcile: all wanted volumes present) ──▶ IN_STAGING
        ──Calibre import + sync──▶ IN_CALIBRE ──(equal-rank REQUESTED reconcile can restore)──▶ REQUESTED

IGNORED ──unignore (user)──▶ REQUESTED
BLACKLISTED ◀──blacklist series (user)    REQUESTED ──ignore (user)──▶ IGNORED
feed re-polls: precedence prevents automated regression to DISCOVERY/NEW and never lifts a veto
```

* Automated **ingestion/reconcile** writes use precedence; explicit user endpoints and
  support/repair actions (blacklist, ignore/unignore, status, LN cleanup, volume-range repair) write
  their target status deliberately.
* `IN_CALIBRE` is terminal against `IN_STAGING` and wins incoming ties against automation except for
  the equal-rank `REQUESTED` case above; a human can always change any status.

### 4.4 Download engines

* **qBittorrent** (engine A): HTTP Web API; test/configure from Settings or the Downloads page.
  Every release submission hard-codes category `manga` and tag `mangatrax`; the `qbit_category`
  setting only filters the live queue listing and `qbit_tags` is stored but never applied (DL-003,
  DL-083). Selective volume download works by setting `filePrio=0/1` on multi-file torrents where the
  file list is available.
* **Torbox** (engine B): submit by magnet/link, poll queue (adaptive 5–30 s), and for ready torrents
  request each wanted file via `requestdl`, gated on `download_present` (not `download_state`);
  strict selection refuses unmappable files and converts them into Manual Pickup holds; files are
  fetched in parallel (bounded), then routed (DL-014…DL-049).
* `download_status` and `download_wanted_json` are the cross-engine contract; `torbox_progress_json`
  carries per-file progress.

### 4.5 Manual Pickup

1. Automation holds a torrent when strict selection cannot map files (`pending`, `expires_at`).
2. `/downloads/manual-pickup` lists held torrents; the user corrects per-file volume mappings and the
   wanted volume list, then fetches selected/all files. There is **no control that edits the pickup's
   series**; the optional "Edit series" action only opens the shared Calibre matcher, which re-keys
   releases via the awaiting-series match flow (DL Q1, UI-105).
3. Saving volumes writes `wanted_json` and the release's `download_wanted_json` so dedupe/status
   computation agrees; fetch triggers the Torbox fetcher, then routing completes the release.
4. Cap/expiry sweep runs every 300 s (DL-034…DL-041).

### 4.6 Discovery and prioritized groups

* Discovery = releases whose series is not monitored/tracked and not blacklisted (with
  `IN_CALIBRE/IN_STAGING/BLACKLISTED/IGNORED` excluded). Users can Track (creates tracker/monitors) or
  Blacklist; tag include/exclude filtering and relevance sorting are server-side (REL-011…REL-017).
* Prioritized: set membership against user-defined release-group names; `/prioritized` lists only
  those, weighting matched groups; used for manual preference and flat/grouped views (REL-007…REL-010,
  REL-044).
* Both are read models over `releases`; no separate tables.

### 4.7 Historical backfill

Start (Discovery modal or shared modal): page Nyaa releases backwards from `start_page`, up to
`backfill_max_pages`, with `backfill_delay_seconds` between pages. Progress is stored in
`app_settings.backfill_progress`; a global banner across all pages can stop it. Per-series refresh is
a 3-phase job (metadata → Calibre link → Nyaa search) sharing the same global backfill flag
(REL-036/037, SER-056…SER-065).

### 4.8 Maintenance and repair

| Flow | Endpoint(s) | What it repairs |
|---|---|---|
| Series key/attribution repair | GET/POST maintenance series-attribution | Releases re-keyed under wrong series names |
| False-series matches | audit/repair false-series | Fuzzy matches that are not real series (repair re-keys selected ids to the title-parsed series; OPS-079) |
| Suspicious volume ranges | GET + repair-volume-ranges | Volume values outside plausible range |
| Light-novel cleanup | audit + cleanup (confirm) | LN releases hidden/deleted per policy |
| Release cache clear | GET/POST clear-releases | Grouped-release caches and stale rows |
| Series aliases | GET/DELETE | Manual alias overrides |
| CBZ series audit | `/api/tagger/audit`, `/api/tagger/repair-series` | Wrong `<Series>` tags inside CBZ files |
| Staging duplicates | GET/POST staging-duplicates/purge | Duplicate files in staging |
| Raw inventory cleanup | audit/clear/delete | Duplicate/already-present raw files |
| Failed documents | scan/retry/convert/delete | Unroutable files, archive→CBZ conversion |
| Logs/db maintenance | prune logs, reset all data | Log retention and destructive reset (which re-seeds a different group set; OPS-098) |

All audits run as recorded tasks where they can be long; repair endpoints require explicit id lists
(never "all") and destructive clears require `confirm:true`.

### 4.9 Authentication and operator flow

* `MANGATRAX_AUTH_MODE=none` (default in compose): no authentication, no CSRF — the operator is
  expected to restrict network access (Part 4 R-1).
* `basic`: env-configured user/pass re-validated on every request from the `Authorization` header
  (browser credential prompt); **no session cookie** is set. Unauthenticated requests (HTML and API)
  receive 401 with a Basic challenge (OPS-011, OPS-012).
* `oidc` (PocketID/Authlib): login → callback → HMAC-signed session cookie; JIT user row; actor used
  for audit. Unauthenticated HTML redirects to `/auth/login?next=…`; `/api/*` gets JSON 401
  (OPS-011/017, UI-020).
* Middleware exemptions: `/auth/*`, `/static/*`, `/favicon.ico`, `/health`, `/healthz`.
* No roles: any authenticated principal can use every endpoint (Part 4 R-9).

---

## 5. Cross-cutting rules a maintainer must not break

1. **Never write a display title into the matching key** (`series_tracking.name`,
   `releases.series_name`); use repair paths to change keys.
2. **Respect `resolve_status` semantics in ingestion/reconcile**, including its special cases (incoming
   `IN_CALIBRE` wins; no `IN_CALIBRE → IN_STAGING` downgrade; equal-rank `REQUESTED` can replace
   `IN_CALIBRE`); user/support actions may write target statuses deliberately.
3. **Treat `NEW` as a valid stored status**; user/API status endpoints must not set it. Prefer
   `DISCOVERY` for non-tracked rows.
4. **Always treat staging as owned** when computing wanted/missing/status; invalidate the ownership
   cache on staging mutations.
5. **Always write `download_wanted_json`** when correcting a wanted selection; keep it consistent
   with `missing_vols_json`.
6. **Automation must be idempotent and veto-aware**: re-polls happen constantly, and user vetoes are
   absolute.
7. **Respect the single-flight locks** (Calibre sync, feed check, Torbox poll/fetch, task maps);
   duplicate long jobs are worse than skipped ones.
8. **Task kinds are a public UI contract**; the backend must emit the kind a button declares, and
   changing a kind requires updating the buttons/tests.
9. **Route/HTML contracts are test-pinned.** The UI suite asserts many ids, strings, nav structure
   and behaviours as source text (see Part 5 §8); any redesign must update the tests in the same
   change, deliberately.
10. **Security helpers are centralised** (`security.validate_public_url`, `is_safe_href`,
    `safe_url_filter`, `validate_directory_setting`, `ensure_path_within_roots`, `escape_like`);
    never construct an outbound URL or a filesystem path without them.
11. **The DB is not transactional across methods.** Multi-step operations must be idempotent and
    order-tolerant; reconciliation is the safety net.
12. **`qbit_category`/`qbit_tags` do not affect submitted torrents** (DL-083); do not rely on them in
    new code or document them as applied.

---

## 6. Page and route surface (summary)

| Group | Routes | Owning part |
|---|---|---|
| HTML pages | `/`, `/series`, `/releases`, `/prioritized`, `/discovery`, `/feeds`, `/downloads`, `/downloads/manual-pickup`, `/logs`, `/settings` | 5 (UI) + domain parts |
| Auth/health | `/auth/login`, `/auth/callback`, `/auth/logout`, `/health` | 4 (OPS) |
| Releases/prioritized/discovery/feeds JSON | 30 endpoints | 1 (REL) |
| Series/Calibre/metadata/covers/search JSON | 24 endpoints | 2 (SER) |
| Downloads/downloaders/staging/tagger/pickup JSON | 42 endpoints | 3 (DL) |
| Settings/tasks/logs/diagnostics/maintenance JSON | 14 endpoints | 4 (OPS) |
| Debug | 1 endpoint (403 unless `MANGATRAX_DEBUG_ACTIONS=1`) | 4 (OPS) |

Totals: 10 HTML + 3 auth + 1 health + 30 + 24 + 42 + 14 + 1 = **125 routes**, of which **111** are
JSON (`/api/*`). The per-domain counts are by owning surface; several endpoints are shared/consumed
across domains, so the rows are an ownership partition, not a behavioural one.

Exact method/path/params/response for each route are in the domain parts (§3 endpoint references) and
the complete route→template→handler map is in Part 5 §2. The Feature Registry is the canonical list
of behaviours; a route without a registry claim is either infrastructure (static/health) or a
documented dead surface (e.g. `/api/downloader/queue` — DL-084).

---

## 7. Known limitations and open-question index

The domain parts contain 70+ evidence-backed open questions and defects (REL §8: 14; SER §7: 19;
DL §8: 13; OPS §8: 26; UI §9: 20 + 50 usability gaps G-01…G-50). They are **not** contradictions of the
specification; they describe where the specification and the product intent disagree. The most
consequential, carried into the UI-enhancement phase, are:

| ID | Issue | Impact |
|---|---|---|
| R-1 | Default deployment has no auth and CSRF is inactive | Any host on the network can administer the app |
| R-2/G-41 | qBittorrent password (and webhook URLs) echoed to HTML | Credential exposure |
| G-01 | Four fixed bottom-right overlays collide on mobile | Primary actions obscured |
| G-02/G-03 | Manual Pickup has no active state; global actions have no global entry point | Discoverability |
| G-12/G-14/G-21/G-25 | Filters/search/sort reset across navigation and pagination | Lost work, misleading counts |
| G-28/G-34/G-45 | Downloads is one 2100-line page; tables are desktop-only | Mobile usability |
| G-38 | Logs show only 150 rows with client-only search | Debugging history impossible |
| G-39 | Settings saves ~45 fields at once, no discard/leave warning | Risk of unintended config churn |
| G-49 | CDN-only styling; no offline fallback | Air-gapped deployments look broken |
| G-50 | No first-run onboarding | New users cannot find setup steps |
| REL Q6 | Per-feed intervals are displayed but never honoured | Feed polling cadence is not what UI says |
| SER Q2 | Manual Calibre link can blank owned volumes until next sync | Temporary false "missing" |
| SER Q19 | False-series audit suggestion can differ from the repair target | Wrong series moved |
| DL Q1 | Manual Pickup "Edit series" does not edit the pickup | Action appears to no-op |
| DL Q8 | Staging group volumes can be wrong for ComicInfo-only volumes | Wrong volume labels |
| OPS R-6 | `background_tasks` grows without bound | Storage growth; no retention |

The full lists, with file:line evidence, are the authoritative record and should be consulted before
changing any related area.

---

## 8. Revision history

| Version | Date | Change |
|---|---|---|
| 0.1–0.5 | 2026-11-08 | Five independent domain drafts authored by the expert panel |
| 0.9 | 2026-11-08 | Merged draft for consensus review (Parts 0–5 + 445-claim registry) |
| 0.10 | 2026-11-08 | Round-1 corrections: 38 reworded claims, 12 new claims (457 total), prose fixes, Part 0 rewritten |
| 1.0 | 2026-11-08 | Final after unanimous panel sign-off; archived copy written |

> **Consensus note.** Version 1.0 is only valid together with `DESIGN_CONSENSUS_LOG.md`, which
> records each round, each disagreement, the resolution, and the five signatures. The archived copy
> is `docs/archive/v1.0/`; the working copy is this `docs/` tree.
