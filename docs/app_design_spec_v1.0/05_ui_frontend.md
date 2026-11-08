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
