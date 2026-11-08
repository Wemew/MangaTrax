# Round 2 consensus review — expert-platform (OPS author)

**Reviewer:** expert-platform (author of Part 4, OPS-001…OPS-098).
**Inputs read:** `CORRECTIONS_ROUND1.md`, all five `reviews/round1_*.md`, `FEATURE_REGISTRY_v0.10.md` (457 claims), and `APP_DESIGN_SPEC_v0.10.md` Part 0 + Part 1 + Part 5 (plus Part 4 to check my own lines and the merged edits).
**Method:** diffed v0.9→v0.10 claim-by-claim, then re-verified every correction that touches my round-1 blockers, every new claim, and each OPS line against `backend/app` at `file:line`. Read-only; no app/server run.

## 0. Integrity checks

| Check | Result |
|---|---|
| Claim count | **457** = REL 44 · SER 94 · DL 84 · OPS 98 · UI 137. IDs unique and contiguous per section; **no removals** from v0.9 (scripted diff). |
| Registry ↔ merged spec | All **457/457** §9 checklist lines in `APP_DESIGN_SPEC_v0.10.md` are textually identical to `FEATURE_REGISTRY_v0.10.md` (0 differences). No drift, no truncation. |
| Change set parity with `CORRECTIONS_ROUND1.md` | Exactly **38 reworded** + **12 added** = 457 ✓; the 38 IDs and 12 new IDs match the corrections record exactly. |
| My OPS extraction | v0.9→v0.10 OPS diff is exactly the **9 agreed rewordings** (OPS-011, 019, 030, 061, 067, 079, 087, 090, 091) + the **4 new claims** (OPS-095…098); no line lost, no extra edit, no truncation. |
| Route arithmetic (Part 0 §6) | 10 HTML + 3 auth + 1 health + (30+24+42+14+1) JSON = **125 total / 111 `/api/*`** ✓ (matches the 125 `@app.*` routes). |

## 1. Round-1 blockers — resolution status

| Round-1 blocker | Status in v0.10 | Verification |
|---|---|---|
| UI-011 (Tasks panel Cancel) | **Resolved** | New text says the Cancel branch “exists but never renders because `/api/tasks` never emits a `cancellable` field”. Confirmed: `background_tasks.py:51-76` (no field), `base.html:1039` (dead branch). |
| SER-090 (cancel support) | **Resolved** | Same correction; “it exposes no working cancel control (the Cancel branch never renders…)”. Consistent with OPS-059/UI-011. |
| UI-020 (auth redirect) | **Resolved** | New text: oidc redirect / basic-mode 401 challenge / none open. Confirmed `auth.py:1051-1063` vs `1065-1077`. |
| OPS-011 (self-flagged) | **Resolved** | Reworded exactly as proposed; matches `auth.py` branches. |
| UI-128 (retry affordance) | **Resolved** | New text names the Releases “Retry loading” and Series fallback reveal; matches `releases.html:230,238`, `series.html:162-165`. |
| REL-025 (“404/empty”) | **Resolved** | New text: empty bodies are retried; only HTTP 404 or a parsed zero-row page end the crawl (`providers/nyaa.py:27-28,190-208,320-335`). |
| Part 0 (~15 wrong refs, `CURRENT_TASK`, 45 keys, route partition) | **Substantially resolved** — all substantive items fixed; **4 pointer typos remain** (non-blocking, §5) | `CURRENT_TASK` gone (grep 0); §2.6 now `background_tasks`/`TaskReporter`; §1.2 “45 writable configuration keys”; §6 partition 30/24/42/14+1=111; refs for backup→OPS-094, `users`→OPS-023, ownership→SER-093/DL-078, wanted-set→DL-082, qbit→DL-083, tasks→UI-137 are correct. Residual: §3.2 inv.2 `OPS-078`, inv.6 `SER-093/OPS-089`, §4.2 step 2 `SER-019/020`, §4.9 basic mode `OPS-013`. |
| Part 1 `download_speed` column | **Resolved** | Removed from the `releases` column list (`APP_DESIGN_SPEC_v0.10.md:1022`); only a status-payload key remains (`:2451`). |
| Part 5 §5.5 auth prose (part of my blocker 2) | **Not resolved** | Line ~4718 still reads “HTML routes redirect to login; `/api/*` returns 401 JSON” without the basic-mode qualification → non-blocking editorial fix, §5. |

