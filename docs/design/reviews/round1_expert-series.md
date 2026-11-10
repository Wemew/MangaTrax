# Consensus Round 1 — Review by expert-series

**Reviewer:** expert-series (author of Part 2 / SER domain)
**Assigned foreign parts:** Part 1 (REL) and Part 4 (OPS); plus Part 0 and the full Feature Registry.
**Code base:** commit `86a7bd8`; all references verified read-only in this checkout.
**Documents read:** `docs/archive/spec-drafts/APP_DESIGN_SPEC_v0.9.md` (Part 0 lines 34-540, Part 1 lines 541-1252, Part 4 lines 3033-3869) and `docs/archive/spec-drafts/FEATURE_REGISTRY_v0.9.md` in full (445 claims).
**Method:** every disagreement below was re-derived from implementation files, not from my memory of the domain specs. I also re-read my own SER checklist lines as extracted into the registry and diffed them against `02_series_library.md`.

---

## 1. Part 0 — System Overview, Architecture, Data Model, Workflows

**VERDICT: DISAGREE (blocking).** The architecture/threading/persistence descriptions are largely accurate, but the cross-reference IDs, the route-count table and the "IN_CALIBRE is terminal" rule are wrong and/or contradict the implementation and other parts.

### What Part 0 gets right (verified)

- "125 HTTP routes total (10 HTML pages + 3 auth + /health + 111 JSON endpoints)" — decorator count in `backend/app/main.py` is 125; HTML views 10, auth 3, `/health` 1, JSON 111 (script-verified).
- "13 tables" and the table list — matches `CREATE TABLE IF NOT EXISTS` occurrences in `backend/app/database.py` (13 names: `series_tracking`, `series_blacklist`, `releases`, `prioritized_groups`, `feeds`, `activity_logs`, `users`, `app_settings`, `manga_metadata_cache`, `series_alias_overrides`, `background_tasks`, `torbox_manual_pickups`, `download_failures`).
- One process/many threads, 10 s worker tick, Torbox poller, 3-worker task executor — `backend/app/scheduler.py:5152-5277`, `backend/app/background_tasks.py:24-30`.
- Request lifecycle, middleware order (auth registered before headers so headers wrap auth responses) — `backend/app/main.py:234-277`.
- Persistence model (SQLite WAL, new connection per method, no FKs, name joins not ids) — `backend/app/database.py:377-403`; grep for `FOREIGN KEY` in `database.py` returns nothing.
- Workflows §4.1/§4.2/§4.5/§4.6/§4.8 are consistent with the code paths they cite.
- §7 open-question counts: REL §8 has 14 items, OPS §8 has R-1…R-26 (26), SER §7 has 18, UI 20 + G-01…G-50 — all match the parts.

### Part 0 findings (non-registry statements; IDs invented for this review)

