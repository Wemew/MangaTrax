# Consensus Round 2 — expert-downloads (DL owner)

**Reviewer:** expert-downloads (author of Part 3 / DL-001…DL-084)
**Round-1 blockers:** UI-094, UI-099, UI-101, OPS-067, six Part 0 corrections, DL-042/DL-081 precision notes
**Inputs read:** `docs/spec/CORRECTIONS_ROUND1.md`; all five `docs/spec/reviews/round1_*.md`; `docs/spec/FEATURE_REGISTRY_v0.10.md` (all 457 lines, section-by-section diff against v0.9); `docs/spec/APP_DESIGN_SPEC_v0.10.md` (Part 0 + Parts 2 and 5, with targeted re-reads of every corrected claim); `backend/app/**` and `backend/app/templates/**` for verification. No spec/code edits; this file is the only write.

## 1. Vote summary

| Section | Claims | Disagreements | Vote | What was verified this round |
|---|---|---|---|---|
| Part 0 (no claim IDs) | — | 0 blocking; 3 residual editorial refs | **AGREE WITH EDITORIAL FIXES** | all six of my round-1 Part 0 corrections re-read at their new locations; route/settings/auth counts re-derived |
| REL | 44 | 1 (REL-043) | **DISAGREE** | REL-018, REL-024, REL-039, REL-043, REL-044 verified against code |
| SER | 94 | 0 | **AGREE** | SER-004, SER-018, SER-019, SER-032, SER-034, SER-090, SER-093, SER-094 verified against code |
| DL | 84 | 0 | **AGREE** | 84/84 registry lines identical to my Part 3 checklist; the 7 rewordings re-verified against code |
| OPS | 98 | 1 (OPS-087) | **DISAGREE** | OPS-030, OPS-061, OPS-079, OPS-087, OPS-090, OPS-091, OPS-095, OPS-096, OPS-097, OPS-098 verified against code |
| UI | 137 | 0 | **AGREE** | UI-011, UI-020, UI-090, UI-094, UI-099, UI-101, UI-103, UI-128, UI-137 verified against templates/JS |
| **Total** | **457** | **2** | **GLOBAL: DISAGREE** | — |

## 2. My round-1 blockers — all resolved (verified)

| Round-1 item | v0.10 state | Verification |
|---|---|---|
| UI-094 (task binding works for one button) | Reworded exactly as proposed: "`/api/staging/tag` is a foreground request that creates no persisted task, so neither button ever gets task-panel reattachment or busy state" | `main.py:2076-2099` (no `_recorded_task`/`run_in_background`); `downloads.html:47`; `base.html:1074-1091` |
| UI-099 (eight fields) | Reworded to seven fields with the exact list | `downloads.html:635,639,643,647,651,655,659` |
| UI-101 (seven sections) | Reworded to "eight card sections (…)" + config card/pickup banner/modals — matches my count | `downloads.html:9,120,166,194,336,443,534,566` (8 `<h2>`), config `57-115`, banner `21-26` |
| OPS-067 (reconcile only "reset") | Reworded to keep/promote/reset | `downloads_reconcile.py:135-183` |
| P0-1 `QUEUED` never written | Fixed in §4.2 step 7 ("IDLE→DOWNLOADING→COMPLETED/FAILED (plus PENDING_DEDUPE/MANUAL_PICKUP)") and codified as OPS-097 | `database.py:2674`; no `QUEUED` writer (readers only: `main.py:1343`, `database.py:1190`, `downloads_reconcile.py:30`) |
| P0-2 qbit category/tags | Fixed in §4.4 + §5 rule 12; codified as DL-083 (verified below) | `scheduler.py:3749`; `main.py:606,3795`; `qbit_tags` read nowhere (grep: defaults/configure only) |
| P0-3 Manual Pickup series mapping | Fixed in §4.5 ("There is **no control that edits the pickup's series**") | `manual_pickup.html:26-84,180-184`; `main.py:2385-2412` |
| P0-4 IN_STAGING attribution | Fixed in §4.2 step 9 and the §4.3 diagram ("`IN_STAGING` is written by this ownership reconciliation, not by the downloader") | `scheduler.py:2669`; `calibre/matcher.py:133-205` |
| P0-5 IN_CALIBRE special case | Fixed: invariant 2 now states incoming IN_CALIBRE wins, no IN_CALIBRE→IN_STAGING downgrade, and equal-rank REQUESTED can replace IN_CALIBRE | `database.py:299-307` |
| P0-6 cross-reference IDs | Substantially fixed (invariants 1/3/4/5/7, §4.2 steps 3/9/10, §4.4, §4.5, §4.7, §1.1/§1.5, §4.9, §6); 3 residual IDs listed in §5 below (non-blocking) | registry IDs re-read |
| DL-042 precision | Applied: "attaches the persisted inventory copy without scanning on read" | `main.py:2115-2122`; `downloads_inventory.py:251-263` |
| DL-081 precision | Applied: "when the matched Calibre book count covers the highest wanted volume" | `downloads_reconcile.py:62-73,155-166` |