## 2. The 12 new claims — verified against code

| Claim | Verdict | Evidence |
|---|---|---|
| **OPS-095** (`test-webhook` real outbound posts, 10 s, per-channel results, only settings endpoint doing it) | AGREE | `notifier/webhook.py:253-335` (`_post_json(..., timeout=10.0)` at `:285,:311`); only caller `main.py:4288`; no other settings endpoint posts webhooks. |
| **OPS-096** (`/api/calibre/sync` persisted task but no `data-task-kind` binding) | AGREE | `main.py:1909-1930` uses `_recorded_task`; `series.html:25` button has only `onclick/id/aria-label/title/class`, and `series.html` contains **no** `data-task-kind`. |
| **OPS-097** (`download_status='QUEUED'` tolerated, never written) | AGREE | All writers: `database.py:2674` DOWNLOADING; callers pass IDLE/FAILED/MANUAL_PICKUP/COMPLETED/PENDING_DEDUPE/DOWNLOADING (`scheduler.py:1675,1711,1857,2357…,4938`; `main.py:3638,3662`); QUEUED appears only in read filters (`database.py:1190`, `main.py:1343`, `downloads_reconcile.py:30`). |
| **OPS-098** (reset seeds 5 groups ≠ fresh-DB 4) | AGREE | `database.py:4702-4708` (5 names) vs `database.py:640-646` (4 names). |
| **UI-137** (`downloads:process` declared by a button but never emitted; duplicate has no binding) | AGREE | `downloads.html:47` declares it; `/api/staging/tag` (`main.py:2076-2099`) awaits synchronously, no `run_in_background`; duplicate “Tag & move now” (`:358-359`) has no attribute; no backend task kind `downloads:process`. |
| **DL-082** (`download_wanted_json` authority chain) | AGREE | `scheduler.py:1029-1053`: persisted wanted → stored missing → fresh matcher. |
| **DL-083** (qBittorrent adds hard-code `manga`/`mangatrax`; `qbit_category` only filters the live listing; `qbit_tags` unused) | AGREE | `main.py:3792-3797`, `scheduler.py:3745-3750`; `main.py:606-607` passes `qbit_category` to `get_torrents`; `qbit_tags` only written/read from config (`main.py:3556-3566`). |
| **DL-084** (`/api/downloader/queue` dead surface) | AGREE | Route `main.py:3578`; grep over `templates/` → 0 callers. |
| **REL-043** (`series_releases` re-derives with search, ignores prioritized/sort) | AGREE | `main.py:1471-1501` (`get_releases_grouped_by_series(status, search=series_key)`, case-insensitive match, slice, no `prioritized_only`/sort). |
| **REL-044** (`/api/prioritized/releases` ≤500 rows, no pagination/total, priority post-sort) | AGREE | `main.py:4130-4133`; `database.py:4616` default `limit=500`; docstring order “highest-ranked matching group, then status/recency”. |
| **SER-093** (ownership = Calibre ∪ staged, bundle-expanded, 60 s cache) | AGREE | `staged_ownership.py:24` (`CACHE_TTL_SECONDS = 60.0`), `:69` (`extract_manga_bundle_and_volumes` per file), `:116-130`. |
| **SER-094** (false-series audit suggests the fixed Calibre match; repair applies the parsed series) | AGREE | `false_series_matches.py:52-67` (`suggested_series = fixed_name` when matched), `:110-126` (`UPDATE … series_name = parsed`). |

## 3. Per-section vote (by exception; every claim not listed = AGREE)