| ID | Why it is wrong / incomplete | Corrected wording | Evidence |
|---|---|---|---|
| P0-01 | Cross-reference IDs are systematically wrong. Examples: "every authenticated user is administrator (OPS-009/R-9)" — OPS-009 is env seeding; stable-key invariant cites SER-019/SER-020 (modal/nav) instead of SER-045-047; status invariant cites SER-021/OPS-078; ownership invariant cites SER-025/DL-036; wanted-set invariant cites DL-033; media-typing invariant cites REL-030/OPS-079; name-join invariant cites SER-018/R-4; §4.2 cites SER-019/020/023, SER-025/026, SER-021; §4.4 cites REL-031 for hardcoded qbit category/tags; §4.7 cites SER-032/033 for per-series refresh; §4.9 cites OPS-009/R-9 | Every cross-reference in Part 0 must be regenerated from the final registry (e.g. stable key → SER-045/046/047; status precedence → REL-018; ownership union → DL-078 + REL-032; wanted-set authority → DL-037/DL-079; media typing → REL-034 + REL-033; no-cascade/name joins → OPS-089/R-9; per-series refresh → SER-056…SER-065; qbit hardcode → REL-021) | `APP_DESIGN_SPEC_v0.9.md:110,296,300,303,305,308,310,312,340,342,349,384,418,444`; registry IDs cited |
| P0-02 | §6 route-count partition sums to 120 JSON endpoints (30 REL + 25 SER + 40 DL + 24 OPS + 1 debug = 120), contradicting §1.2's 111 JSON endpoints (125 total). Measured partition of the 111 JSON routes by owning domain: REL 30, SER 24, DL 42, OPS 14 + debug 1 = 111 | "111 JSON endpoints: REL 30, SER 24, DL 42, OPS 14 + debug 1; total 125 routes." | `APP_DESIGN_SPEC_v0.9.md:130` vs `:483-487`; route list in `backend/app/main.py` (script-verified) |
| P0-03 | §4.3 and §3.2 state "IN_CALIBRE is terminal for automation; a human can change it", but `resolve_status` returns the *incoming* status on an equal-rank tie, and `REQUESTED` and `IN_CALIBRE` both rank 1. Automated reconcile/volume-update can therefore move `IN_CALIBRE → REQUESTED` (intentional per the code comment that ownership reconciliation may transition both ways) | "Incoming `IN_CALIBRE` always wins and `IN_CALIBRE` is never downgraded to `IN_STAGING`, but an equal-rank `REQUESTED` incoming value can replace a stored `IN_CALIBRE`; it is not absolutely terminal for automation." | `backend/app/database.py:253-261,299-307`; writers `database.py:2596-2623`; `scheduler.py:3310-3320` |
| P0-04 | §5 rule 2 "never write a status of NEW — it is an internal recomputable value" contradicts real code paths that persist `NEW` (e.g. different-series writes `NEW`, matcher/upsert can store `NEW`) | "Prefer not to persist `NEW`; several existing paths still do (different-series fallback, matcher/upsert), so `NEW` must be treated as a valid stored status." | `backend/app/main.py:3890-3895`; `backend/app/calibre/matcher.py:209-214`; `database.py:2433-2571` |
| P0-05 | §2.2 says the auth gate exempts only `/health` and `/auth/*`; the middleware also exempts `/static/*`, `/favicon.ico` and `/healthz` | Add `/static/*`, `/favicon.ico`, `/healthz` to the exemption list (matching OPS-011). | `backend/app/auth.py:946-947` |
| P0-06 | §3.2 invariant 4 ("`download_wanted_json` is authoritative over `missing_vols_json`") has no corresponding registry claim | Add a registry claim (proposed DL-083) so the invariant is votable. | `backend/app/scheduler.py:1029-1053`; registry has no line for it |

---

## 2. Part 1 — Releases, Feeds, Discovery & Prioritized (REL)

**VERDICT: AGREE WITH CORRECTIONS (blocking until REL-018 and REL-039 are reworded).** The REL inventory and §3 "how it works" are unusually accurate: 37 of 42 claims pass as written; 5 need correction (below). Part 1's own §3.11 has one factual error, and §3.7 contradicts §3.21.

### REL disagreements