## 3. The 12 new claims — verified against code

| Claim | Verdict | Evidence |
|---|---|---|
| REL-043 | **DISAGREE** — endpoint facts correct, final consequence clause wrong (see §4) | `main.py:1471-1501`; `releases.html:291`; `partials/series_card_chunk.html:279-286`; `prioritized.html` (no `showAllSeriesReleases` definition) |
| REL-044 | AGREE — ≤500 rows, no pagination/total, priority post-sort | `main.py:4130-4133`; `database.py:4616-4668` (sort at 4667) |
| SER-093 | AGREE — Calibre ∪ staged ownership, bundle-expanded, 60 s cached staged map | `calibre/matcher.py:127-131,146-185`; `staged_ownership.py:54-152` |
| SER-094 | AGREE — audit suggests the fixed Calibre match, repair writes the title-parsed series | `false_series_matches.py:54-67` vs `:99-126` |
| DL-082 | AGREE — `download_wanted_json` → missing list → matcher; correction paths write it | `scheduler.py:1029-1053,1453-1546,1757-1776`; `database.py:2625-2641` |
| DL-083 | AGREE — literal `manga`/`mangatrax` on all add paths; `qbit_category` only filters the queue; `qbit_tags` unused | `scheduler.py:3749`; `main.py:3795,606,3556-3557`; grep `qbit_tags` = defaults/configure only |
| DL-084 | AGREE — `/api/downloader/queue` has no template caller | `main.py:3578-3583`; grep over `templates/` = 0 callers |
| OPS-095 | AGREE — real outbound test posts, 10 s timeout, per-channel `{success,message}` + overall | `main.py:4281-4289`; `notifier/webhook.py:253-335` (timeouts at 285/311) |
| OPS-096 | AGREE — sync creates a task but its button has no `data-task-kind` | `main.py:1909-1930`; `series.html:25` (no binding); `base.html:516-539` |
| OPS-097 | AGREE — QUEUED tolerated, never written; Torbox queueing lives in `downloader_id='queued:<n>'` + phase | `database.py:1190,2655-2686`; `downloader/base.py:48-68`; `scheduler.py:1534-1546` |
| OPS-098 | AGREE — reset seeds 5 groups vs 4 on a fresh DB, exact names match | `database.py:637-646` vs `4700-4709` |
| UI-137 | AGREE — task-kind contract; `downloads:process` declared but never emitted | `downloads.html:47`; `main.py:2076-2099`; `base.html:1074-1091` |

## 4. Remaining DISAGREE rows (2)

