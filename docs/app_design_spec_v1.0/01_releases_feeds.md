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