| Claim ID | Why it is wrong / incomplete | Corrected one-sentence claim | Evidence |
|---|---|---|---|
| REL-018 | "treats IN_CALIBRE as terminal" is not true for the equal-rank tie: `resolve_status(IN_CALIBRE, REQUESTED)` returns `REQUESTED` (rank 1 vs 1), and automated reconcile/volume-update paths call it. The same overstatement appears in Part 1 §3.7 ("existing `IN_CALIBRE` is never downgraded…") although Part 1 §3.21's own matrix correctly shows `IN_CALIBRE + REQUESTED → REQUESTED` | "Automated writes use `resolve_status`: blank never clears, vetoes are absolute, incoming `IN_CALIBRE` always wins and `IN_CALIBRE` is never downgraded to `IN_STAGING`; an equal-rank `REQUESTED` incoming value can still replace a stored `IN_CALIBRE`." | `backend/app/database.py:253-261,299-307,2596-2623`; Part 1 `APP_DESIGN_SPEC_v0.9.md:696-705` vs `:879-894` |
| REL-024 | Absolute "providers raise `ProviderFetchError` when nothing could be fetched" has an exception: RSS and HTML providers return `[]` (silent empty) when their URL fails the SSRF guard, before any fetch | "Providers raise `ProviderFetchError` when the source cannot be fetched or parsed, except that RSS/HTML providers return an empty list when their configured URL is rejected by the SSRF guard." | `backend/app/providers/rss.py:48-52`; `backend/app/providers/html_scraper.py:58-62`; `backend/app/providers/base.py:7-15` |
| REL-028 | "explicit chapter discards audited" is only true on the scheduler-side discard paths; provider-level chapter-only/LN discards are silent (no activity row) before a `ReleaseItem` exists | "Explicit chapter-only titles, light novels and JPEG XL releases are discarded before storage; JPEG XL discards and scheduler-side chapter discards are audited, while provider-level chapter/LN filtering is silent." | `backend/app/providers/rss.py:69-77`; `backend/app/providers/nyaa.py:358-365`; `backend/app/scheduler.py:3482-3543` (auditing paths) |
| REL-038 | Absolute "every release/series/prioritized/blacklist write invalidates the 5 s grouped cache" is false: tag updates, release-detail/cover updates, series link/metadata updates do not call `_invalidate_grouped_cache()`; they are only masked by the 5 s TTL | "Status/volume/download/blacklist/tracker-link/group writes invalidate the 5 s grouped cache; tag, release-detail, series-link and cover-only writes do not (bounded by the 5 s TTL)." | `backend/app/database.py:1307-1337,2154-2158,2191-2216,2218-2231,3260-3275` (no invalidation) vs `:2570,2594,2623,…` |
| REL-039 | "`nyaa_base_url` (setting or env) is the single source for every Nyaa URL" is false: a feed's own `config.base_url` wins; the setting is only injected as a per-feed default, and env only seeds the setting | "Nyaa base URLs resolve as per-feed `config.base_url`, then the `nyaa_base_url` setting (seeded from `MANGATRAX_NYAA_BASE_URL`), then `https://nyaa.si`; saving the setting repoints only the exact seeded feed." | `backend/app/providers/registry.py:17-47` (injection only when `config.base_url` absent); `backend/app/nyaa_source.py:96-113` |

**Part 1 text corrections (not registry IDs):**
- §3.11 line 764 "only HTTP 404 is an accepted end" is wrong: a parsed page with zero torrent rows is also an end-of-results (`backend/app/providers/nyaa.py:331-338`); empty *body* is a retryable failure. This also makes Part 1 contradict registry REL-025 ("404/empty").
- §3.7 "existing IN_CALIBRE is never downgraded" contradicts §3.21's matrix (see REL-018).
- Part 1 §7.10 says `qbit_category`/`qbit_tags` "have no effect on this domain"; `qbit_category` is still read for the downloader status listing (`backend/app/main.py:606`), so the "ignored" statement must be scoped to `add_torrent` category/tags only.

### REL claims spot-verified against code (AGREE evidence)

REL-001 (`main.py:1182-1207`, empty lists), REL-002 (`database.py:4004-4544`, display/stable split), REL-003 (6 `<th>` at `releases.html:140-147`), REL-004 (cap 10 `main.py:1301,1369-1375`; defaults 8/50), REL-005 (300 ms `releases.html:267-275`; search SQL `database.py:2904-2913,4528-4540`), REL-006 (`database.py:2896-2903,4509-4523`), REL-008 (`database.py:4572-4585` ON CONFLICT name, cache invalidation 4584/4592), REL-010 (`main.py:1503-1521`), REL-011 (`main.py:1574-1575,1590-1592`; selector `discovery.html:83-86`), REL-012 (`database.py:3115-3136`), REL-013 (`database.py:3075-3091`; pagination links `discovery.html:240-265` drop tags), REL-014 (`database.py:3172-3180`), REL-015 (`database.py:3277-3361`; `main.py:4135-4171`), REL-016 (`database.py:2342-2379`), REL-019 (`main.py:4081-4093`), REL-020 (no template reference; grep), REL-021 (`main.py:3738-3824` 409; `releases.html:641-650` 1.5 s), REL-025 (`nyaa.py:26-38,146-246,320-338`), REL-026 (`scheduler.py:3352-3418`), REL-027 (grep: no read of `feeds.check_interval_minutes`), REL-029 (`scheduler.py:263-392`), REL-031 (`scheduler.py:408-531` thresholds 0.85/300/FINISHED), REL-032 (`database.py:3989-4002`; `scheduler.py:5166-5173,5202-5209`), REL-033 (`database.py:3000,3133`; `ln_releases.py`), REL-034 (`database.py:79-118,1005-1103`), REL-035 (`scheduler.py:3660-3780`), REL-036 (`scheduler.py:4815-4910`), REL-037 (`scheduler.py:4460-4708`), REL-040 (`scheduler.py:3363-3367,1358-1375`), REL-041/REL-042 (grep: no template consumer).