| Section | Claims | AGREE | DISAGREE | Notes |
|---|---|---|---|---|
| REL | 44 | **44** | 0 | REL-018/024/025/028/038/039 rewordings verified against code (incl. `resolve_status` equal-rank tie, SSRF-empty provider path, cache-invalidation exclusions: `update_release_tags`, `update_release_details`, `update_series_link` do not invalidate while `link_tracked_series:2013` does). |
| SER | 94 | **94** | 0 | SER-004/018/019/022/032/034/037/090 rewordings verified (`get_pending_requests(limit=10)` + badge `|length`, 60 ms chain + error-only Load More, strict-`>` fuzzy at `parser.py:1896`, rotating cursor over `get_releases(limit=5000)`). |
| DL | 84 | **84** | 0 | DL-017/018/021/034/041/042/081 verified (`len(active) >= max_concurrent` at `scheduler.py:1503`; 3 attempts sleeping 1 s then 2 s at `torbox.py:1275-1288`; `downloads_reconcile.py:62-72` book-count proxy; `load_inventory` no-scan at `main.py:2116`). |
| OPS | 98 | **98** | 0 | 9 rewordings + 4 new claims verified; one non-blocking mechanism nuance on OPS-087 (§6). |
| UI | 137 | **137** | 0 | UI-011/020/128/137 corrected/added; UI-090/094/099/101/103 verified (`downloads.html:1158` poll, `:47/:359`, 7 tag fields `:634-659`, pickup whole-zip only when no file list `scheduler.py:1803`). |
| **Total** | **457** | **457** | **0** | — |
| Part 0 | — | AGREE (substantive) | 0 blocking | 4 residual pointer typos → §5 (non-blocking). |
| Part 1 | — | AGREE | 0 | `download_speed` removed; Nyaa stop rule fixed; qBittorrent wording scoped to release adds. |
| Part 5 | — | AGREE | 0 blocking | 1 residual prose line → §5 (non-blocking). |

## 4. Remaining DISAGREE rows

**None.** I have no claim-level disagreement with any of the 457 lines. Details of the two prose/pointer carry-overs from round 1 follow as **non-blocking editorial defects** rather than DISAGREE rows, because no registry claim is affected and the corrections record already defers reference/renumbering cleanup to a post-v1.0 editorial pass (`CORRECTIONS_ROUND1.md` §4).

| Item | Why it is wrong | Corrected wording | Evidence |
|---|---|---|---|
| `APP_DESIGN_SPEC_v0.10.md:314-315` | §3.2 invariant 2 cites **OPS-078** (series-attribution audit) for `resolve_status` tie behaviour. | “…an equal-rank incoming `REQUESTED` **can** replace a stored `IN_CALIBRE` (REL-018; see Part 4 §4.4).” | `database.py:272-307`; OPS-078 text in registry. |
| `APP_DESIGN_SPEC_v0.10.md:325` | §3.2 invariant 6 (“name joins, not ids”) cites **SER-093** (staged ownership) and **OPS-089** (connection/WAL policy). | “…audited repair paths rewrite attribution explicitly (SER-032, SER-037, Part 2 Q4).” | registry SER-093/OPS-089 texts; `database.py:1872-2018`. |
| `APP_DESIGN_SPEC_v0.10.md:358-359` | §4.2 step 2 (identity reconcile / auto-link) cites **SER-019/020** — SER-020 is the nav-highlight claim. | “…auto-link/merge links trackers to Calibre series (exact unique first, fuzzy) (SER-032, SER-037, SER-053).” | registry SER-020 text; `database.py:2020-2085`. |
| `APP_DESIGN_SPEC_v0.10.md:472` | §4.9 basic-mode paragraph cites **OPS-013** (`/auth/login` OIDC PKCE) for the Basic challenge. | “Unauthenticated requests (HTML and API) receive 401 with a Basic challenge (OPS-011, OPS-012).” | `auth.py:533-558`; OPS-012 text. |
| `APP_DESIGN_SPEC_v0.10.md:4718` (Part 5 §5.5) | Repeats the pre-round-1 auth statement; true only in `oidc` mode. | “**CSRF/auth:** in `oidc` mode HTML routes redirect to `/auth/login` and `/api/*` returns 401 JSON; in `basic` mode both return 401 with a Basic challenge; mutating requests are subject to the Origin/Referer policy when auth is enabled and `MANGATRAX_PUBLIC_URL`/`CSRF_PROTECT` apply. Expired sessions surface a JSON body inside a toast.” | `auth.py:1051-1077`; UI-020 (fixed). |
| `APP_DESIGN_SPEC_v0.10.md:3224-3225` (Part 4 §3.1, mine) | My settings table still describes `qbit_category` as “torrent category” and `qbit_tags` as “torrent tags”, implying they are applied to adds — contradicting DL-083. | `qbit_category`: “filter for the live queue listing only; release adds hard-code `manga` (DL-083)”; `qbit_tags`: “stored but never applied (DL-083)”. | `main.py:3792-3797,606`; DL-083. |