| Claim | Why wrong / incomplete | Corrected one-sentence claim | Evidence |
|---|---|---|---|
| **REL-043** | The endpoint behaviour is right, but the consequence is not: the shared card partial renders the "Show all N releases" button on **both** pages, yet `showAllSeriesReleases()` is defined **only** in `releases.html`. `/api/prioritized/cards` renders that same partial (`is_prioritized_view` only changes styling), so on `/prioritized` the button throws `ReferenceError` and never calls `/api/releases/series_releases`; and from `/releases` (the only caller) no prioritized filter exists to be bypassed. The "can reveal non-prioritized releases" outcome therefore cannot happen through the UI. | "`GET /api/releases/series_releases` re-derives the full group list with a search (bypassing the grouped cache), matches `series_key`/`series_name` case-insensitively, slices the requested releases, and ignores `prioritized_only`/`sort`; its only caller is the "Show all" handler on `/releases`, while the same button rendered on `/prioritized` has no handler defined on that page and is inert." | `main.py:1471-1501`; `main.py:1406-1424` (prioritized cards render the shared partial); `partials/series_card_chunk.html:279-286` (button, no `is_prioritized_view` guard); `releases.html:291` (only definition); `prioritized.html:229-594` (no `showAllSeriesReleases`) |
| **OPS-087** | The conclusion ("not one atomic transaction") is right, but the supporting facts are wrong: `init_db` is not "a single final commit". It commits **twice on a fresh database** — once at `database.py:936` (inside the one-time LN backfill, executed only while the `ln_release_backfill_done` marker is absent) and once at `:995`; the `with conn:` at `:408` wraps only the cursor assignment (verified by indentation: `with` at 12, body at 16, next statement back at 12), so it does not create a surrounding transaction. The parenthetical asserting that "under the shipped Python 3.11 sqlite3 legacy transaction mode, DDL statements implicit-commit" is not evidenced by the code and should be dropped (CPython ≥3.6 no longer implicitly commits before DDL; the safe, code-verifiable statement is that the explicit commits split the work). | "`init_db` creates and migrates exactly 13 tables idempotently on one connection; on a first run it explicitly commits twice — after the conditional one-time LN backfill (`database.py:936`, only when the marker is absent) and a final commit at `:995` — and once on later runs, so the schema work is not one atomic transaction. Tables: series_tracking, series_blacklist, releases, prioritized_groups, feeds, activity_logs, users, app_settings, manga_metadata_cache, series_alias_overrides, background_tasks, torbox_manual_pickups and download_failures." | `database.py:405-408` (`with conn:` scope), `:920-936` (conditional commit), `:995` (final commit); 13 `CREATE TABLE IF NOT EXISTS` verified |

Both are one-sentence fixes. Nothing else in REL or OPS is disputed.

## 5. Part 0 — AGREE with 3 residual editorial reference fixes (non-blocking)

The six corrections I demanded in round 1 are applied and accurate (verified in §2). Three residual wrong claim-ID references remain in the prose (no behavioural content). They do not change any claim, so they do not block; list them for the v1.0 edit pass:

1. **§3.2 invariant 2** cites `(REL-018, OPS-078)`; OPS-078 is the attribution audit. Correct set: `REL-018` (+ `SER-086` for blacklist/veto coupling).
2. **§3.2 invariant 6** ("Name joins, not ids") cites `(SER-093, Part 2 Q4, OPS-089)`; SER-093 is the ownership claim. Correct: cite Part 2 §4.2 and `OPS-089` (no FK constraints).
3. **§4.2 step 2** cites `(SER-019/020, SER-032)`; SER-019 is the Calibre match modal and SER-020 the Series nav state. Correct: `SER-031`, `SER-037`, `SER-053` (+ `SER-032`).

Everything else I verified in Part 0 stands: 125 routes / 111 JSON (§1.2, §6 arithmetic now 30+24+42+14+1 = 111 ✓), 45 writable settings keys, no `CURRENT_TASK` symbol, auth exemption list, basic-mode 401 vs oidc redirect (§4.9), route partition, lifecycle diagram incl. unignore direction and equal-rank caveat, INVARIANT 7 + §5 rule 8 task-kind contract, §5 rule 12 qbit settings, §7 counts (SER §7 now 19).

## 6. My own DL lines — no truncation / no regression