---

## 3. Part 4 — Platform, Settings, Auth, Ops & Persistence (OPS)

**VERDICT: AGREE WITH CORRECTIONS (blocking until OPS-079 is reworded).** 88 of 94 claims pass as written. Part 4's operational detail is strong (Docker/entrypoint/auth/security/scheduler/task/logging/webhook/diagnostics sections all matched the code in the checks I ran), with the following exceptions.

### OPS disagreements

| Claim ID | Why it is wrong / incomplete | Corrected one-sentence claim | Evidence |
|---|---|---|---|
| OPS-019 | "throttled to once per 300 s per identity" has one exception: the OIDC callback JIT-provisions with `force=True`, bypassing the throttle on every successful login | "Successful Basic authentications and OIDC requests JIT-upsert a `users` row keyed on subject, normally throttled to once per 300 s per identity, except the OIDC callback which forces an upsert on every login; existing non-empty fields are never blanked." | `backend/app/auth.py:1080-1100` (throttle), `:1150` (`force=True`), `database.py:3478-3509` |
| OPS-030 | "leaves … `app_settings` … intact" is not exact: `reset_all_data` resets `backfill_status` and `backfill_progress` in `app_settings` (and also leaves `series_alias_overrides` etc. intact as stated) | "reset-all-data deletes releases/trackers/blacklist/activity/metadata/feeds/prioritized-groups, clears the cover cache, resets the backfill state keys in `app_settings`, and leaves users, most settings, background tasks, alias overrides, pickups and failures intact." | `backend/app/database.py:4670-4722` (backfill keys at `4712-4713`) |
| OPS-079 | "repair … re-keys only rows the fixed matcher would no longer map" is false: the repair endpoint takes any selected existing release id, re-parses its own title and re-keys it to that parsed series, without re-validating the id against the audit's candidate list (only IGNORED/BLACKLISTED statuses are preserved) | "False-series audit is read-only and flags suspicious rows; repair requires `confirm:true` and at most 500 ids, and re-keys every selected existing release to its title-parsed series while preserving veto statuses (selection is not re-validated against the audit list)." | `backend/app/false_series_matches.py:85-151`; `backend/app/main.py:2519-2548` |
| OPS-090 | "45+ default keys" is numerically wrong: 39 keys are seeded in the defaults dict, plus `filter_non_manga` and `media_filter_applied` from the `INSERT OR IGNORE` subset = 41 keys on a fresh DB; startup one-shot passes add `jpegxl_filter_applied` and `ln_release_backfill_done` (43 total) | "On a fresh database the app seeds 41 `app_settings` keys (39 defaults plus `filter_non_manga` and `media_filter_applied`; startup passes later add `jpegxl_filter_applied` and `ln_release_backfill_done`), one built-in Nyaa RSS feed and four prioritized groups." | `backend/app/database.py:728-780` (39 keys), `:782-837` (2 new keys), `:918-938`, `:1105-1162` |
| OPS-087 | "created idempotently in one transaction" is imprecise for the shipped Python 3.11 runtime: `init_db` uses one connection and a final `conn.commit()`, but Python 3.11's `sqlite3` implicitly commits before DDL (`CREATE TABLE`) in legacy isolation mode, so the DDL is not literally one transaction | "init_db creates/migrates the 13 tables idempotently on one connection (with a single final commit); under the shipped Python 3.11 sqlite3 legacy transaction mode DDL statements implicit-commit, so it is not one atomic transaction." | `backend/app/database.py:405-997`; `Dockerfile:1` (python:3.11-slim) |
| OPS-091 | Wording implies the whole typing gate is `media_filter_applied` + `filter_non_manga`; there is a second pass (`_run_jpegxl_filter_once`) gated only by `jpegxl_filter_applied` that runs even when `filter_non_manga` is false | "The LN/chapter-only/JPEGXL re-typing pass is gated by `media_filter_applied` and `filter_non_manga`; a separate JPEGXL-only pass gated by `jpegxl_filter_applied` runs regardless of `filter_non_manga`; neither deletes rows or writes status." | `backend/app/database.py:1005-1103,1105-1164` |
| UI-011 (foreign but found) | "renders up to 12 tasks with progress, ETA, error and Cancel" conflicts with OPS-059: the Cancel button markup exists but never renders because `serialize_task` emits no `cancellable` field | "The Tasks panel renders up to 12 tasks with progress, ETA and error; its Cancel button markup is unreachable because `serialize_task` never emits `cancellable`." | `backend/app/templates/base.html:1039`; `backend/app/background_tasks.py:51-76` |

