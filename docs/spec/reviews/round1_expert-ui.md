# Consensus Round 1 — expert-ui review

**Reviewer:** EXPERT-5 (UI/UX authority; author of Part 5 / UI-001…UI-136)
**Assignment:** Part 0 verdict + Part 3 (DL) verdict + Part 4 (OPS) verdict + vote on all 445 registry claims by exception + self-check of the UI extraction.
**Code base:** current checkout (`backend/app/**`, `backend/app/templates/**`). All line numbers below are from this tree and were re-read for this review; no server or test suite was run.
**Method:** registry read end-to-end (481 lines); Part 0 (spec lines 1–540), Part 3 (2112–3032) and Part 4 (3033–3874) read; every disputed/surprising/absolute/numeric claim checked against code with `file:line`. 32 foreign claims independently verified (evidence table in §3/§4). No spec file, code or test was modified.

**Vote summary**

| Section | Claims | Disagreements | Result |
|---|---|---|---|
| REL | 42 | 0 | No disagreements in this section. |
| SER | 92 | 0 | No disagreements in this section. |
| DL | 81 | 0 | No disagreements in this section. |
| OPS | 94 | 3 | DISAGREE — OPS-061, OPS-067, OPS-087 |
| UI | 136 | 3 | DISAGREE — UI-011, UI-099, UI-101 |
| Part 0 | — | 5 corrections | DISAGREE (overview statements) |

---

## 1. Part 0 verdict — **DISAGREE (5 factual/cross-reference corrections, no architectural disagreement)**

What I verified as correct (sample, all re-read):

* Route surface: **125 routes** = 10 HTML + 3 auth + `/health` + 111 JSON (main.py route decorators; cross-checked against Part 5 §2 map). ✓
* "Ten HTML pages, all server-rendered": ✓ (main.py:1138,1169,1182,1209,1571,1622,1771,1851,1863,1875 render ten distinct templates).
* "One process, many threads" incl. 3-worker task executor, one worker thread, one Torbox poller, daemon feed/backfill threads: ✓ (background_tasks.py:24–27; scheduler.py:5281–5298; scheduler.py:3386–3389; scheduler.py:4767–4772).
* 13 tables, no FK constraints, WAL, name-based joins, `resolve_status` precedence, ownership = Calibre ∪ staging, media typing additive, task kinds as UI contract: ✓ (database.py:405–997, 107–118, 222–320, 377–403; staged ownership in database.py:4016–4022).
* Thread/job cadence table in §2.3: ✓ including 45 s inventory+route, 30 s qbit filter, 300 s tag/stale/pickup/expansion, 60 s dedupe sweep, 10 s loop sleep, activity prune first tick/≤6 h (scheduler.py:5185–5277).
* First-run and primary-flow workflows §4.1–4.2, status lifecycle §4.3, engine model §4.4 (qbit category/tags *are* hard-coded at every add site — scheduler.py:3745–3749, 2417–2422; main.py:3792–3797 — so the §4.4 statement is behaviourally right), Discovery/Prioritized/backfill/maintenance/auth sections: ✓.
* §7 open-question counts: REL 14 (spec:1190–1203), SER 18 (spec:1971+), DL 13 (spec:2839–2881), OPS 26 (R-1…R-26), UI 20 questions + 50 gaps. ✓

Corrections required before v1.0:

