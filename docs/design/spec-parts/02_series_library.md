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

The historical defect this model fixes is documented in `docs/archive/development/PROJECT.md:157-164`: AniList renamed series ("Dungeon Meshi" → "Delicious in Dungeon") and broke Calibre matching/ComicInfo. The repair for already-corrupted rows is SER-053.

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

**Q8 — API surfaces with no UI, and docs that imply otherwise.** `GET /api/search` (M2 feature 17; `docs/archive/development/PROJECT.md:41`), `GET /api/series/link-audit`, `POST /api/series/link-repair`, `GET /api/maintenance/series-aliases` have no consumer in `backend/app/templates` (verified by grep). The Series page search is a client-side substring filter, so AniList alias search is unreachable for interactive users. Should the redesign wire these up or drop them?

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