### OPS claims spot-verified against code (AGREE evidence)

OPS-001 (`Dockerfile:1-31`, `backend/requirements.txt:1-9`), OPS-002 (`entrypoint.sh:1-36`), OPS-003 (both compose files: port 8082, log caps, cap_drop ALL, 4 mounts), OPS-004 (`main.py:1040-1058,171-198`), OPS-005 (`Dockerfile:46-49`), OPS-006 (`main.py:213-232`), OPS-007 (`scheduler.py:5300-5314`), OPS-008 (`main.py:200-211`), OPS-009 (`database.py:726-837`), OPS-010 (`auth.py:340-352,390-449`), OPS-011 (`auth.py:946-947,1021-1077`; `main.py:249-264`), OPS-012 (`auth.py:533-558,949`), OPS-013 (`auth.py:153,968-969`; `main.py:1061-1091`), OPS-014 (`auth.py:379-387`; `main.py:1094-1125`), OPS-015 (`auth.py:885-900,1155-1162`), OPS-016 (`auth.py:984-990,1051-1063`; `main.py:1068-1080`), OPS-017 (`auth.py:472-527`), OPS-018 (`auth.py:616-676,682-901`; skew constant `:154`), OPS-020 (`auth.py:240-246`; `database.py:25-38`; `logs.html:40-42`), OPS-021 (`auth.py:1007-1019`), OPS-022 (`main.py:234-277` middleware order), OPS-023/024 (grep; `auth.py:947`), OPS-025 (`security.py:76-266`), OPS-026 (`security.py:341-417`), OPS-028 (`security.py:319-335`; `database.py:41-43`), OPS-029 (`schemas.py:982-992`; `main.py:4342-4388`), OPS-031 (`database.py:3633` audit), OPS-033 (`maintenance.py:28-101`), OPS-034/036 (`main.py:4211-4217`), OPS-035 (`_validated_payload` 400 at `main.py:320-325`; 45 fields counted in `schemas.py:645-923`), OPS-037/038 (`main.py:4233-4245,4224-4231`), OPS-039 (`storage_health.py:30-172`; `main.py:357-394`), OPS-042 (37 entries, only definition), OPS-048 (`main.py:114-168`), OPS-050 (`scheduler.py:2296-2327`; `database.py:1248-1291`; `scheduler.py:5244-5250`), OPS-051 (`database.py:1248-1291` result keys), OPS-052 (`main.py:1863-1873` 150 rows), OPS-053 (`background_tasks.py:24-30,136-173`), OPS-054 (`background_tasks.py:104-129`; `database.py:1577-1579` clamp), OPS-055 (`main.py:1267-1297`), OPS-056 (`main.py:1235-1245`; `background_tasks.py:51-76`), OPS-058 (`main.py:1256-1264`; `background_tasks.py:176-187`), OPS-059/060 (grep: only `tags:refresh` cancellable; prune never called), OPS-061 (`scheduler.py:5152-5279`), OPS-062/063 (`scheduler.py:3115-3209,3352-3369`), OPS-065 (`scheduler.py:5191-5219`), OPS-069 (`scheduler.py:5244-5250`), OPS-072 (`scheduler.py:2815-2835` exact formula), OPS-084 (`network_diagnostics.py:26-34,375-400`; `main.py:2229-2275`), OPS-085 (`notifier/webhook.py:41-61,239-251`), OPS-086 (`scheduler.py:1358-1420`), OPS-087 (13 tables, script-verified), OPS-089 (`database.py:377-403`; no FK grep), OPS-092 (`database.py:1293-1304,1796-1813`), OPS-093 (`timezone_util.py:6-23`), OPS-094 (grep: no VACUUM/backup).