| ID | Location | Problem | Corrected statement |
|---|---|---|---|
| P0-1 | §4.9, spec:443 | "`basic`: env-configured user/pass; **session cookie**; same gate." Basic mode never sets a cookie: `/auth/login` redirects to `/` (main.py:1064–1067) and every request is re-authenticated from the `Authorization` header (auth.py:533–558, 978–990). Cookies exist only in OIDC mode. | "`basic`: env-configured user/pass re-validated on every request from the Authorization header (browser-cached credential prompt); no session cookie is set; same gate." Evidence: main.py:1061–1067, 1115–1124 (the only `set_cookie` calls); auth.py:533–558. |
| P0-2 | §6, spec:483–487 | Per-group endpoint counts (30 + 25 + 40 + 24 + 1 = 120 JSON) plus 10 HTML + 4 auth/health = **134 ≠ 125** (the document's own total). | "Per-domain endpoint counts must be reconciled to the verified 125 (14 non-`/api` + 111 `/api/*`); several endpoints are shared/overlapping across the groups, so the rows are indicative, not additive." Evidence: counted route decorators in main.py = 125; Part 0 spec:130. |
| P0-3 | §3.2 invariants / §4.2 / §4.4 / §4.5 / §4.7 | Stale registry anchors point at unrelated claims: e.g. §3.2.1 "(SER-019, SER-020)" (display-title rule is SER-037/045/067; SER-019/020 are match-modal/nav), §3.2.2 "(SER-021/OPS-078)" (precedence is REL-018/SER-086), §3.2.3 "(SER-025, DL-036)" (ownership is SER-024/DL-078), §3.2.5 "(REL-030, OPS-079)" (media typing is REL-034/OPS-091), §4.2 step 2 "(SER-019/020/023)" (auto-link is SER-032), step 3 "(SER-025/026, OPS-078)" (SER-033 + DL-078), step 9 "(SER-009/025)" (SER-033), step 10 "(OPS-062…OPS-066)" (OPS-085/086), §4.4 "(REL-031)" (qbit add is DL-003 + REL Q10), §4.5 "(DL-047…DL-053)" (pickup sweep is DL-041), §4.7 "(REL-036/037, SER-032/033)" (series refresh is REL-037/SER-056–065). §1.1 "OPS-009/R-9" and §1.5 "OPS-073/R-8" also carry wrong OPS ids (R-8 is Part 4 R-8; OPS-073 is initial sync; backup is OPS-094). | Re-anchor each cross-reference to the registry ID that actually states the behaviour; the R-numbers (§7) are correct. Evidence: registry lines as cited; e.g. OPS-094 (no backup), SER-032 (auto-link), DL-078 (staged ownership). |
| P0-4 | §4.2 step 7, spec:355 | "`download_status` moves IDLE→**QUEUED**→DOWNLOADING→COMPLETED/FAILED." `QUEUED` is read/compared (main.py:1343; database.py:1190–1191; downloads_reconcile.py:30) but **never written** by current code: qBittorrent submission sets DOWNLOADING directly (database.py:2655–2686) and Torbox queuing is encoded as `downloader_id="queued:<id>"` + progress `phase` (downloader/base.py:48–68; scheduler.py:1534–1546), not `download_status`. | "`download_status` moves IDLE→DOWNLOADING→COMPLETED/FAILED (plus PENDING_DEDUPE/MANUAL_PICKUP); `QUEUED` is a tolerated legacy value that current code never writes; Torbox slot-queueing lives in `downloader_id='queued:<n>'` and the progress phase." Evidence: no writer found; main.py:1343 read; database.py:2655–2686; downloader/base.py:48–68. |
| P0-5 | §3.2.7 / §2.3 vs OPS-061 | The overview repeats the "scheduler catches every job exception" model; the actual loop wraps **all** jobs in a single `try/except` per tick (scheduler.py:5187–5275), so one failing job skips the rest of that tick (the document's own §3.4/R-4 says so). | Align §2.3/§3.2.7 with OPS-061's corrected wording (per-tick shield, not per-job). Evidence: scheduler.py:5187–5275, 5274–5277. |

No disagreement with Part 0's architecture, data-model, persistence, workflow or reliability statements beyond the five items above.

---

## 2. Part 3 (DL) verdict — **AGREE (0 disagreements in DL-001…DL-081)**

Part 3's inventory (§2), mechanics (§3) and checklist (§9) are internally consistent and match the code on every claim I tested. Independently verified (evidence table):

| Claim | Verified evidence |
|---|---|
| DL-001 engine selection/fallback | downloader/__init__.py:12–78 (`DOWNLOADER_ENGINES`, fallback to qbit in `resolve_downloader_engine`). |
| DL-002 8 s test-result cache | downloader/qbit.py:76–140 (cache + structured failure payloads). |
| DL-003 qbit add form + "Fails" | downloader/qbit.py:142–189 ($159–167 form; :182 `"Fails" not in text`). |
| DL-004 pause/resume/delete semantics | main.py:3585–3601 (`deleteFiles=false` at :3600). |
| DL-005 selective filePrio + ancillary skip | downloader/qbit.py:269–368. |
| DL-006 30 s filter only for bundles with partial missing set | scheduler.py:4912–4974 (query :4924–4929; `if vols and len(missing) >= len(vols): continue` :4961–4963; interval :5261–5264). |
| DL-007 adapter is test-only | `QBitDownloader` referenced only by downloader/__init__.py factory + tests/test_torbox_downloader.py:52,332,346; production call sites pass `engine="torbox"` or use `get_qbit_client()` (scheduler.py:984–993). |
| DL-014 queued handle | downloader/base.py:48–68; scheduler.py:1534–1546, 2989–3006. |
| DL-016 poller cadence | scheduler.py:2815–2849 (`max(5, torbox_poll_seconds)` busy; `min(30,max(15,floor*3))` idle :2835; wake :2851–2856). |
| DL-018 strict planner rules | scheduler.py:2130–2251 (skip :2130; no file list :2143; unmappable :2159–2167; absent wanted :2168–2178; zip ≤20 files AND ≤90 % cap :2205–2216; per-file fallback :2226–2236; size checks :2237–2242). |
| DL-023 2–8 range connections, >16 MiB | downloader/torbox.py:999–1103, 62 (`TORBOX_RANGE_THRESHOLD_BYTES` 16 MiB); settings clamp 1–8 (schemas.py:803–813); `max(2, min(...))` chunk count at downloader/torbox.py:999–1015. |
| DL-024 size cap before requestdl | scheduler.py:2064–2085, 2199–2242; torbox.py:836–841, 1344–1353. |
| DL-026 hold instead of fail (enabled + under cap) | scheduler.py:1640–1682 (:1648–1657 cap/enable checks; :1666–1680 create+status). |
| DL-029 retry guards 404/409/429/5 s + cancel stale + fresh missing | main.py:3604–3663 (guards :3611–3628; cancel :3630–3636; resolve :3639; submit :3640–3650). |
| DL-030 remove: failed = local only; others remote then local | main.py:3666–3701 (:3677–3690; always `clear_downloader_submission` :3690). |
| DL-031 clear-failed local-only with progress | main.py:3704–3728 (:3708–3722). |
| DL-041 cap + 300 s sweep | scheduler.py:1614–1621 settings; :1651–1657 cap; sweep timer :5234–5242. |
| DL-042 status clamp/never-500/degraded + inventory | main.py:543–569, 574–611; endpoint 2104–2123 (:2115–2122 attaches inventory; `Cache-Control: no-store` :2123). |
| DL-043 1 s TTL, single-flight, 30 s stale, speed history | main.py:52–59 constants; 850–913 (TTL :860, building single-flight :862–877, stale ≤30 s :901, live-only history :891). |
| DL-044 check-now 409 unless torbox | main.py:2126–2143 (:2129–2130). |
| DL-054 Process Downloads = routing + legacy tag, foreground | main.py:2076–2099 (awaits both `to_thread` calls; no `run_in_background`; combined counts :2086–2098). |
| DL-058 inventory root-only scan, 200 cap, persisted | downloads_inventory.py:20–22, 159–226 (:180–184 cap/truncated; depth docstring :165), persistence :229–248. |
| DL-064 failure scan classes & skips | download_failures.py:89–157 (classes :109–153). |
| DL-072 audit status set | tagger/series_audit.py:70–77 (all eight statuses). |
| DL-077 Calibre ComicInfo formats | tagger/naming.py:1–9, 65–74, 196–249; tag chip limits tag_display.py:9–13. |
| DL-079 60 s dedupe sweep, batch ≤50 | scheduler.py:2329–2350 (limit=50), timer :5266–5272. |
| DL-081 stale reconcile full behaviour | downloads_reconcile.py:108–195 (kept :143–153, IN_CALIBRE :155–165, staging re-point :167–178, reset :180–183). |

No DL claim was found wrong, incomplete in a way that changes meaning, or contradicted by current code. Part 3's own open questions (Q1–Q13) correctly describe the defects it does not vote on.

---

## 3. Part 4 (OPS) verdict — **DISAGREE (3 claims need rewording)**

Independently verified as correct (sample): OPS-001/002/003 (Dockerfile:1–52; entrypoint.sh:1–36; docker-compose*.yml:16–33/21–33 incl. `cap_drop: ALL`, 10m×5 log cap, 8082:8080), OPS-004/005/006/007/008 (main.py:1040–1058, 213–230, 198–211; Dockerfile:46–49; scheduler.py:5300–5314), OPS-009 (database.py:725–837), OPS-011 (auth.py:946–947, 1000–1005, 1021–1077 — exemption set and `?next=` redirect), OPS-013/014/017 (main.py:1061–1125; auth.py:472–527, 1108–1125), OPS-019 (auth.py:961 `provision_interval=300`), OPS-020/021/022 (main.py:234–277; auth.py:1007–1019), OPS-025/026/027/028 (security.py:76–335), OPS-029/030/031/032/033 (main.py:4342–4388; database.py:4670–4722; maintenance.py:62–101), OPS-034/035/036/037/038 (main.py:4211–4279; schemas.py:645–923 — **45** optional fields; SETTINGS_KEYS **37** entries, unreferenced), OPS-039/040 (storage_health.py:30–123; main.py:4254–4279), OPS-042 (schemas.py:338–380; no reference outside schemas.py), OPS-043 (settings.html:19–51, 857–865), OPS-044–047, OPS-048 (main.py:114–168 — one named RotatingFileHandler, maxBytes/backupCount, removal on disable), OPS-049/050/051/052 (database.py:1248–1291, 1587–1604; main.py:2664–2672; scheduler.py:5244–5250), OPS-053/054/055/056/057/058 (background_tasks.py:24–187; main.py:1235–1297, 1256–1264), OPS-059/060 (serialize_task never emits `cancellable` — background_tasks.py:51–76; `prune`/`prune_background_tasks` have no caller), OPS-062/063 (scheduler.py:3120–3124, 3359–3362 — non-blocking locks, `skipped=true`), OPS-064 (scheduler.py:3372–3391), OPS-065/066/067/068/069/070/071/072 (scheduler.py:5189–5272; 2815–2849), OPS-073 (scheduler.py:5152–5160, 3212–3241), OPS-074/075/076, OPS-077/078/079/080/081/082/083/084/085/086, OPS-088/089/090/091/092/093/094 (database.py:377–403, 405–997, 725–857; timezone_util.py:6–23; no `VACUUM`/backup).

Disagreements: see §4 rows OPS-061, OPS-067, OPS-087.

---

## 4. DISAGREE table

| Claim ID | Why wrong / incomplete | Corrected one-sentence claim | Evidence (file:line) |
|---|---|---|---|
| **OPS-061** | "catches **every job** exception so the loop continues" implies per-job isolation. The loop has **one** `try/except` around the whole job block per tick; any job exception aborts the remaining jobs for that tick (only the loop itself survives). Part 4 §3.4/R-4 already state this, so the registry line contradicts its own part. | "The scheduler runs one daemon worker thread with a 10 s sleep, re-reads interval settings each tick, and shields the whole tick in a single `try/except`: a job exception is logged and the remainder of that tick's jobs are skipped, while the loop itself continues." | scheduler.py:5185–5277 (single `try` at 5187, single `except` at 5274); spec:3342. |
| **OPS-067** | "COMPLETED releases whose files no longer exist **are reset**" is materially incomplete: the reconcile first re-points at a found copy (downloads *or* staging), promotes to IN_CALIBRE when Calibre owns the volumes, and only resets when nothing matches. Most vanished-file rows are kept/promoted, not reset. DL-081 states the full behaviour correctly, so OPS-067 under-describes it. | "Every 300 s, COMPLETED releases whose recorded files no longer exist are reconciled: re-pointed at a found copy, promoted to IN_CALIBRE when Calibre owns the wanted volumes, or otherwise reset to IDLE with `DOWNLOAD_FILES_MISSING`." | downloads_reconcile.py:108–195 (kept 143–153, IN_CALIBRE 155–165, staging re-point 167–178, reset 180–183); scheduler.py:5221–5232. |
| **OPS-087** | "created idempotently **in one transaction**" is false: `init_db` has two explicit commits (the `background_tasks` table is created before the first commit, `torbox_manual_pickups`/`download_failures` after it), plus post-transaction one-shot passes. The table list itself is correct. | "init_db creates exactly 13 tables idempotently in two schema transactions (commit at database.py:936 and :995), followed by one-shot media-typing passes outside that transaction; there is no migration version table." | database.py:405–997 (commits :936, :995); one-shot passes :999–1003. |
| **UI-011** | "renders up to 12 tasks with progress, ETA, error **and Cancel**" is false: the Cancel button is conditionally rendered from `task.cancellable`, a field `serialize_task()` never emits, so it never appears. This also contradicts OPS-059 (which is correct) and duplicates the finding. | "A floating Tasks panel polls `/api/tasks?active=0&limit=20` every 1.5s on every page and renders up to 12 tasks with progress, ETA and error; the Cancel button is coded but never renders because `serialize_task` omits `cancellable` (tags:refresh is the only cancellable kind)." | base.html:1039; background_tasks.py:51–76; main.py:1560 (only `cancellable=True`). |
| **UI-099** | "edits **eight** fields" is wrong: the Preview/Manual Tag modal has seven editable inputs (staged filename, Series, Volume, Title, Writer, Publisher, Genre) plus a read-only current-ComicInfo preview. | "The Preview/Manual Tag modal loads the current ComicInfo, edits seven fields (filename, Series, Volume, Title, Writer, Publisher, Genre), saves, toasts and reloads." | downloads.html:616–670 (inputs :635,639,643,647,651,655,659). |
| **UI-101** | "its **seven** sections" undercounts: the page is a single long page with ~14 stacked sections (diagnostics, pickup banner, header, storage warning, downloader card, awaiting, failed docs, queue, raw inventory, staging, staging cleanup, ComicInfo audit, tag modal, match modal). | "The Downloads page is a single long page with no tabs or in-page navigation across roughly fourteen stacked sections." | downloads.html:6–670, 2064–2106; Part 3 §7.1 list (spec:2790–2805). |

Non-blocking clarification (not a DISAGREE): **UI-020** says HTML requests are "redirected to `/auth/login`"; the exact Location is `/auth/login?next=<quoted path>` (auth.py:1021–1077, main.py:250). Suggest appending "(with `?next=`)" to the claim for precision.

---

## 5. Missing / duplicated features (IDs to add or cross-reference)

**Proposed new claims (no existing registry line covers them):**

| Proposed ID | Claim | Evidence |
|---|---|---|
| DL-082 (or OPS-095) | `download_status='QUEUED'` is a tolerated but never-written value in current code: qBittorrent submission writes DOWNLOADING directly, and Torbox slot-queueing is encoded as `downloader_id='queued:<n>'` plus `torbox_progress_json.phase`, while QUEUED survives only in read filters. | main.py:1343; database.py:1190–1191; downloads_reconcile.py:29–33; database.py:2655–2686; downloader/base.py:48–68. (Also fixes Part 0 P0-4.) |
| UI-137 | The "Process Downloads" button advertises `data-task-kind="downloads:process"` but `/api/staging/tag` is a foreground request that creates no persisted task, so its busy/re-attach state can never activate; the duplicate "Tag & move now" button has no task binding at all. | downloads.html:47, 358; base.html:1074–1099; main.py:2076–2099; Part 3 §6.4 (spec:2776). |
| OPS-095 | `reset-all-data` re-seeds **five** prioritized groups (`danke-Empire`, `LuCirAn`, `1stKind`, `TenmaLand`, `Stick`) that differ from the **four** fresh-DB seeds (`danke-Empire`, `1R0N`, `Lu自主规制`, `a-sloth`); the "default groups" are therefore not one list. | database.py:637–646 (fresh) vs database.py:4700–4709 (reset); already flagged as REL Q8 (spec:1197). |

**Duplicated coverage (keep both claims but cross-reference; do not merge away IDs):**

* Scheduler-job pairs: DL-006 ↔ OPS-071 (30 s qbit filter); DL-041 ↔ OPS-068 (300 s pickup sweep); DL-056 ↔ OPS-065/OPS-066/OPS-067 (5 min tag / 45 s inventory / 5 min reconcile); DL-079 ↔ OPS-070 (60 s dedupe sweep); DL-080 ↔ REL-035 (auto-download gates); OPS-050 ↔ OPS-069 (activity prune job); OPS-062 ↔ SER-028 (calibre interval); OPS-063 ↔ REL-026/REL-027 (feed polling/intervals).
* UI/domain pairs: UI-088 ↔ DL-016/OPS-072 (Torbox poll cadence); UI-087 ↔ DL-042 (queue table data); UI-113/UI-125 ↔ OPS-043 (settings sections/Advanced).
* Mode-of-operation duplicates already tracked as open questions, not claims: REL Q8 (seed drift), REL Q10 (qBittorrent category/tags settings ignored by release downloads), REL Q11 (server-rendered-empty views).

No registry claim should be dropped; the only *conflicts* are the six DISAGREE rows above (three of which are duplicates of other claims that are already correct).

---

## 6. Cross-domain inconsistencies

1. **Task kinds / cancel UI.** UI-011 vs OPS-059 is a direct contradiction (Cancel button). Fix UI-011 (see §4); OPS-059, OPS-058 and UI-012 are consistent with the code. Also Part 3 §6.4 states `downloads:process` is decorative, UI-094 says the header button carries the binding, and no registry claim states that the binding can never match — add UI-137 (§5). Manual-pickup task kinds (`torbox:pickup:fetch/retry`) are consistent across DL-038/039, UI-103/104 and base.html:1074–1099.
2. **Mobile statements.** Part 3 §7.3 (spec:2826–2833) describes modals as `fixed inset-0` with "internal scroll regions" and mentions toasts/task panel only in passing; Part 5 (G-01, UI-010/UI-013) records that (a) most modals have no internal scroll cap, no Escape close, no focus trap and no body-scroll lock, and (b) four fixed bottom-right overlays (toasts, tasks, sticky Load More, settings save bar) collide on phones. Part 3's mobile summary should reference Part 5 G-01/G-04 rather than imply dialogs are uniformly scroll-safe. Also Part 3 §7.3's "no visibility-based pause" and Part 5's global 1.5 s tasks poll agree — keep.
3. **Status + ownership rules.** (a) `QUEUED` lifecycle drift: Part 0 §4.2/Part 4 §4.4 treat it as a live status; code never writes it (P0-4/DL-082). (b) Reset vs fresh seeds: Part 0/Part 4 describe one "default groups" concept; two different lists exist (OPS-090 fresh 4 vs reset 5 — REL Q8; new OPS-095). (c) Ownership: Part 0 invariant 3, DL-078, REL-032/033 and SER-024 agree ("owned = Calibre ∪ staging, bundle-expanded, 60 s cache") — no conflict. (d) Status precedence: Part 0 invariant 2, REL-018 and SER-086 agree; only Part 0's anchor IDs are wrong (P0-3). (e) qBittorrent `qbit_category`/`qbit_tags`: OPS-046 says the configure endpoint persists them, DL-003 says adds post the hard-coded `manga`/`mangatrax`; both are true (settings are stored but ignored on add) — Part 0 §4.4 states the consequence but anchors it to the wrong claim (REL Q10 is the correct reference).

---

## 7. Self-check — UI-001…UI-136 as extracted into the registry

**Extraction integrity:** the UI section of `FEATURE_REGISTRY_v0.9.md` (lines 343–480) contains exactly 136 claim lines in the same order as Part 5 §10 (UI-001…UI-136); textual comparison found no lost text, no reordering and no truncation (spot-checked ~25 lines verbatim; all IDs contiguous). No UI claim was silently dropped.

**Misstatements found in my own claims** (also in §4): **UI-011** (Cancel renders — false), **UI-099** (eight fields — seven), **UI-101** (seven sections — ~fourteen). **Clarification:** UI-020 should say `/auth/login?next=…`.

Re-checked and confirmed accurate (sample): UI-004/UI-005 (5 `data-nav-item` + 6th Manual Pickup, dead badge), UI-006/UI-008 (dropdown/mobile contracts), UI-009/UI-010 (progress bar; toast a11y/escaping), UI-014/UI-015/UI-016 (banners/modal reachability), UI-021/UI-022/UI-023/UI-024 (focus ring, CSS tokens, CDN, `slate-850`), UI-035 (seven columns), UI-038 (auto-chaining + hidden Load More), UI-043/UI-044 (dead local function, empty server list), UI-046/UI-047/UI-049 (pills, dual search, load-more contract), UI-058/UI-065 (dead deferred classes), UI-070 (pagination drops filters), UI-087 (six columns), UI-088 (Torbox-only poll), UI-094 (decorative task binding), UI-113/UI-125 (dead Advanced anchor; debug-gated section), UI-126 (qbit password in HTML), UI-129 (three confirm idioms), UI-132 (cover lazy/fallback), UI-136 (no error pages).

---

## 8. Verification evidence index (foreign claims checked for this review)

Claim IDs checked against code with line evidence: REL-004, REL-011, REL-013, REL-014, REL-020, REL-027, REL-041, REL-042; SER-006, SER-007, SER-013, SER-072, SER-088, SER-089, SER-090, SER-091, SER-092; DL-001…DL-003, DL-005…DL-007, DL-014, DL-016, DL-018, DL-023, DL-024, DL-026, DL-029…DL-031, DL-041…DL-044, DL-054, DL-058, DL-064, DL-072, DL-077, DL-079, DL-081; OPS-001…OPS-011 (sample), OPS-013/014, OPS-017, OPS-019, OPS-021/022, OPS-025…OPS-030, OPS-033…OPS-040, OPS-042, OPS-048…OPS-061, OPS-062…OPS-073, OPS-076, OPS-077…OPS-094. (The DL/OPS tables in §2–§3 list the specific line numbers.)

---

## 9. Global verdict

**GLOBAL VERDICT: DISAGREE**

Blocking disagreements (must be reworded before expert-ui can sign off; all are factual/completeness fixes, not architectural objections):

1. **OPS-061** — loop shields the whole tick, not every job; reword per §4.
2. **OPS-067** — reconcile keeps/promotes/resets; reword per §4.
3. **OPS-087** — schema creation uses two transactions, not one; reword per §4.
4. **UI-011** — task panel Cancel button never renders; reword and align with OPS-059.
5. **UI-099** — tag modal edits seven fields, not eight.
6. **UI-101** — Downloads has ~fourteen sections, not seven.
7. **Part 0 P0-1…P0-5** — basic-mode "session cookie" is false; §6 endpoint counts sum to 134≠125; stale cross-reference IDs throughout §1.1/§1.5/§3.2/§4.2/§4.4/§4.5/§4.7; `QUEUED` is never written (lifecycle wording); scheduler-shield wording must match the corrected OPS-061.

Non-blocking additions requested: DL-082/OPS-095 (`QUEUED` never written), UI-137 (`downloads:process` binding can never match), OPS-095 (fresh-DB 4 groups vs reset 5 groups). REL (42), SER (92) and DL (81) are **no disagreements** in this review; Part 5 is otherwise accepted 133/136.
