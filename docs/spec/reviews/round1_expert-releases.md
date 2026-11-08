# Consensus Round 1 — review by expert-releases (REL author)

**Reviewer:** expert-releases · **Task:** task-6 · **Scope:** Part 0, Part 2 (SER-001…SER-092), Part 3 (DL-001…DL-081), all 445 registry claims, self-check of REL-001…REL-042.
**Code base checked:** commit `86a7bd8`, `backend/app/**`, `backend/app/templates/**`, `tests/**`. No spec or code file was edited by this review.

**Counts:** 445/445 registry claims reviewed (by exception, per section) · ≈140 claim slots verified against code with `file:line` evidence (REL 42/42 identity/self-check, SER ≈75, DL ≈25 — spot-check log in §6) · 7 DISAGREE rows (2 SER, 4 DL, 1 self-flagged REL) · 12 blockers · 0 unverifiable claims.

---

## 1. Part 0 verdict — **DISAGREE**

Architecture/data-model statements are accurate: 125 route decorators (`backend/app/main.py` grep: 125), 13 tables, WAL/new-connection model, one worker + one Torbox poller + 3-worker task executor, 5 s grouped / 1 s downloader-status / 60 s staged-ownership caches, feed-poll/backfill mutexes. Two structural Part 0 statements are **wrong**, five workflow/cross-cutting statements are **materially wrong**, and ≥12 cross-reference IDs point to the wrong registry rows (they do not match the shipped registry — Part 0 appears to cite an earlier registry revision).