---

## 4. Missing and duplicated features in the merged spec

### Duplicated behaviour claims (same or near-same behaviour under two IDs)

| Canonical ID to keep | Duplicate(s) | Note |
|---|---|---|
| SER-074 | REL-042 | Identical `/api/search` claim (alias-aware search, no UI consumer) appears in both domains |
| SER-056…SER-065 | REL-037 | The whole per-series 3-phase refresh is claimed once in REL and again as SER-056-065; REL-037 should reference SER instead of restating phases |
| SER-083 | REL-038, OPS-082 | Release-cache preview/clear + audit is claimed three times |
| SER-054 | REL-015 | Discovery "Track Series" stable-key/re-key/notify is claimed twice |
| SER-086 | REL-016 | Blacklist upsert/veto behaviour is claimed in both SER and REL |
| SER-077/SER-078 | OPS-079 | False-series audit/repair claimed twice |
| SER-079/SER-080 | OPS-078 | Attribution audit/repair claimed twice |
| SER-081/SER-082 | OPS-077 | Suspicious-volume-range audit/repair claimed twice |
| SER-084 | OPS-033 | Safe maintenance clear claimed twice |
| SER-085 | OPS-029, OPS-030, OPS-032 | Debug gate/reset/re-key action claimed across SER and OPS |
| SER-071 | OPS-076 | Provider-tag refresh claimed twice |
| SER-087 | OPS-039 | Storage-health probe claimed twice |
| SER-026 / SER-030 / SER-027 | OPS-044 / OPS-045 / OPS-062 | Calibre probe/status/sync claims overlap across SER and OPS |
| SER-001…SER-005, SER-006…SER-020 | UI-028…UI-034, UI-035…UI-044 | Dashboard/series UI behaviour duplicated by the UI part (expected for an audit part, but it doubles voting cost) |
| SER-088/089/090 | UI-014/UI-015/UI-011 | Global banner/task-panel behaviour duplicated with the UI part |

Recommendation: keep the domain that *owns* the code as canonical and have the other part reference the ID, rather than restating the claim; otherwise a single correction must be edited in 2-3 places (as OPS-059/UI-011 and REL-018/Part 0 show).

### Missing claims (behaviours documented in parts but absent from the registry)

| Proposed ID | Missing claim | Evidence |
|---|---|---|
| REL-043 | `GET /api/releases/series_releases` re-derives the full group list with a search (cache bypass), matches `series_key`/`series_name` case-insensitively, slices releases and ignores `prioritized_only`/`sort`, so "Show all" on `/prioritized` can reveal non-prioritized releases | `backend/app/main.py:1471-1501`; Part 1 §3.3 |
| REL-044 | `GET /api/prioritized/releases` returns up to 500 prioritized rows for an optional status with no pagination/total and post-sorts by priority | `backend/app/main.py:4130-4133`; `database.py:4616-4668` (no registry line) |
| OPS-095 | `POST /api/settings/test-webhook` performs real outbound Discord/generic test posts (10 s timeout), returns per-channel results, and is the only settings endpoint that exercises webhook URLs | `main.py:4281-4289`; `notifier/webhook.py:253-335` |
| OPS-096 | `POST /api/calibre/sync` has no `data-task-kind` binding despite creating a persisted task, so the UI cannot disable or reattach to it across reloads | `main.py:1909-1930`; R-25 in Part 4; no registry line |
| DL-082 | `GET /api/downloader/queue` is a dead route with no template caller (Part 0 §6 mentions it as "documented dead surface" only) | `backend/app/main.py:3578`; grep: no template consumer |
| DL-083 | Wanted-set authority: a persisted `download_wanted_json` overrides the stored missing list, which overrides a fresh matcher computation in `resolve_release_missing_volumes` | `backend/app/scheduler.py:1029-1053`; only in Part 0 §3.2 invariant 4 |
| SER-093 | Ownership is Calibre-owned ∪ staged volumes (bundle-expanded, 60 s cached staged map) and this union is what drives missing/IN_STAGING decisions; currently spread across SER-034/SER-064 and DL-078 without one SER claim | `backend/app/calibre/matcher.py:127-131,146-185`; `staged_ownership.py:54-152` |
| SER-094 | A planned/missing claim for "`/series` row latest-release cover" is already SER-009; no action needed. (ID reserved to keep numbering stable if the panel adds the ownership claim above as SER-093.) | — |