- **Extraction:** `FEATURE_REGISTRY_v0.10.md` DL block = 84 claims (DL-001…DL-084) and is **character-identical to my Part 3 checklist** for all 84 lines (scripted extraction/regex compare: 84 registry vs 84 spec, 0 differences). No truncated or dropped clause; IDs contiguous.
- **The 7 agreed rewordings** are exactly the only DL text changes vs v0.9 (diff against `FEATURE_REGISTRY_v0.9.md`: DL-017, DL-018, DL-021, DL-034, DL-041, DL-042, DL-081 changed; DL-082/083/084 added; DL-001…DL-016, DL-019/020, DL-022…DL-033, DL-035…DL-040, DL-043…DL-080, DL-081 text otherwise unchanged).
- **Re-verified against code:** DL-017 `len(active) >= max_concurrent` ✓ (`scheduler.py:1495-1510`); DL-018 unknown-or-≤90 % zip rule ✓ (`scheduler.py:2199-2216`; streaming cap `torbox.py:1320-1338,836-841`); DL-021 three attempts, sleeps 1 s then 2 s ✓ (`torbox.py:1266-1297`, backoff map + `attempt < 3` gate); DL-034 no series control ✓; DL-041 at/above cap ✓ (`scheduler.py:1651-1657`); DL-042 persisted copy ✓; DL-081 book-count proxy ✓.
- **No new assertion needed**; DL-082/083/084 were reviewed above and are accurate.

## 7. Foreign spot-checks for correction-introduced errors

Beyond the 12 new claims, I re-verified reworded foreign claims to ensure the corrections did not introduce new errors (15 claims): REL-018 (equal-rank logic at `database.py:299-307`), REL-024 (RSS/HTML SSRF ⇒ `[]`, `html_scraper.py:60-62`), REL-039 (per-feed `config.base_url` wins, `providers/nyaa.py:20,55-57`), SER-004 (badge = 10-row fetch length, `index.html:142,152`), SER-018 (`series.html:160,164-165`), SER-019 (`series.html:401` sends no `source`), SER-032 (strictly `> 0.90`, `parser.py:1890-1903`), SER-034 (pool 5000 + cursor, `scheduler.py:3279,3289-3292`), SER-090 (no cancel render), OPS-030 (backfill keys reset, `database.py:4712-4713`), OPS-061 (single try/except per tick, `scheduler.py:5187-5274`), OPS-079 (repair re-keys all selected ids without audit re-validation, `false_series_matches.py:85-151`), OPS-090 (39 defaults + 2 = 41 seeds, + 2 startup = 43, counted from `database.py:728-837`), OPS-091 (separate `jpegxl_filter_applied` pass, `database.py:1003,1117,1141`), UI-011 (Cancel branch never renders, `base.html:1039`; `background_tasks.py:51-76`), UI-020 (basic 401 challenge, `auth.py:949,1060`), UI-090 (check-now refreshes, `downloads.html:872-880`), UI-128 (Releases retry / Series reveal, `releases.html:230,238`; `series.html:162-165`).

**Only REL-043 and OPS-087 contain errors; all other corrections are accurate.** No SER/UI/DL correction introduced a new error.

## 8. Global verdict

**GLOBAL VERDICT: DISAGREE**

Remaining blocking disagreements (2; both wording-level, no architectural or behavioural dispute):

1. **REL-043** — the "Show all" consequence is impossible: the handler is defined only on `/releases`; on `/prioritized` the shared card partial's button is inert (no handler), so it cannot call `/api/releases/series_releases` and cannot reveal non-prioritized releases. Apply the corrected sentence in §4 (`releases.html:291`; `partials/series_card_chunk.html:279-286`; `main.py:1406-1424`).
2. **OPS-087** — "single final commit" is wrong: fresh-database init commits at `database.py:936` (conditional one-time LN backfill) and `:995`; the `with conn:` at `:408` wraps only the cursor assignment, and the Python-3.11 "DDL implicit-commit" parenthetical should be dropped. Apply the corrected sentence in §4.

Non-blocking: the three residual Part 0 claim-ID references in §5 (v1.0 edit list).

I will vote **AGREE on all 457 claims** as soon as REL-043 and OPS-087 carry the corrected sentences above (they can be applied editorially without changing any other line). All of my round-1 blockers are confirmed resolved, the 12 new claims are otherwise accurate, my 84 DL lines are intact, and every reworded foreign claim I re-checked is accurate.
