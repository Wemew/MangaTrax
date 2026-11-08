# MangaTrax Application Design Specification — v0.11 (DRAFT FOR PANEL CONSENSUS)

> **This is a review draft.** It merges Part 0 (system overview and cross-cutting workings) with the
> five independently authored domain specifications. The accompanying `FEATURE_REGISTRY_v0.11.md`
> contains all 457 behaviour claims, one line each. The expert panel votes AGREE / DISAGREE on every
> claim and on the overview before this can become v1.0. See `DESIGN_CONSENSUS_LOG.md` for the
> process and current round.
>
> Code base: commit `86a7bd8` (`fix/light-novel-release-filtering`). All paths are repo-relative and
> all line numbers refer to that commit. This draft incorporates every round-1 and round-2 correction
> (`CORRECTIONS_ROUND1.md`).
>
> Review assignments, round 2 (each part is read by two experts who did not author it):
> * Part 1 (REL): expert-series, expert-platform
> * Part 2 (SER): expert-releases, expert-downloads
> * Part 3 (DL): expert-releases, expert-ui
> * Part 4 (OPS): expert-series, expert-ui
> * Part 5 (UI): expert-downloads, expert-platform
> * Everyone: Part 0 + `FEATURE_REGISTRY_v0.11.md`.

---

## Table of contents

* Part 0 — System Overview, Architecture, Data Model, End-to-End Workflows & Cross-cutting Rules
* Part 1 — Releases, Feeds, Discovery & Prioritized (REL-001…REL-044)
* Part 2 — Series Tracking, Calibre & Overview (SER-001…SER-094)
* Part 3 — Downloads, Downloaders, Staging & Tagger (DL-001…DL-084)
* Part 4 — Platform, Settings, Auth, Ops & Persistence (OPS-001…OPS-098)
* Part 5 — UI, Frontend, Interaction Inventory & Usability Audit (UI-001…UI-137)
* Companion: `FEATURE_REGISTRY_v0.11.md` (all 457 claims, the voting sheet)

---

# Part 0 — System Overview, Architecture, Data Model, End-to-End Workflows & Cross-cutting Rules


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
   `docs/app_design_spec_v1.0/` with the consensus log.

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
   rank 1, so an equal-rank incoming `REQUESTED` **can** replace a stored `IN_CALIBRE` (REL-018, SER-086). User/support paths bypass precedence deliberately.
3. **Ownership = Calibre-owned ∪ staged volumes**, bundle-expanded per book, with a 60 s cached
   staged-ownership map injected into the Calibre matcher (SER-093, DL-078).
4. **Wanted set authority.** A persisted `releases.download_wanted_json` overrides
   `missing_vols_json`, which overrides a fresh matcher computation; correction paths must write it
   (DL-082).
5. **Media typing is additive.** `media_type` typing never writes `status` and never deletes rows;
   only `unknown` rows are re-typed; tracked views exclude non-manga types, and a separate
   light-novel exclusion is applied in addition (REL-034, REL-033, OPS-091).
6. **Name joins, not ids.** Deleting/renaming a Calibre series does not update trackers or releases;
   audited repair paths rewrite attribution explicitly (Part 4 §4.1, OPS-089, Part 2 Q4).
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
> is `docs/app_design_spec_v1.0/`; the working copy is this `docs/` tree.


---


# Part 1 — Releases, Feeds, Discovery & Prioritized


# MangaTrax Domain Spec 01 — Releases, Feeds, Discovery & Prioritized (REL-*)

**Scope (3 lines).**
1. In scope: the four HTML pages `/releases`, `/prioritized`, `/discovery`, `/feeds`, every JSON endpoint they (or their JS) call, and the machinery behind them — feed providers, Nyaa crawler, parser/detection filters, status precedence, prioritized-group matching, caches and backfill.
2. Evidence base: the real implementation (`backend/app/*`, `backend/app/providers/*`, `backend/app/templates/*`, `tests/*`) rather than `PROJECT.md`, citing `path:line` for every non-obvious claim; behaviors that are broken/dead are marked PARTIAL/VESTIGIAL rather than smoothed over.
3. Out of scope except as cross-domain contracts: deep UI/UX auditing (EXPERT-5), Calibre scanning internals (Series domain), the downloader/Torbox pipeline (Downloads domain), and auth/OIDC internals (Platform domain).

Terminology used throughout: **stable matching key** = `releases.series_name` / `series_tracking.name` (Calibre name or user-typed name, used for matching/grouping/tagging); **display title** = cosmetic AniList canonical/English title resolved at render time; **wishlist** = rows with `status='REQUESTED'`; **prioritized group** = a row in `prioritized_groups` matched by *set membership* against a release's group set; **tracked view** = the filtered release list defined by `tracked_media_clause()` + light-novel exclusion.

---

## 2. Feature inventory

Lifecycle legend: **ACTIVE** = implemented and reachable from the UI/API with working behavior; **PARTIAL** = implemented but one documented sub-behavior is degraded, unreachable, or API-only; **VESTIGIAL** = data/columns/endpoints exist and are surfaced, but nothing consumes them (or they are provably inert). Evidence is in the "Implementation" column and expanded in §3.

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|----|---------|-----------|----------------------|----------------------------|---------|-------------|
| REL-001 | `/releases` page shell, view switch (Group by Series / Flat), status filter pills, page-level sort param | ACTIVE | Header, 7 status pills (All/Wanted/In Calibre/In staging/Discovery/Completed/Ignored), search box, 3-way sort select; the shell renders with a skeleton and lazy-loads rows via JS | `backend/app/main.py:1182-1207`, `backend/app/templates/releases.html:1-186` | GET `/releases?status=&view=&sort=` | none (stateless render); `sort` validated to `updated\|post_date\|series` |
| REL-002 | Grouped-by-series model + series cards (volume coverage grid, Calibre ownership, status/priority aggregation) | ACTIVE | One card per canonical series with cover, in-Calibre/total-volumes badges, available-volume pills coloured owned vs missing, release accordion | `backend/app/database.py:3959-4544`, `backend/app/templates/partials/series_card_chunk.html:1-290` | `GET /api/releases/cards`, `/api/prioritized/cards` | read-only; computed in memory (write only via maintenance path `database.py:3989-4002`) |
| REL-003 | Flat releases table ("All Releases") | ACTIVE | Table of release rows (cover, title+posted date, series+volumes, status/missing, feed, actions) | `backend/app/main.py:1434-1468`, `backend/app/templates/partials/flat_release_row_chunk.html:1-205` | `GET /api/releases/flat_rows` | none |
| REL-004 | Explicit "Load More" pagination, per-series 10-release cap, "Show all N releases" expander, sticky load-more, showing-counters | ACTIVE | 8 series cards (or 50 flat rows) per batch; remaining counter; per-card expander loads further pages of that series | `backend/app/main.py:1301`, `:1369-1375`, `:1378-1501`; `releases.html:120-184`, `:188-489` | clicks/`DOMContentLoaded` → JS fetch | none |
| REL-005 | Releases search (debounced, server-side on cards/flat; client-side filtering of already-loaded DOM) | ACTIVE | Typing filters after 300 ms; server returns matching groups/rows; already-rendered cards are also hidden client-side | `releases.html:70-81`, `:267-275`, `:491-541`; `database.py:2904-2913`, `:2993-2999`, `:4528-4540` | input event | none |
| REL-006 | Releases sort modes (`updated`, `post_date`, `series`) | ACTIVE | Sort select writes `?sort=`; both views re-fetch with `sort` | `releases.html:75-79`, `:277-289`; `main.py:1385-1388`, `:1441-1445`; `database.py:2896-2903`, `:4509-4523` | select change | none (URL query only) |
| REL-007 | `/prioritized` page (prioritized-only grouped/flat views + inline VIP group manager) | ACTIVE | Same layout as Releases but restricted to series with ≥1 release from a prioritized group; inline add/remove group pills with per-group release counts | `backend/app/main.py:1209-1232`; `backend/app/templates/prioritized.html:1-215` | GET `/prioritized` | none |
| REL-008 | Prioritized group CRUD | ACTIVE | Add group (+notes), list with counts, delete with confirm; also manageable from `/settings` | `backend/app/main.py:4112-4128`; `database.py:4556-4592`; `prioritized.html:518-556`; `settings.html:1500-1540` | POST/DELETE `/api/prioritized/groups` | `prioritized_groups` table (`database.py:627-646`) |
| REL-009 | Prioritized matching = group-set membership; priority weighting = highest-ranked matching group | ACTIVE | A joint tag `(1r0n, Ushi)` counts for every prioritized group it names; ordering puts the highest configured priority first, then the normal status/recency tier | `database.py:130-191`, `:203-218`, `:4490-4506`, `:4616-4668`; `parser.py:813-860`, `:1014-1113` | every list read | `releases.release_group`, `releases.release_groups_json`; `prioritized_groups.priority` |
| REL-010 | `/api/prioritized/flat_rows` | PARTIAL | Returns flat prioritized rows; no `search`, no `sort`, no `total` field, and `has_more` is `len==limit` | `backend/app/main.py:1503-1521`; `prioritized.html:372-428` (client-side filter only) | JS fetch | none |
| REL-011 | `/discovery` page shell, tabs, server-side pagination, page-size selector | ACTIVE | Server-rendered card grid (12/24/48/96 per page), page indicator with First/Prev/Next/Last, "Blacklisted Series" tab, badge counts | `backend/app/main.py:1571-1620`; `backend/app/templates/discovery.html:1-289` | GET `/discovery?page=&page_size=&q=` | none |
| REL-012 | Discovery dataset definition + per-series dedupe | ACTIVE | Shows plausible new series (Vol 1 / unnumbered / complete), excluding tracked-monitored, blacklisted, in-Calibre/in-staging/ignored rows, deduped to one representative release per series name | `database.py:3093-3140` (count), `:3142-3258` (rows) | discovery page read | none |
| REL-013 | Discovery tag filtering (include/exclude, any/all match) + tag autocomplete picker | PARTIAL | Tag chips filter the grid server-side; picker queries `/api/tags`. Filters are lost when paginating or using the search form (see §7) | `main.py:1577-1597`; `database.py:3074-3091`, `:1339-1407`; `discovery.html:44-90`, `:566-640` | `Apply`/`Clear` (URL params) | none |
| REL-014 | Discovery sort modes (`newest`, `post_date`, `series`, `relevance`) | PARTIAL | Sort select writes `?sort=`; `relevance` degrades to `newest` unless include-tags are present; pagination links drop `sort` | `main.py:1580-1597`; `database.py:3165-3180`; `discovery.html:62-67`, `:239-273` | select/pagination | none |
| REL-015 | Discovery "Track Series" (stable-key resolution, re-key siblings, notify) | ACTIVE | One click creates/updates `series_tracking`, flips the release(s) to `REQUESTED`, re-keys legacy rows, fires the new-request webhook | `main.py:4135-4171`; `database.py:3277-3361`; `scheduler.py:622-788`; `discovery.html:490-505` | POST `/api/discovery/track` | `series_tracking`, `releases.status/series_name` |
| REL-016 | Discovery blacklist + Blacklist management tab (manual add, list, search, restore) | ACTIVE | Blacklist a card or type any name; adding also re-keys all releases of that series to `BLACKLISTED` and unmonitors tracking; restore deletes the veto and demotes rows to `DISCOVERY` | `main.py:4095-4108`, `:4173-4179`; `database.py:2342-2393`; `discovery.html:290-376`, `:507-557` | POST/DELETE `/api/blacklist` | `series_blacklist`; `releases.status` |
| REL-017 | Discovery "Fetch" MangaUpdates info (synopsis/URL/cover, cached) | ACTIVE | Per-card Fetch button fills synopsis, MangaUpdates link and cover, cached under `mu:<name>` | `main.py:4181-4209`; `database.py:3260-3275`; `discovery.html:198-211`, `:438-468` | POST `/api/discovery/{id}/fetch-info` | `releases.description/mangaupdates_url/cover_url`; `manga_metadata_cache` (`mu:`) |
| REL-018 | Release status precedence/lifecycle model | ACTIVE | Automated re-polls/reconciles can never downgrade `REQUESTED`/`IN_CALIBRE`/`IGNORED`/`BLACKLISTED`; `IGNORED`/`BLACKLISTED` are only liftable by explicit user actions | `database.py:221-307`; `database.py:2433-2571` (upsert), `:2573-2594`, `:2596-2623`; `tests/test_status_precedence.py:184-609` | every automated write | `releases.status` |
| REL-019 | Ignore / Unignore a release (per-batch) | ACTIVE | Eye-off button sets `IGNORED` (stays visible under the Ignored filter); eye button restores it to `REQUESTED` | `main.py:4081-4093`; `templates/partials/flat_release_row_chunk.html:183-191`, `series_card_chunk.html:260-268`; `base.html:852-893` | POST `/api/releases/{id}/ignore\|unignore` | `releases.status` |
| REL-020 | `POST /api/releases/{id}/status` | PARTIAL | API-only: restricted enum `REQUESTED\|IN_CALIBRE\|IGNORED\|BLACKLISTED\|DISCOVERY`, writes verbatim (no precedence); no template in this repo calls it | `main.py:4074-4079`; `schemas.py:555-558` | API client only | `releases.status` |
| REL-021 | "Download" a release + derived download/staging/Calibre state + in-place polling | ACTIVE | Download button sends the release to the configured engine (409 if already in staging); a state chip (`Downloading 42%`, `In staging`, `In Calibre`, `Failed` …) updates by polling `/state` every 1.5 s while non-terminal | `main.py:1314-1361`, `:3730-3735`, `:3738-3824`; `releases.html:586-682`; `prioritized.html:558-620` | POST `/api/releases/{id}/download`; GET `/api/releases/{id}/state` | `releases.download_status/download_error/torbox_progress_json/downloader*` |
| REL-022 | "Different series" per-release re-key (existing Calibre series / new title / detach→Discovery) | ACTIVE | Modal searches Calibre series, or accepts a new title, or detaches; only the one release is re-keyed (no alias created) | `main.py:3839-3923`; `releases.html:684-770`; `database.py:194-200` (cache invalidation at `main.py:3906-3909`) | POST `/api/releases/{id}/different-series` | `releases.series_name/status/missing_vols_json` |
| REL-023 | `/feeds` page: list, add (modal + live "Test Feed"), enable/disable toggle, delete | ACTIVE | Table of feeds with type/enabled/URL/auto-download/interval/last-check/status; modal supports RSS or HTML scraper + interval + auto-download; "Poll Feeds" runs an async poll | `main.py:1851-1861`, `:1979-2074`; `backend/app/templates/feeds.html:1-273`; `database.py:3407-3448` | GET `/feeds`; POST/DELETE feed endpoints | `feeds` table (`database.py:649-663`) |
| REL-024 | Provider registry and provider implementations (rss, html/html_scraper, nyaa/nyaa_scraper/nyaa_manga) | ACTIVE | Feed `feed_type` chooses a provider object; unknown types fail that feed only | `backend/app/providers/registry.py:8-47`; `providers/base.py:17-104`; `providers/rss.py:34-203`; `providers/html_scraper.py:13-183`; `providers/nyaa.py:40-462` | poll / `/api/feeds/test` | `feeds.feed_type`, `feeds.config_json` |
| REL-025 | Nyaa HTML crawler: page pagination, per-page retries with backoff/jitter, `Retry-After`, ≥1 s page spacing, explicit stop reasons | ACTIVE | Crawls `{base}/?f=0&c=3_1&s=id&o=desc&p=N[&q=…]`, parses view/torrent/magnet/size/date/seeders; distinguishes end-of-results vs transient failure vs user stop vs max pages | `nyaa.py:26-38`, `:146-246`, `:248-462`; `providers/nyaa.py:65-91` | feed poll, backfills, `/api/feeds/test` | none (catalog in `last_crawl_reason/last_crawl_pages`) |
| REL-026 | Feed-poll orchestration: non-blocking manual poll, interval poll, mutual exclusion, status endpoint, persisted task, notification | ACTIVE | "Poll Feeds" returns immediately; UI polls `/api/feeds/check/status`; concurrent polls skip with `skipped=true`; interval poll runs on the worker thread | `main.py:1979-2015`; `scheduler.py:3326-3418`, `:3420-3823`; `base.html:541-592` | button click; worker loop timer | `feeds.last_checked/last_status/last_error`; `background_tasks`; scheduler in-memory status |
| REL-027 | Per-feed `check_interval_minutes` | VESTIGIAL | Stored, editable in the Add-feed modal, displayed as "60m" on `/feeds`, but **never enforced** — all enabled feeds are polled on the single global interval | DB column `database.py:658`; write `database.py:3428-3434`; display `feeds.html:85`; only consumer `scheduler.py:5189-5200` uses global `feed_check_interval_minutes` (grep: no per-feed read) | — | `feeds.check_interval_minutes` (write-only) |
| REL-028 | Ingestion hard filters: explicit chapter-only, light novel, JPEG XL | ACTIVE | Chapter updates and LN/ebook releases are discarded at provider level and again in the poll; JPEG XL is discarded and audited once per guid | `providers/rss.py:69-77`; `html_scraper.py:94-102`; `nyaa.py:358-365`; `scheduler.py:3482-3543`; `parser.py:1124-1231`, `:1258-1342` | each polled row | audit rows only; `releases.media_type`/`is_light_novel` for rows stored by older versions |
| REL-029 | Context-guarded marker-less chapter discard ("Dandadan 250" vs "Mob Psycho 100") | ACTIVE | A trailing bare number is discarded only when the remainder names a *known* series (tracked/Calibre); a real numbered series name is protected; every discard is audited with the reason | `scheduler.py:237-392`, `:3542-3543`, `:3947-3949`; setting `discard_unmarked_chapters` (`database.py:745-748`, `scheduler.py:263-266`) | each polled row | `activity_logs` (`CHAPTER_DISCARDED`) |
| REL-030 | Release title parser/detector: series key, single/bundle volumes, complete flag, groups set, authors, publisher, revision, chapter diagnostics | ACTIVE | Everything else keys off this parse; handles `Vol 1`, `v01-06`, `Vols 1, 2, and 3`, bare ranges, omnibus `(Vols 1-3)`, `ch 1-50 as v1-2`, `(f2)` revisions, multi-group tags | `parser.py:1439-1688`, `:413-672`, `:1014-1113`, `:1233-1342`; `providers/*` call `extract_manga_bundle_and_volumes` | every ingested row | `releases.volumes_json/volume_start/volume_end/is_bundle/is_complete/release_group(s)/revision/publisher` |
| REL-031 | Collected-batch volume inference (year-range title, no volume markers) | ACTIVE | `Blue Lock - Episode Nagi (2024-2026) (Digital) (Stick)` is expanded to the full volume set only when authoritative metadata reports a *finished* count for *this* series; otherwise it stays volume-less and visible | `parser.py:273-301`; `scheduler.py:394-531`, `:3563-3567`, `:4001-4004` | feed poll, series backfill | `releases.volumes_json/inferred_volumes/is_bundle` |
| REL-032 | Complete-series volume expansion + ownership reconciliation (computed on read; persisted by worker/maintenance) | ACTIVE | `(Complete)`/`(Vol.1-11 Complete)` batches get volumes from metadata; missing volumes recomputed against Calibre ∪ staging; status may move to `IN_CALIBRE`/`IN_STAGING`/`REQUESTED` | `database.py:4004-4446` (read-time), `:3989-4002` (persist path), `scheduler.py:4976-5138` (`expand_unresolved_complete_releases`), `:5166-5173`, `:5202-5209` (worker) | GET (in memory) / worker every 300 s / startup | `releases.volumes_json/missing_vols_json/is_bundle/is_complete/status` |
| REL-033 | Light-novel classification persisted + hidden from tracked views + audit/cleanup tooling | ACTIVE | LN rows are invisible in Releases/Discovery/counts/search/wishlist; Settings → Maintenance can audit and Ignore/Delete them | `database.py:536-538`, `:918-938` (legacy backfill), `:3000`, `:3133`, `:3199-3200`, `:4025-4080` (is_light_novel guard); `main.py:2471-2506`; `backend/app/ln_releases.py:17-69`; `parser.py:1144-1231` | poll/upsert; maintenance API | `releases.is_light_novel`; `app_settings.ln_release_backfill_done` |
| REL-034 | Media typing + tracked-media guard (manga/light_novel/chapter_only/novel/audiobook/jpegxl/unknown) | ACTIVE | Non-manga types are typed instead of deleted; only `manga`/`unknown` remain visible in tracked views; typing never deletes or rewrites status | `database.py:46-118`, `:1005-1164`, `:497-498` (column), `:3601-3640` (explicit purge) | one-shot startup passes + debug action | `releases.media_type`; markers `media_filter_applied`, `jpegxl_filter_applied` |
| REL-035 | Auto-download on new/transitioned `REQUESTED` rows (engine readiness, in-flight dedupe, selective volume filter, `PENDING_DEDUPE` retry sweep) | ACTIVE | A tracked series' newly missing volumes are pushed to qBittorrent/Torbox automatically; duplicates already downloading/staged are suppressed and retried later | `scheduler.py:1055-1126`, `:1248-1304`, `:3659-3780`, `:4622-4660`, `:2329-2459` (`sweep_pending_dedupe`) | feed poll / series backfill | `releases.download_status/downloader*/download_wanted_json/torrent_hash` |
| REL-036 | Polite historical backfill (`/api/backfill/start\|stop\|status`) | ACTIVE | Modal asks start page + max pages (resume hint from `last_page`); global banner shows progress on every page and can stop; a failed page aborts with `backfill_status=error`, never a fake success | `main.py:4293-4321`; `scheduler.py:4755-4910`, `:3852-3868`; `base.html:290-328`, `:645-819`; `discovery.html:19-24`, `:559-563` | POST start/stop; banner polls GET status | `app_settings.backfill_status/backfill_progress/backfill_last_page` |
| REL-037 | Series refresh + targeted Nyaa backfill (3 phases, spinoff guard) | ACTIVE | Card "Backfill" button refreshes metadata → Calibre info → reconciles → searches Nyaa for primary+alternate titles, ingesting only releases whose own identity belongs to the target | `main.py:4013-4072`; `scheduler.py:4460-4708`, `:4710-4753`; `base.html:895-927` | POST `/api/series/{id}/backfill` or `/api/series/backfill` | `background_tasks`; backfill settings keys; releases rows |
| REL-038 | Release-cache preview/clear + grouped-cache invalidation | ACTIVE | Settings → Maintenance previews how many cached rows a filter would delete and deletes them (active downloads are skipped and audited) | `main.py:2587-2628`; `database.py:1167-1246`; `settings.html:1200-1240`; `tests/test_release_cache_clear.py:53-255` | GET preview / POST clear | `releases` deletions; `activity_logs.RELEASES_CLEARED` |
| REL-039 | Nyaa/tracker base-URL mirror support (`nyaa_base_url`) | ACTIVE | Setting + per-feed `config.base_url`; repoints the built-in seeded RSS feed on save; every Nyaa URL (crawl/view/torrent/relative joins) uses the mirror | `backend/app/nyaa_source.py:39-113`; `database.py:3364-3405`; `main.py:4254-4262`; `scheduler.py:964-977`; `providers/nyaa.py:55-80` | settings save / provider construction | `app_settings.nyaa_base_url`; `feeds.url` of the stock row only |
| REL-040 | Feed-scan / new-request webhook notifications | ACTIVE | Manual polls are silent; automatic polls only notify when something actually changed (new/transition/requested/sent) | `scheduler.py:1332-1375`, `:3363-3367`, `:3670-3675` (poll notify) and `:4616-4621` (backfill notify); settings toggles `webhook_feed_scan_notifications`, `webhook_processed_notifications` (`database.py:743-744`) | auto poll / ingestion | `app_settings` webhook URLs; notifier |
| REL-041 | `GET /api/releases/grouped` (raw JSON dump of all groups) | VESTIGIAL | Returns every group with no pagination/search; no template or JS in this repo calls it | `main.py:1547-1550` | API client only | none |
| REL-042 | `GET /api/search` unified search (tracked series + AniList aliases + releases) | PARTIAL | Rich alias-aware search, but **no UI in this app consumes it** (Releases search is `/api/releases/*?search=`; Discovery has its own search) | `main.py:3498-3514`; `backend/app/search.py:72-182`; tests `tests/test_m2_features.py:694-731`; grep shows no template caller | API client only | none |

Feature count: **42** (REL-001 … REL-042).

---

## 3. How it works

### 3.1 Page shells and the lazy-load hand-off (REL-001, REL-003, REL-007, REL-011)

All four pages are server-rendered Jinja shells extended from `base.html`; only Discovery renders data server-side.

* **`/releases`** (`main.py:1182-1207`): computes only an *initial total* (`db.count_releases(status=status)` for `view=flat`, else `db.count_releases_grouped_by_series(status=status)`), normalizes `sort` to `updated\|post_date\|series`, and passes **empty** `releases`/`grouped_series` lists. Consequence: the template's server-rendered branches (`releases.html:88-90` and `:151-152`) are dead in practice — the page always paints a skeleton and the JS fetches the first batch (`releases.html:477-489`). The pre-computed total exists so the "Showing X of Y"/"N remaining" counters and the Load More button are present from first paint (`releases.html:195-200`).
* **`/prioritized`** (`main.py:1209-1232`): same pattern, but `initial_total` is 0 for the flat view (`main.py:1212-1219`) because prioritized flat pagination has no total (REL-010). It also loads `prioritized_groups` for the inline manager (`main.py:1211`).
* **`/discovery`** (`main.py:1571-1620`): fully server-rendered: clamps `page` to `total_pages` (`main.py:1590-1592`), queries the page slice, decorates each row with `tag_display` chips (limit 5, `tag_display.display_tags(..., view="discovery")`, `main.py:1598-1600`), and also loads the entire blacklist for tab 2 (`main.py:1601`).
* **`/feeds`** (`main.py:1851-1861`): renders `db.get_all_feeds()` (ordered by id, `database.py:3415-3419`) into a table; all mutations are fetched from the page's own JS.

Base template supplies the shared infrastructure used by all four pages: `showToast` (`base.html:402-424`), the global backfill banner + 2.5 s status poller (`base.html:345-365`, `:645-689`), the backfill setup modal (`base.html:290-328`, `:753-819`), the Calibre-sync banner (`base.html:333-343`, `:691-751`), `blacklistSeriesGlobal` / `ignoreReleaseGlobal` / `unignoreReleaseGlobal` / `backfillSeriesGlobal` (`base.html:821-927`), the image lightbox (`base.html:278-288`), the global Tasks panel (`base.html:389-398`), and the mobile nav (`base.html:220-273`).

Request path for a page action, typical: browser → `fetch('/api/releases/cards?...')` → FastAPI route → `db.get_releases_grouped_by_series()` → Jinja partial render inside the handler (`templates.get_template("partials/…").render({...})`) → JSON `{status, offset, count, total, has_more, html}` → DOM `insertAdjacentHTML('beforeend', html)` → `lucide.createIcons()`. Note the server returns **pre-rendered HTML fragments, not data** for every paginated list endpoint here (`main.py:1394-1404`, `:1458-1468`, `:1491-1501`, `:1512-1521`).

### 3.2 Grouped-by-series model (REL-002)

`db.get_releases_grouped_by_series()` (`database.py:3959-3987`) is a thin wrapper over `_build_grouped_releases(..., persist=False)` (`database.py:4004-4544`). Behavior, in order:

1. **Staged-ownership map**: `staged_ownership.get_staged_volumes_map(db, staging_dir)` is loaded first (failure → empty map) (`database.py:4015-4022`) — staged volumes count as *owned* alongside Calibre volumes (task-61).
2. **Cache**: key is `f"{status}_{prioritized_only}_{sort}"`; if the call is not a persist run and has no `search`, a cached full group list younger than **5.0 s** is sliced by limit/offset and returned (`database.py:4024-4031`). Search **bypasses** the cache entirely (so every keystroke after the 300 ms debounce rebuilds all groups — `database.py:4525`, `:4528-4540`). Cache is invalidated by `_invalidate_grouped_cache()` (`database.py:371-375`) which is called by every relevant writer (`upsert_release`, `update_release_status`, prioritized-group CRUD, blacklist changes, discovery track, cache clears, media re-typing, …).
3. **Row selection** (`database.py:4034-4099`): the SQL is built from `tracked_media_clause()` (`database.py:107-118`: `COALESCE(media_type,'unknown') NOT IN ('light_novel','chapter_only','novel','audiobook','jpegxl')`), `prioritized_release_clause()` when `prioritized_only` (`database.py:203-218`), plus `COALESCE(is_light_novel,0)=0`, plus — for the default (no status) and `COMPLETED` branches — `status NOT IN ('BLACKLISTED','IGNORED')` and a `series_blacklist` name exclusion. `status=COMPLETED` is a *pseudo-status*: `(is_complete = 1 OR lower(title) LIKE '%complete%')` (`database.py:4073-4082`). A real status branch uses exact `status = ?` (case-sensitive SQL compare).
4. **Per-release decoration** (`database.py:4101-4111`): `is_complete` (column OR title regex), `is_inferred`, `volumes`, `missing_volumes`, `release_groups` (set), `is_prioritized`, `prioritized_priority` (highest matching group's priority, `database.py:173-186`).
5. **Series resolution** (`database.py:4113-4277`): loads all `series_tracking` and `manga_metadata_cache` rows; builds normalized and canonical/romaji alias indexes; builds an `alias_claims` map so a legacy row stored under an AniList alias can be relabelled to its single claiming tracked stable name (ambiguous aliases never relabel, `database.py:4143-4165`). For each release it derives candidate parts (`| / // – — ;`, parenthesised parts, `split_series_candidate_parts`), finds metadata (cache row for the stable name, else a candidate with `total_volumes`), finds a tracked/Calibre match by exact normalized name → canonical bridge → candidate parts → strict substring containment (requires length ratio ≥ 0.70 and both ≥ 6 chars, `database.py:4226-4232`), and finally sets `stable_name` (Calibre match wins, else alias-recovered, else stored), `display_series_name`/`canonical_series_name` (AniList authoritative canonical title only, `database.py:4244-4252`), `romaji_name`, and group fields.
6. **Group record creation** (`database.py:4277-4308`): keyed by `normalize_text(display_title)`; carries `series_key`, `series_name` (display), `stable_name`, `romaji_name`, `raw_name`, cover (metadata → release → tracked), `in_calibre`, `is_monitored`, `calibre_series_id/book_count/latest_index`, `total_volumes`, `status` (AniList status), `synopsis`, `releases[]`, dedupe/missing sets, `is_complete`, `has_complete_release`, `is_publishing_finished` (`FINISHED|COMPLETED`), `has_wanted/has_discovery/has_in_calibre/has_in_staging/has_prioritized`, `prioritized_priority`.
7. **Complete-batch expansion + reconciliation** (`database.py:4333-4404`): a complete release with no parsed volumes is expanded to `1..total_volumes`; `missing` recomputed from Calibre owned ∪ staged; status candidate `IN_CALIBRE` (all owned in Calibre), else `IN_STAGING` (all owned only via staging), else `REQUESTED` (if in Calibre) / unchanged; then a second reconcile pass for any release with volumes against combined ownership. **Both passes are in-memory only on this GET path**; `persist=True` writes them (`database.py:4365-4377`, `:4397-4404`) and is only reached from `reconcile_release_volume_state()` (`database.py:3989-4002`), called by the worker at startup and every 300 s (`scheduler.py:5166-5173`, `:5202-5209`).
8. **In-group dedupe and supersede** (`database.py:4406-4470`): dedupe signature is `guid` else `(download_url|link|title, sorted volumes, sorted group set, revision)`; duplicate-signature rows are dropped from `releases[]` (but still contribute volumes/missing/flags). Re-release revisions are never collapsed: rows sharing `(sorted volumes, sorted group set)` are flagged `is_superseded` when a higher `revision` exists (`database.py:4449-4467`), and `group["releases"]` is finally sorted by `(is_superseded, -revision)` — which **overrides** the SQL ordering for the `updated`/`series` sorts (Python's stable sort keeps SQL order only within equal keys).
9. **Aggregates** (`database.py:4471-4488`): `all_volumes`, `missing_volumes`, `releases_count`, `wanted_count`, `prioritized_count`; a group is forced `in_calibre` if it has an `IN_CALIBRE` release or >0 owned volumes.
10. **Ordering** (`database.py:4490-4523`): tier sort `(priority, -prioritized_priority, display name)` where tier = 1 wanted+prioritized, 2 wanted, 3 prioritized, 4 monitored, 5 discovery, 6 in-Calibre, 7 rest. Then `sort=post_date` re-sorts groups by `max(published_at||created_at||updated_at)` of their releases (string compare on ISO-8601-Z values) descending and sorts each group's releases by the same key; `sort=series` re-sorts groups by display name.

The card partial renders: cover, WANTED/TRACKED ribbon, series heading + romaji, In-Calibre/total/complete/publishing badges, release count, Track (only when not in Calibre and not monitored), Backfill, Blacklist, Releases accordion (stable name is sent to all actions — `series_card_chunk.html:75-98`), volume-coverage pills (owned vs missing), synopsis, and (inside the accordion) up to 10 release rows with group tag, volume/complete badges, status and download-state chips, feed source, `different series` / Download / Ignore / open-link actions (`series_card_chunk.html:140-287`).

### 3.3 Pagination, per-card cap, "Show all" (REL-004)

* `GET /api/releases/cards` (`main.py:1378-1404`) and `/api/prioritized/cards` (`main.py:1406-1432`): params `status`, `offset ≥ 0`, `limit` (default **8**, 1…50), `search`, `sort` (whitelisted). Each group's release list is capped at **10** by `_cap_group_releases` (`main.py:1301`, `:1369-1375`), which also sets `release_total` (true count before capping) and `releases_shown`, and decorates each release with `release_state`. Response `{status, offset, count, total, has_more, html}`; `total` = number of groups after search (`db.count_releases_grouped_by_series`, `database.py:4546-4553`), so `has_more = offset + len(groups) < total`.
* `GET /api/releases/flat_rows` (`main.py:1434-1468`): default **50** (1…200); fetches a page and dedupes by `release_dedupe_signature` (`database.py:194-200`); `has_more` uses the **pre-dedupe** length (`main.py:1456`). Because `releases.guid` is `UNIQUE NOT NULL` (`database.py:452`), the dedupe set can never fire for normal rows — see §7.
* `GET /api/releases/series_releases?series_key=…&offset=&limit=` (`main.py:1471-1501`): rebuilds the full group list **with a search** (cache bypass) and finds the group whose `series_key`/`series_name` matches the requested key case-insensitively; then slices `releases[offset:offset+limit]` (default limit 10) and returns rows. Because it re-derives the group, it ignores `prioritized_only` (so "Show all" on `/prioritized` can reveal non-prioritized releases of that series) and always uses the `updated` ordering.
* Front-end state machine (`releases.html:188-475`): `seriesOffset/flatOffset`, `hasMore*`, `isFirst*Batch`, error flags; `updateLoadMoreUi` (`:221-244`) drives button label, remaining counter, "Showing X of Y", the sticky button (`:179-184`, `:246-249`), and hides the button when exhausted. `fetchNextSeriesBatch`/`fetchNextFlatBatch` (`:340-403`, `:414-473`) append HTML, advance offset by `data.count`, sync `total`, re-run lucide and card-summary recomputation, and on the first empty batch render an empty state with a "Clear filters" escape hatch (`:356-372`, `:430-442`). Error paths set `*LoadError` which turns the button into "Retry loading" (`:230`).
* Cards render a "Show all N releases" button only when `release_total > releases|length` (`series_card_chunk.html:279-286`); clicking loads further pages of that series via `/api/releases/series_releases` (`releases.html:291-311`) and removes the button when `has_more` is false or offsets reach the total.

### 3.4 Search and sort (REL-005, REL-006, REL-014)

* Input handling: `handleReleaseSearch` debounces 300 ms, resets pagination offsets, refetches whichever view is active (`releases.html:267-275`). The client also hides non-matching already-rendered cards/rows via `data-series-name`/`data-release-name` attributes and shows an `N matches` counter (`releases.html:491-541`; attributes emitted at `series_card_chunk.html:3`, `flat_release_row_chunk.html:2`).
* Server search: flat → `get_releases(..., search=)` adds a LIKE-escaped OR over `title`, `series_name`, `release_group` (`database.py:2904-2913`); grouped → `_build_grouped_releases` builds everything, then filters groups by case-insensitive substring over `series_name`, `stable_name`, `raw_name`, `romaji_name`, **and `synopsis`** (`database.py:4528-4540`), finally slicing limit/offset.
* Sort: `sort=updated` uses a status tier (`REQUESTED`, `DISCOVERY`, `IN_CALIBRE`, other) then `updated_at DESC, id DESC` (`database.py:2901-2903`, `:4068-4070`, `:4096-4098`); `post_date` uses `COALESCE(NULLIF(trim(published_at),''), created_at, updated_at) DESC` for flat rows (`database.py:2896-2898`) and the group-max re-sort described above; `series` uses `lower(COALESCE(series_name,title))` (`database.py:2899-2900`) / group display name. `published_at` is normalized to UTC ISO `YYYY-MM-DDTHH:MM:SSZ` on write (`database.py:2396-2431`), with `created_at` backfilled for legacy rows (`database.py:911-917`), and a blank re-upsert never clears it (`database.py:2493`; test `tests/test_post_date_sorting.py:19-46`).
* Discovery sort is separate (§3.6).

### 3.5 Prioritized domain (REL-007, REL-008, REL-009, REL-010, REL-041)

* **Matching is set membership.** A release stores the primary group in `release_group` and the full ordered set in `release_groups_json` (`database.py:455-456`, `:584-624`). Parser produces the set (`parser.py:1014-1113`; separators `, & + / and`, multiple brackets, Oxford-comma lists; format/publisher words are rejected as groups — `tests/test_multi_group_prioritization.py:94-128`, `:265-281`). In memory, `release_groups_of()` reads the JSON (falling back to splitting the legacy string) (`database.py:130-170`); in SQL, `prioritized_release_clause()` uses `json_each(release_groups_json)` joined to `prioritized_groups` plus a legacy whole-string fallback (`database.py:203-218`).
* **Weighting**: `release_prioritized_priority()` returns the **maximum** priority among the release's matching groups (`database.py:173-186`). Group ordering uses that maximum as the second sort key within a tier (`database.py:4490-4506`); prioritized release lists sort by it descending (`database.py:4666-4668`); group badges/counts use it (`database.py:4437-4442`, `:4475`).
* **Schema and seed** (`database.py:627-646`): `prioritized_groups(id, name UNIQUE, notes, priority DEFAULT 1, created_at)`. When the table is empty, four groups are seeded: `danke-Empire`, `1R0N`, `Lu自主规制`, `a-sloth`. (Note: `reset_all_data` seeds a *different* five-group list at `database.py:4702-4708` — see §7.)
* **CRUD** (`main.py:4112-4128`): GET returns groups with `release_count` computed by a correlated subquery over set membership + `tracked_media_clause` (`database.py:4556-4570`), ordered `priority DESC, name COLLATE NOCASE`; POST upserts by name with notes/priority (`database.py:4572-4585`; request model `schemas.py:629-639`, priority 0…100 default 1); DELETE by id. Both invalidate the grouped cache.
* **Views**: `/prioritized` grouped view calls the same grouped builder with `prioritized_only=True` (`main.py:1417-1420`), which adds the SQL membership predicate to every branch (`database.py:4040-4071`). Its flat view uses `GET /api/prioritized/flat_rows` → `db.get_prioritized_releases()` (`database.py:4616-4668`), which also applies the membership predicate, excludes `BLACKLISTED`/`IGNORED` + blacklisted series in the default branch, then post-sorts rows by `-prioritized_priority`; `has_more` is simply `len == limit` and no `total` is returned.
* **`GET /api/releases/grouped`** (`main.py:1547-1550`) dumps `get_releases_grouped_by_series()` (no params, no pagination) — unused by any client in this repo.
* **Settings** additionally renders the group list and supports add/remove (`settings.html:457-470`, `:1500-1540`), duplicating the `/prioritized` manager.

### 3.6 Discovery domain (REL-011 … REL-017)

**Dataset.** Both `count_discovery_releases` (`database.py:3093-3140`) and `get_discovery_releases` (`database.py:3142-3258`) use the same CTE predicate:

```
(r.volume_start = 1.0 OR r.volume_start IS NULL)
AND r.status NOT IN ('IN_CALIBRE','IN_STAGING','BLACKLISTED','IGNORED')
AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_blacklist))
AND (series_name IS NULL OR lower(series_name) NOT IN (SELECT lower(name) FROM series_tracking WHERE is_monitored = 1))
AND tracked_media_clause('r')
AND COALESCE(r.is_light_novel, 0) = 0
```

Rows are deduped to one representative per `lower(trim(coalesce(nullif(series_name,''), title)))` by `ROW_NUMBER()` with a preference order: has cover → has description → has MangaUpdates URL → newest `updated_at` → highest id (`database.py:3115-3136`, `:3182-3207`). `latest_index` of the choice matters because only that row's metadata is enriched. Consequences worth noting: `REQUESTED` rows remain discoverable unless the series is *monitored* (so a `REQUESTED` created via the API-only status endpoint still appears); completed-series bundles with `volume_start` NULL also qualify; unmonitored tracked series reappear.

**Pagination/count.** `count_discovery_releases` counts `rn = 1`; the page handler computes `total_pages = ceil(count/page_size)` (min 1), clamps `page` (`main.py:1590-1592`), and offsets. `page_size` default 24, bounds 6…120 (`main.py:1574-1575`); the UI offers 12/24/48/96 (`discovery.html:82-87`).

**Sort.** `get_discovery_releases(sort=…)`: `relevance` orders by `tag_match_count DESC, updated_at DESC, id DESC` but **only when include-tags were supplied** (the count column is only selected then), otherwise falls through to `newest`; `series` → `lower(coalesce(series_name,title))`; `post_date` → `COALESCE(NULLIF(trim(published_at),''), created_at, updated_at) DESC`; default/newest → `updated_at DESC, id DESC` (`database.py:3165-3180`). Sort does not change the count (`tests/test_post_date_sorting.py:83-96`).

**Tag filters.** Include/exclude lists are normalized (`tag_display.normalise_tag`, `[^a-z0-9]+ → ' '`) and matched as `tags_text LIKE '%|tag|%'` (`database.py:3074-3091`); `tags_text` is the `"|a|b|"` denormalization written by `update_release_tags*` (`database.py:1307-1327`, `tag_display.py:21-27`). `tag_match=any|all` only affects include tags. The picker is a JS combobox that queries `GET /api/tags?q=&limit=` (`discovery.html:573-640`; API `main.py:1565-1568`; query builder `database.py:1339-1407`, which unions `manga_metadata_cache` with denormalized `releases.tags_json/tags_text` and `series_tracking` tags).

**Row enrichment** (`database.py:3210-3258`): priority flags, volumes, description cleaning, display/romaji names from the metadata cache, MangaUpdates fallback from the `mu:<name>` cache key (the cached row stores the MU *URL* in `romaji_title` — a legacy hack), cover fallback, and a synthesized MangaUpdates search URL when none exists. This path calls `get_cached_manga_metadata` once per row (N+1 DB reads).

**Track** (`main.py:4135-4171`): validates `DiscoveryTrackRequest` (`series_name` 1…500, optional `release_id`), resolves the stable identity through `scheduler.resolve_series_identity()` (`scheduler.py:622-788`; priority: user alias override → Calibre match → Calibre-DB tracked name → typed tracked name → AniList canonical/romaji as bridge → reverse alias claim → parsed name), then `db.track_discovered_series(stable_name, release_id, legacy_names=[typed_name])` (`database.py:3277-3361`), which upserts `series_tracking` (`is_monitored=1`, preserves existing website/description), sets `releases.status='REQUESTED'` and re-keys either the given release id or *all* rows whose series name equals the clean name, then re-keys rows under legacy aliases. It logs `SERIES_TRACKED` and fires `notify_new_request` for the referenced release (`main.py:4164-4170`).

**Blacklist** (`main.py:4173-4179` and `:4095-4108`): `add_to_blacklist()` upserts the name, sets every release of that series (case-insensitive) to `BLACKLISTED`, and sets `series_tracking.is_monitored=0` (`database.py:2342-2360`); `remove_from_blacklist()` deletes the row and demotes only `BLACKLISTED` rows of that name to `DISCOVERY` so automation can reclassify them (`database.py:2362-2379`). Both invalidate the grouped cache.

**Fetch-info** (`main.py:4181-4209`): resolves `series_name or title`, calls MangaUpdates synchronously (`asyncio.to_thread`), persists description/url/cover via `update_release_details` (COALESCE semantics: `None` leaves the old value — `database.py:3260-3275`), warms the cover cache, and returns the three values.

### 3.7 Release status lifecycle and precedence (REL-018, REL-019, REL-020)

`resolve_status(existing, incoming)` (`database.py:272-307`) is the single gate for **automated** status writes:

1. blank/`None` incoming never clears an existing status;
2. unset existing adopts incoming;
3. if existing ∈ `USER_VETO_STATUSES = {IGNORED, BLACKLISTED}` → keep existing (absolute for automation);
4. incoming `IN_CALIBRE` always wins (acquisition terminal);
5. existing `IN_CALIBRE` is never downgraded to `IN_STAGING`;
6. otherwise the higher `_STATUS_RANK` wins (`DISCOVERY`/`NEW` 0, `REQUESTED`/`IN_CALIBRE` 1, `IN_STAGING` 1.5, `IGNORED`/`BLACKLISTED` 2 — `database.py:247-261`), so `REQUESTED`↔`IN_CALIBRE` reconciliation works but sticky states are never downgraded to a classification.

Writers: `upsert_release()` resolves against the stored status on update (`database.py:2466-2472`); `update_release_status(..., enforce_precedence=True)` for automation vs verbatim for deliberate API calls (`database.py:2573-2594`); `update_release_volumes_and_status()` resolves (`database.py:2596-2623`); the grouped read/reconcile uses `resolve_status` in memory (`database.py:4362-4363`, `:4394-4396`); `expand_unresolved_complete_releases` and expansion paths preserve sticky states. Deliberate user transitions deliberately bypass the model: `POST /ignore` writes `IGNORED` verbatim (`main.py:4081-4086`), `POST /unignore` writes `REQUESTED` verbatim (`main.py:4088-4093`), `POST /status` writes the validated value verbatim (`main.py:4074-4079`), blacklist writes `BLACKLISTED` verbatim (`database.py:2353-2357`), discovery track writes `REQUESTED` verbatim (`database.py:3339-3351`), different-series uses `resolve_status(old, new)` except for veto/detach (`main.py:3891-3898`). Regression coverage: `tests/test_status_precedence.py:184-609` including the poll-summary-counts test that forces the poll's `requested` counter to reflect the *stored* status (`scheduler.py:3653-3662`).

Ignore semantics: `IGNORED` rows disappear from default lists and grouped views (`status NOT IN ('BLACKLISTED','IGNORED')`) but are returned by `status=IGNORED` (exact match, `database.py:2928-2933`) and remain visible in the per-series accordion if the group still has other rows. `unignore` restores to `REQUESTED` (not to the prior status). There is no undo of `IGNORED → REQUESTED` to `NEW`/`DISCOVERY` except via `POST /status`.

`POST /api/releases/{id}/status`: request model is a closed enum of five statuses (`schemas.py:555-558`), so `IN_STAGING`/`NEW` cannot be set through it. No template in this repository references it (grep over `backend/app/templates`), hence PARTIAL.

### 3.8 Download action and derived state (REL-021)

`release_download_state(release)` (`main.py:1314-1361`) derives one of `failed`, `in_calibre`, `in_staging`, `downloading`, `waiting_inflight`, `downloaded`, `not_downloaded` with this precedence: `download_status=FAILED` or `status=FAILED` → failed; `status=IN_CALIBRE`; `status=IN_STAGING`; `download_status=COMPLETED and routed_at` → in_staging; `DOWNLOADING|QUEUED` → downloading; `PENDING_DEDUPE` → waiting_inflight; `COMPLETED` → downloaded; else not_downloaded. It returns `progress_pct` (from `torbox_progress_json.progress_pct` or bytes ratio), `speed_bps`, `error`, `can_download` (not_downloaded/failed), `can_retry` (failed), `terminal` (in_staging/in_calibre). Labels at `main.py:1303-1311`. Listed in `GET /api/releases/{id}/state` (`main.py:3730-3735`, 404 if missing) and embedded in every card/row (`_decorate_release_state`, `main.py:1364-1366`) and in the POST response.

`POST /api/releases/{id}/download` (`main.py:3738-3824`): 404 if unknown; **409** when `status=IN_STAGING`; `download_url = download_url or link`; missing volumes resolved by `scheduler.resolve_release_missing_volumes()` (persisted `download_wanted_json` → stored missing list → fresh matcher computation, `scheduler.py:1029-1053`). If the configured engine is Torbox (`resolve_downloader_engine`), it calls `scheduler.submit_torbox_download(...)`; success-but-skipped returns `status="skipped"` (strict volume selection found nothing missing), success runs a staging pass and returns `{status:"success", engine:"torbox", cached, message, state}`; failure returns 500 with message+state. For qBittorrent: `qbit.add_torrent(download_url, downloads_dir, category="manga", tags="mangatrax")` (category/tags are **hardcoded** here, not from `qbit_category`/`qbit_tags` settings), then `update_download_handle(..., wanted_volumes=missing)`; selective-volume filtering is applied only when the release is a bundle, has a torrent hash, and some but not all volumes are missing (`main.py:3807-3813`), then a staging pass runs. The UI never reloads: it updates the chip/button in place and polls `/state` every 1.5 s while state ∈ {downloading, waiting_inflight, downloaded} (`releases.html:586-682`; `prioritized.html:558-620`).

### 3.9 Different-series re-key (REL-022)

`POST /api/releases/{id}/different-series` (`main.py:3839-3923`) accepts exactly one of: `detach: true` (target = series parsed from the release title, else the stored name), `new_series_title`, or `calibre_series_id` (resolved against the Calibre snapshot, falling back to `series_tracking`). It builds a candidate release dict with the new `series_name`, re-runs `matcher.check_release(candidate, auto_request_monitored=True)` to derive status + missing volumes (detach forces the fallback branch: status `DISCOVERY` for vol-1/complete/bundle/volume-less, else `NEW`), and then: user-veto statuses are preserved verbatim; detach forces `DISCOVERY`/`NEW`; otherwise `resolve_status(old_status, computed)` is applied. It writes `series_name`, `status`, `missing_vols_json` directly via SQL (`main.py:3899-3905`), invalidates the grouped cache, and audits `RELEASE_REKEYED_DIFFERENT_SERIES`. Response includes the updated row, missing volumes, release status and derived download state. The modal lists Calibre series via `GET /api/calibre/series?search=` (`main.py:2322-2325`) and has a "Detach → Discovery" button (`releases.html:684-770`).

### 3.10 Feeds, providers and polling (REL-023 … REL-027, REL-039, REL-040)

**Feed model** (`database.py:649-663`): `id, name, feed_type, url, config_json, enabled (1), auto_download (1), check_interval_minutes (60), last_checked, last_status, last_error`. One stock row is seeded when the table is empty: name `Manga Releases (RSS)`, type `rss`, URL `https://nyaa.si/?page=rss&q=manga+cbz&c=3_1&f=0`, interval 60 (`database.py:21-22`, `:839-850`). `add_feed` inserts `enabled=1` (`database.py:3428-3436`); `update_feed_status` stamps `last_checked=CURRENT_TIMESTAMP` plus `OK`/`ERROR` and the error text (`database.py:3444-3448`); `set_feed_enabled` flips the flag (`database.py:3407-3413`); `delete_feed` removes the row (`database.py:3438-3442`).

**API surface** (`main.py:1979-2074`): `POST /api/feeds/check` starts `scheduler.start_feed_check()` (returns `{success, started, message}`), then wraps the poll in a persisted background task (`background_tasks.run_in_background(db, "feeds:poll", "Poll Feeds", _feed_monitor, …)`) whose monitor thread polls the scheduler status every 0.75 s and finishes the task; the response adds `task_id`/`task`. `GET /api/feeds/check/status` returns `{running, started_at, finished_at, result}` from scheduler memory (`scheduler.py:3409-3418`). `POST /api/feeds/test` validates `FeedTestRequest` (strict SSRF with DNS re-check — `schemas.py:530-541`) and builds a provider without saving, returning `{status, count, items[:25]}` or a 4xx/5xx JSON error (`main.py:2017-2036`). `POST /api/feeds` validates `FeedCreateRequest` (`name` 1…200, `url`, `feed_type` ∈ rss/html/html_scraper/nyaa/nyaa_scraper/nyaa_manga, `interval` 5…1440, `auto_download`, optional `config`) and inserts; audit `FEED_ADDED` (`main.py:2038-2051`). `DELETE /api/feeds/{id}` (`main.py:2053-2057`). `POST /api/feeds/{id}/toggle` validates `{enabled: bool}`, 404 when missing, audits `FEED_ENABLED`/`FEED_DISABLED` (`main.py:2059-2074`).

**Provider construction** (`providers/registry.py:17-47`): `create_provider(feed, nyaa_base_url=…)` maps `feed_type` lower-case to a class (`rss`→RSSFeedProvider, `html|html_scraper`→HtmlScraperProvider, `nyaa|nyaa_scraper|nyaa_manga`→NyaaMangaProvider), parses `config_json` (dict or JSON string, errors → `{}`), and injects `base_url` from the resolved Nyaa base only when the feed config doesn't define one. Provider contract: `fetch_releases() -> List[ReleaseItem]`, raising `ProviderFetchError` when the source could not be fetched at all so a dead feed is a visible failure rather than an empty success (`providers/base.py:7-15`, `:95-104`).

* `RSSFeedProvider` (`providers/rss.py:42-203`): SSRF-guards the URL (returns `[]` on rejection — a silent-empty path), parses with feedparser under a temporarily lowered default socket timeout (15 s, restored in `finally`), raises on network failure or `bozo` without entries, filters chapter-only and LN titles, resolves web-post URL vs torrent/magnet/enclosure/`nyaa_infohash`/links, extracts cover from media/enclosure/`<img>`, parses the title, and builds `ReleaseItem` with `guid = entry.id|guid|web_post_link`.
* `HtmlScraperProvider` (`providers/html_scraper.py`): configurable `item_selector/title_selector/link_selector/download_selector/image_selector/next_selector/max_pages` (defaults at `:27-33`), SSRF guard, 15 s urllib fetch, per-page item loop with the same chapter/LN filters, relative-URL resolution, next-page following (stops on self-loop or missing next), raising `ProviderFetchError` only when page 1 yields nothing.
* `NyaaMangaProvider` (`providers/nyaa.py`): see §3.11.

**Poll orchestration** (`scheduler.py`): `check_all_feeds(trigger)` acquires `_feed_check_lock` non-blocking; a concurrent call returns `skipped=True` (`:3352-3369`). `start_feed_check()` sets in-memory state `{running, started_at, finished_at, result}` and starts a daemon thread running `check_all_feeds(trigger="manual")` (`:3372-3407`). The interval poll is driven by the worker loop: every 10 s iteration it reads `calibre_sync_interval_minutes` and `feed_check_interval_minutes` (default 60) and calls `check_all_feeds(trigger="auto")` when due (`:5185-5200`).

`_check_all_feeds_locked()` (`:3420-3823`) is the pipeline:

1. Build the Calibre context once per pass (scanner if available, else DB tracked rows; volumes map; metadata cache; `MangaCalibreMatcher` with staged volumes) (`:3434-3440`).
2. Load once per pass: `discard_unmarked_chapters` setting, `known_series_keys` (normalized names from `series_tracking` + Calibre list, with delimiter/paren variants) (`:3442-3448`, `:237-260`).
3. Read global `auto_download`, `downloads_dir`, qbit client, engine (`:3450-3453`).
4. For each feed with `enabled` truthy (`:3455-3457`): build provider (configuration errors fail the feed only, audit `FEED_ERROR`), fetch releases (exceptions → `feeds_failed`, `ERROR`, audit), then per item:
   * JPEG XL → `_log_jpegxl_discard` (once per guid) and skip (`:3482-3486`, `:145-157`);
   * `is_chapter_only` → either protected as a real numbered series name, or discarded with a `CHAPTER_DISCARDED` audit (`:3492-3536`);
   * `is_light_novel` → skip (`:3537-3538`);
   * marker-less chapter guard (`:3539-3543`);
   * `total_found += 1`; resolve identity (`resolve_series_identity(_resolve_release_series_input(...))`) → `series_key`, metadata, cover (cached through `cover_cache.cache_sync`), aliases (`:3547-3561`);
   * collected-batch inference before matching (`:3563-3567`);
   * blacklist check on the stable key → `BLACKLISTED` + no missing volumes, else `matcher.check_release(payload, alt_series_names)` → `status`, `missing_volumes` (`:3573-3583`);
   * for `DISCOVERY`, pull MangaUpdates description/URL (and cover) (`:3585-3597`);
   * build `rel_dict` and upsert with `resolve_status` (`:3599-3638`);
   * detect transitions from stored `(status, download_status)` *before* upsert: `not is_new and prev_status in (DISCOVERY, NEW) and prev_dl_status == IDLE` (`:3625-3651`);
   * compute `effective_status = resolve_status(prev_status, status)`; only if `REQUESTED` count it, and if new/transition (a) force `update_release_status(REQUESTED, enforce_precedence=True)` on transitions, (b) notify the new request, (c) evaluate auto-download (`:3653-3780`);
   * after the loop, mark the feed `OK` (`:3782`); any exception marks the feed `ERROR` and continues (`:3783-3789`).
5. Assemble a summary string with counts and up to 5 per-feed errors; run a staging pass if anything was sent to the downloader; audit `FEED_POLL_COMPLETED`; return the uniform payload `{success, message, releases_found, new_releases, transitions, requested, sent_to_downloader, feeds_failed, errors, skipped}` (`:3791-3823`, `:3326-3350`).
6. Only automatic polls notify: `check_all_feeds` calls `_notify_feed_scan_result(result)` when `trigger == "auto"` (`:3363-3367`), which respects `webhook_feed_scan_notifications` and suppresses notifications unless `new_releases/transitions/requested/sent_to_downloader` (or processed lists) are non-zero (`:1332-1375`).

**Failure behavior.** Provider errors never abort the whole poll; they increment `feeds_failed` and are surfaced in `errors[]` and in the summary appended to the message (`:3796-3799`), and `success = feeds_failed == 0`. A crashed background poll is caught and published as a failed result (`:3399-3407`). The UI `waitForFeedCheck()` polls every 1.5 s up to 15 minutes, toasts the result (success or error) and reloads (`base.html:541-592`).

**Per-feed interval is vestigial**: `check_interval_minutes` is written at creation (`database.py:3432-3434`), shown on `/feeds` (`feeds.html:84-86`), and accepted in the Add-feed modal, but the scheduler only reads the global `feed_check_interval_minutes` (`scheduler.py:5189-5200`) and skips solely on `enabled` (`:3455-3457`). No code path reads `feed.check_interval_minutes` (verified by grep across `backend/app`).

### 3.11 Nyaa crawler (REL-025, REL-039)

`NyaaMangaProvider.__init__` (`providers/nyaa.py:49-63`) reads `category` (default `3_1` = Literature/English-translated, `:24`), `max_pages` (default 2), `delay_seconds` (1.5), `query`, and resolves `base_url` via `resolve_nyaa_base_url(config)` (feed `config.base_url` → `nyaa_base_url` setting → `https://nyaa.si`; `nyaa_source.py:96-113`). `_join_url` keeps absolute hrefs, joins site-relative hrefs onto the configured base (incl. path prefix), and converts protocol-relative `//…` using the base scheme (`:65-80`).

`crawl_pages()` (`:248-462`): per page it builds `{base}/?f=0&c=<category>&s=id&o=desc&p=<page>[&q=<query>]`, calls `progress_cb(page, max_pages, url)`, fetches with `_fetch_page_html`, parses `table.torrent-list tbody tr`, and per row: picks the last non-comment title link (`td:nth-of-type(2) a`), skips duplicate view URLs, filters `is_chapter_only` and `is_light_novel`, extracts torrent/magnet (or view URL) from `td:nth-of-type(3)`, size from column 4, `pub_date` text + `data-timestamp` from column 5 (used for the date cutoff), seeders from column 6, parses the title with `extract_manga_bundle_and_volumes`, and constructs `ReleaseItem(guid=view_url, source_feed_name, raw_data={confidence, post_size, seeders, torrent_url, magnet_url})`. Stop reasons (recorded on `last_crawl_reason`, per-page detail on `last_crawl_pages`): `max pages reached (N)`, `reached end of results (page N: HTTP 404|no rows)`, `date cutoff reached (page N)`, `stopped by user (page N)`, `failed on page N (reason)`.

Resilience/politeness (`:26-38`, `:146-246`): 3 attempts with backoff `(1.0, 3.0)` + ≤0.5 s jitter, or the server's `Retry-After` (seconds or HTTP-date) capped at 30 s; empty body counts as failure; only HTTP 404 or a parsed page with zero torrent rows is an accepted end; on the final attempt it raises `ProviderFetchError`. Minimum interval between page fetches is `max(1.0, delay)` measured start-to-start (`:175-185`, `:274-276`); user-configured `delay_seconds` cannot go below 1 s. `search_series()` (used by series backfill) crawls the primary title then each alternate name with `delay` between searches, deduping by `link` and honoring `stop_check` (`:93-143`).

**Mirror support** (`nyaa_source.py`): `normalize_nyaa_base_url` accepts a bare base or a full Nyaa-style URL, rejects empty/credentials/non-http(s)/malformed ports/`.`/`..` segments, drops query+fragment, lower-cases the host, keeps path prefixes, and runs `security.validate_public_url(..., resolve_dns=False)` (structural SSRF only) (`:46-93`). `resolve_nyaa_base_url` skips invalid candidates with a warning (`:96-113`). `scheduler._nyaa_base_url()` normalizes the setting and falls back to the default on error (`scheduler.py:964-977`). On settings save, `db.migrate_seeded_nyaa_feed()` repoints **only** the exact stock feed row (name + stock URL + type rss) to the new base, preserving its query string; user feeds are never touched (`database.py:3364-3405`, `main.py:4254-4262`).

### 3.12 Ingestion filters, parser and detection (REL-028 … REL-031)

Order of filter application (important — each stage can only discard, except property extraction):

1. **Provider-level** (`rss.py:69-77`, `html_scraper.py:94-102`, `nyaa.py:358-365`): `is_chapter_only(title)` and `is_light_novel(title)` drop the row before it becomes a `ReleaseItem`.
2. **Poll-level** (`scheduler.py:3482-3543`): JPEG XL discard+audit; chapter-only handling with the *numbered-series protection* (a bare 3-digit chapter number whose full title or clean series name is a known series is **kept**; the `discard_unmarked_chapters=false` opt-out also keeps bare chapters, but explicit `Ch./Chapter/#` markers are still always discarded); LN discard; marker-less chapter guard.
3. **Identity/property extraction** (§3.13), collected-batch inference, blacklist check, Calibre matching.
4. **DB write** (`upsert_release`): normalizes `published_at`, re-derives `is_light_novel` from the title (`database.py:2436-2440`), re-derives `is_complete`, split group set (explicit list wins), and applies precedence to `status`.

`is_chapter_only` (`parser.py:1258-1342`): complete-series titles are never chapters; any volume indicator (`vol/volume/vols/vol/book(s) N` or `vN` after delimiter) → not chapter; a bare trailing chapter number ≥100 (`_parse_bare_chapter_number`, `:497-523`) → chapter; explicit `chapter/chap/ch/c/#` markers → chapter; `+`-joined bare ranges → chapter; a bare numeric range that passes `_parse_bare_volume_range` guards → **not** a chapter (bundle); bracketed bare numbers, trailing separator+number, trailing bare range, zero-padded trailing number → chapter.

`is_light_novel` (`parser.py:1144-1231`): a manga marker (`manga|cbz|cbr|comic`) vetoes everything; then (1) explicit media tags `[LN]`/`(Light Novel)`/`[WN]`/`(Novel)`/`(Audiobook)` in either bracket style; (2) dedicated LN imprints (`yen on`, `airship`, `cross infinite world`, `j-novel club/heart`, `tentai books`, `hanashi media`, `seven seas siren`) checked with **parenthesised** metadata stripped (manga convention keeps groups in parens); (3) ebook extensions (`epub|mobi|azw3`); (4) square-bracket `[Publisher] [Group]` pairs where the publisher is an LN imprint; (5) a standalone `[Stick]` bracket. `LN_IMPRINT_PUBLISHERS` is deliberately narrower than `KNOWN_PUBLISHERS` so manga publishers (K-MANGA, Manga UP!, Viz) can never make a manga release look like an LN (`parser.py:92-123`). Persisted to `releases.is_light_novel` on upsert and hidden by `COALESCE(is_light_novel,0)=0` in `get_releases`, `count_releases`, grouped builder, discovery, `search_releases`, and `get_pending_requests` (tests: `tests/test_light_novel_release_filtering.py:72-96`).

`is_jpegxl_release` (`parser.py:1124-1141`): bracket/bare `JPEG XL|JXL` or `.jxl` suffix. Feed rows are discarded before storage and audited `JPEGXL_DISCARDED` once per guid; legacy stored rows are re-typed `jpegxl` by `_run_jpegxl_filter_once` (`database.py:1105-1164`).

`is_complete_series_release` (`parser.py:1233-1256`): a bracket containing a complete-word **plus** a volume reference (`[vol.1-11 Complete]`), explicit complete tags, or a complete-series phrase after a separator → true. It is used for the `COMPLETED` pseudo-status, volume expansion and matching.

**Marker-less chapter guard** (`scheduler.py:263-392`): `_unmarked_chapter_reason()` returns non-empty only when: not complete/LN/chapter-only; no parsed volume start or volumes; parser says `is_chapter` and not bundle/volumes; the *numbered full title* normalized is not itself a known series; the clean series name **is** a known series. Then `_discard_unmarked_chapter()` audits `CHAPTER_DISCARDED` with the reason and returns True. `_resolve_release_series_input()` keeps the numbered full title as the matching key when it is a known series (so retained chapters group correctly). `_explicit_chapter_range_reason()` gives the same audit trail for explicit chapter *ranges* of known series (`:346-373`).

**Parser** (`parser.py`): `extract_manga_bundle_and_volumes(raw_title, known_groups=None)` (`:2211-2218`) → impl `:1439-1688`. Order: strip site prefixes (`:356-360`); normalize omnibus `X 1 (Vols 1-3)` → `X Vols 1-3` (`:152-158`, `:1456-1462`); chapter→volume idiom `ch A-B as v X-Y` (`:174-183`, `:1464-1469`); `clean_manga_title()`; strip the extracted release group; drop `Part NN` when an explicit volume marker also exists (`:1478-1485`); revision; build the result dict (`:1488-1522`) with `release_groups`, `publisher`, `authors`, `revision`, `is_collected_release`, `is_light_novel`, `is_bundle/is_complete`, `volume_start/end`, `volumes`, `confidence`, chapter diagnostics `is_chapter/chapter_number/chapter_ranges`, `numbered_series_name`; then pattern matching: complete marker, bundle range (plural-aware, clamped by `MAX_BUNDLE_SPAN=1000`, `:125`, `:1577-1628`), list-bundle (Oxford comma, `:1630-1649`), single volume incl. `Part`, (`:1651-1669`), bare numeric range (`:1671-1682`), else bare-number chapter strip (`:1684-1687`). Bare ranges are deliberately **volume bundles**, while zero-padded ranges stay chapter notation (`:160-168`, `:1310-1315`).

**Collected-batch inference** (`scheduler.py:394-531`): only for `is_collected_release_candidate()` (parenthesised year range, no volume/chapter markers — `parser.py:273-301`), only when the release has no volumes and is not explicitly complete, only when the *infer* setting is on. It consults the already-resolved metadata, then AniList for each alias, then MangaUpdates; a candidate is accepted only if `total_volumes ∈ 1..300` **and** its own title/aliases match the series key at ≥0.85 similarity (`_metadata_name_matches`) **and** its status is `FINISHED|PUBLISHED`. On success it sets `volumes = 1..N`, `is_bundle`, `inferred_volumes`, and audits `COLLECTED_RELEASE_INFERRED`; otherwise the release stays volume-less (retried next poll).

### 3.13 Complete-series expansion and read-time reconciliation (REL-032)

Read-time: §3.2 step 7 (in-memory only). Write-time: `scheduler.expand_unresolved_complete_releases()` (`:4976-5138`) resolves volume counts for complete rows with no volumes via metadata/AniList/MangaUpdates, writes via `update_release_volumes_and_status()` (precedence-aware), and is invoked at worker startup and every 300 s (`:5161-5165`, `:5202-5209`), together with `db.reconcile_release_volume_state()` (`database.py:3989-4002`) which persists the read-time reconciliation. Any provider-metadata/Calibre change during a series refresh also triggers expansion (`scheduler.py:4525-4535`). This split matters: the GET path is strictly read-only (documented at `database.py:3974-3977`, `:4365-4366`, `:4397-4398`), so what the UI shows converges in the DB only after the maintenance/worker pass.

### 3.14 Light-novel / media typing maintenance (REL-033, REL-034)

`is_light_novel`/`is_chapter_only` are title heuristics; the durable record is `releases.media_type` (`database.py:497-498`, values `database.py:59-85`) plus the legacy `is_light_novel` flag (`:536-538`). Two idempotent one-shot startup passes (after the schema transaction, `database.py:999-1003`):
* `_run_media_filter_once` — only rows typed `unknown` are re-typed (jpegxl → light_novel → chapter_only order), never deleted, never status-writing, gated by `media_filter_applied` and `filter_non_manga`; audits `MEDIA_RECLASSIFY` (`:1005-1103`).
* `_run_jpegxl_filter_once` — legacy JXL rows, marker `jpegxl_filter_applied` (`:1105-1164`).
A one-time `ln_release_backfill_done` pass flags legacy LN rows by title (`:918-938`). Non-manga types are hidden by `tracked_media_clause()` (`:107-118`); the only deletion path is the explicit debug action `purge-non-manga` (`:3601-3640`, `main.py:4373-4378`) and the explicit LN cleanup API (`main.py:2471-2506` → `ln_releases.py:31-69`: audit read-only; cleanup requires explicit ids and requires the row to actually be LN; `ignore` sets `IGNORED`, `delete` deletes the row only).

### 3.15 Auto-download from the feed poll (REL-035)

Eligibility (`scheduler.py:3660-3780`): `effective_status == "REQUESTED"` (post-precedence) **and** (`is_new` or transition) **and** global `auto_download` **and** feed-level `auto_download`; then in order: engine readiness (`_auto_download_engine_ready`: Torbox needs `torbox_enabled` + a non-empty API key, qBit needs a client — `:1055-1069`), non-empty missing volumes, a download URL. In-flight dedupe: `_auto_download_wanted()` removes volumes already reserved by another release of the same series that is `DOWNLOADING`/`COMPLETED` (via `db.get_inflight_reserved_volumes`) and volumes already present in staging from a scan cached 60 s (`:103-1304`, `:1093-1126`). If nothing remains, the row is stored as `download_status='PENDING_DEDUPE'` for the 60 s retry sweep (`:3707-3715`, `:2329-2459`). Otherwise Torbox submit or qBit `add_torrent` + `update_download_handle(wanted_volumes)` + selective volume priorities for bundles (`:3716-3780`). Every decision (submitted/skipped with reason) is audited `AUTO_DOWNLOAD_SUBMITTED|AUTO_DOWNLOAD_SKIPPED` (`:1071-1091`), readable through `GET /api/downloads/auto-download/diagnostics` (`main.py:2213-2217`). Manual downloads intentionally bypass the dedupe (`main.py:3738-3824`).

### 3.16 Backfill flows (REL-036, REL-037)

**Polite historical backfill** (`scheduler.py:4755-4910`): `main.py:4293-4310` rejects with `in_progress` when `scheduler.is_backfilling`, validates `BackfillStartRequest` (`max_pages` 0…5000 where 0 = unlimited, `delay` 0…300, `start_page ≥ 1`), then `run_polite_backfill()` sets the flag and starts a daemon thread (`:4755-4773`). The worker resolves defaults from settings (`backfill_max_pages` 15, `backfill_delay_seconds` 2.0), computes `end_p` (9999 when unlimited), and for each page sets `backfill_last_page` **before** crawling, sets a human progress string, then calls `crawl_pages(max_pages=1, start_page=page_num, delay=…, stop_check=…, raise_on_fetch_error=True)` so each page gets its own retry/stop reporting. `page_info.kind` is then interpreted: `failed` → raise `ProviderFetchError`; `end_of_results` → normal stop; `stopped` → user stop; an `ok` page with zero ingestable releases is **not** treated as end-of-results. Rows are processed through `_process_release()`, `CHAPTER_ONLY/LIGHT_NOVEL/JPEGXL` skipped, new `DISCOVERY` counted. On completion it writes `backfill_status=idle` + the summary (including the stop reason and the page range crawled) and audits `BACKFILL_COMPLETED`; on exception `status=error` + `BACKFILL_ERROR`; `finally` clears `is_backfilling`. `stop_backfill()` only sets the flag and writes `stopping`/`Stop requested…` (`:3852-3856`); `get_backfill_status()` returns `{running, status, progress, last_page}` (`:3858-3868`). The base template polls `/api/backfill/status` every 2.5 s while running, shows the progress text in a global banner with a Stop button, and toasts completion when it transitions to idle (`base.html:645-689`). The setup modal reads `last_page` to offer "Resume from Page N" (`base.html:753-795`) and posts `{start_page, max_pages}` (`:797-819`).

**Series refresh + targeted Nyaa backfill** (`scheduler.py:4460-4708`), triggered by `POST /api/series/backfill` (`main.py:4064-4072`) or `POST /api/series/{id}/backfill` (`main.py:4049-4062`); both validate max_pages 1…500 (default 3), create a persisted background task via `_start_backfill_task` (`main.py:4013-4046`), which starts `run_series_backfill_async` (daemon thread) and either finishes the task immediately or spawns a watcher that mirrors `backfill_progress` until the flag clears. Phases, each exception-isolated: (1) `refresh_series_provider_metadata`, (2) `refresh_series_calibre_info`, (3) reconcile this series' releases, (4) expansion if metadata/Calibre volume counts changed, (5) `detect_series_titles()` (primary + alternate titles) then `NyaaMangaProvider.search_series(...)` with `alt_names` and `stop_check`, and finally per-release `_process_release(..., target_identity=…)`. The **spinoff guard** (`:3968-3999`) skips (audit `SPINOFF_SKIPPED`) any crawled release whose own AniList entry id differs from the target's or whose parsed/stable name is not among the target's alias norms; `status` values `SPINOFF_SKIPPED|CHAPTER_ONLY|LIGHT_NOVEL|JPEGXL` are excluded from counts. New `REQUESTED` rows may auto-download through the same dedupe path (`:4622-4660`). The result carries `{status, message, series_name, searched_titles, releases_found, requested, sent_to_downloader, reconciled, expanded, summary{metadata,calibre,backfill}}`; crawl failure yields `status="partial"` while metadata/Calibre changes remain applied. Since both flows share `is_backfilling`, they are mutually exclusive, and the shared banner/poller shows whichever is running.

### 3.17 Caches (REL-038)

Three distinct caches matter here:

1. **Grouped-release cache** (`database.py:363-375`, `:4024-4031`, `:4525-4526`): per-process dict, keyed `status_prioritized_sort`, 5 s TTL, storing the full (unpaginated, unsearched) group list; search bypasses it; every release/series/prioritized/blacklist/tag write calls `_invalidate_grouped_cache()`. Note it stores *references* to the group dicts returned by the builder (§7.1).
2. **Metadata cache** (`manga_metadata_cache`, `database.py:710-723`): used read-only by grouping/discovery (no network during page render — `database.py:4203`), warmed by poll/identity resolution and `POST /api/discovery/{id}/fetch-info` (`mu:<name>` key hack).
3. **Release-row cache maintenance** (`REL-038`): `GET /api/maintenance/releases-cache?series=&statuses=&exclude_statuses=` previews `count_cached_releases` (excludes rows with `download_status`/`status` in `DOWNLOADING|QUEUED` — `database.py:1189-1201`); `POST /api/maintenance/clear-releases` requires `confirm:true`, accepts a comma list or list for `statuses` and a list for `exclude_statuses` (400 otherwise), runs inside a recorded background task, deletes only non-active matching rows, audits `RELEASES_CLEARED` with deleted/skipped counts, and invalidates the grouped cache (`database.py:1203-1246`; endpoint `main.py:2599-2628`). Tests pin the "never delete active downloads / never touch tracking" invariants (`tests/test_release_cache_clear.py:53-110`).

### 3.18 Notifications and audit trails

Every mutating action and every automatic decision writes `activity_logs` via `db.log_activity(event_type, message, details, actor)` where the actor defaults to the request-scoped ContextVar (authenticated user, `anonymous` when auth is off) or the explicit `"scheduler"` for worker paths (`database.py:25-38`, `:3451-3470`). Events relevant here: `FEED_ADDED`, `FEED_ENABLED|FEED_DISABLED`, `FEED_ERROR`, `FEED_POLL_COMPLETED`, `JPEGXL_DISCARDED`, `CHAPTER_DISCARDED`, `COLLECTED_RELEASE_INFERRED`, `AUTO_DOWNLOAD_SUBMITTED|AUTO_DOWNLOAD_SKIPPED`, `IN_FLIGHT_DEDUPE`, `SELECTIVE_DOWNLOAD`, `MANUAL_DOWNLOAD`, `RELEASE_IGNORED`, `RELEASE_RESTORED`, `SERIES_BLACKLISTED`, `BLACKLIST_REMOVED`, `GROUP_PRIORITIZED`, `GROUP_UNPRIORITIZED`, `SERIES_TRACKED`, `RELEASE_REKEYED_DIFFERENT_SERIES`, `BACKFILL_COMPLETED`, `BACKFILL_ERROR`, `SERIES_BACKFILLED`, `RELEASES_CLEARED`, `NYAA_BASE_UPDATED`, `MEDIA_RECLASSIFY`, plus background-task start/finish events. Webhooks: `notify_new_request` on new/transitioned requests and on discovery Track (`scheduler.py:3670-3675`, `:4616-4621`, `main.py:4164-4170`), `notify_feed_scan` for automatic polls with activity (REL-040), all gated by settings.

### 3.19 Validation and security envelope

All mutating endpoints validate a Pydantic model via `_validated_payload` (`main.py:320-325`; models in `schemas.py`), with `extra="forbid"` and bounded lengths; validation errors map to HTTP 400 with joined messages. Auth/CSRF middleware runs before every route: `MANGATRAX_AUTH_MODE=none|basic|oidc` (default none → everything allowed, actor `anonymous`), and cross-origin unsafe requests are rejected with 403 when auth is enabled, `MANGATRAX_CSRF_PROTECT` (default on) and `MANGATRAX_PUBLIC_URL` are configured (`auth.py:1007-1041`, `main.py:239-267`). SSRF guards: `/api/feeds/test` uses strict URL validation with DNS re-check; feed creation uses structural validation; RSS/HTML providers re-validate before fetching; `nyaa_base_url` is structurally validated. Stored-XSS defense: templates render only `http(s)` URLs as hrefs via the `safe_url` filter (`main.py:299-300`), and user text is escaped by Jinja autoescape (excepting explicit `|escape` inside JS string arguments, e.g. group names in partials). Destructive maintenance (`/api/debug/action`: clear-discovery, clear-descriptions, clear-thumbnails, normalize-english-titles, purge-non-manga, reset-all-data) requires `MANGATRAX_DEBUG_ACTIONS=1` (`main.py:4342-4388`, `auth.py:351`); safe maintenance (cover cache / discovery / descriptions) is available without it via `/api/maintenance/clear` (`main.py:4323-4339`, `maintenance.py`).

### 3.20 Endpoint reference (params, responses, errors)

Paths are grouped by the page that owns them. "fragment" = the endpoint returns pre-rendered HTML in `html` rather than data. All mutating routes run through `_validated_payload()` (400 on validation failure) and the auth/CSRF middleware.

| Method + path | Handler | Params (validation) | Response keys | Errors / notes |
|---|---|---|---|---|
| GET `/releases` | `main.py:1182` | `status` (optional, passed raw to SQL), `view` ∈ `series\|flat` (default series), `sort` ∈ `updated\|post_date\|series` | HTML | unknown `sort` silently coerced to `updated`; counts wrapped in try/except so a DB error renders 0 |
| GET `/prioritized` | `main.py:1209` | `status`, `view` | HTML | `initial_total=0` for flat |
| GET `/discovery` | `main.py:1571` | `page ≥ 1`, `page_size 6…120` (default 24), `q`, `include_tags` (csv), `exclude_tags` (csv), `tag_match ∈ any\|all`, `sort ∈ relevance\|newest\|series\|post_date` | HTML | `page` clamped to `[1, total_pages]`; unknown sort → `newest` |
| GET `/feeds` | `main.py:1851` | — | HTML | — |
| GET `/api/releases/cards` | `main.py:1378` | `status`, `offset ≥ 0` (0), `limit 1…50` (8), `search`, `sort` | fragment: `status, offset, count, total, has_more, html` | `total` = group count after search; releases per group capped at 10 |
| GET `/api/prioritized/cards` | `main.py:1406` | same as cards | fragment same | implicit `prioritized_only=True` |
| GET `/api/releases/flat_rows` | `main.py:1434` | `status`, `offset`, `limit 1…200` (50), `search`, `sort` | fragment: `status, offset, count, total, has_more, html` | `has_more` uses pre-dedupe length |
| GET `/api/releases/series_releases` | `main.py:1471` | `series_key` (required), `offset`, `limit 1…50` (10), `status` | fragment + `total` (rows in group) | rebuilds with search, ignores `prioritized_only`/`sort` |
| GET `/api/prioritized/flat_rows` | `main.py:1503` | `status`, `offset`, `limit 1…200` (50) | fragment: `status, offset, count, has_more, html` | **no `total`**, no search/sort |
| GET `/api/releases/grouped` | `main.py:1547` | `status` | data: `status, count, series_groups[]` | unbounded full dump (REL-041) |
| GET `/api/search` | `main.py:3498` | `q` (required non-blank, ≤200), `limit 1…50` (20) | `status, query, limit, series[], releases[], counts{series,releases}, total` | 400 when `q` blank; no template caller (REL-042) |
| GET `/api/tags` | `main.py:1565` | `q`, `limit 1…50` (20) | `status, tags[{name,category,count,rank}], count` | reads metadata cache + denormalized tags; no writes |
| GET `/api/releases/{id}/state` | `main.py:3730` | — | `status, release_id, state, label, progress_pct, speed_bps, error, can_download, can_retry, terminal` | 404 when the release is gone |
| POST `/api/releases/{id}/download` | `main.py:3738` | no body | `status, engine?, cached?, message, state` | 404 unknown; 409 `IN_STAGING`; 200 `success\|skipped`; 500 engine rejection |
| POST `/api/releases/{id}/different-series` | `main.py:3839` | JSON: one of `detach=true`, `new_series_title`, `calibre_series_id` | `status, release_id, series_name, missing_volumes, release_status, download_state, release` | 400 no/empty target; 404 release or Calibre series; 200 even when status stayed vetoed |
| POST `/api/releases/{id}/status` | `main.py:4074` | `{status: REQUESTED\|IN_CALIBRE\|IGNORED\|BLACKLISTED\|DISCOVERY}` | `{status:"success"}` | 400 extra/unknown enum values; writes verbatim (REL-020) |
| POST `/api/releases/{id}/ignore` | `main.py:4081` | — | `{status:"success", message}` | writes `IGNORED` verbatim; audits `RELEASE_IGNORED` |
| POST `/api/releases/{id}/unignore` | `main.py:4088` | — | `{status:"success", message}` | writes `REQUESTED` verbatim; audits `RELEASE_RESTORED` |
| POST `/api/blacklist` | `main.py:4095` | `{name 1…500, reason ≤2000}` | `{status:"success"}` | ignores the return value of `add_to_blacklist` (blank name → success anyway) |
| DELETE `/api/blacklist/{id}` | `main.py:4103` | path id | `{status:"success"}` | no 404 for unknown id |
| GET `/api/prioritized/groups` | `main.py:4112` | — | `{status, groups[]}` | each group adds `release_count` |
| POST `/api/prioritized/groups` | `main.py:4117` | `{name 1…200, notes ≤2000, priority 0…100 (1)}` | `{status:"success"}` | upsert by name; returns success even if name blank (model forbids) |
| DELETE `/api/prioritized/groups/{id}` | `main.py:4124` | path id | `{status:"success"}` | no 404 |
| GET `/api/prioritized/releases` | `main.py:4130` | `status` | `{status, count, releases[]}` | unbounded default limit 500 |
| POST `/api/discovery/track` | `main.py:4135` | `{series_name 1…500, release_id? ≥1}` | `{status:"success", tracked_name}` | resolves stable key; network metadata lookup may be slow |
| POST `/api/discovery/blacklist` | `main.py:4173` | `{series_name 1…500, reason ≤2000}` | `{status:"success"}` | — |
| POST `/api/discovery/{id}/fetch-info` | `main.py:4181` | path id | `{status, description, mangaupdates_url, cover_url}` | 404 unknown; live MangaUpdates call |
| POST `/api/feeds/check` | `main.py:1979` | — | `{success, started, message, task_id, task}` | `started=false` + `success=true` when one is already running |
| GET `/api/feeds/check/status` | `main.py:2012` | — | `{running, started_at, finished_at, result\|null}` | never fails; in-memory only |
| POST `/api/feeds/test` | `main.py:2017` | `{url, feed_type, config}` (strict SSRF) | `{status, count, items[≤25]}` | 400 invalid provider type; 500 fetch error (JSON) |
| POST `/api/feeds` | `main.py:2038` | `{name 1…200, url, feed_type ∈ 6 values (rss), interval 5…1440 (60), auto_download (true), config {}}` | `{status:"success", feed_id}` | audit `FEED_ADDED`; URL structural validation only |
| DELETE `/api/feeds/{id}` | `main.py:2053` | path id | `{status:"success"}` | no 404 |
| POST `/api/feeds/{id}/toggle` | `main.py:2059` | `{enabled: bool}` | `{status, feed_id, enabled}` | 404 when the row does not exist |
| POST `/api/backfill/start` | `main.py:4293` | `{max_pages? 0…5000, delay? 0…300, start_page? ≥1}` | `{status:"started", message}` | `{status:"in_progress"}` when `is_backfilling` |
| POST `/api/backfill/stop` | `main.py:4312` | — | `{status:"stopping", message}` | only sets the flag |
| GET `/api/backfill/status` | `main.py:4318` | — | `{running, status, progress, last_page}` | reads `app_settings` + scheduler flag |
| POST `/api/series/{id}/backfill`, `/api/series/backfill` | `main.py:4049`, `:4064` | `{max_pages 1…500 (3)}` / `{series_name, max_pages}` | `{status:"started", message, summary{metadata,calibre,backfill}, task_id, task}` | 404 unknown series id; `in_progress` when a backfill runs |
| GET `/api/maintenance/releases-cache` | `main.py:2587` | `series`, `statuses` (csv), `exclude_statuses` (csv) | `{status, count}` | preview excludes active rows |
| POST `/api/maintenance/clear-releases` | `main.py:2599` | `{confirm:true, series_name?, statuses?(list\|csv), exclude_statuses?(list)}` | `{status, deleted, skipped_active, series_name, statuses, task_id, task}` | 400 without `confirm:true` or bad filter types |
| GET `/api/maintenance/light-novels/audit` | `main.py:2471` | — | `{status, releases[], count}` | read-only, LIMIT 500 |
| POST `/api/maintenance/light-novels/cleanup` | `main.py:2477` | `{confirm:true, release_ids[≤500], action: ignore\|delete}` | `{status, action, changed, skipped, task_id, task}` | 400 confirm/ids/action/ints; non-LN ids are skipped |
| GET `/api/calibre/series` | `main.py:2322` | `search`, `source` | `{status, series[], count}` | used by the different-series modal only |

### 3.21 Status transition matrix (`resolve_status(existing, incoming)`)

Rules in evaluation order: blank incoming → keep existing; unset existing → incoming; existing veto → existing; incoming `IN_CALIBRE` → `IN_CALIBRE`; existing `IN_CALIBRE` + incoming `IN_STAGING` → `IN_CALIBRE`; else higher rank wins, ties go to incoming (`database.py:272-307`).

| existing ↓ / incoming → | `DISCOVERY` | `NEW` | `REQUESTED` | `IN_STAGING` | `IN_CALIBRE` | `IGNORED`/`BLACKLISTED` | blank/`None` |
|---|---|---|---|---|---|---|---|
| unset | DISCOVERY | NEW | REQUESTED | IN_STAGING | IN_CALIBRE | IGNORED/BLACKLISTED | `None` |
| `DISCOVERY` (0) | DISCOVERY | **NEW** (tie→incoming) | REQUESTED | IN_STAGING | IN_CALIBRE | IGNORED/BLACKLISTED | DISCOVERY |
| `NEW` (0) | **DISCOVERY** | NEW | REQUESTED | IN_STAGING | IN_CALIBRE | IGNORED/BLACKLISTED | NEW |
| `REQUESTED` (1) | DISCOVERY (kept) | NEW (kept) | REQUESTED | **IN_STAGING** (1.5) | IN_CALIBRE | IGNORED/BLACKLISTED | REQUESTED |
| `IN_STAGING` (1.5) | kept | kept | kept | IN_STAGING | IN_CALIBRE | IGNORED/BLACKLISTED | IN_STAGING |
| `IN_CALIBRE` (1) | kept | kept | **REQUESTED** (tie→incoming) | **IN_CALIBRE** (special) | IN_CALIBRE | IGNORED/BLACKLISTED | IN_CALIBRE |
| `IGNORED` (2) | kept | kept | kept | kept | kept | kept | IGNORED |
| `BLACKLISTED` (2) | kept | kept | kept | kept | kept | kept | BLACKLISTED |

Deliberate user writes bypass this matrix entirely (`update_release_status(..., enforce_precedence=False)`), which is why `/ignore`, `/unignore`, `/status`, blacklisting/unblacklisting, discovery Track and detach all work "backwards" relative to the ranks.

### 3.22 Provider capability matrix

| Capability | `rss` | `html` / `html_scraper` | `nyaa` / `nyaa_scraper` / `nyaa_manga` |
|---|---|---|---|
| Entry point URL | feed URL as configured | page URL + selector config | built from `base_url` + query (`?f=0&c=3_1&s=id&o=desc&p=N[&q=]`) |
| Pagination | feed-defined (whatever the feed returns) | `next_selector`, up to `max_pages` (2) | `p=N` loop, up to `max_pages` (2 for feeds, explicit for backfill) |
| Post date | `published`/`updated` (feedparser) | none (ReleaseItem defaults to now) | 5th column text + `data-timestamp` (cutoff support) |
| Download URL | enclosure / magnet / infohash-derived magnet / links | `download_selector` (magnet/.torrent), else detail link | magnet > `.torrent`/`/download/` > view URL |
| Cover | media_thumbnail/media_content/enclosure/img | `image_selector` (src/data-src) | none |
| Discards | chapter-only, LN | chapter-only, LN | chapter-only, LN |
| Empty/failure | raises `ProviderFetchError` on network error or bozo-without-entries | raises only when page 1 yields nothing | raises `ProviderFetchError` after exhausted retries on any required page; 404/empty table = clean end |
| Rate limiting | 15 s socket timeout | 15 s urllib timeout | ≥1 s between pages, 3 attempts, backoff+jitter, `Retry-After` ≤30 s |
| Title parsing | `extract_manga_bundle_and_volumes` | same | same |

### 3.23 Worked end-to-end trace (feed row → visible card → staged file)

Concrete example: the seeded Nyaa RSS feed returns `Solo Leveling v01-03 (Digital) (1r0n, Ushi)` while Calibre already owns volume 1.

1. Worker loop ticks every 10 s; when `feed_check_interval_minutes` (60) is due it calls `check_all_feeds(trigger="auto")` (`scheduler.py:5185-5200`).
2. `RSSFeedProvider.fetch_releases()` filters chapter-only/LN titles, resolves the web-post URL vs torrent/magnet, parses the title (`providers/rss.py:42-201`); `extract_manga_bundle_and_volumes` yields series `Solo Leveling`, volumes `[1,2,3]`, `is_bundle=True`, groups `["1r0n","Ushi"]`, `release_group="1r0n"`.
3. Poll filters find nothing to discard (`scheduler.py:3482-3543`); `known_series_keys` already contains `solo leveling` (Calibre-list/tracked).
4. Identity resolution returns `stable_name="Solo Leveling"` (Calibre name wins), display title from authoritative AniList metadata if cached, alt names for the matcher (`scheduler.py:3554-3561`).
5. Not a collected candidate (has volume markers) so inference is a no-op (`:3563-3567`); not blacklisted (`:3573-3577`).
6. `matcher.check_release()` finds the series, `owned={1}`, `staged={}`, `missing=[2,3]` → `status="REQUESTED"` (`calibre/matcher.py:146-185`).
7. `upsert_release()` inserts the row: guid = RSS entry id, `published_at` normalized, `is_light_novel=0`, `is_complete=0`, `release_groups_json=["1r0n","Ushi"]`, `status=resolve_status(None,"REQUESTED")="REQUESTED"` (`database.py:2533-2571`); grouped cache invalidated (`:2570`).
8. `is_new=True` → `total_new+=1`; `effective_status="REQUESTED"` → `requested+=1`, `notify_new_request()` fires, auto-download gates pass, `_auto_download_wanted()` returns `[2,3]` (nothing reserved), qBit `add_torrent` succeeds, `update_download_handle` stores `wanted_volumes=[2,3]`, and because it is a bundle the selective-volume filter deselects volume 1 (`scheduler.py:3653-3780`).
9. `/releases` (default view) renders the series card: `all_volumes=[1,2,3]`, `missing_volumes=[2,3]` (volume 1 green/owned, 2–3 amber/wanted), `has_wanted=True` → WANTED ribbon, `has_prioritized=True` and `prioritized_priority=max(priority(1r0n),priority(Ushi))` if either is configured (`database.py:4423-4442`, `:4471-4488`); the accordion row shows a `Downloading` chip from `release_download_state` (`main.py:1314-1361`).
10. The 45 s inventory/routing pass and the 5-minute tag pass move finished files to staging (`scheduler.py:5252-5259`, `:5211-5219`); `download_status=COMPLETED` + `routed_at` flips the chip to `In staging`, and a later Calibre sync/reconcile rewrites `missing_volumes` to `[]` and status to `IN_CALIBRE` through `resolve_status` (`database.py:4380-4404`; worker persistence at `:3989-4002`).

### 3.24 Test evidence map (claims pinned by tests)

| Claim | Evidence |
|---|---|
| Status precedence matrix + poll-summary counts + unblacklist demotion | `tests/test_status_precedence.py:184-609` (12 tests) |
| Discovery/grouped/flat pagination math, per-card cap, endpoint keys, no auto-infinite-scroll wiring | `tests/test_releases_pagination.py:33-126` |
| Post-date normalization, blank-update safety, flat/grouped/discovery ordering, sort UI wiring | `tests/test_post_date_sorting.py:19-114` |
| LN detection (curly/ASCII), persistence, legacy backfill, list/discovery/count/search/wishlist hiding, maintenance cleanup | `tests/test_light_novel_release_filtering.py:38-135` |
| Multi-group parsing/set membership/priority ordering/dedupe signature/webhook payload/search by secondary group/UI loops | `tests/test_multi_group_prioritization.py:94-426` |
| Ignore lifecycle, blacklist exclusion, backfill start-page/resume persistence, template parity | `tests/test_releases_ignore_and_backfill_resume.py:18-203` |
| Backfill retry/stop-reason/politeness semantics (429 Retry-After, persistent 500 → error, 404 end, exact page budget, ≥1 s spacing) | `tests/test_backfill_pagination.py:1-402` |
| Download-state precedence matrix, `/state` endpoint, in-place polling wiring | `tests/test_release_download_state.py:24-108` |
| Cache-clear safety (active rows skipped, tracking/activity preserved, audit) | `tests/test_release_cache_clear.py:53-255` |
| Different-series re-key (detach, veto preservation, new title, 400/404) | `tests/test_different_series_ln_publisher.py:60-117` |
| Report-date/state/read-model invariants for feeds (async poll, scan notifications) | `tests/test_feed_poll_async.py`, `tests/test_feed_scan_activity_notifications.py` |
| Release-detection conformance (parser edge cases, bundle/chapter guards) | `tests/test_release_detection_conformance.py:1-873` |
| Unified search alias resolution (API-level only) | `tests/test_m2_features.py:690-735` |

### 3.25 Read-model field reference (what templates actually receive)

Grouped view — every item of `grouped_series` / `series_groups` (built at `database.py:4277-4508`, capped/decorated at `main.py:1369-1375`):

| Field | Meaning / source |
|---|---|
| `series_key` | `normalize_text(display_title)`; used as the accordion DOM id and the `series_key` param of `/api/releases/series_releases` (`main.py:1479-1487`) |
| `series_name` | display title (AniList authoritative canonical only, else stable/raw) — cosmetic |
| `stable_name` | stable matching key sent to Track/Backfill/Blacklist actions (`series_card_chunk.html:79-92`) |
| `romaji_name`, `raw_name` | alternate spelling for the subtitle / debugging |
| `cover_url` | metadata → release → tracked cover |
| `in_calibre`, `is_monitored`, `calibre_series_id`, `calibre_book_count`, `calibre_latest_index` | Calibre/tracked state |
| `total_volumes`, `status`, `synopsis` | metadata-driven series info (`status` is AniList's publishing status) |
| `releases[]` | up-to-10 release dicts (see below) |
| `release_total`, `releases_shown` | added by the API cap; drives "Show all N releases" |
| `all_volumes[]`, `missing_volumes[]` | union of parsed volumes / subset not owned (Calibre ∪ staging) |
| `releases_count`, `wanted_count`, `prioritized_count` | group aggregates |
| `is_complete`, `has_complete_release`, `is_publishing_finished` | complete-batch / AniList finished badges |
| `has_wanted`, `has_discovery`, `has_in_calibre`, `has_in_staging`, `has_prioritized`, `prioritized_priority` | status/priority aggregation and sort keys |

Release dict (in groups and flat rows; `_cap_group_releases`/`_decorate_release_state` add the rest): `id`, `guid`, `title`, `series_name` (stable), `display_series_name`/`canonical_series_name`, `romaji_name`, `stable_series_name` (grouped only), `release_group`, `release_groups[]`, `is_prioritized`, `prioritized_priority`, `status`, `download_status`, `download_state{state,label,progress_pct,speed_bps,error,can_download,can_retry,terminal}`, `is_bundle`, `is_complete`, `is_inferred`, `is_rerelease`, `is_superseded`, `volume_start`, `volume_end`, `volumes[]`, `missing_volumes[]`, `revision`, `published_at`, `updated_at`, `link`, `download_url`, `cover_url`, `source_feed_name`, `description`, `mangaupdates_url`, `tags_json`, `media_type`, `publisher`, `download_files_json`. Templates use `display_series_name or series_name` for all labels and `series_name` only for action payloads — the display/stable split is a hard contract (`series_card_chunk.html:75-92`, `flat_release_row_chunk.html:175`, `discovery.html:217-224`).

### 3.26 Concurrency, locking and atomicity

* **Feed poll**: `_feed_check_lock` (non-blocking acquire) serializes manual and interval polls (`scheduler.py:3352-3369`); status is published through `_feed_check_state_lock` (`:3372-3418`). A manual poll additionally creates a persisted background task whose monitor thread only observes the scheduler state (`main.py:1991-2009`).
* **Backfill**: a single boolean `is_backfilling` guards both the polite-historical worker and the series refresh worker, so they are mutually exclusive (`scheduler.py:4717-4721`, `:4761-4766`, `:4297-4298`); `stop_backfill_requested` is a cooperative flag checked per page and per release. Worker threads are daemon threads; no join is awaited by the API.
* **DB writes**: every mutation opens its own SQLite connection (WAL mode, `database.py:397-403`) and commits per operation; there is no multi-statement transaction spanning a poll row. This means a crash mid-poll leaves already-upserted rows committed (acceptable, next poll re-upserts by guid). `_grouped_cache` is a plain dict mutated from both the web threads and the worker thread without a lock — the 5 s TTL and full-clear invalidation are the only coordination, so a reader may briefly see a mixed-generation list (group dict identity is per build, so readers never see partial groups).
* **Upsert racing**: two concurrent writers of the same guid (poll + backfill) can interleave select/insert; `guid` UNIQUE makes the second insert raise, which is caught by the poll's per-feed try/except (`scheduler.py:3783-3789`) and retried next poll — no dedupe/upsert-on-conflict clause exists (`database.py:2463-2571`).
* **Startup order**: `main.py` lifespan only probes storage and `scheduler.start()` (`main.py:213-230`); the first worker action is `perform_initial_sync()` (Calibre sync + legacy re-key repair), followed by complete-series expansion and `reconcile_release_volume_state()` (`scheduler.py:5152-5173`), so the first feed poll may run against a partially synced Calibre context (it then falls back to DB tracked rows, `scheduler.py:3435-3443`).

---

## 4. Data model touched

### 4.1 `releases` (created `database.py:449-478`, upgraded in place `:481-546`)

Reads/writes by this domain: `id`, `guid` (UNIQUE NOT NULL; dedupe/upsert key and provider identity), `title`, `series_name` (stable matching key), `release_group`, `release_groups_json`, `description`, `mangaupdates_url`, `is_bundle`, `is_complete`, `volume_start`, `volume_end`, `volumes_json`, `missing_vols_json`, `download_url`, `link`, `cover_url`, `source_feed_name`, `status`, `download_status`, `torrent_hash`, `media_type`, `revision`, `publisher`, `inferred_volumes`, `is_light_novel`, `published_at`, `created_at`, `updated_at`, plus download-engine columns read for derived state (`downloader`, `downloader_id`, `downloader_started_at`, `download_wanted_json`, `download_error`, `download_attempts`, `torbox_progress_json`, `download_files_json`, `downloaded_at`, `routed_at`), and tag columns (`tags_json`, `tags_text`).

Invariants: `guid` unique (upsert is insert-or-update on guid, `database.py:2461-2571`); `status` ∈ the union of `RECOMPUTABLE_STATUSES`/`STICKY_STATUSES` plus `NEW` (and, historically, `DOWNLOADING/QUEUED/FAILED` can appear in `status` on legacy rows — the active guard checks both `status` and `download_status`, `database.py:1189-1192`); `volumes_json`/`missing_vols_json` are JSON lists of numbers (`[]` default); `published_at` is `""` or ISO-8601 UTC `…Z`; `is_light_novel` and `media_type` are both derived from the title only (never payload inspection); `release_group` is the primary component of `release_groups_json` and is re-derived from the title on startup (`database.py:603-624`); a complete release may have `volume_start=1` with `volumes_json=[]` until metadata resolves the count; `releases.series_name` is the stable matching key and must never be rewritten from AniList metadata (only by explicit user actions: track, different-series, blacklist/unblacklist, re-key repair).

**Column usage in this domain** (W = written by this domain, R = read for its behavior):

| Column | W | R | Written by / used for |
|---|---|---|---|
| `guid` | W | R | provider identity / upsert key (`database.py:2433-2571`); dedupe signature (`:194-200`) |
| `title` | W | R | parser input, searches, display, chapter/LN/complete heuristics |
| `series_name` | W | R | stable grouping/matching key; discovery partition; blacklist comparisons |
| `release_group`, `release_groups_json` | W | R | prioritized membership, badges, dedupe signature, search |
| `status` | W | R | views, wishlist, discovery exclusions, active-row guard, state derivation |
| `download_status` | (Downloads) | R | active-row guard, derived state, auto-download reservations |
| `volumes_json`, `volume_start`, `volume_end` | W | R | cards/rows, discovery volume-1 test, matcher input |
| `missing_vols_json` | W | R | wanted badges, download wanted volumes, reconciliation |
| `is_bundle`, `is_complete`, `inferred_volumes` | W | R | matching status, COMPLETED pseudo-filter, badge rendering |
| `published_at` | W | R | `sort=post_date` (flat, grouped, discovery) |
| `is_light_novel` | W | R | hidden-from-views guard in every list query |
| `media_type` | (one-shot passes) | R | `tracked_media_clause()` |
| `revision` | W | R | `is_rerelease`, supersede ordering, dedupe identity |
| `publisher` | W | R | quality badge in discovery (`discovery.html:128-132`) |
| `description`, `mangaupdates_url`, `cover_url` | W | R | discovery cards/enrichment, card covers |
| `link`, `download_url` | W | R | source links, download action, dedupe fallback |
| `source_feed_name` | W | R | "Feed" column in rows/cards |
| `tags_json`, `tags_text` | (tags refresh) | R | discovery tag filters + chips |
| `download_wanted_json`, `downloader*`, `torbox_progress_json`, `download_error` | (Downloads) | R | derived state + wanted-volume resolution |
| `routed_at`, `downloaded_at`, `download_files_json` | (Downloads) | R | `downloaded` → `in_staging` distinction |
| `created_at`, `updated_at` | R | R | default ordering, post-date fallback, display timestamps |

### 4.2 `series_tracking` (created `database.py:412-428`)

Read for grouping/matching (`name`, `is_monitored`, `book_count`, `latest_index`, `owned_volumes_json`, `calibre_series_id`, `cover_url`/`description`, tags). Written here only by: discovery Track (insert/upsert, `is_monitored=1` — `database.py:3330-3337`), blacklist (sets `is_monitored=0` — `database.py:2357`), and series refresh/metadata update (out of this domain's API). Invariant: `name` is UNIQUE and is the stable matching key that `releases.series_name` groups under.

### 4.3 `series_blacklist` (created `database.py:439-446`)

`id`, `name` (UNIQUE), `reason`, `created_at`. Written by `POST /api/blacklist`, `POST /api/discovery/blacklist`, and `DELETE /api/blacklist/{id}`; read by every tracked-view query (`NOT IN (SELECT lower(name) …)`), discovery, and `is_series_blacklisted()`. Invariant: adding a blacklist entry re-keys all releases of that exact name (case-insensitive) to `BLACKLISTED`; removing it demotes only `BLACKLISTED` rows of that name to `DISCOVERY`; deleting does **not** restore `series_tracking.is_monitored` (no code path re-monitors).

### 4.4 `prioritized_groups` (created `database.py:627-646`)

`id`, `name` (UNIQUE), `notes`, `priority` (default 1), `created_at`. Read by every list read (`_prioritized_priority_map`, SQL membership clause), by `is_group_prioritized`, and by the group-count subquery. Invariant: comparisons are case-insensitive set membership against `releases.release_group` and each element of `release_groups_json`; a group's `release_count` in `GET /api/prioritized/groups` counts *any* release whose set contains it and passes `tracked_media_clause` only (it does **not** apply the LN/blacklist/ignored exclusions of the views — see §7.3).

### 4.5 `feeds` (created `database.py:649-663`)

`id`, `name`, `feed_type`, `url`, `config_json` (`{}`), `enabled` (1), `auto_download` (1), `check_interval_minutes` (60; vestigial, REL-027), `last_checked`, `last_status` (`OK|ERROR`, NULL before first poll), `last_error`. Invariant: only `enabled` rows are polled; provider type must be one of the registry keys, otherwise the feed fails independently with `ERROR`; the stock seeded row is uniquely identified by `(name, url, feed_type='rss')` for mirror migration (`database.py:3390-3397`).

### 4.6 `app_settings` keys written/read by this domain

`nyaa_base_url` (also env-seeded `MANGATRAX_NYAA_BASE_URL`), `feed_check_interval_minutes`, `backfill_max_pages`, `backfill_delay_seconds`, `backfill_status`, `backfill_progress`, `backfill_last_page`, `auto_download`, `discard_unmarked_chapters`, `filter_non_manga`, `infer_collected_releases`, `media_filter_applied`, `jpegxl_filter_applied`, `ln_release_backfill_done`, `webhook_feed_scan_notifications`, `webhook_processed_notifications`, `discord_webhook_url`, `apprise_url`/`generic_webhook_url`, `app_timezone` (render filters), `staging_dir`, `downloads_dir`, `calibre_path`, `downloader_engine`, `torbox_*`, `qbit_*`, `log_*`, `activity_*`, `storage_health_json`. Values are TEXT; booleans are the strings `true|false` and are parsed leniently (`not in ("false","0","no","off")`).

### 4.7 Other tables touched

`manga_metadata_cache` (`search_term` PK; `canonical_title`, `romaji_title`, `cover_url`, `total_volumes`, `status`, `synopsis`, `source`, `genres_json`, `tags_json`) — read for grouping/discovery/display and volume-count inference; `mu:<lower name>` rows store MangaUpdates data (URL smuggled in `romaji_title`, synopsis in `synopsis`). `activity_logs` — every event listed in §3.18. `background_tasks` — polls and backfills create persisted tasks (`database.py:888-910`; `main.py:2003-2009`, `:4018-4045`). `series_alias_overrides` — consulted first by `resolve_series_identity` (`scheduler.py:713-733`; schema `database.py:872-886`). `manga_metadata_cache`/`series_tracking`/`releases` tags columns for discovery tag filtering.

---

## 5. Configuration & tunables

### 5.1 Application settings (DB-backed; defaults from `database.py:725-782`, `:788-837`)

| Key | Default | Effect in this domain |
|-----|---------|----------------------|
| `nyaa_base_url` | env `MANGATRAX_NYAA_BASE_URL` or `https://nyaa.si` | Mirror/proxy base for all Nyaa URLs; saving repoints only the stock seeded feed (`main.py:4254-4262`). |
| `feed_check_interval_minutes` | `60` | Interval poll cadence on the worker thread (`scheduler.py:5190`, `:5197-5200`). Global; the per-feed column is ignored. |
| `backfill_max_pages` | `15` | Page budget when `/api/backfill/start` omits `max_pages` (`scheduler.py:4776-4780`). `0` means unlimited. |
| `backfill_delay_seconds` | `2.0` | Per-page delay when the request omits `delay`; effective page spacing is `max(1.0, delay)` (`scheduler.py:4782-4786`; `nyaa.py:274-276`). |
| `auto_download` | `true` | Global gate for auto-download of new/transitioned requests (`scheduler.py:3450`, `:3677`, `:3685`). |
| `auto_tag` | `true` | Enables the 5-minute staging tag/route pass (companion to downloads; also runs after manual downloads). |
| `discard_unmarked_chapters` | env `MANGATRAX_DISCARD_UNMARKED_CHAPTERS`, else `true` | Enables the context-guarded marker-less chapter discard (`scheduler.py:263-266`, `:3447`, `:3542`, `:3947`). Explicit `Ch.` markers are always discarded regardless. |
| `filter_non_manga` | env `MANGATRAX_FILTER_NON_MANGA`, else `true` | Gates the one-shot media re-typing pass (`database.py:1039-1041`); the visibility filter itself is unconditional. |
| `infer_collected_releases` | env `MANGATRAX_INFER_COLLECTED_RELEASES`, else `true` | Enables collected-batch volume inference (`scheduler.py:408-411`, `:509-510`). |
| `webhook_feed_scan_notifications` | `true` | Enables automatic feed-scan webhooks; suppressed when no activity (`scheduler.py:1360-1368`). |
| `webhook_processed_notifications` | `true` | Processed-batch webhooks (Downloads domain companion). |
| `discord_webhook_url`, `apprise_url`/`generic_webhook_url` | `""` | Notification destinations (`scheduler.py:979-982`); test via `POST /api/settings/test-webhook`. |
| `app_timezone` | `""` | Jinja `localtime`/`localdate` filters used by release/feed timestamps (`main.py:289-298`). |
| `staging_dir` | env `STAGING_DIR` or `/staging` | Ownership ("staged counts as owned") and the staged-volume reservation scan. |
| `downloads_dir` | env `DOWNLOADS_DIR` or `/downloads` | `add_torrent` savepath for manual/auto downloads (`scheduler.py:1321-1329`). |
| `calibre_path` | env `CALIBRE_DIR` or `/calibre` | Calibre context for matching; if unavailable the DB tracked rows are used (`scheduler.py:3435-3443`). |
| `downloader_engine` | env `MANGATRAX_DOWNLOADER_ENGINE` or `qbit` | Chooses qBittorrent vs Torbox in the download action and auto-download. |
| `torbox_enabled`, `torbox_api_key`, `torbox_*` | off / `""` / various | Torbox engine readiness and behaviour (Downloads domain detail). |
| `qbit_enabled`, `qbit_url`, `qbit_username`, `qbit_password` | on / localhost defaults | Engine readiness for auto-download. **Note:** `qbit_category`/`qbit_tags` exist in settings but the release download paths hardcode `manga`/`mangatrax` (`main.py:3792-3797`, `scheduler.py:3745-3750`). |
| `media_filter_applied`, `jpegxl_filter_applied`, `ln_release_backfill_done` | `""` | One-shot migration markers (`database.py:833-837`, `:918-938`, `:1036-1041`, `:1117-1120`). |
| `backfill_status`, `backfill_progress`, `backfill_last_page` | `idle` / `Idle` / none | Backfill state machine surfaced by `/api/backfill/status` and the global banner. |
| `log_to_file`, `log_file_max_mb`, `log_file_backups` | `false`/`10`/`5` | Rotating file logging (`main.py:114-165`). |
| `activity_retention_days`, `activity_max_rows` | `90` / `50000` | Activity-log pruning (audit trail retention for this domain's events). |

### 5.2 Environment variables

`DATA_DIR` (DB location, default `<app>/../data`), `CALIBRE_DIR`, `DOWNLOADS_DIR`, `STAGING_DIR`, `PORT`, `MANGATRAX_NYAA_BASE_URL`, `MANGATRAX_DISCARD_UNMARKED_CHAPTERS`, `MANGATRAX_FILTER_NON_MANGA`, `MANGATRAX_INFER_COLLECTED_RELEASES`, `MANGATRAX_DOWNLOADER_ENGINE`, `MANGATRAX_TORBOX_*`, `MANGATRAX_LOG_*`, `MANGATRAX_ACTIVITY_*`, `MANGATRAX_PATH_ROOTS` (optional settings path allow-list, `main.py:328-337`), `MANGATRAX_AUTH_MODE`, `MANGATRAX_PUBLIC_URL`, `MANGATRAX_SESSION_SECRET`, `MANGATRAX_BASIC_USER/PASS`, `MANGATRAX_OIDC_*`, `MANGATRAX_CSRF_PROTECT` (default on), `MANGATRAX_DEBUG_ACTIONS` (default off), `MANGATRAX_RECONCILE_BATCH_SIZE` (default 2000, reconcile pass bound), `QBIT_URL/QBIT_USER/QBIT_PASS`.

### 5.3 Code constants

`_SERIES_CARD_RELEASE_CAP = 10` (`main.py:1301`); cards `limit` default 8 / max 50; flat rows default 50 / max 200; `series_releases` default 10 / max 50; grouped-cache TTL **5.0 s** (`database.py:4028`); substring fallback thresholds `len ≥ 6`, ratio `≥ 0.70` (`database.py:4226-4232`); `MAX_BUNDLE_SPAN = 1000` (`parser.py:125`); `INFERENCE_MAX_VOLUMES = 300`, `INFERENCE_MIN_NAME_SIMILARITY = 0.85`, finished statuses `FINISHED|PUBLISHED` (`scheduler.py:404-406`); Nyaa `NYAA_FETCH_ATTEMPTS=3`, `NYAA_BACKOFF_SECONDS=(1.0,3.0)`, `NYAA_RETRY_JITTER_SECONDS=0.5`, `NYAA_MAX_RETRY_AFTER_SECONDS=30`, `NYAA_MIN_PAGE_INTERVAL_SECONDS=1.0`, `NYAA_HTML_TIMEOUT_SECONDS=15`, category `3_1` (`nyaa.py:24-38`); RSS timeout 15 s (`rss.py:12`); discovery page size default 24 / 6…120 (`main.py:1574-1575`); status ranks and sticky/veto sets (`database.py:247-261`); media type tuples (`database.py:59-85`); `_RELEASE_STATE_LABELS` (`main.py:1303-1311`); BASE-URL defaults (`nyaa_source.py:39`); seeded feed identity (`database.py:21-22`); backfill page ranges 1…5000 max_pages, 0…300 delay (`schemas.py:974-979`); feed interval 5…1440 (`schemas.py:515`).

### 5.4 Where each tunable is edited (UI mapping)

| Control (settings page) | Setting key | Line |
|---|---|---|
| "Auto-download new releases" checkbox | `auto_download` | `settings.html:278-280` |
| "Discard Marker-less Chapter Releases" checkbox | `discard_unmarked_chapters` | `settings.html:302-304` |
| "Hide Non-Manga Types" checkbox | `filter_non_manga` | `settings.html:310-312` |
| "Infer Collected-Batch Volumes" checkbox | `infer_collected_releases` | `settings.html:318-320` |
| "Feed Check Interval" number input (5–1440) | `feed_check_interval_minutes` | `settings.html:341` |
| "Backfill Max Pages" (0–1000) | `backfill_max_pages` | `settings.html:345` |
| "Backfill Delay" (0.5–30 s) | `backfill_delay_seconds` | `settings.html:350` |
| "Nyaa Base URL" text input | `nyaa_base_url` | `settings.html:376` |
| Prioritized groups list + link to `/prioritized` | `prioritized_groups` (table) | `settings.html:457-470` |
| Release-cache preview + clear | `/api/maintenance/releases-cache`, `/api/maintenance/clear-releases` | `settings.html:1200-1240` |
| Remove/Add prioritized group (settings variant) | `POST/DELETE /api/prioritized/groups` | `settings.html:1500-1540` |
| Webhook URLs / notification toggles | `discord_webhook_url`, `apprise_url`, `webhook_*_notifications` | settings page webhook card (`schemas.py:667-670`) |

The per-feed interval input lives in the Add-feed modal (`feeds.html:145-148`) and is the vestigial tunable of REL-027; the effective cadence is the settings "Feed Check Interval".

---

## 6. Integration contracts

### 6.1 Consumed from the Series domain

* `series_tracking` rows (stable names, `is_monitored`, `owned_volumes_json`, `calibre_series_id`) — used by grouping (Calibre/monitored badges, discovery exclusion), identity resolution, and the staged-ownership union. Contract: `series_tracking.name` is the authoritative stable key; grouping must never mutate it.
* `CalibreMangaScanner`/`MangaCalibreMatcher` (`backend/app/calibre/*`) — `check_release()` returns exactly one of `IN_CALIBRE | IN_STAGING | REQUESTED (or NEW when auto_request_monitored=False) | DISCOVERY | NEW`, plus `missing_volumes` and `matched_series` (`calibre/matcher.py:133-214`). This domain depends on that status vocabulary and on combined ownership = Calibre-owned ∪ staged (`matcher.py:127-131`).
* `scheduler.resolve_series_identity()` (`scheduler.py:622-788`) — returns `{raw_name, stable_name, display_name, source, meta, alt_names, anilist_id, calibre_match}`; every release/track path in this domain uses it. Contract: `stable_name` is what gets persisted; `display_name` is cosmetic only.
* `/api/calibre/series` (used by the different-series modal) and `_calibre_series_snapshot` fallbacks (`main.py:2322-2325`).
* `POST /api/series/backfill` and `/api/series/{id}/backfill` are in the Series namespace but triggered from this domain's cards; their task/progress contract (`background_tasks` + `backfill_progress`) is shared.

### 6.2 Consumed from / exposed to the Downloads domain

* Consumes: `scheduler.get_qbit_client()`, `scheduler.submit_torbox_download()`, `scheduler.resolve_release_missing_volumes()`, `db.update_download_handle()`, `scheduler.process_downloads_to_staging()`; `download_status` vocabulary `IDLE | PENDING_DEDUPE | QUEUED | DOWNLOADING | COMPLETED | FAILED`, which this domain renders and uses for auto-download dedupe; `releases.downloader_id/downloader_started_at/torbox_progress_json/download_files_json/downloaded_at/routed_at`.
* Exposes: every row created/updated by ingestion (guid, title, series key, volumes, missing volumes, download_url/link, status, `download_wanted_json` after submission) — the Downloads domain routes them into staging and marks completion; this domain reads `download_status`/`routed_at` back to render state. Ordering guarantee: a release row exists (with missing volumes computed) before any automatic submission; `status` is settled by `resolve_status` before the auto-download decision (`scheduler.py:3653-3685`).
* Also exposes: `/api/downloads/auto-download/diagnostics` reads the same decision audit trail (`scheduler.py:1128-1246`), and `POST /api/downloads/...` companions (`clear-failed` etc.) do not touch release status.

### 6.3 Consumed from / exposed to the Platform domain

* Consumes: auth middleware actor ContextVar (audit actor), CSRF/Origin enforcement, `MANGATRAX_DEBUG_ACTIONS` gating, `background_tasks.run_in_background`/`TaskReporter`, `security.validate_*_url`/`safe_url_filter`/`ensure_path_within_roots`, `timezone_util` filters, `cover_cache.cache_sync`, `notifier` (Discord/generic webhooks), `scheduler.start()/stop()` worker lifecycle.
* Exposes: `/api/feeds/check|status`, `/api/backfill/status` (used by the global banner on every page), `/api/tasks` re-attachment for `feeds:poll` and `backfill:series` tasks, activity log events, and the settings keys listed in §5.1. Ordering guarantee: page shells never block on network work; heavy polling/backfill runs on daemon threads with in-memory or DB-persisted progress.

### 6.4 Consumed from / exposed to the UI domain

* Exposes to UI: the five HTML pages and the HTML-fragment JSON endpoints (`html` field) plus the JSON contracts `{status, offset, count, total?, has_more, html}`, `/api/releases/{id}/state` (`{release_id,state,label,progress_pct,speed_bps,error,can_download,can_retry,terminal}`), feed CRUD JSON, prioritized group JSON, discovery track/blacklist/fetch-info JSON, backfill status `{running,status,progress,last_page}`, feed check status `{running,started_at,finished_at,result}`.
* Consumes from UI: only `search`, `status`, `view`, `sort`, `offset`, `limit`, `page`, `page_size`, `q`, `include_tags`, `exclude_tags`, `tag_match` query params and the JSON bodies described in §3; no local storage or cookies are used by these pages.

---

## 7. UI touchpoints & mobile behavior (summary)

Per page (controls, state, known gaps — deep UI analysis belongs to EXPERT-5):

**`/releases`** (`releases.html`). Controls: view switch (2 links), 7 status pills (links), debounced search input, sort select (3 options), per-card Releases accordion, Track/Backfill/Blacklist card buttons, per-release `Different series` / `Download` / Ignore-Unignore / open-link, "Show all N releases" per card, Load More (view-specific + sticky floating), showing/remaining counters, "Clear filters" empty state, download-state chips. State: `currentStatus` (`const` from server), `currentView`, `currentSearch`, `currentSort`, `seriesOffset/flatOffset` (first batch starts at 0 and the first fetch happens on `DOMContentLoaded`), `hasMore*`, per-release poll timers (1.5 s) that stop on terminal states. Gaps: (a) the sticky Load More button is `fixed bottom-5 right-5` (`releases.html:179`) and the global Tasks widget is `fixed bottom-4 right-4` (`base.html:390`) — they can overlap on short/mobile viewports; (b) `filterReleases()` references a `.series-card-deferred` class that the card partial never emits, so the "hide deferred" branch is dead and client-side filtering also hides rows that would come from later batches (server paging still works); (c) the flat table is horizontally scrollable on mobile (6 columns), no card fallback; (d) after `Show all`, the newly appended rows are not re-run through the state-poll machinery unless the user clicks Download; (e) the whole page depends on JS — with JS off only the skeleton renders (server-rendered lists are always empty).

**`/prioritized`** (`prioritized.html`). Controls: same view switch/status pills; inline VIP-group manager (add form with name+notes, pills with release_count and remove buttons); client-side filter input (no server search); Load More + sticky button; same card/row partials (amber theme). State: `seriesOffset/flatOffset`; **no sort control**, and its APIs receive neither `search` nor `sort` (so reloading with a sort param does nothing); flat view starts with `total=0` so the counter is relative until the first response. Gaps: no per-release "Show all" difference vs Releases; the inline group add form does not expose `priority` (API default 1); removal requires a `confirm()` (blocking dialog).

**`/discovery`** (`discovery.html`). Controls: two tabs (Discovered / Blacklisted) with counts, header "Polite Backfill" button (opens the global modal), GET search form (`q`), tag picker (query + Include/Exclude select + Apply/Clear + chips), sort select (4 options), per-page selector (12/24/48/96), pagination (First/Prev/page indicator/Next/Last), per-card tag chips (click a non-prioritized group chip to prioritize it), Track/Blacklist buttons, MangaUpdates link, per-card Fetch (synopsis), cover lightbox; Blacklist tab: manual add form (name+reason), search filter, table with Unblock/Restore. State: server-rendered page + URL params (`page`, `page_size`, `q`, `include_tags`, `exclude_tags`, `tag_match`, `sort`); client-side `filterDiscovery`/`filterBlacklist` narrow the current page only. Gaps (factual): pagination links carry only `page`, `page_size`, `q` — tag filters and `sort` are dropped (`discovery.html:239-273`); the GET search form similarly drops tags/sort (`:45-54`); `tag_match=all` has no UI control (hidden input from the URL only, `:73-74`); `relevance` sort silently behaves like `newest` without include-tags; card grid is 1/2/3 columns with long titles clamped (`line-clamp-3`) and a horizontal-scrolling tag row.

**`/feeds`** (`feeds.html`). Controls: Poll Feeds button (async + toasts + reload), Add Feed button/modal (name, type rss|html, interval, URL, auto-download checkbox, "Test Feed Live"), per-row enable/disable toggle, delete (confirm), last-check/error display. State: none client-side except modal; poll state comes from the shared helpers in `base.html`. Gaps: 8-column table with `overflow-x-auto` (mobile horizontal scroll); the per-feed interval shown is not the effective cadence (REL-027); there is no edit-feed action (delete + re-add only); `last_error` is only visible as a `title` tooltip on the word "Error"; the add modal offers only rss/html though the API accepts Nyaa types.

**Control map (page → control → call → state).**

| Page | Control | Call | State / side effect |
|---|---|---|---|
| Releases | view switch (2 anchors) | full navigation `/releases?view=…` | server re-renders shell; `currentView` const |
| Releases | status pills (7 anchors) | full navigation `/releases?view=…&status=…` | `currentStatus` const |
| Releases | search input | `GET /api/releases/cards\|flat_rows?search=` (300 ms debounce) | `currentSearch`, pagination reset, client-side hide |
| Releases | sort select | `GET …cards\|flat_rows?sort=` + `history.replaceState` | `currentSort` |
| Releases | Load More / sticky button | next `cards`/`flat_rows` page | `seriesOffset`/`flatOffset`, `hasMore*`, counters |
| Releases | "Show all N releases" | `GET /api/releases/series_releases` | appends rows into the card; button removed at end |
| Releases | card Track / Backfill / Blacklist | `/api/discovery/track`, `/api/series/backfill`, `/api/blacklist` | reload after track/blacklist; backfill banner |
| Releases | per-row Download | `POST /api/releases/{id}/download` | chip + `_pollReleaseState` every 1.5 s (`/state`) |
| Releases | per-row ignore/unignore | `/api/releases/{id}/ignore\|unignore` | row dims/disables or reload (`base.html:852-893`) |
| Releases | Different series | `GET /api/calibre/series`, `POST /api/releases/{id}/different-series` | row removed on success, summaries refreshed |
| Releases | Quick-prioritize group chip | `POST /api/prioritized/groups` | reload after 1 s |
| Prioritized | group add form / remove pills | `POST /api/prioritized/groups`, `DELETE …/{id}` | reload after 1 s; removal confirms |
| Prioritized | Load More / sticky | `GET /api/prioritized/cards\|flat_rows` | own offsets; flat total unknown until response |
| Discovery | search form (GET) | `/discovery?q=…&page_size=…` | server render; **tags/sort dropped** |
| Discovery | tag picker Apply/Clear | `/discovery?include_tags=&exclude_tags=&tag_match=&sort=` | server render; preserves `q` |
| Discovery | sort select | `/discovery?sort=…` (full URL params preserved) | server render |
| Discovery | per-page select | `/discovery?page_size=…&page=1` | server render |
| Discovery | First/Prev/Next/Last | `/discovery?page=…&page_size=…[&q=…]` | server render; **tags/sort dropped** |
| Discovery | card Track / Blacklist | `/api/discovery/track`, `/api/discovery/blacklist` | reload after 600 ms |
| Discovery | card Fetch | `POST /api/discovery/{id}/fetch-info` | in-place synopsis + MU link update |
| Discovery | VIP group chip (non-prioritized) | `POST /api/prioritized/groups` | reload after 600 ms |
| Discovery | Polite Backfill | `openBackfillModal()` → `POST /api/backfill/start` | global banner poll every 2.5 s |
| Discovery | Blacklist tab add / search / restore | `/api/blacklist` POST/DELETE; client filter | reload after 600 ms |
| Feeds | Poll Feeds | `POST /api/feeds/check` then 1.5 s `/status` polling (15 min cap) | toasts + reload |
| Feeds | Add Feed / Test Feed Live / Save | `POST /api/feeds/test`, `POST /api/feeds` | modal, reload after 800 ms |
| Feeds | enable/disable / delete | `POST /api/feeds/{id}/toggle`, `DELETE /api/feeds/{id}` | reload after 800 ms |
| All pages | global backfill banner | `GET /api/backfill/status` (2.5 s while running) | banner + completion toast |
| All pages | Calibre-sync banner | `GET /api/calibre/status` (3 s while running) | banner + reload on completion |
| All pages | Tasks panel | `GET /api/tasks`, `/api/tasks/{id}` | persisted task list |

**Mobile/desktop gap matrix (factual, for EXPERT-5 to elaborate).**

| Area | Desktop | Mobile / narrow |
|---|---|---|
| Releases/Prioritized filters | two rows (view switch + 7 pills) fit in the header row | `flex-wrap` header; pills wrap into 2–3 lines above the search bar; search + sort share one row and the sort select can squeeze the input |
| Releases flat view | 6-column table | horizontal scroll inside `overflow-x-auto` (`releases.html:138`); no sticky first column |
| Series cards | cover left, content right (`flex-col md:flex-row`) | cover centred (`mx-auto`), text below; volume pills wrap; action buttons wrap under the heading |
| Card action row | Track/Backfill/Blacklist labels visible | labels hidden at `<sm` (`hidden sm:inline`), icon-only buttons |
| Sticky Load More | bottom-right floating button | same, collides with the global Tasks button (both bottom-right) and can cover the last row |
| Discovery grid | 3 columns (lg) / 2 (md) | 1 column; long titles clamp to 3 lines, tag chips wrap, description clamps to 3 lines with click-to-expand |
| Discovery tag picker | inline query/mode/sort/Apply/Clear row | wraps into multiple lines; autocomplete menu absolutely positioned over content |
| Discovery pagination | full First/Prev/…/Last row | wraps under the "Showing X to Y" text |
| Feeds table | 8 columns | horizontal scroll; "Feed Name"/URL truncate (`max-w-xs truncate`) |
| Modals (backfill, different-series, add-feed) | centred dialogs | full-width dialogs with internal scroll; only the nav menu and image lightbox close on Escape, the others require clicking Cancel/× |
| Nav | grouped dropdowns | hamburger with grouped list, min 44 px targets (`base.html:220-273`) |

**Shared/mobile.** All four pages share the top nav (desktop dropdowns, mobile hamburger), toasts bottom-right (same corner as the Tasks widget and the sticky Load More), the global backfill banner above content on every page, and the Calibre-sync banner. Only Discovery has true server-side pagination; Releases/Prioritized use explicit "Load More" (no infinite scroll — deliberately removed, asserted by `tests/test_releases_pagination.py:100-126`). No keyboard-focus management exists for the load-more/sticky controls, and modals are plain class-toggled divs (Escape handling exists only for the nav menu and image lightbox, not for the backfill/different-series/add-feed modals).

---

## 8. Open questions / ambiguities

1. **Grouped cache is mutated by the endpoint (release_total under-reporting).** `_cap_group_releases()` mutates the group dicts in place (`main.py:1369-1375`: sets `release_total`, `releases_shown`, replaces `group["releases"]` with the capped list), and `_build_grouped_releases` stores `list(result_groups)` — a shallow copy sharing the same dicts — in `self._grouped_cache` (`database.py:4525-4526`). A second request with the same cache key inside the 5 s TTL therefore sees already-capped group release lists, so `release_total` becomes ≤10 even when the series has more, and the "Show all N releases" affordance under-reports/disappears. The documented read-only intent (`database.py:3974-3977`) does not cover this in-memory mutation. Not covered by tests (pagination tests call the DB layer directly, `tests/test_releases_pagination.py:49-76`).
2. **Flat-rows dedupe is provably inert, but its interaction with pagination is unsafe if it ever fires.** `release_dedupe_signature()` returns `guid` when present (`database.py:194-200`) and `guid` is `UNIQUE NOT NULL` (`database.py:452`), so `deduped` equals `releases` for all normal rows; `has_more` is computed from the pre-dedupe length (`main.py:1456`) while the client advances `offset` by `data.count` (post-dedupe, `releases.html:448`). If a legacy empty-`guid` row ever made dedupe drop a row, the next page would overlap and the loop could re-append duplicates.
3. **`release_count` on prioritized groups uses a weaker filter than the views.** `get_prioritized_groups()` counts releases matching the group with only `tracked_media_clause()` (`database.py:4560-4566`), whereas the prioritized/list views also exclude `is_light_novel=1`, `BLACKLISTED`/`IGNORED` and blacklisted series (`database.py:4628-4656`, `:3000`). The badge can therefore count rows the user can never see.
4. **Discovery filters are not preserved by pagination or the search form.** Pagination links rebuild `/discovery?page=…&page_size=…[&q=…]` (`discovery.html:239-273`) and the GET form has only `q` + `page_size` (`:45-54`); `include_tags`, `exclude_tags`, `tag_match` and `sort` are dropped on every page change/search submit, silently resetting the tag filter. Only `TagPicker.apply()` and `changeDiscoverySort()` preserve the full query string (`:567-571`, `:621-630`).
5. **`sort=relevance` without include-tags is a silent no-op.** The relevance order (`tag_match_count DESC`) is only built when include tags exist (`database.py:3165-3173`); otherwise the `else` branch is `updated_at DESC, id DESC` — i.e. identical to `newest`, with no UI indication.
6. **Per-feed intervals are displayed but never honored** (REL-027). Files: column/default `database.py:658`, write `database.py:3428-3434`, display `feeds.html:84-86`, scheduler interval source `scheduler.py:5189-5200`, enabled-only skip `:3455-3457`. A user setting 5 minutes expects 5-minute polling and gets up to 60.
7. **`POST /api/releases/{id}/status` has no in-repo caller and a reduced enum** (`schemas.py:555-558` omits `IN_STAGING` and `NEW`). It is the only API that can move a release to `BLACKLISTED` per-row without blacklisting the whole series; unclear whether it is a supported integration surface or legacy. Tests only exercise `db.update_release_status`, not the endpoint.
8. **Seeded prioritized groups differ between first-init and reset.** `init_db()` seeds `danke-Empire`, `1R0N`, `Lu自主规制`, `a-sloth` (`database.py:640-646`), while `reset_all_data()` seeds `danke-Empire`, `LuCirAn`, `1stKind`, `TenmaLand`, `Stick` (`database.py:4702-4709`). The `/prioritized` copy text references the init set (`prioritized.html:16`). Behavior after "reset all data" therefore differs from a fresh install.
9. **"Show all" on `/prioritized` is not prioritized-only.** `/api/releases/series_releases` re-derives the group without `prioritized_only` (`main.py:1479`), so expanding a card on the prioritized page reveals every release of that series, including non-prioritized ones, and the card's `release_total` (computed with `prioritized_only=True`) can disagree with the rows shown.
10. **qBittorrent category/tags settings are ignored by release downloads.** `qbit_category`/`qbit_tags` defaults exist (`database.py:734-735`) but both the manual (`main.py:3792-3797`) and automatic (`scheduler.py:3745-3750`) `add_torrent` calls hardcode `"manga"`/`"mangatrax"`; the settings are written but have no effect on release-download submission (`qbit_category` is only read to filter the live downloader status list, `main.py:606`; `qbit_tags` is stored but never applied). (Cross-domain: Downloads expert should confirm whether any other path uses them.)
11. **Releases never server-render data.** `releases_view`/`prioritized_view` always pass empty lists (`main.py:1195-1207`, `:1220-1231`), making the templates' `{% if grouped_series %}`/`{% if releases %}` branches unreachable in production (they are exercised only by direct template renders). This is a JS-only page by design, but it means no-JS clients see skeletons forever.
12. **`.series-card-deferred` contract mismatch.** `releases.html:504` and `:521`, `prioritized.html:456`/`:473` special-case `series-card-deferred`/`flat-row-deferred`, but `series_card_chunk.html` never adds those classes (it does use `.series-release-row`, `data-release-id`); only other pages/keywords (`test_performance_and_lazy_loading.py:110-118`) assert the string exists. The deferred-hiding branch is therefore dead.
13. **Discovery includes `REQUESTED` rows when the series is not monitored.** The discovery predicate excludes only `IN_CALIBRE|IN_STAGING|BLACKLISTED|IGNORED` and monitored tracked names (`database.py:3129-3131`, `:3196-3198`). A row made `REQUESTED` via the API-only status endpoint (REL-020) still appears in Discovery, where it can be "Tracked" again or blacklisted; is that intended?
14. **`format`/`media-type` typing vs light-novel flag can diverge.** `upsert_release` re-derives `is_light_novel` from the title on every write (`database.py:2436-2440`) but never updates `media_type`; a row typed `light_novel` whose title no longer matches keeps `is_light_novel=0` and is hidden by the media clause, while a row with `is_light_novel=1` but `media_type='unknown'` is hidden by the LN clause. There is no single "is this manga" predicate — two independent filters must agree.

---

## 9. Consensus checklist

One line per feature ID; each is intended to be voted AGREE/DISAGREE against the code as-is.

- REL-001 — `/releases` renders a shell with status filters and a view switch and always lazy-loads its rows via the fragment APIs (server-rendered lists are empty by design).
- REL-002 — Releases are grouped by a *display* series key derived from metadata/Calibre matching, while actions use the separate *stable* series name; complete-batch expansion and Calibre/staging ownership reconciliation are computed in memory on this read path and are not persisted by it.
- REL-003 — The flat view lists release rows in a 6-column table with server-side status/search/sort filtering and HTML-fragment pagination.
- REL-004 — Paging is explicit "Load More" (8 series / 50 rows per batch), each series card shows at most 10 releases with a "Show all N" expander, and the remaining counters come from a server-computed total.
- REL-005 — Releases search is debounced 300 ms, sent server-side (`title|series_name|release_group` for flat; display/stable/raw/romaji/synopsis for groups), and also filters already-rendered DOM client-side.
- REL-006 — Three sort modes exist (`updated` status-tier+recency, `post_date` normalized provider date with created/updated fallback, `series` A–Z); within a card, releases are finally ordered by supersede flag then revision.
- REL-007 — `/prioritized` is the same two-view UI restricted to series having at least one release from a prioritized group, plus an inline group manager.
- REL-008 — Prioritized groups support list/add(notes, priority)/delete; add upserts by case-sensitive name; every change invalidates the grouped cache.
- REL-009 — Prioritized matching is case-insensitive set membership over `release_groups_json` (any group qualifies), and ordering/priority uses the highest-priority matching group from that set.
- REL-010 — `/api/prioritized/flat_rows` returns prioritized rows without `search`, `sort` or `total`, and reports `has_more` as `len(rows)==limit`.
- REL-011 — `/discovery` is server-rendered with page-size 12/24/48/96 (default 24), clamped page numbers, a page indicator, and a separate Blacklisted table tab.
- REL-012 — Discovery shows deduped volume-1/unnumbered/(complete) releases that are not in Calibre/in staging, not monitored-tracked, not blacklisted and not ignored, preferring rows with cover/description/MangaUpdates data.
- REL-013 — Discovery tag filtering is server-side include/exclude over a `"|tag|"`-denormalized text column with any/all include semantics and an autocomplete backed by `/api/tags`; the UI loses the filters when paginating.
- REL-014 — Discovery sort offers newest/post_date/series/relevance, where relevance only differs when include-tags exist, and pagination links omit the sort parameter.
- REL-015 — Tracking a discovered series resolves the stable key (Calibre/tracked name beats AniList title), creates/updates `series_tracking`, sets the release(s) to `REQUESTED` verbatim, re-keys legacy names, and notifies.
- REL-016 — Blacklisting upserts the series, sets all its releases to `BLACKLISTED`, unmonitors it; unblacklisting deletes the entry and demotes only `BLACKLISTED` rows to `DISCOVERY`.
- REL-017 — Fetch-info pulls MangaUpdates synopsis/URL/cover on demand, caches them, and returns the values without changing status.
- REL-018 — Automated status writes go through `resolve_status`: a blank incoming value never clears, IGNORED/BLACKLISTED vetoes are absolute, incoming IN_CALIBRE always wins and IN_CALIBRE is never downgraded to IN_STAGING; otherwise the higher rank wins, and an equal-rank incoming REQUESTED can replace a stored IN_CALIBRE. Explicit user and support/repair paths write their target status deliberately.
- REL-019 — Ignore writes `IGNORED` verbatim and Unignore writes `REQUESTED` verbatim (bypassing precedence), both with audit events.
- REL-020 — `POST /api/releases/{id}/status` exists with a 5-value enum and verbatim writes but has no UI caller in this repository.
- REL-021 — A release can be manually sent to the configured engine; derived state (failed/in_calibre/in_staging/downloading/waiting_inflight/downloaded/not_downloaded) gates the button and is polled in place every 1.5 s; IN_STAGING returns 409.
- REL-022 — "Different series" re-keys exactly one release to a chosen Calibre series, a typed title, or a detach-to-Discovery target, preserving user-veto statuses, without creating a global alias.
- REL-023 — `/feeds` supports adding RSS/HTML feeds (name, URL, interval, auto-download), live-testing a URL, toggling enabled state, deleting, and shows last check status/error.
- REL-024 — Feed type selects a provider from a fixed registry (rss, html/html_scraper, nyaa/nyaa_scraper/nyaa_manga); unknown types fail only that feed; providers raise `ProviderFetchError` when the source cannot be fetched or parsed, except that RSS/HTML providers return an empty list when their configured URL is rejected by the SSRF guard.
- REL-025 — The Nyaa crawler paginates `?f=0&c=3_1&s=id&o=desc&p=N`, retries transient failures (including empty response bodies) with backoff/Retry-After, spaces page fetches by at least 1 s, treats only an HTTP 404 or a parsed page with zero torrent rows as genuine end-of-results, and records an explicit stop reason.
- REL-026 — Feed polling is serialized by a lock, manual polls run on a daemon thread with a status endpoint and a persisted background task, interval polls run on the worker thread, and failures are per-feed rather than global.
- REL-027 — The per-feed `check_interval_minutes` value is stored and displayed but never used; all enabled feeds poll on the global `feed_check_interval_minutes`.
- REL-028 — Explicit chapter-only titles, light novels and JPEG XL releases are discarded before storage; JPEG XL discards and scheduler-side chapter discards are audited, while provider-level chapter/LN filtering is silent.
- REL-029 — A marker-less trailing chapter number is discarded only when the title-minus-number names an already-known series, with "Mob Psycho 100"-style numbered series protected and every discard audited.
- REL-030 — The parser extracts series key, volume sets/ranges, complete flag, ordered release-group set, authors, publisher, revision and chapter diagnostics from a release title, and ignores format/publisher words as groups.
- REL-031 — Year-range collected batches are expanded to a full volume set only from authoritative metadata that reports a finished count for that same series; otherwise they stay volume-less, visible and retried.
- REL-032 — Complete-series expansion and missing-volume/status reconciliation happen in memory on reads and are persisted only by the worker/maintenance pass, using staged volumes as owned.
- REL-033 — Light-novel rows are persisted (`is_light_novel`) and excluded from releases lists, counts, grouped views, discovery, search and wishlist, with a separate audit/ignore/delete maintenance tool.
- REL-034 — Media typing (`media_type`) marks non-manga/jpegxl rows without deleting them or touching status, and a single tracked-media predicate hides them everywhere.
- REL-035 — Auto-download fires only on new/transitioned REQUESTED rows when global+feed gates and the engine are ready, subtracts in-flight/staged volumes, stores `PENDING_DEDUPE` when nothing remains, and audits each decision.
- REL-036 — The polite backfill honors start page/max pages/delay, persists last page for resume, reports its stop reason, aborts with `error` (never a fake success) on a persistent fetch failure, and is stoppable via `/api/backfill/stop`.
- REL-037 — Series refresh runs provider-metadata → Calibre-info → reconcile → expansion → Nyaa search over primary+alternate titles, skipping spinoff releases whose identity is not the target's, and returns per-phase summaries.
- REL-038 — Settings can preview and delete cached release rows (skipping active downloads) with an audit entry; status/volume/download/blacklist/tracker-link/prioritized-group writes invalidate the 5 s grouped cache, while tag, release-detail, series-link and cover-only writes do not (bounded by the 5 s TTL).
- REL-039 — Nyaa base URLs resolve as per-feed `config.base_url`, then the `nyaa_base_url` setting (seeded from `MANGATRAX_NYAA_BASE_URL`), then `https://nyaa.si`; saving the setting repoints only the exact seeded feed, never user feeds.
- REL-040 — Webhook notifications for feed scans are sent only for automatic polls and only when the poll produced new requests/transitions/submissions; manual polls are silent.
- REL-041 — `GET /api/releases/grouped` dumps all grouped releases with no pagination/filtering and has no consumer in this repository.
- REL-042 — `GET /api/search` performs alias-aware unified search over tracked series + releases but is not consumed by any page in this repository.
- REL-043 — `GET /api/releases/series_releases` re-derives the full group list with a search (bypassing the grouped cache), matches `series_key`/`series_name` case-insensitively, slices the requested releases, and ignores `prioritized_only`/`sort`; its only caller is the "Show all" handler on `/releases`, while the same button rendered on `/prioritized` has no handler defined on that page and is inert.
- REL-044 — `GET /api/prioritized/releases` returns up to 500 prioritized rows (optionally filtered by status) with no pagination or total field, post-sorted by group priority.


---


# Part 2 — Series Tracking, Calibre & Overview


# MangaTrax — Series Tracking, Calibre Library Integration & Overview (SER-001…SER-092)

**Scope (3 lines).**
This document specifies the *library identity* domain of MangaTrax: the `/` (Overview) and `/series` HTML pages, the series-tracking data model, the zero-lock Calibre `metadata.db` reader and every Calibre sync/link/audit path that repairs series identity or attribution.
It covers the stable-matching-key vs. display-title naming model, alias/dual-title resolution, spinoff and false-match guards, per-series refresh/backfill, volume ownership and completeness computation, metadata enrichment (AniList/MangaUpdates), cover caching, provider tags, unified search, storage health banners, and every series-domain maintenance endpoint reached from Settings.
All findings were produced read-only against commit `86a7bd8`; paths are repo-relative, line numbers refer to that commit, and tests were read (not executed) for behavioural confirmation.

---

## 1. Feature inventory

Legend — **ACTIVE**: reachable and exercised by UI/API/worker. **PARTIAL**: implemented but with a documented gap, unused surface, or a defect path. **VESTIGIAL**: code that exists but has no caller/consumer.

### 1.1 Overview page (`/`)

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| SER-001 | Overview page shell + Calibre status hero | ACTIVE | "MangaTrax Dashboard" hero; green badge "Calibre Connected (N Series • M Books)" when `metadata.db` is readable, else amber "Calibre DB Not Found (Check Path)"; no action button | `backend/app/main.py:1138-1167`; `backend/app/templates/index.html:8-32`; `backend/app/calibre/scanner.py:43-80` | `GET /` | none (reads `app_settings.calibre_path`; in-memory `_SUMMARY_CACHE`) |
| SER-002 | Overview KPI cards (5) | ACTIVE | Monitored Series / Wanted Bundles / Active Downloads / In Library / Discovered counters, each deep-linking to the corresponding list | `backend/app/main.py:1140`; `backend/app/database.py:3525-3551`; `backend/app/templates/index.html:35-100` | `GET /` | none (aggregate SQL over `series_tracking` + `releases`) |
| SER-003 | Overview active-downloader queue | ACTIVE | Grid of up to 5 torrent cards (name, %, MB/s, state, size) shown only when torrents exist; short 1.0 s live timeout so a dead qBittorrent cannot stall the page | `backend/app/main.py:1149-1150,1165`; `backend/app/main.py:535-632`; `backend/app/templates/index.html:103-133` | `GET /` | none (live downloader call; degraded payload on failure) |
| SER-004 | Overview "Wanted Releases & Bundles" | PARTIAL | Card list of wanted releases with cover, bundle badge, missing volumes, Download button, and "Your Manga Library is Completely Up-to-Date!" empty state. Badge shows the length of a *capped* list (≤10 fetched, ≤8 rendered), not the true total handled by SER-002 | `backend/app/main.py:1144`; `backend/app/database.py:3059-3060`; `backend/app/database.py:2893-2977`; `backend/app/templates/index.html:136-232` (`[:8]` at :152, badge at :142) | `GET /` | none (reads `releases` status `REQUESTED`) |
| SER-005 | Overview recent activity | ACTIVE | Last 10 `activity_logs` entries with event chip, message, details, local-time stamp; "View all logs →" | `backend/app/main.py:1145`; `backend/app/database.py:3471-3476`; `backend/app/templates/index.html:236-262` | `GET /` | reads `activity_logs` |

### 1.2 Series page (`/series`) and its row API

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| SER-006 | `/series` page shell | ACTIVE | Header "Tracked Manga Series", search input, Sync Calibre button, 7-column table, skeleton "Loading tracked series…", initial total rendered as "Load More Series (N remaining)", friendly filtered-empty state | `backend/app/main.py:1169-1180`; `backend/app/templates/series.html:1-79`; `:71-77` empty state | `GET /series` | none (server renders `series_list=[]`, `total_series=db.count_all_series()`) |
| SER-007 | `/api/series/rows` chunk API | ACTIVE | Returns rendered `<tr>` HTML plus `offset/count/total/has_more`; supports `monitored_only`, `offset`, `limit` (1–200), `search` | `backend/app/main.py:1523-1545`; `backend/app/database.py:2087-2124`, `2126-2143`; `backend/app/templates/partials/series_table_row_chunk.html:1-71` | `GET /api/series/rows` (called by page JS `fetchNextSeriesRows`, `series.html:108-169`) | none (reads `series_tracking` + latest release cover) |
| SER-008 | Monitored toggle | ACTIVE | Check/X button per row flips `is_monitored`; toast then full page reload; no 404 for unknown ids | `backend/app/main.py:3826-3830`; `backend/app/database.py:2145-2152`; `backend/app/templates/partials/series_table_row_chunk.html:3-7`; `series.html:256-267` | `POST /api/series/{id}/toggle` | writes `series_tracking.is_monitored = 1 - is_monitored` |
| SER-009 | Row cover thumbnail + lightbox | ACTIVE | 32×48 cover if the series' newest release row has `cover_url`; click opens image modal | `backend/app/database.py:2096-2101` (cover subquery); `backend/app/templates/partials/series_table_row_chunk.html:10-12`; modal `base.html:278-288` | page render / row chunk | none (reads `releases.cover_url`) |
| SER-010 | Website link display + edit modal | ACTIVE | External-link icon when `website_url` set; link button opens "Edit Series Webpage Link" modal; empty value is accepted (clears link); server silently succeeds for unknown ids | `backend/app/main.py:3832-3837`; `backend/app/database.py:2154-2158`; `backend/app/schemas.py:561-572`; `backend/app/security.py:301-315`; `series.html:80-96,303-333`; row `:16-20,62-64` | `POST /api/series/{id}/link` | writes `series_tracking.website_url` |
| SER-011 | "Owned" column + Not-linked CTA | PARTIAL | Linked rows show Calibre `book_count`; unlinked rows show "Not linked — Match to Calibre series" button opening the match modal. The column does **not** display `owned_volumes_json`, so the label "Owned" means Calibre book count, not the owned-volume set used by reconciliation | row `backend/app/templates/partials/series_table_row_chunk.html:31-37`; `backend/app/calibre/scanner.py:144-148`; `backend/app/database.py:1846-1861` | page render | none |
| SER-012 | Latest-volume column | ACTIVE | "Vol. N" from `series_tracking.latest_index` (Calibre max `series_index`, raised to the max owned bundle volume) | row `:38-40`; `backend/app/calibre/scanner.py:136-151`; upsert `database.py:1856-1858` | page render | reads `series_tracking.latest_index` |
| SER-013 | Series tag chips | ACTIVE | Up to 4 provider tag chips with `+N` overflow and tooltip (genres first); falls back to nothing when no tags | row `:41-55`; `backend/app/tag_display.py:9-14,84-107`; Jinja global `main.py:279-285` | page render (row chunk) | reads `series_tracking.tags_json` / `tags` |
| SER-014 | Per-series Backfill button | ACTIVE | History icon "Backfill": toast, spinner, then starts the 3-phase refresh; page remains usable; global backfill banner appears via polling | row `:58-61`; `backend/app/templates/series.html:269-301`; `backend/app/main.py:4049-4062` | `POST /api/series/{id}/backfill` (`{"max_pages": 3}` hard-coded in UI) | writes `background_tasks` row, `app_settings.backfill_status/backfill_progress` |
| SER-015 | Row blacklist button | ACTIVE | Shared `blacklistSeriesGlobal` posts the series name; toast says undoable in Discovery > Blacklist; card removed without reload | row `:65-67`; `backend/app/templates/base.html:821-850`; `backend/app/main.py:4095-4101` | `POST /api/blacklist` | writes `series_blacklist` + sets that series' `releases.status='BLACKLISTED'` and `series_tracking.is_monitored=0` (`database.py:2342-2360`) |
| SER-016 | Sync Calibre button | ACTIVE | Header button disables + spins, calls the synchronous manual sync, toasts the server summary, reloads after 1.5 s | `backend/app/templates/series.html:25-28`; `backend/app/templates/base.html:516-539`; `backend/app/main.py:1909-1930` | `POST /api/calibre/sync` | writes `background_tasks` row + full Calibre sync side effects |
| SER-017 | Client-side search filter | PARTIAL | `oninput` lowercases the query and hides non-matching loaded rows by `data-name` (name + author + publisher + tags); shows "No manga series match your search" + Clear search. Server-side `search`/`monitored_only` parameters exist (SER-007) but the page never sends them; while a query is active the infinite loader still fetches the remaining pages in the background, so results eventually cover the library | `series.html:21-24,194-233`; row `data-name` at `partials/..._row_chunk.html:2`; unused API params `main.py:1526-1533` | typing in search box | none |
| SER-018 | Infinite scroll / Load More | ACTIVE | Rows load in 50-row batches; an `IntersectionObserver` with 300 px root margin auto-triggers the next batch; fallback Load-More button re-enabled on error; fetches settle to `has_more=false` | `series.html:101-192,235-254`; `main.py:1523-1545` | page load + scroll | none |
| SER-019 | Match-to-Calibre modal | ACTIVE | Modal "Match '<name>' to Calibre series", search box, Calibre list with "N book(s)" per entry, "Link & merge"; success toast reports re-keyed releases, updates the row's Owned cell, removes the button. The modal sends no `source`, so the list is name-ordered with no similarity ranking | `series.html:353-435`; `backend/app/main.py:3926-3959` | `GET /api/calibre/series?search=` then `POST /api/series/{id}/match-calibre` | calls `db.link_tracked_series` (SER-037) → `series_tracking` + `releases.series_name` + `series_alias_overrides` |
| SER-020 | Series nav active state | ACTIVE | Desktop "Library ▾" and mobile "Library" section highlight on `/series` (`active_page == 'series'`) | `backend/app/templates/base.html:127-145,227-230`; route context `main.py:1176` | page render | none |

### 1.3 Calibre scanner, sync and linking

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| SER-021 | Zero-lock Calibre `metadata.db` reader | ACTIVE | Reads Calibre over SMB/NFS without locking: `mode=ro&immutable=1`, `query_only=ON`, 2 s busy timeout, read-only fallback if `immutable` unsupported | `backend/app/calibre/scanner.py:17-41` | first use by sync/summary/audit | none on Calibre DB |
| SER-022 | Library summary + mtime cache | ACTIVE | Counts `books`/`series`/`authors`; cached per DB path keyed by file mtime (`_SUMMARY_CACHE`) | `backend/app/calibre/scanner.py:9,43-80` | `GET /` and `GET /api/calibre/test` | in-memory cache only |
| SER-023 | Calibre series extraction | ACTIVE | Per-series `book_count`, `latest_index`, authors (group_concat), publisher (first linked), tags (group_concat), plus resolved owned volumes | `backend/app/calibre/scanner.py:82-188` | sync / snapshot / test | none |
| SER-024 | Calibre volume map (bundles/dual titles) | ACTIVE | `series name → owned volume set`; parses bundle titles so `v01-02` contributes `{1,2}` even though `series_index=1`; indexes by lower-case name, normalized name, Calibre id and delimiter parts | `backend/app/calibre/scanner.py:190-243`; `backend/app/parser.py:1934-2133` | sync / matcher construction | none |
| SER-025 | Synthetic standalone-series entries | ACTIVE | Books with no Calibre series but a parseable title are exposed as pseudo-series with `series_id = book.id + 1000000`, `book_count=1` | `backend/app/calibre/scanner.py:153-181` | `get_all_series()` | none (synthetic id re-derived each scan) |
| SER-026 | `GET /api/calibre/test` | ACTIVE | Settings "Test" button probes a user-supplied path; path is contained to `calibre_path`, default `CALIBRE_DIR` and `staging_dir` roots (403 on escape); returns summary JSON | `backend/app/main.py:1937-1953`; `security.ensure_path_within_roots`; `settings.html:872-885` | `GET /api/calibre/test?path=` | none |
| SER-027 | Manual Calibre sync endpoint | ACTIVE | "Sync now" waits for the scan (deliberately synchronous), then returns sync summary + persisted task with progress counters | `backend/app/main.py:1909-1930`; `main.py:1267-1297`; `scheduler.py:3115-3209` | `POST /api/calibre/sync` | `background_tasks` row, `activity_logs`, all sync side effects |
| SER-028 | Periodic Calibre sync | ACTIVE | Worker re-syncs every `calibre_sync_interval_minutes` (default 15) using a non-blocking lock; concurrent syncs skip with "already in progress" | `scheduler.py:5189-5195`; `scheduler.py:3120-3127`; interval setting `database.py:752` | background worker loop (10 s tick) | same as SER-027 |
| SER-029 | Startup initial sync | ACTIVE | First worker action: legacy re-key repair then initial Calibre sync, exception-shielded so the web UI already serves | `scheduler.py:3212-3241`; `scheduler.py:5152-5160`; `main.py:214-230` | worker thread start (FastAPI lifespan) | same as SER-027 |
| SER-030 | Calibre sync status endpoint | ACTIVE | Cheap JSON `{in_progress, initial_sync_done, last_sync}`; drives the global banner and never blocks | `scheduler.py:3243-3249`; `main.py:1932-1935` | `GET /api/calibre/status` (polled at 3 s while running) | reads in-memory lock/flag |
| SER-031 | `series_tracking` upsert from Calibre | ACTIVE | Every Calibre series becomes/updates a tracker (insert `is_monitored=1`); authors/publisher/tags only overwritten when non-empty; `website_url` only set while empty/blank; `owned_volumes_json` only updated when the new set is non-empty; `last_sync` stamped; grouped cache invalidated | `backend/app/database.py:1816-1870` (SQL at `1846-1863`) | sync | writes `series_tracking` |
| SER-032 | Auto-link trackers during sync | ACTIVE | After each upsert batch, trackers with no `calibre_series_id` are matched: unique exact candidate wins, fuzzy at ≥0.90; multiple exact candidates are left for manual match. Re-keys releases and merges duplicate trackers. Not audit-logged | `database.py:1864-1869`; `database.py:2020-2085`; merge service `1872-2018` | sync | writes `series_tracking`, `releases.series_name`, `series_alias_overrides` |
| SER-033 | Pending-request resolution | ACTIVE | After sync, wanted releases whose volumes are now in Calibre are promoted to `IN_CALIBRE` (or `IN_STAGING`), audited `REQUEST_RESOLVED`, and webhook-notified when acquired | `scheduler.py:3153-3174`; `backend/app/calibre/matcher.py:216-229`; status precedence `database.py:272-307` | sync (manual or periodic) | writes `releases.status`, `activity_logs`; webhook |
| SER-034 | Bounded incremental release reconcile | ACTIVE | Re-checks active releases (REQUESTED/DISCOVERY/IN_CALIBRE/NEW) against Calibre; skips provably-stable owned rows; processes at most `MANGATRAX_RECONCILE_BATCH_SIZE` (default 2000, min 50) per pass with a rotating cursor; updates missing volumes/status only when changed | `scheduler.py:3251-3322`; `scheduler.py:116-118` | every sync | writes `releases.missing_vols_json/status` |
| SER-035 | Complete-release expansion after sync | ACTIVE | After sync, unexpanded complete-series rows are expanded from metadata/Calibre ownership | `scheduler.py:3181-3185`; `scheduler.py:4976+` | every sync | writes expanded volumes/status |
| SER-036 | `/api/calibre/series` picker | ACTIVE | Calibre series list for match pickers; optional `search` substring filter and optional `source` name for `difflib` similarity ranking (score round 3dp); falls back to synced tracker rows when the live library is unavailable | `main.py:2278-2325`; snapshot `main.py:2855-2863` | `GET /api/calibre/series` (Series modal sends only `search`; Downloads/Manual Pickup also send `source`) | none |
| SER-037 | Tracked-series link/merge service | ACTIVE | Links one tracker to a Calibre series: re-keys releases to the Calibre name, merges duplicate trackers (MAX monitored, union owned volumes), keeps a single tracker, registers alias override when names differ, invalidates grouped cache; never changes release statuses/vetoes | `database.py:1872-2018`; endpoint `main.py:3926-3959` | `POST /api/series/{id}/match-calibre`, auto-link (SER-032), link-repair (SER-039) | writes `series_tracking`, `releases.series_name`, `series_alias_overrides` |
| SER-038 | Link audit endpoint | PARTIAL | Read-only proposal list of unlinked trackers fuzzy-matching a Calibre series at 0.90, confidence label `fuzzy_or_candidate`; **no UI caller** anywhere in `backend/app/templates` | `main.py:3962-3987` | `GET /api/series/link-audit` (API only) | none |
| SER-039 | Link repair endpoint | PARTIAL | Verifies/merges all unlinked trackers; defaults to dry-run unless `confirm: true`; dry-run returns the audit payload; no UI caller. Confirm path calls `auto_link_tracked_series(snapshot, None, …)` with **no volumes map**, so a source-only tracker's `owned_volumes_json` can be written as `[]` until the next sync | `main.py:3990-4010`; `database.py:2020-2085`; `database.py:1897-1907,1945-1951` | `POST /api/series/link-repair` (API only) | writes `series_tracking`/`releases` when confirmed |

### 1.4 Series identity, matching and naming

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| SER-040 | Text normalization | ACTIVE | Lowercase + NFKD + dashes/colons/slashes to spaces + punctuation stripped + whitespace collapsed; the comparison primitive for every match/alias/override key | `backend/app/parser.py:10-18` | everywhere | none |
| SER-041 | Dual-title/delimiter candidate splitting | ACTIVE | Single source of truth for splitting on the vertical bar, slash, en/em dash, spaced hyphen, double slash and semicolon, used by scanner, matcher, metadata clients, DB grouping and search | `parser.py:20-56` | everywhere | none |
| SER-042 | Calibre index matching ladder | ACTIVE | `CalibreSeriesIndex.match()`: exact normalized dict lookup → bounded substring containment (`len_ratio ≥ 0.70`, len ≥ 6, earliest library order wins) → fuzzy only on shared rare-token/3-gram candidates with `SequenceMatcher > 0.80`; built once per sync for performance | `parser.py:1737-1917`; delegated wrapper `1920-1932`; matcher wiring `calibre/matcher.py:21-27` | every release match / audit / link proposal | none |
| SER-043 | Distinctive-head fuzzy guard | ACTIVE | Rejects fuzzy similarity produced only by a shared generic suffix ("The Red Ranger Becomes an Adventurer in Another World" ≠ "Level 0 Demon King Becomes an Adventurer in Another World"); accepted/rejected matches are logged to `mangatrax.series_match` | `parser.py:1703-1734,1891-1917` | fuzzy matching | logs only |
| SER-044 | Colon-subtitle candidates | ACTIVE | Adds colon-split parts and cumulative `"A: B"` prefixes as candidates without weakening whole-title rules ("Failure Frame: I Became the Strongest…" → Calibre "Failure Frame") | `parser.py:1843-1857`; test `tests/test_tracked_series_calibre_link.py:50-76` | matching | none |
| SER-045 | Stable-key/display identity ladder | ACTIVE | `resolve_series_identity()` returns stable key + cosmetic display + source: (0) user alias override, (1) Calibre scanner hit, (1b) Calibre-origin tracker, (2) user-typed tracker, (3)/(4) AniList canonical/romaji only as lookup bridges, (5) reverse AniList alias claims, (6) raw parsed name. AniList titles never become the stored key | `scheduler.py:622-788` | feed/backfill ingestion, Discovery track, audits | returns payload; persisted by callers |
| SER-046 | User alias override precedence | ACTIVE | Manual-match alias overrides win before AniList/Calibre identity resolution; lookup tries the raw normalized name then every parse-shape candidate and cleaned source | `scheduler.py:713-733`; `database.py:1752-1787` | ingestion after a manual match | reads `series_alias_overrides` |
| SER-047 | Reverse AniList alias resolution | ACTIVE | A raw name that is an AniList alias of an already-tracked series resolves to the tracked stable name; Calibre-claimed aliases preferred; ambiguous multi-claim names keep the parsed name | `scheduler.py:769-785`; `database.py:3751-3818` | ingestion | none |
| SER-048 | Spinoff identity scoping | ACTIVE | A metadata entry reached only via delimiter/parent fallback is rejected; delimiter parts accepted only when they agree on one AniList id, so `Parent: Spinoff` never inherits the parent's identity/aliases | `scheduler.py:556-620`; test `tests/test_spinoff_identity.py:70-102` | ingestion / audits | none |
| SER-049 | Backfill spinoff skip guard | ACTIVE | During a targeted parent refresh, a crawled release whose own parsed/AniList identity differs (`anilist_id` mismatch or name outside target aliases) is audited `SPINOFF_SKIPPED` and never stored/downloaded | `scheduler.py:3968-3999`; test `tests/test_spinoff_identity.py:133-166` | series backfill (SER-061) | `activity_logs` only |
| SER-050 | `detect_series_titles()` | ACTIVE | Builds the search-title set for a refresh from the requested name, delimiter/parenthesis parts and the AniList entry's own canonical/romaji/synonyms; deliberately does **not** harvest titles from stored releases (avoids self-amplifying spinoff loops); drops giant combined concatenations | `scheduler.py:4104-4182`; test `tests/test_spinoff_identity.py:108-127` | per-series refresh | none |
| SER-051 | `series_name_candidates()` recovery | ACTIVE | Plausible spellings from one filename: parsed primary, `[bracket]` form, bracket content, cleaned display, underscore-stripped, colon parts, `clean_series_for_matching`; deduped | `parser.py:2219-2255`; `parser.py:2178-2216`; tests `tests/test_series_name_candidates.py:36-77` | manual match alias registration (SER-076), identity override lookup, auto-link | none |
| SER-052 | Release title parsing → series/volumes | ACTIVE | `extract_manga_bundle_and_volumes()` parses series, full volume expansion, complete-series flag, groups, authors/publisher, revision, collected-batch and chapter diagnostics; bracket/brace/underscore recovery and volume-only fallback; guards reversed ranges/bracket delimiters | `parser.py:1439-1687`; recovery `2178-2216`; bundle/volume expansion `1934-2133`; `is_complete_series_release` `1233-1256` | feed ingestion, audits | none |
| SER-053 | Legacy AniList re-key repair | ACTIVE | Guarded startup repair re-keys releases stranded under an AniList display title back to their tracked stable key; a duplicate-tracker case is only merged with provable bidirectional alias evidence; idempotent, audited `SERIES_KEYS_REPAIRED` | `database.py:3868-3956`; helper `3832-3866`; called `scheduler.py:956-962,3223` | every startup; debug action `normalize-english-titles` (SER-085) | writes `releases.series_name`, `activity_logs` |
| SER-054 | Track a discovered series | ACTIVE | "Track Series" writes the resolved stable key into `series_tracking` (monitored=1, default MangaUpdates search URL, description from release/cache), sets the release to `REQUESTED`, re-keys legacy names | `database.py:3277-3361`; endpoint `main.py:4135-4171`; UI `releases.html:543+` / `prioritized.html:495+` | `POST /api/discovery/track` | writes `series_tracking`, `releases`, `activity_logs` |
| SER-055 | Series templates map for tagging | ACTIVE | `normalized name → {series_name, authors, publisher, tags}` for ComicInfo inheritance; user alias overrides are merged so routing/tagging resolve the same target; used by staging routing, inventory and tagger | `database.py:2313-2339`; consumers `tagger/pipeline.py:86+`, `downloads_inventory.py:232`, `scheduler.py:3832` | routing/tagging passes | reads `series_tracking` + `series_alias_overrides` |

### 1.5 Per-series refresh (3-phase) and backfill

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| SER-056 | Refresh endpoint by series id | ACTIVE | Resolves tracker by id (404 when missing), starts the background job, returns job payload + `task_id` + persisted task | `main.py:4049-4062`; helper `4013-4046`; schema `schemas.py:575-578` (default 3 pages, max 500) | `POST /api/series/{id}/backfill` | `background_tasks`, settings (`backfill_status/progress`) |
| SER-057 | Refresh endpoint by series name | ACTIVE | Same job keyed by any name (works for groups without a tracked id); validation min length 1/max 500 | `main.py:4064-4072`; `schemas.py:581-590` | `POST /api/series/backfill` | same as SER-056 |
| SER-058 | 3-phase job + task watcher | ACTIVE | Background daemon runs metadata → Calibre → Nyaa crawl in one job; a watcher thread mirrors `app_settings.backfill_progress` into the persisted task and marks it success at completion; the endpoint responds immediately | `scheduler.py:4710-4753`; watcher `main.py:4013-4046`; status settings `scheduler.py:4724-4738,3858-3868` | SER-056/057 | `background_tasks`, `activity_logs`, settings |
| SER-059 | Phase 1 — provider metadata | ACTIVE | Force-refreshes AniList for the stable name (MangaUpdates fallback when AniList unavailable); writes enrichment only (description/authors/website if empty), updates release covers + cover cache; KEEPS cached values on provider outage and reports a skip; audits `SERIES_METADATA_REFRESHED` with changes | `scheduler.py:4203-4338`; `metadata/anilist.py:146-268`; tests `tests/test_series_refresh.py:117-243` | SER-058 phase 1 | `manga_metadata_cache`, `series_tracking.description/authors/website_url`, `releases.cover_url`, cover files |
| SER-060 | Phase 2 — Calibre info | ACTIVE | Re-reads the one series from Calibre; updates `calibre_series_id`, book_count, latest_index, owned volumes and non-empty authors/publisher/tags/description/website; caches refreshed cover; clean skip when library unavailable or series absent; audits `SERIES_CALIBRE_REFRESHED` | `scheduler.py:4340-4436`; test `tests/test_series_refresh.py:275-309` | SER-058 phase 2 | `series_tracking` columns, cover files |
| SER-061 | Phase 3 — targeted Nyaa crawl | ACTIVE | Searches Nyaa for every detected title up to `max_pages` each, processes every result through the normal ingest path with the target-identity guard (SER-049) and chapter/LN filters, counts found/requested/sent, triggers auto-download per policy, and returns a structured summary; crawl failure yields `status: partial` while metadata/Calibre changes stay applied | `scheduler.py:4460-4708` (phases at `4483-4536`, crawl `4567-4660`, summary `4662-4708`); tests `tests/test_series_refresh.py:342-447` | SER-058 phase 3 | `releases` (upsert), `activity_logs`, downloader state |
| SER-062 | Post-refresh series reconcile | ACTIVE | Recomputes missing volumes/status for only that series' releases (vetoed rows excluded) after the refresh | `scheduler.py:4438-4458`; `database.py:2233-2250` | SER-058 | `releases.missing_vols_json/status` |
| SER-063 | Conditional complete-series expansion | ACTIVE | If phase 1 changed total volumes or phase 2 changed book count, expands unexpanded complete-series rows for that series only | `scheduler.py:4525-4535`; expansion `4976+` | SER-058 | `releases` volume columns/status |
| SER-064 | Backfill auto-download + dedupe | ACTIVE | For new REQUESTED releases, auto-download (if enabled) minus volumes already in flight/staged; duplicates get `PENDING_DEDUPE` and are retried by the periodic sweep; qBittorrent selective volume download applied for partial bundles; Torbox path uses the same wanted set | `scheduler.py:4612-4660`; `scheduler.py:1248-1306`; `sweep_pending_dedupe` `2329+` | SER-061 release loop | `releases.download_status/downloader_*`, downloader calls |
| SER-065 | Single global backfill mutex | PARTIAL | Per-series refresh and the polite historical backfill share one in-process `is_backfilling` flag: starting a series refresh while another backfill runs returns `in_progress`, yet `_start_backfill_task` still creates a task and a watcher that marks it `success` when the *other* job ends (misleading task history; no series refresh happened) | `scheduler.py:4717-4721`; `scheduler.py:4761-4773`; `main.py:4024-4046` | SER-056/057 during any backfill | `background_tasks` (misleading row) |

### 1.6 Metadata providers, covers, tags and search

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| SER-066 | AniList GraphQL client | ACTIVE | Sliding-window limiter 90 req/60 s with Retry-After/429 handling, 4 s timeout, authoritative `source="anilist"` rows cached under search term + canonical + romaji; `force_refresh` bypasses a fresh cache but keeps cached values if the provider fails | `metadata/anilist.py:17-87,89-119,121-134,146-268,285-394`; cache `database.py:3642-3725` | ingestion, phase 1, tags refresh, Discovery info | `manga_metadata_cache` |
| SER-067 | Authoritative display-title resolution | ACTIVE | Display title = AniList canonical English only when `source=='anilist'` and canonical non-empty; otherwise the stable key. Used by landing cards, search, release grouping; never feeds back into matching | `database.py:328-357,3727-3749`; grouped builder `4250-4252` | render paths | none |
| SER-068 | MangaUpdates synopsis/URL fallback | ACTIVE | Public REST client with 5 s timeout; second per-series call extracts "NN Volumes" → `total_volumes` + FINISHED status; cleaned synopsis (HTML/BBCode/Markdown/URL strip); cache under `mu:<name>` with URL smuggled in `romaji_title`; used for Discovery detail and phase-1 fallback | `metadata/mangaupdates.py:26-59,70-160,162-240`; consumers `main.py:4181-4209`, `scheduler.py:4228-4254` | Discovery "fetch info", phase 1 fallback | `manga_metadata_cache` |
| SER-069 | Cover cache download pipeline | ACTIVE | Hash-named disk cache (`sha256[:24].jpg`), 5 MB cap, 7 s timeout, max 3 redirects, per-hop SSRF validation, raster magic-byte check, atomic rename; never raises | `covers.py:96-245`; constants `covers.py:20-24` | `/api/covers` miss, metadata refresh, Discovery info | `DATA_DIR/covers/*.jpg` |
| SER-070 | `/api/covers` serving | PARTIAL | Streams the cached file with `Cache-Control: public, max-age=864000`, else returns a built-in SVG placeholder (200). All cache files are named `.jpg`, so PNG/WebP payloads are still served as `image/jpeg` (mismatched MIME under the global `X-Content-Type-Options: nosniff`) | `main.py:1955-1977`; `covers.py:101-107`; headers middleware `main.py:269-277` | `GET /api/covers?url=` from every cover `<img>` | read/creates cache file |
| SER-071 | Provider tag refresh job | ACTIVE | Settings button starts a cancellable background task: up to 1000 releases, one AniList lookup per distinct untagged series, persists tags to all releases of the series and to `series_tracking`, caches metadata; returns `{updated, skipped, failed, total}` | `scheduler.py:790-827`; endpoint `main.py:1552-1562`; settings button `settings.html:681-691`; test `tests/test_provider_tags.py` | `POST /api/maintenance/refresh-tags` | `releases.tags_json/tags_text`, `series_tracking.tags_json/tags_text`, `manga_metadata_cache`, `background_tasks` |
| SER-072 | Tag display policy | ACTIVE | Per-view top-N chips (series 4, dashboard 3, releases 3, discovery 5), genres first then rank, spoilers hidden, overflow count + tooltip; tags stored as an ordered dict list plus a pipe-delimited exact-token `tags_text` column | `tag_display.py:9-14,30-107`; row `partials/..._row_chunk.html:41-55`; index `index.html:189-197`; tests `tests/test_tag_display_gaps.py:61-76` | page render | none |
| SER-073 | Known-tag universe API | ACTIVE | `GET /api/tags?q=&limit=` union of metadata-cache genres/tags plus release/series denormalized tags, spoilers excluded, ranked exact → prefix → substring → count; used by Discovery tag picker | `main.py:1565-1568`; `database.py:1339-1407`; test `tests/test_tag_display_gaps.py:20-58` | `GET /api/tags` | none |
| SER-074 | Unified search API | PARTIAL | `GET /api/search?q=` returns tracked series hits (stable key + display title + `match`: stable/normalized/anilist_alias + matched alias, authors, publisher, monitored, cover) and release hits; blank query 400; LIKE wildcards literal; limit 1–50. **No template or UI calls it** (verified by grep across `backend/app/templates`); M2-feature-17 surface remains API-only and the Series page uses literal client-side filtering instead | `main.py:3498-3514`; `search.py:72-182`; `database.py:3007-3057`; tests `tests/test_m2_features.py:678-731` | `GET /api/search` (API only) | none |

### 1.7 Aliases, audits, maintenance, cross-cutting banners

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| SER-075 | Alias override list/delete endpoints | PARTIAL | `GET /api/maintenance/series-aliases` returns all overrides (no UI caller); `DELETE /api/maintenance/series-aliases/{id}` removes one, invalidates grouped cache, 404 when unknown. Settings renders overrides server-side and uses the DELETE from its Remove buttons | `main.py:2573-2584`; `database.py:1745-1750,1789-1793`; settings card `settings.html:745-764,1300-1316` | Settings maintenance; external API | writes `series_alias_overrides` |
| SER-076 | Alias override creation | ACTIVE | "Manual match" on Downloads creates one override for the source plus one per distinct parse-shape candidate; target may be a Calibre series (auto-syncs tracking) or a typed new title (`track_discovered_series`); re-keys matching releases, routes downloads, audits `SERIES_MANUALLY_MATCHED` | `main.py:2328-2458`; `main.py:2385-2393`; `database.py:1761-1787`; tests `tests/test_manual_series_match.py:102-192` | `POST /api/downloads/awaiting-series/match` | `series_alias_overrides`, `series_tracking`, `releases.series_name`, `activity_logs` |
| SER-077 | False-series-match audit | ACTIVE | Settings → Maintenance "Audit matches": scans newest 2000 releases; flags rows whose stored series differs from the title-parsed series and which the fixed distinctive-head matcher would no longer map (or whose stored series is a known Calibre series); suggests target, `can_repair` false for IGNORED/BLACKLISTED; read-only | `false_series_matches.py:25-82`; endpoint `main.py:2508-2516`; UI `settings.html:710-725,1598-1618`; test `tests/test_false_fuzzy_series_matches.py:40-94` | `POST /api/maintenance/false-series/audit` | none (read-only) |
| SER-078 | False-series-match repair | ACTIVE | Requires `confirm:true` and 1..500 release ids; re-keys each to its title-parsed series, preserves IGNORED/BLACKLISTED status, recomputes DISCOVERY/REQUESTED/IN_CALIBRE from Calibre; per-row failure isolation; invalidates grouped cache; audits `FALSE_SERIES_MATCH_REPAIRED`; persisted task | `false_series_matches.py:85-151`; endpoint `main.py:2519-2548` | `POST /api/maintenance/false-series/repair` | `releases.series_name/status/missing_vols_json`, `activity_logs`, `background_tasks` |
| SER-079 | Series attribution audit | ACTIVE | Scans *all* releases under a tracked series, re-parses the title, and proposes the identity-resolved own series; genuine aliases skipped; multi-part names that resolve only to `raw` are `ambiguous` (never repairable); audits `audit:attribution` task | `scheduler.py:829-911`; endpoint `main.py:2631-2639`; UI `settings.html:766-798,1319-1346`; test `tests/test_spinoff_identity.py:172-207` | `GET /api/maintenance/series-attribution` (note: this GET persists a task row) | `background_tasks`, `activity_logs` |
| SER-080 | Series attribution repair | ACTIVE | Re-keys only explicitly selected, `safe_to_repair` rows (max 200 ids; empty list allowed → 0 repaired); idempotent; audits `SERIES_ATTRIBUTION_REPAIRED` | `scheduler.py:913-954`; endpoint `main.py:2642-2661` | `POST /api/maintenance/series-attribution-repair` | `releases.series_name`, `activity_logs`, `background_tasks` |
| SER-081 | Suspicious volume-range audit | PARTIAL | Finds releases whose stored volumes are chapter-scale (all ≥100) or whose title now parses chapter-only; explicit volume markers exempt; only chapter-only rows are `safe_to_repair`. Endpoint is a **GET that persists a background task + logs** although no release data changes | `database.py:1643-1710`; endpoint `main.py:2461-2468`; UI `settings.html:693-708,1265-1289` | `GET /api/maintenance/suspicious-volume-ranges` | `background_tasks`, `activity_logs` |
| SER-082 | Volume-range repair | ACTIVE | Clears phantom volumes for selected *safe* rows, sets `is_bundle=0`, `status='IGNORED'`; unsafe rows counted skipped; silently truncates ids to first 200; audits `CHAPTER_RANGE_REPAIRED` | `database.py:1712-1742`; endpoint `main.py:2551-2570` | `POST /api/maintenance/repair-volume-ranges` | `releases.volumes_json/missing_vols_json/volume_*/is_bundle/status`, `activity_logs` |
| SER-083 | Series/release cache preview + clear | ACTIVE | Settings "Release cache": preview count for a series/status filter, then clear (confirm required); never touches tracked series/Calibre; skips DOWNLOADING/QUEUED rows; audits; persisted task | `main.py:2587-2628`; `database.py:1194-1247`; UI `settings.html:643-679` | `GET /api/maintenance/releases-cache`, `POST /api/maintenance/clear-releases` | deletes `releases` rows, `activity_logs`, `background_tasks` |
| SER-084 | Safe maintenance actions | ACTIVE | `POST /api/maintenance/clear` with closed enum: purge cover cache, clear unreviewed discovery, reset descriptions (also blanks `series_tracking.description`); audited `MAINTENANCE_CLEARED`; not debug-gated | `maintenance.py:28-101`; endpoint `main.py:4323-4339`; `database.py:3554-3599`; schema `schemas.py:995-1010` | Settings/safe API | cover files, `releases`/`series_tracking.description`, `activity_logs` |
| SER-085 | Debug-gated actions affecting series | PARTIAL | Only with `MANGATRAX_DEBUG_ACTIONS=1`: `normalize-english-titles` kept as an API-compat name but now runs the guarded stable-key repair (SER-053); `reset-all-data` deletes `series_tracking`, blacklist, releases, caches, metadata cache, resets feeds/groups, re-runs a Calibre sync; `purge-non-manga` deletes non-manga rows only | `main.py:4342-4388`; `database.py:4670-4722,3601-3640`; UI `settings.html:801-845` | `POST /api/debug/action` (403 unless flag) | many tables; cover cache |
| SER-086 | Blacklist coupling to library state | ACTIVE | Blacklisting a name also un-monitors its tracker and sets its releases BLACKLISTED; unblacklisting resets only BLACKLISTED rows to DISCOVERY; automated writes can never lift the veto | `database.py:2342-2393`; endpoints `main.py:4095-4110,4173-4179`; precedence `database.py:272-307` | Series row / Discovery blacklist buttons | writes `series_blacklist`, `releases.status`, `series_tracking.is_monitored` |
| SER-087 | Storage health probe and warnings | PARTIAL | Startup/settings-save probes `downloads_dir` and `staging_dir` with a real temp write and actionable uid/errno hint; persisted; surfaced on `/settings` and `/downloads` and in `/health`, **not** on Overview or `/series` | `storage_health.py:30-172`; `main.py:357-394`; `main.py:223`; templates `partials/storage_warning.html`, `partials/storage_path_warning.html`; `settings.html:266-272`; `downloads.html:54` | startup, settings save, `/health`, downloads/settings render | `app_settings.storage_health_json` |
| SER-088 | Global Calibre sync banner | ACTIVE | Slim dismissible banner "Indexing your Calibre library in the background" shown across pages while `in_progress` or before `initial_sync_done`; polls 3 s; on completion toasts and reloads; never rendered on `/auth/*` | `templates/base.html:333-344,690-751`; `main.py:1932-1935` | any page load | none |
| SER-089 | Global historical-backfill banner | ACTIVE | Sticky banner with live `backfill_progress` text and "Stop Backfill" button; polled every 2.5 s via `/api/backfill/status`; success toast on idle | `base.html:345-365,645-689,753-819`; `main.py:4293-4321`; `scheduler.py:3852-3868` | any page load; backfill start | reads settings, writes on stop |
| SER-090 | Global background-tasks panel | ACTIVE | Bottom-right "Tasks" button + panel polling `/api/tasks` every 1.5 s: title, pct or indeterminate bar, status, message, elapsed/ETA, error, Cancel when supported; finish toast; buttons with matching `data-task-kind` disable while active | `base.html:389-398,1002-1148`; `main.py:1235-1264`; `background_tasks.py` | any page | reads `background_tasks`; cancel writes requested flag |
| SER-091 | Series page mobile/responsive behavior | ACTIVE | Desktop table is horizontally scrollable on small screens; search input full width then fixed 64; nav collapses to hamburger menu with 44 px rows; Overview KPI grid 2→5 columns. Known gaps: series table has no card layout, monitored toggle is a 28 px target, row icon buttons carry `title` but no `aria-label` (the a11y test only scans top-level templates, not `templates/partials/`) | `series.html:9-35`; `base.html:220-273,193-218`; row `partials/..._row_chunk.html:3-7,62-67`; a11y test scope `tests/test_ui_polish.py:171-200` | responsive render | none |
| SER-092 | Unused series data-access helper | VESTIGIAL | `MangaDatabase.get_series_by_id()` has no caller in backend or tests; endpoints resolve rows through `get_all_series()`/`get_series_by_name()` instead | `database.py:2171-2176` (only definition; repo-wide grep has one match) | none | reads `series_tracking` |

---

## 2. How it works

### 2.1 Overview (`/`) request flow

`dashboard_view` (`main.py:1138-1167`) is synchronous per request and assembles five independent inputs:

1. **KPI stats** — `db.get_dashboard_stats()` (`database.py:3525-3551`):
   * `monitored_series = COUNT(*) FROM series_tracking WHERE is_monitored = 1` (includes user-typed trackers with no Calibre id);
   * `pending_requests = COUNT(*) FROM releases WHERE status='REQUESTED'` with the media guard and `is_light_novel=0`;
   * `acquired_books = COUNT(*) WHERE status='IN_CALIBRE'` (a count of *release rows*, not distinct volumes — see open question Q5);
   * `active_downloads = COUNT(*) WHERE download_status='DOWNLOADING'`;
   * `discovered_count = count_discovery_releases()`.
   The media guard is the single predicate `tracked_media_clause()` (`database.py:107-118`): `COALESCE(media_type,'unknown') NOT IN ('light_novel','chapter_only','novel','audiobook')`, so only `manga`/`unknown` rows count and "inconclusive means kept".
2. **Calibre status hero** — `CalibreMangaScanner(db.get_setting("calibre_path", CALIBRE_DIR)).get_library_summary()` (`main.py:1141-1143`). The scanner returns `{available: True, db_path, total_books, total_series, total_authors}` or `{available: False, total_*, error?}`; the template renders the green/amber badge from `available` only (`index.html:20-31`). The summary is cached in-process by `metadata.db` mtime (`scanner.py:9,47-54`), so repeat dashboard loads are cheap.
3. **Wanted list** — `db.get_pending_requests(limit=10)` → `get_releases(status='REQUESTED', limit=10)` (`database.py:3059-3060`, `2893-2977`): filters the media guard + `is_light_novel=0`, orders by `updated_at DESC, id DESC`, decorates `display_series_name` from the metadata cache. The template renders at most `pending_requests[:8]` (`index.html:152`) while the section badge prints `pending_requests|length` (max 10, `index.html:142`) — the badge is not the database total.
4. **Downloader snapshot** — `_build_downloader_status(1.0)` off the event loop (`main.py:1149`), then only `torrents[:5]` are handed to the template (`main.py:1165`). `_build_downloader_status` (`main.py:535-632`) clamps the timeout to ≤5 s, returns a fully valid degraded payload for disabled/unreachable downloaders (`status:"degraded"`, empty torrents, message), and for Torbox delegates to the cached Torbox status builder. A hung downloader therefore produces at most one 1 s wait, never an error page.
5. **Recent activity** — `db.get_logs(limit=10)` (`database.py:3471-3476`), rendered with `event_type` colour chips and `localtime` Jinja filter (`main.py:289-298`).

Failure behaviour: only the downloader/manual-pickup calls are individually guarded (`main.py:1149-1154`). A database error or a scanner exception in `get_library_summary` is caught inside the scanner (returns `available:False`), but an unexpected failure in `get_dashboard_stats`/`get_logs` propagates to FastAPI as a 500; there is no dashboard-level try/except.

The Overview does **not** include the storage-health banner (SER-087), does not render any series list, and has no per-page search.

### 2.2 `/series` page: shell, lazy rows, interactions

**Shell.** `series_view` (`main.py:1169-1180`) renders the page with `series_list: []` and `total_series: db.count_all_series()` (`database.py:2126-2143`; unfiltered). The table body contains a skeleton row and the JS bootstraps:

* `DOMContentLoaded` → `fetchNextSeriesRows()` when no server-rendered rows exist, and `setupSeriesScroll()` (`series.html:248-254`);
* `fetchNextSeriesRows` requests `/api/series/rows?offset=0&limit=50`, inserts `data.html` with `insertAdjacentHTML('beforeend')`, updates the remaining count, re-inits Lucide icons, re-applies the current search filter, then schedules the next batch after 60 ms while `has_more` is true (`series.html:108-169`);
* `IntersectionObserver` (rootMargin 300 px) on the load-more container calls `loadMoreSeriesRows()`; that function first reveals any `.series-row-deferred` rows (a legacy server-side deferral class that the current row chunk never emits, so it falls through to `fetchNextSeriesRows()`) (`series.html:171-192,236-246`).

**Row chunk contract.** `/api/series/rows` renders `templates/partials/series_table_row_chunk.html` with the request context; the table body is replaced/extended by the client. Each row exposes `class="series-row"` and `data-name="<lower name> <lower authors> <lower publisher> <lower tags>"`, which is the only input to the client-side filter (`row:2`). `tag_display` is a Jinja **global** registered at startup (`main.py:279-285`), so the `{% if tag_display is defined %}` branches in the row (`:42-54`) and dashboard (`index.html:189-197`) are always taken; the raw `s.tags` fallback markup is unreachable (dead template branch, not a separate feature).

**Toggle.** `toggleMonitored(id)` posts and reloads the whole page on success (`series.html:256-267`). Server side `UPDATE series_tracking SET is_monitored = 1 - is_monitored` and re-reads the value (`database.py:2145-2152`). There is no existence check: a missing id returns `{"status":"success","is_monitored":false}` with HTTP 200, and the UI still reloads.

**Website link.** `editSeriesLink`/`saveSeriesLink` open the modal and POST `{website_url}` (`series.html:303-333`). The request model forbids extra keys and validates via `security.validate_stored_website_url()`: empty string clears the link, anything else must be an absolute http(s) URL (so `javascript:`/`data:` are rejected with 422 before the DB) (`schemas.py:561-572`; `security.py:301-315`). `update_series_link` does not verify the series exists (`database.py:2154-2158`), so a stale id still returns success. The anchor is rendered through the `safe_url` Jinja filter (`main.py:300`; row `:17`).

**Backfill.** `backfillSeries(id, name, btn)` posts `{max_pages: 3}`, shows a toast, and relies on the global backfill banner/polling for progress (`series.html:269-301`). The API validates `max_pages` 1–500 (default 3) (`schemas.py:575-578`), and the UI never lets the user choose the count (3 is hard-coded at `series.html:281`; the shared Releases/Prioritized helper also hard-codes 3 at `base.html:907`).

**Blacklist.** The row calls the shared `blacklistSeriesGlobal` → `POST /api/blacklist {name, reason}` (`base.html:821-850`). The database call is a deliberate, non-precedence write: insert blacklist row, set all releases of that name `BLACKLISTED`, set the tracker `is_monitored=0` (`database.py:2342-2360`). The UI animates the row/card away.

**Match-to-Calibre.** The modal (`series.html:353-435`) searches `/api/calibre/series?search=…`, auto-selects the first hit, then POSTs `{calibre_series_id}` to `/api/series/{id}/match-calibre`. The endpoint resolves the tracker from `db.get_all_series()`, resolves the target from a live Calibre snapshot (falling back to synced tracker rows), calls `db.link_tracked_series(tracker.name, target, actor)` and invalidates the grouped cache (`main.py:3926-3959`). Because the target row comes from the scanner snapshot, it carries `volumes` (list) but **not** `owned_volumes_json`; `link_tracked_series` only reads `owned_volumes`/`owned_volumes_json` (`database.py:1897-1907`) and, in the source-only branch, writes `owned_volumes_json = json.dumps(sorted(volumes))` (`database.py:1946-1951`) — i.e. `[]`. The consequence (see open question Q2) is that a manual link can blank the tracker's owned-volume set until the next Calibre sync, which also affects release reconciliation in the meantime. The first (merge) branch preserves `target_vols` (`database.py:1920-1937`), and the `sync_calibre_series` upsert protects non-empty old values (`database.py:1859`).

**Nav/mobile.** The page is reached from the desktop "Library" dropdown (`base.html:127-145`) and the mobile "Library" group (`base.html:227-230`); `active_page:"series"` is set by the view. The table is wrapped in `overflow-x-auto` (`series.html:34`), so on narrow viewports it scrolls horizontally instead of reflowing.

### 2.3 Calibre integration: scanner, sync, reconcile, link/repair

#### 2.3.1 Zero-lock reader

`CalibreMangaScanner.__init__` accepts either the library directory or a direct `metadata.db` path (`scanner.py:17-19`). `is_available()` requires the file to exist and be readable (`:21-22`). `_get_connection()` builds a SQLite URI `file:<abs>?mode=ro&immutable=1` with `PRAGMA query_only=ON` and `busy_timeout=2000`; if the SQLite build rejects `immutable=1` it falls back to `mode=ro` (`:24-41`). Nothing is ever written or locked; `fetch` errors are caught by each public method and degrade to `[]`/`{}`/counts of zero after logging (`:76-80,183-188,238-243`).

#### 2.3.2 Series extraction

`get_all_series()` (`scanner.py:82-188`) runs one grouped query over Calibre's `series`/`books`/`books_series_link`/`books_authors_link`/`authors`/`books_publishers_link`/`publishers`/`books_tags_link`/`tags`, producing per series: `series_id`, `series_name`, `book_count`, `latest_index = MAX(books.series_index)`, group-concatenated authors, first linked publisher, group-concatenated tags. It then resolves owned volumes through `get_series_volumes_map()` keyed by lower-case name and (fallback) normalized name, sets `s["volumes"]`, and raises `latest_index` to the max owned bundle volume (`:136-151`). Finally it adds *standalone* books (no series link) whose parsed title yields a series+volumes as synthetic series with `series_id = book.id + 1000000`, `book_count = 1` (`:153-181`). That synthetic id is not a real Calibre series id; it is stable only as long as the Calibre book id is, and it can be selected in every Calibre-series picker (SER-036) because those consume the same snapshot.

#### 2.3.3 Volume map

`get_series_volumes_map()` (`scanner.py:190-243`) reads every book row (title, series name, `series_index`) and delegates to `parse_calibre_volumes_and_series(title, series, index)` (`parser.py:1934-2133`). That parser starts from `series_index` and additionally expands bundle notations (`v01-02`, `Vol. 1-2`, `v01+v02`, `v01, 02`), with guards for reversed ranges and bracket-delimiter false ranges (`parser.py:1938-1966`). The resulting volume set is stored under up to four keys per row: lower-case series name, normalized series name, Calibre series id as `str` and as the same id value, plus every `split_series_candidate_parts()` part of a dual title (`scanner.py:222-236`). This is what makes `get_owned_volumes()` work by id, by name, by normalized name or by either half of a dual title (`matcher.py:63-105`).

#### 2.3.4 Sync algorithm

`MangaScheduler.sync_calibre_library()` (`scheduler.py:3115-3209`) executes under a non-blocking `_calibre_sync_lock`; a concurrent call returns `{"success": True, "message": "Calibre sync already in progress", synced_series: 0, resolved_requests: 0}` (`:3120-3127`). Steps:

1. Resolve `calibre_path` setting (fallback `CALIBRE_DIR`); if `metadata.db` is not accessible return `{"success": False, message: "Calibre library not accessible at: …"}`, zero counts (`:3131-3136`).
2. `series_list = scanner.get_all_series()`; `volumes_map = scanner.get_series_volumes_map()` with progress callbacks (`:3138-3143`).
3. `db.sync_calibre_series(series_list, volumes_map=volumes_map)` — the tracker upsert (SER-031). Each row: `INSERT … ON CONFLICT(name) DO UPDATE`, with these merge rules (`database.py:1846-1863`):
   * `calibre_series_id`, `book_count`, `latest_index` always overwritten;
   * `authors`/`publisher`/`tags` overwritten **only when the incoming value is non-empty**;
   * `website_url` only filled when the stored value is empty/NULL (so a user-edited link survives);
   * `owned_volumes_json` only filled when the incoming JSON is not `"[]"` (so a failed volume-map read cannot wipe ownership);
   * `is_monitored` is *not* in the update list (a user toggle survives sync);
   * `last_sync = CURRENT_TIMESTAMP`;
   * the default `website_url` for new rows is a MangaUpdates search URL derived from the name (`database.py:1830`).
   * after a non-empty batch the grouped cache is invalidated and `auto_link_tracked_series()` runs inline (SER-032, `database.py:1864-1869`).
4. Build `MangaCalibreMatcher(series_list, volumes_map, metadata_cache, staged_volumes_map=self._staged_volumes_map())` (`scheduler.py:3149-3151`). The matcher pre-builds `CalibreSeriesIndex` once so per-release matching is indexed, and carries the staged-ownership map so staging counts as owned (`matcher.py:10-22`).
5. Resolve pending requests: `matcher.resolve_pending_requests(db.get_pending_requests())`; for each resolved row, `update_release_status(target_status, enforce_precedence=True)` so only promotion to `IN_CALIBRE`/`IN_STAGING` is allowed, audit `REQUEST_RESOLVED`, and fire the "acquired" webhook for IN_CALIBRE (`scheduler.py:3153-3174`; precedence `database.py:272-307`).
6. `_reconcile_releases_with_calibre(matcher)` — bounded incremental reconcile (SER-034).
7. `expand_unresolved_complete_releases(matcher=matcher)` — complete-series expansion (SER-035), failure only logged.
8. Log `CALIBRE_SYNC` summary and run `process_downloads_to_staging(actor="scheduler")` so a newly registered series immediately unlocks waiting downloads (`scheduler.py:3191-3196`).
9. Return `{success, message, synced_series, resolved_requests}`. Any unexpected exception becomes `{success: False, message: "Error syncing Calibre library: …"}` + `CALIBRE_SYNC_ERROR` audit (`:3203-3207`).

The manual endpoint wraps this in a persisted task (`_recorded_task`, `main.py:1267-1297`) and returns the sync result merged with `task_id`/`task` (`main.py:1916-1930`).

#### 2.3.5 Auto-link on sync

`db.auto_link_tracked_series()` (`database.py:2020-2085`) runs over every tracker with `calibre_series_id IS NULL`. It builds candidates from `series_name_candidates(name)` plus delimiter parts plus colon parts, collects **exact** normalized hits against the Calibre list:

* exactly one distinct exact target → link it;
* more than one → skip (ambiguous, left for manual match);
* none → fuzzy `CalibreSeriesIndex.match(name, threshold=0.90)`;
* owned volumes recovered from the supplied `volumes_map` by id/name/normalized name;
* calls `link_tracked_series(..., owned_volumes=...)`; per-tracker exceptions are debug-logged and the loop continues (`:2078-2084`).

Neither the sync nor the auto-link path writes an activity-log row for a re-key, so automatic renames are invisible in the UI audit trail (the only visible trace is the changed series name; see Q7).

#### 2.3.6 Link/merge service

`db.link_tracked_series(source_name, calibre_series, actor, owned_volumes=None)` (`database.py:1872-2018`):

* target name from `series_name`/`name`; `calibre_id` from `series_id`/`id`/`calibre_series_id`; book count/latest index coerced; owned volume set from the explicit arg, else `calibre_series["owned_volumes_json"]` (`:1882-1907`);
* looks up both the source and the target tracker case-insensitively (`lower(trim(name))`, `:1910-1913`);
* **merge branch** (both rows, different ids): `UPDATE` target with `calibre_series_id = COALESCE(new, old)`, `book_count/latest_index = MAX(old,new)`, `is_monitored = MAX(source, target)`, fills empty authors/publisher/tags, unions owned volumes, re-keys source releases to the target name, deletes the source tracker (`:1918-1944`);
* **rename branch** (source only): renames the tracker to the target name and re-keys its releases (`:1945-1957`);
* **metadata-only branch** (target only): fills Calibre id/counts/owned (`:1958-1964`);
* then a duplicate-merge pass: any other tracker whose normalized name equals source/target or is a `series_name_candidates` member of the target is merged (monitored MAX, owned union, releases re-keyed, tracker deleted) (`:1965-2006`);
* registers an alias override `source → target` when the normalized names differ (best-effort) (`:2008-2012`);
* invalidates the grouped cache and returns `{status, source, target, calibre_series_id, merged, rekeyed_releases, owned_volumes, book_count}`.

User vetoes are never touched (the SQL only updates `series_name`/`updated_at` for releases), confirmed by `tests/test_tracked_series_calibre_link.py:130-131`.

#### 2.3.7 Link audit / link repair

`GET /api/series/link-audit` (`main.py:3962-3987`) takes the live snapshot (or synced rows), and for every tracker without `calibre_series_id` calls `match_manga_series_in_calibre(name, snapshot, threshold=0.90)`; each hit becomes `{series_id, source, target, calibre_series_id, book_count, confidence:"fuzzy_or_candidate"}`. It is a pure read. No template references this endpoint.

`POST /api/series/link-repair` (`main.py:3990-4010`) computes `dry_run = body.dry_run is True or body.confirm is not True`; dry-run re-invokes the audit handler and re-parses its `JSONResponse.body`; otherwise it calls `db.auto_link_tracked_series(snapshot, None, actor)` and returns `{status, dry_run:false, linked:[…], count}`. Passing `volumes_map=None` means owned volumes for newly linked trackers resolve to the empty set (Q2). Neither endpoint is reachable from the UI.

### 2.4 Identity and matching algorithms

#### 2.4.1 Naming model (the central invariant)

There are exactly two names (`database.py:310-328`):

* **stable matching key** — persisted in `series_tracking.name` and `releases.series_name`; equal to the Calibre library name when the series is in Calibre, else the user-typed name. Matching, grouping, dedupe, templates and ComicInfo tagging all key off it. External metadata must never rewrite it.
* **display title** — cosmetic; AniList canonical English only when the cached metadata row is authoritative (`source=='anilist'` and non-empty canonical), else the stable key (`database.py:331-357`). Resolved at render time (`get_display_title`, grouped builder, search) and never fed back into a lookup.

The historical defect this model fixes is documented in `PROJECT.md:157-164`: AniList renamed series ("Dungeon Meshi" → "Delicious in Dungeon") and broke Calibre matching/ComicInfo. The repair for already-corrupted rows is SER-053.

#### 2.4.2 Normalization and candidate generation

* `normalize_text` (SER-040) is the key function for all comparisons (`parser.py:10-18`).
* `split_series_candidate_parts` (SER-041) is the single delimiter implementation (`parser.py:20-56`), explicitly replacing copy-pasted regexes across the codebase.
* `clean_series_for_matching` conservatively removes brace tags and trailing "as range" clauses while preserving real brackets/parentheses (`parser.py:649-670`), e.g. `"[OSHI NO KO] {Plus}" → "[OSHI NO KO]"`.
* `series_name_candidates` (SER-051) returns the parser primary, `[bracket]` and bracket contents, cleaned display, underscore-stripped form, colon parts and the cleaned-for-matching variant, deduped by normalized value (`parser.py:2219-2255`).

#### 2.4.3 Index matching rules

`CalibreSeriesIndex` (`parser.py:1737-1917`) is the single matcher implementation; `match_manga_series_in_calibre()` constructs a throw-away index (`:1920-1932`) while hot callers (matcher, auto-link, audits) build it once. Candidate order in `match()` (`:1829-1917`):

1. the whole title, then every delimiter part, then colon parts and cumulative colon prefixes (`:1836-1862`);
2. exact normalized lookup (`_by_norm`, `:1864-1868`);
3. substring containment only when the query normalized length ≥ 6, iterating only length buckets that satisfy `len_ratio ≥ 0.70`, earliest library order wins (`:1870-1887`);
4. fuzzy: candidate indices come from shared rare tokens (bucket ≤200) and tiny 3-gram buckets (≤20), with a length window derived from the ratio bound; only scores strictly above the threshold (default 0.80) are considered; the **distinctive-head guard** rejects candidates whose only shared meaningful token is a generic suffix, logging `Rejected fuzzy series match` (`:1689-1734,1789-1917`). Tests pin both the rejection (`Red Ranger` vs `Level 0 Demon King`) and the preserved positives (typos, colon subtitle, "Solo Levelling"→"Solo Leveling", "Solo Leveling: Ragnarok"→"Solo Leveling") in `tests/test_false_fuzzy_series_matches.py:26-37`.

#### 2.4.4 Identity resolution ladder

`scheduler.resolve_series_identity(raw_series_name, matcher=None, meta=None)` (`scheduler.py:622-788`) is used for every ingested release (feed poll, backfill) and by the audits. It resolves metadata up front (cached; `:648-656`), validates that the metadata entry belongs to the release's own spelling via `_identity_entry_aliases` (SER-048, `:556-620`), then tries in order:

0. explicit user alias override lookup — raw normalized name, then every candidate and cleaned source (`:713-733`);
1. Calibre scanner match for each alias (`:735-739`);
1b. Calibre-origin tracker rows by normalized alias (works when the library mount is unavailable) (`:741-746`);
2. user-typed tracker rows (`:748-752`);
3./4. AniList canonical English then romaji, used only as lookup bridges into Calibre/tracked names (`:754-767`);
5. reverse alias claims from `get_authoritative_alias_index()`, preferring a single Calibre claim, then a single typed claim; multi-claim names are logged and keep the parsed key (`:769-786`);
6. fallback: the parsed name itself is the stable key, source `raw` (`:787-788`).

Returned payload: `{raw_name, stable_name, display_name, source, meta, alt_names, anilist_id, calibre_match}`. Callers persist `stable_name` (e.g. `_process_release` at `scheduler.py:3957-3993`; `POST /api/discovery/track` at `main.py:4135-4171`).

#### 2.4.5 Spinoff guard and attribution

`_identity_entry_aliases` (`scheduler.py:556-620`) only accepts aliases from the release's *own* AniList entry. If the combined spelling only resolved through a delimiter/parent fallback, each part is re-resolved independently and accepted only when all authoritative parts agree on one `anilist_id`; otherwise `({}, [raw], None)` is returned. `_process_release` then applies the backfill target guard: during a targeted parent refresh, a release is skipped (`SPINOFF_SKIPPED`, audited with title/parsed/resolved/target ids) when its resolved `anilist_id` differs from the target's, or when its parsed and stable names are both outside the target's alias set (`scheduler.py:3968-3999`). `detect_series_titles` deliberately avoids harvesting titles from stored releases to prevent a spinoff row from becoming an "alternate title" of the parent (`scheduler.py:4130-4134`). Historical mis-attributions are repaired by SER-079/080.

#### 2.4.6 Legacy re-key repair

`repair_rekeyed_series_names` (`database.py:3868-3956`) builds the authoritative alias index + Calibre-origin name set, scans distinct `releases.series_name` values and, when a release name is claimed by exactly one tracker, re-keys it to that stable name. Multi-claim names are resolved only through `_prefer_calibre_duplicate_claim` (`database.py:3832-3866`): exactly one Calibre-origin claim and every other claim must be a *bidirectional* AniList alias of it; otherwise the row is logged and skipped. It is idempotent, invalidates the grouped cache, and audits `SERIES_KEYS_REPAIRED`. It runs at every startup from `perform_initial_sync` and from the renamed debug action.

### 2.5 Per-series refresh / backfill

**Endpoints.** Both `/api/series/{id}/backfill` and `/api/series/backfill` validate a Pydantic model (`max_pages` 1–500, default 3; name required for the by-name variant) and call `_start_backfill_task(name, max_pages)` (`main.py:4013-4046`, `4049-4072`; `schemas.py:575-590`). `_start_backfill_task`:

1. creates a persisted `background_tasks` row (`kind="backfill:series"`, title `Refresh series: <name>`, scope `<name>`, actor from the request) and a `TaskReporter` (`main.py:4018-4023`);
2. calls `scheduler.run_series_backfill_async(...)`, which owns the global `is_backfilling` flag. If the flag was already set it returns `{"status": "in_progress", "message": "Another backfill is already running"}` **without** starting a worker (`scheduler.py:4717-4721`);
3. if `is_backfilling` is false immediately after the call (job already finished or refused), the task is closed as `success` with the returned result;
4. otherwise it starts a daemon watcher thread that polls `app_settings.backfill_progress` every 0.75 s, reports it into the task, and marks the task success when the flag clears (`main.py:4028-4045`). The watcher does not distinguish "this job finished" from "another job that held the flag finished", which is the SER-065 defect.

**Worker.** `run_series_backfill_async` sets `is_backfilling=True`, `stop_backfill_requested=False`, writes `backfill_status="running"` and an initial `backfill_progress`, then runs `backfill_series()` on a daemon thread; on completion it writes `backfill_status="idle"` and the final message, on exception `backfill_status="error"` + `Series refresh failed: …`, and always clears the flag (`scheduler.py:4710-4753`).

**`backfill_series()` phases** (`scheduler.py:4460-4708`):

1. **Title detection** — `detect_series_titles(clean_name)` returns primary + alternates; the display string joins them with " & " (`:4471-4478`). The primary is `detected_titles[0]` (the requested name first), alternates are the rest.
2. **Phase 1** — `refresh_series_provider_metadata(clean_name, actor)` with `backfill_progress` text; any exception is caught, audited as a failed `SERIES_METADATA_REFRESHED`, and converted to `{updated:False, skipped:True, reason}` (`:4483-4495`).
3. **Phase 2** — `refresh_series_calibre_info(clean_name, actor)` with the same isolation (`:4497-4512`).
4. **Reconcile** — a fresh matcher (`_series_refresh_matcher`, `:4194-4201`) and `_reconcile_series_releases(clean_name, matcher)` recompute missing volumes/status for that series only (`:4514-4524`, `:4438-4458`). If `changes` mention "total volumes" or "book count", `expand_unresolved_complete_releases(matcher, series_name=clean_name)` runs (`:4525-4535`).
5. **Identity context** — `known_series_keys = _known_series_keys(matcher)` (tracked + Calibre names) and `target_identity` built from `resolve_series_identity(clean_name)` with all detected titles and alt names added to `alias_norms` and the AniList id recorded (`:4537-4560`).
6. **Crawl** — `NyaaMangaProvider.search_series(primary_title, max_pages, alt_names=alt_titles, stop_check=lambda: self.stop_backfill_requested, progress_cb=…)`; crawl exceptions become `crawl_error` and `releases=[]` without aborting the Job (`:4567-4590`).
7. **Ingest loop** — each result goes through `_process_release(..., known_series_keys=…, target_identity=…)`; `SPINOFF_SKIPPED`/`CHAPTER_ONLY`/`LIGHT_NOVEL`/`JPEGXL` are skipped without counting; found/requested/sent counters; webhook for new requests; auto-download policy per SER-064 (`:4596-4660`).
8. **Summary** — a human summary assembling metadata/calibre/backfill texts plus "Nyaa crawl failed: …" when applicable, audited `SERIES_BACKFILLED`, returned with `status:"partial"` on crawl failure and a structured `summary` block (`:4662-4708`).

Phase behaviour is pinned by `tests/test_series_refresh.py`: force refresh bypasses a fresh cache and keeps values on outage (`:117-166`); metadata phase never changes the stable key while updating description/authors/covers (`:173-215`); provider outage keeps values and reports a skip (`:221-243`); the Calibre phase updates authors/publisher/tags/counts/owned volumes and skips cleanly when unavailable (`:275-309`); reconcile only touches the target series (`:315-336`); a total-volume change triggers scoped expansion (`:342-387`); phase isolation (`:394-447`); and both endpoints return the same pending structure (`:453-502`).

### 2.6 Metadata enrichment, covers, tags, search

#### 2.6.1 AniList (`backend/app/metadata/anilist.py`)

* Rate limiting: a module-level sliding-window `AniListRateLimiter(90 requests / 60 s)` serialises all queries; 429 responses set a `Retry-After` cooldown and the query is retried once (`anilist.py:17-87,292-384`). `X-RateLimit-Remaining ≤ 2` adds a 1 s pace (`:56-71`).
* Query: one GraphQL call for `Media(search, type: MANGA, format_in:[MANGA, ONE_SHOT])` returning titles, covers, volumes, chapters, status, description, genres, ranked tags (with spoiler flags), synonyms (`:89-119`). `NOVEL` results are rejected (`:314-316`). Canonical title preference is English → heuristic synonym → romaji/term (`:318-332`).
* `resolve_manga_series(raw, force_refresh=False)` (`:146-268`):
  * cleans the term (`clean_query_term`, `:121-134` strips brackets/extensions/volume tokens/format words);
  * cache fast path: returns a cached row only when `source not in ('fallback','cleaned')` **and** `total_volumes` truthy; also tries candidate delimiter parts (`:190-206`);
  * without `force_refresh`, on a cache miss or unusable row it queries AniList, then candidate parts, then a hyphen/colon prefix (`:208-228`);
  * `force_refresh` always queries, but if the provider fails/returns nothing authoritative and a cached authoritative row exists, the cached row is returned instead of blanking (`:230-234`);
  * if nothing at all is found, returns a `source:"fallback"` payload echoing the raw name (`:236-252`);
  * authoritative results are cached under the search term plus romaji and canonical keys (`:254-266`).
* Consequence: AniList cache entries have **no TTL**. A cached row with a canonical title but no `total_volumes` never satisfies the fast path and re-queries on each use; an authoritative row with a volume count is effectively permanent until a per-series refresh forces an update.
* `is_authoritative_metadata()` (`database.py:331-344`) is the gate for display titles, aliases and the search index: only `source == "anilist"` + non-empty `canonical_title`.

#### 2.6.2 MangaUpdates (`backend/app/metadata/mangaupdates.py`)

* Public REST search (`stype:"title", perpage:1`, 5 s timeout) then a second `/v1/series/{id}` call to extract `(<N> Volumes)` from the status string → `total_volumes` and `FINISHED`; `ongoing|releasing` → `RELEASING` (`:162-240`).
* Cache key is `mu:<lower name>`; the cache row smuggles the MangaUpdates URL into `romaji_title` (the only spare text column) and is only written when a description exists (`:87-107,145-158`).
* Descriptions are cleaned of HTML, BBCode, Markdown links, raw URLs, publisher prefixes and orphan spaces before punctuation (`clean_description_text`, `:26-59`).
* Used as phase-1 fallback and for Discovery "fetch info" (`main.py:4181-4209`, which also caches the cover).

#### 2.6.3 Covers (`backend/app/covers.py`, `main.py:1955-1977`)

Cache files live in `DATA_DIR/covers`, named `sha256(url)[:24] + ".jpg"` (`covers.py:96-107`). `_download_to_cache` (`:142-245`): returns an existing non-empty file immediately; otherwise streams the URL with `User-Agent`/`Accept` headers, validates **every** redirect hop against `security.validate_public_url` (SSRF, DNS-rebinding re-validation), caps redirects at 3, timeout 7 s, enforces the 5 MB limit from `Content-Length` *and* while streaming, validates JPEG/PNG/WEBP/GIF magic bytes, writes to `.tmp` and renames atomically; on any failure it deletes the temp file and returns `None`. `get_or_download` runs the blocking work in a thread (`:123-134`).

`GET /api/covers?url=…` (`main.py:1957-1977`): empty url → inline SVG placeholder 200; a cached file → `FileResponse` with 10-day cache header and a media type derived from the **file extension**, which is always `.jpg`; failure/miss → the same placeholder SVG with 200. The global `X-Content-Type-Options: nosniff` header (`main.py:269-277`) applies, so a PNG stored under `.jpg` is declared `image/jpeg` (Q6).

Covers are refreshed onto releases by the metadata phase (`db.update_series_cover_on_releases`, `database.py:2218-2231`) and warmed into the cache there (`scheduler.py:4294-4302`). The `/series` row cover, by contrast, comes from the newest release row's `cover_url` subquery (`database.py:2096-2101`), never from the metadata cache or Calibre directly.

#### 2.6.4 Provider tags and display

AniList genres/tags are flattened by `tag_display.tags_from_metadata()` (genres first, rank 100000; provider tags ranked; spoiler flag preserved) (`tag_display.py:30-62`). `encode_tags()` stores the ordered dict list as JSON plus an exact-token `|name|name|` text column for cheap filtering (`:65-66`). `display_tags()` sorts genres first then rank, hides spoilers, dedupes by normalized name and returns `{chips, overflow, all, tooltip}` with per-view limits (`:84-107`). Denormalisation writes both `releases.tags_json/tags_text` and `series_tracking.tags_json/tags_text` (`database.py:1307-1337`; `scheduler.py:4094-4099`).

`GET /api/tags` (`database.py:1339-1407`) unions: metadata-cache `genres_json` + `tags_json`, then `releases` and `series_tracking` denormalised tags; spoilers excluded; counts and max rank accumulated; ordering exact → prefix → substring → unsupported, then count/rank/name; limit clamped 1–50 (endpoint validates 1–50 too).

#### 2.6.5 Unified search (`main.py:3498-3514`, `backend/app/search.py`)

`unified_search(db, query, limit)`:

* normalizes the query (blank after normalization returns an empty success payload; the endpoint itself raises 400 for blank input);
* builds an authoritative metadata index (normalized search_term/canonical/romaji → cache row) and the tracked-series alias sets (`get_authoritative_alias_sets`, `database.py:3751-3791`);
* matches tracked series by substring on the stable normalized name first, else on any authoritative alias, ranking `stable_name` (0) < `normalized_name` (1) < `anilist_alias` (2); each hit returns `stable_name`, `display_title`, `match`, `matched_alias`, authors, publisher, `is_monitored`, `cover_url` (`search.py:113-149,173-181`);
* calls `db.search_releases()` (`database.py:3007-3057`) which LIKE-escapes the term (`escape_like`) and searches release title, series name, primary group and every `release_groups_json` member, ordered title-match → series-match → group-match → other, then newest; rows carry `volumes`, `missing_volumes`, `is_complete`, `release_groups`, `display_series_name`;
* response `{status, query, limit, series:[…], releases:[…], counts:{series,releases}, total}`.

No UI consumer exists (grep over `backend/app/templates` for `api/search` returns nothing); tests cover alias resolution, romaji, wildcard literalness and limits (`tests/test_m2_features.py:678-731`).

### 2.7 Audits and repairs

#### 2.7.1 False fuzzy series matches (`backend/app/false_series_matches.py`)

`audit_false_series_matches(db, calibre_series, limit=2000)` (`:25-82`):

* loads up to 2000 releases, newest first (`ORDER BY id DESC`);
* skips rows where stored series == title-parsed series (normalized), or where either is empty;
* re-runs the fixed matcher on the parsed title against the live snapshot; if that maps to the stored name it is not suspicious;
* if the fixed match maps to a *different* real series, that name is the suggested target; otherwise the suggested target is the parsed title, but the row is only flagged when the stored name is itself a known Calibre series (otherwise a custom name is not a false match);
* flags `can_repair = status not in (IGNORED, BLACKLISTED)`; reason string explains "no longer matches under distinctive-head fuzzy rules".

`repair_false_series_matches(db, release_ids, calibre_series)` (`:85-151`) processes ids individually: missing release/parse → skipped; vetoed rows keep their status but are still re-keyed; otherwise it recomputes status from the Calibre match (IN_CALIBRE when no missing volumes, else REQUESTED) or falls back to DISCOVERY (vol 1/empty/complete/bundle) or NEW; writes `series_name`, `status`, `missing_vols_json`, audits `FALSE_SERIES_MATCH_REPAIRED`; per-row exceptions count as failed; finally invalidates the grouped cache. Endpoint requires `confirm:true` and 1–500 ids; it does not consult `can_repair` itself (the UI disables those checkboxes, but the API accepts them).

#### 2.7.2 Series attribution / spinoff (`scheduler.audit_series_attribution`, `repair_series_attribution`)

`audit_series_attribution` (`scheduler.py:829-911`) scans **all** releases with a non-empty series name; only current names that are tracked are considered. For each, the title is re-parsed; if the parsed series differs from the stored one and is not an authoritative alias of it, `resolve_series_identity(parsed_series)` proposes the stable name. Multi-part names whose identity source is still `raw` are marked ambiguous (`safe_to_repair=false`) and never guessed. Record fields: `id,title,current_series,suggested_series,identity_source,anilist_id,safe_to_repair,status,reason`; summary counts scanned/repairable/ambiguous.

`repair_series_attribution` (`scheduler.py:913-954`) re-runs the audit and applies only `safe_to_repair` rows in the explicit id list; writes `series_name` (never status), audits `SERIES_ATTRIBUTION_REPAIRED`; idempotent (second run skips). Tests: `tests/test_spinoff_identity.py:172-207`.

#### 2.7.3 Suspicious volume ranges (`database.py:1643-1742`)

Audit scans every release. For each with stored volumes: parse the title; if the title contains an explicit volume marker (`vol|volume|vols|v.<number>`) and is not a chapter title, it is trusted at any magnitude. Otherwise it is flagged when any of: all stored volumes ≥ 100; the title now parses chapter-only; or the title has chapter ranges and no volumes. `safe_to_repair` is true only when the parser supplies **no** suggested volume set (chapter-only reclassification); rows with suggested volumes are reported but not repairable. Repair clears `volumes_json`/`missing_vols_json`/`volume_start`/`volume_end`, sets `is_bundle=0` and `status='IGNORED'`, audits `CHAPTER_RANGE_REPAIRED`, invalidates the grouped cache. The endpoint truncates to the first 200 ids.

### 2.8 Ownership and completeness rules (the series-domain algorithm)

**Definitions.**

* *Calibre-owned volumes* for a series = union over `volumes_map` keys (Calibre id as str and as value, lower-case name, normalized name, dual-title parts) plus any `owned_volumes_json` already carried by the matched row (`matcher.py:63-105`). The map itself expands bundle titles (SER-024).
* *Staged-owned volumes* = `staged_ownership.get_staged_volumes_map()`: a real walk of `staging_dir` parsing every `.cbz/.cbr/.zip/.epub` filename into series+volumes (temp/hidden/symlink entries skipped), unioned with every candidate key (normalized name + delimiter parts); plus a bounded fallback for up to 2000 `COMPLETED` + `routed_at` release rows whose filenames were unparseable, used only when the file scan has no key for that series (`staged_ownership.py:54-113`). The map is cached 60 s (`:24,116-129`), and explicitly invalidated after mutations (`:132-136`).
* *Combined owned* = Calibre ∪ staged (`matcher.py:127-131`); staging therefore counts as owned but remains visually "In staging" (README:386-403; `resolve_status` keeps `IN_STAGING` distinct).

**Per-release check** — `MangaCalibreMatcher.check_release()` (`matcher.py:133-214`):

1. Resolve the series by the release's `series_name` (index matching, then alt names, then metadata cache canonical/romaji bridges — `find_series`, `:29-61`).
2. If matched and the release has volumes: `missing = [v for v in volumes if round(v,2) not in combined]`.
   * no missing and all in Calibre → `IN_CALIBRE` (reason "already in Calibre library");
   * no missing but some only staged → `IN_STAGING`;
   * missing → `REQUESTED` when `auto_request_monitored` else `NEW`, with a reason listing missing volumes, owned count and staged-missing volumes.
3. If matched and no volumes → `REQUESTED`/`NEW` ("Release detected for monitored series").
4. If not matched: `DISCOVERY` when `volumes == [1.0]`, or no volumes, or `is_complete`, or (`volume_start == 1.0` and bundle); otherwise `NEW`.

**Status is never blindly applied.** Every automated write goes through `resolve_status(existing, incoming)` (`database.py:272-307`): incoming blank keeps existing; unset adopts incoming; `IGNORED`/`BLACKLISTED` are absolute for automation; `IN_CALIBRE` is the acquisition terminal (IN_STAGING never downgrades it); otherwise the higher rank wins (`DISCOVERY/NEW` 0, `REQUESTED` 1, `IN_STAGING` 1.5, `IN_CALIBRE` 1, veto 2 — note `_STATUS_RANK` has `IN_CALIBRE: 1` with a special-case branch before the comparison, `database.py:253-261,299-307`).

**Group-level reconciliation** (Releases page / grouped API, shared with the series identity model) — `_build_grouped_releases()` (`database.py:4004-4544`):

* loads releases (media-guarded, LN excluded), decorates volumes/missing/groups/priority;
* loads all `series_tracking` rows into `tracked_map`/`norm_tracked_map` and the whole metadata cache into `cache_map`/`norm_cache_map`; builds an alias→stable map only for unambiguous claims (`:4143-4165`);
* resolves Calibre identity per release: normalized name → metadata canonical → candidate parts → strict ≥70 % substring containment fallback (`:4205-4232`);
* computes `stable_name` (alias recovery / Calibre match), cosmetic `display_title`, `romaji_name`, and groups by **normalized display title** (`canonical_key`), upgrading a group's Calibre identity if a later release in the same display group carries it (`:4234-4331`);
* complete-series rows with no volumes are expanded from `total_volumes` (release or metadata) to `1..N`, then missing volumes are recomputed against `owned_volumes_json` ∪ staged volumes and the status is recomputed through `resolve_status` (`:4333-4404`); **the GET path only decorates in memory** (`persist=False`); the explicit `reconcile_release_volume_state()` runs the same computation with `persist=True` (`database.py:3989-4002`);
* dedupes releases by guid or (link, volume set, sorted group set, revision) (`:4406-4421`), flags superseded revisions (highest `revision` wins) (`:4447-4470`), aggregates `all_volumes`, `missing_volumes`, counts, `in_calibre` fallback (`has_in_calibre` or any owned volume → true, `:4477-4482`);
* orders groups: wanted+prioritized → wanted → prioritized → monitored → discovery → in-calibre → other, with user priority first inside a tier (`:4490-4508`); supports `post_date` and `series` sorts;
* caches the result for 5 s keyed by `status_prioritized_sort` when there is no search term (`:4024-4031,4525-4526`), and `_invalidate_grouped_cache()` is called by every series-domain mutation.

**Persistence of reconciliation.** The read path is side-effect free, but the worker loop runs `db.reconcile_release_volume_state()` at startup and every 5 minutes (`scheduler.py:5166-5173,5202-5209`), so the database converges to what the Releases UI shows. Complete-release expansion against metadata is also attempted at startup/5 min and after every Calibre sync (`scheduler.py:5161-5165,5202-5209,3181-3185`).

**The `/series` page does not compute completeness.** It renders `book_count` (Owned column) and `latest_index` (Latest Vol) directly from `series_tracking` (SER-011/SER-012); owned-volume sets and missing volumes are only surfaced on Releases/Discovery/cards and via the matcher/auto-download decisions.

### 2.9 Background jobs in this domain

| Job | Where | Cadence / trigger | Effect |
|---|---|---|---|
| Worker loop tick | `scheduler.py:5152-5277` | every 10 s while running | dispatches the checks below |
| Initial sync + legacy re-key repair | `scheduler.py:5157-5160,3212-3241` | once at worker start | SER-029/SER-053 |
| Calibre sync | `scheduler.py:5189-5195` | `calibre_sync_interval_minutes` (default 15 min) and after startup | SER-027…SER-035 |
| Volume-state persistence | `scheduler.py:5202-5209` | every 5 min | `reconcile_release_volume_state()` |
| Complete-release expansion | `scheduler.py:5202-5209,5161-5165` | startup, every 5 min, after sync | expansion writes |
| Per-series refresh | `scheduler.py:4710-4753` | on demand | 3-phase job |
| Historical backfill | `scheduler.py:4755-4773` | on demand (`POST /api/backfill/start`) | separate feature; shares the mutex |
| Provider tag refresh | `scheduler.py:790-827` | on demand, cancellable persisted task | tags backfill |
| False-series/attribution/volume-range audits & repairs | endpoints + modules | on demand, persisted tasks | audits/repairs |
| Calibre sync / backfill / refresh tasks | `background_tasks.run_in_background` / `_recorded_task` | on demand | persisted progress, cancel support |

`GET /api/maintenance/suspicious-volume-ranges` and `GET /api/maintenance/series-attribution` are read-only for release data but still create `background_tasks` rows and `BACKGROUND_TASK_STARTED/FINISHED` activity-log rows (`main.py:1267-1297,2461-2468,2631-2639`). `POST /api/maintenance/false-series/audit` runs inline with **no** task row (`main.py:2508-2516`).

### 2.10 Failure and edge behaviour (series domain)

| Situation | Behaviour | Evidence |
|---|---|---|
| Calibre path missing/unreadable | sync returns `success:false` with message; scanner list methods return `[]`; summary says unavailable; snapshot falls back to synced tracker rows | `scheduler.py:3131-3136`; `scanner.py:43-45,87-88`; `main.py:2855-2863` |
| `immutable=1` rejected by SQLite build | falls back to `mode=ro` connection | `scanner.py:37-41` |
| Concurrent Calibre sync | second caller returns "already in progress" without error | `scheduler.py:3120-3127` |
| AniList rate limit / 429 | limiter waits/retries once; provider failure is a clean skip (phase isolated) | `anilist.py:30-87,292-393`; `scheduler.py:4222-4234` |
| MangaUpdates/AniList both down during phase 1 | cached values kept, skipped:true, audited with provider error | `scheduler.py:4311-4317`; `tests/test_series_refresh.py:221-243` |
| Calibre unavailable during phase 2 | clean skip with reason, tracker untouched | `scheduler.py:4347-4351`; `tests/test_series_refresh.py:297-305` |
| Series not found in Calibre during phase 2 | skip "series not found in the library" | `scheduler.py:4367-4370` |
| Nyaa crawl failure during phase 3 | `status:"partial"`, error in summary, metadata/Calibre changes retained | `scheduler.py:4586-4590,4681-4687`; `tests/test_series_refresh.py:428-444` |
| Another backfill running when refresh starts | endpoint returns `in_progress`; task may later be marked success by the other job's completion (Q1) | `scheduler.py:4717-4718`; `main.py:4028-4045` |
| Unknown series id on toggle/link | HTTP 200 success, no write (toggle false, link returns URL) | `main.py:3826-3837`; `database.py:2145-2158` |
| Unknown series id on backfill | 404 "Series not found" | `main.py:4049-4056` |
| Unknown Calibre id on match-calibre | 404 "Calibre series not found" | `main.py:3945-3946` |
| match-calibre merge with release veto | veto preserved, only series name changes | `tests/test_tracked_series_calibre_link.py:130-131` |
| Owned-volume set after manual link | can be written as `[]` until next sync (Q2) | `database.py:1897-1907,1946-1951`; `main.py:3948-3951` |
| Cover URL unreachable/blocked/too large | placeholder SVG, 200; nothing negative cached | `covers.py:142-245`; `main.py:1963-1977` |
| Cover cache hit for PNG/WebP | served with `image/jpeg` because filenames are `.jpg` (Q6) | `covers.py:101-107`; `main.py:1965-1972` |
| Unified search blank query | 400 "q is required and must not be blank" | `main.py:3510-3512` |
| Unified search wildcards | `%`/`_` matched literally (LIKE escaping) | `tests/test_m2_features.py:723-726`; `security.escape_like` |
| Storage folder unwritable | probe records errno/uid, warning rendered only on Settings/Downloads, `/health.storage.ok=false` | `storage_health.py:30-172`; `main.py:365-394,1040-1058`; templates as SER-087 |
| Scanner summary mtime unchanged after content change | stale `_SUMMARY_CACHE` hit (until mtime changes/process restart) | `scanner.py:47-54` |
| Tracker deleted/renamed in Calibre | no pruning: stale `series_tracking` row survives all future syncs | `database.py:1846-1863` (no DELETE path) |

### 2.11 Endpoint reference (series domain)

All routes are behind the auth middleware (`main.py:240-267`); POST/DELETE are also subject to the configured CSRF Origin/Referer policy.

| Endpoint | Params / body | Response shape | Handler |
|---|---|---|---|
| `GET /` | — | HTML dashboard | `main.py:1138` |
| `GET /series` | — | HTML shell | `main.py:1169` |
| `GET /api/series/rows` | `monitored_only` bool, `offset` ≥0, `limit` 1–200 (50), `search` | `{status, offset, count, total, has_more, html}` | `main.py:1523` |
| `POST /api/series/{id}/toggle` | — | `{status:"success", is_monitored: bool}` | `main.py:3826` |
| `POST /api/series/{id}/link` | `{website_url}` (max `MAX_URL_LENGTH`, http(s) or empty, extra keys rejected) | `{status, website_url}` | `main.py:3832`; `schemas.py:561` |
| `POST /api/series/{id}/match-calibre` | `{calibre_series_id}` int | `{status, source, target, calibre_series_id, merged, rekeyed_releases, owned_volumes, book_count}` | `main.py:3926`; `database.py:2014-2018` |
| `GET /api/series/link-audit` | — | `{status, proposed:[{series_id, source, target, calibre_series_id, book_count, confidence}], count}` | `main.py:3962` |
| `POST /api/series/link-repair` | `{dry_run?:bool, confirm?:bool}` (dry unless `confirm:true`) | dry: `{status, dry_run:true, proposed, count}`; confirmed: `{status, dry_run:false, linked:[...], count}` | `main.py:3990` |
| `POST /api/series/{id}/backfill` | `{max_pages}` 1–500 default 3 | `{status:"started"/"in_progress", message, summary{pending}, task_id, task}` | `main.py:4049`; `schemas.py:575` |
| `POST /api/series/backfill` | `{series_name, max_pages?}` | same | `main.py:4064`; `schemas.py:581` |
| `POST /api/calibre/sync` | — | `{success, message, synced_series, resolved_requests, task_id, task}` | `main.py:1909` |
| `GET /api/calibre/status` | — | `{in_progress, initial_sync_done, last_sync}` | `main.py:1932` |
| `GET /api/calibre/test` | `path` required | scanner summary JSON (or `{available:false,…}`) | `main.py:1937` |
| `GET /api/calibre/series` | `search?`, `source?` | `{status, series:[{id,name,authors,book_count,score}], count}` | `main.py:2322`; `main.py:2278` |
| `GET /api/covers` | `url` required | image bytes (`image/jpeg`/png/webp by extension but always `.jpg`) or SVG placeholder | `main.py:1957` |
| `GET /api/tags` | `q?`, `limit` 1–50 (20) | `{status, tags:[{name,category,count,rank}], count}` | `main.py:1565` |
| `GET /api/search` | `q` ≤200, `limit` 1–50 (20) | `{status, query, limit, series, releases, counts, total}` | `main.py:3498` |
| `POST /api/maintenance/refresh-tags` | — | `{status:"started", task_id, task}` | `main.py:1552` |
| `GET /api/maintenance/series-aliases` | — | `{status, aliases:[row…]}` | `main.py:2573` |
| `DELETE /api/maintenance/series-aliases/{id}` | — | `{status, deleted:1}` or 404 | `main.py:2578` |
| `GET /api/maintenance/series-attribution` | — | `{status, summary{scanned,repairable,ambiguous}, files:[…], task_id, task}` | `main.py:2631` |
| `POST /api/maintenance/series-attribution-repair` | `{release_ids:[int]}` (≤200 applied) | `{status, repaired, skipped, results, task_id, task}` | `main.py:2642` |
| `POST /api/maintenance/false-series/audit` | — | `{status, candidates:[{release_id,title,stored_series,parsed_series,suggested_series,status,missing_volumes,can_repair,reason}], count}` | `main.py:2508` |
| `POST /api/maintenance/false-series/repair` | `{confirm:true, release_ids:[…]}` (1–500) | `{status, repaired, failed, skipped, details, task_id, task}` | `main.py:2519` |
| `GET /api/maintenance/suspicious-volume-ranges` | — | `{status, summary{scanned,repairable,reported}, files:[{id,title,series_name,current_volumes,suggested_volumes,suggestion,reason,safe_to_repair}], task_id, task}` | `main.py:2461` |
| `POST /api/maintenance/repair-volume-ranges` | `{release_ids:[…]}` (first 200) | `{status, repaired, skipped, task_id, task}` | `main.py:2551` |
| `GET /api/maintenance/releases-cache` | `series?`, `statuses?`, `exclude_statuses?` | `{status, …count fields}` | `main.py:2587` |
| `POST /api/maintenance/clear-releases` | `{confirm:true, series_name?, statuses?, exclude_statuses?}` | `{status, …result, task_id, task}` | `main.py:2599` |
| `POST /api/maintenance/clear` | `{action}` where action is one of `clear-cover-cache`, `clear-discovery`, `clear-descriptions` | `{status, message, action, count}` | `main.py:4323`; `schemas.py:995` |
| `POST /api/debug/action` | `{action}` (debug flag required) | `{status, message}` or 403 | `main.py:4342` |
| `POST /api/discovery/track` | `{series_name, release_id?}` | `{status, tracked_name}` | `main.py:4135` |
| `POST /api/backfill/start` and `/api/backfill/stop`; `GET /api/backfill/status` | `{max_pages?, delay?, start_page?}` | `{status,message}` / `{running, status, progress, last_page}` | `main.py:4293-4321` |
| `GET /health` | — | auth + storage + logs + network diagnostics JSON | `main.py:1040-1058` |

---

## 3. Data model touched

### 3.1 MangaTrax SQLite tables

**`series_tracking`** (created `database.py:412-428`; extra columns added `:430-436,980-993`) — one row per tracked series; this is the library identity table.

| Column | Type/default | Written by | Invariant / notes |
|---|---|---|---|
| `id` | INTEGER PK autoincrement | insert | used by toggle/link/backfill endpoints |
| `calibre_series_id` | INTEGER NULL | sync (SER-031), link (SER-037), phase 2 (SER-060) | NULL = user-typed/untracked; may hold a **synthetic** id (`book.id+1e6`) from SER-025 |
| `name` | TEXT UNIQUE NOT NULL | sync, link, track_discovered | the STABLE matching key; UNIQUE is case-sensitive while all lookups are `lower(name)` (Q3) |
| `authors`, `publisher`, `tags` | TEXT default '' | sync (only non-empty), phase 2, provider refresh | Calibre/template metadata inherited by the tagger |
| `description` | TEXT default '' | phase 2/phase 1, track_discovered, clear-descriptions | display only |
| `book_count` | INTEGER default 0 | sync, link, phase 2 | rendered as "Owned" on /series |
| `latest_index` | REAL default 0.0 | sync, link, phase 2 | max Calibre series_index, raised to max owned bundle volume |
| `is_monitored` | INTEGER default 1 | insert=1, toggle, blacklist sets 0, merge MAX | drives demo auto-request behaviour and the Overview counter |
| `website_url` | TEXT default '' | sync (only if empty), link endpoint, phase 2/1-if-empty, track_discovered | http(s) only via schema; rendered with `safe_url` |
| `owned_volumes_json` | TEXT default '[]' | sync (only non-empty), link (may blank, Q2), phase 2, reconcile consumers | JSON list of floats; source of truth for missing-volume reconciliation |
| `last_sync` | TIMESTAMP default CURRENT_TIMESTAMP | sync/link/phase 2 | not rendered on /series |
| `tags_json` | TEXT default '[]' | provider tag refresh | ordered `[{name,category,rank,spoiler}]` |
| `tags_text` | TEXT default '' | provider tag refresh | pipe-delimited normalized tokens for filtering |

**`series_alias_overrides`** (`database.py:871-886`) — `id`, `source_norm` UNIQUE NOT NULL, `source_series`, `target_name`, `target_calibre_series_id`, `created_at`, `actor`; index on `target_name`. Invariant: one override per normalized source; inserting the same source updates the target/actor (SER-076). Consumed by identity resolution (SER-046) and the template map (SER-055).

**`series_blacklist`** (`database.py:439-446`) — `id`, `name` UNIQUE, `reason`, `created_at`. Written by blacklist actions (SER-015/SER-086); read by release/list filters (`database.py:2923,2938,4064,4093`) and by the ingestion blacklist check (`scheduler.py:4037-4041`).

**`manga_metadata_cache`** (`database.py:711-723` + tags columns `:981-984`) — `search_term` PK, `canonical_title`, `romaji_title`, `cover_url`, `total_volumes`, `status`, `synopsis`, `source`, `created_at`, `genres_json`, `tags_json`, `tags_updated_at`. Authoritative rows (`source='anilist'` + canonical) drive display titles, aliases and search; MangaUpdates rows use `mu:<name>` keys and `romaji_title` as URL storage.

**`releases`** (columns in this domain: `series_name`, `volumes_json`, `missing_vols_json`, `volume_start/end`, `is_bundle`, `is_complete`, `inferred_volumes`, `status`, `download_status`, `cover_url`, `tags_json`, `tags_text`, `published_at`, `updated_at`) — written by ingestion (SER-052/SER-054), reconcile (SER-033/SER-034/SER-062), audits/repairs (SER-077/SER-078/SER-079/SER-080/SER-081/SER-082), link (SER-037), blacklist (SER-086), manual match (SER-076).

**`app_settings`** — `calibre_path`, `calibre_sync_interval_minutes`, `staging_dir`, `staging_dir`-dependent staged map, `backfill_status`, `backfill_progress`, `backfill_last_page`, `storage_health_json`, `app_timezone`, `filter_non_manga`, `media_filter_applied`, `ln_release_backfill_done`, `discard_unmarked_chapters`, `infer_collected_releases`, `auto_download`, `auto_tag`. No FK enforcement is used for these keys.

**`background_tasks`** (`database.py:888-906`) — persisted job rows: `kind`, `title`, `scope`, `status`, `progress_pct`, `message`, `items_done/total`, `started_at/finished_at`, `actor`, `error`, `result_json`. Used by every series refresh/audit/repair triggered from the UI and read by the global Tasks panel.

**`activity_logs`** (`database.py:666-675` + `actor` upgrade `:677-685`) — every mutation in this domain writes an event (`CALIBRE_SYNC`, `CALIBRE_SYNC_ERROR`, `REQUEST_RESOLVED`, `SERIES_METADATA_REFRESHED`, `SERIES_CALIBRE_REFRESHED`, `SERIES_BACKFILLED`, `SPINOFF_SKIPPED`, `SERIES_ATTRIBUTION_REPAIRED`, `FALSE_SERIES_MATCH_REPAIRED`, `CHAPTER_RANGE_REPAIRED`, `SERIES_MANUALLY_MATCHED`, `SERIES_KEYS_REPAIRED`, `SERIES_TRACKED`, `SERIES_BLACKLISTED`, `MAINTENANCE_CLEARED`, `BACKGROUND_TASK_STARTED/FINISHED`) except the auto-link path (Q7).

### 3.2 Calibre `metadata.db` tables read (never written)

`books` (id, title, series_index), `series` (id, name), `books_series_link` (book, series), `authors`/`books_authors_link`, `publishers`/`books_publishers_link`, `tags`/`books_tags_link` — see the queries in `scanner.py:90-118,155-163,200-205` and the test fixture schema in `tests/mock_calibre_manga.py:12-70`.

### 3.3 Files and in-memory state

* `DATA_DIR/covers/<sha256[:24]>.jpg` — cover cache (SER-069).
* `_SUMMARY_CACHE` (`scanner.py:9`) — per-path scanner summary keyed by mtime.
* `_grouped_cache` (`database.py:363`) — 5 s grouped-releases cache keyed `status_prioritized_sort`; invalidated by every series mutation.
* `staged_ownership` module cache (`staged_ownership.py:26-28`) — 60 s.
* `MangaScheduler.is_backfilling` / `stop_backfill_requested` / `_calibre_sync_lock` / `last_calibre_sync` / `_initial_sync_done` (`scheduler.py:88-134`) — process-local job state.

### 3.4 Cross-table invariants (as implemented)

1. `series_tracking.name` == `releases.series_name` for a series = the same stable matching key; AniList display titles are never persisted into either.
2. A tracked series has at most one row per exact `name`; merges delete the redundant tracker and re-key its releases.
3. `is_monitored` defaults to 1 on insert and is preserved across sync; merges take MAX; blacklist forces 0.
4. `website_url` is never blanked by sync; only an explicit link update (including empty) changes it.
5. `owned_volumes_json` is never blanked by sync (non-empty rule); link/repair can blank it (Q2).
6. `series_alias_overrides.source_norm` is unique; overriding an existing source updates the target.
7. Authoritative metadata (`source='anilist'` + canonical title) is the only metadata allowed to drive display titles/aliases/search; fallback/cleaned rows are ignored for those purposes.
8. Automated status writes obey `resolve_status`; explicit user endpoints can set any allowed status.
9. `/series` rows include *all* trackers (Calibre-synced and user-typed); the Overview "Monitored Series" count uses `is_monitored=1` only.
10. Deleting a release (cache clear) never deletes its tracker; deleting a tracker only happens via merges (SER-037/032) or `reset-all-data` (SER-085).

---

## 4. Configuration & tunables

### 4.1 Environment variables

| Variable | Default | Effect in this domain | Evidence |
|---|---|---|---|
| `CALIBRE_DIR` | `/calibre` (in-container) | fallback Calibre library path when no `calibre_path` setting exists; also an allowed root for `/api/calibre/test` | `main.py:80,1141,1944-1946`; `database.py:729`; `docker-compose.yml:26` |
| `DATA_DIR` | `<backend>/../data` | location of `mangatrax.db` and the `covers/` cache | `main.py:77-79`; `covers.py:97-99` |
| `STAGING_DIR` | `/staging` | default `staging_dir` seeding (staged ownership map + settings) | `database.py:736`; `main.py:353-354` |
| `TZ` | Europe/Copenhagen fallback in code | timezone used by `localtime`/`localdate` filters when `app_timezone` is unset | `timezone_util.py:14-23`; `docker-compose.yml:34` |
| `MANGATRAX_PATH_ROOTS` | unset | `os.pathsep` list restricting `calibre_path`/`staging_dir`/`downloads_dir`; unset keeps legacy freedom but still blocks traversal/symlink escapes | `main.py:330-337,4224-4231`; `docker-compose.yml:60` |
| `MANGATRAX_DEBUG_ACTIONS` | unset/0 | enables `POST /api/debug/action` (SER-085); otherwise 403 | `auth` config; `main.py:4342-4353` |
| `MANGATRAX_RECONCILE_BATCH_SIZE` | 2000 (min 50) | max releases reconciled against Calibre per sync pass | `scheduler.py:115-118,3292-3305` |
| `MANGATRAX_NYAA_BASE_URL` | `https://nyaa.si` default | base used by the per-series Nyaa crawl (cross-domain) | `database.py:759`; `scheduler.py:4576` |

### 4.2 `app_settings` keys specific to this domain

| Key | Default | Effect |
|---|---|---|
| `calibre_path` | `CALIBRE_DIR` env or `/calibre` | library directory containing `metadata.db`; read on every sync/snapshot/dashboard render |
| `calibre_sync_interval_minutes` | `15` | periodic sync interval; UI/Settings allows 1–1440 (`settings.html:337`) |
| `staging_dir` | `/staging` | staged-ownership scan root; also a `/api/calibre/test` allowed root |
| `backfill_status` | seeded/absent | `idle`/`running`/`stopping`/`error`; shown by the global banner |
| `backfill_progress` | `''` | human progress text mirrored into the refresh task and banner |
| `backfill_last_page` | `1` | resume hint for the historical backfill modal |
| `storage_health_json` | absent | last persisted storage probe; reused while paths match |
| `app_timezone` | unset (falls back to `TZ`) | dashboard/activity timestamp rendering only |
| `auto_download` | `true` | whether the backfill auto-submits wanted releases |
| `auto_tag` | `true` | gates the periodic staging tag/route pass that consumes the series templates map |
| `filter_non_manga` | `true` | one-shot media-typing marker gate; affects which releases the library/stat queries count |
| `discard_unmarked_chapters` | `true` | scheduler chapter discard (affects which releases ever reach the library) |
| `infer_collected_releases` | `true` | collected-batch volume inference (affects volume/completeness) |
| `media_filter_applied`, `ln_release_backfill_done` | `''`/`'true'` | one-shot migration markers |

### 4.3 Constants and hard-coded tunables

| Constant | Value | Effect | Evidence |
|---|---|---|---|
| Scanner connect timeout / busy timeout | 5.0 s / 2000 ms | Calibre read robustness | `scanner.py:31-35` |
| Library-summary cache key | `metadata.db` mtime | avoids re-counting books on dashboard loads | `scanner.py:9,47-54` |
| Matcher default fuzzy threshold | 0.80 | `CalibreSeriesIndex.match` default; used for general matching | `parser.py:1829` |
| Auto-link / link-audit threshold | 0.90 | stricter auto-merge | `database.py:2064`; `main.py:3976` |
| Substring rule | `len_ratio ≥ 0.70` and query len ≥ 6 | matching containment | `parser.py:1870-1887` |
| Fuzzy candidate buckets | rare token ≤200, tiny gram ≤20 | fuzzy candidate bounding | `parser.py:1758-1761` |
| Series backfill default / max pages | 3 / 500 | per-series crawl breadth; UI hard-codes 3 | `schemas.py:575-585`; `series.html:281`; `base.html:907` |
| False-series audit scan depth | 2000 newest releases | audit scope | `false_series_matches.py:25-37` |
| Provider-tag refresh scan depth | 1000 releases | tags backfill scope | `scheduler.py:794` |
| Cover limits | 5 MB, 7 s, 3 redirects | SSRF/size safety | `covers.py:20-24` |
| Cover cache filename | `sha256(url)[:24].jpg` | deterministic cache key | `covers.py:101-104` |
| Staged ownership cache TTL | 60 s | freshness of staged ownership | `staged_ownership.py:24` |
| Grouped cache TTL | 5 s | Releases API responsiveness | `database.py:4024-4031` |
| Tag chip limits | series 4, dashboard 3, releases 3, discovery 5 | display policy | `tag_display.py:9-14` |
| AniList rate limit / timeout | 90 per 60 s / 4 s | provider safety | `anilist.py:87,142-144` |
| MangaUpdates timeout | 5 s | provider safety | `mangaupdates.py:66-68` |
| HTTP cache header for covers | `public, max-age=864000` | 10-day browser cache | `main.py:1974` |
| `MAX_URL_LENGTH` | from `security` | `website_url` and cover URL length bound | `schemas.py:564`; `security.py` |

---

## 5. Integration contracts

### 5.1 What this domain consumes

| Source domain | Contract consumed | Where |
|---|---|---|
| Releases/feeds ingestion | `releases` rows as the substrate for identity resolution, ownership reconciliation and grouping; parsed titles/volumes from `parser.extract_manga_bundle_and_volumes` | `scheduler._process_release` (`scheduler.py:3870-4102`); grouped builder (`database.py:4004-4544`) |
| Releases/Discovery | `POST /api/discovery/track` and blacklist are the two user entry points that create/stop trackers; Discovery writes `manga_metadata_cache` via AniList/MangaUpdates which this domain reads for display/aliases/search | `main.py:4135-4179`; `database.py:3277-3361`; `track_discovered_series` |
| Downloads / Staging | staged ownership map (built from the real staging scan plus a bounded COMPLETED/routed release-row fallback) is added to Calibre ownership for missing-volume/status decisions; `series_templates_map` is consumed by routing, tagger and inventory | `staged_ownership.py:54-152`; `database.py:2313-2339`; `scheduler.py:219-222,3832` |
| Downloads manual match | creates alias overrides + tracks a typed target and re-keys releases; this domain owns the override table and the identity precedence | `main.py:2328-2458`; `series_alias_overrides` |
| Settings/Platform | `calibre_path`, `calibre_sync_interval_minutes`, `staging_dir`, `app_timezone`, debug flag, path roots, storage probe, persisted tasks, activity-log actor | `main.py:353-394,1944-1948,4224-4231`; `background_tasks.py`; `storage_health.py` |
| Auth | `auth_module.get_current_actor()` for every audited write and `background_tasks` actor; CSRF/Origin gating for POST/DELETE | `main.py:240-267`; `database.py:25-38` |
| Network providers | AniList GraphQL, MangaUpdates REST, Nyaa search (phase 3) | `metadata/*`, `providers/nyaa.py`, `scheduler.backfill_series` |

### 5.2 What this domain exposes

| Consumer | Contract exposed | Where |
|---|---|---|
| Releases page / APIs | stable matching key vs display title (`display_series_name`, `stable_name`, `romaji_name`), grouped series structures, ownership/missing-volume statuses, alias recovery, spinoff guards, `IN_STAGING`/`IN_CALIBRE` semantics | `database._build_grouped_releases`; `GET /api/releases/cards`, `/grouped`, `/flat_rows` |
| Downloads / auto-download | `MangaCalibreMatcher.check_release()` status + `missing_volumes`; wanted-set computation; staged ownership; blacklist context | `matcher.py:133-214`; `scheduler._auto_download_wanted` (`:1248-1306`) |
| Tagger / routing | normalized series template map (authors/publisher/tags) keyed by alias overrides so ComicInfo `<Series>` matches Calibre | `database.py:2313-2339`; `tagger/pipeline.py:86+` |
| Platform / Settings | `/api/calibre/*`, alias overrides CRUD, attribution/false-series/volume-range audit+repair, releases-cache clear, safe maintenance, `/health.storage`, debug actions | `main.py` endpoints; `maintenance.py`; `false_series_matches.py` |
| UI shell | `/api/calibre/status`, `/api/calibre/sync`, `/api/backfill/status` (+ `/api/backfill/stop`), `/api/tasks`, Jinja globals `tag_display`, `safe_url`, `localtime/localdate` | `base.html`; `main.py:279-317` |
| Webhooks | "acquired" notification when a pending request resolves into Calibre; "new request" notifications from backfill ingest | `scheduler.py:3168-3174,4616-4621` |
| External API consumers/tests | `/api/search`, `/api/series/link-audit`, `/api/series/link-repair`, `GET /api/maintenance/series-aliases` (no first-party UI) | see SER-038/SER-039/SER-074/SER-075 |

### 5.3 Shared invariants other domains must respect

1. Write only `stable_name` into `releases.series_name` / `series_tracking.name`; put cosmetic titles in the metadata cache and let render paths resolve them (SER-045/SER-067).
2. Treat `IGNORED`/`BLACKLISTED` as absolute for automated writes, use `resolve_status()` for every automatic transition (SER-086; `database.py:272-307`).
3. Treat staging as owned: never re-request a volume present in the staging map; keep the "In staging" label distinct from "In Calibre" (SER-034/SER-064; `staged_ownership.py`).
4. After any series-identity mutation, call `db._invalidate_grouped_cache()` so the Releases views reflect it; series rows are not cached beyond the browser (SER-031/SER-037/SER-078/SER-082).
5. A tracked series' `book_count`/`latest_index` are Calibre facts, not ownership sets; do not use them as owned volumes.
6. Callers of `db.link_tracked_series` should pass an explicit `owned_volumes` list when they have the live Calibre volumes, or the tracker's owned set may be blanked (Q2).

---

## 6. UI touchpoints & mobile behavior (summary)

### 6.1 `/` — Overview

* **Controls:** only per-wanted-card "Download" buttons (`index.html:213-217`, JS `:266-281` posting `/api/releases/{id}/download`) and static links to list pages. No series-specific actions, no search box, no refresh button.
* **State:** five stats always render; hero badge flips green/amber from live scanner availability; downloader section appears only when torrents exist; wanted section shows cards or a success empty state; recent logs always render (possibly empty list container).
* **Banners:** global Calibre-sync banner and global historical-backfill banner (both from `base.html`) appear above the content; the global Tasks launcher is bottom-right.
* **Known gaps:** storage-health warnings are absent (SER-087); the wanted badge caps at the fetched ≤10 rows (SER-004); "In Library" counts release rows, not books (Q5); no way to trigger a Calibre sync from Overview (button lives on `/series`).
* **Mobile:** stat grid is 2 columns (`grid-cols-2 lg:grid-cols-5`, `index.html:35`); wanted grid 1→4 columns; torrent cards 1→3; horizontal overflow is not used. Tap targets for Download are small (`text-[11px]`, `px-2.5 py-1`) but not icon-only.

### 6.2 `/series`

* **Controls:** search input; Sync Calibre button; per-row monitored toggle (icon), Backfill, website-link edit (icon), blacklist (icon); "Not linked — Match to Calibre series" button; modal buttons (search field, series list, Link & merge, Cancel); Load More button (fallback); Clear search in the empty state.
* **State:** skeleton until the first batch; rows appended in 50-row batches with infinite scroll; remaining count; search filters loaded rows by name/authors/publisher/tags and shows the filtered-empty state; monitored toggle reloads the page; match modal updates the row's Owned cell in place and removes the CTA on success; backfill leaves the page usable with the global banner.
* **Known gaps:** all series rows are fetched eventually even with an active filter (SER-017); the "Owned" column shows `book_count`, not owned volumes (SER-011); no sort control; no server-side search/monitored filter in the UI despite API support; monitor toggle is a 28 px icon button with `title` only, and row icon buttons lack `aria-label` while the a11y test only scans top-level templates (SER-091); the row-level tag fallback branch is unreachable (tag_display always defined).
* **Mobile:** table horizontally scrolls inside `overflow-x-auto`; header stacks (`flex-col sm:flex-row`); the search field is full-width then 64; the modal is `max-w-xl` with `max-h-64` list; no alternative card layout and no sticky table header. The shared mobile nav exposes Series under "Library" with 44 px rows; icon touch targets in rows remain below 44 px.

### 6.3 Global shell relevant to this domain

* Desktop nav groups Series under "Library ▾" and highlights the parent when `/series` is active; mobile menu mirrors it (`base.html:120-273`).
* Global Calibre-sync banner (auto-dismissible, auto-refreshes on completion) and backfill banner with Stop (`base.html:333-365`).
* Global Tasks panel (persisted progress, Cancel) drives every series refresh/audit/repair task (`base.html:389-398,1002-1148`).
* Storage warnings render only on `/downloads` and `/settings` (`partials/storage_warning.html`, `partials/storage_path_warning.html`); `/` and `/series` never show them.
* Image lightbox shared by series rows and Overview covers (`base.html:278-288`).
* All covers are fetched through `/api/covers?url=…`, so a blocked/expired provider URL degrades to the same placeholder everywhere.

---

## 7. Open questions / ambiguities

**Q19 — False-series audit target and repair target can differ.** The audit's `suggested_series` is the fixed Calibre match when one exists, but repair re-keys to the title-parsed series (`false_series_matches.py:55-67` vs `:99-126`), so the UI's recommendation and the applied name may not match. Either the audit should report the repair target, or repair should use the suggested target.

**Q1 — Per-series refresh while another backfill runs can record a false success.** `run_series_backfill_async` returns `in_progress` without starting work (`scheduler.py:4717-4718`), but `_start_backfill_task` still creates a task and a watcher that closes it as `success` when `is_backfilling` clears (`main.py:4028-4045`). Evidence that the flag is shared by the polite historical backfill: `scheduler.py:4761-4773`. Should the endpoint return 409 / a non-success task instead, and should the watcher bind to its own job id?

**Q2 — Manual link/merge can blank `owned_volumes_json`.** `link_tracked_series` accepts `owned_volumes` and otherwise reads `calibre_series["owned_volumes_json"]` (`database.py:1897-1907`); the rename branch writes `owned_volumes_json = json.dumps(sorted(volumes))` (`:1946-1951`). Callers `/api/series/{id}/match-calibre` (`main.py:3948-3951`) and confirmed `/api/series/link-repair` (`main.py:4005`) pass no owned volumes, and the snapshot rows carry `volumes`, not `owned_volumes_json` (`scanner.py:127-147`). Result: after a manual link, missing-volume reconciliation for that series can treat Calibre-owned volumes as missing until the next sync restores the non-empty set via `sync_calibre_series` (`database.py:1859`). Is this intended, or should callers pass `target.get("volumes")`?

**Q3 — `series_tracking.name` uniqueness is case-sensitive but every lookup is case-insensitive.** Schema `name TEXT UNIQUE NOT NULL` (`database.py:416`); lookups use `lower(trim(name))` (`:1910-1913,2167`). Two Calibre series differing only by case (or a user-typed duplicate) could create two rows that merge logic then treats as duplicates. No `COLLATE NOCASE` or normalized unique index exists.

**Q4 — Removed/renamed Calibre series are never pruned.** `sync_calibre_series` only inserts/updates (`database.py:1846-1863`); nothing deletes trackers absent from the scan. A series deleted or renamed in Calibre therefore leaves a stale tracker (old name, old counts, no owned volumes), and an unlinked tracker with the old name may later be fuzzy-linked to something else. Expected behaviour is undocumented.

**Q5 — Counter semantics on Overview.** `acquired_books` counts release rows with `status='IN_CALIBRE'` (`database.py:3537-3538`), not distinct books/volumes despite the "In Library" label (`index.html:75-86`). The wanted badge prints the length of a list capped at 10/8 (`index.html:142,152`) while the KPI card is the true count — two different numbers for the same concept on one page.

**Q6 — Cover MIME/cache-extension mismatch and cover source.** `CoverCache.get_filename` always returns `.jpg` (`covers.py:101-104`), so `GET /api/covers` always reports `image/jpeg` (`main.py:1965-1972`) even for PNG/WebP/GIF payloads stored under that name, while the global `nosniff` header is active (`main.py:273`). Separately, `/series` obtains covers only from the newest release row (`database.py:2096-2101`), so a Calibre- or AniList-only series (no release covers) shows no thumbnail. Is the extension a deliberate simplification or an oversight?

**Q7 — Automatic re-key/merge is not audited.** `link_tracked_series` and `auto_link_tracked_series` write no `activity_logs` row (no `log_activity` in `database.py:1872-2085`), and `sync_calibre_series` calls auto-link inline (`:1864-1869`). Users cannot see when the periodic sync silently renamed a series or re-keyed releases; only manual actions (`SERIES_MANUALLY_MATCHED`, `SERIES_KEYS_REPAIRED`) are auditable.

**Q8 — API surfaces with no UI, and docs that imply otherwise.** `GET /api/search` (M2 feature 17; `PROJECT.md:41`), `GET /api/series/link-audit`, `POST /api/series/link-repair`, `GET /api/maintenance/series-aliases` have no consumer in `backend/app/templates` (verified by grep). The Series page search is a client-side substring filter, so AniList alias search is unreachable for interactive users. Should the redesign wire these up or drop them?

**Q9 — `/series` search strategy.** The API supports server-side `search` (name/authors/publisher) and `monitored_only` (`main.py:1526-1533`; `database.py:2104-2113`), but the page never sends either; instead it keeps auto-fetching all pages while filtering locally (`series.html:151-161,194-233`). On a large Calibre library this means many requests per search and a lag until the matching page arrives. Was the API surface built for a planned server-side search UI?

**Q10 — Synthetic standalone series can become link targets.** `scanner.get_all_series()` exposes pseudo-series with `series_id = book.id + 1000000` (`scanner.py:171-180`), and `/api/calibre/series` feeds the same list to the match pickers (`main.py:2278-2319`). Linking a tracker to such an entry stores a fake Calibre id in `series_tracking.calibre_series_id`; the "unlinked" CTA then disappears even though the series is not a real Calibre series. Should synthetic rows be filtered from picker/match paths?

**Q11 — GET endpoints persist task/log rows.** `GET /api/maintenance/suspicious-volume-ranges` and `GET /api/maintenance/series-attribution` create `background_tasks` rows and `BACKGROUND_TASK_STARTED/FINISHED` activity logs because they are wrapped in `_recorded_task` (`main.py:2461-2468,2631-2639,1267-1297`). The equivalent false-series audit is a POST with no task (`main.py:2508-2516`). Inconsistent REST semantics and an audit-trail side effect for a read; intentional?

**Q12 — AniList cache has no TTL and a partial-row fast-path hole.** Authoritative cache rows are returned forever unless a per-series refresh forces a re-fetch (`anilist.py:190-234`); a cached row with a canonical title but no `total_volumes` fails the fast-path condition (`:194`) and triggers a network lookup on every use. Should the redesign add a TTL/`tags_updated_at`-style freshness policy and treat canonical-only rows as usable cache?

**Q13 — MangaUpdates URL stored in `romaji_title` can leak into "Romaji" labels.** `get_series_info` caches `{"romaji_title": info["url"], …}` (`mangaupdates.py:148-156`), while the grouped builder assigns `romaji_name` from the cache row's `romaji_title` without requiring authoritative metadata (`database.py:4254-4260`; `get_releases` decoration `:2964-2974`). Templates render `Romaji: {{ romaji_name }}` on cards/rows (`partials/series_card_chunk.html:37-40`, `partials/flat_release_row_chunk.html:17-19`, `discovery.html:103-105`). A MangaUpdates-cached series can therefore display a URL under a "Romaji" label. Is the URL smuggling still desired, and should romaji display be gated on `is_authoritative_metadata`?

**Q14 — Storage warnings are not part of the Overview/library surfaces.** The domain brief lists storage warnings as library-domain, but the probe result is only rendered on `/settings` and `/downloads` (templates grep: 3 includes) and `/health`; Overview/series show nothing (`storage_health.py:30-172`; `main.py:365-394`). If the redesign wants one "library health" banner, the data already exists in `app_settings.storage_health_json`.

**Q15 — `link-repair` dry-run depends on parsing an internal response object.** `api_series_link_repair` calls `api_series_link_audit()` directly and does `json.loads(getattr(audit_response, "body", b"{}"))` (`main.py:3998-4004`). This works only while the audit handler keeps returning a `JSONResponse`; it is brittle to refactors and bypasses FastAPI response handling.

**Q16 — Unknown/absent ids on mutation endpoints.** Toggle and link return 200 "success" for unknown series ids (no rowcount check: `main.py:3826-3837`; `database.py:2145-2158`), while backfill/match-calibre/alias-delete return 404. Inconsistent error semantics across sibling endpoints; the UI relies on reload so it cannot tell.

**Q17 — Series refresh by name accepts untracked names.** `POST /api/series/backfill` only validates a non-empty name (`schemas.py:581-585`); `refresh_series_calibre_info` skips "series is not tracked" (`scheduler.py:4342-4344`) but the metadata phase and Nyaa crawl still run for any name, potentially creating DISCOVERY releases for arbitrary strings. Is free-form refresh intended for the Releases/Prioritized "backfill this card" path (yes, `base.html:896-927`) or should it require a tracked/existing series?

**Q18 — `owned_volumes_json` vs Calibre `book_count` on the Series page.** Reconciliation uses the volume set (SER-008/SER-025), while the Series page shows `book_count` as "Owned" (SER-011). A series with a book whose `series_index` is 0/duplicated, or a synthetic standalone series, can show "Owned 1" with no owned volumes (or vice versa). Should the redesign show both numbers or relabel the column?

---

## 8. Consensus checklist

Each line is a single checkable behaviour claim (vote AGREE / DISAGREE after review).

SER-001 — `GET /` renders the dashboard hero with a green "Calibre Connected (N Series • M Books)" badge when `metadata.db` is readable and an amber "Calibre DB Not Found" badge otherwise, using the live cached `get_library_summary()` result.
SER-002 — The Overview KPI row shows five counters sourced from `get_dashboard_stats()`: `is_monitored=1` trackers, REQUESTED releases, IN_CALIBRE releases, DOWNLOADING releases, and the discovery count.
SER-003 — The Overview shows an Active Downloader queue of at most five torrent cards only when the degraded-safe `_build_downloader_status(1.0)` returns torrents.
SER-004 — The Overview wanted list is built from `get_pending_requests(limit=10)`, renders at most 8 cards, and its section badge shows the length of that 10-row fetch (min(total,10)), not the true pending total.
SER-005 — The Overview recent-activity panel renders the 10 newest `activity_logs` rows with event chips and timezone-converted timestamps.
SER-006 — `GET /series` returns a shell whose table body initially contains a loading skeleton and no rows, with the total series count rendered server-side.
SER-007 — `GET /api/series/rows` returns JSON with `status`, `offset`, `count`, `total`, `has_more` and server-rendered table-row HTML, supporting `monitored_only`, `offset`, `limit` (1–200) and `search`.
SER-008 — `POST /api/series/{id}/toggle` flips `series_tracking.is_monitored` and returns the new boolean, returning HTTP 200 even for an unknown id.
SER-009 — A series row renders a cover image only when the newest release row for that series has a non-empty `cover_url`, and clicking it opens the shared lightbox.
SER-010 — The series link modal validates `website_url` as an empty string or absolute http(s) URL and persists it to `series_tracking.website_url` without verifying the series exists.
SER-011 — The series "Owned" column displays `series_tracking.book_count` for Calibre-linked series and a "Not linked — Match to Calibre series" button otherwise; it never displays `owned_volumes_json`.
SER-012 — The series "Latest Vol" column displays `series_tracking.latest_index`, which is the Calibre max `series_index` raised to the max owned bundle volume when applicable.
SER-013 — Series rows render up to four provider-tag chips with a `+N` overflow indicator and tooltip, using the shared `tag_display` Jinja global.
SER-014 — The per-series Backfill button posts `{"max_pages": 3}` to `/api/series/{id}/backfill`, starts the 3-phase background refresh and leaves the page usable while progress appears in the global backfill banner/task panel.
SER-015 — The series row blacklist button posts `/api/blacklist`, which also sets that tracker `is_monitored=0` and marks its releases `BLACKLISTED`.
SER-016 — The series page Sync Calibre button calls the synchronous `POST /api/calibre/sync`, then shows the returned summary toast and reloads after 1.5 s.
SER-017 — The series search box filters only rows already loaded into the DOM (by lowercased name/authors/publisher/tags) and never sends the server-side `search` or `monitored_only` parameters.
SER-018 — Series rows load in 50-row batches via `/api/series/rows`; after each batch the page schedules the next fetch 60 ms later while `has_more` is true (the whole library is fetched without scrolling). An IntersectionObserver triggers the same fetch, and the Load More button (hidden by default) is revealed only when a batch fetch errors.
SER-019 — The match-to-Calibre modal searches `/api/calibre/series` without a `source` parameter (so results are name-ordered, not similarity-ranked) and POSTs `{calibre_series_id}` to `/api/series/{id}/match-calibre`, which merges/links trackers and re-keys releases while preserving release statuses.
SER-020 — The Series page is reached from the desktop "Library" dropdown and the mobile "Library" group, and the parent nav item is highlighted when `active_page == 'series'`.
SER-021 — `CalibreMangaScanner` reads Calibre's `metadata.db` through a read-only URI (`mode=ro&immutable=1` when supported, `mode=ro` fallback) with `query_only` and a bounded busy timeout, and never writes to the Calibre database.
SER-022 — `get_library_summary()` returns `{available: true, db_path, total_books, total_series, total_authors}` when the library is readable, otherwise `{available: false, ...}` with no guaranteed `db_path`; it caches per DB path keyed by file mtime.
SER-023 — `get_all_series()` returns per series: id, name, book count, latest index (max Calibre series_index), concatenated authors, first publisher, concatenated tags and the resolved owned-volume list.
SER-024 — The Calibre volume map expands bundle titles so that a `v01-02` book contributes both volumes 1 and 2, and indexes each set by lower-case name, normalized name, Calibre id and dual-title parts.
SER-025 — Standalone Calibre books with a parseable series title are exposed as synthetic series with `series_id = book.id + 1000000` and `book_count = 1`.
SER-026 — `GET /api/calibre/test` contains the requested path within the configured Calibre/default/staging roots, returning 403 for escapes and otherwise the scanner summary.
SER-027 — `POST /api/calibre/sync` runs the Calibre scan synchronously, returns the sync result together with a persisted task whose progress/status are recorded, and rejects concurrent runs by skipping rather than erroring.
SER-028 — The background worker triggers a Calibre sync whenever `calibre_sync_interval_minutes` (default 15) has elapsed since the last sync.
SER-029 — The worker's first action is the legacy series-key repair followed by an initial Calibre sync, and neither can prevent the web UI from serving.
SER-030 — `GET /api/calibre/status` returns `{in_progress, initial_sync_done, last_sync}` without blocking, and drives the global sync banner via 3-second polling.
SER-031 — Calibre sync upserts every series into `series_tracking` with `is_monitored=1` on insert, never overwriting non-empty authors/publisher/tags, never blanking `website_url`/`owned_volumes_json`, and never changing `is_monitored` on an existing row.
SER-032 — After a sync batch, trackers without a `calibre_series_id` are auto-linked: a unique exact candidate wins, fuzzy matching via `CalibreSeriesIndex.match(threshold=0.90)` accepts only scores strictly above 0.90, and multiple exact candidates are left unlinked.
SER-033 — After sync, previously wanted releases whose missing volumes are now in Calibre are promoted to `IN_CALIBRE` (or `IN_STAGING`) through the precedence model, audited `REQUEST_RESOLVED`, and webhook-notified when acquired.
SER-034 — Release reconciliation after sync skips provably-stable IN_CALIBRE rows, is bounded by `MANGATRAX_RECONCILE_BATCH_SIZE` (default 2000) with a rotating cursor over up to 5000 releases in status-tier/recency order, then filtered to active statuses, and persists only rows whose missing volumes or status changed.
SER-035 — After a successful sync the scheduler attempts to expand unexpanded complete-series releases, and failure of that expansion is logged rather than propagated.
SER-036 — `GET /api/calibre/series` returns the Calibre series picker list with optional substring filtering and optional `difflib` similarity ranking when `source` is supplied, falling back to synced tracker rows when the live library is unavailable.
SER-037 — `link_tracked_series` re-keys the source tracker's releases to the Calibre name, merges duplicate trackers (MAX monitored, union owned volumes), deletes redundant trackers, registers an alias override when the names differ, and never changes release statuses or vetoes.
SER-038 — `GET /api/series/link-audit` returns read-only `{proposed, count}` proposals for unlinked trackers matching a Calibre series at threshold 0.90, and no template in the app calls it.
SER-039 — `POST /api/series/link-repair` is a dry-run unless `confirm:true`, and its confirmed path auto-links unlinked trackers while passing no Calibre volume map.
SER-040 — `normalize_text` lowercases, applies NFKD, replaces dashes/colons/slashes with spaces, strips remaining punctuation and collapses whitespace, and is the comparison primitive used across matching, aliases and overrides.
SER-041 — `split_series_candidate_parts` is the single implementation for splitting titles on `|`, `/`, en/em dashes, spaced hyphen, `//` and `;`, used by the scanner, matcher, metadata clients, DB grouping and search.
SER-042 — `CalibreSeriesIndex.match()` applies exact normalized lookup, then substring containment requiring length ratio ≥ 0.70 and query length ≥ 6, then fuzzy scoring above the threshold on rare-token/3-gram candidates only.
SER-043 — Fuzzy series matches whose similarity rests only on a shared generic suffix are rejected by `_fuzzy_title_head_agrees` and logged as "Rejected fuzzy series match".
SER-044 — Matching adds colon-split parts and cumulative colon prefixes as candidates so a long subtitle can match a shorter Calibre series name without weakening whole-title rules.
SER-045 — `resolve_series_identity` prefers user alias override, then Calibre scanner, then Calibre-origin tracker, then typed tracker, then AniList canonical/romaji as lookup bridges, then reverse alias claims, and only then the parsed name as stable key.
SER-046 — A manual-match alias override is consulted before any AniList/Calibre identity resolution, matched by normalized raw name and by parse-shape candidates/cleaned source, and its target becomes the stable key.
SER-047 — A release whose raw name is an AniList alias of exactly one tracked series resolves to that series' stable name, with Calibre-claimed aliases preferred; multi-claim aliases keep the parsed name.
SER-048 — A metadata entry reached only through a delimiter/parent fallback is rejected as identity evidence, and delimiter parts are accepted only when they resolve to the same AniList id.
SER-049 — During a targeted parent refresh, a crawled release whose parsed/AniList identity differs from the target is audited `SPINOFF_SKIPPED` and never stored or auto-downloaded.
SER-050 — `detect_series_titles` builds refresh search titles from the requested name, delimiter/parenthesis parts and the AniList entry's own aliases/synonyms only, never harvesting names from stored releases.
SER-051 — `series_name_candidates` returns deduped plausible spellings covering the parsed primary, bracket form and contents, cleaned display, underscore-stripped form, colon parts and cleaned-for-matching variant.
SER-052 — Release parsing expands explicit volume ranges into every individual volume, detects complete-series titles, exposes chapter diagnostics without applying the discard policy, and recovers bracket/brace/underscore series names.
SER-053 — The startup legacy repair re-keys only releases whose stored AniList-style name is unambiguously claimed by one tracked series (or by a provable bidirectional Calibre duplicate), is idempotent and audits `SERIES_KEYS_REPAIRED`.
SER-054 — `POST /api/discovery/track` resolves the stable key, inserts/updates a monitored tracker, sets the release status to REQUESTED, re-keys legacy names and audits `SERIES_TRACKED`.
SER-055 — `get_series_templates_map()` returns normalized-name → `{series_name, authors, publisher, tags}` including alias-override sources that resolve to an existing target template.
SER-056 — `POST /api/series/{id}/backfill` returns 404 for an unknown tracker and otherwise starts the 3-phase refresh for the tracker's name with a persisted task.
SER-057 — `POST /api/series/backfill` starts the same 3-phase refresh for any non-blank name of 1–500 characters, defaulting `max_pages` to 3.
SER-058 — The refresh job runs metadata → Calibre → Nyaa crawl in one background daemon, mirrors progress from `app_settings.backfill_progress` into the persisted task, and clears `is_backfilling` in a finally block.
SER-059 — Phase 1 force-refreshes AniList (MangaUpdates fallback), writes only enrichment/cover data, keeps cached values and reports a skip when providers fail, and audits `SERIES_METADATA_REFRESHED`.
SER-060 — Phase 2 re-reads one series from Calibre and updates its Calibre id, book count, latest index, owned volumes and non-empty metadata/website fields, skipping cleanly when Calibre or the series is unavailable.
SER-061 — Phase 3 crawls Nyaa for the detected titles at `max_pages` each, processes results with the target-identity guard and chapter/light-novel filters, and returns `partial` status when only the crawl fails.
SER-062 — After the refresh phases, missing volumes and statuses are recomputed for that series' non-vetoed releases only.
SER-063 — A total-volume or book-count change detected during a refresh triggers complete-series expansion scoped to the refreshed series.
SER-064 — During a refresh, new REQUESTED releases are auto-downloaded per policy minus owned/staged/in-flight volumes, and duplicate volumes are parked as `PENDING_DEDUPE`.
SER-065 — Per-series refresh and historical backfill share one in-process `is_backfilling` flag, and a refresh requested while another backfill runs is reported as `in_progress` but still creates a task that can be marked success by the other job's completion.
SER-066 — AniList queries are rate-limited to 90 per 60 s with 429/Retry-After cooldowns, authoritative results are cached under search-term/canonical/romaji keys, and a forced refresh keeps cached values when the provider fails.
SER-067 — The cosmetic display title is the AniList canonical English title only for authoritative cache rows (`source='anilist'` with canonical title) and otherwise the stable matching key, and it never determines matching.
SER-068 — MangaUpdates supplies synopsis/URL fallback with cleaned description text and derives `total_volumes`/status from the series status string, caching results only when a description exists.
SER-069 — The cover cache validates every redirect hop against the SSRF guard, enforces a 5 MB cap and 7-second timeout, verifies raster magic bytes, writes atomically, and never raises to callers.
SER-070 — `GET /api/covers` serves a cached file with a 10-day cache header or returns a built-in SVG placeholder with HTTP 200, always tagging cache hits as `image/jpeg` because cache filenames are `.jpg`.
SER-071 — The provider-tag refresh task scans up to 1000 releases, AniList-enriches distinct untagged series, persists tags onto all their releases and the tracker, and returns updated/skipped/failed counters.
SER-072 — Tag display shows genres first then rank, hides spoilers, dedupes by normalized name, and limits chips to 4 for series and 3 for the dashboard with overflow count and tooltip.
SER-073 — `GET /api/tags` returns the union of metadata-cache genres/tags and denormalized release/series tags, spoilers excluded, ranked exact→prefix→substring→count, limited to 1–50.
SER-074 — `GET /api/search` returns tracked-series hits with stable key, display title, match reason and matched alias plus LIKE-escaped release hits, and no first-party template consumes it.
SER-075 — `GET /api/maintenance/series-aliases` lists overrides and `DELETE /api/maintenance/series-aliases/{id}` deletes one (404 if absent), invalidating the grouped cache; only the DELETE is used by the UI.
SER-076 — A manual awaiting-series match persists an alias override for the source and each distinct parse-shape candidate, re-keys matching releases, routes downloads and audits `SERIES_MANUALLY_MATCHED`.
SER-077 — The false-series audit is read-only, scans the newest 2000 releases, flags rows whose stored series no longer matches the title under the fixed matcher, and disables repair for IGNORED/BLACKLISTED rows.
SER-078 — The false-series repair requires `confirm:true`, preserves IGNORED/BLACKLISTED statuses, re-keys selected rows to their parsed series, recomputes status otherwise, and audits `FALSE_SERIES_MATCH_REPAIRED`.
SER-079 — The attribution audit scans all releases under tracked series, proposes identity-resolved own series, marks multi-part raw-only identities ambiguous (unrepairable), and runs as a persisted task via a GET endpoint.
SER-080 — The attribution repair applies only `safe_to_repair` selected rows (max 200 ids), changes only `series_name`, is idempotent and audits `SERIES_ATTRIBUTION_REPAIRED`.
SER-081 — The suspicious-volume-range audit flags chapter-scale/all-≥100 stored volumes or titles that now parse chapter-only, exempts explicit volume markers, and marks only chapter-only reclassifications repairable.
SER-082 — The volume-range repair clears phantom volumes/chapter state for safe rows, sets them IGNORED, skips unsafe rows, silently truncates ids to 200 and audits `CHAPTER_RANGE_REPAIRED`.
SER-083 — The release-cache preview/clear path is series/status scoped, requires confirmation, never touches trackers/Calibre, keeps DOWNLOADING/QUEUED rows, and records a persisted task plus audit row.
SER-084 — `POST /api/maintenance/clear` accepts only the three safe enum actions, performs them, and audits `MAINTENANCE_CLEARED` without requiring the debug flag.
SER-085 — `POST /api/debug/action` is 403 unless `MANGATRAX_DEBUG_ACTIONS=1`; its `normalize-english-titles` action now runs the guarded stable-key repair, and `reset-all-data` clears trackers/releases/caches and re-runs a Calibre sync.
SER-086 — Blacklisting un-monitors the tracker and marks its releases BLACKLISTED, unblacklisting restores only BLACKLISTED rows to DISCOVERY, and automated writes can never lift IGNORED/BLACKLISTED.
SER-087 — The storage probe really writes and deletes a temp file in downloads/staging, persists uid/errno diagnostics in `app_settings.storage_health_json`, and its warning banners render only on Settings and Downloads (plus `/health`).
SER-088 — The global Calibre-sync banner appears on non-auth pages while a sync is in progress or before the initial sync completes, polls `/api/calibre/status` every 3 seconds, and reloads the page when indexing finishes.
SER-089 — The global historical-backfill banner polls `/api/backfill/status` every 2.5 seconds, displays the progress text and offers a Stop button that requests a graceful stop.
SER-090 — The global Tasks panel polls `/api/tasks`, shows persisted progress/message/ETA/error for series-domain jobs, and disables matching action buttons while a task of the same kind/scope runs; it exposes no working cancel control (the Cancel branch never renders because `/api/tasks` omits `cancellable`).
SER-091 — On narrow viewports the Series table scrolls horizontally, the mobile nav exposes Series under Library with 44 px rows, and the series row icon buttons rely on `title` without `aria-label` (the a11y test scans only top-level templates, not partials).
SER-092 — `MangaDatabase.get_series_by_id()` exists but has no caller in the application or tests.
SER-093 — Ownership used for missing-volume and IN_STAGING decisions is Calibre-owned volumes UNION staged volumes, bundle-expanded per book and based on a 60 s cached staged-ownership map.
SER-094 — False-series audit suggests the fixed Calibre match when one exists, but repair re-keys selected releases to their title-parsed series, so the suggested and applied series names can differ.


---


# Part 3 — Downloads, Downloaders, Staging & Tagger


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


---


# Part 4 — Platform, Settings, Auth, Ops & Persistence


# MangaTrax — Platform, Settings, Auth/Security, Scheduler/Ops, Diagnostics & Persistence (OPS domain)

**Scope (3 lines).**
1. Platform surface: deployment/entrypoint, startup & shutdown, authentication/authorization (none, basic, OIDC), security controls, the settings system and `/settings`, activity logging and `/logs`, the persisted background-task system, scheduler jobs and the backfill worker, diagnostics (`/health`, network, speedtest), maintenance/debug tooling, webhooks, timezone handling, and the SQLite schema/migrations/transaction policy.
2. Evidence base: read-only static analysis of the implementation at the cited `file:line` in this checkout, cross-checked against the existing suites; no server was started and no test suite was run.
3. Out of scope (sibling specs): release/series/download feature semantics and parser/tagger internals; this document covers only the platform contracts those domains consume.

**Verification method.** Static read of `backend/app/*.py`, `backend/app/notifier/`, `backend/app/templates/{settings,logs,base}.html`, `Dockerfile`, `docker-compose*.yml`, `entrypoint.sh`, `backend/requirements.txt`, and the relevant suites (`test_auth_modes.py`, `test_oidc_mock_idp.py`, `test_security_remediation.py`, `test_background_tasks.py`, `test_task_progress_*`, `test_maintenance_tools.py`, `test_storage_health.py`, `test_network_diagnostics.py`, `test_log_rotation.py`, `test_startup_performance.py`, `test_audit_actor.py`, `test_release_cache_clear.py`, `tests/e2e/harness.py`). Line numbers are authoritative for this checkout.

**Contents.** §2 feature inventory (OPS-001…OPS-094) · §3 how it works (3.1 settings · 3.2 auth · 3.3 security · 3.4 scheduler · 3.5 task API · 3.6 logging · 3.7 webhooks · 3.8 diagnostics · 3.9 maintenance · 3.10 deployment · 3.11 endpoint contracts · 3.12 failure modes) · §4 data model (4.1 tables · 4.2 migrations · 4.3 WAL/transactions · 4.4 state machines) · §5 configuration reference (5.1 env vars · 5.2 settings keys) · §6 integration contracts (6.1 task-kind map · 6.2 test coverage map) · §7 UI touchpoints & mobile · §8 open questions/risks (R-1…R-26) · §9 consensus checklist.

---

## 2. Feature inventory

Lifecycle states: **ACTIVE** = implemented and reachable as designed; **PARTIAL** = implemented but materially incomplete/limited/misleading versus its documented intent; **VESTIGIAL** = code/constant/route exists but has no effective consumer or effect.

| ID | Feature | Lifecycle | User-facing behavior | Implementation (file:line) | Trigger | Persistence |
|---|---|---|---|---|---|---|
| OPS-001 | Container image & dependency set | ACTIVE | `python:3.11-slim` image installing `curl`, `ca-certificates`, `gosu`, `p7zip-full`, `unar` + `backend/requirements.txt` (fastapi, uvicorn, jinja2, bs4, feedparser, pydantic, authlib, py7zr, rarfile) | `Dockerfile:1-42`, `backend/requirements.txt:1-9` | Docker build | image layer |
| OPS-002 | Entrypoint privilege step-down (PUID/PGID) | ACTIVE | Starts as root, remaps `appuser` to `PUID`/`PGID` (default 1000), only chowns when an offender is found, then `gosu appuser` | `entrypoint.sh:1-36` | Container start | none |
| OPS-003 | docker-compose deployment profile | ACTIVE | Port 8082→8080, 4 bind mounts (`/calibre:ro`, `/staging`, `/downloads`, `./data`), json-file log cap 10 MB×5, `no-new-privileges`, `cap_drop: ALL`, TZ + auth env templates | `docker-compose.yml:1-74`, `docker-compose.truenas.yml:1-93` | `docker compose up` | host volumes |
| OPS-004 | `/health` liveness payload | ACTIVE | JSON: `status:"ok"`, `auth_mode`, `storage`, `logs`, `network`; never 5xx on probe failure (each block individually shielded) | `main.py:1040-1058`, `main.py:171-198`, `main.py:365-394` | GET `/health` (no auth) | none (reads `app_settings`) |
| OPS-005 | Docker HEALTHCHECK on `/health` | ACTIVE | `curl -f http://localhost:8080/health` every 30 s, 5 s timeout, 10 s start period, 3 retries | `Dockerfile:46-49` | Docker daemon | none |
| OPS-006 | Availability-first lifespan (startup) | ACTIVE | Nothing heavy before `yield`; only `_refresh_storage_health("startup")` and `scheduler.start()`; initial Calibre sync deferred to worker thread | `main.py:213-232`, `test_startup_performance.py:73-152` | App start | `app_settings.storage_health_json` |
| OPS-007 | Graceful shutdown | ACTIVE | `scheduler.stop()`: clears `running`, signals Torbox poller, joins both threads (2 s each), shuts the fetch executor down without waiting | `main.py:228-230`, `scheduler.py:5300-5314` | SIGTERM / uvicorn shutdown | none |
| OPS-008 | Refuse-to-start on invalid auth config | ACTIVE | `AuthConfigError` logged critically and `SystemExit(1)` before the app object is created | `main.py:200-211`, `auth.py:390-449` | App start | none |
| OPS-009 | Env→`app_settings` seeding layer | ACTIVE | Env vars seed DB settings **only** on first DB creation (plus a fixed `INSERT OR IGNORE` subset) | `database.py:726-837` | First `MangaDatabase.__init__` | `app_settings` |
| OPS-010 | Auth mode selection (`none`/`basic`/`oidc`) | ACTIVE | Default `none` = every request allowed, actor `anonymous`. `basic`/`oidc` gate all non-exempt paths | `auth.py:148-152`, `auth.py:326-352`, `main.py:203-211` | `MANGATRAX_AUTH_MODE` | env |
| OPS-011 | Auth middleware gate & exemption set | ACTIVE | Exempt: `/auth/*`, `/static/*`, `/favicon.ico`, `/health`, `/healthz`; HTML gets 302→`/auth/login?next=…`, `/api/*` gets JSON 401; actor ContextVar set per request | `main.py:234-267`, `auth.py:946-949`, `auth.py:1000-1005`, `auth.py:1021-1077` | Every HTTP request | none |
| OPS-012 | HTTP Basic challenge | ACTIVE | 401 + `WWW-Authenticate: Basic realm="MangaTrax", charset="UTF-8"`; credentials compared with `secrets.compare_digest` | `auth.py:533-558`, `auth.py:949`, `main.py:251-259` | Unauthenticated request in `basic` mode | env |
| OPS-013 | OIDC `/auth/login` (state/nonce/PKCE) | ACTIVE | 302 to discovered authorization endpoint with `response_type=code`, `scope="openid profile email"`, `state`, `nonce`, S256 PKCE; signed state cookie `mangatrax_oidc_state`, 600 s TTL, HttpOnly/lax | `main.py:1061-1091`, `auth.py:1108-1123`, `auth.py:153`, `auth.py:800-814` | GET `/auth/login` | state cookie (client) |
| OPS-014 | OIDC `/auth/callback` + session cookie | ACTIVE | Verifies state cookie + `state`, PKCE verifier, exchanges code, validates ID token, sets `mangatrax_session` (HttpOnly, lax, `Secure` iff `PUBLIC_URL` is https), deletes state cookie, redirects to sanitized `next` | `main.py:1094-1125`, `auth.py:1125-1153` | IdP redirect | session cookie (client) + `users` row |
| OPS-015 | `/auth/logout` + IdP end-session | ACTIVE | Clears session cookie; redirects to advertised `end_session_endpoint` with `post_logout_redirect_uri=PUBLIC_URL` when available | `main.py:1128-1134`, `auth.py:885-900`, `auth.py:1155-1162` | GET `/auth/logout` | deletes cookie |
| OPS-016 | Break-glass Basic while `oidc` | ACTIVE | If `MANGATRAX_BASIC_PASS` set, Basic is accepted even in OIDC mode; browser visitors still get the OIDC redirect; a failed Basic attempt re-challenges; `/auth/login` falls back to the Basic challenge (401) instead of 503 when the IdP is down | `auth.py:356-362`, `auth.py:978-990`, `auth.py:1051-1063`, `main.py:1068-1080`, `test_auth_modes.py:349-394` | Basic header present | env |
| OPS-017 | Session state token (HMAC, not JWT) | ACTIVE | `base64url(json).base64url(HMAC-SHA256)` with `v=1`, purpose, `iat`, `exp`; verified constant-time, purpose- and expiry-bound | `auth.py:472-527`, `auth.py:968-969` | Every cookie-verified request | client cookie |
| OPS-018 | ID-token verification (Authlib, fail-closed) | ACTIVE | JWKS + `jwt.decode` + `claims.validate()` via Authlib; explicit claim checks for `iss`, `aud`, `exp`, `iat`/`nbf` skew (60 s), `nonce`, `sub`; missing Authlib raises `AuthProviderUnavailable` (never an insecure fallback) | `auth.py:682-901`, `auth.py:616-676`, `backend/requirements.txt:7`, `test_oidc_mock_idp.py:485-515` | `/auth/callback` | discovery/JWKS cached 3600 s in memory |
| OPS-019 | JIT user provisioning (`users`) | ACTIVE | Upserts on first OIDC login and on Basic auth (throttled 300 s/identity), refreshes `last_seen`, never blanks existing non-empty fields | `auth.py:1080-1100`, `database.py:3478-3509`, `test_audit_actor.py:116-175` | Authenticated request | `users` table |
| OPS-020 | Audit actor attribution | ACTIVE | Middleware sets `current_actor`; `log_activity()` defaults to it (`anonymous`/user, `system` default in threads); scheduler passes `actor="scheduler"`; logs page renders an actor chip | `auth.py:197-224`, `database.py:25-38`, `database.py:3451-3469`, `scheduler.py:135-143`, `logs.html:40-43` | Any `log_activity` call | `activity_logs.actor` |
| OPS-021 | CSRF Origin/Referer guard | PARTIAL | Rejects unsafe methods when `Origin`/`Referer` does not match `PUBLIC_URL`'s origin — **only if auth is enabled**, `MANGATRAX_CSRF_PROTECT` is truthy (default 1) **and** `PUBLIC_URL` is set | `auth.py:1007-1019`, `auth.py:1035-1041`, `test_auth_modes.py:336-345` | Unsafe HTTP methods | none |
| OPS-022 | Security-headers middleware | ACTIVE | Adds `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `X-XSS-Protection: 1; mode=block`, `Referrer-Policy: strict-origin-when-cross-origin` on every response (outermost middleware) | `main.py:269-277`, `main.py:234-238` | Every response | none |
| OPS-023 | `users` read helpers | VESTIGIAL | `get_user` / `get_all_users` exist but no route/template/CLI calls them; there is no user-admin UI or users API | `database.py:3511-3522` (no callers found in `backend/`) | — | `users` table |
| OPS-024 | `/healthz` auth exemption | VESTIGIAL | `"/healthz"` is in `_EXEMPT_EXACT` but no `/healthz` route registered (only `/health`) | `auth.py:947` vs route list `main.py:1040` | — | none |
| OPS-025 | SSRF / outbound URL validation | ACTIVE | `validate_public_url` (strict, DNS re-check of every resolved address, fail-closed) vs `validate_outbound_url` (structural, no DNS); blocks non-http(s), embedded credentials, loopback/RFC1918/CGNAT/link-local/metadata/multicast/reserved literals, `localhost*`, single-label hosts; length ≤ 2048; used by cover cache redirect hops, feed tests, notifier, speedtest allow-list | `security.py:76-266`, `covers.py:142-245`, `notifier/webhook.py:41-61`, `network_diagnostics.py:325-329`, `test_security_remediation.py:62-166` | User-supplied URL / persisted config | none |
| OPS-026 | Path containment for directory settings | ACTIVE | Rejects NUL, `..`, non-absolute, >4096 chars; realpath-resolves; `ensure_path_within_roots` requires the resolved path inside an allowed root (symlink escapes rejected); applied to `calibre_path`/`staging_dir`/`downloads_dir` and `/api/calibre/test` | `security.py:341-417`, `schemas.py:474-483`, `schemas.py:878-891`, `main.py:1937-1953` | Settings save; path probe | none |
| OPS-027 | Stored-XSS href allow-list | ACTIVE | `is_safe_href`/`safe_url_filter` allow only absolute http(s) URLs; `validate_stored_website_url` rejects `javascript:`/`data:`/`file:` before persisting `website_url`; Jinja filter `safe_url` registered | `security.py:272-315`, `main.py:299-300`, `main.py:3832-3838` | Series link save / render | `series_tracking.website_url` |
| OPS-028 | SQL `LIKE` wildcard escaping | ACTIVE | `escape_like`/`like_contains` escape `\`, `%`, `_`; callers must add `ESCAPE '\'` (helper `LIKE_ESCAPE_SQL`) | `security.py:319-335`, `database.py:41-43`, `test_security_remediation.py:418-444` | Search endpoints | none |
| OPS-029 | Debug-action gate | ACTIVE | `/api/debug/action` returns 403 unless `MANGATRAX_DEBUG_ACTIONS=1`; strict `DebugActionRequest` enum (`clear-discovery`, `clear-descriptions`, `clear-thumbnails`, `normalize-english-titles`, `purge-non-manga`, `reset-all-data`) | `main.py:4342-4388`, `schemas.py:982-992`, `auth.py:349-351`, `test_maintenance_tools.py:310-317` | POST `/api/debug/action` | audit rows |
| OPS-030 | Reset-all-data | PARTIAL | Deletes releases, series_tracking, series_blacklist, activity_logs, metadata cache, feeds, prioritized groups, re-seeds default feeds/groups, clears cover cache, triggers a sync. It does **not** touch `users`, `app_settings`, `background_tasks`, `series_alias_overrides`, `torbox_manual_pickups`, `download_failures` — the "all data" label is broader than the deletion set | `main.py:4379-4386`, `database.py:4670-4722` | POST debug action `reset-all-data` | multiple tables |
| OPS-031 | `purge-non-manga` | ACTIVE | Deletes only rows whose `media_type` is an explicit non-manga value; `manga`/`unknown`/NULL kept; audited as `MEDIA_PURGE` | `main.py:4373-4378`, `database.py:3601-3639` | POST debug action | `releases` |
| OPS-032 | Re-keyed series-name repair | ACTIVE | The legacy `normalize-english-titles` action now runs the guarded, idempotent repair that re-keys releases stranded under an AniList display title back to their tracked series' stable key | `main.py:4365-4372`, `database.py:3868-3958`, `scheduler.py:956-962` | POST debug action | `releases.series_name` |
| OPS-033 | Safe maintenance endpoint | ACTIVE | `/api/maintenance/clear` accepts only `clear-cover-cache`, `clear-discovery`, `clear-descriptions` (closed enum, `extra="forbid"`), not debug-gated, audited as `MAINTENANCE_CLEARED` with actor/action/count | `main.py:4323-4339`, `maintenance.py:1-101`, `schemas.py:995-1010`, `test_maintenance_tools.py:95-130,373-395` | POST `/api/maintenance/clear` | `activity_logs` |
| OPS-034 | `GET /api/settings` | ACTIVE | Returns every `app_settings` row; only `torbox_api_key` is replaced by the mask `••••••••••••` when set | `main.py:4211-4217`, `schemas.py:381-383` | GET | reads `app_settings` |
| OPS-035 | `POST /api/settings` | ACTIVE | Strict `SettingsUpdateRequest` (`extra="forbid"`, 45 optional string fields), per-field validators, then `set_setting(k, str(v))` for each provided key; returns `{status, storage}` | `main.py:4219-4279`, `schemas.py:645-923` | Form save / API | `app_settings` |
| OPS-036 | Secret masking / plaintext asymmetry | PARTIAL | Torbox key is masked in HTML+JSON and an unchanged/blank submission is ignored; `qbit_password` is **stored and returned in plaintext** by `GET /api/settings` and rendered into the settings form `value=`; `discord_webhook_url`/`apprise_url` are returned in full; an empty `qbit_password` submission clears it | `main.py:1878-1879,4211-4217,4246-4251`, `settings.html:119,166` | GET/POST `/api/settings` | `app_settings` |
| OPS-037 | `downloads_dir` ≠ `staging_dir` invariant | ACTIVE | Rejects a save that would make the raw-download folder equal (realpath, case-insensitive) to the staging folder | `main.py:4233-4245` | POST `/api/settings` | none |
| OPS-038 | Optional `MANGATRAX_PATH_ROOTS` containment | ACTIVE | When set (`os.pathsep`-separated), `calibre_path`/`staging_dir`/`downloads_dir` must resolve inside those roots; unset = containment checks only | `main.py:328-337,4224-4231`, `test_security_remediation.py:562-576` | POST `/api/settings` | env |
| OPS-039 | Storage health probe & persistence | ACTIVE | Real temp-file create/delete probe of `downloads_dir` and `staging_dir`; returns per-path `{exists,is_dir,writable,uid,gid,errno,error,free_bytes}`; persisted to `storage_health_json`, reused when configured paths are unchanged, refreshed at startup/save/`/health` | `storage_health.py:30-172`, `main.py:357-394`, `main.py:386-394`, `test_storage_health.py:46-270` | Startup, settings save, `/health` | `app_settings.storage_health_json` |
| OPS-040 | Settings-save side effects | ACTIVE | On save: Nyaa base URL repoints the exact seeded feed (`migrate_seeded_nyaa_feed`) and audits `NYAA_BASE_UPDATED`; logging keys reconfigure the rotating handler immediately; path keys re-probe storage and run one inventory+routing pass; always audits `SETTINGS_UPDATED` | `main.py:4254-4279` | POST `/api/settings` | `app_settings`, `feeds` |
| OPS-041 | Internal `app_settings` markers | ACTIVE | Non-UI keys written by the app: `media_filter_applied`, `jpegxl_filter_applied`, `ln_release_backfill_done`, `storage_health_json`, `last_speedtest_json`, `activity_last_prune_at`, `backfill_status`, `backfill_progress`, `backfill_last_page`, `torbox_last_poll_at` | `database.py:833-837,920-938,1070-1074,1138-1142`; `main.py:369,2263`; `scheduler.py:2308,3855-3866,4793-4794` | App internals | `app_settings` |
| OPS-042 | `SETTINGS_KEYS` constant | VESTIGIAL | Tuple of 37 key names defined in `schemas.py` but referenced nowhere (the live contract is `SettingsUpdateRequest`); it already omits `convert_archives_to_cbz`, `webhook_*`, `filter_non_manga`, `infer_collected_releases`, `torbox_manual_pickup_*` | `schemas.py:338-380` (no other reference in `backend/`) | — | none |
| OPS-043 | `/settings` page | ACTIVE | Server-rendered 9-section form (Calibre, Downloader, Staging, Schedules, Tracker, Webhooks, Groups, Maintenance, Advanced-if-debug) with sticky section nav, dirty-state sticky save bar, in-page probes (path test, downloader test, Torbox test, webhook tests, speedtest, copy JSON, prune, audits/repairs) | `main.py:1875-1905`, `settings.html:1-1200` | GET `/settings` | reads many tables |
| OPS-044 | `/api/calibre/test` | ACTIVE | Probes a user-supplied path contained to `{calibre_path, CALIBRE_DIR, staging_dir}`; 403 outside roots, 200 with library summary | `main.py:1937-1953`, `test_security_remediation.py:536-548` | Test Path button | none |
| OPS-045 | `/api/calibre/status` | ACTIVE | Non-blocking `{in_progress, initial_sync_done, last_sync}` | `main.py:1932-1935`, `scheduler.py:3243-3249` | UI poll | in-memory |
| OPS-046 | `/api/downloader/configure` | ACTIVE | Writes `qbit_*` settings **directly** (not via `SettingsUpdateRequest`), audits `DOWNLOADER_CONFIGURED`, then tests the connection | `main.py:3548-3576`, `schemas.py:926-934` | Legacy downloader UI | `app_settings` |
| OPS-047 | `/api/downloader/test`, `/api/downloader/torbox/test` | ACTIVE | Transient-credential connection tests; Torbox accepts an unsaved key (mask treated as "use saved") | `main.py:3516-3547` | Test Connection buttons | none |
| OPS-048 | Rotating file logging | ACTIVE | When `log_to_file=true`, exactly one `RotatingFileHandler` (`DATA_DIR/logs/mangatrax.log`, `maxBytes=max_mb MiB`, `backupCount=backups`, level INFO, named `mangatrax-file`); removing it on disable; reconfigured on save; idempotent | `main.py:88-168`, `main.py:4264-4268`, `test_log_rotation.py:131-185` | Startup; settings save | data dir file |
| OPS-049 | Activity-log ingestion model | ACTIVE | Every audit write inserts `(event_type, message, details, actor)` with UTC `created_at`; `actor` resolves explicit → ContextVar → `system`; no severity/level column; ~100+ distinct event types across modules | `database.py:3451-3475`, `database.py:667-685` | Any audited action | `activity_logs` |
| OPS-050 | Scheduled activity retention prune | ACTIVE | On the first worker tick, then at most every 6 h: delete rows older than `activity_retention_days`, then delete lowest-id rows above `activity_max_rows`, then `PRAGMA wal_checkpoint(TRUNCATE)`; audited `ACTIVITY_PRUNED` when rows were deleted | `scheduler.py:2296-2327`, `database.py:1248-1291`, `scheduler.py:5244-5250` | Worker loop | `activity_logs` |
| OPS-051 | `POST /api/logs/prune` | ACTIVE | Manual prune with the same retention math, run as a recorded task `logs:prune`; response `{status, deleted_old, deleted_excess, remaining, checkpointed, task_id, task}` | `main.py:2664-2672`, `logs.html:74-83`, `settings.html:1151-1160`, `test_log_rotation.py:225-260` | Logs page / Settings button | `activity_logs`, `background_tasks` |
| OPS-052 | `/logs` page | PARTIAL | Renders the newest 150 activity rows with event-type and actor chips, UTC→local timestamps, a client-side substring filter (message/details/event/actor) and a "Prune now" button. No pagination, no server-side filter, no date range, no auto-refresh, no detail expansion | `main.py:1863-1873`, `logs.html:1-107` | GET `/logs` | reads `activity_logs` |
| OPS-053 | `run_in_background` task runner | ACTIVE | Creates a `background_tasks` row, registers in-process cancel flag when `cancellable`, submits to a module-level `ThreadPoolExecutor(max_workers=3)`, audits start/finish/failure/cancel, always clears in-memory maps; returns `{task_id, task}` | `background_tasks.py:24-31,136-173` | Async actions | `background_tasks`, `activity_logs` |
| OPS-054 | `TaskReporter` progress coalescing | ACTIVE | Writes at most every 0.5 s unless progress % moved ≥1.0 or the message changed or `force=True`; clamps to 0–100; progress-write failures never break the task | `background_tasks.py:79-130`, `database.py:1563-1585` | Inside task functions | `background_tasks` |
| OPS-055 | Foreground "recorded" tasks | ACTIVE | `_recorded_task` creates a task row, runs the work synchronously (via `asyncio.to_thread`), reports 100 %, stores the result JSON, audits start/finish, and re-raises failures after marking them `failed`; used by ~20 quick/moderate actions | `main.py:1267-1297` | Audit/repair/prune/sync/speedtest endpoints | `background_tasks`, `activity_logs` |
| OPS-056 | `GET /api/tasks` | ACTIVE | `active`(0/1), `kind`, `limit`(1–200); returns `{status, active_count, tasks[]}` with serialized elapsed/ETA/result; `active_count` costs a second `list_background_tasks` query | `main.py:1235-1245`, `background_tasks.py:51-76`, `database.py:1611-1626` | Global tasks panel (1.5 s poll) | reads `background_tasks` |
| OPS-057 | `GET /api/tasks/{id}` | ACTIVE | Single task or 404 | `main.py:1248-1253`, `database.py:1606-1609` | Task panel / watcher | reads table |
| OPS-058 | `POST /api/tasks/{id}/cancel` | PARTIAL | 404 if unknown; 409 if the task has no in-process cancel flag (all recorded tasks, and **any** task after a restart); otherwise sets the event and returns `{cancelling:true}`. `request_cancel`'s boolean result is ignored | `main.py:1256-1264`, `background_tasks.py:176-187` | Cancel button / API | in-memory event only |
| OPS-059 | Cancel UI wiring | VESTIGIAL | `base.html` renders a Cancel button only when `task.cancellable` is truthy, but `serialize_task` never emits a `cancellable` field, so the button never appears; the only cancellable kind is `tags:refresh` | `base.html:1039`, `background_tasks.py:51-76`, `main.py:1555-1561` | — | none |
| OPS-060 | Background-task pruning helper | VESTIGIAL | `background_tasks.prune()`/`db.prune_background_tasks(days=7)` exist but are never called; `background_tasks` therefore grows unbounded (every page load of an audit endpoint adds rows) | `background_tasks.py:194-199`, `database.py:1628-1640` (no callers) | — | `background_tasks` |
| OPS-061 | Worker loop cadence | ACTIVE | Single daemon thread, 10 s tick, jobs guarded by a wall-clock interval table (see the job table in §3.4); any exception is logged and the loop continues; `float()` settings are re-read every tick | `scheduler.py:5152-5279`, `scheduler.py:5281-5298` | Lifetime of process | none |
| OPS-062 | Calibre sync job + lock | ACTIVE | Interval = `calibre_sync_interval_minutes` (default 15); non-blocking `_calibre_sync_lock` prevents concurrent manual+auto sync; scans metadata.db, syncs series, resolves requests (precedence-enforced), reconciles, expands complete releases, routes downloads; audits `CALIBRE_SYNC`/`_ERROR` | `scheduler.py:3115-3209`, `main.py:1909-1930` | Loop; Sync now | DB tables; `last_calibre_sync` memory |
| OPS-063 | Feed-polling job | ACTIVE | Interval = `feed_check_interval_minutes` (default 60); blocking poll guarded by `_feed_check_lock` (concurrent call returns `skipped=true`); auto scans send the summary webhook | `scheduler.py:5189-5200,3352-3369`, `scheduler.py:1358-1375` | Loop | `feeds`, `releases`, logs |
| OPS-064 | Manual async "Poll Feeds" | ACTIVE | Starts a daemon thread and returns immediately; UI polls `/api/feeds/check/status`; `running/finished_at/result` state is in memory only | `scheduler.py:3372-3418`, `main.py:1979-2016` | Poll Feeds button | in-memory state |
| OPS-065 | Auto-tag staging job | ACTIVE | Every 300 s when `auto_tag` is literally `"true"`: tag staged CBZ files and route eligible raw downloads into staging; errors logged at debug | `scheduler.py:5191-5219,3826-3850` | Loop | files, DB |
| OPS-066 | Frequent inventory + routing pass | ACTIVE | Every 45 s: refresh raw-download inventory and run tag+move routing, so finished files appear without user action | `scheduler.py:5252-5259,2273-2294` | Loop | DB |
| OPS-067 | Stale-release reconcile job | ACTIVE | Every 300 s: reset COMPLETED releases whose files vanished (self-heal) | `scheduler.py:5221-5232,5139-5150` | Loop | DB |
| OPS-068 | Manual-pickup expiry sweep | ACTIVE | Every 300 s: expire past-due Torbox manual pickups (best-effort remote delete) | `scheduler.py:5234-5242,1684-1719` | Loop | `torbox_manual_pickups`, logs |
| OPS-069 | Activity-prune job | ACTIVE | See OPS-050; first tick then ≤6 h | `scheduler.py:5244-5250` | Loop | `activity_logs` |
| OPS-070 | Pending-dedupe sweep | ACTIVE | Every 60 s: retry auto-downloads suppressed by in-flight dedupe (batch ≤50) | `scheduler.py:5266-5272,2329-2345` | Loop | `releases` |
| OPS-071 | Active-download filter job | ACTIVE | Every 30 s: pause/cancel torrent files no longer wanted | `scheduler.py:5261-5264,4912-…` | Loop | engine + DB |
| OPS-072 | Torbox poller thread | ACTIVE | Separate daemon thread; adaptive cadence `torbox_poll_seconds` (default 5) when busy/fetching/ready/routing, else `min(30, max(15, 3×floor))`; wakeable via `request_torbox_poll()` | `scheduler.py:2815-2869,5294-5298` | Loop | `releases.torbox_progress_json` |
| OPS-073 | Initial sync (worker first action) | ACTIVE | Legacy re-key repair then initial Calibre sync (if `metadata.db` exists), both exception-shielded; sets `_initial_sync_done`; exactly once per process | `scheduler.py:3212-3241,5157-5160`, `test_startup_performance.py:158-254` | Worker start | DB |
| OPS-074 | Polite historical backfill worker | ACTIVE | Daemon thread paginates Nyaa/feeds from `start_page` to `max_pages` (0 = unlimited) with `backfill_delay_seconds` delay; persists `backfill_status`/`backfill_progress`/`backfill_last_page`; stops on request | `scheduler.py:4755-4910`, `main.py:4293-4310` | Settings/Discovery backfill | `app_settings`, DB |
| OPS-075 | Backfill start/stop/status API | PARTIAL | `/start` returns `in_progress` when already running (checked before starting; a race can still start a second thread because `run_polite_backfill` also checks), `/stop` sets the flag, `/status` reads persisted settings | `main.py:4293-4321`, `scheduler.py:3852-3868` | Backfill buttons | `app_settings` |
| OPS-076 | Provider-tag refresh | ACTIVE | Async cancellable task `tags:refresh`; backfills AniList genres/tags for up to the first 1000 releases lacking tags; updates `releases.tags_*`, `series_tracking.tags_*` and the metadata cache | `main.py:1552-1562`, `scheduler.py:790-827` | Settings button | DB columns |
| OPS-077 | Suspicious volume-range audit/repair | ACTIVE | GET triggers a persisted task that scans **all** releases and reports chapter-scale volume bundles / chapter-only re-parses; repair only for `safe_to_repair` rows: clears volumes/range, sets `IGNORED`, audits `CHAPTER_RANGE_REPAIRED` | `main.py:2461-2468,2551-2570`, `database.py:1643-1742` | Settings Maintenance | `releases`, `background_tasks` |
| OPS-078 | Series-attribution audit/repair | ACTIVE | Scans tracked releases whose own title parses to another series; ambiguous multi-part names are reported but not repairable; repair re-keys selected rows and audits `SERIES_ATTRIBUTION_REPAIRED` | `main.py:2631-2661`, `scheduler.py:829-954` | Settings Maintenance | `releases`, logs |
| OPS-079 | False fuzzy series-match audit/repair | ACTIVE | POST audit (read-only) and repair (requires `confirm:true`, ≤500 ids) via `false_series_matches`; re-keys only rows the fixed matcher would no longer map | `main.py:2508-2548`, `false_series_matches.py:1-151` | Settings Maintenance | `releases` |
| OPS-080 | Light-novel audit/cleanup | ACTIVE | GET audit (read-only); POST cleanup requires `confirm:true`, `action ∈ {ignore,delete}`, ≤500 ids; delete removes DB rows only (never files) | `main.py:2471-2505`, `ln_releases.py:1-69` | Settings Maintenance | `releases`, logs |
| OPS-081 | Series alias overrides | ACTIVE | `GET /api/maintenance/series-aliases` lists manual source→target mappings; `DELETE /api/maintenance/series-aliases/{id}` removes one (404 if missing) and invalidates the grouped cache | `main.py:2573-2584`, `database.py:1745-1793` | Settings Maintenance | `series_alias_overrides` |
| OPS-082 | Release-cache preview & clear | ACTIVE | `GET /api/maintenance/releases-cache` returns a count for series/status filters; `POST clear-releases` requires `confirm:true`, computes the same filters, always excludes `DOWNLOADING`/`QUEUED` rows, audits `RELEASES_CLEARED` with `skipped_active` | `main.py:2587-2628`, `database.py:1194-1246`, `test_release_cache_clear.py` | Settings Release cache card | `releases`, logs |
| OPS-083 | Network diagnostics snapshot | ACTIVE | Read-only `/proc`,`/sys`, cgroup, DNS, TCP and route facts + heuristic warnings (MTU mismatch, non-BBR congestion control, CPU quota < 1 core, retrans > 0.5 %, Docker DNS); included in `/health` and rendered in Settings with "Copy JSON" | `network_diagnostics.py:1-323`, `main.py:2220-2226,1052-1057`, `settings.html:606-641`, `test_network_diagnostics.py:68-212` | Settings load, `/health`, API | none (no network I/O) |
| OPS-084 | Network speedtest | ACTIVE | Fixed Cloudflare endpoint only (`https://speed.cloudflare.com/__down`), bytes ∈ [1, 1 GB], connections ∈ [1, 8], ≤90 MB per request with sequential rounds, 60 s timeout, global 60 s rate limit (429) taken **before** validation; result persisted to `last_speedtest_json` | `network_diagnostics.py:26-34,325-479`, `main.py:72-73,2229-2275` | Settings "Run speed test" | `app_settings.last_speedtest_json` |
| OPS-085 | Webhook notifier dispatch | ACTIVE | Discord embeds and generic JSON for `NEW_REQUEST`, `ACQUIRED`, `FEED_SCAN`, `DOWNLOAD_PROCESSED`; per-call 5 s timeout; failures logged, never raised; URL structurally SSRF-validated at the transport boundary | `notifier/webhook.py:1-335`, `scheduler.py:979-982` | Release/feed/download events | none |
| OPS-086 | Webhook notification gating | ACTIVE | Automatic feed-scan summaries send only when the scan had activity and `webhook_feed_scan_notifications != "false"`; processed-batch notifications gate on `webhook_processed_notifications != "false"` | `scheduler.py:1332-1420`, `settings.html:427-440` | Feed poll / routing | `app_settings` |
| OPS-087 | Schema bootstrap: 13 tables | ACTIVE | Created idempotently in one transaction: `series_tracking`, `series_blacklist`, `releases`, `prioritized_groups`, `feeds`, `activity_logs`, `users`, `app_settings`, `manga_metadata_cache`, `series_alias_overrides`, `background_tasks`, `torbox_manual_pickups`, `download_failures` (+ indexes) | `database.py:405-997`, indexes `database.py:859-870,883-886,907-910,956-959,976-979` | First connection / every startup | `mangatrax.db` |
| OPS-088 | Column migrations & in-place backfills | ACTIVE | Idempotent `PRAGMA table_info` + `ALTER TABLE` for ~25 `releases` columns, 2 `series_tracking`, 1 `activity_logs`, 7 tag columns; plus in-place data backfills (is_complete, Nyaa `/download/`→`/view/`, description sanitise, `release_groups_json`, published_at, legacy LN typing) — all inside `init_db` | `database.py:430-624,677-685,911-938,980-993` | Every startup | `mangatrax.db` |
| OPS-089 | Connection policy (WAL/foreign keys/timeout) | ACTIVE | New `sqlite3` connection per operation: `timeout=10.0`, `row_factory=Row`, `PRAGMA journal_mode=WAL` (falls back to `DELETE` on failure), `PRAGMA foreign_keys=ON`; no FK constraints are declared on any table; `OperationalError` on open is re-raised with uid/gid/PUID/PGID guidance | `database.py:377-403` | Every query | `mangatrax.db`, `-wal` |
| OPS-090 | Bootstrap defaults & seed data | ACTIVE | When `app_settings`/`feeds`/`prioritized_groups` are empty: 45+ default keys (env-seeded where documented), the stock Nyaa RSS feed, and 4 prioritized groups | `database.py:725-857` | First startup | `app_settings`, `feeds`, `prioritized_groups` |
| OPS-091 | One-shot media re-typing pass | PARTIAL | Gated by `media_filter_applied` **and** `filter_non_manga`; re-types only `unknown` rows (LN / chapter-only / JPEGXL), never deletes, never writes `status`; audited `MEDIA_RECLASSIFY`. After the marker is set the setting has no further effect — there is no "show hidden non-manga types" runtime switch (the tracked views always filter) | `database.py:1005-1103,1105-1164`, `database.py:107-118`, `schemas.py:677` | First startup after upgrade | `releases.media_type` |
| OPS-092 | `app_settings` accessors + activity stats | PARTIAL | `get_setting`/`set_setting`/`get_all_settings` open a new SQLite connection per call (no cache), so every setting read is a fresh connection; `activity_log_stats` returns `{count, oldest_created_at}`; `/health` reports DB and `-wal` file sizes | `database.py:1796-1813,1293-1304`, `main.py:171-198` | Everywhere | `app_settings` |
| OPS-093 | Timezone resolution & template filters | ACTIVE | `app_timezone` setting → `TZ` env → hard-coded `Europe/Copenhagen` → host tz; `localtime`/`localdate` Jinja filters convert UTC SQLite timestamps to the configured IANA zone with DST; invalid zone silently falls back and is stored as-is | `timezone_util.py:1-52`, `main.py:287-298`, `settings.html:353-359` | Every rendered timestamp | `app_settings.app_timezone` |
| OPS-094 | Persistence durability / backup | PARTIAL | Durable state is the `./data` volume (`mangatrax.db` + `-wal`, `covers/`, `logs/`). There is **no** backup/restore, export, or `VACUUM` path anywhere; the only WAL maintenance is `wal_checkpoint(TRUNCATE)` during activity pruning; nothing documents the volume as the backup unit except implication | `main.py:76-86`, `database.py:1281-1287`, `docker-compose.yml:73-74` | — | host volume |

---

## 3. How it works

### 3.1 Settings system (keys, types, validation, effect)

Settings live in `app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)` (`database.py:703-708`) — **all values are strings**; consumers coerce. `get_setting` returns `""` or a caller default; `set_setting` is an upsert (`database.py:1796-1813`). `POST /api/settings` accepts exactly the 45 fields of `SettingsUpdateRequest` (`schemas.py:645-923`) and persists each provided key with `str(v)`; `GET /api/settings` returns the whole table (secrets: see OPS-036).

Truthiness is **not uniform**, and this matters because the schema only validates a handful of keys:

* `== "true"` after `.lower()`: `auto_download` (`scheduler.py:3450,4562`), `auto_tag` (`scheduler.py:5191`), `downloads`/`auto_download` diagnostics (`scheduler.py:1166`), Torbox `as_bool` elsewhere.
* "not in (`false`,`0`,`no`,`off`)": `qbit_enabled` via `as_bool` (`downloader/__init__.py:76`), `discard_unmarked_chapters` (`scheduler.py:263-266`), `filter_non_manga` (`database.py:1039-1041`).
* `== "false"` disables: `webhook_feed_scan_notifications` (`scheduler.py:1360`), `webhook_processed_notifications` (`scheduler.py:1380`).
* `as_bool(...)` default: `convert_archives_to_cbz` (`tagger/pipeline.py:558`), `torbox_manual_pickup_enabled` (`scheduler.py:1613`).

| Key | Type | Default (seed) | Validation at `POST /api/settings` | Effect / consumer |
|---|---|---|---|---|
| `calibre_path` | str | `CALIBRE_DIR` env or `/calibre` (`database.py:729`) | `validate_directory_setting` (NUL/`..`/relative/symlink; if existing must be a dir) (`schemas.py:878-881`, `security.py:406-417`) | Calibre `metadata.db` root: `scheduler.py:3131`, `main.py:1141`, `/api/calibre/test` roots `main.py:1944-1946` |
| `qbit_enabled` | str bool | `"true"` (`database.py:733`) | none | `QBitClient.enabled` (`scheduler.py:984-993`, `downloader/__init__.py:76`); `false/0/no/off` ⇒ `status:"disabled"` in `/api/downloads/status` (`main.py:580-585`) |
| `qbit_url` | str | `QBIT_URL` env or `http://localhost:8080` | scheme ∈ {http,https} + netloc only; LAN allowed (`schemas.py:900-911`) | qBittorrent Web API base (`scheduler.py:989`) |
| `qbit_username` | str | `QBIT_USER` env or `admin` | max 200 | login (`scheduler.py:990`) |
| `qbit_password` | str | `QBIT_PASS` env or `adminadmin` | max 500; **no masking/validation** | login (`scheduler.py:991`); rendered plaintext into `/settings` (`settings.html:119`) |
| `qbit_category` | str | `manga` | max 100 | filter for the live queue listing only; release adds hard-code `manga` (DL-083) |
| `qbit_tags` | str | `mangatrax` | max 200 | stored but unused: release adds hard-code `mangatrax` (DL-083) |
| `staging_dir` | str | `STAGING_DIR` env or `/staging` | `validate_directory_setting` | final tagged destination; storage probe; staging scan (`main.py:353-355`, `scheduler.py:5148`) |
| `downloads_dir` | str | `DOWNLOADS_DIR` env or `/downloads` | `validate_directory_setting`; also `≠ staging_dir` (realpath, case-insensitive) (`main.py:4233-4245`) | raw downloads root, must be same filesystem as staging for rename-based moves (`scheduler.py:1321-1329`, `docker-compose.yml:28-30`) |
| `auto_download` | str bool | `"true"` (`database.py:740`) | none | global auto-download gate in feed ingestion/backfill (`scheduler.py:3450,3685,4562`) |
| `auto_tag` | str bool | `"true"` | none | enables the 300 s tag+route job (`scheduler.py:5191-5219`) |
| `convert_archives_to_cbz` | str bool | `"false"` (`database.py:742`) | none | lossless CBR/RAR/7Z/CB7/ZIP→CBZ conversion in the pipeline (`tagger/pipeline.py:558`) |
| `calibre_sync_interval_minutes` | str int | `15` | **none** (max_length only) | loop interval `float()` (`scheduler.py:5189`); a non-numeric value raises and skips the whole tick's job set (see R-5) |
| `feed_check_interval_minutes` | str int | `60` | **none** | loop interval (`scheduler.py:5190`) |
| `backfill_max_pages` | str int | `15` | **none** | polite backfill page cap; `0` = unlimited (`scheduler.py:4778,4788`) |
| `backfill_delay_seconds` | str float | `2.0` | **none** | delay between backfill requests (`scheduler.py:4784`) |
| `app_timezone` | str | `""` (fallback `Europe/Copenhagen`) | **none** (max 100) | Jinja `localtime`/`localdate` (`main.py:287-298`, `timezone_util.py:6-23`); invalid value stored and ignored |
| `discord_webhook_url` | str | `""` | optional; structural SSRF (`validate_outbound_url`) when non-blank (`schemas.py:893-898`) | notifier (`scheduler.py:980`) |
| `webhook_feed_scan_notifications` | str bool | `"true"` | none | send auto feed-scan summary unless `"false"` (`scheduler.py:1358-1375`) |
| `webhook_processed_notifications` | str bool | `"true"` | none | send processed-batch notification unless `"false"` (`scheduler.py:1377-1420`) |
| `apprise_url` | str | `""` (`INSERT OR IGNORE` absent ⇒ `""`) | optional structural SSRF | generic webhook; read as `apprise_url or generic_webhook_url` (`scheduler.py:981`) |
| `discard_unmarked_chapters` | str bool | `true` (env `MANGATRAX_DISCARD_UNMARKED_CHAPTERS`) | max 20 only | context-guarded chapter discard at ingestion (`scheduler.py:263-266`) |
| `filter_non_manga` | str bool | `true` (env `MANGATRAX_FILTER_NON_MANGA`) | max 20 only | **only** gates the one-shot re-typing pass (`database.py:1039-1041`); has no effect on the always-on view filter (`database.py:107-118`) |
| `infer_collected_releases` | str bool | `true` (env `MANGATRAX_INFER_COLLECTED_RELEASES`) | max 20 only | collected-batch volume inference (`scheduler.py:408-412,509`) |
| `nyaa_base_url` | str URL | `MANGATRAX_NYAA_BASE_URL` env or `https://nyaa.si` | `normalize_nyaa_base_url` (`schemas.py:913-923`, `nyaa_source.py`) | every Nyaa request: feed poll, backfill paging, `.torrent` links (`scheduler.py:964-977`); save also repoints the seeded feed (`main.py:4254-4262`) |
| `downloader_engine` | str enum | `qbit` (env `MANGATRAX_DOWNLOADER_ENGINE`) | `Literal`-like validator `qbit|torbox` (`schemas.py:718-726`) | engine selection (`scheduler.py:996-1003`, `main.py:551`) |
| `torbox_enabled` | str bool | `false` (env) | none | Torbox downloader enablement (`downloader/__init__.py` builder) |
| `torbox_api_key` | secret | `""` (env `MANGATRAX_TORBOX_API_KEY`) | max 500; mask/blank ignored on save (`main.py:4246-4251`) | Torbox auth; masked in HTML/API (`main.py:1878-1879,4215-4216`) |
| `torbox_delete_after_download` | str bool | `true` (env) | none | delete remote torrent after successful fetch (`scheduler.py` Torbox paths) |
| `torbox_poll_seconds` | str int | `5` (env) | int ∈ [5, 3600] (`schemas.py:839-850`) | poller floor (`scheduler.py:2818`) |
| `torbox_max_concurrent` | str int | `2` (env) | int ∈ [1, 20] | Torbox slot cap |
| `torbox_timeout_hours` | str float | `6` (env) | ∈ [0.25, 168] | hard download timeout |
| `torbox_strict_volume_selection` | str bool | `true` (env `MANGATRAX_TORBOX_STRICT_VOLUMES`) | none | strict exact-volume selection vs whole-torrent fallback |
| `torbox_manual_pickup_enabled` | str bool | **not seeded**; `get_setting` default `"true"` (`scheduler.py:1613`) | none | hold unmappable torrents for Manual Pickup |
| `torbox_manual_pickup_max_items` | str int | not seeded; default `10` | none | pickup cap (0 = unlimited) (`scheduler.py:1615`) |
| `torbox_manual_pickup_expiry_days` | str int | not seeded; default `7` | none | pickup expiry (0 = never) (`scheduler.py:1619`) |
| `torbox_max_download_gb` | str num | `64` (env) | ∈ [1, 10240], integral preferred (`schemas.py:816-827`) | per-file/whole-archive GiB cap |
| `torbox_delete_on_failure` | str bool | `true` (env) | bool words only (`schemas.py:829-837`) | delete permanently failing torrents |
| `torbox_download_concurrency` | str int | `4` (env) | int ∈ [1, 16] | parallel file downloads; 1 = sequential |
| `torbox_parallel_chunks` | str int | `4` (env) | int ∈ [1, 8] | ranged chunks per large file |
| `log_to_file` | str bool | `false` (env `MANGATRAX_LOG_TO_FILE`) | normalized to `"true"`/`"false"` (`schemas.py:728-736`) | attaches/removes the rotating handler (`main.py:114-168`) |
| `log_file_max_mb` | str int | `10` (env) | ∈ [1, 100] | `maxBytes` (`main.py:133-135,152-154`) |
| `log_file_backups` | str int | `5` (env) | ∈ [1, 20] | `backupCount` |
| `activity_retention_days` | str int | `90` (env) | ∈ [0, 3650]; 0 = forever | prune cutoff (`scheduler.py:2299`, `database.py:1264-1267`) |
| `activity_max_rows` | str int | `50000` (env) | ∈ [0, 1 000 000]; 0 = uncapped | row cap (`database.py:1270-1278`) |
| `media_filter_applied` | marker | `""` | **not writable** (unknown key ⇒ 400) | one-shot typing gate (`database.py:1036-1038,1070-1074`) |
| `jpegxl_filter_applied` | marker | absent | not writable | JPEGXL one-shot gate (`database.py:1117-1142`) |
| `ln_release_backfill_done` | marker | absent | not writable | legacy LN classification (`database.py:920-938`) |
| `storage_health_json` | JSON | absent | not writable | cached storage probe (`main.py:365-394`) |
| `last_speedtest_json` | JSON | absent | not writable | written (`main.py:2263-2269`), **never read** anywhere |
| `activity_last_prune_at` | epoch str | absent | not writable | written (`scheduler.py:2308`), **never read** |
| `backfill_status` / `backfill_progress` / `backfill_last_page` | str | `idle`/`''`/absent | not writable | backfill UI state (`scheduler.py:3852-3868,4793`) |
| `torbox_last_poll_at` | timestamp | absent | not writable | poll telemetry (`scheduler.py:2863`) |
| `generic_webhook_url` | str | absent | not writable (fallback) | alias read by notifier (`scheduler.py:981`) |

Validation gaps worth calling out as **spec facts** (not opinions): `calibre_sync_interval_minutes`, `feed_check_interval_minutes`, `backfill_max_pages`, `backfill_delay_seconds`, `app_timezone`, `auto_download`, `auto_tag`, `discard_unmarked_chapters`, `filter_non_manga`, `infer_collected_releases`, `webhook_*`, `torbox_*` boolean-ish keys accept arbitrary ≤20-char strings through the API. Consequences are enumerated in §8.

**UI control ↔ setting key mapping.** The Settings form always submits a **fixed literal of all 45 keys** (`settings.html:1032-1078`) — there is no diff/patch protocol, so pressing "Save Settings" rewrites every writable key, including values the user never touched (and including the plaintext `qbit_password` read back from the DOM). Several keys have no UI control at all (`feed_check_interval_minutes` has one, but nothing exposes `torbox_manual_pickup_*` seeding, `generic_webhook_url`, or any internal marker).

| Settings control id (`settings.html`) | Setting key | Control kind / line |
|---|---|---|
| `setting-calibre-path` | `calibre_path` | text + Test Path (`69`) |
| `setting-qbit-enabled` | `qbit_enabled` | checkbox (`103`) |
| `setting-qbit-url` | `qbit_url` | text (`110`) |
| `setting-qbit-user` | `qbit_username` | text (`115`) |
| `setting-qbit-pass` | `qbit_password` | password, value echoed (`119`) |
| `setting-qbit-category` | `qbit_category` | text (`126`) |
| `setting-qbit-tags` | `qbit_tags` | text (`131`) |
| `setting-downloader-engine` | `downloader_engine` | select qbit/torbox (`149`) |
| `setting-torbox-enabled` | `torbox_enabled` | checkbox (`155`) |
| `setting-torbox-api-key` | `torbox_api_key` | password, masked value (`166`) |
| `setting-torbox-delete-after` | `torbox_delete_after_download` | checkbox (`180`) |
| `setting-torbox-strict-selection` | `torbox_strict_volume_selection` | checkbox (`187`) |
| `setting-torbox-manual-pickup-enabled` | `torbox_manual_pickup_enabled` | checkbox (`194`) |
| `setting-torbox-manual-pickup-max-items` | `torbox_manual_pickup_max_items` | number 0–1000 (`202`) |
| `setting-torbox-manual-pickup-expiry-days` | `torbox_manual_pickup_expiry_days` | number 0–3650 (`207`) |
| `setting-torbox-delete-on-failure` | `torbox_delete_on_failure` | checkbox (`211`) |
| `setting-torbox-download-concurrency` | `torbox_download_concurrency` | number 1–16 (`219`) |
| `setting-torbox-parallel-chunks` | `torbox_parallel_chunks` | number 1–8 (`224`) |
| `setting-torbox-max-download-gb` | `torbox_max_download_gb` | number 1–10240 (`229`) |
| `setting-torbox-poll-seconds` | `torbox_poll_seconds` | number 5–3600 (`234`) |
| `setting-torbox-max-concurrent` | `torbox_max_concurrent` | number 1–20 (`238`) |
| `setting-torbox-timeout-hours` | `torbox_timeout_hours` | number 0.25–168 (`242`) |
| `setting-downloads-dir` | `downloads_dir` | text + storage warning (`264`) |
| `setting-staging-dir` | `staging_dir` | text + storage warning (`270`) |
| `setting-auto-download` | `auto_download` | checkbox (`278`) |
| `setting-auto-tag` | `auto_tag` | checkbox (`286`) |
| `setting-convert-archives` | `convert_archives_to_cbz` | checkbox (`294`) |
| `setting-discard-unmarked-chapters` | `discard_unmarked_chapters` | checkbox (`302`) |
| `setting-filter-non-manga` | `filter_non_manga` | checkbox (`310`) |
| `setting-infer-collected-releases` | `infer_collected_releases` | checkbox (`318`) |
| `setting-calibre-interval` | `calibre_sync_interval_minutes` | number 1–1440 (`337`) |
| `setting-feed-interval` | `feed_check_interval_minutes` | number 5–1440 (`341`) |
| `setting-backfill-pages` | `backfill_max_pages` | number 0–1000 (`345`) |
| `setting-backfill-delay` | `backfill_delay_seconds` | number 0.5–30 (`350`) |
| `setting-timezone` | `app_timezone` | text (`357`) |
| `setting-nyaa-base-url` | `nyaa_base_url` | text (`376`) |
| `setting-discord-url` | `discord_webhook_url` | url + Test Discord (`404`) |
| `setting-apprise-url` | `apprise_url` | url + Test Generic (`418`) |
| `setting-webhook-feed-scan` | `webhook_feed_scan_notifications` | checkbox (`428`) |
| `setting-webhook-processed` | `webhook_processed_notifications` | checkbox (`435`) |
| `setting-log-to-file` | `log_to_file` | checkbox (`575`) |
| `setting-log-max-mb` | `log_file_max_mb` | number 1–100 (`580`) |
| `setting-log-backups` | `log_file_backups` | number 1–20 (`585`) |
| `setting-activity-retention` | `activity_retention_days` | number 0–3650 (`589`) |
| `setting-activity-max-rows` | `activity_max_rows` | number 0–1000000 (`592`) |

Note the mismatch class this table exposes: the browser enforces ranges via `min`/`max`/`type=number`, but the server model does not, so the UI ranges in this table are **not** enforced for API callers.

### 3.2 Authentication flows

**Configuration object.** `AuthConfig.from_env` (`auth.py:326-352`) reads the documented env block (`auth.py:29-66`): `MANGATRAX_AUTH_MODE`, `PUBLIC_URL` (rstrip `/`), `SESSION_SECRET`, `BASIC_USER` (default `admin`), `BASIC_PASS`, `OIDC_ISSUER` (rstrip `/`), `OIDC_CLIENT_ID/SECRET`, `SESSION_MAX_AGE` (clamped 300 s…30 d, default 604800), `CSRF_PROTECT` (default 1), `DEBUG_ACTIONS` (default 0). Derived: `auth_enabled` = mode ∈ {basic,oidc}; `break_glass_enabled` = oidc ∧ basic_pass; `callback_url` = `PUBLIC_URL + /auth/callback`; `public_origin`; `cookie_secure` = scheme of `PUBLIC_URL == https`. `validate()` (`auth.py:390-449`) rejects an unknown mode, a `PUBLIC_URL` that is not absolute http(s) or contains query/fragment, `basic` without a password, and `oidc` without public URL / ≥16-char session secret / issuer / client id / secret. `main.py:203-211` logs `describe()` (secrets masked) and exits 1 on failure.

**`none` (default).** No credential is ever read; every request is allowed and the actor label is `anonymous` (`auth.py:1030-1033`). This is the default in both compose files (`docker-compose.yml:43`, `docker-compose.truenas.yml:55`).

**`basic`.** Middleware → `parse_basic_authorization` (strict `Basic <b64>` where the decoded payload must contain `:`) → `basic_identity` with two `secrets.compare_digest` calls (`auth.py:533-558`). Failure ⇒ 401 + `WWW-Authenticate` challenge (`auth.py:1051-1063`), so browsers show their native dialog. There is no lockout, delay, or attempt counter.

**`oidc`.** `/auth/login` (`main.py:1061-1091`) → `AuthManager.start_login` generates `state`/`nonce` (`token_urlsafe(32)`) and a 64-byte PKCE verifier whose S256 challenge is base64url(sha256); all four values plus a sanitized `next` are HMAC-signed into the state cookie (600 s TTL, HttpOnly, lax, `Secure` per `PUBLIC_URL`) and the browser is 302'd to the discovered `authorization_endpoint` (`auth.py:1108-1123,800-814`). `/auth/callback` (`main.py:1094-1125`) reads the state cookie, requires `code`, verifies state-cookie signature/expiry and constant-time `state` equality, requires the PKCE verifier, POSTs the code to `token_endpoint` (client secret + `redirect_uri` from `PUBLIC_URL` only), validates the ID token, JIT-provisions the user (`force=True`), sets the session cookie and redirects to the sanitized `next` (`auth.py:1125-1153`). Errors are generic in the browser (`?error=oidc_denied|oidc_failed`) and detailed only in logs (`main.py:1104-1113`).

**ID-token verification.** `OIDCClient` caches discovery and JWKS for 3600 s (`auth.py:693-841`). Signature/JWKS validation is delegated to Authlib (`JsonWebKey.import_key_set` + `jwt.decode` + `claims.validate()`); an ImportError fails closed with `AuthProviderUnavailable` (`auth.py:843-864`). Claim checks are then re-done explicitly (`iss` normalized-trailing-slash equality, `aud` membership, numeric `exp` not expired, `iat` not > now+60, `nbf` not > now+60, constant-time `nonce`, non-empty `sub`) (`auth.py:616-676`). Discovery validates that the document's `issuer` matches the configured issuer and that `authorization_endpoint`, `token_endpoint`, `jwks_uri` exist (`auth.py:786-797`).

**Logout.** Local cookie deletion always happens; if the discovery document advertises `end_session_endpoint`, the redirect goes there with `post_logout_redirect_uri = PUBLIC_URL` (`auth.py:885-900`, `main.py:1128-1134`).

**Middleware.** Registered before the headers middleware, so auth-produced 401/302/403 responses still carry security headers. Order inside `check_request` (`auth.py:1021-1077`): resolve identity → (auth disabled ⇒ allow) → CSRF check → exempt path → authenticated allow (+ throttled JIT refresh) → unauthenticated: 401 challenge (basic, or oidc-with-break-glass when an `Authorization` header was presented) → `/api/*` JSON 401 → 302 `/auth/login?next=<quoted path>`. Any internal exception returns a 500 JSON `{"detail":"Authentication error"}` rather than failing open (`main.py:243-245`). The actor ContextVar is set/reset around `call_next` (`main.py:247-267`), which is what gives every audit row its actor.

**Actors.** `Identity.actor` prefers `display_name` → `username` → `subject` → `"unknown"` (`auth.py:240-246`); Basic identities are `basic:<user>` subject with `username` as display name; worker threads call `log_activity(..., actor="scheduler")` explicitly (`scheduler.py:135-143`, `database.py:25-38`).

**Cookie matrix (the only two cookies the app sets).**

| Cookie | Value | TTL / `Max-Age` | Flags | Set by | Removed by |
|---|---|---|---|---|---|
| `mangatrax_session` | `SignedToken(session_secret, session_max_age, "session")` payload `{sub, username, email, name, src}` | `MANGATRAX_SESSION_MAX_AGE` (default 604800 s), also embedded as `exp` inside the token | `HttpOnly`, `SameSite=Lax`, `path=/`, `Secure` iff `PUBLIC_URL` is https | `/auth/callback` (`main.py:1115-1123`) | `/auth/logout` (`main.py:1133`); expiry/tamper ⇒ treated as unauthenticated |
| `mangatrax_oidc_state` | `SignedToken(session_secret, 600, "oidc_state")` payload `{state, nonce, verifier, next}` | 600 s | same flags | `/auth/login` (`main.py:1082-1090`) | `/auth/callback` (`main.py:1124`); expiry/tamper ⇒ `AuthError` |

No CSRF token cookie/header exists; CSRF relies solely on the Origin/Referer comparison described in §3.3, and no cookie is `SameSite=Strict`.

### 3.3 Security controls and exact coverage

| Control | Where | Exact coverage / limits |
|---|---|---|
| Auth gate | `auth.py:1000-1077`, `main.py:234-267` | All paths except `/auth/*`, `/static/*`, `/favicon.ico`, `/health`, `/healthz`. `none` mode = no gate at all. |
| Basic credential check | `auth.py:550-558` | Constant-time user+pass; no rate limiting/lockout; password held in env. |
| Session integrity | `auth.py:472-527` | HMAC-SHA256 over `base64url(json)` incl. purpose+expiry; not encrypted (claims are readable in the cookie); no rotation/revocation list — logout only deletes the client cookie, a copied cookie stays valid until `exp`. |
| CSRF | `auth.py:1007-1019` | Only unsafe methods; only when `auth_enabled ∧ csrf_protection ∧ public_origin`; Origin compared exactly (trailing `/` stripped), else Referer prefix; **no** Origin/Referer ⇒ allowed. OIDC mode always has `public_origin` (required), `basic` mode only if `PUBLIC_URL` set, `none` mode never. |
| Security headers | `main.py:269-277` | 4 headers on all responses. **No** CSP, HSTS, `Permissions-Policy`, `Cache-Control` on authenticated pages. Inline `<script>`/`onclick` are used throughout templates, so a strict CSP is not currently compatible. |
| Cookie attributes | `main.py:1082-1090,1115-1123`, `auth.py:379-387` | `HttpOnly`, `SameSite=Lax`, `path=/`, `Secure` iff `PUBLIC_URL` is https. App never derives scheme from the request. |
| SSRF | `security.py:76-266` | Blocked ranges incl. `169.254.0.0/16` metadata, CGNAT, TEST-NETs, IPv6 ULA/link-local/multicast, IPv4-mapped re-check; single-label hostnames rejected; DNS re-checked for strict callers; credentials-in-URL rejected. Speedtest further pinned to a fixed Cloudflare URL prefix. |
| Path containment | `security.py:341-417` | Traversal/NUL/relative/symlink resolved; `ensure_path_within_roots` for `/api/calibre/test` and optional `MANGATRAX_PATH_ROOTS`; staging filename resolution validates against the staging scan before touching disk (`main.py:402-463`). |
| Stored XSS (URLs) | `security.py:272-315`, `main.py:300` | Only absolute http(s) accepted as `website_url` and rendered as href. |
| SQL LIKE injection | `security.py:324-335`, `database.py:41-43` | Wildcards escaped; callers add `ESCAPE '\'`; all other SQL uses parameters. |
| Debug gate | `main.py:4349-4353` | 403 unless `MANGATRAX_DEBUG_ACTIONS=1`; safe cleanups deliberately outside the gate (`maintenance.py:1-15`). |
| Config secrets in logs | `main.py:210`, `auth.py:451-466` | `describe()` masks session secret, basic pass, OIDC client secret; Torbox key is explicitly not logged. But `GET /api/settings` and `/settings` HTML expose `qbit_password` (OPS-036). |

### 3.4 Scheduler, background jobs and lifecycle

`MangaScheduler` is constructed at import (`main.py:86`). `start()` (`scheduler.py:5281-5298`) starts two daemon threads: the main worker (`MangaTraxScheduler`) and the Torbox poller (`MangaTraxTorboxPoller`); `stop()` (`scheduler.py:5300-5314`) clears `running`, sets the poller stop flag + wakeup, joins both with a 2 s timeout, and shuts the fetch executor without waiting. `perform_initial_sync()` runs as the worker's very first action.

| Job (thread) | Cadence | What it does | Calls | Lock |
|---|---|---|---|---|
| Initial sync (worker, once) | first action | legacy re-key repair, then Calibre sync if `metadata.db` exists | `repair_series_matching_keys`, `sync_calibre_library` | `_calibre_sync_lock` inside sync |
| Calibre sync (worker) | `calibre_sync_interval_minutes` (15) | scan library, sync series, resolve requests (precedence), reconcile, expand complete, route downloads; `CALIBRE_SYNC` audit | `sync_calibre_library` | `_calibre_sync_lock` (non-blocking; concurrent call returns `already in progress`) |
| Feed poll (worker) | `feed_check_interval_minutes` (60) | poll enabled feeds, ingest, classify, auto-download, notify summary | `check_all_feeds(trigger="auto")` | `_feed_check_lock` (non-blocking ⇒ `skipped`) |
| Complete-series expansion + volume reconcile | every 300 s | expand unresolved complete releases; persist reconciled volume state | `expand_unresolved_complete_releases`, `db.reconcile_release_volume_state` | none |
| Tag + route staging | every 300 s, if `auto_tag=="true"` | tag staged CBZs, move eligible raw downloads to staging | `tag_staging_files`, `process_downloads_to_staging` | none explicit |
| Stale-file reconcile | every 300 s | reset COMPLETED rows whose files vanished | `reconcile_stale_release_files` | none |
| Manual-pickup sweep | every 300 s | expire past-due pickups (best-effort remote delete) | `sweep_torbox_manual_pickups` | none |
| Activity prune | first tick then ≤6 h (21600 s) | retention + row-cap prune + WAL truncate checkpoint | `prune_activity_logs` | none |
| Inventory + routing | every 45 s | refresh downloads inventory, tag+move eligible files | `periodic_downloads_inventory_and_route` | none |
| Active-download filter | every 30 s | keep only still-wanted torrent files | `check_and_filter_active_downloads` | engine-internal |
| Pending-dedupe sweep | every 60 s | retry dedupe-suppressed auto-downloads (≤50/pass) | `sweep_pending_dedupe` | none |
| Torbox poller (separate thread) | busy: `torbox_poll_seconds`; idle: `min(30, max(15, 3×floor))` | poll Torbox, drive fetch/routing, extend progress JSON | `poll_torbox_downloads` | `_torbox_poll_lock`, per-release fetch locks |
| Polite backfill (on demand daemon thread) | per request delay | page Nyaa/feeds from `start_page`, cap `backfill_max_pages`, persist progress | `_run_polite_backfill_worker` | `is_backfilling` flag |
| Async feed poll (on demand) | one-shot thread | manual "Poll Feeds" without blocking the event loop | `check_all_feeds(trigger="manual")` | `_feed_check_state_lock` |
| Task executor (module-level) | on demand | up to 3 concurrent persisted background tasks | `_run_task` | `_LOCK` around in-memory maps |

The loop sleeps 10 s per tick and wraps **all** interval computations and job calls in one `try/except` (`scheduler.py:5185-5277`), so a single bad interval value or job exception aborts the remainder of that tick (see §8 R-5).

#### 3.4.1 Threads and synchronization primitives

| Primitive | Defined | Protects | Notes |
|---|---|---|---|
| `threading.Thread` uvicorn workers | `Dockerfile:52` | HTTP request handling | one process, default uvicorn worker count |
| asyncio default executor | `main.py` `asyncio.to_thread(...)` | blocking route work (DB reads, audits, sync, speedtest) | shared with Starlette; long audits consume its slots |
| `MangaTraxScheduler` thread | `scheduler.py:5292` | periodic job loop | daemon; 10 s sleep |
| `MangaTraxTorboxPoller` thread | `scheduler.py:5295-5298` | Torbox polling | daemon; adaptive wait on `_torbox_poll_wakeup` |
| feed-check thread | `scheduler.py:3386-3389` | manual "Poll Feeds" | one per click, guarded by in-memory `running` state |
| backfill thread | `scheduler.py:4767-4772` | polite historical backfill | daemon; `is_backfilling` flag |
| task executor (3) | `background_tasks.py:24-27` | persisted background tasks | module-level; survives across requests |
| Torbox fetch executor | `scheduler.py:2475-2485` | local Torbox file fetches | bounded by `torbox_max_concurrent` |
| speedtest `ThreadPoolExecutor(connections)` | `network_diagnostics.py:446` | parallel downloads | max 8 |
| `scheduler._lock` | `scheduler.py:90` | `start()`/`stop()` re-entrancy | |
| `_calibre_sync_lock` | `scheduler.py:91` | exactly one Calibre sync | non-blocking acquire |
| `_feed_check_lock` | `scheduler.py:125` | exactly one feed poll | non-blocking ⇒ `skipped` |
| `_feed_check_state_lock` | `scheduler.py:126` | feed-poll status dict | |
| `_torbox_poll_lock` / `_torbox_fetch_lock` / `_torbox_fetch_locks[release]` | `scheduler.py:101-103` | one poll pass, one fetch-map mutation, one fetch per release | |
| `_TORBOX_STATUS_LOCK` + `building` flag | `main.py:57-58` | single-flight rebuild of the 1 s Torbox status cache | |
| `background_tasks._LOCK` | `background_tasks.py:30` | `_RUNNING` / `_CANCEL_FLAGS` maps | |
| `TaskReporter._lock` | `background_tasks.py:86` | throttle bookkeeping per task | |
| `progress_lock` | `network_diagnostics.py:403` | shared byte counter across speedtest threads | |
| `_NETWORK_SPEEDTEST_LAST` | `main.py:72-73` | 60 s rate-limit window | **no lock** — concurrent requests can both pass the check |
| `CoverCache` writes | `covers.py:229` | atomic move into the cache dir | no in-memory lock; filename is a hash of the URL so duplicate work is wasted but harmless |
| `MangaDatabase._grouped_cache` | `database.py:363,371-375` | per-process memo of grouped releases | plain dict, no lock; invalidation from any thread |
| OIDC discovery/JWKS caches | `auth.py:705-708` | avoids a fetch per request | no lock; a benign duplicate fetch is possible |

Worker-thread DB access opens its own SQLite connection per call (WAL), so background writes do not block readers in practice; `PRAGMA busy_timeout` is not set beyond `sqlite3.connect(timeout=10.0)`.

### 3.5 Persisted background tasks — API semantics

* **Creation.** `run_in_background(db, kind, title, fn, actor, scope, cancellable)` (`background_tasks.py:158-173`) inserts a `background_tasks` row with `status='running'` before submitting; failures to submit mark the row `failed`. `_recorded_task` (`main.py:1267-1297`) is the synchronous variant.
* **Progress.** `fn` receives a `TaskReporter`; calling it writes `items_done`, `items_total`, `message` (≤500 chars), `progress_pct` (clamped 0–100) at most every 0.5 s unless the percentage moved ≥1 or the message changed or `force=True` (`background_tasks.py:104-129`, `database.py:1563-1585`).
* **Completion.** success ⇒ `status='success'`, `progress_pct=100`, `result_json` (JSON ≤20 000 chars), `finished_at`; `TaskCancelled` ⇒ `cancelled`; any other exception ⇒ `failed` with `"<Type>: <msg>"` (≤2000 chars). Each transition writes an `activity_logs` row `BACKGROUND_TASK_FINISHED`.
* **Reading.** `serialize_task` adds `elapsed_seconds` (UTC wall clock, from `started_at`/`finished_at`), rounded `progress_pct`, `active = status=="running"`, parsed `result`, `remaining`, `eta_seconds` (only when `total` and `done` are non-zero) (`background_tasks.py:33-76`). It does **not** emit `cancellable`.
* **Cancellation.** Only tasks created with `cancellable=True` have a `threading.Event` in `_CANCEL_FLAGS`; `cancel_supported` is an in-memory check, so cancellation is impossible after a process restart and for every recorded task. `request_cancel` returns `False` when the flag vanished; the endpoint ignores it and still answers `{"cancelling": true}` (`main.py:1256-1264`). A cancelled `tags:refresh` surfaces as `failed` (`RuntimeError("Cancelled by user")` via `scheduler.py:808-809`) rather than `cancelled`, because `refresh_provider_tags` raises a plain `RuntimeError` and only `TaskCancelled` maps to the cancelled state.
* **Kinds in use** (grep of `run_in_background`/`_recorded_task`): `tags:refresh`, `feeds:poll`, `torbox:pickup:fetch`, `torbox:pickup:retry`, `downloads:inventory-clear`, `downloads:staging-series-match`, `downloads:staging-purge`, `downloads:process`, `downloads:prune`, `downloads:flatten`, `downloads:rescan`, `downloads:failures-scan`, `downloads:archive-convert`, `downloads:torbox-clear-failed`, `speedtest`, `audit:volume-ranges`, `repair:volume-ranges`, `audit:attribution`, `repair:attribution`, `audit:comicinfo`, `repair:comicinfo`, `repair:false-series`, `repair:light-novels-ignore`, `repair:light-novels-delete`, `releases:clear`, `logs:prune`, `sync:calibre`.
* **Retention.** None in practice (OPS-060).

### 3.6 Logging (activity log vs file log)

Two independent systems:

1. **Activity log (DB).** `activity_logs(id, event_type, message, details, actor, created_at)` (`database.py:666-685`). Every write goes through `db.log_activity` — 104 call sites producing ≈70 distinct `event_type` literals (plus a few dynamically composed ones), e.g. `SETTINGS_UPDATED`, `MAINTENANCE_CLEARED`, `DEBUG_CLEAR`, `MEDIA_PURGE`, `CALIBRE_SYNC`, `CALIBRE_SYNC_ERROR`, `REQUEST_RESOLVED`, `BACKGROUND_TASK_STARTED/FINISHED`, `ACTIVITY_PRUNED`, `RELEASES_CLEARED`, `CHAPTER_RANGE_REPAIRED`, `SERIES_ATTRIBUTION_REPAIRED`, `TORBOX_MANUAL_PICKUP_CAP`, `NYAA_BASE_UPDATED`, `SYSTEM_RESET`, `MEDIA_RECLASSIFY`. Timestamps are `CURRENT_TIMESTAMP` (UTC). There is no severity column; the `/logs` page colours on substring `ERROR`/`AUTO_TAG`/`DOWNLOAD` (`logs.html:37`), which misses most failure events (e.g. `CALIBRE_SYNC_ERROR` does match `ERROR`; `MEDIA_PURGE` does not).
2. **File log.** `logging.basicConfig(level=INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")` at import (`main.py:46-50`); optional `RotatingFileHandler` at `DATA_DIR/logs/mangatrax.log` (`main.py:88-168`). `uvicorn` is started from `CMD` so its access log goes to stdout only — it is never written into the activity log and (unless propagated) not necessarily into the rotating file.

Retention (OPS-050) applies only to `activity_logs`; the rotating file self-limits via `maxBytes×(backupCount+1)`; Docker's `json-file` driver caps stdout at 10 MB×5 (`docker-compose.yml:13-17`).

**Event catalogue by area** (from the 68 literal `event_type` values): settings/maintenance (`SETTINGS_UPDATED`, `MAINTENANCE_CLEARED`, `DEBUG_CLEAR`, `MEDIA_PURGE`, `MEDIA_RECLASSIFY`, `SYSTEM_RESET`, `NYAA_BASE_UPDATED`, `ACTIVITY_PRUNED`, `RELEASES_CLEARED`, `CHAPTER_RANGE_REPAIRED`, `SERIES_ATTRIBUTION_REPAIRED`, `FALSE_SERIES_MATCH_REPAIRED`, `SERIES_KEYS_REPAIRED`, `CBZ_SERIES_REPAIRED`); task lifecycle (`BACKGROUND_TASK_STARTED`, `BACKGROUND_TASK_FINISHED`); Calibre (`CALIBRE_SYNC`, `CALIBRE_SYNC_ERROR`, `REQUEST_RESOLVED`, `AWAITING_CALIBRE_SERIES`, `SERIES_CALIBRE_REFRESHED`, `SERIES_METADATA_REFRESHED`); feeds/backfill (`FEED_ADDED`, `FEED_ENABLED`, `FEED_ERROR`, `FEED_POLL_COMPLETED`, `BACKFILL_COMPLETED`, `BACKFILL_ERROR`, `SERIES_BACKFILLED`); downloads (`MANUAL_DOWNLOAD`, `AUTO_DOWNLOAD`, `SELECTIVE_DOWNLOAD`, `IN_FLIGHT_DEDUPE`, `DOWNLOADER_CONFIGURED`, `DOWNLOADS_ROUTING_FAILED`, `DOWNLOAD_FILES_MISSING`, `DOWNLOAD_FILE_DELETED`, `DOWNLOAD_FAILURE_*`, `DOWNLOAD_INVENTORY_*`, `TORBOX_*`); tagging (`AUTO_TAG_CBZ`, `MANUAL_TAG_CBZ`, `TAGGED_AND_MOVED_TO_STAGING`, `ARCHIVE_CONVERTED_TO_CBZ`, `VOLUME_NAME_*`, `STAGING_FILE_RENAMED`, `STAGING_DUPLICATE_PURGED`); media typing (`JPEGXL_DISCARDED`, `CHAPTER_DISCARDED`, `COLLECTED_RELEASE_INFERRED`, `LIGHT_NOVEL_RELEASE_*`, `SPINOFF_SKIPPED`); discovery/series (`SERIES_TRACKED`, `SERIES_BLACKLISTED`, `BLACKLIST_REMOVED`, `RELEASE_IGNORED`, `RELEASE_RESTORED`, `RELEASE_REKEYED_DIFFERENT_SERIES`, `GROUP_PRIORITIZED`, `GROUP_UNPRIORITIZED`, `SERIES_MANUALLY_MATCHED`). This catalogue is the de-facto API for any future log-level/severity redesign: there is no `level` field to filter on, so severity can only be derived from the event name.

### 3.7 Webhooks

`NotificationService` (`notifier/webhook.py:63-335`) holds `discord_webhook` and `generic_webhook` read fresh from settings per send (`scheduler.py:979-982`). Events: `notify_new_request` (Discord embed with series/volume/group(s)/authors/publisher/revision/feed + generic `NEW_REQUEST` payload), `notify_acquired` (`ACQUIRED`), `notify_feed_scan` (`FEED_SCAN` with found/requested/sent/new/transitions/failures + first 3 errors), `notify_download_processed` (`DOWNLOAD_PROCESSED` with series/volumes/files/destination). Delivery uses `urllib` with a 5 s timeout and swallows all exceptions after logging (`notifier/webhook.py:239-251`); `_post_json` structurally validates the URL first (`notifier/webhook.py:41-61`) — so a self-hosted LAN Apprise endpoint is allowed while loopback/metadata literals are not. `test_webhook` (used by `POST /api/settings/test-webhook`) returns per-channel `{success,message}` and an overall `status` (`notifier/webhook.py:253-335`, `main.py:4281-4289`); the request model validates all three URL fields structurally and forbids unknown keys (`schemas.py:954-968`).

### 3.8 Diagnostics

* **`/health`** composes `AUTH.health_payload()` → `{"status":"ok","auth_mode":mode}` plus `storage` (cached probe), `logs` (activity row count, oldest row + age in days, DB size, `-wal` size, retention settings, file-log info) and `network` (full snapshot). Every block is individually `try/except`-wrapped, so `/health` never 5xxs because a probe failed (`main.py:1044-1058`). It does **not** verify DB writability, disk space thresholds, or scheduler liveness beyond the storage probe; `status` is always `"ok"`.
* **`GET /api/diagnostics/network`** returns the collector snapshot unchanged (`main.py:2220-2226`); the collector is pure `/proc`,`/sys`, `/etc/resolv.conf`, cgroup reads with heuristics and never raises (`network_diagnostics.py:239-322`).
* **`POST /api/diagnostics/speedtest`** accepts `{bytes, connections}`, defaults 100 MB/4 connections, enforces a **global** 60 s minimum interval keyed on a module dict (429 otherwise) — the timestamp is stored before payload validation, so an invalid request also consumes the window — then runs as a recorded task `speedtest`, persists a trimmed result to `last_speedtest_json` (never read back), and maps "must be between"/"URL rejected" errors to HTTP 400 (`main.py:2229-2275`). Speed measurement is decimal MB/s over wall time per connection plus aggregate; Cloudflare's 90 MB per-request cap is handled by sequential rounds with `capped` reporting (`network_diagnostics.py:368-479`).

**`/health` payload schema (as consumed by Docker/monitoring/tests).**

```
{
  "status": "ok",                       # constant; never "degraded"
  "auth_mode": "none|basic|oidc",
  "storage": {                          # from storage_health.build_storage_health
    "checked_at": "<ISO Z>", "ok": bool,
    "paths": {"downloads": <probe>, "staging": <probe>},
    "downloads": <probe>, "staging": <probe>       # duplicated convenience keys
  },                                    # probe = {path, exists, is_dir, writable, uid, gid, errno, error, free_bytes}
  "logs": {
    "activity_rows": int, "oldest_created_at": str, "oldest_age_days": float|null,
    "db_size_bytes": int, "wal_size_bytes": int,
    "retention_days": str, "max_rows": str,
    "file_log": {"enabled": bool, "path": str, "size_bytes": int, "max_mb": str, "backups": str}
  },
  "network": { "checked_at", "container", "hostname", "uptime", "interfaces", "mtu",
               "sysctls", "cpu", "dns", "tcp", "route", "app", "warnings" }
}
```

Any block may instead be `{"error": "<Type>: <msg>"}` when its probe raised; the HTTP status stays 200 (`main.py:1044-1058`).

### 3.9 Maintenance & debug tooling

Two tiers, both behind normal auth/CSRF:

* **Safe** — `POST /api/maintenance/clear` with `clear-cover-cache` (deletes cached cover files under `DATA_DIR/covers`, `covers.py:109-121`), `clear-discovery` (deletes `status='DISCOVERY'` release rows whose series is neither tracked nor blacklisted, `database.py:3577-3599`), `clear-descriptions` (blanks `releases.description` and `series_tracking.description`, `database.py:3554-3575`). Each returns `{status,message,action,count}` and writes one `MAINTENANCE_CLEARED` row including actor/action/count (`maintenance.py:74-101`).
* **Destructive/rare** — `POST /api/debug/action` behind `MANGATRAX_DEBUG_ACTIONS=1`: `clear-discovery`, `clear-descriptions`, `clear-thumbnails` (same effect as clear-cover-cache), `normalize-english-titles` (now the re-key repair), `purge-non-manga`, `reset-all-data`. Responses are `{status,message}` and unknown actions (unreachable via the enum) return 400 (`main.py:4342-4388`).
* **Audit/repair pairs** return `{...result, task_id, task}`: suspicious volume ranges (`GET` audit + `POST` repair ≤200 ids), series attribution (`GET` audit + `POST` repair ≤200 ids), false fuzzy series matches (`POST` audit + `POST` repair requiring `confirm:true`, ≤500 ids), light novels (`GET` audit + `POST` cleanup requiring `confirm:true`, `action ∈ {ignore,delete}`, ≤500 ids), release cache (`GET` preview + `POST` clear requiring `confirm:true`), alias overrides (`GET` + `DELETE`), provider tags (`POST` async cancellable task). Note that the two `GET` audits mutate the DB (task rows + possible audit rows) and block until the whole scan completes.
* **`/api/maintenance/refresh-tags`** is the only *cancel-capable* async maintenance action and returns `{status:"started", task_id, task}` immediately.

### 3.10 Deployment configuration

`Dockerfile` sets `PORT=8080`, `CALIBRE_DIR=/calibre`, `DOWNLOADS_DIR=/downloads`, `STAGING_DIR=/staging`, `DATA_DIR=/app/data`, creates uid/gid 1000 `appuser`, chmod-777s `/app/data /calibre /staging /downloads`, exposes 8080, healthchecks `/health`, entrypoint `entrypoint.sh`, CMD `uvicorn app.main:app --host 0.0.0.0 --port 8080`. `entrypoint.sh` honours `PUID`/`PGID`, remaps the user, conditionally chowns, and `exec gosu appuser`. Both compose files mount `/calibre:ro`, `/staging`, `/downloads`, `./data`, drop all capabilities, set `no-new-privileges`, cap Docker logs, and default `MANGATRAX_AUTH_MODE=none`.

`main.py:76-86` establishes: `DATA_DIR` (default `backend/../data`), `DB_PATH = DATA_DIR/mangatrax.db`, `CALIBRE_DIR` (default `/calibre`), and the module singletons `db`, `cover_cache` (`DATA_DIR/covers`), `mangaupdates`, `scheduler`. The `__main__` block reads `PORT` (`main.py:4390-4393`) but the container runs uvicorn directly.

### 3.11 Endpoint contracts in the OPS domain

"Auth" is the middleware gate from §3.2: in `none` mode every endpoint is open; in `basic`/`oidc` every row below is gated except `/health`, `/auth/*` and static assets. Error bodies are `{"detail": …}` unless noted.

| Method & path | Auth | Request | Response | Side effects / notes |
|---|---|---|---|---|
| `GET /health` | exempt | — | `{status, auth_mode, storage, logs, network}` | read-only, but opens several SQLite connections; always 200 |
| `GET /auth/login` | exempt | `next` query | 302 to authorization URL + state cookie; 302 `/` in non-oidc mode; 401 Basic challenge (break-glass) or 503 when the IdP is unreachable | writes `mangatrax_oidc_state` cookie |
| `GET /auth/callback` | exempt | `code`, `state`, `error` | 302 to `next`/`/` + session cookie; 302 `/auth/login?error=oidc_denied|oidc_failed` on failure | JIT upsert of `users`; deletes state cookie |
| `GET /auth/logout` | exempt | — | 302 (`/` or IdP end-session) | deletes session cookie |
| `GET /settings` | gated | — | HTML form | reads many tables + storage probe + network snapshot; renders `qbit_password` |
| `GET /logs` | gated | — | HTML list of 150 rows | read-only |
| `GET /api/settings` | gated | — | `{status, settings}` | Torbox key masked, other secrets cleartext |
| `POST /api/settings` | gated | `SettingsUpdateRequest` (45 keys, extra forbidden) | `{status:"success", storage}` | writes all provided keys; audits `SETTINGS_UPDATED`; may repoint seeded feed, reconfigure logging, probe storage, run a routing pass; 400 on validation |
| `POST /api/settings/test-webhook` | gated | `{discord_webhook_url?, generic_webhook_url?, apprise_url?}` | `{status, message, results:{discord?,generic?}}` | performs real outbound POSTs (10 s timeout); 400 on invalid/SSRF URL |
| `POST /api/logs/prune` | gated | — (no body) | `{status, deleted_old, deleted_excess, remaining, checkpointed, task_id, task}` | deletes activity rows, WAL truncate checkpoint, creates a task row |
| `GET /api/tasks` | gated | `active` 0/1, `kind`, `limit` 1–200 | `{status, active_count, tasks[]}` | read-only; doubles the query for `active_count` |
| `GET /api/tasks/{id}` | gated | path int | `{status, task}` or 404 | read-only |
| `POST /api/tasks/{id}/cancel` | gated | — | `{status, task_id, cancelling:true}`, 404 unknown, 409 not cancellable | in-memory cancel event only |
| `GET /api/diagnostics/network` | gated | — | full network snapshot | read-only `/proc`,`/sys` |
| `POST /api/diagnostics/speedtest` | gated | `{bytes?, connections?}` | speed result `{status:"success"|"error", …}` | real network I/O to a fixed Cloudflare URL; 429 within 60 s globally; 400 for out-of-range; creates a task; persists `last_speedtest_json` |
| `POST /api/maintenance/refresh-tags` | gated | — | `{status:"started", task_id, task}` | async `tags:refresh` background task (cancellable) |
| `GET /api/maintenance/suspicious-volume-ranges` | gated | — | `{status, summary, files[], task_id, task}` | runs a full release scan **inside the request**; creates a task row (`audit:volume-ranges`) |
| `POST /api/maintenance/repair-volume-ranges` | gated | `{release_ids:[int]}` (≤200) | `{status, repaired, skipped, task_id, task}` | rewrites `volumes_json`, `volume_range`, `is_bundle`, sets `IGNORED`; audits `CHAPTER_RANGE_REPAIRED`; 400 on non-int ids |
| `GET /api/maintenance/light-novels/audit` | gated | — | LN audit payload | read-only |
| `POST /api/maintenance/light-novels/cleanup` | gated | `{confirm:true, release_ids:[int]≤500, action:"ignore"|"delete"}` | `{...result, task_id, task}` | mutates/deletes rows only; 400 without `confirm`/valid action/ids |
| `POST /api/maintenance/false-series/audit` | gated | — | `{status, summary, files[]}` | read-only; loads the Calibre snapshot first |
| `POST /api/maintenance/false-series/repair` | gated | `{confirm:true, release_ids:[int]≤500}` | `{...result, task_id, task}` | re-keys selected rows; 400 without `confirm`/ids |
| `GET /api/maintenance/series-aliases` | gated | — | `{status, aliases[]}` | read-only |
| `DELETE /api/maintenance/series-aliases/{id}` | gated | path int | `{status, deleted:1}` or 404 | deletes override; invalidates grouped cache |
| `GET /api/maintenance/releases-cache` | gated | `series`, `statuses`, `exclude_statuses` (CSV) | `{status, count}` | read-only; always excludes active rows |
| `POST /api/maintenance/clear-releases` | gated | `{confirm:true, series_name?, statuses?, exclude_statuses?}` | `{status:"success", deleted, skipped_active, …, task_id, task}` | deletes release rows except active; audits `RELEASES_CLEARED`; 400 without `confirm` |
| `GET /api/maintenance/series-attribution` | gated | — | `{status, summary, files[], task_id, task}` | full scan inside the request; creates a task row |
| `POST /api/maintenance/series-attribution-repair` | gated | `{release_ids:[int]≤200}` | `{status, repaired, skipped, results[], task_id, task}` | re-keys `series_name`; audits `SERIES_ATTRIBUTION_REPAIRED` |
| `POST /api/maintenance/clear` | gated | `{action: Literal[clear-cover-cache, clear-discovery, clear-descriptions]}` | `{status, message, action, count}` | deletes cover files / discovery rows / blanks descriptions; audits `MAINTENANCE_CLEARED`; 400 on any other key/action |
| `POST /api/debug/action` | gated + flag | `{action: 6-value Literal}` | `{status, message}` | destructive; 403 unless `MANGATRAX_DEBUG_ACTIONS=1`; 400 on unknown action |
| `GET /api/calibre/test` | gated | `path` query (required) | library summary or 403 | read-only; path contained to configured roots |
| `GET /api/calibre/status` | gated | — | `{in_progress, initial_sync_done, last_sync}` | read-only, non-blocking |
| `POST /api/calibre/sync` | gated | — | sync result + `task_id` + `task` | **synchronous** (waits for the scan); shares the sync lock; audits `CALIBRE_SYNC` |
| `POST /api/downloader/configure` | gated | `DownloaderConfigureRequest` | `{status, message, connection}` | writes `qbit_*` directly (no path/URL validators from `SettingsUpdateRequest`); audits `DOWNLOADER_CONFIGURED`; tests connection |
| `POST /api/downloader/test` | gated | optional qbit credentials | `{success, message, version}` | outbound connection attempt (transient or saved creds) |
| `POST /api/downloader/torbox/test` | gated | optional `torbox_api_key` | `{success, …}` | mask/blank ⇒ saved key |
| `POST /api/backfill/start` | gated | `{max_pages?, delay?, start_page?}` (`max_pages` 0–5000, `delay` 0–300, `start_page` ≥1) | `{status:"started"|"in_progress", message}` | starts a daemon thread; persists `backfill_status/progress` |
| `POST /api/backfill/stop` | gated | — | `{status:"stopping"}` | sets the stop flag |
| `GET /api/backfill/status` | gated | — | `{status, progress, …}` | reads persisted backfill settings |

### 3.12 Failure modes, observability and operator behavior

| Failure | Detected by | Current behaviour | Operator-visible signal |
|---|---|---|---|
| Data dir not writable | `get_connection` `OperationalError` (`database.py:377-395`) | Re-raises with the DB dir mode/uid/gid and the running uid/gid plus a "chmod/chown or PUID/PGID" hint | exception traceback in container log; app does not start |
| Storage mount unwritable | `storage_health.probe_path` at startup/save/`/health` | Warning log per path with errno/uid; `/settings` inline warning; save toast; `/health.storage.ok=false` | `logger "mangatrax.storage"` warning, UI warning card, JSON |
| Calibre path missing | `sync_calibre_library` scanner `is_available()` | Returns `{success:false, message:"Calibre library not accessible at: …"}`; loop retries at the interval | `/api/calibre/status` unchanged; `CALIBRE_SYNC_ERROR` only on exceptions (not on unavailable) |
| Feed provider down | provider errors inside the poll | Per-feed `last_status/last_error` updated; scan result includes `feeds_failed`/`errors[]`; automatic scans notify only when there was activity | Feeds page, task result, `/api/feeds/check/status` |
| qBittorrent down | `_build_downloader_status` with 1.5 s timeout | `status:"degraded"`, empty torrent list, `message` explains; dashboard still renders | UI shows degraded status; never a 500 |
| Torbox down / rate-limited | downloader exceptions typed in `downloader/base.py` | Release marked `FAILED` with `download_error`, retry counter incremented, optional remote delete | Downloads queue row + activity log |
| Settings saved with an unusable interval | `float()` in worker tick | `ValueError` logged each tick; that tick's remaining jobs are skipped | repeating `Error in background worker loop` lines |
| Disk full | `storage_health` ENOSPC/EDQUOT, routing failures | Warning with free GiB; moves fail and are logged with the storage hint | `/health.storage.error`, UI warning |
| Activity log growth | scheduled prune + `/health.logs` (rows/oldest/DB/WAL) | Prunes by retention and row cap, truncates WAL | Prune audit row + `/health` numbers |
| Background task failure | `_run_task` except branch | Row `failed` + `error` text, audit `BACKGROUND_TASK_FINISHED`, toast in task panel | Task panel error text |
| DB locked > 10 s | `sqlite3` busy timeout | `OperationalError` propagates to the caller; route → 500, worker → logged and retried next tick | exception log |
| Auth misconfiguration | `AuthConfig.validate()` at startup | Critical log + exit 1 (never serve open) | container exits, `describe()` line lists what is unset |
| OIDC provider down | `/auth/login` `AuthProviderUnavailable` | 503, or 401 Basic challenge when break-glass is configured; callback failures redirect to `?error=oidc_failed` | server log `Could not start OIDC login` |

---

## 4. Data model

### 4.1 Tables (13) and key columns

All tables are created in `init_db()` with `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info`-guarded `ALTER TABLE` upgrades (`database.py:405-997`). No `FOREIGN KEY` constraints are declared; referential integrity is application-level.

1. **`series_tracking`** — `id PK`, `calibre_series_id INTEGER`, `name TEXT UNIQUE NOT NULL` (the **stable matching key**), `authors`, `publisher`, `tags`, `description`, `book_count INTEGER`, `latest_index REAL`, `is_monitored INTEGER 1`, `website_url TEXT` (http(s)-only), `owned_volumes_json '[]'`, `last_sync`, `tags_json '[]'`, `tags_text ''`. Invariant: `name` is never rewritten to an AniList title; display titles are resolved at render (`database.py:159-357`).
2. **`series_blacklist`** — `id`, `name UNIQUE`, `reason`, `created_at`.
3. **`releases`** — `id`, `guid UNIQUE NOT NULL`, `title`, `series_name` (stable key), `release_group`, `release_groups_json`, `description`, `mangaupdates_url`, `is_bundle`, `volume_start`, `volume_end`, `volumes_json`, `missing_vols_json`, `download_url`, `link`, `cover_url`, `source_feed_name`, `status` (`REQUESTED|DISCOVERY|IN_STAGING|IN_CALIBRE|IGNORED|BLACKLISTED`, default `REQUESTED`), `download_status` (`IDLE|QUEUED|DOWNLOADING|PENDING_DEDUPE|COMPLETED|FAILED`), `torrent_hash`, `media_type` (`manga|light_novel|chapter_only|novel|audiobook|jpegxl|unknown`), `revision`, `publisher`, `inferred_volumes`, `created_at`, `updated_at`, plus upgrade columns `is_complete`, `downloader`, `downloader_id`, `downloader_started_at`, `download_wanted_json`, `download_error`, `download_attempts`, `torbox_progress_json`, `published_at`, `is_light_novel`, `download_files_json`, `downloaded_at`, `routed_at`, `tags_json`, `tags_text`. Invariants: `guid` unique (dedupe); status transitions are owned by `resolve_status()` precedence (`enforce_precedence=True` for automated writes, e.g. `scheduler.py:3160-3162`); `media_type` typing never writes `status` and never deletes.
4. **`prioritized_groups`** — `id`, `name UNIQUE`, `notes`, `priority INTEGER 1`, `created_at`; seeded with 4 groups when empty (`database.py:637-646`).
5. **`feeds`** — `id`, `name`, `feed_type`, `url`, `config_json`, `enabled 1`, `auto_download 1`, `check_interval_minutes 60`, `last_checked`, `last_status`, `last_error`; seeded with one Nyaa RSS feed when empty; the mirror migration only rewrites the exact seeded row (`database.py:19-22,852-857`).
6. **`activity_logs`** — `id`, `event_type NOT NULL`, `message NOT NULL`, `details`, `actor` (default `system`, upgraded column, NULL/blank coerced to `system` at startup), `created_at`. Indexed on `created_at`.
7. **`users`** — `id`, `subject UNIQUE NOT NULL`, `username`, `email`, `display_name`, `auth_source`, `first_seen`, `last_seen`; `idx_users_subject`. Written by JIT provisioning only.
8. **`app_settings`** — `key PK`, `value NOT NULL`; the single source of runtime configuration (see §3.1 and §5).
9. **`manga_metadata_cache`** — `search_term PK`, `canonical_title NOT NULL`, `romaji_title`, `cover_url`, `total_volumes`, `status`, `synopsis`, `source` (default `anilist`), `created_at`, plus `genres_json`, `tags_json`, `tags_updated_at`. Only rows with `source == authoritative` and a non-empty `canonical_title` may drive display titles (`database.py:331-357`).
10. **`series_alias_overrides`** — `id`, `source_norm UNIQUE NOT NULL`, `source_series`, `target_name`, `target_calibre_series_id`, `created_at`, `actor`; upsert keyed on normalized source (`database.py:1761-1787`).
11. **`background_tasks`** — `id`, `kind`, `title`, `scope`, `status` (`running|success|failed|cancelled`), `progress_pct REAL`, `message`, `items_done`, `items_total`, `started_at`, `finished_at`, `actor`, `error`, `result_json`, `updated_at`; index `(status, started_at)`. Invariant: a row is created before work starts, so reload reattachment is possible.
12. **`torbox_manual_pickups`** — `id`, `release_id`, `torrent_id`, `title`, `series_name`, `files_json`, `wanted_json`, `reason`, `status` (`pending|fetching|partial|…`), `error`, `created_at`, `expires_at`, `updated_at`; index `(status, created_at)`.
13. **`download_failures`** — `id`, `file_relative UNIQUE NOT NULL`, `reason`, `detected_type`, `release_id`, `series_name`, `volumes_json`, `attempts`, `status`, `created_at`, `updated_at`; index `(status, created_at)`.

Indexes: `releases(series_name)`, `releases(lower(series_name))`, `releases(status)`, `releases(updated_at)`, `releases(release_group)`, `releases(volume_start)`, `releases(media_type)`, `activity_logs(created_at)`, `series_tracking(name)`, `series_tracking(is_monitored)`, `manga_metadata_cache(search_term)` and the three table-specific indexes above (`database.py:859-870,883-886,907-910,956-959,976-979`).

**Application-level relationships (no FK constraints exist).** `releases.series_name` is the join key to `series_tracking.name` and `series_blacklist.name`, compared case-insensitively after trimming (`lower(trim(...))`, e.g. `database.py:1171,3591-3592`) — not by id, so renaming a tracked series does not update releases (the repair paths handle that explicitly). `releases.source_feed_name` matches `feeds.name` (a display name, not a stable id). `torbox_manual_pickups.release_id` and `download_failures.release_id` reference `releases.id` with no cascade: deleting a release orphans pickups/failures. `series_alias_overrides.source_norm` is the normalized source series, `target_name` is compared against tracked names. `users.subject` is the OIDC `sub` or `basic:<user>`, referenced by nothing but the audit label. `activity_logs` has no foreign key to anything — its `details` often embed ids as free text (e.g. `MAINTENANCE_CLEARED` writes `action=…; count=…`).

### 4.2 Migrations

There is no migration framework or version table. Upgrade logic is: create-if-not-exists, then `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` per column, then idempotent in-place data fixes (all inside `init_db`'s single transaction, `database.py:405-997`), followed by two one-shot post-transaction passes (`_run_media_filter_once`, `_run_jpegxl_filter_once`, `database.py:999-1003`) gated by `app_settings` markers. Some backfills run unconditionally on every startup inside the transaction (e.g. re-deriving `release_group` for every release row by re-parsing titles, `database.py:603-624`; `is_complete` backfill `database.py:548-556`; description sanitisation `database.py:565-575`) — these are O(releases) work on every boot.

### 4.3 WAL / transaction / concurrency behavior

* Every operation opens a **new** connection (`database.py:377-403`) with `timeout=10.0`, `journal_mode=WAL` (fallback `DELETE`), `foreign_keys=ON` (declared but unenforced since no FKs exist), `row_factory=sqlite3.Row`.
* Writes use `with self.get_connection() as conn:` + explicit `conn.commit()`; sqlite3's context manager commits on success/rolls back on exception, so multi-statement writes are atomic per method. Cross-method operations are **not** transactional (e.g. `clear_cached_releases` counts then deletes; `_recorded_task` writes a task row, then work, then a finish row).
* Long-running bulk writes exist on the startup path (`init_db` full-table re-parse) and in `clear_cached_releases`, which can hold the write lock for a while under WAL.
* `PRAGMA wal_checkpoint(TRUNCATE)` is executed only at the end of activity pruning (`database.py:1281-1287`) — i.e. at startup, at most every 6 h, and on manual prune. WAL therefore grows between prunes; `/health` reports its size.
* No `VACUUM`, no `ANALYZE`, no backup/`sqlite3 .backup` API call exists.

### 4.4 State machines and invariants owned by the platform layer

**Release status (the precedence model).** `STICKY_STATUSES = {REQUESTED, IN_STAGING, IGNORED, BLACKLISTED, IN_CALIBRE}`, `RECOMPUTABLE_STATUSES = {DISCOVERY, NEW}`, `USER_VETO_STATUSES = {IGNORED, BLACKLISTED}`; ranks `DISCOVERY/NEW = 0`, `REQUESTED = IN_CALIBRE = 1`, `IN_STAGING = 1.5`, `IGNORED/BLACKLISTED = 2` (`database.py:222-261`). `resolve_status(existing, incoming)` (`database.py:272-320`) is the single owner of **automated** writes: blank incoming never clears, unset adopts, the veto tier is immutable to automation, otherwise the higher rank wins. Automated callers pass `enforce_precedence=True` (`database.py:2573-2596`); explicit user endpoints deliberately bypass it so a human can always override (`database.py:243-246`). Invariants: a feed poll can never resurrect an IGNORED/BLACKLISTED release; a sticky status is never downgraded to DISCOVERY/NEW; a user can always lift a veto.

**Download status.** Values observed: `IDLE` (default), `DOWNLOADING`, `PENDING_DEDUPE`, `COMPLETED`, `FAILED`; `QUEUED` is tolerated on read but never written by current code (OPS-097). `PENDING_DEDUPE` exists only so the 60 s dedupe sweep can retry a suppressed auto-download once the in-flight reservation clears (`scheduler.py:2329-2345`). `download_attempts`/`download_error` accompany FAILED; `downloaded_at`/`routed_at`/`download_files_json` record the local-fetch and routing milestones.

**Background task.** `running` (created before submit) → `success` (progress forced to 100, `result_json` stored) | `failed` (`error` text) | `cancelled` (only reachable via `TaskCancelled`). `finished_at`/`updated_at` set on every terminal transition; a `running` row can survive a process crash forever (nothing reconciles orphaned `running` rows on startup — see R-6).

**Backfill.** `idle → running → idle | error` with `backfill_progress` free text, persisted in `app_settings`; `/stop` writes `stopping` (`scheduler.py:3855-3856`) and the worker continues to the end of the current item before writing `idle` with a stop reason (`scheduler.py:4818-4823`). There is no `stopping → idle` guarantee if the process dies.

**Manual pickup.** `pending` / `fetching` / `partial` are "active" for the cap count (`database.py:1505-1511`), `expires_at` drives the 300 s sweep, and `error` carries the last mapping failure. `expires_at IS NULL` means never expire.

**Feed health.** `feeds.last_status` is only ever `OK` or `ERROR` (plus `last_error` text) (`scheduler.py:3468,3474,3782,3788`); `last_checked` is UTC.

**Media typing.** `media_type` starts `unknown` and may be set to `light_novel`/`chapter_only`/`jpegxl` by the one-shot pass or at ingestion; `manga` may be set when a title is recognised. Invariants: typing never writes `status`, never deletes, and only `unknown` rows are (re)typed (`database.py:49-85,1005-1103`). The tracked-view predicate is `COALESCE(media_type,'unknown') NOT IN (non-manga types)` (`database.py:107-118`), i.e. "inconclusive means visible".

**Settings lifecycle.** `get_setting` has no defaults table at read time; the defaults only exist because `init_db` seeds them on a fresh DB (§3.1). Consequently a key deleted from `app_settings` reverts to each caller's hard-coded default (which may differ from the documented seed), and there is no way to distinguish "unset" from "empty string" through the public API.

---

## 5. Configuration reference

### 5.1 Environment variables (read at process start unless noted)

| Variable | Default | Read at | Effect |
|---|---|---|---|
| `PORT` | `8080` | `main.py:4392` (`__main__` only) | uvicorn port when run as a script; container CMD hard-codes 8080 |
| `DATA_DIR` | `backend/../data` | `main.py:77-79` | Root for `mangatrax.db`, `covers/`, `logs/` |
| `CALIBRE_DIR` | `/calibre` | `main.py:80`, `database.py:729` | Fallback Calibre root + allowed probe root + default setting seed |
| `STAGING_DIR` | `/staging` | `database.py:736` | Default `staging_dir` (fresh DB only) |
| `DOWNLOADS_DIR` | `/downloads` | `database.py:739,831` | Default `downloads_dir` (fresh DB, and `INSERT OR IGNORE` on upgrade) |
| `QBIT_URL` / `QBIT_USER` / `QBIT_PASS` | `http://localhost:8080` / `admin` / `adminadmin` | `database.py:730-732` | qBittorrent seeds, **fresh DB only** (no `INSERT OR IGNORE` for these) |
| `TZ` | — | `timezone_util.py:14` | Fallback timezone when `app_timezone` is empty (before the hard-coded `Europe/Copenhagen`) |
| `PUID` / `PGID` | `1000` | `entrypoint.sh:5-6` | Runtime uid/gid remap |
| `MANGATRAX_AUTH_MODE` | `none` | `auth.py:341` | `none`/`basic`/`oidc` |
| `MANGATRAX_PUBLIC_URL` | `""` | `auth.py:342` | redirect_uri base + cookie `Secure` + CSRF origin; required for `oidc` |
| `MANGATRAX_SESSION_SECRET` | `""` | `auth.py:343` | HMAC key for session/state cookies; required (≥16 chars) for `oidc` |
| `MANGATRAX_BASIC_USER` | `admin` | `auth.py:344` | Basic username |
| `MANGATRAX_BASIC_PASS` | `""` | `auth.py:345` | Basic password; also break-glass credential in `oidc` mode |
| `MANGATRAX_OIDC_ISSUER` | `""` | `auth.py:346` | Discovery base (`<issuer>/.well-known/openid-configuration`) |
| `MANGATRAX_OIDC_CLIENT_ID` | `""` | `auth.py:347` | OIDC client id / token audience |
| `MANGATRAX_OIDC_CLIENT_SECRET` | `""` | `auth.py:348` | Token-endpoint client secret |
| `MANGATRAX_SESSION_MAX_AGE` | `604800` | `auth.py:334-338` | Session lifetime, clamped 300…2 592 000 s |
| `MANGATRAX_CSRF_PROTECT` | `1` | `auth.py:350` | Enables the Origin/Referer check (still requires auth enabled + public origin) |
| `MANGATRAX_DEBUG_ACTIONS` | `0` | `auth.py:351` | Enables `/api/debug/action` and the Settings "Advanced / Dangerous" card |
| `MANGATRAX_PATH_ROOTS` | `""` | `main.py:336` (per request) | `os.pathsep` list restricting `calibre_path`/`staging_dir`/`downloads_dir` |
| `MANGATRAX_RECONCILE_BATCH_SIZE` | `2000` (min 50) | `scheduler.py:116` | Per-pass Calibre reconcile window |
| `MANGATRAX_DISCARD_UNMARKED_CHAPTERS` | `true` | `database.py:748,792` | Seed for `discard_unmarked_chapters` |
| `MANGATRAX_FILTER_NON_MANGA` | `true` | `database.py:796` | Seed for `filter_non_manga` |
| `MANGATRAX_INFER_COLLECTED_RELEASES` | `true` | `database.py:751,800` | Seed for `infer_collected_releases` |
| `MANGATRAX_NYAA_BASE_URL` | `https://nyaa.si` | `database.py:759,804` | Seed for `nyaa_base_url` |
| `MANGATRAX_DOWNLOADER_ENGINE` | `qbit` | `database.py:761,807` | Seed for `downloader_engine` |
| `MANGATRAX_TORBOX_ENABLED` | `false` | `database.py:762,808` | Seed |
| `MANGATRAX_TORBOX_API_KEY` | `""` | `database.py:763,809` | Seed (secret) |
| `MANGATRAX_TORBOX_DELETE_AFTER_DOWNLOAD` | `true` | `database.py:764-766,810` | Seed |
| `MANGATRAX_TORBOX_POLL_SECONDS` | `5` | `database.py:767,811` | Seed |
| `MANGATRAX_TORBOX_MAX_CONCURRENT` | `2` | `database.py:768,812` | Seed |
| `MANGATRAX_TORBOX_MAX_DOWNLOAD_GB` | `64` | `database.py:769,813` | Seed |
| `MANGATRAX_TORBOX_DOWNLOAD_CONCURRENCY` | `4` | `database.py:770,814` | Seed |
| `MANGATRAX_TORBOX_PARALLEL_CHUNKS` | `4` | `database.py:771,815` | Seed |
| `MANGATRAX_TORBOX_TIMEOUT_HOURS` | `6` | `database.py:778,822` | Seed |
| `MANGATRAX_TORBOX_STRICT_VOLUMES` | `true` | `database.py:779,823` | Seed |
| `MANGATRAX_TORBOX_DELETE_ON_FAILURE` | `true` | `database.py:777,821` | Seed |
| `MANGATRAX_LOG_TO_FILE` | `false` | `database.py:772,816` | Seed |
| `MANGATRAX_LOG_FILE_MAX_MB` | `10` | `database.py:773,817` | Seed |
| `MANGATRAX_LOG_FILE_BACKUPS` | `5` | `database.py:774,818` | Seed |
| `MANGATRAX_ACTIVITY_RETENTION_DAYS` | `90` | `database.py:775,819` | Seed |
| `MANGATRAX_ACTIVITY_MAX_ROWS` | `50000` | `database.py:776,820` | Seed |

Note: because env values are **seeds**, later env changes do not affect an existing database except for the `INSERT OR IGNORE` subset (which also only applies while the key is absent). All auth/debug/path-roots/CSRF/reconcile values are read from env on every process start and are not settings-page editable.

### 5.2 Settings keys

See the exhaustive table in §3.1 (45 writable keys + 10 internal/derived keys). Nothing else is writable: `POST /api/settings` returns 400 for unknown keys (`schemas.py:646`, `test_security_remediation.py:478,522-524`).

---

## 6. Integration contracts

**Platform → all domains (settings service).** The only supported read API is `db.get_setting(key, default)` / `db.get_all_settings()`; unknown keys return the caller's default. Keys have no schema-level types, so each consumer owns coercion (see the truthiness list in §3.1). Tests can and do monkey-patch `main.scheduler`/`main.db` (`tests/e2e/harness.py:338-380`), so routes must tolerate replaced globals.

**Platform → Releases/Series/Downloads (auth context & actor).** `auth.get_current_actor()` is valid only inside a request or a thread that explicitly set/reset it; background threads get `"system"` and should pass `actor="scheduler"`. All route handlers that write audit rows either rely on the ContextVar (implicit) or pass `auth_module.get_current_actor()` into a worker so attribution survives `asyncio.to_thread` (`main.py:1924, 2466, 2492-2496`).

**Platform → UI (task kinds & task hooks).** UI buttons advertise `data-task-kind` (+ optional `data-task-scope`); `MangaTasks.reattach()` disables a button while an active task with the same kind/scope exists and restores it afterwards (`base.html:1074-1099`). Kinds therefore form a contract: changing a kind string silently breaks reattachment. The panel polls `/api/tasks?active=0&limit=20` every 1.5 s and shows a toast on running→finished transitions (`base.html:1061-1072,1044-1059`).

**Platform → UI (settings reads).** Templates read `settings.<key>` directly from `get_all_settings()`; missing keys render as empty (Jinja undefined), which is why several UI fields carry `or 'default'` fallbacks (`settings.html:126,131,345,350,357,376`). `settings.torbox_api_key` is pre-masked by the view; `settings.qbit_password` is not.

**Platform → UI (health/diagnostics shapes).** `/health` JSON keys (`status`, `auth_mode`, `storage.paths.{downloads,staging}`, `logs.*`, `network.*`) are consumed by Docker (status code only), `test_storage_health.py:173-208`, `test_log_rotation.py:225-260`, `test_network_diagnostics.py:182-212`. Settings renders `network_diagnostics` from the view context, not from the API.

**Security helpers as a stable contract.** `security.validate_public_url`, `validate_outbound_url`, `validate_directory_setting`, `ensure_path_within_roots`, `is_safe_href`, `safe_url_filter`, `escape_like`, `like_contains` are imported by providers, notifier, covers, schemas, database and main; changing their semantics changes SSRF/path/HTML behaviour everywhere. Tests patch `security.resolve_hostname_addresses` to avoid DNS (`test_security_remediation.py:118-166`).

**Maintenance contract.** Audit endpoints return `{status, summary:{scanned,repairable,...}, files:[...], task_id, task}`; repair endpoints accept explicit id lists (never "all") and return `{status, repaired, skipped, task_id, task}`. Clients must send `confirm:true` for light-novel cleanup, false-series repair and release-cache clear.

**Persistence contract.** `releases.series_name` is the stable matching key used by Releases/Series/Downloads; `IN_CALIBRE`/`IN_STAGING` transitions are owned by the precedence model; `media_type` typing is additive only. Any platform code that rewrites `series_name` must go through the audited repair paths (`repair_series_attribution`, `repair_rekeyed_series_names`).

### 6.1 Task-kind ↔ UI control map

Buttons that expose `data-task-kind` (the reattachment key) and the endpoint that creates the task:

| `data-task-kind` | UI location | Endpoint | Cancellable |
|---|---|---|---|
| `tags:refresh` | Settings → Provider tags (`settings.html:688`) | `POST /api/maintenance/refresh-tags` | yes (the only one) |
| `audit:volume-ranges` | Settings → Suspicious volume ranges (`settings.html:701`) | `GET /api/maintenance/suspicious-volume-ranges` | no (blocking) |
| `repair:volume-ranges` | Settings → volume ranges (`settings.html:702`) | `POST /api/maintenance/repair-volume-ranges` | no |
| `audit:attribution` | Settings → Spinoff attribution (`settings.html:774`) | `GET /api/maintenance/series-attribution` | no (blocking) |
| `repair:attribution` | Settings → Spinoff attribution (`settings.html:777`) | `POST /api/maintenance/series-attribution-repair` | no |
| `audit:false-series` | Settings → False fuzzy matches (`settings.html:718`) | `POST /api/maintenance/false-series/audit` | no |
| `repair:false-series` | Settings → False fuzzy matches (`settings.html:719`) | `POST /api/maintenance/false-series/repair` | no |
| `audit:light-novels` | Settings → Light-novel releases (`settings.html:735`) | `GET /api/maintenance/light-novels/audit` | no |
| `logs:prune` | Settings → Logs & rotation (`settings.html:565`) | `POST /api/logs/prune` | no |
| `speedtest` | Settings → Network diagnostics (`settings.html:616`) | `POST /api/diagnostics/speedtest` | no |
| `audit:comicinfo`, `repair:comicinfo`, `downloads:process`, `downloads:prune`, `downloads:flatten` | Downloads page (owned by another domain, listed for completeness) | tagger/downloads endpoints | no |
| `torbox:pickup:fetch`, `torbox:pickup:retry` | Downloads → Manual Pickup (`manual_pickup.html:40-56`) | pickup endpoints | no |

Note the coupling: the light-novel audit has a `data-task-kind` on its button but the endpoint does **not** create a background task (`main.py:2471-2474`), so the button is greyed out only while some *other* `audit:light-novels` task happens to be running — i.e. never. The false-series **audit** is likewise a direct call with no task row (`main.py:2508-2516`) even though its button advertises `audit:false-series`; only its repair goes through `_recorded_task` (blocking).

### 6.2 Existing test coverage map (for the redesign validation phase)

| OPS area | Suite that asserts it | What is actually asserted |
|---|---|---|
| 010–012, 016, 021 | `test_auth_modes.py:140-394` | config defaults/validation, policy decisions (redirect vs 401 vs 403), exemptions, break-glass, CSRF origin handling |
| 013–018 | `test_auth_modes.py:400-511`, `test_oidc_mock_idp.py:248-515` | PKCE/state/nonce, full login against a mock IdP, rejection paths, Authlib fail-closed |
| 019–020 | `test_audit_actor.py:47-268` | actor column migration, ContextVar attribution, users JIT upsert, scheduler actor, manual route attribution |
| 022 | none found (header middleware not covered by the E2E harness, see R-13) | — |
| 023–024 | none found | dead helpers/route are not asserted anywhere |
| 025–028 | `test_security_remediation.py:62-444` | SSRF scheme/host guards, outbound boundaries, cover redirect hops, stored-XSS, path traversal, LIKE escaping |
| 029, 030–033 | `test_maintenance_tools.py:95-395`, `test_security_remediation.py:509-520` | safe-action map, scoping, strict enum, debug gating, template placement, audit actor |
| 034–038 | `test_security_remediation.py:453-576` | unknown-key rejection, directory traversal rejection, `MANGATRAX_PATH_ROOTS` containment |
| 039 | `test_storage_health.py:46-330` | writable/missing/unwritable probes, hints, startup persist+warn, `/health` block, settings-save probe, routing-failure hint |
| 040 | `test_log_rotation.py:186-224` | log keys drive settings/docs; no assertion on the routing side effect |
| 048, 050–051 | `test_log_rotation.py:50-281` | retention math, single audit line, rotating handler, `/health` + prune endpoint |
| 052 | `test_audit_actor.py:269-...` | `/logs` renders the actor label |
| 053–058 | `test_background_tasks.py:35-157`, `test_task_progress_counters.py`, `test_task_progress_followups.py` | lifecycle, cancel, prune API, task ids, counter reporters, UI kind hooks |
| 059–060 | none found (the dead Cancel button and the never-called prune are not tested) | — |
| 061–075 | indirect: `test_startup_performance.py:158-254`, `test_feed_poll_async.py`, `test_mangatrax_workflow.py`; no per-interval test | worker retry/initial-sync contract, async feed poll, end-to-end flows |
| 076 | `test_provider_tags.py` | tag backfill + display |
| 079 | `test_false_fuzzy_series_matches.py` | audit/repair selection |
| 080 | `test_light_novel_release_filtering.py`, `test_different_series_ln_publisher.py` | LN detection/cleanup behaviour |
| 081 | `test_manual_series_match.py` | alias override create/use |
| 082 | `test_release_cache_clear.py` | preview/clear filters and active-row skip |
| 083–084 | `test_network_diagnostics.py:68-262` | collector parsing/warnings/caps, speedtest math, endpoints, template wiring |
| 085–086 | `test_webhook_scan_processed.py`, `test_feed_scan_activity_notifications.py` | notification payload/gating |
| 087–091 | `test_media_typing_migration.py`, `test_mangatrax_workflow.py` | non-destructive re-typing, schema/behaviour |
| 092–094 | none found (settings accessor cost, timezone fallback, backup) | — |

---

## 7. UI touchpoints & mobile behavior (summary)

**`/settings`** (`settings.html`, 9 sections + conditional Advanced):
* Sticky in-page nav (`#settings-section-nav`) that becomes a horizontally scrollable row below `lg` (`settings.html:21-51`); sticky "Unsaved changes" save bar appears bottom-right once dirty (`settings.html:860-865`) and submits via `form="settings-form"`.
* Calibre: path input + "Test Path" → `/api/calibre/test` (`settings.html:66-75,872-885`).
* Downloader: enable toggle, URL/user/password, category/tags, Test Connection badge (`settings.html:101-134,887-935`); engine select, Torbox enable/key/test, strict selection, manual pickup (enable/cap/expiry), delete-on-failure, concurrency/chunks/size/poll/max-concurrent/timeout (`settings.html:136-245`).
* Staging: downloads dir + staging dir inputs with inline storage-health warnings from the server probe (`settings.html:261-274`, `partials/storage_path_warning.html`); 6 behaviour checkboxes (auto-download, auto-tag, convert archives, discard marker-less chapters, hide non-manga, infer collected volumes) (`settings.html:276-324`).
* Schedules: Calibre interval, feed interval, backfill pages/delay, timezone (`settings.html:327-361`).
* Tracker: Nyaa base URL with normalisation help text (`settings.html:363-386`).
* Webhooks: Discord URL + Test, generic/Apprise URL + Test, two notification checkboxes, inline result box (`settings.html:388-443,940-1028`).
* Groups: list with release counts and delete, add form (`settings.html:445-493`).
* Maintenance (always visible, no debug flag): purge cover cache / clear unreviewed discovery / reset descriptions (`settings.html:495-556`); Logs & rotation card with prune button, file-log toggle/size/backups, activity retention/max rows, live file/DB/WAL figures (`settings.html:558-604`); Network diagnostics card with warnings, 11 facts, Copy JSON and Run speed test (`settings.html:606-641`); Release cache card (series select, status preset, preview count, clear) (`settings.html:643-679`); Provider tags refresh (`681-691`); Suspicious volume ranges audit/repair (`693-708`); False fuzzy series matches audit/repair (`710-725`); Light-novel audit/ignore/delete (`727-743`); Alias overrides list with Remove (`745-764`); Spinoff attribution table with checkboxes (`766-798`).
* Advanced/Dangerous (`debug_actions_enabled` only): purge non-manga, repair re-keyed names, danger-zone reset with two-step confirmation (`settings.html:801-845,1403-1470`).
* Mobile: all grids collapse to one column (`grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`), the section nav becomes a horizontal scroller that stays sticky under the top bar (210 px column only at `lg`, `settings.html:19-51`), and the save bar is `fixed bottom-5 right-5` so it stays reachable while scrolling (`settings.html:860`). The page is dense (1 693 lines of markup) and every audit renders an unvirtualised, horizontally scrolling result table (`settings.html:788-797`); form controls use `text-xs py-2` (≈32 px tall) while the mobile nav in `base.html` uses `min-h-[44px]` — the settings form is the least touch-friendly surface in the app. The global task panel polls every 1.5 s on mobile too (`base.html:1140`).
* Accessibility/markup notes relevant to a redesign: section links are `<a href="#…">` inside a `role="tablist"` but carry no `role="tab"`/`aria-selected`, the prune/speedtest/probe buttons are plain `<button>` with `aria-label` present only on some, the sticky save bar has no live region, and severity/status colours are chosen by Tailwind classes derived from substring matching rather than a structured level (`logs.html:37`, `settings.html:91,143`), so the same state is not colour-coded consistently across pages.

**`/logs`** (`logs.html`): header + client-side search box + "Prune now" (`logs.html:8-29`); server-renders 150 rows, each with event-type chip, actor chip, message, details, localised timestamp (`logs.html:32-60`); empty states for "no logs" and "no filter matches" (`64-69`); `pruneActivityLogs()` POSTs `/api/logs/prune`, toasts deleted/remaining and reloads (`74-83`); `filterLogs()` is a pure DOM substring filter and does not touch the server (`85-98`). No auto-refresh, pagination, column filtering, or CSV export; new events only appear after a manual reload.

**Known UI gaps (factual).** Cancel button in the global task panel never renders (OPS-059). The task panel has no "open full list" view beyond the newest 12. The Advanced card disappears silently when the env flag is off (no hint that destructive tooling exists). `/settings` embeds the qBittorrent password in the DOM. Audits on large libraries block the request until the full scan finishes, so the button stays disabled by `data-task-kind` only after the response returns.

---

## 8. Open questions / ambiguities & risks

Everything below is evidence-based and is **not** a specification claim.

**R-1 — Default deployment is fully open, and CSRF protection is inactive in that mode.** Both compose files ship `MANGATRAX_AUTH_MODE=none` (`docker-compose.yml:43`, `docker-compose.truenas.yml:55`); the middleware allows every request, and `_cross_origin_forbidden` short-circuits unless `auth_enabled` (`auth.py:1008-1009`). So the default install has no authentication, no CSRF check, and every admin endpoint (`/api/debug/action`, `/api/settings`, `/api/logs/prune`, maintenance clears) is reachable by anything that can reach the port. The README's security posture depends entirely on the operator following an optional hardening block.

**R-2 — `qbit_password` (and webhook URLs) are exposed in cleartext.** `GET /api/settings` masks only the Torbox key (`main.py:4211-4217`) and `/settings` renders `value="{{ settings.qbit_password }}"` (`settings.html:119`). This defeats the Torbox masking work for the other credential, is visible in browser history/DOM/devtools and to any LAN client in `none` mode, and makes the "secrets are masked" statement in `settings.html:167` only half true. Also asymmetric: an empty `qbit_password` submission clears it while an empty Torbox key is ignored.

**R-3 — `filter_non_manga` is effectively a dead after the one-shot pass.** Its only read is in `_run_media_filter_once` (`database.py:1039-1041`); the tracked views always apply `tracked_media_clause()` regardless of the setting (`database.py:107-118`). The UI label "Hide Non-Manga Types … (they are kept in the database)" (settings.html:310-314) therefore promises a runtime toggle that does not exist once `media_filter_applied` is set. Same class of issue: `last_speedtest_json` and `activity_last_prune_at` are written and never read (OPS-041), and `SETTINGS_KEYS` is a stale unused tuple (OPS-042).

**R-4 — Settings validation stops at string length for behaviour-critical keys.** `calibre_sync_interval_minutes`, `feed_check_interval_minutes`, `backfill_max_pages`, `backfill_delay_seconds`, `app_timezone`, `auto_download`, `auto_tag`, `discard_unmarked_chapters`, `filter_non_manga`, `infer_collected_releases`, `webhook_*` and the manual-pickup keys are `Optional[str]` with only `max_length` (`schemas.py:659-716`). Nothing prevents `calibre_sync_interval_minutes="abc"`, which raises `ValueError` on the first worker tick inside the single outer `try` of `_worker_loop` (`scheduler.py:5189-5191,5274-5277`), aborting that tick's entire job sequence and logging an error every 10 s until fixed. Similarly, `auto_tag="1"` silently disables tagging because the consumer compares `== "true"` (`scheduler.py:5191`) while `qbit_enabled="1"` works via `as_bool` — inconsistent truthiness across keys.

**R-5 — Cancellation is mostly cosmetic/unreachable.** Only `tags:refresh` is created with `cancellable=True` (`main.py:1555-1561`); `serialize_task` never emits `cancellable`, so the panel's Cancel button (`base.html:1039`) is dead code (OPS-059). The in-memory `_CANCEL_FLAGS` map means cancellation cannot survive a restart (409), and a cancelled tag refresh is recorded as `failed` with `RuntimeError: Cancelled by user` rather than `cancelled` (`scheduler.py:808-809` vs `background_tasks.py:140-143`). `request_cancel`'s `False` return is ignored by the endpoint (`main.py:1261-1264`), so a race can answer `{"cancelling": true}` for a task that will not cancel.

**R-6 — `background_tasks` grows without bound.** `prune_background_tasks`/`background_tasks.prune` exist but are never called (OPS-060), while the two GET audit endpoints create a new row on every request (`main.py:2461-2468,2631-2639`) and the task panel polls every 1.5 s. Storage growth is therefore proportional to page views + audit clicks, with no retention setting. `/health` reports activity-log rows and DB size but not task rows.

**R-7 — `/health` is a liveness probe wearing a readiness badge.** It always reports `status:"ok"` (`auth.py:1165-1166`), does no DB read/write check, and only reports storage writability best-effort. Docker's HEALTHCHECK therefore cannot detect a broken DB, a stalled scheduler thread, or an exhausted disk; a container whose scheduler thread died (unhandled `BaseException`, or `stop()` mid-flight) still reports healthy.

**R-8 — No backup/restore and no schema version.** There is no migration version table, no `VACUUM`, no backup API/CLI/route (grep of `backup`, `VACUUM` in `backend/app`). Durability depends on the operator backing up the `./data` volume, and the docs (PROJECT.md/README) do not describe a backup procedure. Combined with the startup re-parse backfills (which rewrite `release_group` for every row on every boot, `database.py:603-624`), a bad release of MangaTrax can mutate all historical rows with no version marker to detect it.

**R-9 — 13 tables with no foreign keys and no per-user isolation.** `users` exists only for audit attribution; no table has an owner/user column, so multi-user OIDC deployments share one mutable state and every authenticated user is effectively an administrator (no roles/claims checks anywhere in `auth.py`). Deleting a series row does not cascade (no FK), which the platform relies on implicitly.

**R-10 — `reset-all-data` is narrower than its name.** It leaves `users`, `app_settings` (including secrets), `background_tasks`, `series_alias_overrides`, `torbox_manual_pickups`, `download_failures` intact (`database.py:4670-4722`), while its log message says "All application data, releases, and caches have been reset." (`main.py:4386`). Operators expecting a factory reset will retain manual matches and pickups.

**R-11 — Two GET endpoints mutate and block.** `/api/maintenance/suspicious-volume-ranges` and `/api/maintenance/series-attribution` are `GET` but create `background_tasks` rows, write audit rows when repairs follow, and run a full-table Python scan inside the request (`asyncio.to_thread` + `await`, `main.py:2461-2468,2631-2639`). They are therefore non-idempotent GETs (cacheable/prefetchable semantics do not hold) and their latency scales with release count.

**R-12 — Inline-JS string interpolation in Settings (stored-XSS-adjacent).** `settings.html:477` (and `prioritized.html:102`) build an `onclick` string with `'{{ pg.name|escape }}'`. HTML-escaping (`&#39;`) is decoded back to `'` by the HTML parser in an attribute context, so a prioritized-group name containing `'` breaks out of the JS string; group names are user input validated only by strip+length (`schemas.py:629-639`). This is a click-triggered script-execution path in the Settings page; the DB-level value is stored verbatim.

**R-13 — Auth middleware/security headers are not exercised by the E2E harness.** `tests/e2e/harness.py:246-326` invokes route functions directly and never runs Starlette middleware, so the auth gate, CSRF check and security headers have no integration test through the harness; only `test_auth_modes.test_fastapi_middleware_mapping` exercises the middleware mapping in isolation. A regression in `main.py:234-277` would not be caught by the E2E tiers.

**R-14 — File logging and activity logging can diverge.** `uvicorn` access logs and any `logging` configured by uvicorn go to stdout; the rotating handler is attached to the root logger at INFO (`main.py:119-158`). Handler attachment happens at import time and again on settings save, but a `uvicorn` logger with `propagate=False` would bypass the file; there is no test asserting that uvicorn records land in the app log file.

**R-15 — `/healthz` exemption and `users` read helpers are dead surface** (OPS-023, OPS-024): they hint at features (k8s-style probe, user admin) that do not exist and invite a future route to be silently unauthenticated by adding `/healthz`.

**R-16 — Timezone filter performs a DB query per rendered timestamp.** `jinja_localtime` calls `db.get_setting("app_timezone")` for every timestamp (`main.py:289-295`), and `get_setting` opens a new SQLite connection (`database.py:1796-1801`). A 150-row `/logs` render therefore opens 150 connections plus page queries; this is a measurable per-render cost and a correctness risk if `app_timezone` is edited concurrently.

**R-17 — Speedtest rate limiting is global and pre-validation.** `_NETWORK_SPEEDTEST_LAST` is a module dict (`main.py:72-73`) shared by all users/tabs, written before the payload is validated (`main.py:2232-2245`), so one user's invalid request can 429 another user for up to 60 s. The test endpoint is also the only outbound network call triggered from Settings, so on a locked-down network it will always fail and looks like a bug.

**R-18 — Audit actor trust boundary.** The actor is a user-controlled display value written straight into `activity_logs.actor` (`auth.py:240-246`) and rendered (escaped) in `/logs`. An IdP display name containing newlines/quotes is stored verbatim; it cannot execute script (Jinja autoescape) but can make audit rows ambiguous/multiline. There is no per-user ID column, only the label.

**R-19 — `resolve_next_path` protects the redirect target but `next` is also consumed on the OIDC-error path.** `/auth/callback` with `?error=` always redirects to `/auth/login?error=oidc_denied` (`main.py:1104-1106`), discarding the original `next`, so a deep link during an IdP error is lost (behavioural, not security).

**R-20 — Scheduler startup cost is deferred, not removed.** `perform_initial_sync` runs in the worker, but `init_db`'s unconditional backfills (`database.py:548-575,584-624`) still run synchronously during import/first `MangaDatabase()` construction, before `lifespan` yields — `main.py:83` constructs the DB at import. The startup-performance contract (`test_startup_performance.py:73-113`) only guards the lifespan body, not module import, so a large library still delays the first byte of the process.

**R-21 — Debug flag state is only checked at startup.** `AUTH_CONFIG.debug_actions` is read from env once (`auth.py:351`); the Settings Advanced card is rendered from that same value (`main.py:317,1903`). Flipping `MANGATRAX_DEBUG_ACTIONS` requires a restart, and there is no audit row when the flag changes.

**R-22 — `POST /api/downloader/configure` bypasses the settings schema** (`main.py:3548-3576`): it writes `qbit_url` without the `qbit_url` validator that `SettingsUpdateRequest` applies, and can set an empty `qbit_password` (clearing it). It is a second write path for the same keys with weaker guarantees.

**R-23 — A settings save is not atomic.** `api_save_settings` loops `db.set_setting(k, str(v))` (`main.py:4252-4253`), and each `set_setting` opens its own connection and commits (`database.py:1803-1807`). A failure or process kill mid-loop leaves a partially applied configuration (e.g. new `staging_dir` but old `downloads_dir`), with no transaction or rollback and no "settings version" to detect it.

**R-24 — Saving a path setting has a file-moving side effect.** After writing `staging_dir`/`downloads_dir`, the handler re-probes storage and then `await`s `scheduler.periodic_downloads_inventory_and_route()` (`main.py:4269-4278`), which scans the downloads folder and tags/moves eligible files. A form save can therefore start moving files, and the HTTP response is blocked for the duration; if it raises, the exception is swallowed at debug level and the user sees a success toast with no routing performed.

**R-25 — `POST /api/calibre/sync` is synchronous.** It creates a recorded task and waits for the whole scan (`main.py:1909-1930`), so a large library blocks one HTTP request for the full duration (the same work the worker defers at startup precisely because it takes ~2 minutes, `main.py:217-227`). The UI has no `data-task-kind` hook for `sync:calibre`, so a second click or a page reload has no reattachment path.

**R-26 — Recorded-task failures reach the client as a bare 500.** `_recorded_task` marks the row `failed`, audits the error, then re-raises (`main.py:1294-1297`); since the route does not wrap it, FastAPI returns a generic "Internal Server Error" with no detail. The reason is visible only in `/logs` (audit row) or `/api/tasks/{id}` (`error`), so a UI toast for e.g. a failed audit shows nothing actionable.

---

## 9. Consensus checklist

One line per feature ID; the panel votes AGREE/DISAGREE on each.

OPS-001 — The Docker image is python:3.11-slim with curl, gosu, p7zip-full and unar installed and installs backend/requirements.txt (which includes authlib).
OPS-002 — When started as root the entrypoint remaps appuser to PUID/PGID (default 1000), chowns only when it finds a mis-owned file, and drops privileges via gosu appuser before exec; otherwise it execs the command directly.
OPS-003 — Both compose files publish 8082:8080, mount /calibre read-only plus /staging, /downloads and ./data, cap Docker json-file logs at 10 MB × 5, and drop all capabilities.
OPS-004 — GET /health returns status, auth_mode, storage, logs and network, and each block is individually exception-shielded so /health never returns 5xx because a probe failed.
OPS-005 — The container HEALTHCHECK curls http://localhost:8080/health every 30 s with a 5 s timeout, 10 s start period and 3 retries, and /health is auth-exempt.
OPS-006 — The FastAPI lifespan performs only a storage probe and scheduler.start() before yielding; no Calibre scan, series-key repair or initial sync runs before the app serves.
OPS-007 — Shutdown calls scheduler.stop(), which clears the running flag, wakes and joins the worker and Torbox threads with 2 s timeouts, and shuts the fetch executor without waiting.
OPS-008 — An invalid or incomplete authentication configuration is logged critically and terminates the process with exit code 1 instead of serving unauthenticated.
OPS-009 — Environment variables seed app_settings only when the table is newly created, plus a fixed INSERT OR IGNORE subset, so env changes after first start do not alter existing values.
OPS-010 — MANGATRAX_AUTH_MODE accepts exactly none, basic and oidc, defaults to none, and an unknown value is rejected by AuthConfig.validate().
OPS-011 — The auth middleware allows /auth/*, /static/*, /favicon.ico, /health and /healthz; in oidc mode unauthenticated HTML requests get 302 to /auth/login?next=... and /api/* gets JSON 401, while in basic mode every unauthenticated request (HTML and API) gets 401 with a Basic challenge.
OPS-012 — Basic credentials are compared with secrets.compare_digest for both username and password, and a failed attempt returns 401 with a Basic WWW-Authenticate challenge.
OPS-013 — /auth/login performs authorization-code + PKCE S256 with random state and nonce, a 600 s HMAC-signed state cookie (HttpOnly, SameSite=Lax) and redirect_uri built only from MANGATRAX_PUBLIC_URL.
OPS-014 — /auth/callback verifies the state cookie and state value, requires the PKCE verifier, exchanges the code, validates the ID token, sets the HttpOnly session cookie and deletes the state cookie.
OPS-015 — /auth/logout always clears the session cookie and redirects to the IdP end_session_endpoint with post_logout_redirect_uri=PUBLIC_URL when the provider advertises one.
OPS-016 — In oidc mode with MANGATRAX_BASIC_PASS set, HTTP Basic is accepted as break-glass, a wrong Basic attempt re-challenges instead of redirecting, and /auth/login returns a 401 Basic challenge instead of 503 when the IdP is unreachable.
OPS-017 — Session and OIDC-state cookies use a base64url(JSON).base64url(HMAC-SHA256) token that binds version, purpose, issue time and expiry and is verified in constant time.
OPS-018 — ID-token signature and JWKS validation is delegated to Authlib and fails closed with AuthProviderUnavailable when Authlib is missing, while iss, aud, exp, iat/nbf skew, nonce and sub are additionally checked explicitly.
OPS-019 — Successful Basic authentications and OIDC requests JIT-upsert a users row keyed on subject, normally throttled to once per 300 s per identity, except the OIDC callback which forces an upsert on every login; existing non-empty profile fields are never blanked.
OPS-020 — The middleware sets a per-request actor ContextVar that log_activity() uses as its default actor, background work passes actor="scheduler", and the logs page displays that actor.
OPS-021 — Cross-origin unsafe requests are rejected with 403 only when auth is enabled, MANGATRAX_CSRF_PROTECT is truthy and MANGATRAX_PUBLIC_URL is set; requests without Origin/Referer are allowed.
OPS-022 — Every response carries X-Content-Type-Options, X-Frame-Options, X-XSS-Protection and Referrer-Policy, added by the outermost middleware so auth 401/302/403 responses are covered.
OPS-023 — The users-table read helpers get_user and get_all_users have no caller in the application and there is no users API or admin UI.
OPS-024 — /healthz is listed as an auth-exempt path but no /healthz route is registered; only /health exists.
OPS-025 — security.validate_public_url rejects non-http(s), embedded credentials, blocked hostnames and all loopback/private/link-local/metadata/multicast/reserved literals, and in strict mode re-checks every resolved address and fails closed on DNS errors.
OPS-026 — Directory settings and the /api/calibre/test probe reject NUL bytes, ".." segments, relative paths, over-long paths and symlink escapes, requiring the resolved path to stay inside allowed roots.
OPS-027 — Only absolute http(s) URLs can be stored as website_url or rendered through the safe_url filter, so javascript:, data:, file: and vbscript: values are rejected at save and at render.
OPS-028 — SQL LIKE wildcards in user search terms are escaped by escape_like/like_contains and callers append ESCAPE '\', so a term is always matched literally.
OPS-029 — POST /api/debug/action returns 403 unless MANGATRAX_DEBUG_ACTIONS=1 and validates its action against a closed enum of six debug actions.
OPS-030 — reset-all-data deletes releases, tracked series, blacklist, activity logs, metadata cache, feeds and prioritized groups, clears the cover cache, resets the backfill state keys in app_settings, and leaves users, most settings, background_tasks, alias overrides, manual pickups and download failures intact.
OPS-031 — purge-non-manga deletes only releases whose media_type is an explicit non-manga value, keeping manga, unknown and NULL rows, and audits the purge.
OPS-032 — The normalize-english-titles debug action now runs the guarded idempotent re-key repair instead of renaming series keys to AniList English titles.
OPS-033 — POST /api/maintenance/clear accepts only clear-cover-cache, clear-discovery and clear-descriptions, is not debug-gated, and writes one MAINTENANCE_CLEARED audit row with actor, action and count.
OPS-034 — GET /api/settings returns every app_settings row and replaces a non-empty torbox_api_key with the mask placeholder.
OPS-035 — POST /api/settings rejects unknown keys with 400, validates each known field, persists each provided value with set_setting(str(value)) and returns {status, storage}.
OPS-036 — The Torbox API key is masked in HTML and JSON and a saved mask/blank value is ignored, while qbit_password is returned in plaintext by GET /api/settings and rendered into the settings form.
OPS-037 — Saving a downloads_dir equal (realpath, case-insensitive) to staging_dir is rejected with 400.
OPS-038 — When MANGATRAX_PATH_ROOTS is set, calibre_path, staging_dir and downloads_dir saves are rejected if they resolve outside those roots; when unset only traversal/NUL/relative/symlink checks apply.
OPS-039 — Storage health is proven by creating and deleting a temp file in downloads_dir and staging_dir, reports per-path exists/is_dir/writable/uid/gid/errno/free_bytes, and is persisted to storage_health_json and reused while the configured paths are unchanged.
OPS-040 — Saving settings audits SETTINGS_UPDATED, repoints only the exact seeded Nyaa feed when nyaa_base_url changes, reconfigures file logging for log_* keys, and re-probes storage plus runs one inventory/routing pass for path keys.
OPS-041 — app_settings also holds non-writable internal keys (media_filter_applied, jpegxl_filter_applied, ln_release_backfill_done, storage_health_json, last_speedtest_json, activity_last_prune_at, backfill_status/progress/last_page, torbox_last_poll_at) that POST /api/settings cannot set.
OPS-042 — schemas.SETTINGS_KEYS lists 37 keys, is referenced nowhere, and already omits several keys that SettingsUpdateRequest accepts.
OPS-043 — /settings renders nine in-page sections with a sticky scrollable section nav, a dirty-state sticky save bar, and the Maintenance card plus all safe audits visible without the debug flag.
OPS-044 — /api/calibre/test contains the requested path to {calibre_path, CALIBRE_DIR, staging_dir} and returns 403 for a path outside those roots.
OPS-045 — /api/calibre/status returns in_progress, initial_sync_done and last_sync without blocking.
OPS-046 — /api/downloader/configure writes qbit_* settings directly (bypassing SettingsUpdateRequest validation), audits DOWNLOADER_CONFIGURED, and then tests the connection.
OPS-047 — /api/downloader/test and /api/downloader/torbox/test accept transient credentials, and a submitted Torbox key equal to the mask falls back to the saved key.
OPS-048 — With log_to_file=true exactly one named RotatingFileHandler writes DATA_DIR/logs/mangatrax.log with maxBytes=log_file_max_mb MiB and backupCount=log_file_backups, and disabling removes it.
OPS-049 — Activity rows store event_type, message, details, UTC created_at and a resolved actor (explicit → ContextVar → "system"), with no severity column.
OPS-050 — Activity pruning deletes rows older than activity_retention_days, then the lowest-id rows above activity_max_rows, then truncates the WAL checkpoint, and runs on the first worker tick and at most every 6 hours.
OPS-051 — POST /api/logs/prune runs the same retention math as a recorded logs:prune task and returns deleted_old, deleted_excess, remaining, checkpointed plus task_id and task.
OPS-052 — /logs server-renders the newest 150 activity rows with event-type and actor chips, a client-side substring filter and a Prune now button, and has no pagination, server-side filtering or auto-refresh.
OPS-053 — run_in_background inserts a background_tasks row before submitting to a 3-worker ThreadPoolExecutor, registers an in-process cancel event only when cancellable, audits start/finish/failure/cancel and always clears its in-memory maps.
OPS-054 — TaskReporter writes progress at most every 0.5 s unless progress moved ≥1 point, the message changed or force=True, and clamps progress_pct to 0–100.
OPS-055 — _recorded_task creates a task row, runs the work synchronously, marks it success with a stored result JSON and 100 %, or failed with the exception text, and audits both transitions.
OPS-056 — GET /api/tasks accepts active(0/1), kind and limit(1–200) and returns status, active_count and serialized tasks with elapsed_seconds, active, result, remaining and eta_seconds.
OPS-057 — GET /api/tasks/{id} returns the serialized task or 404.
OPS-058 — POST /api/tasks/{id}/cancel returns 404 for unknown tasks and 409 when the task has no live in-process cancel flag, otherwise sets the event and returns cancelling=true.
OPS-059 — The global task panel's Cancel button never renders because serialize_task does not emit a cancellable field, and tags:refresh is the only cancellable kind.
OPS-060 — background_tasks.prune()/prune_background_tasks() are defined but never called, so the background_tasks table grows without any retention.
OPS-061 — The scheduler runs one daemon worker thread with a 10 s sleep, re-reads interval settings each tick, and shields the whole tick in a single try/except: a job exception is logged and the remainder of that tick's jobs are skipped, while the loop itself continues.
OPS-062 — Calibre sync runs every calibre_sync_interval_minutes (default 15) under a non-blocking _calibre_sync_lock, so a manual and an automatic sync can never overlap.
OPS-063 — Feed polling runs every feed_check_interval_minutes (default 60) under a non-blocking _feed_check_lock, and a concurrent call returns skipped=true instead of running twice.
OPS-064 — The manual Poll Feeds action starts a daemon thread and returns immediately, with running/result state held only in memory and polled via a status endpoint.
OPS-065 — The auto-tag job runs every 300 s only when auto_tag is exactly "true" and both tags staged CBZs and routes eligible raw downloads into staging.
OPS-066 — An inventory + tag/move routing pass runs every 45 s so finished downloads become visible and eligible for staging without user action.
OPS-067 — Every 300 s, COMPLETED releases whose recorded files no longer exist are reconciled: re-pointed at a found copy, promoted to IN_CALIBRE when the matched Calibre book count covers the highest wanted volume, or otherwise reset to IDLE with DOWNLOAD_FILES_MISSING.
OPS-068 — Every 300 s, past-due Torbox manual pickups are expired with a best-effort remote delete.
OPS-069 — The activity-log prune job runs on the first worker tick and then at most every 6 hours (21600 s).
OPS-070 — Every 60 s, auto-downloads suppressed by in-flight dedupe are retried in batches of at most 50.
OPS-071 — Every 30 s, active downloads are checked and no-longer-wanted torrent files are filtered out.
OPS-072 — A separate Torbox poller thread polls at torbox_poll_seconds (default 5) when busy and backs off to min(30, max(15, 3× the floor)) when idle, and can be woken immediately.
OPS-073 — The worker's first action performs the legacy series-key repair and, when metadata.db exists, the initial Calibre sync, both exception-shielded, exactly once per process.
OPS-074 — The polite backfill worker runs in a daemon thread from start_page to backfill_max_pages (0 = unlimited) with backfill_delay_seconds between requests and persists backfill_status, backfill_progress and backfill_last_page.
OPS-075 — /api/backfill/start returns in_progress when a backfill is already running, /stop signals it to stop, and /status reads the persisted backfill settings.
OPS-076 — /api/maintenance/refresh-tags starts a cancellable tags:refresh background task that backfills AniList genres/tags for at most the first 1000 releases lacking tags.
OPS-077 — The suspicious-volume-range audit scans all releases and the repair clears volumes/range, sets status IGNORED, and only repairs rows the audit marked safe_to_repair.
OPS-078 — The series-attribution audit reports releases whose own title parses to a different tracked series and marks ambiguous multi-part titles as not repairable; repair re-keys only selected, safe rows.
OPS-079 — False fuzzy series-match audit is read-only and repair requires confirm=true, accepts at most 500 release ids, and re-keys every selected existing release to its title-parsed series while preserving veto statuses (selection is not re-validated against the audit list).
OPS-080 — Light-novel cleanup requires confirm=true, an action of ignore or delete, and at most 500 ids, and delete removes database rows only, never files.
OPS-081 — Series alias overrides are listed via GET /api/maintenance/series-aliases and deleted by id via DELETE, which returns 404 for an unknown id and invalidates the grouped cache.
OPS-082 — Release-cache preview and clear use the same series/status filters, always skip DOWNLOADING/QUEUED rows, require confirm=true, and audit RELEASES_CLEARED with the skipped-active count.
OPS-083 — Network diagnostics are read-only /proc, /sys, cgroup, resolv.conf, TCP and route facts with heuristic warnings, perform no network I/O, never raise, and appear in both /health and Settings.
OPS-084 — The speedtest is restricted to the fixed Cloudflare endpoint, caps bytes at 1 GB and connections at 8, splits >90 MB requests into sequential rounds, enforces a global 60 s rate limit, and persists a trimmed result.
OPS-085 — Discord and generic webhook deliveries for NEW_REQUEST, ACQUIRED, FEED_SCAN and DOWNLOAD_PROCESSED use a 5 s timeout, structurally validate the URL, and never raise to callers.
OPS-086 — Automatic feed-scan notifications are suppressed when webhook_feed_scan_notifications is "false" and processed-batch notifications when webhook_processed_notifications is "false", and manual polls never notify.
OPS-087 — `init_db` creates and migrates exactly 13 tables idempotently on one connection; on a first run it explicitly commits twice — after the conditional one-time LN backfill (`database.py:936`, only when the marker is absent) and a final commit at `:995` — and once on later runs, so the schema work is not one atomic transaction. Tables: series_tracking, series_blacklist, releases, prioritized_groups, feeds, activity_logs, users, app_settings, manga_metadata_cache, series_alias_overrides, background_tasks, torbox_manual_pickups and download_failures.
OPS-088 — Upgrade migrations are PRAGMA-table_info-guarded ALTER TABLE additions plus idempotent in-place data backfills, with no migration version table.
OPS-089 — Every database operation opens a new SQLite connection with timeout=10 s, Row factory, WAL journal mode (falling back to DELETE) and foreign_keys=ON, and no table declares a foreign-key constraint.
OPS-090 — On a fresh database the app seeds 41 app_settings keys (39 defaults plus filter_non_manga and media_filter_applied; startup passes later add jpegxl_filter_applied and ln_release_backfill_done for 43 total), one built-in Nyaa RSS feed, and four prioritized groups.
OPS-091 — The LN/chapter-only/JPEGXL re-typing pass is gated by media_filter_applied and filter_non_manga; a separate JPEGXL-only pass gated by jpegxl_filter_applied runs regardless of filter_non_manga; neither deletes rows or writes status, and after the markers are set filter_non_manga has no further runtime effect.
OPS-092 — get_setting, set_setting and get_all_settings each open a new SQLite connection per call, so settings reads are not cached.
OPS-093 — Timestamps are resolved through app_timezone → TZ → Europe/Copenhagen → host zone by the localtime/localdate Jinja filters, and an invalid IANA name silently falls back to Europe/Copenhagen.
OPS-094 — Durable state is the ./data volume (mangatrax.db plus -wal, covers, logs); there is no backup, restore, export or VACUUM path, and the only WAL maintenance is a truncate checkpoint during activity pruning.
OPS-095 — `POST /api/settings/test-webhook` performs real outbound Discord and/or generic webhook test posts (10 s timeout), returns per-channel `{success,message}` results plus an overall status, and is the only settings endpoint that exercises webhook URLs.
OPS-096 — `POST /api/calibre/sync` creates a persisted task and waits for the whole scan, but its UI trigger has no `data-task-kind` binding, so the button cannot be disabled or reattached across reloads (it has no data-task-kind binding).
OPS-097 — `download_status='QUEUED'` is a tolerated legacy value that current code never writes: qBittorrent submission writes DOWNLOADING directly, Torbox slot-queueing is encoded in `downloader_id='queued:<n>'` plus `torbox_progress_json.phase`, and QUEUED survives only in read filters.
OPS-098 — reset-all-data re-seeds five prioritized groups (danke-Empire, LuCirAn, 1stKind, TenmaLand, Stick) that differ from the four groups seeded on a fresh database (danke-Empire, 1R0N, Lu自主规制, a-sloth).


---


# Part 5 — UI, Frontend, Interaction Inventory & Usability Audit


# 05 — UI / Frontend, Interaction Inventory, Route Map & Cross-cutting Usability Audit

## 1. Title & scope statement

**Feature-ID prefix:** `UI-`
**Owner:** EXPERT-5 (UI/UX authority for the panel)
**Scope:** the complete presentation layer of MangaTrax as implemented in
`backend/app/templates/` (server-rendered Jinja2 + inline vanilla JS) and the HTTP
surface in `backend/app/main.py` that feeds it. Covers: global shell (`base.html`),
all ten page templates, all six partials, every interactive control, per-page JS,
responsive behaviour as-is, accessibility as-is, page→API mapping, state-refresh
patterns, visual language, and a route→template→page map for every HTTP route. This
document is the input for the later UI-enhancement design/validation phase.

**Out of scope:** backend business logic except where it is the direct contract of a
UI control (response shapes the JS depends on); test authorship; deployment.

**Method:** static, read-only code reading of the current working tree. No server was
started and no browser automation exists in this repo; all UI assertions in the test
suite are therefore *source-level* (see §"Hard test constraints" in §5.6). Citations
are `file:line` relative to the repo root. Route count verified 2026-11: **125 HTTP
routes** (14 non-`/api` + 111 `/api/*`), not the 76 assumed in the task brief.

**State vocabulary used below**

| State | Meaning |
|---|---|
| `ACTIVE` | Control/feature is wired end-to-end and has a live call site. |
| `PARTIAL` | Works, but path is conditional/broken/dead in some mode, or evidence shows it cannot work as intended. |
| `VESTIGIAL` | Markup/JS exists but nothing can reach it (no call site, dead branch, never-populated element, or dead API). |

---

## 2. Route → template → page map

### 2.1 HTML pages, auth, health (14 routes)

| # | Method | Route | Handler (`main.py`) | Template / response | Owning page / UI shell |
|---|---|---|---|---|---|
| 1 | GET | `/` | `dashboard_view` 1138–1167 | `index.html`; context `active_page=dashboard`, `stats`, `calibre_status`, `pending_requests`, `recent_logs`, `torrents[:5]` | Overview (Dashboard) |
| 2 | GET | `/series` | `series_view` 1169–1180 | `series.html`; `active_page=series`, `series_list=[]` (empty shell; rows fetched from API), `total_series` | Library → Series |
| 3 | GET | `/releases` | `releases_view` 1182–1207 | `releases.html`; query `status`, `view` (series\|flat), `sort` (post_date\|updated\|series, default updated); `initial_total`; empty arrays | Library → Releases |
| 4 | GET | `/prioritized` | `prioritized_view` 1209–1232 | `prioritized.html`; query `status`, `view`; `prioritized_groups`, `initial_total`; empty arrays | Library → Prioritized |
| 5 | GET | `/discovery` | `discovery_view` 1571–1620 | `discovery.html`; query `page`, `page_size` (24 default, 6–120), `q`, `include_tags`, `exclude_tags`, `tag_match` (any\|all), `sort` (relevance\|newest\|series\|post_date); server-paginated rows + `blacklisted_series` | Discover → Discovery |
| 6 | GET | `/downloads/manual-pickup` | `manual_pickup_view` 1622–1660 | `manual_pickup.html`; `active_page=downloads` (note: *not* a distinct active page); `pickups`, `pickup_cap`, `pickup_expiry_days`, `pickup_enabled` | Downloads → Manual Pickup (rendered as 6th top-level nav link) |
| 7 | GET | `/downloads` | `downloads_view` 1771–1849 | `downloads.html`; `qbit_status`, `engine`, `torrents`, `staging_files`, `staging_groups`, `awaiting_series`, `downloads_inventory`, `settings`, `manual_pickup_count`, `storage_health` | Downloads |
| 8 | GET | `/feeds` | `feeds_view` 1851–1861 | `feeds.html`; `feeds` | Discover → Feeds |
| 9 | GET | `/logs` | `logs_view` 1863–1873 | `logs.html`; `logs` (last 150 rows, fixed) | System → Logs |
| 10 | GET | `/settings` | `settings_view` 1875–1905 | `settings.html`; `settings`, `prioritized_groups`, `storage_health`, `network_diagnostics`, `file_logging`, `release_cache`, `series_alias_overrides`, `release_series`, `torbox_api_key_set`, `debug_actions_enabled` | System → Settings |
| 11 | GET | `/auth/login` | `auth_login` 1061–1091 | 302 to IdP (OIDC) or `/` (basic/none); JSON 401/503 on provider failure | Auth (no template) |
| 12 | GET | `/auth/callback` | `auth_callback` 1094–1125 | 302 + session cookie; 302 `/auth/login?error=…` on failure | Auth (no template) |
| 13 | GET | `/auth/logout` | `auth_logout` 1128–1134 | 302 + cookie delete | Auth user menu |
| 14 | GET | `/health` | `health_check` 1040–1058 | JSON: auth, storage, logs, network diagnostics | Ops/infra, no UI |

There are **no** templates for auth, 404/500 errors, or health — auth pages render the
provider or a JSON body; FastAPI's default JSON 404/422 bodies will surface as raw
JSON in the browser if a user follows a bad link.

### 2.2 JSON API routes (111) and their owning UI surface

Legend: DASH=dashboard, SERIES=series page, REL=releases page, PRIO=prioritized page,
DISC=discovery page, FEEDS=feeds page, DL=downloads page, MP=manual pickup page,
LOGS=logs page, SET=settings page, GLOBAL=`base.html` shell (tasks/banners/modals),
`—` = no UI caller (API-only / vestigial for the UI).

| # | Method | Route | `main.py` | Response contract the UI relies on | Owner |
|---|---|---|---|---|---|
| 15 | GET | `/api/tasks` | 1235 | `{tasks:[…serialize_task], active_count}` | GLOBAL |
| 16 | GET | `/api/tasks/{task_id}` | 1248 | `{task}`; 404 | GLOBAL, MP |
| 17 | POST | `/api/tasks/{task_id}/cancel` | 1256 | `{status,cancelling}`; 404/409 | GLOBAL |
| 18 | GET | `/api/releases/cards` | 1378 | `{offset,count,total,has_more,html}` | REL, PRIO |
| 19 | GET | `/api/prioritized/cards` | 1406 | same | PRIO |
| 20 | GET | `/api/releases/flat_rows` | 1434 | `{offset,count,total,has_more,html}` | REL |
| 21 | GET | `/api/releases/series_releases` | 1471 | `{offset,count,total,has_more,html}` | REL (card "Show all") |
| 22 | GET | `/api/prioritized/flat_rows` | 1503 | `{offset,count,has_more,html}` (no `total`) | PRIO |
| 23 | GET | `/api/series/rows` | 1523 | `{offset,count,total,has_more,html}` | SERIES |
| 24 | GET | `/api/releases/grouped` | 1547 | `{count,series_groups}` | — |
| 25 | POST | `/api/maintenance/refresh-tags` | 1552 | `{status:started,task_id}` | SET |
| 26 | GET | `/api/tags` | 1565 | `{tags:[{name,category,count}]}` | DISC |
| 27 | GET | `/api/torbox/manual-pickups` | 1663 | `{pickups,count,active_count}` | MP (no caller) |
| 28 | POST | `/api/torbox/manual-pickups/{id}/map` | 1678 | result dict; 404 | MP |
| 29 | POST | `/api/torbox/manual-pickups/{id}/volumes` | 1690 | result dict; 400 | MP |
| 30 | POST | `/api/torbox/manual-pickups/{id}/fetch` | 1704 | `{status:started,task_id}` | MP |
| 31 | POST | `/api/torbox/manual-pickups/{id}/retry` | 1737 | `{status:started,task_id}` | MP |
| 32 | POST | `/api/torbox/manual-pickups/{id}/discard` | 1760 | result dict | MP |
| 33 | POST | `/api/calibre/sync` | 1909 | `{success,message,task_id,task}` (synchronous) | SERIES |
| 34 | GET | `/api/calibre/status` | 1932 | `{in_progress,initial_sync_done,last_sync}` | GLOBAL banner |
| 35 | GET | `/api/calibre/test` | 1937 | library summary dict | SET |
| 36 | GET | `/api/covers` | 1957 | image bytes; SVG fallback; 200 always | ALL (cover `<img src>`) |
| 37 | POST | `/api/feeds/check` | 1979 | `{success,started,message,task_id,task}` | FEEDS |
| 38 | GET | `/api/feeds/check/status` | 2012 | `{running,started_at,finished_at,result}` | GLOBAL helper |
| 39 | POST | `/api/feeds/test` | 2017 | `{status:success,count,items}` / 400/500 `{status:error,message}` | FEEDS |
| 40 | POST | `/api/feeds` | 2038 | `{status:success,feed_id}` | FEEDS |
| 41 | DELETE | `/api/feeds/{id}` | 2053 | `{status:success}` | FEEDS |
| 42 | POST | `/api/feeds/{id}/toggle` | 2059 | `{status:success,enabled}`; 404 | FEEDS |
| 43 | POST | `/api/staging/tag` | 2076 | `{status,success,message,counts}` | DL |
| 44 | GET | `/api/downloads/status` | 2104 | full status payload: `torrents`, `downloader`, `inventory`, `last_scanned_at`, `engine`, `degraded` | DL |
| 45 | POST | `/api/downloads/torbox/poll` | 2126 | poll result | DL |
| 46 | POST | `/api/downloads/prune-empty` | 2146 | `{removed}` | DL |
| 47 | POST | `/api/downloads/flatten-staging` | 2162 | `{status:success,flattened,conflicts_renamed,dirs_removed,task_id}` | DL |
| 48 | POST | `/api/downloads/scan` | 2173 | `{status:success,inventory,last_scanned_at,entries,reconciled,task_id}` | DL |
| 49 | GET | `/api/downloads/auto-download/diagnostics` | 2213 | diagnostics payload (global_enabled, engine, checks, recent_decisions) | DL |
| 50 | GET | `/api/diagnostics/network` | 2220 | network snapshot | SET |
| 51 | POST | `/api/diagnostics/speedtest` | 2229 | speedtest result + task_id; 429/400 | SET |
| 52 | GET | `/api/calibre/series` | 2322 | `{series:[{id,name,authors,book_count}]}` | SERIES, DL, MP |
| 53 | POST | `/api/downloads/awaiting-series/match` | 2448 | `{target:{name},…}` | DL, MP |
| 54 | GET | `/api/maintenance/suspicious-volume-ranges` | 2461 | audit list | SET |
| 55 | GET | `/api/maintenance/light-novels/audit` | 2471 | audit list | SET |
| 56 | POST | `/api/maintenance/light-novels/cleanup` | 2477 | `{status,ignored,deleted?}` | SET |
| 57 | POST | `/api/maintenance/false-series/audit` | 2508 | audit list | SET |
| 58 | POST | `/api/maintenance/false-series/repair` | 2519 | repair result | SET |
| 59 | POST | `/api/maintenance/repair-volume-ranges` | 2551 | repair result | SET |
| 60 | GET | `/api/maintenance/series-aliases` | 2573 | alias list | SET (no caller) |
| 61 | DELETE | `/api/maintenance/series-aliases/{id}` | 2578 | `{status:success}` | SET |
| 62 | GET | `/api/maintenance/releases-cache` | 2587 | `{count}` | SET |
| 63 | POST | `/api/maintenance/clear-releases` | 2599 | `{status:success,…}` | SET |
| 64 | GET | `/api/maintenance/series-attribution` | 2631 | audit list | SET |
| 65 | POST | `/api/maintenance/series-attribution-repair` | 2642 | repair result | SET |
| 66 | POST | `/api/logs/prune` | 2664 | `{deleted_old,deleted_excess,remaining}` | LOGS, SET |
| 67 | GET | `/api/tagger/preview` | 2826 | `{comicinfo:{…}}` | DL tag modal |
| 68 | POST | `/api/tagger/manual-tag` | 2837 | `{status,success,message,…}` | DL tag modal |
| 69 | POST | `/api/downloads/inventory/audit` | 2957 | `{duplicates,already_present,…}` | DL raw cleanup |
| 70 | POST | `/api/downloads/inventory/clear` | 2967 | `{status,task_id,…}` | DL raw cleanup |
| 71 | POST | `/api/downloads/inventory/delete` | 2997 | `{bytes_freed}` | DL raw cleanup |
| 72 | GET | `/api/downloads/failures` | 3020 | `{failures:[…]}` | DL failed docs |
| 73 | POST | `/api/downloads/failures/scan` | 3027 | scan result | DL failed docs |
| 74 | POST | `/api/downloads/failures/{id}/retry` | 3047 | retry result | DL failed docs |
| 75 | POST | `/api/downloads/failures/{id}/convert` | 3078 | convert result | DL failed docs |
| 76 | POST | `/api/downloads/failures/{id}/delete` | 3161 | delete result | DL failed docs |
| 77 | POST | `/api/downloads/failures/delete-all` | 3182 | delete result | DL failed docs |
| 78 | GET | `/api/downloads/staging-groups` | 3200 | group list | — |
| 79 | POST | `/api/downloads/staging-series/match` | 3206 | `{status,started?,task_id}` | DL staging match |
| 80 | POST | `/api/downloads/files/delete` | 3250 | `{bytes_freed,series_remaining}` | DL file rows |
| 81 | GET | `/api/downloads/staging-duplicates` | 3334 | `{duplicates:[…]}` | DL staging cleanup |
| 82 | POST | `/api/downloads/staging-duplicates/purge` | 3344 | `{status,task_id}` | DL staging cleanup |
| 83 | GET | `/api/tagger/audit` | 3370 | `{files,summary}` | DL audit |
| 84 | POST | `/api/tagger/repair-series` | 3474 | `{field_counts}` | DL audit |
| 85 | GET | `/api/search` | 3498 | `{releases,series,…}` | — |
| 86 | POST | `/api/downloader/test` | 3516 | `{success,message,version}` | DL, SET |
| 87 | POST | `/api/downloader/torbox/test` | 3537 | `{success,message,plan,active_slots}` | SET |
| 88 | POST | `/api/downloader/configure` | 3548 | `{status,connection:{success,message}}` | DL |
| 89 | GET | `/api/downloader/queue` | 3578 | queue list | — |
| 90 | POST | `/api/downloader/pause/{hash}` | 3585 | result | DL |
| 91 | POST | `/api/downloader/resume/{hash}` | 3591 | result | DL |
| 92 | DELETE | `/api/downloader/delete/{hash}` | 3597 | result | DL |
| 93 | POST | `/api/downloads/torbox/{release_id}/retry` | 3608 | retry result | DL |
| 94 | POST | `/api/downloads/torbox/{release_id}/remove` | 3666 | remove result | DL |
| 95 | POST | `/api/downloads/torbox/clear-failed` | 3704 | `{removed}` | DL |
| 96 | GET | `/api/releases/{id}/state` | 3730 | `{release_id,state,label,progress_pct,can_download,…}` | REL, PRIO |
| 97 | POST | `/api/releases/{id}/download` | 3738 | `{status,message,state}` (success\|skipped) | REL, PRIO, DASH |
| 98 | POST | `/api/series/{id}/toggle` | 3826 | `{status}` | SERIES |
| 99 | POST | `/api/series/{id}/link` | 3832 | `{status}` | SERIES |
| 100 | POST | `/api/releases/{id}/different-series` | 3839 | `{series_name,release_status}` | REL |
| 101 | POST | `/api/series/{id}/match-calibre` | 3926 | `{target,rekeyed_releases,book_count}` | SERIES |
| 102 | GET | `/api/series/link-audit` | 3962 | audit list | — |
| 103 | POST | `/api/series/link-repair` | 3990 | repair result | — |
| 104 | POST | `/api/series/{id}/backfill` | 4049 | `{status:started,message}` | SERIES |
| 105 | POST | `/api/series/backfill` | 4064 | `{status:started,message}` | SERIES, REL, PRIO |
| 106 | POST | `/api/releases/{id}/status` | 4074 | status update | — |
| 107 | POST | `/api/releases/{id}/ignore` | 4081 | `{status:success}` | REL |
| 108 | POST | `/api/releases/{id}/unignore` | 4088 | `{status:success}` | REL |
| 109 | POST | `/api/blacklist` | 4095 | `{status:success}` | SERIES, REL, DISC |
| 110 | DELETE | `/api/blacklist/{id}` | 4103 | `{status:success}` | DISC |
| 111 | GET | `/api/prioritized/groups` | 4112 | `{groups}` | PRIO, SET (page context; no caller) |
| 112 | POST | `/api/prioritized/groups` | 4117 | `{status:success}` | PRIO, SET, REL, DISC |
| 113 | DELETE | `/api/prioritized/groups/{id}` | 4124 | `{status:success}` | PRIO, SET |
| 114 | GET | `/api/prioritized/releases` | 4130 | release list | — |
| 115 | POST | `/api/discovery/track` | 4135 | `{status:success}` | DISC, REL, PRIO |
| 116 | POST | `/api/discovery/blacklist` | 4173 | `{status:success}` | DISC |
| 117 | POST | `/api/discovery/{id}/fetch-info` | 4181 | `{status:success,description,mangaupdates_url}` | DISC |
| 118 | GET | `/api/settings` | 4211 | `{status,settings}` (Torbox key masked) | — (server-rendered page instead) |
| 119 | POST | `/api/settings` | 4219 | `{status:success,storage:{paths}}`; 400 on bad paths | SET |
| 120 | POST | `/api/settings/test-webhook` | 4281 | `{status,message}` | SET |
| 121 | POST | `/api/backfill/start` | 4293 | `{status:started\|in_progress,message}` | GLOBAL modal (opened from DISC) |
| 122 | POST | `/api/backfill/stop` | 4312 | `{status:stopping}` | GLOBAL banner |
| 123 | GET | `/api/backfill/status` | 4318 | `{running,status,progress,last_page}` | GLOBAL banner, modal |
| 124 | POST | `/api/maintenance/clear` | 4323 | maintenance result | SET |
| 125 | POST | `/api/debug/action` | 4342 | `{status,message}`; 403 unless `MANGATRAX_DEBUG_ACTIONS=1` | SET (debug-gated) |

**Route observations**

- `/api/covers` (main.py:1957) is the only binary/image endpoint; there is **no**
  `StaticFiles` mount and **no** favicon route (the template head references no icon),
  so browser tabs show a generic icon and every asset is CDN-hosted (base.html:8,10).
- 14 routes have no template call site: #24, 27, 60, 78, 85, 89, 102, 103, 106, 111,
  114, 118 and (ops only) #14 — API-first leftovers the UI redesign may reuse or prune.
- Every HTML page route sets `active_page`, which is the single source of nav
  highlighting; `manual_pickup_view` intentionally reuses `downloads` (main.py:1655)
  so the separate Manual Pickup nav item never gets an active state.

---

## 3. Feature inventory

IDs are stable and map 1:1 to the consensus checklist (§10). "Persistence" is noted
only where the UI stores state (DB, URL, localStorage, in-memory JS).

### 3.1 Global shell — `backend/app/templates/base.html`

| ID | Feature | State | User-facing behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-001 | Dark-only app shell | ACTIVE | Every page uses `bg-[#0b0f19]`, slate text, `html class="dark"`; no light theme exists | base.html:2,95; no light-mode classes anywhere | implicit | html class |
| UI-002 | Sticky global header | ACTIVE | 64px bar pinned to top, blurred, z-40, holds brand + nav + user/mobile controls | base.html:101–218 | implicit | — |
| UI-003 | Brand lockup + product tagline | ACTIVE | Rose gradient tile, "MangaTrax", badge "Bundles & qBit", subtitle "Calibre & BookOrbit Monitor", links to `/` | base.html:105–118 | click | — |
| UI-004 | 5-item grouped desktop nav | ACTIVE | Overview, Library▾ (Series/Releases/Prioritized), Discover▾ (Discovery/Feeds), Downloads, System▾ (Logs/Settings); active page highlighted, parent highlighted for children, Prioritized in amber | base.html:121–191; highlights 122/129/135/138/141/149/155/158/165/177/183/186 | click/hover | `active_page` from server |
| UI-005 | "Manual Pickup" 6th top-level nav link | PARTIAL | Renders as an extra flat item between Downloads and System; has no `data-nav-item`, no active state, and an always-empty count badge | base.html:169–173 (badge `nav-manual-pickup-count` 172, never populated anywhere) | click | — |
| UI-006 | Desktop dropdowns (click-to-open, touch friendly) | ACTIVE | Click trigger toggles panel; outside click and Escape close; Escape returns focus to trigger; aria-expanded/haspopup/controls kept in sync; menus are `role=menu`/`menuitem` | base.html:128–190 markup; JS 430–514 | click / Escape | DOM state only |
| UI-007 | Auth-conditional user menu | ACTIVE | Only when `auth_mode != 'none'`; shows actor name + "Signed in (mode)" + Sign out (`/auth/logout`) | base.html:197–211; `auth_mode`/`auth_actor` globals registered main.py:315–317 | click | OIDC/session cookie server-side |
| UI-008 | Mobile hamburger menu | ACTIVE | `md:hidden` button; toggles grouped mobile menu with Library/Discover/System headings, 10 links with `min-h-[44px]`, auth block; closes on outside click/Escape | base.html:212–214, 221–273; JS 458–480, 496–498, 501–514 | click | DOM state only |
| UI-009 | Global page-load progress bar | PARTIAL | 2.5px gradient bar animates on internal `<a>` clicks; completes on `pageshow`. **Buttons that reload the page get no bar** (sync/tag/feed/reset flows call `location.reload()` directly) | base.html:98, 930–967, 970–999 | anchor click | — |
| UI-010 | Toast notification stack | PARTIAL | Bottom-right transient toasts, success/error/info; auto-dismiss after 4s; used by nearly every action. Container has **no** `role`/`aria-live`; message is injected with `innerHTML` (unescaped) | base.html:276, 402–424 | any JS action | 4s timer |
| UI-011 | Global background-task panel | ACTIVE | Floating "Tasks" button shows active count; panel lists up to 12 tasks with progress bar, %, elapsed, ETA, error and Cancel; finished-task toast; polls every 1.5s on every page; server state survives reload | base.html:390–398, 1001–1145; `setInterval(poll, 1500)` 1140; `MangaTasks` API 1142 | poll timer | server DB via `/api/tasks` |
| UI-012 | Task-button reattachment | ACTIVE | Buttons with `data-task-kind` (+`data-task-scope`) are disabled and show progress while a matching task runs; re-enabled on completion | base.html:1074–1099; used in downloads.html:47,352,355,541,575,579 and manual_pickup.html:40–41,56 | tasks poll | server DB |
| UI-013 | Global image lightbox | PARTIAL | Click a cover to open enlarged image + title caption; backdrop/Close/Escape dismiss; body scroll locked. Images are plain `<img onclick>` (not focusable, no keyboard open), alt is static "Enlarged Cover", no zoom/prev-next, **no other modal closes on Escape** | base.html:279–288, 619–643 | cover click | — |
| UI-014 | Background Calibre sync banner | ACTIVE | Hidden unless `in_progress` or initial sync unfinished; role=status/aria-live; one status check on load, 3s poll only while running; dismissible; on completion toast + auto-reload; never on `/auth/*` server- or JS-side | base.html:335–343, 691–751; tests pin strings (§5.6) | page load + 3s poll | poll timer + `calibreSyncDismissed` in memory |
| UI-015 | Global historical-backfill banner | ACTIVE | Appears on every page while backfill runs; shows progress text and Stop Backfill button; starts polling only when running (2.5s); on finish shows "completed" toast; auth-page aware only by not being loaded there | base.html:346–365, 645–689 | load + 2.5s poll | poll timer |
| UI-016 | Backfill setup modal | PARTIAL | Start-page + max-pages form with resume hint; guards "already running"; POSTs `/api/backfill/start`, opens banner on success. No `role=dialog`, no Escape close, no focus trap; background scroll not locked; only reachable from the Discovery page's "Polite Backfill" button | base.html:291–328, 753–819; opener discovery.html:20, 559–563 | Discovery button | `lastKnownBackfillPage` in memory |
| UI-017 | Shared blacklist action | ACTIVE | Confirms via toast, animates the row/card out; fallback full reload | base.html:822–850; callers in series.html row partial:65, releases partials:89/193, discovery passim | button click in rows/cards | DB |
| UI-018 | Shared ignore / unignore release | ACTIVE | Ignore dims the row and disables the clicked button; unignore restores by reload | base.html:853–893 | row buttons | DB |
| UI-019 | Shared per-series backfill action | PARTIAL | Spins the button, POSTs `/api/series/backfill` (`max_pages:3`), toasts, force-shows banner + starts polling. No cancel; second click blocked only while the request is in flight | base.html:896–927; callers series row partial:58, series_card_chunk:85 | button click | server task |
| UI-020 | Global auth gate UI | ACTIVE | HTML → 302 `/auth/login`; `/api/*` → 401 JSON (browser shows raw JSON if fetched directly); header hides auth menu in `none` mode | main.py:239–267; base.html:197 | any request | cookies |
| UI-021 | Keyboard focus ring | ACTIVE | 2px rose outline with 2px offset on `:where(a,button,input,select,textarea,summary):focus-visible`; dropdown menu items get slate background focus | base.html:82–93 | keyboard | — |
| UI-022 | Custom scrollbars + word-break utility + cover shimmer | ACTIVE | Dark 8px scrollbars; `.break-words-anywhere`; shimmer placeholder class used on every cover | base.html:35–45, 72–80 | implicit | — |
| UI-023 | CDN-only frontend dependencies | PARTIAL | Tailwind CDN + Lucide `@latest` from unpkg; no vendored CSS/JS/fonts. Offline/air-gapped or CSP-restricted deployments render unstyled and icon-less; `latest` is unpinned | base.html:8,10 | page load | — |
| UI-024 | Invalid Tailwind color `slate-850` | PARTIAL | Cards/rows use `hover:bg-slate-850/50` and a `via-slate-850` gradient stop, but `slate-850` is not defined in the Tailwind config nor a Tailwind default → hover feedback silently missing on many rows; hero gradient mid-stop ignored | base.html:12–31; usage e.g. index.html:9, series_table_row_chunk:2, flat_release_row_chunk:2, series_card_chunk:149, downloads.html:233/396 | hover | — |
| UI-025 | Footer | ACTIVE | Brand dot, "MangaTrax v1.0.0", strapline, GitHub link; version string hardcoded | base.html:371–386 | — | — |
| UI-026 | Unused `{% block scripts %}` hook | VESTIGIAL | Base exposes a `scripts` block after all global JS; no page overrides it (all pages put JS inside `content`) | base.html:1146 | — | — |
| UI-027 | Dead "Manual Pickup" nav counter | VESTIGIAL | `<span id="nav-manual-pickup-count">` exists hidden in both desktop and nothing updates it; page does show count in its own header | base.html:172 | — | — |

### 3.2 Dashboard — `index.html`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-028 | Hero + Calibre connectivity chip | ACTIVE | Green "Calibre Connected (N Series • N Books)" or amber "Calibre DB Not Found (Check Path)" | index.html:9–32 | render | — |
| UI-029 | 5 KPI stat cards | ACTIVE | Monitored Series / Wanted Bundles / Active Downloads / In Library / Discovered, each with deep-link (series, wanted, downloads, in-calibre, discovery) | index.html:35–100 | click | — |
| UI-030 | Active downloader queue preview | ACTIVE | Up to 5 torrents with progress bar, speed, state, size; links to /downloads; hidden when none | index.html:103–133; `torrents[:5]` main.py:1165 | render | — |
| UI-031 | Wanted releases grid | ACTIVE | Up to 8 pending releases with cover (lightbox), bundle/volume/completion badge, feed, title, tag chips, missing volumes, date, Download button | index.html:136–233; chips 189–197 | render | — |
| UI-032 | Dashboard Download button | ACTIVE | POSTs `/api/releases/{id}/download`, toast, full reload after 1.5s (no in-place state update, unlike Releases page) | index.html:213–217, 266–281 | click | DB |
| UI-033 | Recent activity list | ACTIVE | 10 latest log rows with colour-coded event badge, message, details, local time; link to /logs | index.html:236–262; main.py:1145 | render | — |
| UI-034 | Manual-pickup alert on dashboard | VESTIGIAL | View computes `manual_pickup_count` (main.py:1152–1154) but never passes it; no banner/link, so held torrents are invisible on the landing page | main.py:1148–1166 | — | — |

### 3.3 Series — `series.html` + `partials/series_table_row_chunk.html`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-035 | Tracked-series table | ACTIVE | 7 columns (Status, Series, Authors, Owned, Latest Vol, Tags, Actions); rows injected from API | series.html:33–69; row partial 1–71 | render | — |
| UI-036 | Monitoring toggle | ACTIVE | Per-row check/x button POSTs `/api/series/{id}/toggle`, toast, reload after 0.8s | row partial:4–6; series.html:256–267 | click | DB |
| UI-037 | Series search + no-match empty state | PARTIAL | Client-side filter over loaded rows; friendly empty state with Clear search. Rows are auto-loaded in the background, so filtering can miss rows not yet fetched | series.html:21–24, 72–77, 194–233 | typing | DOM only |
| UI-038 | Auto infinite-scrolling row loader | PARTIAL | Skeleton first, then `/api/series/rows?offset&limit=50`; after each batch auto-schedules the next (`setTimeout(fetchNextSeriesRows, 60)`), and an IntersectionObserver on the load-more container repeatedly triggers loading — i.e. the whole table drains without user action, while a "Load More" button is also rendered (hidden) | series.html:101–192, 236–254; button 63–68; `series-row-deferred` machinery 171–192 is dead (no template emits that class) | DOMContentLoaded | in-memory offset |
| UI-039 | Sync Calibre button | ACTIVE | Only page with the Calibre sync trigger; spinner + disabled while synchronous `/api/calibre/sync` runs, toast, reload | series.html:25–28; base.html:516–539 | click | DB/task |
| UI-040 | Series link editor modal | PARTIAL | Inline modal to set `website_url`; save POSTs `/api/series/{id}/link`, toast, reload. No Escape close, no focus trap, no dialog role | series.html:80–96, 303–333 | row link button | DB |
| UI-041 | "Match to Calibre series" modal | ACTIVE | Search Calibre series (`/api/calibre/series`), pick target, "Link & merge"; updates owned count in the row and removes the "Not linked" button without reload | series.html:353–435; row partial:31–36 | row button | DB |
| UI-042 | Row action cluster | ACTIVE | Backfill (`backfillSeries`), edit link, blacklist; also cover thumbnail opens lightbox and series name may link out (`safe_url`) | row partial:8–26, 56–69 | click | — |
| UI-043 | Local `blacklistSeries()` duplicate | VESTIGIAL | Defined at series.html:335–350 but no call site; rows use the shared `blacklistSeriesGlobal` | series.html:335 | — | — |
| UI-044 | `series_list` server render branch | PARTIAL | Template supports rendering rows server-side (`{% if series_list %}` + include), but `series_view` always passes `[]` → branch unreachable until a JS fetch | series.html:48–49; main.py:1177 | — | — |

### 3.4 Releases — `releases.html` + partials `series_card_chunk`, `flat_release_row_chunk`, `series_release_rows_chunk`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-045 | View switcher (Group by Series / All Releases flat) | ACTIVE | Two links set `?view=` and reload; current mode styled brand-600 | releases.html:22–34 | link | URL `view` |
| UI-046 | Status filter pills | PARTIAL | 7 links: All/Wanted/In Calibre/In staging/Discovery/Completed/Ignored; each reloads. Pills **drop sort and search** when used (href only carries `view`+`status`) | releases.html:36–66 | link | URL `status` |
| UI-047 | Search box (debounced server search) | ACTIVE | 300ms debounce resets pagination and refetches cards/flat rows with `search=`; a separate client-side filter also hides non-matching loaded nodes and shows "N matches" (only among loaded rows) | releases.html:70–81, 267–275, 491–541 | typing | in-memory `currentSearch` |
| UI-048 | Sort select | ACTIVE | Recently updated / Latest release post / Series A–Z; refetches in place and writes `?sort=` via `history.replaceState` (no reload) | releases.html:75–79, 277–289 | change | URL `sort` |
| UI-049 | Load More (series + flat) with sticky duplicate | ACTIVE *(contract-pinned)* | Buttons render disabled from first paint, show "X remaining" + "Showing X of Y", flip to "Retry loading" on error, and a fixed bottom-right sticky button duplicates the action; counts come from server-rendered `initial_total` | releases.html:120–131, 166–173, 177–185; `updateLoadMoreUi` 221–244 | click | in-memory offsets |
| UI-050 | Series card with volume coverage | ACTIVE | Cover, romaji, In-Calibre/Discovery chip, total volumes, completed/publishing chips, release count, Track/Backfill/Blacklist, Releases accordion, owned/missing volume pill legend, synopsis | series_card_chunk:1–290 | render + click | — |
| UI-051 | Releases accordion + "Show all N releases" | ACTIVE | Card releases capped at 10 server-side; expanding shows them; "Show all" pages `/api/releases/series_releases` into the same container and removes itself when exhausted | series_card_chunk:139–147, 279–286; releases.html:291–311; cap `_SERIES_CARD_RELEASE_CAP` main.py:1301, 1369–1375 | click | DOM |
| UI-052 | Per-release download with live state | ACTIVE | Delegated click POSTs download; chip + button update in place; polls `/api/releases/{id}/state` every 1.5s while downloading/waiting/downloaded; card summary recomputed; **no reload** (test-pinned) | releases.html:586–682; chips in partials 234–238/61–63 | click | DB + timers |
| UI-053 | Ignore / unignore release | ACTIVE | Base shared helpers; ignore dims row to 35% | base.html:853–893; card 260–268; flat 183–191 | click | DB |
| UI-054 | Move release to different series | ACTIVE | Modal: search Calibre, create new title, or "Detach → Discovery"; removes the row on success; no Escape close / dialog semantics | releases.html:684–745, 748–770 | row button | DB |
| UI-055 | Prioritize release group | ACTIVE | Clicking a group chip POSTs `/api/prioritized/groups` then reloads | releases.html:566–584, card 169–171, flat 30–32 | click | DB |
| UI-056 | Track series from a card | ACTIVE | "Track" button POSTs `/api/discovery/track` then reloads | releases.html:543–564; card 79–84 | click | DB |
| UI-057 | Empty state + Clear filters | ACTIVE | Copy "No manga series found matching current filters." with Clear filters → `/releases` | releases.html:356–371, 313–316 | render | — |
| UI-058 | Deferred-row branches | VESTIGIAL | `filterReleases` and `DOMContentLoaded` branch on `.series-card-deferred` / `.flat-row-deferred`, but no server chunk ever emits those classes → dead conditionals | releases.html:477–489, 500–534 | — | — |

### 3.5 Prioritized — `prioritized.html`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-059 | Prioritized groups management card | ACTIVE | Lists group pills with release counts and remove buttons; quick-add form (name + optional notes); remove confirms with `confirm()` | prioritized.html:70–112, 518–556 | submit/click | DB |
| UI-060 | Groups add form on mobile | PARTIAL | Notes input is `hidden sm:inline-block` → not editable under 640px | prioritized.html:84–85 | — | — |
| UI-061 | View switcher + status pills | PARTIAL | Same pattern as Releases (links, full reload, filters reset each other); no sort control at all | prioritized.html:22–66 | link | URL |
| UI-062 | Search + "N matches" | ACTIVE | Client-side filter over loaded cards/rows only (`filterPrioritized`) | prioritized.html:114–120, 444–493 | typing | DOM |
| UI-063 | Load More + sticky control | ACTIVE | Same contract as Releases; cards from `/api/prioritized/cards`, flat from `/api/prioritized/flat_rows` (no search/sort params accepted server-side) | prioritized.html:149–215, 229–442 | click | offsets |
| UI-064 | Prioritized-card empty state | ACTIVE | "No prioritized manga releases found matching current filters." + Clear filters | prioritized.html:319–335, 277–279 | render | — |
| UI-065 | Dead deferred branches | VESTIGIAL | `.series-card-deferred` / `.flat-row-deferred` checks exist but no producer | prioritized.html:452–486 | — | — |

### 3.6 Discovery — `discovery.html`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-066 | Tab switcher (Discovered / Blacklisted) | PARTIAL | Plain buttons rewrite class lists; **not** aria-selected/role=tab; state not in URL → tab resets to Discovered on reload/navigation | discovery.html:27–39, 381–398 | click | DOM only |
| UI-067 | Discovery search (dual model) | PARTIAL | `oninput` filters only the current page's cards client-side; pressing Enter submits the GET form and re-queries the server. Two different result sets under one input | discovery.html:45–54, 400–408 | typing / Enter | URL `q` (form only) |
| UI-068 | Tag picker (include/exclude chips) | PARTIAL | Autocomplete listbox from `/api/tags`, chips with remove, Include/Exclude mode, Apply/Clear rewrite the URL. **No keyboard arrow navigation**, `aria-expanded` set true and never reset, no Escape/outside-click close | discovery.html:55–75, 573–639 | typing/click | URL `include_tags`/`exclude_tags`/`tag_match` |
| UI-069 | Sort select + hidden sort state | PARTIAL | `#discovery-sort` navigates immediately on change (dropping unapplied chips); `TagPicker.apply()` writes `#tag-sort-mode` (server value), which can overwrite a newer selection | discovery.html:62–67, 73–74, 567–571, 621–630 | change/Apply | URL `sort` |
| UI-070 | Page-size select + pagination | PARTIAL | Per-page 12/24/48/96; first/prev/page/next/last links. **Pagination links carry only `page`, `page_size`, `q`** — tag includes/excludes, `tag_match` and `sort` are lost on every page change | discovery.html:80–89, 231–276, 410–415 | change/link | URL |
| UI-071 | Track / Blacklist per discovery card | ACTIVE | Two-button grid; POST `/api/discovery/track` (with release id) or `/api/discovery/blacklist`; toast + reload after 0.6s | discovery.html:216–226, 426–522 | click | DB |
| UI-072 | MangaUpdates synopsis box + Fetch | ACTIVE | Truncated synopsis, click to expand/collapse, Fetch button POSTs `/api/discovery/{id}/fetch-info` and replaces text/link in place | discovery.html:198–211, 438–468 | click | DB |
| UI-073 | MangaUpdates external link | ACTIVE | Falls back to a MangaUpdates search URL when no stored URL | discovery.html:188–193 | click | — |
| UI-074 | Blacklist management tab | ACTIVE | Manual add form (name + reason), searchable table, "Unblock / Restore" per row | discovery.html:290–376, 417–424, 524–557 | submit/click | DB |
| UI-075 | Polite Backfill entry point | PARTIAL | The only opener of the global backfill modal lives here; on every other page the feature is unreachable (banner only shows if already running) | discovery.html:20–23, 559–563 | click | — |
| UI-076 | Prioritize release group chip | ACTIVE | Duplicate of the Releases implementation (same endpoint/toast/reload) | discovery.html:118–120, 470–488 | click | DB |

### 3.7 Feeds — `feeds.html`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-077 | Feeds table | ACTIVE | Name, Type, Enabled, URL, Auto-Download, Interval, Last Check (OK time / ERROR with tooltip), Actions | feeds.html:34–119 | render | — |
| UI-078 | Enable/disable toggle | ACTIVE | "On"/"Off" chip POSTs `/api/feeds/{id}/toggle`, toast, reload | feeds.html:61–71, 253–271 | click | DB |
| UI-079 | Add-feed modal + live test | PARTIAL | Name/type/interval/URL/auto-download; "Test Feed Live" POSTs `/api/feeds/test` and reports item count. No `config`/selector fields, so an "HTML Web Scraper" can be created but not configured; no Escape close/focus trap; empty-state also opens it | feeds.html:123–173, 175–237 | button/submit | DB |
| UI-080 | Delete feed | ACTIVE | `confirm()` then DELETE, toast, reload | feeds.html:96–101, 239–251 | click | DB |
| UI-081 | Poll Feeds | ACTIVE | Only call site; POSTs async feed check, toasts "started", then polls `/api/feeds/check/status` every 1.5s up to 15 min, toasts result, reloads; button spinner+disabled meanwhile | feeds.html:20–31; base.html:541–592 | click | poll timer |

### 3.8 Downloads — `downloads.html` (largest surface, 2106 lines)

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-082 | Auto-download diagnostics card | ACTIVE | Explains why auto-download is/isn't firing: global/engine/feeds/monitored counts, last-poll stats, "Why nothing fires" checklist, recent decisions; Refresh button; loads on DOMContentLoaded | downloads.html:6–19, 1556–1590 | load / click | — |
| UI-083 | Manual-pickup advisory banner | ACTIVE | Shows held-torrent count with "Open Manual Pickup →" link | downloads.html:21–26 | render | — |
| UI-084 | Downloader status card + in-app config | ACTIVE | Connection state, message, Configure In-App panel (URL/user/password/enabled), Save & Test; `testDownloader()` toast | downloads.html:56–115, 673–719 | click | DB settings |
| UI-085 | Awaiting-Calibre-series list | ACTIVE | Lists unknown series with file count/volumes, per-file Delete, "Match series" opening the matched-series modal | downloads.html:117–160, 1042–1141 | click | DB |
| UI-086 | Failed documents | ACTIVE | Scan/list unreadable/stuck files with Retry / Convert / Delete (two-step), Delete all (armed confirm), inline empty state | downloads.html:162–189, 1677–1836 | load + click | DB/FS |
| UI-087 | Active queue table (qBit or Torbox) | PARTIAL | 6 columns with progress/speed/ETA/state; per-row Retry/Remove (Torbox) or Pause/Resume/Delete (qBit). Torbox phase labels incl. fetching/ready/routing; qBit path is **not auto-refreshed** (poll starts only when `engine == 'torbox'`) | downloads.html:191–330; torbox render 946–991; poll gate 1209–1211 | render + poll | DB/remote |
| UI-088 | Torbox live queue polling | ACTIVE | On page load when Torbox engine, fetch `/api/downloads/status` every 1s while active else 5s; updates rows in place with sparkline, stale/reconnecting chip | downloads.html:752–1159 | timer | in-memory maps |
| UI-089 | Clear failed (Torbox) | ACTIVE | Two-step "click again" confirm inside button (`_queueConfirm`) then POST clear-failed, removes rows | downloads.html:768–793, 840–871 | click | DB/remote |
| UI-090 | Check now (Torbox poll) | ACTIVE | POST `/api/downloads/torbox/poll`, toast, refresh | downloads.html:872–880 | click | — |
| UI-091 | Raw downloads inventory table | ACTIVE | Name/files/size/age/series/state/matched-release/action; Manual match for awaiting rows; Delete; last-scan age label | downloads.html:332–437, 993–1040 | render | DB |
| UI-092 | Raw cleanup preview | ACTIVE | Audit duplicates/already-present, preview list, two-step multi-delete via `/api/downloads/inventory/clear`, per-row delete via `.../delete` | downloads.html:363–377, 1839–1973 | click | FS/DB |
| UI-093 | Rescan / Prune empty / Flatten staging | ACTIVE | Three buttons POST their endpoints, toast summary, then in-place refresh; two are task-bound (`downloads:prune`, `downloads:flatten`) | downloads.html:346–357, 1161–1205 | click | FS/DB |
| UI-094 | "Tag & move now" duplicate trigger | PARTIAL | Second button (`tagMoveNow()` → `triggerStagingTag()`) duplicates the header "Process Downloads" action; header one carries `data-task-kind="downloads:process"` so reattachment/busy state only works for one of the two | downloads.html:47–50, 358–360, 1207 | click | — |
| UI-095 | Staging directory groups | ACTIVE | `<details>` per series (open by default, collapse state persisted in localStorage), counts, tagged/untagged, intervention flags, optional filter "Needs naming intervention" | downloads.html:439–528, 2043–2061 | render + toggle | localStorage `staging-group-open:*` |
| UI-096 | Staging file actions | ACTIVE | Preview/Tag (opens tag modal), Fix naming (prefilled), Delete (two-step timer) per file | downloads.html:494–518, 1361–1430, 1593–1675 | click | FS |
| UI-097 | Duplicate purge (staging cleanup) | ACTIVE | Audit → checkboxes → Purge selected/all → inline confirm box → task watched via MangaTasks; results toast | downloads.html:530–558, 1432–1554 | click | FS/DB |
| UI-098 | ComicInfo `<Series>` audit & repair | ACTIVE | Audit endpoint fills a 7-column table with per-row diff, status badges, selectable rows; Repair Selected rewrites only chosen files | downloads.html:560–612, 1213–1360, 3370–3497 | click | FS |
| UI-099 | Preview/manual tag modal | PARTIAL | Loads `/api/tagger/preview`, editable filename/series/volume/title/writer/publisher/genre, saves `/api/tagger/manual-tag`, refresh on success; no dialog role/Escape/focus trap; long form on mobile | downloads.html:616–670, 1361–1430 | click | FS |
| UI-100 | Staging series match modal | PARTIAL | Search Calibre / new title, retag+link group via background task; no dialog role/Escape; nested inside `<summary>` interactions elsewhere | downloads.html:1975–2041, 2086–2105 | click | DB/FS |
| UI-101 | Mobile layout of downloads page | PARTIAL | Seven full-width sections stacked, 20+ actions, several 6–8 column tables inside `overflow-x-auto`; no tabs/anchor nav; longest page in the app | downloads.html:28–614 | — | — |

### 3.9 Manual Pickup — `manual_pickup.html`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-102 | Pickup cards with quota/expiry header | ACTIVE | Count/cap and expiry days; empty state when none | manual_pickup.html:4–24 | render | — |
| UI-103 | Fetch whole torrent / Fetch selected | ACTIVE | POST fetch (`all:true` / `file_indexes`), toast, watch task, inline error line, status + task panel poll | manual_pickup.html:40, 56, 125–156, 136–156 | click | DB/task |
| UI-104 | Retry strict mapping | ACTIVE | POST retry, task watch, inline error, keeps mapping on failure | manual_pickup.html:41, 157–171 | click | DB/task |
| UI-105 | Edit series (match modal) | PARTIAL | Opens shared match modal seeded with the **card title** (release/torrent title) rather than the stored series name; POSTs `/api/downloads/awaiting-series/match` | manual_pickup.html:42, 180–259, 262–281 | click | DB |
| UI-106 | Volume mapping editor | ACTIVE | Per-file suggested volume number input auto-saves mapping; wanted-volumes text field with "Save volumes"; select-all/fetch-selected counter | manual_pickup.html:47–79, 87–124 | change/click | DB |
| UI-107 | Discard pickup | ACTIVE | `confirm()`, POST discard, removes card | manual_pickup.html:43, 172–179 | click | DB |
| UI-108 | Live refresh | VESTIGIAL | No polling/refresh button; new pickups appear only after manual page reload (the Downloader page banner is the only cross-link) | manual_pickup.html (whole file has no timer/fetch-status) | — | — |

### 3.10 Logs — `logs.html`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-109 | Activity log list | ACTIVE | 150 newest rows, event-type badge, actor badge (scheduler/system/user), message, mono details, timestamp | logs.html:31–61; main.py:1865 | render | DB (limit fixed) |
| UI-110 | Client-side search | PARTIAL | Filters `data-text` of the 150 rendered rows only; no server search, no level/actor/date filter; empty state with Clear search | logs.html:20–23, 63–69, 85–105 | typing | DOM |
| UI-111 | Prune now | ACTIVE | POST `/api/logs/prune`, toast counts, reload | logs.html:25–27, 74–83 | click | DB |
| UI-112 | Log pagination / auto-refresh / export | VESTIGIAL | None implemented | — | — | — |

### 3.11 Settings — `settings.html`

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-113 | Sticky in-page section nav + scroll-spy | PARTIAL | 9 anchor pills (Calibre, Downloader, Staging, Schedules, Tracker, Webhooks, Groups, Maintenance, Advanced); IntersectionObserver highlights current, sets `aria-current`; horizontal scroll on <lg. **Advanced anchor is a dead link whenever `MANGATRAX_DEBUG_ACTIONS` is off** (its card is conditionally rendered) | settings.html:19–51, 802–845, 1543–1598 | scroll/click | — |
| UI-114 | Single giant settings form with sticky save bar | PARTIAL | ~45 fields in one form; one submit; "Unsaved changes" bar slides in on any input/change and saves from anywhere. No per-section save, no validation messages beyond native `required`, no discard/reset | settings.html:54–55, 847–865, 1581–1597; `saveSettings` 1030–1102 | input/submit | DB |
| UI-115 | Calibre path + Test Path | ACTIVE | GET `/api/calibre/test?path=`, toast series/book counts or error | settings.html:56–76, 872–885 | click | DB |
| UI-116 | Downloader settings + Test Connection | ACTIVE | URL/user/password/category/tags/enabled; test updates badge + toast | settings.html:78–135, 887–925 | click | DB |
| UI-117 | Engine + Torbox block | ACTIVE | Engine select (qbit/torbox), enable toggle, API key (masked), retention/concurrency/timeouts, 3 Test buttons (downloader, Torbox) and status badges | settings.html:136–246, 927–972 | change/click | DB |
| UI-118 | Staging/downloads paths + pipeline toggles | ACTIVE | Two path fields with inline not-writable warnings (`partials/storage_path_warning`), 6 boolean toggles (auto-download, auto-tag, archive conversion, chapter guard, non-manga filter, collected-batch inference) | settings.html:248–325, 266/272; partial 1–3 | change | DB |
| UI-119 | Schedules | ACTIVE | Calibre sync interval, feed check interval, backfill pages/delay, app timezone | settings.html:327–362 | change | DB |
| UI-120 | Tracker (Nyaa mirror) | ACTIVE | Nyaa base URL field; save migrates the seeded feed server-side | settings.html:364–386; main.py:4254–4262 | change | DB |
| UI-121 | Webhooks + per-target test | ACTIVE | Discord URL, generic/Apprise URL, two "Test" buttons with inline result box + toast, two notification toggles | settings.html:388–443, 974–1028 | click | DB |
| UI-122 | Prioritized groups management | ACTIVE | Table of groups with release counts + delete (confirm), add group form, link to /prioritized | settings.html:445–495, 1501–1541 | submit/click | DB |
| UI-123 | Maintenance tools | ACTIVE | Cards for: clear cover cache, clear discovery, clear descriptions (confirm), prune activity logs, log-to-file + rotation, network diagnostics (Copy JSON + Speed test), release-cache count + filtered clear, provider tags refresh, suspicious volume-range audit/repair, false-series audit/repair, light-novel audit/ignore/delete, alias overrides list, series-attribution audit/repair | settings.html:496–800; endpoint map §2.2 rows 25, 49–65, 66, 119, 124 | click | DB/FS |
| UI-124 | Network diagnostics/speed test | ACTIVE | Renders diagnostics JSON + Copy; speedtest button calls bounded endpoint, shows result, task-bound (`data-task-kind="speedtest"`) | settings.html:561–645, 1104–1150 | click | in-memory |
| UI-125 | Debug/Advanced + danger zone | PARTIAL | Only rendered when `debug_actions_enabled`; purge non-manga, repair re-keyed names, two-step "Reset All Application Data" (6s timeout). Endpoint returns 403 if env flag off — JS path still exists but is unreachable from a hidden section | settings.html:801–845, 1403–1499; main.py:4342–4353 | click | DB |
| UI-126 | Secret/password values in HTML | PARTIAL | qBittorrent password is rendered into `value=` in both settings and downloads config; Torbox key is masked server-side but still posts the mask as "(unchanged)" | settings.html:119; downloads.html:101; main.py:1879, 4248–4251 | render | DB |
| UI-127 | `settings-new-group-*` fields inside the giant form | ACTIVE | Add-group fields sit *inside* `settings-form`; Enter triggers saveSettings, not add-group (add uses a separate type=button) | settings.html:486–488; 1030 | click | DB |

### 3.12 Cross-cutting interaction primitives

| ID | Feature | State | Behavior | Implementation | Trigger | Persistence |
|---|---|---|---|---|---|---|
| UI-128 | Toast-only failure feedback | PARTIAL | Most fetch failures surface only as a toast; page-level fetch errors in Series/Releases are swallowed to `console.error` (series.html:162–165, releases.html:396–402) leaving a skeleton/last state with no retry affordance | see above | error | — |
| UI-129 | Native `confirm()` for destructive actions | PARTIAL | 20 call sites (feeds delete, torrent delete, duplicate purge, group remove, reset, etc.) use blocking browser dialogs; other destructive flows use in-page two-step confirmations (downloads, settings reset) → three different confirmation idioms | grep `confirm(`; e.g. downloads.html:742, feeds.html:240, prioritized.html:543 | click | — |
| UI-130 | Inline `onclick` with server data | PARTIAL | Server values are interpolated into inline handlers with HTML escaping only, e.g. `onclick="blacklistSeriesGlobal('{{ s.name|escape }}', this)"`; quotes inside names can break the handler (and toast `innerHTML` then re-renders the name). Newer code already prefers `data-*` + delegation | row partial:58/65; series_card_chunk:80/85/169; releases.html:677–682 | — | — |
| UI-131 | Full-page reload as the state-refresh default | PARTIAL | ~30 actions call `setTimeout(() => location.reload(), …)`; only releases/prioritized download + task panel + staging actions update in place. Reloads drop scroll position, expanded accordions, modals, and client-side filters | e.g. index.html:273, series.html:262, discovery.html:500, feeds.html:230, settings.html:1518 | action | — |
| UI-132 | Cover image lazy-loading conventions | ACTIVE | `loading="lazy" decoding="async"` + `cover-placeholder` shimmer + `/api/covers?url=` proxy with server SVG fallback | index.html:158; series_card_chunk:11; flat partial:5; discovery:147; series row:11 | render | — |
| UI-133 | URL query-state for filters | PARTIAL | Server-readable filters (`status`, `view`, `sort`, `q`, `page`, `page_size`, tag params) are in the URL, but links/pagination drop subsets of them and client-side filters are not reflected at all | §2.1, UI-046/070 | navigation | URL |
| UI-134 | Dark palette / visual tokens | ACTIVE | `brand` rose scale 50/100/500/600/700/900, `darkbg #0b0f19`, `darkcard #161e2e`, `darkborder #273549`; slate text scale; rounded-xl/2xl cards; uppercase 10–11px micro-labels; Lucide 14–16px icons; status colours (rose/emerald/teal/amber/cyan/violet/purple) | base.html:12–32, 95 | render | — |
| UI-135 | Typography scale | PARTIAL | Tailwind default sans stack; page titles 2xl, card titles sm–xl, body 11–14px, micro-labels 9–10px. No custom font, no fluid type; many 10px labels fall below comfortable mobile reading size | across templates | render | — |
| UI-136 | Route-level empty/error states | PARTIAL | Purpose-built empty states exist for series/releases/prioritized/feeds/logs/discovery/downloads/staging; there is **no** page-level error boundary (API failure leaves skeleton/spinner forever) and no styled 404/500 | §3.3–3.11 | render | — |

**Feature count: 136 (UI-001 … UI-136).**

---

## 4. Interaction inventory

Every interactive element that exists in the current templates, per page. "Handler"
is the JS function / `fetch` target. Feedback legend: **T** = toast, **R** = full
reload, **IP** = in-place DOM update, **C** = `confirm()` dialog, **N** = nothing
visible (silent).

### 4.1 Global shell (`base.html`)

| Control | Type | Handler → calls | Success feedback | Failure feedback |
|---|---|---|---|---|
| Brand tile / "MangaTrax" | link | `/` | page nav + progress bar | — |
| Overview | link | `/` | nav highlight `active_page=dashboard` | — |
| Library ▾ | button | `toggleDropdown` (local) | menu opens, `aria-expanded=true`; only one open at a time | — |
| Series / Releases / Prioritized | links | `/series`, `/releases`, `/prioritized` | parent highlight; `nav-link-loading` spinner + bar | — |
| Discover ▾ | button | `toggleDropdown` | as above | — |
| Discovery / Feeds | links | `/discovery`, `/feeds` | parent highlight | — |
| Downloads | link | `/downloads` | highlight | — |
| Manual Pickup (+ count badge) | link | `/downloads/manual-pickup` | **no** active highlight (active_page=downloads); badge never populated | — |
| System ▾ | button | `toggleDropdown` | menu | — |
| Logs / Settings | links | `/logs`, `/settings` | parent highlight | — |
| User menu (auth only) | button | `toggleDropdown` | shows actor + mode + Sign out | — |
| Sign out | link | `/auth/logout` → 302 `/` | redirect; cookies cleared | — |
| Hamburger | button | `toggleMobileMenu()` | menu opens below header; `aria-expanded`/label swap | — |
| Mobile menu links ×10 + Sign out | links | same routes | closes menu on outside click/Escape; nav highlight | — |
| Calibre sync banner Dismiss | icon button | `dismissCalibreSyncBanner()` | banner hidden, poll cleared, stays dismissed for the page lifetime | — |
| Calibre sync banner (implicit) | status region | 3s poll `/api/calibre/status` | on finish: T + R after 1.2s | silent (hides banner) |
| Global backfill banner Stop | button | `globalStopBackfill()` → POST `/api/backfill/stop` | T "Stopping backfill..." | T error |
| Global backfill banner (implicit) | status region | 2.5s poll `/api/backfill/status` | hidden on finish + T "completed" | silent |
| Tasks toggle | button | `MangaTasks.togglePanel()` | panel open/closed, `aria-expanded` synced, icons re-rendered | — |
| Tasks panel Cancel (per task) | button | `MangaTasks.cancel(id)` → POST `/api/tasks/{id}/cancel` | row refreshes via poll | T with server `detail` (only if response parsed) |
| Task panel (implicit) | live region | GET `/api/tasks?active=0&limit=20` every 1.5s + on `pageshow` | task list/progress/ETA; finished-task toast (5s) | silent |
| Backfill setup modal: Resume-from-page hint | link/span | `useResumePage()` | fills start page input | — |
| Backfill Start | button | `submitBackfillStart()` → POST `/api/backfill/start` JSON | closes modal, T, force-shows banner, starts poll | T (message or "Could not start") |
| Backfill Cancel / X | buttons | `closeBackfillModal()` | modal closes | — |
| Image lightbox backdrop / Close / Esc | overlay+button+key | `closeImageModal()` (Esc handler base.html:641) | modal closes, body scroll restored | — |
| Toast stack | transient | `showToast(msg, type)` | auto-remove at 4s | — |
| Progress bar | transient | `ProgressBar.start()/done()` on internal `<a>` clicks + `pageshow` | animates 15→92→100% | — |

### 4.2 Dashboard (`/`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| "View series / wanted / queue / acquired / discovery" | links | `/series`, `/releases?status=REQUESTED`, `/downloads`, `/releases?status=IN_CALIBRE`, `/discovery` | page nav | — |
| Downloader queue "Manage queue" | link | `/downloads` | nav | — |
| Wanted release cover | `<img onclick>` | `openImageModal` | lightbox | — |
| Wanted release Download | button | `downloadRelease(id)` → POST `/api/releases/{id}/download` | T "Sent release to downloader!"; **R** after 1.5s | T `data.message` / error |
| Recent activity "View all logs" | link | `/logs` | nav | — |
| Calibre status chip | static | — | shows connected counts / not found | — |

No filters, no tabs, no forms. (Manual-pickup count is computed but not surfaced — UI-034.)

### 4.3 Series (`/series`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| Search box | input `oninput` | `filterSeries()` (client-side over `.series-row[data-name]`) | hides rows; empty state + Clear search | — |
| Clear search | button | `clearSeriesFilters()` | input cleared, refilter, focus returned | — |
| Sync Calibre | button | `triggerCalibreSync()` → POST `/api/calibre/sync` | spinner→T→R 1.5s | T error |
| Monitoring toggle (per row) | button | `toggleMonitored(id)` → POST `/api/series/{id}/toggle` | T + R 0.8s | T only on exception; no toast for `status!=success` |
| Cover thumbnail (per row) | `<img onclick>` | `openImageModal(src, series name)` | lightbox | — |
| Series external link | link `safe_url` | new tab | — | — |
| "Not linked — Match to Calibre series" | button | `openSeriesCalibreMatch(id, name, btn)` → GET `/api/calibre/series?search=` | modal + ranked list | inline "Could not load Calibre series." |
| Match modal search | input `oninput` | `searchSeriesCalibreMatch()` | list re-renders; first item preselected | inline error |
| Match modal item | button | `selectSeriesCalibreMatch(id)` | updates selection (visual highlight only in list markup) | — |
| Match modal "Link & merge" | button | `submitSeriesCalibreMatch()` → POST `/api/series/{id}/match-calibre` | closes modal, T with target + rekeyed count, owned cell updated, button removed | inline `detail` |
| Match modal Cancel / X | buttons | `closeSeriesCalibreMatch()` | closes (no Escape) | — |
| Backfill (per row) | button | `backfillSeries(id,name,btn)` → POST `/api/series/{id}/backfill` | spinner, T, banner shown, polling; button restored 0.8s | T on exception |
| Edit link (per row) | button | `editSeriesLink(id,url,name)` | opens modal with current URL | — |
| Link modal Save | button | `saveSeriesLink()` → POST `/api/series/{id}/link` | T + R 0.8s | T on exception only |
| Link modal Cancel | button | `closeModalLink()` | closes | — |
| Blacklist (per row) | icon button | `blacklistSeriesGlobal(name, this)` → POST `/api/blacklist` | T, row fades/removes | T |
| Load More Series (hidden until error/empty-deferred) | button | `loadMoreSeriesRows()` → reveals `.series-row-deferred` (dead) else `fetchNextSeriesRows()` | rows appended, counter updated | button revealed, console error |
| (implicit) row loader | observer + self-chain | GET `/api/series/rows?offset&limit=50` | appends, auto-loads next batch every 60ms timeout | silent console error; no retry UI except button |

No forms, no tabs. Two modals.

### 4.4 Releases (`/releases`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| Group by Series | link | `/releases?view=series&status=…` | reload; mode styled | — |
| All Releases (Flat) | link | `/releases?view=flat&status=…` | reload | — |
| Status pills (All/Wanted/In Calibre/In staging/Discovery/Completed/Ignored) | links | `/releases?view=…&status=…` | reload; pill styled | — (sort/search lost) |
| Search box | input `oninput` debounced 300ms | `handleReleaseSearch()` → resets pagination → `fetchNextSeriesBatch`/`fetchNextFlatBatch` with `search=`; then `filterReleases()` | replaces container/table body; "N matches" counter | silent console error; load-more becomes "Retry loading" |
| Sort select | select `onchange` | `changeReleaseSort()` → `history.replaceState(?sort=)` + refetch | re-renders same view sorted | as above |
| Load More Series | button | `loadMoreSeries()` → GET `/api/releases/cards?status&offset&limit=8&search&sort` | appends cards; counter "Showing X of Y series"; disables while loading | label "Retry loading" via error flag |
| Load More Releases (flat) | button | `loadMoreFlat()` → GET `/api/releases/flat_rows?…&limit=50` | appends rows | same retry state |
| Sticky Load More | fixed button | `clickStickyLoadMore()` | same as current view's loader | — |
| Card Track | button | `handleTrackSeries(stable_name,this)` → POST `/api/discovery/track` | T + R 1s | T + re-enable |
| Card Backfill | button | `backfillSeriesGlobal` | T + banner | T |
| Card Blacklist | button | `blacklistSeriesGlobal` | T + card fades | T |
| Card releases accordion | button | `toggleReleasesAccordion(acc_id)` | show/hide rows, chevron rotates | — |
| Card group chip (non-prioritized) | button | `quickPrioritizeGroup(name)` → POST `/api/prioritized/groups` | T + R 1s | T |
| Card "Show all N releases" | button | `showAllSeriesReleases(this)` → GET `/api/releases/series_releases?series_key&offset&limit=10` | appends rows, label updates, removes at end | T "Could not load series releases" |
| Release row cover | `<img onclick>` | `openImageModal` | lightbox | — |
| Release feed/title/external links | links | new tab (`safe_url`) | — | — |
| Different series (per release) | button | `openDifferentSeriesModal(id, series, btn)` | modal | — |
| Different-series modal search | input | `searchDifferentSeries()` → GET `/api/calibre/series?search=` | list + first preselected | inline error |
| Different-series item | button | `selectDifferentSeries(id)` | selection state | — |
| Different-series Apply | button | `submitDifferentSeries(false)` → POST `/api/releases/{id}/different-series` | T, row removed, summaries refreshed | inline `detail` |
| Different-series Detach → Discovery | button | `submitDifferentSeries(true)` | T, row removed | inline error |
| Different-series Cancel / X | buttons | `closeDifferentSeriesModal()` | closes (no Escape) | — |
| Download / Retry release | button (delegated) | `downloadRelease(id, btn)` → POST `/api/releases/{id}/download` | chip/button updated, poll 1.5s, T | T + state restored |
| Ignore / Unignore release | icon button | `ignoreReleaseGlobal` / `unignoreReleaseGlobal` | T, row dimmed / reload 0.6s | T |
| Clear filters (empty state) | button | `clearReleaseFilters()` → `/releases` | reload | — |

### 4.5 Prioritized (`/prioritized`)

Same view/status pill links as Releases (prioritized.html:22–66). Additional:

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| New group name / notes | inputs (notes hidden <sm) | form `onsubmit=addPrioritizedGroup` | — | required name; native |
| Add Group | submit | `addPrioritizedGroup(e)` → POST `/api/prioritized/groups` | T + R 1s | T message |
| Remove group (× per pill) | icon button | `removePrioritizedGroup(id,name)` → confirm → DELETE `/api/prioritized/groups/{id}` | T + R 1s | T |
| Search box | input `onkeyup` | `filterPrioritized()` (client-side) | hides cards/rows; "N matches" | — |
| Load More Series / Load More Releases / Sticky | buttons | `loadMoreSeries`/`loadMoreFlat`/`clickStickyLoadMore` → `/api/prioritized/cards`, `/api/prioritized/flat_rows` | appends; counters | retry state |
| Empty state Clear filters | button | `clearPrioritizedFilters()` → `/prioritized` | reload | — |
| Card actions | buttons/links | identical shared handlers to Releases (`handleTrackSeries`, `backfillSeriesGlobal`, `blacklistSeriesGlobal`, `toggleReleasesAccordion`, `quickPrioritizeGroup`, `showAllSeriesReleases`, `openDifferentSeriesModal`, `ignoreReleaseGlobal`, `downloadRelease`) | as above | as above |
| Download release | delegated click | `downloadRelease` + `_priorityPoll` (own chip/button updater) | IP, T | T |

### 4.6 Discovery (`/discovery`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| Polite Backfill | button | `toggleBackfill()` → `openBackfillModal()` | modal; guards when already running (T) | silent on status fetch error |
| Tab: Discovered Manga | button | `switchTab('discovery')` | panel swap + tab styling by class rewrite | — |
| Tab: Blacklisted Series | button | `switchTab('blacklist')` | panel swap | — |
| Search input | input `oninput` + form GET | `filterDiscovery()`; Enter submits `/discovery?q=…&page_size=…` | client filter / server page | — |
| Clear search (×) | link | `/discovery?page_size=…` | reload | — |
| Tag filter input | input `oninput` | `TagPicker.search()` → GET `/api/tags?q&limit=20` | dropdown suggestions | "Could not load tags." |
| Tag mode select | select | read at add time (`include`/`exclude`) | chip colour differs | — |
| Sort select | select | `changeDiscoverySort(value)` → full navigation with `?sort=` | re-render sorted | — |
| Apply (tags) | button | `TagPicker.apply()` → `/discovery?...include_tags&exclude_tags&tag_match&sort` | reload | — |
| Clear (tags) | button | `TagPicker.clear()` → `/discovery?...` without tag params | reload | — |
| Tag chip × | button (dynamic) | inline `onclick` → remove from local selection + re-render | chip disappears | — |
| Per page select | select | `changePageSize(size)` → `?page_size=&page=1` | reload | — |
| First / Previous / Next / Last | links | `/discovery?page=&page_size=[&q]` | reload (tag/sort filters lost) | — |
| Track Series (card) | button | `handleTrackBtn` → `trackDiscovered(name,id)` → POST `/api/discovery/track` | T + R 0.6s | T only on exception |
| Blacklist (card) | button | `handleBlacklistBtn` → POST `/api/discovery/blacklist` | T + R 0.6s | T only on exception |
| Group chip | button | `quickPrioritizeGroup(name)` | T + R 0.6s | T |
| MangaUpdates link | link | external (`safe_url` / computed search) | new tab | — |
| Synopsis box | div click | CSS `line-clamp-3 hover:line-clamp-none` | expands on hover/tap | — |
| Fetch synopsis | button | `fetchMangaUpdatesInfo(id, btn)` → POST `/api/discovery/{id}/fetch-info` | text + link replaced, T; button restored | T |
| Manual blacklist name/reason | inputs | form `onsubmit=submitManualBlacklist` | — | — |
| Add to Blacklist | submit | `submitManualBlacklist(e)` → POST `/api/blacklist` | T + R 0.6s | T only on exception |
| Blacklist search | input | `filterBlacklist()` | filters rows | — |
| Unblock / Restore | button | `handleUnblockBtn` → `removeFromBlacklist(id,name)` → DELETE `/api/blacklist/{id}` | T + R 0.6s | T |

### 4.7 Feeds (`/feeds`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| Poll Feeds | button | `triggerFeedCheck()` → POST `/api/feeds/check`, then 1.5s status polls (max 15min) | T "started", T result, R 1.5s; spinner | T + re-enable |
| Add Feed (header + empty state) | buttons | `openAddFeedModal()` | modal | — |
| Feed name/type/interval/url/autodownload | inputs/select/checkbox | read by `submitAddFeed` | — | browser `required` |
| Test Feed Live | button | `testFeedLive()` → POST `/api/feeds/test` | T "Found N items." | T message |
| Save Feed | submit | `submitAddFeed(e)` → POST `/api/feeds` | T + R 0.8s | T |
| Cancel / modal × (none) | button | `closeAddFeedModal()` | closes; no X/backdrop/Escape | — |
| On/Off toggle | button | `toggleFeed(id, bool)` → POST `/api/feeds/{id}/toggle` | T + R 0.8s | T |
| Delete feed | icon button | `deleteFeed(id)` → confirm → DELETE `/api/feeds/{id}` | T + R 0.8s | T |

### 4.8 Downloads (`/downloads`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| Refresh diagnostics | button | `refreshAutoDownloadDiagnostics()` → GET `/api/downloads/auto-download/diagnostics` | summary/checklist/decisions | inline "Could not load diagnostics" |
| Open Manual Pickup | link | `/downloads/manual-pickup` | nav | — |
| Test Downloader Connection (header) | button | `testDownloader()` → POST `/api/downloader/test` | T | T |
| Process Downloads | button | `triggerStagingTag()` → POST `/api/staging/tag` | T summary, R 1.5s; spinner | T |
| Configure In-App | button | `toggleDownloaderConfig()` | panel show/hide | — |
| Config URL/user/password/enabled + Save & Test | inputs + button | `saveDownloaderConfig()` → POST `/api/downloader/configure` | T; in-place refresh when connected | T (warning type renders as info) |
| Test (config card) | button | `testDownloader()` | T | T |
| Failed docs "Scan for failed files" | button | `scanFailedDocuments()` → POST `/api/downloads/failures/scan` | list rendered, T | T |
| Failed docs Delete all | button | `armDeleteAllFailures()` → two-step box → `executeDeleteAllFailures()` → POST `/api/downloads/failures/delete-all` | T, list re-render | T |
| Failed doc Retry / Convert | buttons | `retryFailedDocument`, `convertFailedDocument` → POST `/api/downloads/failures/{id}/retry|convert` | T, re-render/refresh | T |
| Failed doc Delete | button | `deleteFailedDocument` → 2nd click `executeFailureDelete` → POST `.../delete` | T, row removed | T |
| Torbox Clear failed | button | `clearFailedTorbox()` → armed confirm → POST `/api/downloads/torbox/clear-failed` | T, rows removed | T |
| Torbox Check now | button | `checkTorboxNow()` → POST `/api/downloads/torbox/poll` | T, refresh | T |
| Torbox row Retry / Remove | buttons | `retryTorbox(id)` / `removeTorbox(id)` (armed confirm for remove) → POST retry/remove | T, row update/removal | T |
| qBit row Pause / Resume / Delete | buttons | `pauseTorrent`, `resumeTorrent`, `deleteTorrent` (confirm) → POST/DELETE `/api/downloader/...` | T + R 0.8s | T on exception only |
| Clean up raw duplicates | button | `openRawCleanupPreview()` → POST `/api/downloads/inventory/audit` | panel + summary + candidate list | inline error |
| Delete duplicates / already-present | buttons | `confirmRawCleanup(mode)` → armed box → `executeRawCleanup()` → POST `/api/downloads/inventory/clear` | T, rows removed | T |
| Raw row Delete | button | `deleteRawInventoryEntry` (2nd click) → POST `/api/downloads/inventory/delete` | T with bytes freed; refresh | T |
| Manual match (raw / awaiting) | button | `openManualMatch(series)` → GET `/api/calibre/series`; Apply → POST `/api/downloads/awaiting-series/match` | closes modal, T, list refreshed | inline |
| Rescan | button | `rescanDownloads()` → POST `/api/downloads/scan` | T with reset/Calibre counts; in-place refresh | T |
| Prune empty folders | button (task-bound) | `pruneEmptyFolders()` → POST `/api/downloads/prune-empty` | T, refresh | T |
| Flatten staging | button (task-bound) | `flattenStaging()` → POST `/api/downloads/flatten-staging` | T, refresh | T |
| Tag & move now | button | `tagMoveNow()` → `triggerStagingTag()` | duplicate of Process Downloads | T |
| Staging intervention filter | select | `filterStagingIntervention(mode)` | hides groups | — |
| Staging group summary / "Match series" | `<details>` + button | toggle; `openStagingSeriesMatch(source, btn)` → GET `/api/calibre/series`; Retag & link → POST `/api/downloads/staging-series/match`, task watch; summary title updated | T retagged count, task panel | inline error / T task.error |
| Staging row Fix naming / Preview-Tag / Delete | buttons | `openTagModal(rel, {fix:true,…})`; `openTagModal(rel)` → GET `/api/tagger/preview`; `deleteDownloadFile(this,'staging',rel)` (2nd click) → POST `/api/downloads/files/delete` | modal prefilled / modal / T + row removal + counters | inline error / T |
| Tag modal Save ComicInfo.xml | button | `saveManualTag()` → POST `/api/tagger/manual-tag` | T, closes, R 0.8s | T `detail` |
| Audit staging duplicates | button (task-bound) | `auditStagingDuplicates()` → GET `/api/downloads/staging-duplicates` + render | table + state text | inline error |
| Select all / row checkboxes | checkboxes | `toggleAllDuplicates`, `onchange` count | Purge buttons appear with counts | — |
| Purge selected / Purge all / Cancel | buttons | `purgeSelectedDuplicates`, `purgeAllDuplicates`, `cancelDuplicatePurge` → `executeDuplicatePurge()` → POST `/api/downloads/staging-duplicates/purge`, task watch | T totals, table cleared | T |
| Audit Series Tags | button (task-bound) | `auditSeriesTags()` → GET `/api/tagger/audit` | 7-column table + summary | inline error |
| Audit row checkbox | checkbox | `toggleAuditSelection` | Repair Selected count | — |
| Repair Selected | button (task-bound) | `repairSelectedSeries()` → POST `/api/tagger/repair-series` | T with field counts, re-audit | T |
| Inline modals: tag, manual match, staging match | overlays | open/close functions above | — | No Escape, no dialog role, no focus trap |

### 4.9 Manual Pickup (`/downloads/manual-pickup`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| Fetch whole torrent | button (task-bound) | `pickupFetchAll(id)` → confirm → POST `.../fetch {all:true}` | T started, `MangaTasks.watch`, status text + inline error updated | inline error / T |
| Retry strict mapping | button (task-bound) | `pickupRetry(id)` → POST `.../retry` | T started, task watch | inline error / T |
| Edit series | button | `pickupEditSeries(id)` → `openPickupMatch(card title)` → GET `/api/calibre/series`; Apply → POST `/api/downloads/awaiting-series/match` | T matched | inline error |
| Discard | button | `pickupDiscard(id)` → confirm → POST `.../discard` | T, card removed | T |
| Select all files | checkbox | `pickupToggleAll(box,id)` | toggles file checkboxes, button label count | — |
| Wanted volumes field | input | read by `pickupSaveVolumes(id)` → POST `.../volumes` | T "Volumes saved" | T `detail` |
| Save volumes | button | `pickupSaveVolumes(id)` | T | T |
| Fetch selected | button (task-bound) | `pickupFetchSelected(id)` → POST `.../fetch {file_indexes}` | T started, task watch | T "Select at least one file" |
| Per-file Use checkbox | checkbox | `pickupRefreshButton(id)` | updates "Fetch selected (N)" | — |
| Per-file suggested volume | number input `onchange` | `pickupSaveMapping(id)` → POST `.../map` | silent (no toast) | T `detail` only if response not ok |
| Match modal search / new title / Apply / Cancel | inputs/buttons | `loadPickupMatchSuggestions`, `submitPickupMatch`, `closePickupMatch` | as above | inline |

### 4.10 Logs (`/logs`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| Search logs | input `oninput` | `filterLogs()` (client-side) | rows filtered; empty state + Clear search | — |
| Clear search | button | `clearLogFilters()` | input cleared, filter reset, focus returned | — |
| Prune now | button | `pruneActivityLogs()` → POST `/api/logs/prune` | T counts or "Nothing to prune", R 0.8s | T `detail` |

### 4.11 Settings (`/settings`)

| Control | Type | Handler → calls | Success | Failure |
|---|---|---|---|---|
| 9 section nav pills | anchors | native hash scroll; IntersectionObserver scroll-spy sets `aria-current` | active pill styling | dead `#section-advanced` when debug off |
| Test Path (Calibre) | button | `testCalibrePath()` → GET `/api/calibre/test?path=` | T counts | T error |
| Test Connection (downloader) | button | `testDownloaderConnection()` → POST `/api/downloader/test` | badge Connected/version + T | badge Disconnected + T |
| Test Connection (Torbox) | button | `testTorboxConnection()` → POST `/api/downloader/torbox/test` | badge + inline plan/slots + T | badge + inline + T |
| Test Discord / Test Generic | buttons | `testSingleWebhook(type)` → POST `/api/settings/test-webhook` | inline result box + T | inline + T |
| Add prioritized group | button | `addPrioritizedGroupSettings()` → POST `/api/prioritized/groups` | T + R 1s | T |
| Delete prioritized group | icon button | `deletePrioritizedGroupSettings(id,name)` → confirm → DELETE | T + R 1s | T |
| Clear cover cache / Clear discovery / Clear descriptions | buttons | `maintenanceAction(action)` → confirm → POST `/api/maintenance/clear` | T message | T `detail` |
| Prune activity logs | button (task-bound) | `pruneActivityNow()` → POST `/api/logs/prune` | T + R 0.8s | T |
| Log-to-file toggle + rotation fields | inputs | part of `saveSettings` payload | — | — |
| Copy JSON (network) | button | `copyNetworkDiagnostics()` (clipboard) | T? (visual state) | silent/console |
| Run network speed test | button (task-bound) | `runNetworkSpeedTest()` → POST `/api/diagnostics/speedtest` | inline result + T | inline error + T |
| Release-cache series/status filters | selects | `updateReleaseCacheCount()` → GET `/api/maintenance/releases-cache?...` | count label updates | inline error |
| Clear release cache | button | `clearReleaseCache()` → confirm → POST `/api/maintenance/clear-releases` | T + R | T |
| Refresh provider tags | button (task-bound) | `refreshProviderTags()` → POST `/api/maintenance/refresh-tags` | T started | T |
| Audit / Repair volume ranges | buttons (task-bound) | `auditVolumeRanges()` → GET `.../suspicious-volume-ranges`; `repairVolumeRanges()` → POST `.../repair-volume-ranges` with selected ids | table + selection + T | inline |
| Audit / Repair false fuzzy matches | buttons | `auditFalseSeriesMatches`, `repairFalseSeriesMatches` | table + T | inline |
| Audit / Ignore / Delete light novels | buttons | `auditLightNovels`, `cleanupLightNovels(action,btn)` | table + T | inline |
| Remove series alias | button | `removeSeriesAlias(id)` → confirm → DELETE `/api/maintenance/series-aliases/{id}` | T + R | T |
| Audit / Repair series attribution | buttons | `auditSeriesAttribution`, `repairSeriesAttribution` | table + T | inline |
| Debug: Purge Non-Manga / Repair Re-keyed Names | buttons | `debugAction(action)` → confirm → POST `/api/debug/action` | T + R | T (403 body) |
| Reset All Application Data | button | `handleResetDataClick()` (2-click, 6s window) → POST `/api/debug/action {reset-all-data}` | T + redirect `/` | T, re-enable |
| Cancel reset | button | `cancelResetConfirmation()` | restores button | — |
| Save Settings (form + sticky bar) | submit ×2 | `saveSettings(e)` → POST `/api/settings` | T (or storage-path error toast); **stays on page**, no reload, save bar hides on submit | T error |
| Any field input/change | event | reveals sticky "Unsaved changes" bar | bar appears | — |

The Settings page has **no** per-section reset, no "test all", no search, and no way to
discard changes (bar disappears only on submit — reloads lose nothing because the page
is never reloaded after save, so the bar can reappear on the next keystroke).

### 4.12 Feedback conventions actually in use

- **Toasts** every action except: per-file mapping save (manual pickup), attribution
  audit failures, and page-level fetch failures (console only).
- **Full reload** after: dashboard download, series toggle/backfill/link/blacklist,
  feeds add/delete/toggle/poll, discovery track/blacklist/prioritize/blacklist-restore,
  prioritized add/remove group, downloads tag/flatten/prune/rescan/tag-modal save,
  settings groups/maintenance/debug. **No reload** after: releases/prioritized
  download, state polls, downloads in-place refresh, settings save.
- **Two-step confirmations** exist in: downloads Torbox remove/clear failed, failed-doc
  delete / delete-all, raw-cleanup delete, staging purge (inline box), settings reset
  (button text swap). All other destructive actions use native `confirm()`.

---

## 5. Frontend architecture & workings

### 5.1 Rendering model

- **No SPA, no router, no build step.** Every page is a full server render
  (`Jinja2Templates(directory=.../templates)`, main.py:280) that extends
  `base.html` via `{% extends %}` / `{% block content %}` / `{% block scripts %}`
  (scripts block unused, UI-026). Navigation = normal `<a href>` full page loads.
- **Heavy data is lazy-loaded after first paint.** Series, Releases, Prioritized,
  Discovery (per page), Downloads (status/inventory live), Logs (server-rendered,
  fixed) — the page shell renders fast and JS fills the body. `releases_view` and
  `prioritized_view` deliberately compute `initial_total` up front so the Load More
  control exists before the first API response (main.py:1187–1194, 1212–1219).
- **All page JS is inline** inside each template's `{% block content %}`, sharing
  globals defined in `base.html`. There are no JS modules, no bundler, no ES imports.
  Duplicate function definitions exist per page (e.g. `downloadRelease` is defined
  three times: index.html:267, releases.html:653, prioritized.html:594; `handleTrackSeries`
  twice; `quickPrioritizeGroup` three times).
- **Icons** are Lucide `<i data-lucide="name">` placeholders hydrated by
  `lucide.createIcons()` — called once at base load (base.html:400) and re-called
  after every dynamic HTML injection (dozens of sites). Icon names are not validated,
  so a typo renders an empty box.
- **Covers** all go through `/api/covers?url=` (main.py:1957–1977): disk cache,
  `Cache-Control: public, max-age=864000`, SVG fallback on failure. `loading="lazy"`
  + `decoding="async"` + `.cover-placeholder` shimmer are used consistently.
- **Static assets:** none are served by the app; no `StaticFiles` mount and no
  favicon. Tailwind and Lucide are CDN `<script>` tags (base.html:8,10), Tailwind
  configured inline (base.html:11–32), everything else is a `<style>` block
  (base.html:33–93).

### 5.2 Visual language / design tokens

| Token | Value | Where |
|---|---|---|
| Dark mode | `class="dark"` on `<html>`, `darkMode:'class'` in config; only dark styles exist | base.html:2, 13 |
| Page background | `#0b0f19` (`darkbg`, also body arbitrary class) | base.html:25, 95 |
| Card background | `#161e2e` (`darkcard`) | base.html:26 |
| Border colour | `#273549` (`darkborder`) but most borders use slate-800/700 | base.html:27 |
| Brand scale | rose: 50 `#fff1f2`, 100 `#ffe4e6`, 500 `#f43f5e`, 600 `#e11d48`, 700 `#be123c`, 900 `#881337` | base.html:17–24 |
| Accent usage | brand-600 = primary CTA; cyan = downloads/processor; amber = prioritized/maintenance; emerald/teal = success/owned/staging; rose = danger/wanted; violet = merge/different-series; purple = bundles; sky = publishing-complete; fuchsia = JPEG XL | across templates |
| Radii | `rounded-lg` (controls), `rounded-xl` (buttons/inputs), `rounded-2xl` (cards/modals) | across |
| Shadows | `shadow-lg/xl/2xl`, coloured `shadow-brand-600/30` on primary CTAs | across |
| Spacing/layout | `max-w-7xl` shell with `px-4 sm:px-6 lg:px-8 py-8`; card padding `p-4/5/6`; vertical rhythm `space-y-6` per page, `space-y-8` on dashboard | base.html:331; pages |
| Type | Tailwind `font-sans` (system stack), antialiased; page `h1` = `text-2xl font-bold tracking-tight`; section `h2` = `text-lg font-bold`; card `h3` = `text-sm font-bold uppercase tracking-wider`; body = `text-xs` (12px) and `text-[11px]`/`text-[10px]` micro-labels; numbers often `font-mono` | across |
| Brand gradient | header tile `from-brand-600 to-rose-400`; progress bar `from-rose-500 via-brand-500 to-amber-400` | base.html:98,107 |
| Motion | hover `transition-colors`, `hover:scale-105` on brand, `active:scale-95` on primary buttons, `animate-spin/pulse/bounce` for live states, cover shimmer keyframes | base.html:47–80 + pages |
| Invalid token | `slate-850` is referenced (hover rows, hero gradient) but **not defined** → silently no-op (UI-024) | base.html:12–31 vs index.html:9 etc. |

Config is minimal — no custom breakpoints, fonts, spacing, or plugins were added, so
all responsive behaviour uses Tailwind defaults (`sm 640 / md 768 / lg 1024 / xl 1280 /
2xl 1536`).

### 5.3 Shared JS modules / globals in `base.html`

| Global | Kind | Responsibility | Lines |
|---|---|---|---|
| `showToast(message, type)` | function | transient notification; success/error/info styles; `innerHTML` | 402–424 |
| `closeAllDropdowns` / `toggleDropdown` | functions | nav dropdown state + aria | 430–456 |
| `closeMobileMenu` / `toggleMobileMenu` | functions | mobile menu state + aria labels | 458–480 |
| document click/keydown handlers | listeners | outside-click close, Escape close + focus return | 482–514 |
| `triggerCalibreSync` | shared async | POST `/api/calibre/sync`; null-safe buttons/spinner; toast + reload | 516–539 |
| `triggerFeedCheck` | shared async | POST `/api/feeds/check`; waits via `waitForFeedCheck`; toast + reload | 541–576 |
| `waitForFeedCheck` | async loop | 1.5s poll of `/api/feeds/check/status`, 15 min cap | 578–592 |
| `triggerStagingTag` | shared async | POST `/api/staging/tag`; toast + reload | 594–617 |
| `openImageModal` / `closeImageModal` | functions | lightbox + body scroll lock | 619–639 |
| `globalStopBackfill` | async | POST `/api/backfill/stop` | 647–654 |
| `initGlobalBackfillPolling` | async | one status check, then 2.5s poll while running; completion toast | 656–689 |
| `initCalibreSyncBanner` | async | one check (`in_progress`/`initial_sync_done`), 3s poll while running, dismiss + auto-reload | 691–751 |
| `openBackfillModal` / `closeBackfillModal` / `useResumePage` / `submitBackfillStart` | functions | backfill setup modal + start | 753–819 |
| `blacklistSeriesGlobal` / `ignoreReleaseGlobal` / `unignoreReleaseGlobal` / `backfillSeriesGlobal` | shared async | row/card actions with optimistic DOM effects | 822–927 |
| `ProgressBar` | object | top loading bar state machine | 930–967 |
| `MangaTasks` | IIFE object | tasks poll/render/re-attach/watch/cancel/panel toggle; exposes `window.MangaTasks` | 1001–1145 |

Page-local globals worth knowing (they leak to `window` because scripts are classic):
`currentStatus`, `currentView`, `currentSearch`, `currentSort`, `seriesOffset`,
`flatOffset`, `_releaseStateTimers`, `_differentRelease`, `TagPicker`, `_tagModalFile`,
`_stagingDuplicates`, `_failedDocuments`, `_rawCleanupCandidates`, `_downloadsPollTimer`,
`resetDataClickCount`, etc.

### 5.4 Event delivery patterns (three co-existing styles)

1. **Inline `onclick`/`oninput`/`onchange`/`onsubmit` handlers** — the dominant style
   (~150 call sites). Server data is interpolated into JS string literals with HTML
   escaping only (UI-130).
2. **Direct property assignment after dynamic render** — e.g. tag-picker suggestion
   buttons get `button.onclick = ...` (discovery.html:608–610).
3. **Document-level delegation** — used for dynamically paged content:
   - Releases/Prioritized download buttons (`.download-release-btn`, releases.html:677–682,
     prioritized.html:615–620) — the only delegation that respects re-rendered chunks.
   - Everything else injected later (accordion rows, staging match results, table
     chunks) relies on inline handlers baked into the returned HTML.

There is **no** generic event bus, no store, no component lifecycle. State that must
survive a chunk fetch is either re-read from the DOM or stored in module globals.

### 5.5 Fetch patterns & error handling

- Pattern: `const res = await fetch(url, {method, headers: {'Content-Type':
  'application/json'}, body: JSON.stringify(payload)}); const data = await res.json();
  if (data.status === 'success') {...} else { showToast(...) }` — repeated ~60 times.
- **Inconsistent success keys** the client must know: `status:'success'` (most),
  `success:true` (`/api/calibre/sync`, `/api/downloader/test`, `/api/staging/tag`),
  `status:'started'` (backfill/backfill-series/feed check/pickup fetch),
  `status:'success'|'skipped'` (release download), `status:'partial'` (staging tag).
  Several handlers only check `status==='success'` and therefore silently ignore
  legitimate error responses (e.g. manual-pickup track, discovery track, series link
  save).
- **HTTP status is mostly ignored**; only newer code checks `res.ok`
  (manual_pickup, downloads, settings maintenance). Classic bugs are therefore
  possible on 400/422/403 (JSON `detail` shown only when parsed).
- **No global fetch error handler / no retry policy.** `try/catch` always ends in a
  toast (`Error: TypeError...`) or an empty `catch {}` (poll loops).
- **CSRF/auth**: in `oidc` mode HTML routes redirect to login and `/api/*` returns 401 JSON; in `basic` mode every unauthenticated request gets a 401 Basic challenge (`none` is open); mutating
  requests are subject to the auth middleware's Origin/Referer policy. UI never
  handles 401 specially, so an expired session during an inline action produces a
  JSON body inside a toast.
- **No abort controllers**: navigation away leaves in-flight fetches; polling timers
  are not cleared on `pagehide` (only `pageshow` restarts polls).
- **XSS surface in the shell**: `showToast` writes `innerHTML`; several messages embed
  feed-derived series names (base.html:832, 911; releases.html:554). Server-side
  `safe_url` protects anchors (test-pinned) but not toast text.

### 5.6 Polling / timers (all intervals that exist)

| Timer | Interval | Started by | Stops when | Evidence |
|---|---|---|---|---|
| MangaTasks `/api/tasks?active=0&limit=20` | 1.5s, forever | `DOMContentLoaded` on every app page | never (only page unload) | base.html:1140 |
| Backfill banner `/api/backfill/status` | 2.5s | initial check when running; also started by any backfill trigger | status not running | base.html:656–687 |
| Calibre sync banner `/api/calibre/status` | 3s | only when `in_progress` or `initial_sync_done===false` | sync done (`hideCalibreSyncBanner`) | base.html:715–749 |
| Feed check `/api/feeds/check/status` | 1.5s (max 15 min) | after `triggerFeedCheck` | check finished | base.html:578–592 |
| Release state `/api/releases/{id}/state` | 1.5s per release | after a download POST | terminal state | releases.html:637–651; prioritized.html:578–592 |
| Downloads live status (Torbox only) | 1s while active, else 5s | page load when `engine=='torbox'`; after every mutating action via `refreshDownloadsNow()` | never while on page | downloads.html:1143–1159, 1209–1211 |
| Task-specific `MangaTasks.watch(taskId)` | 1.5s | pickup fetch/retry, staging match, staging purge, raw cleanup | task inactive | base.html:1101–1118 |

Worst case on `/downloads` with Torbox: 1.5s tasks + 1s status + 2.5s backfill (if
running) + 3s Calibre banner = up to ~4 requests/s from one tab.

### 5.7 Lazy loading, pagination & caching-as-seen-by-the-UI

| Page | First paint | Paging model | Page size | Batch control | Cache |
|---|---|---|---|---|---|
| Series | skeleton row, fetch on DOMContentLoaded | offset/limit via `/api/series/rows`; **auto-chains** next batch 60ms after each success + IntersectionObserver on the load-more container | 50 | "Load More Series" button exists but is hidden unless error | none client-side; DB query cache (`db._grouped_cache`) |
| Releases (series view) | 2 skeleton cards, explicit `initial_total`, disabled Load More | offset/limit `/api/releases/cards`; explicit Load More (+sticky duplicate) | 8 series per batch, each card capped at 10 releases | button + sticky | none |
| Releases (flat) | table skeleton, disabled Load More | offset/limit `/api/releases/flat_rows` | 50 | button + sticky | none; server dedupes by signature per page |
| Prioritized | 3 skeleton cards / table skeleton, `initial_total` | same shape, `/api/prioritized/cards` (8) and `/api/prioritized/flat_rows` (50) | 8 / 50 | button + sticky | none |
| Discovery | full server render of one page | **server pagination** with first/prev/next/last + page-size select | 24 (6–120) | page links | none |
| Downloads | full server render + live patch | none for queue/inventory/staging | all rows | none (Refresh/Rescan force fresh) | server inventory table |
| Logs | 150 rows, no paging | none | 150 fixed | none | none |
| Feeds/Settings | full render | none | all | none | none |

`loadMore*` functions all gate on `isLoading*` and `hasMore*` flags and call the
single `updateLoadMoreUi()` helper that derives button label/disabled/remaining-count
and toggles the sticky control — the one genuinely well-factored paging state machine
in the codebase (releases.html:221–244, prioritized.html:248–269). Tests pin it (§5.11).

### 5.8 URL / query-state patterns

| Page | Server-read query params | JS-written params | Lost on navigation |
|---|---|---|---|
| Releases | `status`, `view`, `sort` | `sort` (replaceState) | sort/search when a status pill is clicked; search always (client-only) |
| Prioritized | `status`, `view` | — | same |
| Discovery | `page`, `page_size`, `q`, `include_tags`, `exclude_tags`, `tag_match`, `sort` | `page_size`, `sort`, tags via TagPicker, `q` via form | pagination links drop tags/sort; page-size uses current URL so keeps them |
| All others | none | none | modal/accordion/scroll state never in URL |

There is no client-side history management beyond Releases' `replaceState`; browser
Back returns to the previous full page (server state), which is consistent but loses
in-page context.

### 5.9 Auth-conditional UI

- Only **one** region is auth-conditional: header user menu + mobile-menu auth block
  (`{% if auth_mode and auth_mode != 'none' %}` base.html:197, 261). Everything else
  is shown regardless of actor/mode.
- `auth_actor()` and `auth_mode` are Jinja globals (main.py:315–317); the actor is
  displayed in the user menu only, never on actions/logs pages.
- In `basic` mode the login route redirects to `/` (no UI), so a failed basic auth
  produces the browser's native credential prompt — outside the app's visual language.
- Auth pages do not extend `base.html` because they are redirects/JSON; the Calibre
  banner and backfill banner guard `/auth/*` explicitly (base.html:332, 716).

### 5.10 Page → API mapping (what refreshes how)

| Page | Initial data | Live refresh | Mutations used |
|---|---|---|---|
| Dashboard | server render + 1s-bounded downloader snapshot (main.py:1149) | none (only reload) | release download |
| Series | server `total_series` + `/api/series/rows` batches | none | toggle, link, match-calibre, backfill, blacklist, sync-calibre |
| Releases | server `initial_total` + `/api/releases/cards|flat_rows` | release-state 1.5s after download | download, ignore/unignore, different-series, prioritize group, track series, backfill series, blacklist |
| Prioritized | server groups + `initial_total` + cards/flat rows | release-state 1.5s | download, ignore/unignore, prioritize groups CRUD, track series, backfill |
| Discovery | full server render | none | track, blacklist, fetch-info, prioritize group, backfill start/status |
| Feeds | full server render | feed-check 1.5s poll | add/delete/toggle feed, poll feeds, test feed |
| Downloads | full server render + `/api/downloads/status` (Torbox live) | 1s/5s status poll; tasks panel | ~30 endpoints (see §4.8) |
| Manual Pickup | full server render | tasks panel only | map, volumes, fetch, retry, discard, match |
| Logs | full server render (150) | none | prune |
| Settings | full server render (incl. storage/network probes, main.py:1881–1905) | none (all actions in place/reload) | settings save, tests, maintenance, debug |

### 5.11 Hard test constraints the redesign must respect or deliberately update

All UI tests are **source assertions over template text**; the E2E harness replaces
Jinja with a shim that returns `<!-- Rendered template <name> -->`
(tests/e2e/harness.py:164–176), so no test can observe real DOM/behaviour and there
is no browser automation in the repo. Any redesign that changes markup strings must
update these tests *in the same change*. Constraints (file → pinned contract):

**tests/test_nav_structure.py**
1. Desktop nav: exactly 5 `data-nav-item` values in order
   `overview, library, discover, downloads, system`; Overview/Downloads must be
   `<a>`; Library/Discover/System must contain `<button` + `data-dropdown-trigger`.
   The old 7 flat names must not appear as `data-nav-item` (lines 74–88).
2. Each dropdown: `aria-haspopup="true"`, `aria-expanded="false"`,
   `aria-controls="nav-menu-<name>"`, `id="nav-menu-<name>"`, `role="menu"`,
   `role="menuitem"`, child `nav-tab` class, correct child hrefs, and parent
   highlight expressions `active_page in ['series','releases','prioritized']` /
   `['discovery','feeds']` / `['logs','settings']`; amber for Prioritized (94–127).
3. All 9 routes present in desktop AND mobile nav (133–141).
4. No page actions in header/mobile/overflow: forbidden strings
   `onclick="triggerCalibreSync()"`, `onclick="triggerStagingTag()"`,
   `onclick="triggerFeedCheck()"` in header; forbidden `nav-menu-actions`,
   `more-horizontal`, `aria-label="More maintenance actions"` anywhere; Sync Calibre
   exactly once in series.html with `id="btn-sync-calibre"`/`id="icon-calibre-sync"`;
   staging trigger exactly once in downloads.html with `id="btn-tag-staging"`/
   `id="icon-staging-tag"`; shared function definitions stay in base.html (147–178).
5. Header must contain exactly **5** `<button` tags (3 nav dropdowns + user + mobile)
   — adding any header action/search/hamburger extra breaks the test (188–193).
6. Poll Feeds: exactly one call site `onclick="triggerFeedCheck()"` and it must be in
   feeds.html; definition in base.html; feeds button has `id="btn-check-feeds"`,
   `id="icon-feed-check"`; the feeds button block appears before `openAddFeedModal()`,
   contains `bg-slate-800` and no `bg-brand-600`; discovery must not contain
   `triggerFeedCheck` (206–241).
7. User menu: actor in header, Sign out inside `nav-menu-user`, no standalone logout
   link before the user dropdown (247–259).
8. Mobile menu: hamburger contract, headings `>Library<`, `>Discover<`, `>System<`,
   all 9 routes, no action calls, no `>Actions<`, no "Poll Feeds", and
   **at least 10 occurrences of `min-h-[44px]`** (265–287).
9. Dropdown JS: `function toggleDropdown(trigger)`, `function closeAllDropdowns(`,
   `function toggleMobileMenu()`, `function closeMobileMenu()`,
   `event.target.closest('[data-dropdown-menu]')`, `event.key !== 'Escape'`,
   `trigger.focus()`, `source.count("setAttribute('aria-expanded'") >= 4`,
   `nav-link-loading`, `link.closest('nav')` (293–309).
10. Every `active_page` literal for the 9 pages and the three parent-list expressions
    (315–328).

**tests/test_ui_polish.py**
11. Settings section nav: `id="settings-section-nav"`, `sticky top-20`, exactly the
    9 anchors in order Calibre/Downloader/Staging/Schedules/Tracker/Webhooks/Groups/
    Maintenance/Advanced, each with `href="#<id>"`, `id="<id>"`,
    `data-settings-nav="<id>"`; `IntersectionObserver`, `setActive(`,
    `aria-current`; save button inside `#settings-form`; `#settings-save-bar` exists
    and its button has `form="settings-form"`; `form.addEventListener('input'` and
    `'change'`; field ids `setting-calibre-path`, `setting-staging-dir`,
    `setting-qbit-url`, `setting-discord-url`, `settings-new-group-name` (39–81).
12. Calibre banner: `id="calibre-sync-banner"`, copy strings, `/api/calibre/status`,
    `initial_sync_done`, `in_progress`, exact `setInterval(checkCalibreSyncStatus, 3000)`,
    `dismissCalibreSyncBanner`, `location.reload()`, server guard
    `request.url.path.startswith('/auth/')` and JS guard
    `window.location.pathname.startsWith('/auth/')` (87–104).
13. Page-owned actions as in #4 (110–132).
14. Empty states: exact copy in releases ("No manga series found matching current
    filters."/"Clear filters"/`clearReleaseFilters`), prioritized (same with
    `clearPrioritizedFilters`), series (`id="series-filter-empty"`, "No manga series
    match your search", `clearSeriesFilters`, "No manga series found in Calibre
    library."), feeds ("No feeds configured yet", `openAddFeedModal()`), logs ("No
    activity logs recorded yet", `id="logs-filter-empty"`, `clearLogFilters`)
    (138–162).
15. A11y source test: **every** `<button>` in **every** template whose inner text is
    empty must have both `aria-label="` and `title="` in the opening tag; exact
    focus-visible selector
    `:where(a, button, input, select, textarea, summary):focus-visible`;
    `min-h-[44px]` present in base; `base.count('aria-expanded="false"') >= 4`;
    `aria-label="Open navigation menu"`; `closeMobileMenu()` (168–200).

**tests/test_template_markup.py**
16. No duplicate attribute (`class`, `id`, `data-release-id`, `data-task-kind`,
    `onclick`, `title`) inside any `<button|a|div|span|input|select|option|label|form|textarea>`
    tag in any template/partial (42–50).
17. Download buttons in `series_card_chunk.html` and `flat_release_row_chunk.html`
    must contain `download-release-btn` plus `bg-brand-600`, `rounded`, and `px-`/`p-`
    in the SAME `class` value; card button label `<span>Download</span>` (53–71).
18. No line-initial `<!--` inside any `<script>`; `downloads.html` must have exactly
    one `<script` and the `manual-match-modal` markup must appear **after** the first
    `</script>` (74–106).

**tests/test_downloads_page_views.py**
19. downloads.html must contain `(t.eta or 0)`, `(t.progress or 0)`, `(t.size or 0)`,
    `(t.speed_bps or 0)`, `(t.dlspeed or 0)`, `(t.downloaded_bytes or 0)`,
    `(t.total_bytes or 0)`; must NOT contain `t.eta > 0` or `(t.progress * 100)`.
    index.html must contain `(t.progress or 0)`, `(t.dlspeed or 0)`, `(t.size or 0)`
    (161–175).

**tests/test_releases_loadmore_availability.py + test_releases_pagination.py +
test_performance_and_lazy_loading.py**
20. Load-more controls render disabled from first paint:
    `id="btn-load-more-series" disabled`, `id="btn-load-more-flat" disabled`; labels
    `load-more-series-label`, `load-more-flat-label`; counters `deferred-series-count`,
    `deferred-flat-count`; `id="sticky-load-more"`, `clickStickyLoadMore`,
    `sticky-load-more-text`; `load-more-series-container` must not start
    `style="display: none;"`; `class="hidden px-5 py-2.5` forbidden on the series
    button (loadmore 32–78).
21. `function updateLoadMoreUi`, at least 8 `updateLoadMoreUi()` calls, error flags
    set/cleared, `p.error ? 'Retry loading'`, `updateAllCardSummaries();`, the literal
    comment "never block the Load More control"; filtering must not hide controls;
    `initial_total` consumed with the exact interpolation
    `{{ initial_total if current_view != 'flat' else 0 }}`; both fetchers contain
    `isLoadingSeries = false;\n        updateLoadMoreUi();` (and flat equivalent)
    (54–94).
22. No auto-chain/observer on releases or prioritized: forbidden
    `setTimeout(fetchNextSeriesBatch`, `setTimeout(fetchNextFlatBatch`,
    `setupInfiniteScroll()`; both pages must contain "Load More Series" and "Load More
    Releases"; releases must send `search=${encodeURIComponent(currentSearch)}`,
    `handleReleaseSearch`, `resetReleasePagination`, `showAllSeriesReleases`; card
    must contain `Show all {{ group.release_total }} releases` and `release_total`
    (pagination 108–125). **Note:** series.html is the opposite — it is expected to
    contain `series-row-deferred` + `loadMoreSeriesRows` + `IntersectionObserver`
    auto-scroll (performance 110–126); a redesign that removes series auto-loading
    must update that test.
23. base.html must contain `page-progress-bar`, `ProgressBar`, `nav-link-loading`;
    releases must contain "Load More Series" and must NOT contain
    `setupInfiniteScroll`; prioritized must contain `series-card-deferred` and
    `loadMoreSeries`; series must contain `series-row-deferred` and
    `loadMoreSeriesRows` (performance 97–127).

**tests/test_release_download_state.py**
24. Both partials: `release-state-chip`, `data-release-id=`, `download-release-btn`,
    `can_download`. releases.html: no `location.reload()` between
    `async function downloadRelease` and `</script>`; must contain
    `/api/releases/${releaseId}/state`, `setTimeout(poll, 1500)`,
    `updateReleaseStateDom`, `addEventListener('click'`, `Retry`,
    `updateAllCardSummaries`, `release-card-summary`. prioritized.html must contain
    `/state` and no reload inside its `downloadRelease` (84–108).

**tests/test_background_tasks.py**
25. base.html: `global-tasks`, `tasks-panel`, `tasks-active-count`, `MangaTasks`,
    `bindButton`, `watch`, `/api/tasks?active=0`, `setInterval(poll, 1500)`,
    `aria-live`, `tasks-toast`; `location.reload` must NOT appear inside the
    `MangaTasks` IIFE (158–168).

**tests/test_manual_pickup_polish.py + test_torbox_manual_pickup.py**
26. manual_pickup.html: no `prompt(`; must contain `pickup-match-modal`,
    `openPickupMatch`, `submitPickupMatch`, `pickup-match-search`,
    `pickup-match-new-title`, `pickup-match-list`,
    `/api/downloads/awaiting-series/match`, `pickup-error`, `task.error`,
    "mapping kept", `data-task-kind="torbox:pickup:retry"`; plus labels "Torbox
    Manual Pickup", "Fetch selected", "Fetch whole torrent", "Retry strict mapping",
    "Edit series", "Discard", `pickupSaveMapping`, `pickupSaveVolumes`,
    `data-task-kind="torbox:pickup:fetch"`; base must contain
    `/downloads/manual-pickup`; downloads must contain `manual_pickup_count` and
    "Open Manual Pickup"; settings must contain
    `setting-torbox-manual-pickup-enabled`, `-max-items`, `-expiry-days`,
    "0 = unlimited", "0 = never".

**tests/test_tracked_series_calibre_link.py**
27. series row partial: "Not linked — Match to Calibre series" +
    `openSeriesCalibreMatch`; series.html: `series-calibre-match-modal`,
    `searchSeriesCalibreMatch`, `submitSeriesCalibreMatch`,
    `/api/series/${_seriesCalibreMatch.id}/match-calibre`.

**tests/test_tag_display_gaps.py**
28. series row: exact `tag_display(s.tags_json, view='series')`,
    `tag_info.chips`, `tag_info.overflow`, `tag_info.tooltip`; index:
    `tag_display(r.tags_json, view='dashboard')`, chips, overflow.

**tests/test_security_remediation.py**
29. discovery.html, flat row, series card, series row must contain `|safe_url` and
    must not render bare `href="{{ r.link }}"` / `href="{{ item.link }}"` /
    `href="{{ s.website_url }}"`.

**Feature-suite strings (downloads/settings)** — many suites additionally pin ids and
copy that any redesign must preserve, notably: auto-download diagnostics
(`auto-download-diagnostics`, `auto-download-summary`, `auto-download-checklist`,
`auto-download-decisions`), failed docs (`failed-documents-card/body/empty/confirm`,
`btn-delete-all-failures`), raw cleanup (`raw-cleanup-panel/summary/list/confirm`,
`btn-clear-raw-duplicates`, `btn-clear-raw-present`), staging duplicates
(`staging-duplicates-state/wrap/body/confirm`, `btn-purge-selected-duplicates`,
`btn-purge-all-duplicates`, `staging-dup-select-all`), file delete
(`file-delete-btn`, `deleteDownloadFile(`, `data-relative`), staging groups
(`staging-series-group`, `data-needs-intervention`, `staging-group-count`), Torbox
queue (`torbox-queue-body`, `torbox-row-`, `torbox-reconnecting`, `torbox-updated`,
`btn-clear-failed-torbox`), tag modal (`tag-modal`, `tag-field-*`), and settings
maintenance ids (`btn-maint-*`, `btn-audit-*`, `btn-repair-*`, `release-cache-series`,
`release-cache-statuses`, `settings-save-bar`, `btn-reset-data`).

### 5.12 Presentation-layer security constraints (already enforced)

- Every dynamic external `href` must pass through the `safe_url` Jinja filter
  (main.py:300; test_security_remediation.py:328–341).
- Store/display split: series displayed name may be AniList English, but every action
  must be wired to `stable_name` (series_card_chunk comment 75–77; code uses
  `group.stable_name or group.series_name`).
- Cover images are proxied (no arbitrary remote `<img>`), and URLs are validated
  server-side by `/api/covers`.
- Server sets `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`,
  `Referrer-Policy` on every response (main.py:269–277); **no CSP** is set, which is
  why inline scripts/CDN remain possible.

---

## 6. Responsive & mobile behaviour (as-is)

### 6.1 Breakpoints actually used

Tailwind defaults only (no custom screens in base.html:12–32). Counts are approximate
occurrences across templates.

| Breakpoint | px | What changes |
|---|---|---|
| (base) | 0–639 | Mobile: hamburger; stacked page headers; 2-column KPI grid; 1-column discovery/prioritized grids; tables horizontally scroll; load-more + save-bar + tasks + toasts all fixed bottom-right; settings section nav horizontal scroll; card internals stack |
| `sm` | 640 | Many headers become rows (`sm:flex-row`), inputs gain fixed widths (`sm:w-64`), settings notes input appears (`hidden sm:inline-block`), discovery "Showing X to Y" line appears (`hidden sm:block`), inline button labels appear (`hidden sm:inline` in cards) |
| `md` | 768 | Desktop nav replaces hamburger (`hidden md:flex` / `md:hidden`); mobile menu force-hidden; auth user menu appears; series cards become side-by-side (cover left); dashboard downloader grid 2 cols |
| `lg` | 1024 | Page header rows (`lg:flex-row` on releases/prioritized/downloads); dashboard 5-col KPI grid; settings becomes `[210px_minmax(0,1fr)]` two-column with sticky nav; discovery grid 3 cols |
| `xl` | 1280 | Dashboard wanted grid 4 cols only |

There is no tablet-specific design, no `xs`, no orientation handling, and no
container-query usage.

### 6.2 What is hidden, stacked, or scrolled on mobile

- **Hidden on mobile:** desktop nav + dropdowns + user menu; row/button text labels
  (`hidden sm:inline` in cards/rows); prioritized group notes field; discovery
  "Showing X to Y" summary; some column-ish metadata (truncated instead).
- **Stacked:** every page header (`flex-col`), downloads/inspection card blocks,
  series cards (cover on top), action clusters wrap under content, manual-pickup
  cards, settings cards.
- **Horizontal scroll:** every `<table>` is wrapped in `overflow-x-auto`
  (series, feeds, releases-flat, downloads queue/inventory/staging/failed-docs/
  duplicates/audit, discovery blacklist, prioritized-flat, manual-pickup files).
  No table has a mobile card fallback, sticky first column, or min-width guidance, so
  action columns (right-aligned) are usually off-screen at 360–390px and users must
  scroll horizontally to find Download/Ignore/Delete.
- **Tap targets:** only mobile-nav links enforce `min-h-[44px]` (10 links). Row icon
  buttons are `p-1.5` (~28px), monitoring toggles `w-7 h-7` (28px), cover thumbnails
  32×48 (series table), 64×96 (discovery/dashboard), 96×144 (series cards), table
  checkboxes default size, `Tasks` button ~32px tall, banner dismiss buttons ~28px.
  None meet the 44×44 guidance except the mobile menu and primary page buttons.
- **Fixed overlays compete bottom-right:** `#toast-container` (`bottom-5 right-5`,
  z-50), `#global-tasks` (`bottom-4 right-4`, z-50), `#sticky-load-more`
  (`bottom-5 right-5`, z-40 on Releases/Prioritized), `#settings-save-bar`
  (`bottom-5 right-5`, z-40). On phones these overlap: the Tasks widget covers the
  toast origin and can sit on top of the sticky Load More / save bar. There is no
  safe-area inset (`env(safe-area-inset-bottom)`) handling.
- **Long pages:** `/downloads` (~7 major sections, 20+ actions) and `/settings` (~45
  fields in 9 cards) are the worst mobile scrolls; Settings has the sticky section
  nav + save bar, Downloads has neither tabs nor in-page nav.
- **Modals:** all are centered overlays with `p-4`; tall content (tag modal 8 fields,
  settings page) can exceed small viewports. Only the image lightbox locks body
  scroll; background scrolling behind other modals is possible. No `max-h`/scroll
  container on modal bodies in some cases (tag modal is fine at ~500px, but on a
  568px-tall landscape phone it will clip).
- **iOS specifics:** no `-webkit-tap-highlight-color` handling, no `inputmode`
  hints, numeric inputs are `type="text"` in places (backfill fields are
  `type="number"`, manual-pickup volumes `type="number"`), no autocomplete attributes
  on credentials.
- **Hover-only affordances:** discovery synopsis expands via CSS `line-clamp-3
  hover:line-clamp-none` — on touch there is no hover, and the element is not
  focusable, so the full synopsis is effectively unreachable on phones; row/card
  `hover:bg-slate-850` feedback is both invalid (UI-024) and hover-only.

### 6.3 Known mobile breakages / awkwardness (code-grounded)

1. `hover:line-clamp-none` synopsis expansion never fires on touch
   (discovery.html:208).
2. Sticky Load More sits under the Tasks widget; on iPhones with browser chrome the
   button can be under the home indicator (`bottom-5` without safe-area)
   (releases.html:179, base.html:390).
3. Settings sticky section nav is `sticky top-20` (80px) but the header is 64px tall
   — 16px gap; on mobile the horizontally scrolling pill row has no fade/scroll hint
   (settings.html:21–23).
4. Settings "Unsaved changes" save bar can cover the Tasks widget's bottom area and
   the last form field on small screens (settings.html:860).
5. Discovery filter bar is four controls in a row (`flex items-center gap-2`) that
   does not wrap at 360px — tag input + mode select + sort select + Apply + Clear
   overflow and force horizontal page scroll (discovery.html:56–70).
6. Releases summary bar (search input + sort select + match counter) has no wrap
   strategy (`flex items-center gap-3`), squeezing the input to ~120px on phones
   (releases.html:71–81).
7. Series page auto-loading drains all rows while the user is still at the top, on
   mobile data this is a large serial fetch (50 rows/page) with no user intent
   (series.html:156–161, 236–246).
8. Manual Pickup volume inputs (`w-20`) and file checkboxes inside a 5-column table
   are very hard to hit on a phone (manual_pickup.html:59–79).
9. Discovery cards' cover is 64×96 with `onclick` — a 64px-wide tap target competing
   with the card's title link (discovery.html:147).
10. Mobile nav does not close when the same page link is tapped (it reloads anyway)
    and there is no scroll lock while it is open, so the page behind scrolls
    (base.html:221–273).

---

## 7. Accessibility as-is

### 7.1 What exists

| Item | Evidence |
|---|---|
| Uniform keyboard focus ring (2px rose + offset) on links/buttons/inputs/selects/textareas/summary | base.html:82–86 |
| Dropdown menu items get a distinct focus background | base.html:87–92 |
| `aria-haspopup`, `aria-expanded`, `aria-controls` on 3 nav dropdowns, user menu, hamburger, Tasks toggle | base.html:129/133/149/153/177/181/199/203/212/391 |
| `aria-label` on hamburger, Calibre banner dismiss, Tasks toggle; mobile menu nav landmark label | base.html:212, 340, 391 |
| `role="menu"` / `role="menuitem"` on dropdown panels/items | base.html:134–190 |
| Escape closes dropdowns/mobile menu and returns focus to trigger | base.html:501–514 |
| Image lightbox close button labelled + title | base.html:281 |
| Calibre banner is `role="status"` `aria-live="polite"`; tasks panel + tasks toast are `aria-live="polite"`; modal match lists are `aria-live="polite"` | base.html:335, 396, 397; series.html:361; releases.html:756; downloads.html:2073, 2094; manual_pickup.html:270 |
| Settings section nav has `aria-label="Settings sections"`, `role="tablist"`, `aria-current` on active | settings.html:21–22, 1552–1563 |
| Icon-only button rule enforced by test (every icon-only button must have `aria-label` + `title`) | test_ui_polish.py:168–200 (passing ⇒ currently true for all buttons) |
| `alt` text on covers (generic "Cover", series name in cards, "Enlarged Cover" in lightbox) | index.html:158; series_card_chunk:9; base.html:285 |
| Lazy images with `loading`/`decoding` | UI-132 |
| `aria-label` on several selects/inputs (tag filters, sort, staging filter, per-pickup volumes) | discovery.html:57–62; downloads.html:458; manual_pickup.html:54, 269, 273 |

### 7.2 What is missing / broken

1. **No skip link** and no landmark labels beyond `main` being a `<main>` element
   (base header/nav/footer are semantic but there is no "skip to content").
2. **No `aria-current="page"` on navigation links** — active page is colour-only
   (base.html:122 etc.; only the settings in-page nav sets aria-current).
3. **Modals are not dialogs:** `#modal-backfill-setup`, `#modal-add-feed`,
   `#tag-modal`, `#series-calibre-match-modal`, `#different-series-modal`,
   `#staging-series-match-modal`, `#pickup-match-modal` have no `role="dialog"`,
   no `aria-modal`, no `aria-labelledby`, no focus trap, no initial focus, no focus
   restoration. Only `#manual-match-modal` has `role="dialog" aria-modal="true"`
   (downloads.html:2065).
4. **Escape only closes the lightbox and menus** (base.html:641–643, 501–514). Every
   other modal requires mouse/tab to the close button.
5. **Covers/lightbox trigger is a bare `<img onclick>`** — not keyboard focusable,
   no role, no key handler; screen readers get generic alt and no hint that it opens
   a viewer (index.html:158, discovery.html:147, partials).
6. **Toast container has no live region** (`#toast-container` base.html:276), so
   success/failure of nearly every action is silent for screen-reader users.
7. **Tables have no `<caption>`, no `scope="col"`, no sortable/aria-sort semantics**
   (all table templates).
8. **Heading hierarchy skips:** Settings and Downloads use `h3`/`h4` as section
   headings under a single `h1` with no `h2`; modals use `h3` with no preceding `h2`
   (settings.html:58, downloads.html:9; modals passim).
9. **Search inputs lack labels/aria-labels:** series search, releases search, logs
   search, prioritized search, blacklist search, batch search rely on placeholder
   text only (series.html:23; releases.html:73; logs.html:23; prioritized.html:117;
   discovery.html:330).
10. **Form labels are mostly not associated** — settings uses `<label>` without
    `for=` next to inputs (`settings.html:67–69`, `109–110`, etc.) except checkboxes;
    the feed modal labels have no `for` (`feeds.html:133–134`).
11. **Fake menu semantics without keyboard support:** `role="menu"`/`menuitem`
    dropdowns and the tag-picker `role="combobox"`/`listbox` have no arrow-key
    navigation, no `aria-activedescendant`, tag `aria-expanded` is left `"true"`, and
    role=menu links are tab-focusable (mixed interaction model).
12. **Tabs are not ARIA tabs:** Discovery "Discovered / Blacklisted" buttons have no
    `role="tab"`, `aria-selected`, or controlled `aria-controls`; panels lack
    `role="tabpanel"` (discovery.html:27–39).
13. **Small type and low-contrast micro-copy:** `text-[9px]`–`text-[10px]` with
    `text-slate-500` on `#0b0f19` (e.g. footer, table micro-labels) is below common
    AA guidance for body text at that size.
14. **Colour-only status encoding:** wanted/owned/ignored/downloading states are
    colour + icon + text in most places (good), but archive/duplicate/volume pills
    sometimes rely on fill colour only (e.g. volume pills green vs amber carry text
    "Vol N" only → missing/owned distinguished by colour alone; title tooltip is
    mouse-only).
15. **No reduced-motion support:** `animate-pulse`, `animate-spin`, `animate-bounce`,
    shimmer, progress bar and `transition-all` run regardless of
    `prefers-reduced-motion` (base.html:47–80 + pages).
16. **No `aria-busy`** while a panel loads/skeleton is shown; skeleton rows are
    `div`s with no status text for AT users (series.html:51–58).
17. **Native `confirm()`/`prompt`-free but blocking dialogs** (20 `confirm()` uses)
    are announced but cannot be styled/localized; the in-page two-step confirms are
    not announced as changes (downloads.html:1518, settings.html:1439).
18. **Icon-only actions rely on `title`** for meaning; on mobile `title` never
    displays, so Delete/Pause/Remove/Blacklist icons are unlabelled visually (the
    `aria-label` helps AT but not sighted mobile users).
19. **Focus management after dynamic render** is absent: after Load More, modal
    open/close, row removal, and tab switch, focus stays where it was (often on a
    removed node → `document.body`).
20. **`<details>` staging groups** are keyboard-usable natively, but the "Match
    series" button inside `<summary>` calls `preventDefault()/stopPropagation()`
    (downloads.html:482) — keyboard activation works, but the interaction is
    non-standard (a button nested in a disclosure control).

---

## 8. Usability gap audit

Severity: **C**ritical (blocks or loses work / cannot be discovered), **H**igh (common
task takes clearly more effort / error-prone), **M**edium (friction/consistency),
**L**ow (polish). Device column: B=both, M=mobile-worse, D=desktop-worse.

| # | Page | Gap (code-grounded) | Sev | Device | Why it matters |
|---|---|---|---|---|---|
| G-01 | Global shell | Four fixed bottom-right overlays collide: toasts (base.html:276), Tasks (390), sticky Load More (releases.html:179, prioritized.html:210), Settings save bar (860); no safe-area handling | H | B | Mobile users lose the primary paging/save affordance under the Tasks widget; toasts appear behind it |
| G-02 | Global shell | Manual Pickup is a 6th top-level nav item with no active state and an always-empty count badge (base.html:169–173, 172); tests only pin 5 `data-nav-item`s so the extra item is unpoliced | H | B | The requirement "5-item grouped nav" is factually not met; users cannot tell they are on the page; held-torrent count never advertises urgency from the shell |
| G-03 | Global shell | Header has no global entry point for the highest-value background actions (Sync Calibre, Process/Tag downloads, Poll feeds) — deliberate (tests pin it), but those actions are only on 3 pages, and Manual Pickup/backfill only on 1–2 pages | H | B | Discoverability depends on remembering which page owns an action; mobile users must go to Series to sync Calibre |
| G-04 | Global shell | Background backfill can start only from Discovery → Polite Backfill; the banner can stop it everywhere but cannot start it (base.html:346–365 vs discovery.html:20) | M | B | Users seeing "no releases" have no visible way to backfill from Releases/Prioritized |
| G-05 | Global shell | Tasks polling is a permanent 1.5s request on every page, plus page-specific polls; no pause/visibility check | M | B | Battery/data drain, constant load; on mobile this keeps the radio awake |
| G-06 | Global shell | Toast messages are HTML-injected and sometimes contain feed-derived names; no escaping (base.html:402–424, 832) | H | B | Broken markup/notification spoofing; potential stored-XSS path via feed-controlled titles |
| G-07 | Global shell | Coverage failures are silent: page-level fetch errors only `console.error` (series.html:162, releases.html:396); toast container not announced (UI-010) | H | B | User sees a skeleton or stale list and no explanation; AT users hear nothing |
| G-08 | Global shell | No styled 404/500/403 pages; HTML auth redirect is the only "error page" | M | B | Direct URL typos or expired-session fetches surface raw JSON |
| G-09 | Dashboard | `manual_pickup_count` computed but never surfaced (main.py:1152–1166) | M | B | Held torrents are invisible on the landing page; mismatch with Downloads banner |
| G-10 | Dashboard | Download button triggers full-page reload after 1.5s from the landing page, while the Releases page updates in place; no state chip on dashboard cards | M | D | Inconsistent feedback; user loses scroll position on the busiest page |
| G-11 | Series | Infinite auto-load drains every series page (50 at a time) with no user intent, while a (hidden) "Load More" button and a filtered empty state coexist; `series-row-deferred` branches are dead (series.html:156–192, 236–254) | H | M | Unbounded data use on mobile, scrollbar keeps growing, dead code confuses future work; contradicts explicit paging on Releases |
| G-12 | Series | Search only filters rows already fetched, and rows keep arriving in the background → "no results" can be false negative (series.html:194–233) | H | B | User may conclude a series is untracked when it is simply not loaded yet |
| G-13 | Series | "Not linked — Match to Calibre series" is a 10px amber text link inside a table cell (row partial:31–36); on mobile it is inside a horizontally scrolled table | H | M | High-value repair action is hidden and tiny |
| G-14 | Releases | Status pills/view switcher are full-page links that reset `sort` and search; each click costs a reload (releases.html:22–66) | H | B | Comparing statuses is slow; filters do not compose |
| G-15 | Releases | Two independent search/filter layers: debounced server search + client-side `filterReleases` hiding loaded nodes; "N matches" counts only loaded rows (releases.html:491–540) | M | B | Counts are misleading and can contradict the paged result set |
| G-16 | Releases | "Show all N releases" inside an expanded card loads rows via a separate endpoint while the card summary state is recomputed by client code only for some paths (releases.html:291–311, 618–635) | L | D | Counts can drift after ignore/remove |
| G-17 | Releases/Prioritized | Sticky Load More overlaps Tasks/toasts and duplicates a button already in flow (releases.html:177–185) | M | M | Two identical CTAs, one obscured |
| G-18 | Releases/Prioritized | Sort/release-state is in place, but ignore/unignore and track/prioritize cause full reloads while download does not → mixed mental model (UI-131) | M | B | User cannot predict whether the page will jump |
| G-19 | Prioritized | Group notes are not editable under 640px (`hidden sm:inline-block`, prioritized.html:84–85); groups can be added from this page or Settings (two sources of truth) | M | M | Mobile-created groups always lose notes |
| G-20 | Prioritized | No sort control at all, and the flat API ignores search/sort params while the UI shows a search box (prioritized.html:114–120; main.py:1503–1521) | M | B | Flat view cannot be ordered/searched server-side; search silently only hides loaded rows |
| G-21 | Discovery | Pagination links drop `include_tags`, `exclude_tags`, `tag_match`, `sort` (discovery.html:240–273) | H | B | A tag-filtered browsing session silently resets to unfiltered on page 2 |
| G-22 | Discovery | Tag picker: no keyboard navigation, stuck `aria-expanded`, sort select overwrites pending tag state and Apply can overwrite a newer sort (discovery.html:62–75, 567–639) | H | B | Keyboard/AT users cannot use tag filtering; selections are lost unexpectedly |
| G-23 | Discovery | Search is both client-side filter and server GET in one input (discovery.html:45–54, 400–408) | M | B | Same query yields different result sets depending on Enter vs typing |
| G-24 | Discovery | Tabs are not ARIA tabs and tab choice is not in the URL (discovery.html:27–39) | L | B | Refresh/back always returns to the Discovered tab |
| G-25 | Discovery | Sorting a page reloads immediately, discarding unapplied tag chips (discovery.html:567–571) | M | B | Lost work |
| G-26 | Feeds | No edit UI for an existing feed; "HTML Web Scraper" type can be created without any selector/config fields (feeds.html:137–154) | H | B | Broken-feel feature: the scraper type exists in the select but cannot be configured |
| G-27 | Feeds | Empty-state CTA opens the same add modal, but the modal has no X/Escape/backdrop close (only Cancel under the form, off-screen on small phones) | M | M | Hard to abort on mobile |
| G-28 | Downloads | One 2100-line page with 7 stacked sections and ~30 controls; no tabs/anchor nav; on mobile the user scrolls past diagnostics, banners, inventory and staging to reach the queue (downloads.html:6–614) | H | M | Primary queue/action surface is buried; hard to build a mental model |
| G-29 | Downloads | qBittorrent queue never auto-refreshes (only Torbox starts the poll; downloads.html:1209–1211) | H | B | qBit users see stale progress/state until they reload or act |
| G-30 | Downloads | Two buttons do the same job ("Process Downloads" downloads.html:47 and "Tag & move now" 358) but only one carries `data-task-kind`, so only one shows reattached progress | M | B | Duplicate affordance with inconsistent busy state |
| G-31 | Downloads | Three confirmation idioms on one page: native `confirm()`, armed click-again timers, and inline confirm boxes (UI-129) | M | B | Users cannot predict what a delete click will do |
| G-32 | Downloads | Staging groups default `open` with collapse state only in localStorage; filter "Needs naming intervention" hides whole groups but the count/empty state does not update (downloads.html:2043–2061) | L | B | Misleading counts and no "nothing matches" feedback |
| G-33 | Downloads | Tag modal has 8 fields, no dialog semantics/Escape, and remains the only way to fix metadata (downloads.html:616–670) | M | M | Keyboard users can be trapped; long form on small screens |
| G-34 | Downloads | Raw-inventory and queue tables are 6–8 columns inside horizontal scroll; destructive icon buttons are 28px (downloads.html:291–317, 418–424) | H | M | Mobile users cannot reliably tap the right row action; wrong-row deletes are possible |
| G-35 | Manual Pickup | No refresh/poll for new pickups; only a count on Downloads links here (manual_pickup.html has no timer) | M | B | Held torrents can sit unseen indefinitely unless the user revisits |
| G-36 | Manual Pickup | "Edit series" seeds the match modal with the card's torrent title instead of the stored series name (manual_pickup.html:180–184) | M | B | Wrong prefill → wrong Calibre match risk |
| G-37 | Manual Pickup | Volume inputs auto-save on `change` with no toast/undo; invalid values silently coerce to null (manual_pickup.html:69, 87–99) | M | B | Mapping mistakes are easy and invisible |
| G-38 | Logs | Only 150 rows, no pagination/auto-refresh/filter by event/actor/date; search is client-side over those 150 (logs.html) | H | B | Debugging a past failure is effectively impossible once logs rotate past the window; actor chip exists but cannot filter |
| G-39 | Settings | One save for ~45 fields: any change requires a full save; no per-section save/reset/discard, no unsaved-leave warning; errors are only toast-level (settings.html:54, 1030–1102) | H | B | High risk of unrelated settings churn and of believing a change saved when it did not |
| G-40 | Settings | Section nav lists 9 anchors but the Advanced card is conditionally rendered → dead anchor when debug actions are off (settings.html:47–49 vs 802) | M | B | Clicking a nav item does nothing; nav misrepresents the page |
| G-41 | Settings | qBittorrent password is rendered into HTML `value=` (settings.html:119, downloads.html:101) | H | B | Anyone with page-source/history access sees the credential; browser autofill may also persist it |
| G-42 | Settings | Maintenance is a 300-line wall of destructive buttons with mixed confirm patterns and no grouping by risk beyond headings (settings.html:496–800) | M | D | Easy to hit a destructive action; hard to find the intended one |
| G-43 | Settings | Test buttons (Calibre/downloader/Torbox/webhook/speedtest) are scattered across sections with different result surfaces (badge/inline/toast) | L | B | Inconsistent feedback; users must look in three places |
| G-44 | Settings | Number fields have no client validation messages; server 400/403 responses surface as raw `detail` toast or, for `/api/debug/action`, a partly-applied UI state | M | B | Users cannot tell which field is invalid |
| G-45 | All tables | No sortable headers, no column chooser, no row selection, no sticky header on long tables; right-aligned Actions column requires horizontal scroll on phones | M | M | Data-dense pages are effectively desktop-only |
| G-46 | All pages | Destructive/keyboard flows: no undo for blacklist/ignore/delete actions (toast only), no optimistic rollback | M | B | Misclicks cost a manual reprieve (Discovery → blacklist tab, rescan, etc.) |
| G-47 | All pages | No URL state for modals/accordions/tabs; every reload (and most actions) resets context (UI-131/133) | M | B | Repeatedly re-navigating to the same card/row is the dominant interaction cost |
| G-48 | All pages | No global loading skeleton consistency: some pages show skeletons (series/releases/prioritized), others show blank sections until fetch (downloads sections), others render fully (feeds/logs) | L | B | Perceived performance varies wildly; users may think a section is empty |
| G-49 | All pages | No offline/degraded-state messaging for CDN failure (UI-023) — if unpkg/tailwind CDN is blocked, the page renders as unstyled raw HTML with no warning | H | B | Homelab/air-gapped deployments look broken with no diagnostic |
| G-50 | All pages | No first-run/onboarding path: a fresh install lands on Dashboard with "Calibre DB Not Found" and no guided link to Settings; auth `none` mode has no sign-in affordance | H | B | New users cannot find the 3 settings (Calibre path, downloader, feeds) that make the app work |

---

## 9. Open questions / ambiguities

1. **Nav requirement vs reality:** the brief and tests say "5-item grouped nav", but a
   6th top-level link (Manual Pickup) is rendered outside the `data-nav-item` contract
   (base.html:169). Is Manual Pickup intended to become a Downloads-dropdown child
   (and should `task-15`'s test be updated), or stay flat with a real active state and
   populated badge?
2. **`active_page` for Manual Pickup:** the page reuses `downloads`
   (main.py:1655). Should the redesign introduce `manual_pickup` as a first-class
   page value (requires test updates at test_nav_structure.py:315–328)?
3. **Offline/CDN policy:** is the deployment target allowed to reach
   cdn.tailwindcss.com and unpkg.com? If not, the redesign must vendor a compiled
   Tailwind stylesheet + Lucide bundle and probably add a favicon route.
4. **Theme scope:** dark-only is current. Is a light theme or `prefers-color-scheme`
   support in scope? The `dark` class + `darkMode:'class'` suggests it was intended
   but never built.
5. **Paging intent:** should Series adopt the explicit Load-More model of
   Releases/Prioritized (currently it auto-drains), or should Releases adopt
   infinite scroll? The two models currently coexist.
6. **Client-side vs server-side filtering:** should search be server-side everywhere
   (Series/Prioritized/Logs too) so result counts are truthful?
7. **Filter persistence:** are status/view/sort/search expected to persist across
   page navigation (sessionStorage or URL everywhere)? Current links drop arbitrary
   subsets.
8. **Modal system:** is a shared modal component (dialog role, focus trap, Escape,
   scroll lock) acceptable given all templates are inline and tests pin many modal
   ids/copy?
9. **Confirm idiom:** pick one of native `confirm()`, armed two-step button, or inline
   confirm box — current code uses all three. Which is the product standard?
10. **Mobile scope:** what is the supported minimum width/device (360px? 390px?), and
    is a card-based mobile fallback for tables in scope (would change many
    test-pinned selectors)?
11. **Manual Pickup discoverability:** should the count badge in the nav be
    implemented (base.html:172) and/or a dashboard banner added (main.py computes the
    count already)?
12. **Backfill entry point:** should "Polite Backfill" move to Releases/Prioritized
    (where the missing-data pain is felt) and should the modal become the mobile-safe
    start surface?
13. **Secret handling:** should the qBittorrent password stop being echoed into HTML
    (write-only field with "unchanged" semantics like the Torbox key mask)?
14. **Task polling:** is a 1.5s global poll acceptable long-term, or should the
    redesign move to SSE/WebSocket or visibility-aware polling?
15. **Tag picker sort conflict (UI-069):** is the visible sort select supposed to be
    the single source of truth (removing the hidden `tag-sort-mode`), or should
    Apply use the pending picker state only?
16. **`tag_display` everywhere:** chips currently render on Discovery, Series rows and
    Dashboard; Releases cards/flat rows also call it. Are tag chips desired on every
    surface or only some (view-dependent chip counts are 4/3/… per view)?
17. **Logs retention UX:** the page shows 150 rows while the prune tool manages
    retention; is a "load older"/date-range UI in scope?
18. **Accessibility target:** is WCAG 2.1 AA the acceptance bar (implies labels,
    contrast, keyboard operability, 44px targets, focus management)? Several current
    patterns (bare `<img onclick>`, menu roles without arrow keys) would fail it.
19. **Small-screen Downloads:** is splitting `/downloads` into tabs (Queue / Staging /
    Inventory / Audit) acceptable, given many tests pin element ids but not layout?
20. **Where may the redesign change test-pinned strings?** The tests are source
    assertions; the panel should decide the process (update tests alongside markup,
    or freeze the contract strings and design within them).

---

## 10. Consensus checklist

Every line is one checkable behaviour claim. Experts should vote AGREE / DISAGREE per
ID; wording is deliberately narrow (what the code does **today**, not what it should
do).

UI-001 — The app shell is dark-only (`html.dark`, `#0b0f19` background) with no light-theme styles.
UI-002 — A sticky 64px header (`h-16`, `sticky top-0 z-40`) is present on every page and holds the brand, navigation and user/mobile controls only.
UI-003 — The brand block links to `/` and shows the product name, "Bundles & qBit" badge and "Calibre & BookOrbit Monitor" subtitle.
UI-004 — The desktop nav renders exactly five `data-nav-item` top-level items in the order Overview, Library, Discover, Downloads, System.
UI-005 — A sixth flat "Manual Pickup" nav link renders between Downloads and System, has no active state, and its `#nav-manual-pickup-count` badge is never populated.
UI-006 — Library/Discover/System dropdowns open on click, close on outside click and Escape, restore focus to the trigger on Escape, and keep `aria-expanded` in sync.
UI-007 — The auth user menu (actor name, mode, Sign out) renders only when `auth_mode != 'none'`.
UI-008 — A mobile hamburger toggles a grouped mobile menu whose 10 links each have `min-h-[44px]` and which closes on outside click and Escape.
UI-009 — A top page-progress bar animates on internal anchor clicks and completes on `pageshow`, but does not appear for button-triggered reloads.
UI-010 — Toasts render bottom-right, auto-dismiss after 4s, differentiate success/error/info, and have no `aria-live`/`role` and no message escaping.
UI-011 — A floating Tasks panel polls `/api/tasks?active=0&limit=20` every 1.5 s on every page and renders up to 12 tasks with progress, ETA and error; its Cancel branch exists but never renders because `/api/tasks` never emits a `cancellable` field.
UI-012 — Buttons carrying `data-task-kind` are disabled and show live progress while a matching persisted task runs.
UI-013 — Clicking a cover opens a global lightbox with close button, backdrop click and Escape dismissal; the trigger is not keyboard-focusable.
UI-014 — The Calibre sync banner appears only while `in_progress` or `initial_sync_done === false`, polls every 3s while running, and is dismissible with auto-reload on completion.
UI-015 — The backfill banner appears on any page while a backfill runs, polls every 2.5s, shows progress text, and offers Stop Backfill.
UI-016 — The backfill setup modal accepts start page and max pages, shows a resume hint when `last_page > 1`, and is reachable only from the Discovery page.
UI-017 — Blacklisting from a row/card posts `/api/blacklist`, toasts, and fades/removes the item or reloads as fallback.
UI-018 — Ignoring a release dims its row/card and disables the ignore button; unignoring reloads the page.
UI-019 — The shared per-series backfill action posts `/api/series/backfill` with `max_pages:3`, toasts, and starts the global backfill banner if accepted.
UI-020 — In oidc mode unauthenticated HTML requests are redirected to `/auth/login?next=...` and `/api/*` receives 401 JSON; in basic mode both receive a 401 Basic challenge; the header hides auth UI in `none` mode.
UI-021 — Every link, button, input, select, textarea and summary gets a 2px rose `:focus-visible` outline with 2px offset.
UI-022 — Custom dark scrollbars, a `.break-words-anywhere` utility and a shimmer cover placeholder exist in the global stylesheet.
UI-023 — Tailwind (cdn.tailwindcss.com) and Lucide (`unpkg … @latest`) are loaded from CDNs with no local fallback assets.
UI-024 — Templates use a `slate-850` colour that is not defined in the Tailwind config, so those hover/gradient utilities have no effect.
UI-025 — The footer shows "MangaTrax v1.0.0", the zero-lock strapline and a GitHub link.
UI-026 — `base.html` defines a `{% block scripts %}` hook that no page template overrides.
UI-027 — `#nav-manual-pickup-count` exists in the header but no code ever sets its text or removes `hidden`.
UI-028 — The dashboard hero shows a green Calibre-connected chip with series/book counts or an amber not-found chip.
UI-029 — The dashboard shows five KPI cards, each linking to the matching filtered page.
UI-030 — The dashboard shows up to five active torrents with progress, speed, state and size, linking to `/downloads`.
UI-031 — The dashboard shows up to eight wanted releases as cards with cover, bundle/volume badge, feed, tag chips, missing volumes and Download.
UI-032 — The dashboard Download button posts the download endpoint, toasts, and reloads the page after 1.5s.
UI-033 — The dashboard lists the ten latest activity rows with colour-coded event badge and local timestamp, linking to `/logs`.
UI-034 — The dashboard computes a manual-pickup count but never renders it.
UI-035 — The Series page renders a seven-column table fed by `/api/series/rows` in 50-row pages.
UI-036 — Each series row has a monitoring toggle that posts `/api/series/{id}/toggle` and reloads after 0.8s.
UI-037 — Series search is a client-side filter over currently loaded rows with a dedicated no-match empty state and Clear search.
UI-038 — The Series page auto-fetches successive 50-row pages via a self-scheduled timeout and an IntersectionObserver, while also rendering a hidden "Load More Series" button.
UI-039 — "Sync Calibre" exists only on the Series page and runs a synchronous POST `/api/calibre/sync` with spinner, toast and reload.
UI-040 — Each series row has a link-editor modal that saves `website_url` through `/api/series/{id}/link`.
UI-041 — Unlinked series rows show "Not linked — Match to Calibre series", which opens a Calibre search modal and links/merges via `/api/series/{id}/match-calibre`.
UI-042 — Each series row offers Backfill, Edit link and Blacklist actions plus a clickable cover and optional external link.
UI-043 — `series.html` defines a local `blacklistSeries()` that is never called (rows use the shared global function).
UI-044 — `series.html` supports server-rendered rows, but `/series` always passes an empty list so only the JS path is used.
UI-045 — Releases offers a Group-by-Series vs Flat view switch implemented as full-page links carrying `?view=`.
UI-046 — Releases renders seven status filter pills as full-page links; using one drops the current sort and search.
UI-047 — Releases search debounces 300ms, resets pagination, and refetches server-side, while a second client-side filter hides loaded nodes and reports "N matches".
UI-048 — Releases sort select refetches in place and writes `?sort=` with `history.replaceState`.
UI-049 — Both Releases views render a disabled Load More button from first paint with remaining/"Showing X of Y" counts, retry-on-error, and a fixed sticky duplicate.
UI-050 — Releases series cards show cover, romaji, In-Calibre/Discovery chip, totals, completion chips, release count, Track/Backfill/Blacklist, volume coverage grid and optional synopsis.
UI-051 — Series cards cap releases at 10 and offer "Show all N releases", which pages the remaining rows into the accordion and removes itself when exhausted.
UI-052 — Release download updates the state chip/button in place and polls `/api/releases/{id}/state` every 1.5s while non-terminal, without reloading the page.
UI-053 — Each release row/card offers Ignore (dims the row) and Unignore (reloads).
UI-054 — A per-release modal can move the release to another Calibre series, a new title, or detach it to Discovery.
UI-055 — A release-group chip can be added to Prioritized Groups with one click from Releases, Prioritized and Discovery.
UI-056 — A "Track" button on series cards posts `/api/discovery/track` and reloads.
UI-057 — An empty Releases result shows the exact copy "No manga series found matching current filters." with a Clear filters button.
UI-058 — Releases/prioritized code branches on `series-card-deferred` and `flat-row-deferred` classes that no server chunk ever emits.
UI-059 — Prioritized shows group pills with release counts and per-pill remove buttons plus a quick add-group form.
UI-060 — The prioritized group-notes input is hidden below the `sm` breakpoint.
UI-061 — Prioritized reuses the Releases view switcher and status pills, with no sort control.
UI-062 — Prioritized search is client-side over loaded cards/rows and reports "N matches".
UI-063 — Prioritized Load More uses `/api/prioritized/cards` (8) and `/api/prioritized/flat_rows` (50) with the shared `updateLoadMoreUi` helper and sticky control.
UI-064 — An empty prioritized result shows the exact copy "No prioritized manga releases found matching current filters." with Clear filters.
UI-065 — Prioritized contains the same dead deferred-class branches as Releases.
UI-066 — Discovery has two client-side tabs (Discovered, Blacklisted) implemented by class rewriting, not ARIA tabs, and not reflected in the URL.
UI-067 — The Discovery search input both filters loaded cards on input and submits a server GET form on Enter.
UI-068 — Discovery has an include/exclude tag picker with autocomplete from `/api/tags`, removable chips and Apply/Clear, without arrow-key navigation.
UI-069 — Discovery's visible sort select navigates immediately and can conflict with the tag picker's hidden sort state.
UI-070 — Discovery paginates server-side, but its page links carry only `page`, `page_size` and `q`, dropping tag filters and sort.
UI-071 — Each discovery card has Track Series and Blacklist buttons that post their endpoints, toast, and reload after 0.6s.
UI-072 — Each discovery card shows a truncated MangaUpdates synopsis with a Fetch button that updates the text and MangaUpdates link in place.
UI-073 — The MangaUpdates link falls back to a MangaUpdates search URL when no stored URL exists.
UI-074 — Discovery's Blacklisted tab provides manual add, search and per-row Unblock/Restore.
UI-075 — The global backfill setup modal is opened only by the Discovery page's "Polite Backfill" button.
UI-076 — Discovery cards and Releases rows expose the same one-click "prioritize this release group" action.
UI-077 — Feeds renders a table of name, type, enabled, URL, auto-download, interval, last-check and actions.
UI-078 — Each feed has an On/Off toggle that posts `/api/feeds/{id}/toggle` and reloads after 0.8s.
UI-079 — The Add Feed modal collects name, type, interval, URL and auto-download, offers Test Feed Live, and has no selector/config fields for HTML scrapers.
UI-080 — Deleting a feed uses native `confirm()` and reloads after 0.8s.
UI-081 — "Poll Feeds" exists only on the Feeds page, starts the async feed check, polls status every 1.5s up to 15 minutes, toasts the result and reloads.
UI-082 — The Downloads page starts with an auto-download diagnostics card that loads on page load and can be refreshed.
UI-083 — The Downloads page shows an amber banner with the manual-pickup count and an "Open Manual Pickup" link when pickups exist.
UI-084 — The Downloads page has a downloader status card with an in-app Configure panel (URL/user/password/enabled) and Save & Test.
UI-085 — The Downloads page lists raw downloads awaiting a Calibre series with per-file Delete and a Match series action.
UI-086 — The Downloads page has a Failed documents card supporting scan, list, retry, convert, per-row delete and delete-all with an armed confirmation.
UI-087 — The Downloads queue table renders six columns and per-row Torbox retry/remove or qBittorrent pause/resume/delete controls.
UI-088 — Torbox queues auto-refresh every 1s while active (5s idle), while qBittorrent queues do not auto-refresh at all.
UI-089 — "Clear failed" on the Torbox queue requires a second click on the same button, then posts clear-failed and removes rows.
UI-090 — "Check now" on the Torbox queue posts `/api/downloads/torbox/poll` and refreshes the queue.
UI-091 — The raw downloads inventory table shows name/files/size/age/series/state/matched release with Manual match and Delete actions.
UI-092 — Raw cleanup audits duplicates and already-present copies, previews them, and requires confirmation before clearing.
UI-093 — Rescan, Prune empty folders and Flatten staging each post their endpoint, toast a summary and refresh in place.
UI-094 — A second "Tag & move now" button duplicates the header "Process Downloads" action; the header carries `data-task-kind="downloads:process"`, but `/api/staging/tag` is a foreground request that creates no persisted task, so neither button ever gets task-panel reattachment or busy state (the spinner is local JS only).
UI-095 — Staging files are grouped in `<details>` blocks per series whose open/closed state persists in localStorage and can be filtered to "needs naming intervention".
UI-096 — Staging file rows offer Preview/Tag, Fix naming (prefilled) and two-step Delete.
UI-097 — Staging duplicate cleanup audits, presents selectable rows, shows an inline confirm, purges via a background task and reports totals.
UI-098 — The ComicInfo `<Series>` audit table shows per-field diffs, status badges and selectable rows, and repairs only selected files.
UI-099 — The Preview/Manual Tag modal loads the current ComicInfo, edits seven fields (staged filename, Series, Volume, Title, Writer, Publisher, Genre), saves, toasts and reloads.
UI-100 — The staging series-match modal searches Calibre or accepts a new title, then retags/links the group through a background task.
UI-101 — The Downloads page is a single long page with no tabs or in-page navigation across eight card sections (auto-download diagnostics, awaiting series, failed documents, active queue, raw inventory, staging directory, staging cleanup, ComicInfo audit), plus the downloader-config card, a conditional pickup banner and modals.
UI-102 — Manual Pickup shows quota/expiry, pickup cards, and a no-pickups empty state.
UI-103 — Manual Pickup supports "Fetch whole torrent" and "Fetch selected", both task-watched with inline error text; the server fetches every un-fetched file individually and uses the whole-torrent zip only when the pickup has no file list at all.
UI-104 — Manual Pickup supports Retry strict mapping with a task watch and inline failure text that says the mapping is kept.
UI-105 — Manual Pickup's "Edit series" opens the shared match modal seeded from the card title.
UI-106 — Manual Pickup volume mapping auto-saves per file on change and has a separate wanted-volumes save action.
UI-107 — Manual Pickup Discard confirms, posts discard and removes the card.
UI-108 — The Manual Pickup page has no refresh control or polling for new pickups.
UI-109 — Logs renders the 150 newest activity rows with event-type and actor badges, details and local timestamps.
UI-110 — Logs search is client-side over the 150 rendered rows and shows a dedicated no-match state.
UI-111 — "Prune now" posts `/api/logs/prune`, toasts deleted counts and reloads after 0.8s.
UI-112 — Logs has no pagination, date/level/actor filtering, export or auto-refresh.
UI-113 — Settings has a sticky nine-anchor section nav with scroll-spy and `aria-current`, whose Advanced anchor is dead when debug actions are disabled.
UI-114 — Settings is one ~45-field form with a single save and a sticky "Unsaved changes" bar; there is no per-section save, discard or leave warning.
UI-115 — Settings can test the Calibre path via `/api/calibre/test` and toasts the series/book counts or the error.
UI-116 — Settings configures downloader URL/user/password/category/tags and can test the connection with a status badge.
UI-117 — Settings configures engine (qbit/Torbox), Torbox key/retention/concurrency/timeouts and can test both downloaders.
UI-118 — Settings edits downloads/staging paths with inline not-writable warnings and six pipeline toggles.
UI-119 — Settings edits Calibre/feed intervals, backfill defaults and the application timezone.
UI-120 — Settings edits the Nyaa base URL, and saving it migrates the seeded feed.
UI-121 — Settings configures Discord and generic/Apprise webhooks with per-target test buttons and two notification toggles.
UI-122 — Settings manages prioritized groups (add, count display, delete) independently of the Prioritized page.
UI-123 — Settings Maintenance exposes cache/discovery/description cleanup, log pruning and rotation, network diagnostics/speedtest, release-cache clearing, tag refresh, and volume-range/false-series/light-novel/alias/attribution audits and repairs.
UI-124 — Network diagnostics can be copied as JSON and a bounded speedtest can be run with task binding.
UI-125 — The Advanced/Danger-Zone section (purge non-manga, repair re-keyed names, two-step full data reset) renders only when `MANGATRAX_DEBUG_ACTIONS=1`.
UI-126 — The qBittorrent password is written into the HTML `value=` attribute on both Settings and Downloads, while the Torbox key is masked server-side.
UI-127 — The Settings add-group inputs live inside the main settings form, so pressing Enter triggers Save Settings rather than Add Group.
UI-128 — Most page-level list-fetch failures are only logged to `console.error`, leaving a skeleton or stale list; Releases turns its Load More button into "Retry loading" and Series reveals its hidden Load More button, so those two pages do have a retry path.
UI-129 — Destructive actions use three different confirmation idioms: native `confirm()`, click-again armed buttons, and inline confirm boxes.
UI-130 — Many inline handlers interpolate server-provided names with HTML escaping only (e.g. `onclick="blacklistSeriesGlobal('{{ s.name|escape }}')"`).
UI-131 — Most mutating actions refresh by full-page reload; only release downloads, live queues, task panel and some downloads actions update in place.
UI-132 — Cover images use `/api/covers` with `loading="lazy"`, `decoding="async"`, a shimmer placeholder and an SVG fallback.
UI-133 — Server-readable filters live in the URL, but many links/pagination drop subsets and client-side filters never appear in the URL.
UI-134 — The visual language is a rose brand scale, three dark surfaces, slate text, rounded-xl/2xl cards, uppercase 10–11px labels and Lucide icons.
UI-135 — Typography uses the Tailwind system sans stack with page titles at `text-2xl` and many labels at 9–11px; no custom or fluid font.
UI-136 — Purpose-built empty states exist for the main lists, but there is no page-level error state or styled 404/500 page.
UI-137 — `data-task-kind` is a UI/backend contract: a button's kind must match a task kind the backend actually emits. The header "Process Downloads" button declares `downloads:process`, but `/api/staging/tag` creates no persisted task, so no task of that kind ever exists and the duplicate "Tag & move now" button has no binding at all.


---