---

## 5. Cross-domain inconsistencies

1. **IN_CALIBRE "terminal"** — Part 0 §4.3, REL-018 and Part 1 §3.7 call it terminal, while Part 1 §3.21's matrix and `database.py:299-307` show `IN_CALIBRE + REQUESTED → REQUESTED` on the equal-rank tie. All four statements must be unified (corrected wording in §2/§3 tables above).
2. **Nyaa end-of-results** — REL-025 says "404/empty"; Part 1 §3.11 says "only HTTP 404"; code ends on 404 *or* a parsed page with zero rows, and treats empty bodies as retryable failures (`providers/nyaa.py:320-338,207-208`). Part 1 §3.11 must be corrected.
3. **Nyaa base URL precedence** — REL-039 says the setting/env is the "single source"; Part 1 §3.11 and `providers/registry.py:40-41` say a per-feed `config.base_url` wins. Registry must state the precedence chain.
4. **Task-kind contract** — Part 0 §3.2 invariant 7 and UI-012 say a button with `data-task-kind` is disabled while a matching task runs, but Part 4 §6.1 documents that the light-novel audit and false-series audit endpoints create no task row, so those buttons can never disable (`main.py:2461-2474,2508-2516`). Either the endpoints must create tasks or the invariant/UI claim must be scoped.
5. **Cancel UI** — UI-011 claims the Tasks panel renders Cancel, OPS-059 claims the button never renders; code confirms OPS-059 (`base.html:1039`; `background_tasks.py:51-76`). UI-011 must be reworded.
6. **"All automated writes pass `enforce_precedence=True`"** — Part 0 §4.3 overstates: `upsert_release` and `update_release_volumes_and_status` route through `resolve_status` internally rather than taking the `enforce_precedence` flag; several automated callers never pass it. Reword as "all automated status writes go through `resolve_status` (via the enforce-precedence wrappers or directly)".
7. **qBittorrent category/tags "ignored"** — Part 1 §7.10 and REL-021 say the settings are ignored; `add_torrent` does hardcode them, but `qbit_category` is still read for the `/api/downloads/status` torrent list (`main.py:606`). Scope the "ignored" statement to release-download submission.
8. **Part 0 §6 vs §1.2 route counts** — internal arithmetic inconsistency (sum 120 vs 111), already listed as P0-02.
9. **Part 0 §2.2 vs OPS-011 exemption list** — Part 0 omits `/static/*`, `/favicon.ico`, `/healthz` (P0-05).

---

## 6. Self-check — my own SER-001…SER-092 lines as extracted into the registry

Programmatic diff of the registry's SER block against `docs/design/parts/02_series_library.md` §8 shows **92/92 lines identical (no lost or altered text)**. All 92 are claimed as written and I re-confirm them AGREE; the following wording notes are the only issues:

| ID | Note | Corrected wording (if the panel wants precision) |
|---|---|---|
| SER-004 | "that capped list length" conflates two caps: the badge prints the length of the 10-row fetch, not the 8 rendered cards | "The Overview wanted list is built from `get_pending_requests(limit=10)`, renders at most 8 cards, and its section badge shows the length of that 10-row fetch (min(total,10)), not the true pending total." |
| SER-019 | The registry line drops my spec's note that the modal sends no `source`, so the Calibre list is name-ordered, not similarity-ranked | "The match-to-Calibre modal searches `/api/calibre/series` without `source` (so results are name-ordered, not similarity-ranked) and POSTs `{calibre_series_id}` to `/api/series/{id}/match-calibre`, which merges/links trackers and re-keys releases while preserving release statuses." |
| SER-037 | Grammar typo in the registry: "merges/link trackers" | "…merges duplicate trackers (MAX monitored, union owned volumes)…" |
| SER-022 | Fields listed are only returned when the library is available; the unavailable payload has no `db_path`/`error` guarantee | No change needed if read as "when available"; else add "when available". |