| # | Part 0 statement (location) | Why it is wrong | Corrected statement | Evidence |
|---|---|---|---|---|
| P0-1 | §4.3 lifecycle diagram: `REQUESTED ──download/completed──▶ IN_STAGING` | A download completing writes `download_status=COMPLETED`, not `status=IN_STAGING`. `IN_STAGING` is set by the matcher/reconcile when all volumes are staged (or by `resolve_status` promotion from a completed reconcile), and the Releases UI labels a completed-but-not-routed download "Downloaded (awaiting staging)". | `REQUESTED ──download completed──▶ (download_status=COMPLETED; label "downloaded") ──routed/staged + reconcile──▶ IN_STAGING ──Calibre import/sync──▶ IN_CALIBRE` | `backend/app/main.py:1314-1361`; `backend/app/calibre/matcher.py:153-172`; `backend/app/database.py:4380-4404` |
| P0-2 | §4.3 diagram: `IGNORED ◀──unignore──┘` | Arrow direction is inverted: Unignore moves `IGNORED → REQUESTED`, it does not write IGNORED. | `IGNORED ──unignore (user)──▶ REQUESTED` | `backend/app/main.py:4088-4093`; `REL-019` |
| P0-3 | §4.3 "Automated writes: feed ingestion, Calibre sync, download completion, cleanup — all pass `enforce_precedence=True`." | Overgeneralised. Ingestion/reconcile pass precedence, but several automated **cleanup/repair** paths write status verbatim or by target: LN cleanup ignore (`IGNORED`), volume-range repair (`IGNORED`), staging-duplicate purge promotion (`IN_CALIBRE` via precedence helper but direct), blacklist/track/ignore endpoints are explicit user writes. Download completion writes no `status` at all. | "Ingestion, Calibre-sync request resolution and volume reconcile write status through `resolve_status`; explicit user endpoints and support/repair actions (blacklist, ignore/unignore, status, LN cleanup, volume-range repair) write their target status deliberately." | `backend/app/ln_releases.py:46-53`; `backend/app/database.py:1724-1729,2353-2357`; `backend/app/main.py:4074-4093`; `backend/app/scheduler.py:3153-3175` |
| P0-4 | §4.2 step 4 "…drops blacklisted/duplicate/non-manga releases" | Wrong for blacklisted and duplicates. A release of a blacklisted series is **stored** with `status='BLACKLISTED'` (so it is visible under the Ignored/Blacklisted filters and can be un-blacklisted); duplicate guids are upserted, not dropped. | "…discards chapter-only/LN/JPEG-XL rows, stores blacklisted-series releases with `BLACKLISTED` status, and upserts duplicates by `guid`." | `backend/app/scheduler.py:3573-3577,3638`; `backend/app/database.py:2461-2571`; `REL-016`, `SER-086` |
| P0-5 | §5 rule 2 "never write a status of `NEW` — it is an internal recomputable value" | Factually false: feed ingestion persists `NEW` for unmatched releases whose volume set is not `[1.0]` (matcher returns NEW, `upsert_release` stores it), and `_STATUS_RANK` gives `NEW` a rank because it is a stored, recomputable status. Only the user/API enum excludes NEW. | "Automation may write `NEW` for unmatched non-volume-1 releases; user/API endpoints must not set `NEW` (the status endpoint enum excludes it)." | `backend/app/calibre/matcher.py:196-214`; `backend/app/scheduler.py:3579-3603`; `backend/app/database.py:253-261,2559` |
| P0-6 | §4.9 "`basic`: env-configured user/pass; **session cookie**; same gate." | Wrong: Basic mode authenticates per request from the `Authorization` header; only OIDC issues the HMAC session cookie. There is no session cookie in basic mode. | "`basic`: HTTP Basic credentials checked per request (`compare_digest`); no session cookie. `oidc`: login → callback → signed session cookie." | `backend/app/auth.py:992-997,1056-1058`; `OPS-013`, `OPS-017` |
| P0-7 | Cross-references (list) | ≥12 IDs point at unrelated claims: §3.2 inv1 `SER-019/SER-020` (should be SER-045/SER-067); inv2 `SER-021/OPS-078` (REL-018/SER-086); inv3 `SER-025/DL-036` (SER-024/DL-078); inv5 `REL-030/OPS-079` (REL-033/REL-034/OPS-091); inv6 `SER-018/R-4` (no claim — Part 2 Q4); inv7 `OPS-058` (UI-012/OPS-059); §1.1 `OPS-009/R-9` for "no roles" (OPS-009 is env seeding); §1.5 `OPS-073/R-8` for "no backup/restore" (OPS-073 is the worker's first action); §4.4 `REL-031` for hard-coded qBit category/tags (REL-031 is collected-batch inference); inv4 `DL-033` for wanted-set authority (DL-033 is the derived state payload). | Re-map every Part 0 citation to the shipped `FEATURE_REGISTRY_v0.9.md`; add the two missing claims in §5 (M1, M2). | `FEATURE_REGISTRY_v0.9.md:69-160,162-244,246-341`; §5 below |
| P0-8 | §0.2/cover reference `FEATURE_REGISTRY.md` | The shipped registry file is `FEATURE_REGISTRY_v0.9.md`; `FEATURE_REGISTRY.md` does not exist. | Use the actual filename (or ship a copy). | `docs/spec/` listing |

Everything else checked in Part 0 (route count, 10 HTML pages, 3 auth routes, `/health`, thread model, caches, table list, name-join model, auth exempt paths, R-1…R-6 index pointers) is accurate.

---

## 2. Part 2 (SER) verdict — **DISAGREE** (2 claim rows)

Part 2 is the strongest part: I verified 55 SER claims against code and found the body prose exact (sync upsert rules, auto-link ladder, link/merge service, blocked-batch reconcile, startup order, calibration thresholds 0.70/0.80/0.90, cover limits, mtime cache, synthetic ids, vestigial helpers). Two registry lines are imprecise/incorrect. No SER claim is behaviourally wrong apart from these.

| Claim | Why it is wrong/incomplete | Corrected claim | Evidence |
|---|---|---|---|
| SER-018 | Says pages are fetched "with an IntersectionObserver auto-fetching the next batch and a Load More button fallback". In code each batch **self-schedules the next after 60 ms while `has_more`**, so the whole library is fetched without scrolling; the observer merely calls the same `fetchNextSeriesRows` and is practically redundant; the Load More button is `hidden` in markup and is only revealed when a fetch throws. | "Series rows load in 50-row batches via `/api/series/rows`; after each batch the page schedules the next fetch 60 ms later while `has_more` is true (the whole library is fetched without scrolling). An IntersectionObserver on the load-more container triggers the same fetch, and the Load More button (hidden by default) is only revealed when a batch fetch errors." | `backend/app/templates/series.html:108-169` (self-chain at 160; error reveal at 164-165), `:171-192,236-246`, `:63-68`; contrast accurate `UI-038` |
| SER-032 | "fuzzy requires score ≥ 0.90" — the index accepts only scores **strictly greater** than the threshold (`best_score = threshold`, `if score <= best_score: continue`), and SER-042/A§2.4.3 correctly say "strictly above". | "…a unique exact candidate wins; fuzzy matching uses `CalibreSeriesIndex.match(threshold=0.90)`, which accepts only scores strictly above 0.90; multiple exact candidates are left unlinked." | `backend/app/parser.py:1829,1890-1903`; `backend/app/database.py:2064`; cf. `SER-042` |

Non-blocking precision note (not voted DISAGREE): SER-034's "re-checks active releases" scans only `get_releases(limit=5000)` (`backend/app/scheduler.py:3279`), so the pool is capped at 5000 regardless of the reconcile batch size; consider adding "within the newest 5000 active rows".

---

## 3. Part 3 (DL) verdict — **DISAGREE** (4 claim rows)

Part 3 is accurate on substance: I verified 24 DL claims (qBit 8 s test cache, add_torrent body/Fails, selective-file rules incl. `single_archive`, 30 s filter pass, Torbox constants 20/90 %/16 MiB/10 MiB/5 redirects, typed errors, state normalisation, planner strictness, poll cadence, retry/remove/clear-failed semantics, staging-tag foreground behaviour, inventory constants, QBitDownloader vestigial). Four registry lines miscarry numeric/edge wording; three of them contradict Part 3's own body text or code.

| Claim | Why it is wrong/incomplete | Corrected claim | Evidence |
|---|---|---|---|
| DL-017 | "Submission is refused **above** `torbox_max_concurrent`" — code refuses when the active (non-queued) count **reaches** the limit (`len(active) >= max_concurrent`), i.e. the next submit is refused at the cap, not above it. Part 3 §3.3 itself says "at/above". | "Submission is refused when the number of active (non-queued) Torbox downloads is already at or above `torbox_max_concurrent` (default 2); any active row older than `torbox_timeout_hours` is marked FAILED with a message saying the release stays REQUESTED for retry." | `backend/app/scheduler.py:1495-1510`; body `APP_DESIGN_SPEC_v0.9.md` §3.3 |
| DL-018 | "returns `zip` **only** for ≤20 fully-needed files whose estimated size is ≤90 % of the cap" — the planner also returns `zip` when the estimated total is unknown/0 (`not estimated_total or estimated_total <= 90 %`); the size cap is then enforced during streaming, not before the link request. | "returns `zip` for a fully-needed torrent with ≤20 files when the estimated total size is unknown or ≤90 % of the configured cap (streaming enforces the cap in the unknown-size case); otherwise it returns exact per-file selections and raises `DownloaderFileError` for a missing file list, any unmappable name, or any absent wanted volume." | `backend/app/scheduler.py:2199-2216`; `backend/app/downloader/torbox.py:1320-1338` |
| DL-021 | "retried up to 3 times with **1/2/4 s** backoff" — the loop has 3 attempts total and sleeps 1 s then 2 s (backoff map's 4 s entry is unreachable because retry requires `attempt < 3`). | "…3 attempts total, re-requesting a fresh `requestdl` link on every attempt and sleeping 1 s then 2 s between them; persistent 403/404/410/416/429/503 failure reports the attempt count." | `backend/app/downloader/torbox.py:1266-1297` (backoff `{1:1.0,2:2.0,3:4.0}`, gate at 1282) |
| DL-041 | "New holds are refused **above** `torbox_manual_pickup_max_items`" — code refuses when active pickups are already **at** the cap (`count_active >= max_items`), so with the default 10 the 11th hold is refused because 10 exist, not because 11 exist. | "New holds are refused once the number of active pickups is at or above `torbox_manual_pickup_max_items` (0 = unlimited), and the 5-minute sweep expires past-due holds by removing the remote torrent and failing the release." | `backend/app/scheduler.py:1648-1657,1684-1718` |

Non-blocking precision note: DL-003 is accurate, but neither DL-003 nor Part 3 §5.1 records that `qbit_category`/`qbit_tags` **settings are written but not used by the release-add paths** (hard-coded `manga`/`mangatrax` at both call sites) — see M4.

---

## 4. Missing / duplicated features

**Missing claims (add to the owning part, then registry).**
1. **M1 — `download_wanted_json` authority.** Part 0 §3.2 inv4 asserts it as an invariant but no registry claim states it; Part 3 §3.13 depends on it. Suggest **DL-082**: "`resolve_release_missing_volumes` prefers `download_wanted_json` over `missing_vols_json`/`volumes_json`, then a fresh matcher computation; every correction path (manual pickup volumes, retries) must write it." Evidence `backend/app/scheduler.py:1029-1053,1757-1776`.
2. **M2 — Calibre rename/delete does not propagate.** Part 0 §3.2 inv6 asserts it; no claim exists (only Part 2 Q4). Suggest **SER-093**: "`sync_calibre_series` only inserts/updates; a Calibre series removed/renamed in the library leaves a stale `series_tracking` row and stale `releases.series_name` values indefinitely." Evidence `backend/app/database.py:1846-1870`.
3. **M3 — false-series audit target ≠ repair target.** No claim/Q covers it: the audit's `suggested_series` is the fixed Calibre match when one exists, but repair writes the **title-parsed** series, so the suggested and applied names can differ. Suggest a new Part 2 open question and a sentence in SER-077/078. Evidence `backend/app/false_series_matches.py:55-67` vs `:99-126`.
4. **M4 — qBittorrent category/tag settings are inert for release adds.** Part 3 §5.1's row claims settings drive "queue + add"; both add paths hardcode `"manga"`/`"mangatrax"`. Suggest **DL-083**: "`qbit_category`/`qbit_tags` are persisted and used for the queue filter in some paths but the release-add paths hard-code `manga`/`mangatrax`; changing the settings does not change submitted torrent tags." Evidence `backend/app/main.py:3565-3566,3792-3797`; `backend/app/scheduler.py:3744-3750`.
5. **M5 — empty/zero-result poll notification guard.** Claimed behaviour (REL-040) exists but the exact suppression predicate (`new_releases/transitions/requested/sent_to_downloader`) is only in body prose; acceptable as REL-040, no new claim needed — noted so the panel does not duplicate it.

**Duplicated claims (all currently consistent; risk is future drift). Recommend "owner + reference" wording or a merge in v1.0.**
* `REL-021` = `DL-032` + `DL-033` (manual release download + derived state).
* `REL-015` = `SER-054` (Discovery Track).
* `REL-016` = `SER-086` (blacklist coupling/restoration).
* `REL-035` = `DL-079` + `DL-080` (auto-download / in-flight dedupe).
* `REL-038` = `SER-083` = `OPS-082` (release-cache preview/clear).
* `REL-042` = `SER-074` (unified search API unused by UI).
* `REL-026` = `OPS-063` + `OPS-064` (feed poll serialisation/async).
* `REL-036`/`REL-037` overlap `SER-056`…`SER-064` and `OPS-074`/`OPS-075` (backfill vs series refresh); wording is consistent today.
* `REL-033` = `OPS-080` (LN cleanup), `REL-034` = `OPS-091` (media re-typing), `REL-028` = `OPS-076`/`OPS-078` territory.

---

## 5. Cross-domain inconsistencies

| # | Inconsistency | Evidence / recommended resolution |
|---|---|---|
| X1 | Part 0's citation set is from a different registry revision (≥12 wrong IDs, §1 P0-7). Parts 2/3 cite correctly; Part 0 is the outlier. | Re-map all Part 0 refs against `FEATURE_REGISTRY_v0.9.md`; blocker B7. |
| X2 | Part 0 §5 rule 2 ("never write NEW") contradicts REL-018 (rank table includes NEW), REL-020 (only the API enum excludes it), and ingestion code. | Fix rule wording (P0-5); blocker B8. |
| X3 | Part 0 §4.2 step 4 ("drops blacklisted releases") contradicts REL-016/SER-086 (blacklist stores `BLACKLISTED` rows) and scheduler code. | Fix Part 0 (P0-4); blocker B9. |
| X4 | Part 0 §4.9 says Basic mode has a session cookie; OPS-013/OPS-017 define sessions for OIDC only, and `auth.py` resolves Basic per request. | Fix Part 0 (P0-6); blocker B10. |
| X5 | `qbit_category`/`qbit_tags`: Part 3 §5.1 implies they drive the add; Part 3 §3.2/DL-003 describe hard-coded values; Part 0 §4.4 cites `REL-031` (unrelated). | Add DL-083 (M4) and one consistent sentence in Parts 0/3. |
| X6 | SER-032 says fuzzy needs "≥ 0.90" while SER-042/Part 2 §2.4.3 say "strictly above the threshold" for the same matcher. | Adopt ">"; blocker B2. |
| X7 | DL-017/DL-041 say "above", Part 3 §3.3 says "at/above", code is `>=`. | Adopt "at or above"; blockers B4/B6. |
| X8 | Part 0 §3.2 inv4 (wanted-set authority) has no registry claim while Part 3 §3.13 relies on it. | Add DL-082 (M1). |
| X9 | Part 0 §4.3 calls the status/ignore endpoints "PATCH-style"; they are POST (`/api/releases/{id}/status`, `/ignore`, `/unignore`). | Reword to POST. |
| X10 | Part 0 §7 says open questions are "indexed in §8 of this document"; Part 0's index is §7. | Renumber or reword. |

Also noted (not a claim contradiction): the false-series audit/repair target mismatch (M3) is a code-vs-UI defect no part documents.

---

## 6. Spot-check log (foreign claims verified against code; ≥15 required)

| Claim | Evidence checked | Result |
|---|---|---|
| SER-001/002/003/004/005 | `main.py:1138-1167,1140-1150,1144-1149,1165`; `database.py:3525-3551,3059-3060`; `index.html:20-31,35-100,103-133,136-160` | correct |
| SER-006/008/010/011/012/013 | `main.py:1169-1180,3826-3837`; `database.py:2145-2158`; `series_table_row_chunk.html:2-66`; `tag_display.py:9-14` | correct |
| SER-014 | `series.html:269-301` (`max_pages: 3` at 281); `main.py:4049-4062` | correct |
| SER-016 | `series.html:25-28`; `base.html:516-539` | correct |
| SER-017/018/019 | `series.html:108-192,194-246,353-435` | 017/019 correct; 018 DISAGREE (D1) |
| SER-021/022/023/024/025/026 | `scanner.py:24-52,43-80,82-188,190-243,153-181`; `main.py:1937-1953` | correct |
| SER-027/028/029/030 | `scheduler.py:3115-3209,3212-3249,3243-3249`; `main.py:1909-1935` | correct |
| SER-031/032/033/034/035 | `database.py:1816-1870,2020-2085`; `scheduler.py:3153-3210,3251-3322` | 031/033/034/035 correct; 032 DISAGREE (D2) |
| SER-036/037 | `main.py:2278-2325`; `database.py:1872-2018` | correct |
| SER-038/039 | `main.py:3962-4010`; grep: no template caller | correct |
| SER-045/046/047/048/049 | `scheduler.py:622-788,556-620,3968-3999` | correct |
| SER-054/055 | `database.py:3277-3361,2313-2339`; `main.py:4135-4171` | correct |
| SER-056/057/058/061/062/063/064/065 | `main.py:4013-4072`; `scheduler.py:4460-4753,4438-4458,4525-4535,4717-4721` | 065 defect confirmed; rest correct |
| SER-066/067/069/070/071/072/073 | `anilist.py:17-83`; `database.py:328-357`; `covers.py:20-24`; `main.py:1955-1977`; `tag_display.py:9-14,30-107`; `database.py:1339-1407` | correct |
| SER-074/075/076 | grep `/api/search` in templates = none; `main.py:2540-2584,2328-2458` | 074 correct; 075/076 consistent (not read line-by-line) |
| SER-077/078/079/080/081/082 | `false_series_matches.py:25-82,85-151`; `main.py:2461-2570,2631-2661`; `database.py:1712-1742` | correct (M3 nuance noted) |
| SER-083/084/085/086/087/088/089/090/091/092 | `main.py:2587-2628,4323-4338,4342-4388,4095-4108`; `maintenance.py`; grep storage partials; `base.html:333-365,645-689,1075-1097`; `test_ui_polish.py:168-200`; repo grep `get_series_by_id` = definition only | correct |
| DL-002/003/004/005/008 | `qbit.py:76-140,142-189,285-368`; `main.py:3548-3601` | correct |
| DL-006/007 | `scheduler.py:4912-4974,999-1003`; grep `build_downloader(qbit_client=…)` = only unused getter | correct |
| DL-015/016/017 | `torbox.py:68-76,617-637`; `scheduler.py:2815-2835,1495-1510` | 015/016 correct; 017 DISAGREE (D4) |
| DL-018/021 | `scheduler.py:2087-2251`; `torbox.py:1266-1297,1320-1338` | DISAGREE (D3, D5) |
| DL-026/029/030/031 | `scheduler.py:1640-1718`; `main.py:3604-3728` | correct |
| DL-041 | `scheduler.py:1648-1657` | DISAGREE (D6) |
| DL-042/043/044/058 | `main.py:473-478,52-70,2104-2123,2126-2143`; `downloads_inventory.py:20-23` | correct |
| DL-054 | `main.py:2076-2099` (foreground await, no task) | correct |
| DL-079/080 | `scheduler.py:1248-1304,2329-2408,3676-3781` | correct (duplicates REL-035) |

---

## 7. Self-check — REL-001…REL-042 in the registry and merged Part 1

* **Identity check passed.** All 42 REL registry lines match the authored consensus checklist character-for-character, and merged Part 1 (`APP_DESIGN_SPEC_v0.9.md:544-1257`) is identical to `docs/spec/01_releases_feeds.md` (709 lines, 0 content diffs after trimming merge wrapper lines). No truncation, no lost text, no renumbering.
* **One self-flagged imprecision (voted DISAGREE, blocker B1):** **REL-018** says "Automated status writes go through `resolve_status`" — overbroad in the same way as Part 0 P0-3. Ingestion, request resolution and reconcile do; support/repair actions and explicit endpoints write status verbatim (`ln_releases.py:46-53`, `database.py:1724-1729,2353-2357`, `main.py:4074-4093`, `scheduler.py:1640-1675` writes `MANUAL_PICKUP` to `download_status` not `status`). Corrected REL-018: "Automated **ingestion/reconcile** status writes go through `resolve_status`, which never clears a status with a blank value, never lifts an existing IGNORED/BLACKLISTED veto, treats IN_CALIBRE as terminal, and otherwise keeps the higher-ranked status; explicit user and support/repair paths write their target status deliberately."
* All other 41 REL lines still match my intent and the code; no other self-corrections requested.

---

## 8. Global verdict

**GLOBAL VERDICT: DISAGREE**

Numbered blockers (claim wording must be amended before v1.0; all are specific and cheap to fix):

1. **B1 (REL-018, self-flagged):** "Automated status writes go through `resolve_status`" must be scoped to ingestion/reconcile; repair/cleanup/user paths write status deliberately (`ln_releases.py:46-53`; `database.py:1724-1729`; `main.py:4074-4093`).
2. **B2 (SER-018):** replace the "IntersectionObserver auto-fetches / Load More fallback" mechanism with the actual 60 ms self-scheduling chain (hidden Load More is error-only); `series.html:108-169,236-246`.
3. **B3 (SER-032):** change "score ≥ 0.90" to "score strictly > 0.90" to match `parser.py:1890-1903` and SER-042.
4. **B4 (DL-017):** change "refused above `torbox_max_concurrent`" to "refused at or above" (`scheduler.py:1503`).
5. **B5 (DL-018):** zip selection also occurs when the size estimate is unknown; phrase the rule accordingly (`scheduler.py:2206-2209`; `torbox.py:1320-1338`).
6. **B6 (DL-021):** backoff is 1 s then 2 s across 3 attempts; the 4 s entry is unreachable (`torbox.py:1275-1290`).
7. **B7 (DL-041):** "above `torbox_manual_pickup_max_items`" → "at or above" (`scheduler.py:1651`).
8. **B8 (Part 0 §5 rule 2):** remove/replace "never write a status of NEW" — NEW is persisted by ingestion (`matcher.py:196-214`; `scheduler.py:3579-3603`; `database.py:2559`).
9. **B9 (Part 0 §4.2 step 4):** blacklisted releases are stored as `BLACKLISTED`, not dropped (`scheduler.py:3573-3577`; REL-016).
10. **B10 (Part 0 §4.9):** Basic auth has no session cookie; sessions are OIDC-only (`auth.py:992-997,1056-1058`; OPS-013/017).
11. **B11 (Part 0 §4.3):** fix the lifecycle diagram (download-completed is `download_status`, not `IN_STAGING`; unignore arrow is `IGNORED → REQUESTED`) and reword the "all automated writes pass precedence" claim (`main.py:1314-1361`; `matcher.py:153-172`; `main.py:4088-4093`).
12. **B12 (Part 0 cross-references):** re-map all Part 0 claim citations to the shipped registry (≥12 are wrong, list in P0-7); add missing claims M1/M2 and the Part 2 precision note from §2.

Resolutions for M3/M4 (missing claims) and the duplicated-claim ownership table can be handled editorially in round 2 and do not need to block the vote once B1–B12 are addressed.
