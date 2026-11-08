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

**Download status.** Values observed: `IDLE` (default), `DOWNLOADING`, `PENDING_DEDUPE`, `MANUAL_PICKUP`, `COMPLETED`, `FAILED`; `QUEUED` is tolerated on read but never written by current code (OPS-097). `PENDING_DEDUPE` exists only so the 60 s dedupe sweep can retry a suppressed auto-download once the in-flight reservation clears (`scheduler.py:2329-2345`). `download_attempts`/`download_error` accompany FAILED; `downloaded_at`/`routed_at`/`download_files_json` record the local-fetch and routing milestones.

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