No SER claim was contradicted by the code checks performed in this round; the SER open questions Q1/Q2/Q3/Q4/Q6/Q8 etc. remain valid and are still evidenced in `02_series_library.md` §7.

---

## 7. Other registry sections (vote-by-exception)

Per-section counts for this reviewer: Part 0 — DISAGREE; REL — 5 disagreements / 42; SER — 0 / 92; DL — 0 / 81; OPS — 6 / 94; UI — 1 / 136.

- **SER-001…SER-092 — no disagreements** (92/92 identical to my authored checklist; wording notes in §6 above only).
- **DL-001…DL-081 — no disagreements identified in this round.** Spot-checked: DL-032 (`backend/app/main.py:3738-3824`, 409 for IN_STAGING), DL-033 (`backend/app/main.py:1314-1361` state mapping), DL-042 (`backend/app/main.py:535-632` never-raise degraded payload), DL-045 (`backend/app/main.py:2213-2217` diagnostics endpoint), DL-046 (`backend/app/scheduler.py:1421-1453` routing requires a template, leaves unresolved files, summarises awaiting), DL-056 (`backend/app/scheduler.py:5191-5219,5252-5259` 45 s and 300 s jobs), DL-078 (`backend/app/calibre/matcher.py:127-131`; `backend/app/staged_ownership.py:116-129` 60 s cache), DL-079 (`backend/app/scheduler.py:2329-2459` PENDING_DEDUPE sweep).
- **UI-001…UI-136 — one disagreement: UI-011** (see §3 table; Cancel never renders). Spot-checked and AGREE: UI-004/UI-005 (`backend/app/templates/base.html:122-190` — five `data-nav-item` items, manual-pickup link is a sixth flat link and `#nav-manual-pickup-count` is never populated), UI-014/UI-015 (`base.html:333-365,691-751`), UI-028…UI-033 (`index.html:20-100,136-262`), UI-035…UI-044 (`series.html:32-96,101-254,353-435`). No other UI disagreements raised; the assigned UI reviewers should still confirm the remaining lines.

---

## 8. Global verdict

**GLOBAL VERDICT: DISAGREE**

Blocking items (must be fixed before any AGREE):

1. **P0-01** — Part 0's cross-reference IDs are wrong throughout §1.1, §3.2, §4.2, §4.4, §4.7 and §4.9; every ID must be regenerated from the final registry.
2. **P0-02** — Part 0 §6's domain route counts contradict §1.2 (sum 120 vs 111); corrected measured partition is REL 30, SER 24, DL 42, OPS 14 + debug 1.
3. **IN_CALIBRE terminal contradiction** — Part 0 §4.3, REL-018, Part 1 §3.7 vs Part 1 §3.21 and `database.py:299-307`; unify to the code-exact rule (equal-rank tie goes to incoming, so `IN_CALIBRE → REQUESTED` is possible).
4. **REL-018 and REL-039** — reword "terminal" and "single source for every Nyaa URL" as specified above.
5. **REL-038** — reword the absolute "every write invalidates the grouped cache" claim.
6. **OPS-079** — false-series repair does not re-validate selected ids against the audit; the claim overstates its safety.
7. **OPS-019, OPS-030, OPS-087, OPS-090, OPS-091, UI-011** — apply the precision corrections in §3 (notably OPS-090's default-key count: 41 seeded, 43 after startup markers, not "45+").
8. **Part 1 §3.11 / Part 0 §5** — fix the "only HTTP 404" end-of-results statement, and scope or drop the "never write NEW" rule so it no longer contradicts real code paths (`main.py:3890-3895`, `calibre/matcher.py:209-214`).

Non-blocking but recommended in the same amendment: split the duplicated claims listed in §4 (or mark the duplicate IDs as aliases of the canonical one) and add the missing claims REL-043, REL-044, OPS-095, OPS-096, DL-082, DL-083, SER-093 so the "445 claims" sheet is not silently incomplete.