## 5. Spot-check log — foreign claims re-verified after the corrections (no new errors found)

| Claim | Verified against |
|---|---|
| SER-034 (rotating cursor over newest 5000) | `scheduler.py:3279` `get_releases(limit=5000)`, `:3292-3301` cursor/batch; `:116` env batch size |
| SER-004 (badge = length of the 10-row fetch) | `main.py:1144` `get_pending_requests(limit=10)`; `index.html:142` `pending_requests|length`; cards `[:8]` at `:152` |
| DL-017 (refuse at/above max concurrent) | `scheduler.py:1499-1510` `len(active) >= max(1, max_concurrent)` |
| DL-021 (3 attempts, 1 s then 2 s) | `downloader/torbox.py:1275-1288` (`{1:1.0, 2:2.0, 3:4.0}`, loop `(1,2,3)`) |
| DL-081 / OPS-067 (keep → IN_CALIBRE → reset) | `downloads_reconcile.py:62-72` (`max(volumes) <= book_count`), `:143-183`, `_reset_release` logs `DOWNLOAD_FILES_MISSING` |
| DL-042 (persisted inventory, no scan) | `main.py:2116-2118` `downloads_inventory.load_inventory` |
| SER-022 / SER-032 | `scanner.py:43-77`; `parser.py:1890-1917` (`best_score = threshold`, strict `>`) with `database.py:2064` `threshold=0.90` |
| UI-099 / UI-101 / UI-103 | `downloads.html:617-670` (7 fields); section markup; `scheduler.py:1803` (whole-torrent only when no file list) |
| OPS-095 / OPS-096 | `notifier/webhook.py:285,311`; `series.html:25` + `main.py:1909-1930` |

## 6. Self-check of my OPS extraction

* **Fidelity:** OPS v0.9→v0.10 diff = the 9 approved rewordings + 4 new claims; nothing else changed, nothing lost; registry OPS lines are byte-identical to the Part 4 §9 checklist (spec↔registry diff 0/457).
* **Re-verified rewordings:** OPS-011 (mode matrix, `auth.py:1051-1077`), OPS-019 (`force=True` only in `complete_login`, `auth.py:1150`; throttled `record_identity` otherwise), OPS-030 (`database.py:4712-4713` resets backfill keys), OPS-061 (whole-tick shield `scheduler.py:5185-5277`), OPS-067 (`downloads_reconcile.py:143-183`), OPS-079 (`false_series_matches.py:85-150` re-keys any selected existing id, no audit re-validation), OPS-090 (39 defaults + 2 INSERT-OR-IGNORE keys = 41; + `jpegxl_filter_applied`/`ln_release_backfill_done` = 43 — counted in `database.py:728-781,788-837,918-938,1105-1164`), OPS-091 (separate JPEGXL pass ignores `filter_non_manga`, `database.py:1105-1164`).
* **One non-blocking precision note (my own line):** OPS-087 says “DDL statements implicit-commit”. The tested/consequential part (“not one atomic transaction”) is correct: `init_db` executes the early DDL outside the implicit DML transaction (each commits in SQLite autocommit) and everything from the first INSERT onward is committed at the single `conn.commit()` (`database.py:405-412, 995`). For precision the mechanism could read “DDL statements run outside the implicit DML transaction (autocommit until the first DML)”, because CPython ≥3.6 no longer issues an implicit commit *before* DDL. Claim outcome unaffected; suggest for the editorial pass.
* **Own-part prose note:** Part 4 §3.1 `qbit_category`/`qbit_tags` rows need the DL-083 wording (§4 table above). No OPS claim changes.

## 7. Global verdict

**GLOBAL VERDICT: AGREE**

Zero blocking disagreements remain: all **457/457** registry claims are accepted as written, all of my round-1 blockers are resolved at claim level, the 12 new claims are accurate against the code, and the 38 rewordings I re-checked introduced no new errors. The six items in §4 are document-hygiene corrections (four wrong cross-reference pointers in Part 0, one prose line in Part 5 §5.5, one stale row in my own Part 4 §3.1) that should be swept up in the v1.0 editorial pass — they do not change any claim's truth and I do not consider them consensus blockers.
