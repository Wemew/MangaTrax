# Consensus Round 1 — expert-downloads (DL owner)

**Reviewer:** expert-downloads (author of Part 3 / DL-001…DL-081)
**Assigned foreign parts:** Part 2 (SER, `# Part 2 —`) and Part 5 (UI, `# Part 5 —`)
**Also voted:** Part 0, REL (by exception), OPS (by exception), and every DL line (self-check)
**Inputs read:** `docs/spec/APP_DESIGN_SPEC_v0.9.md` (Part 0 + Part 2 lines 1258–2111 + Part 5 lines 3875–5389), `docs/spec/FEATURE_REGISTRY_v0.9.md` (all 445 lines)
**Method:** claims verified against the current working tree (`backend/app/**`, `backend/app/templates/**`, `Dockerfile`, schema/tests), not against memory; every disagreement below carries file:line evidence. No files other than this review were written.

## Vote summary

| Section | Claims | Reviewed | Disagree | Verified with code this round |
|---|---|---|---|---|
| Part 0 (overview) | — (no claim IDs) | all | 6 corrections | 12 statements checked |
| REL | 42 | all by exception | 0 | 4 spot-checks incl. numerics |
| SER | 92 | all by exception | 0 | 26 spot-checks |
| DL | 81 | all (own) | 0 | 81/81 text-confirmed; 0 lost |
| OPS | 94 | all by exception | 1 (OPS-067 wording) | 14 spot-checks |
| UI | 136 | all by exception | 3 (UI-094, UI-099, UI-101) | 27 spot-checks |

**No disagreements in REL and SER.** Sections with no disagreement are stated per registry rule 4; nothing in those two sections is implicitly accepted without reading.

---

## 1. Part 0 verdict — DISAGREE (6 corrections, none silent-accepted)

Part 0 makes no claim IDs, but several architecture/workflow statements are **not what the code does**. None of these is a reason to reject the 445-claim registry by itself; they must be corrected before v1.0 because Part 0 is presented as the synthesis baseline.

| # | Part 0 statement | Why wrong/incomplete | Corrected statement | Evidence |
|---|---|---|---|---|
| P0-1 | §4.2 step 7: "`download_status` moves IDLE→QUEUED→DOWNLOADING→COMPLETED/FAILED" | `QUEUED` is **never written** anywhere; it is only tolerated on read (and in the legacy reconcile exclusion list) | "`download_status` moves IDLE→DOWNLOADING→COMPLETED/FAILED, with PENDING_DEDUPE and MANUAL_PICKUP as additional parked states; QUEUED is read-compatible only" | writes: `database.py:2674` (DOWNLOADING), `scheduler.py:1675,1857,1942,2587,2669,3715`; no QUEUED writer (repo-wide grep: only readers `main.py:1343`, `database.py:1190`, `downloads_reconcile.py:30`) |
| P0-2 | §4.4: "category/tags currently hard-coded (`manga`/`mangatrax`) regardless of settings (REL-031)" | Wrong cross-reference (REL-031 is year-range collected batches) and imprecise: `qbit_category` **is** used, but only to filter the live status/queue list; `qbit_tags` is persisted and never applied to any submission | "Every qBittorrent submission passes the literal category `manga` and tag `mangatrax`; `qbit_category` only filters `/api/downloads/status` and `qbit_tags` is stored but unused" | literals: `scheduler.py:3749`, `main.py:3795`; filter: `main.py:606`; stored settings: `main.py:3556-3557`; registry has no claim for this (§4 missing-claims) |
| P0-3 | §4.5 step 2: "the user maps a series (Calibre picker) and volume numbers per file" | Manual Pickup has **no series mapping control**; "Edit series" opens the shared Calibre modal seeded from the **torrent title** and POSTs `/api/downloads/awaiting-series/match`, which re-keys releases by normalized series but never writes `torbox_manual_pickups.series_name` | "the user corrects per-file volume mappings and the wanted volume list, then fetches selected/all files; an optional 'Edit series' action re-keys matching releases via the awaiting-series match flow (it does not edit the pickup's series)" | template `manual_pickup.html:180-184,242-259`; server `main.py:2385-2412`; this is exactly DL Q1 and Part 5 UI-105 |
| P0-4 | §4.3 lifecycle diagram: "REQUESTED ──download/completed──▶ IN_STAGING" | Download completion writes `download_status=COMPLETED`; `releases.status='IN_STAGING'` is produced by **ownership reconciliation** (matcher sees all wanted volumes staged) and by explicit reconcile writes, not by the downloader | "REQUESTED → IN_STAGING happens on the next ownership reconciliation once all wanted volumes exist in staging; download completion itself only sets `download_status=COMPLETED`" | `scheduler.py:2669` (COMPLETED), `matcher.py:133-205` (`IN_STAGING` branch), `database.py:4359,4391` grouped candidates, `main.py:1339-1342` mapping |
| P0-5 | §3.2 invariant 2: "the higher rank wins" with ranks 0/1/1.5/2 | Omits the two `IN_CALIBRE` special cases without which IN_STAGING (1.5) would downgrade IN_CALIBRE (1) under a pure rank comparison | "…otherwise the higher rank wins, **except** that an incoming IN_CALIBRE always wins and IN_STAGING never downgrades an existing IN_CALIBRE" | `database.py:299-307` (special cases before the rank comparison at 305) |
| P0-6 | Claim-ID cross-references throughout Part 0 | Many references point at claims that describe something else; a reader following them is misled. List: invariant 1 → "SER-019, SER-020" (should be SER-045/SER-067); invariant 2 → "(SER-021/OPS-078)" (should be REL-018); invariant 3 → "(SER-025, DL-036)" (should be SER-024, DL-078); invariant 4 → "(DL-033)" (should be DL-037 + a missing claim, see §4); invariant 5 → "(REL-030, OPS-079)" (should be REL-034, OPS-091); invariant 7 → "(OPS-058)" (should be OPS-059 + UI-012); §1.1 "(OPS-009/R-9)" for "no roles" (OPS-009 is env seeding; OPS-011/OPS-023); §4.4 "(REL-031)" (see P0-2); §4.5 "(DL-047…DL-053)" (Manual Pickup is DL-034…DL-041); §4.7 "(SER-032/033)" (series refresh is SER-056…SER-065) | "References re-pointed to the claims that actually carry the behaviour" | Registry IDs at `FEATURE_REGISTRY_v0.9.md:69-160,164-244,248-341,345-480` |

Verified-correct Part 0 statements (no action): "125 HTTP routes (10 HTML + 3 auth + /health + 111 JSON)" — `grep '^@app\.(get|post|put|delete|patch)' main.py` = 125; "13 tables" — 13 `CREATE TABLE IF NOT EXISTS` in `database.py`; thread/interval table §2.3 — matches `scheduler.py:5175-5277` (10 s tick, 15 min Calibre, 60 min feeds, 300 s expansion/tag/stale/pickup, 45 s inventory+route, 30 s filter, 60 s dedupe, ≤6 h prune); §2.6 cache TTLs — grouped 5 s, downloader status 1 s, staged ownership 60 s; §4.9 auth modes — `auth.py`; §5 rules — consistent with `resolve_status`, `wanted` handling and `security.py`.

---

## 2. Part 2 verdict (SER, 92 claims) — AGREE, no disagreements

All 92 SER claims were read; 26 were verified line-by-line against the implementation. Every checked claim matched the code, including the numeric/absolute ones:

| Claim | Verified behaviour | Evidence |
|---|---|---|
| SER-001/002/003/004 | dashboard renders 5 KPIs from `get_dashboard_stats()` (is_monitored=1, REQUESTED, IN_CALIBRE, DOWNLOADING, discovery), one green/amber hero from `get_library_summary()`, ≤5 torrents, pending list `limit=10` / render `[:8]` / badge `length` | `main.py:1139-1167`; `database.py:3525-3551`; `index.html:23,28,34-100,142,152`; `main.py:1149,1165` |
| SER-007 | `/api/series/rows` params `monitored_only/offset/limit(1-200)/search`, response `{status,offset,count,total,has_more,html}` | `main.py:1523-1545` |
| SER-008 | unknown id → HTTP 200 `{is_monitored:false}` (no existence check) | `database.py:2145-2152`; `main.py:3826-3830` |
| SER-011 | Owned column = `book_count` gated on `calibre_series_id`, else "Not linked"; `owned_volumes_json` never shown | `partials/series_table_row_chunk.html:31-37` |
| SER-017/018 | search is client-side over `data-name`; page fetches rows with only `offset/limit=50`; IntersectionObserver auto-load | `series.html:103,116,205,238-246` |
| SER-021 | `file:…?mode=ro&immutable=1`, `query_only=ON`, `busy_timeout=2000`, fallback `mode=ro` | `calibre/scanner.py:29-39` |
| SER-022/023/024/025 | summary keys `{available,db_path,total_books,total_series,total_authors}`; latest_index raised to max owned bundle volume; volume map expanded/dual-keyed; synthetic id `book.id+1000000` | `scanner.py:43-80,118-157,190-243,172` |
| SER-031 | upsert: authors/publisher/tags only when non-empty; website_url only when empty; owned only when `!= '[]'`; `is_monitored` absent from the UPDATE list | `database.py:1846-1863` |
| SER-032 | unique exact wins, multiple exact skipped, fuzzy `threshold=0.90`, re-link called inline after sync | `database.py:2020-2085,1864-1869` |
| SER-033 | pending requests resolve to IN_CALIBRE **or IN_STAGING** | `calibre/matcher.py:216-229` |
| SER-034 | `MANGATRAX_RECONCILE_BATCH_SIZE` default 2000, min 50, rotating cursor | `scheduler.py:114-118` |
| SER-036/070/074 | picker scores `round(score,3)`; covers `Cache-Control: public, max-age=864000` + `image/jpeg`; `/api/search` limit 1-50 | `main.py:2315,1965,1974,3501` |
| SER-042 | exact → substring (`len_ratio≥0.70`, min length 6) → fuzzy `>0.80` on rare-token/3-gram candidates | `parser.py:1829-1917` |
| SER-072 | per-view chip limits discovery 5 / series 4 / releases 3 / dashboard 3 | `tag_display.py:9-14` |
| SER-079/081/082 | GET attribution audit persists a task; suspicious-volume audit trusts explicit markers and only chapter-only rows are repairable; repair silently truncates `ids[:200]` | `main.py:2631-2639`; `database.py:1643-1710`; `main.py:2551-2562` |
| SER-087 | storage warnings only on Settings/Downloads/health — not Overview/Series | no include in `index.html`/`series.html`; `downloads.html:54`; `settings.html:266-272` |
| SER-091 | row icon buttons have `title` only (no `aria-label`); a11y test scans only top-level template files | `partials/…row_chunk.html:4,58,62,65`; `tests/test_ui_polish.py:174-177` |
| SER-092 | `get_series_by_id` defined once, zero callers (backend + tests) | `database.py:2171`; repo grep single match |

**SER verdict: AGREE — 92/92, no corrections required.**

---

## 3. Part 5 verdict (UI, 136 claims) — DISAGREE (3 claims)

133 of 136 UI claims matched the templates/JS. 27 were verified with file:line, including the numeric ones that are usually wrong in UI inventories: UI-004 five `data-nav-item` items in order (`base.html:122,128,148,165,176`), UI-005 sixth flat Manual Pickup link + never-populated `#nav-manual-pickup-count` (`base.html:172,176,249`), UI-008 10 × `min-h-[44px]` mobile links (`base.html:223-257`), UI-010 toast 4 s / unescaped `innerHTML` / no `aria-live` (`base.html:275-276,402-423`), UI-011 12 tasks & 1.5 s poll (`base.html:1026,1140`), UI-024 `slate-850` absent from the Tailwind config (`base.html:12-31`), UI-035 seven series columns (`series.html:38-44`), UI-036 0.8 s reload (`series.html:256-262`), UI-038 hidden Load More (`series.html:64`), UI-043 local `blacklistSeries` defined once and never called (`series.html:335`), UI-044 `series_list: []` always (`main.py:1169-1180` + `series.html:48`), UI-046 seven status pills (`releases.html:42-62`), UI-058 deferred classes only consumed in JS (`releases.html:479-521`, `prioritized.html:456-473`; no server chunk emits them), UI-081 1.5 s/15 min poll (`base.html:555-591`), UI-087 six queue columns + engine-specific controls (`downloads.html:191-330`), UI-088 Torbox-only polling 1 s/5 s (`downloads.html:1143-1159,1209-1211`), UI-095 `<details>` open-state persisted in `localStorage` (`downloads.html:2045-2050`), UI-118 six pipeline toggles (`settings.html:278-320`), UI-126 qbit password in `value=` on both pages (`settings.html:119`, `downloads.html:101`), UI-129 exactly 20 `confirm(` sites, UI-131 32 `location.reload()` sites, UI-132 lazy/async covers (`series.html:11`), UI-014 banner condition/poll (`base.html:726,731`), UI-016 backfill modal only from Discovery (`discovery.html:561`).

### DISAGREE table (registry claims)

| Claim ID | Why wrong/incomplete | Corrected one-sentence claim | Evidence |
|---|---|---|---|
| **UI-094** | Says the header button carries `data-task-kind="downloads:process"` "so reattachment/busy state only works for one of the two". `/api/staging/tag` creates **no** background task at all, so no task of kind `downloads:process` ever exists and reattachment works for **neither** button. | "A second 'Tag & move now' button duplicates the header 'Process Downloads' action; the header carries `data-task-kind=\"downloads:process\"`, but that kind is never emitted by any task, so neither button gets task-panel reattachment or busy state (the spinner is local JS only)." | `main.py:2076-2099` (no `_recorded_task`/`run_in_background`); only occurrence of the string is the attribute `downloads.html:47` (plus a test source assertion `tests/test_task_progress_followups.py:112`); reattach matches `task.kind ===` button kind `base.html:1074-1091` |
| **UI-099** | "edits eight fields" — the modal has **seven** editable fields (filename, series, volume, title, writer, publisher, genre); the eighth UI element is the read-only current-ComicInfo block. | "The Preview/Manual Tag modal loads the current ComicInfo, edits seven fields (filename, series, volume, title, writer, publisher, genre), saves, toasts and reloads." | `downloads.html:635,639,643,647,651,655,659` (7 `tag-field-*` inputs; current-XML block at 631 is display-only) |
| **UI-101** | "seven sections" — the page currently renders **eight** `<h2>` card sections plus the downloader-config card and the conditional Manual Pickup banner (i.e. nine+ blocks). | "The Downloads page is a single long page with no tabs or in-page navigation for its eight card sections (auto-download diagnostics, awaiting series, failed documents, active queue, raw downloads, staging directory, staging cleanup, ComicInfo audit) plus the downloader-config card and a conditional pickup banner." | `downloads.html:9,120,166,194,336,443,534,566` (8 `<h2>`); config card `57-115`; banner `21-26` |

### Non-blocking wording note (UI)

- **UI-090/UI-103 labels vs behaviour:** both describe the control as "Fetch whole torrent", but the API fetches all *unfetched* files individually and only uses the whole-torrent `zip_link` when the pickup has no file list at all (`scheduler.py:1786-1805,1839-1842`). The claim is not false about the click→POST contract, but the consensus wording should note this so the UI-enhancement phase does not "preserve" a misleading label as a semantic promise. Recommend appending to UI-103: "(the server fetches every un-fetched file individually; the whole-torrent zip is only used when Torbox returned no file list)". This is already DL Q3.

**UI verdict: DISAGREE — 3 claims must be reworded (UI-094, UI-099, UI-101); 133/136 agree.**

---

## 4. Missing / duplicated features (IDs to add or merge)

### Missing claims (behaviour exists but no registry line states it)

1. **NEW (DL domain, DL-082):** "`resolve_release_missing_volumes` prefers `releases.download_wanted_json`, then the stored missing list, then a fresh matcher computation; every correction path (manual pickup volumes, retry, dedupe) therefore writes `download_wanted_json`." — this is Part 0 invariant 4 but has **no registry claim** (DL-037 covers only the pickup correction path; DL-079 covers dedupe). Evidence `scheduler.py:1029-1053`; `database.py:2625-2641`; regression `tests/test_manual_pickup_volume_correction.py:62-96`.
2. **NEW (DL/REL boundary, DL-083 or REL-043):** the qBittorrent category/tags split from P0-2 — submissions always `manga`/`mangatrax`; `qbit_category` filters the live list; `qbit_tags` unused. Without this claim, UI-116 ("Settings configures … category/tags") reads as if those settings affect submissions. Evidence `scheduler.py:3749`, `main.py:3795,606,3556-3557`.
3. **NEW (UI, fold into UI-094 correction or new UI-137):** "`data-task-kind` values used by a page must correspond to a task kind that the backend actually emits; `downloads:process` is the only page-declared kind with no emitter." Evidence `downloads.html:47` vs `main.py:2076-2099`; the real emitted download kinds are `downloads:prune`, `downloads:flatten`, `downloads:rescan`, `downloads:failures-scan`, `downloads:archive-convert`, `downloads:inventory-clear`, `downloads:staging-purge`, `downloads:staging-series-match`, `downloads:torbox-clear-failed`, `torbox:pickup:fetch`, `torbox:pickup:retry`, `audit:comicinfo`, `repair:comicinfo` (grep of `_recorded_task`/`run_in_background` calls).
4. **NEW (OPS, optional):** "`background_tasks` has no retention: `prune`/`prune_background_tasks` exist but no production caller invokes them" — OPS-060 already covers this; no new ID needed (listed for completeness).

### Duplicated claims (same behaviour claimed twice; keep both, enforce sync)

Registry rule 3 forbids dropping claims, so these are *overlaps to keep aligned*, not merges:

- Timer statements appear in OPS and DL: OPS-065 ≈ DL-056 (auto-tag/routing cadence), OPS-066 ≈ DL-056 (45 s pass), OPS-067 ≈ DL-081 (stale reconcile), OPS-068 ≈ DL-041 (pickup expiry sweep), OPS-070 ≈ DL-079 (dedupe sweep), OPS-071 ≈ DL-006 (30 s filter), OPS-072 ≈ DL-016 (Torbox poller cadence).
- Dashboard shell: SER-001…SER-005 duplicate UI-028…UI-033 (by design: SER owns data semantics, UI owns markup — but the numbers must stay identical).
- Global banners/panel: SER-088…SER-090 ≈ UI-014, UI-015, UI-011.
- Downloads page: DL-034…DL-041 (server behaviour) vs UI-102…UI-108 (controls) — intentional pairing; UI-105 already disagrees with P0-3 and agrees with DL Q1.
- Calibre-sync/health: SER-027/SER-030 ≈ OPS-045 (status endpoint) and OPS-062 (lock/cadence).

---

## 5. Cross-domain inconsistencies

1. **Wanted-set authority has no claim and a wrong Part 0 reference.** Part 0 invariant 4 cites DL-033 (derived download state); the real authority is `download_wanted_json` precedence + the pickup/retry writers. See missing claim #1.
2. **qBittorrent category/tags:** UI-116 implies the setting governs downloads; DL-003 shows the submit path hard-codes `manga`/`mangatrax`; `qbit_category` only filters the list. Part 0 §4.4 cites REL-031 (unrelated). See P0-2 and missing claim #2.
3. **Task-kind contract:** Part 0 invariant 7 cites OPS-058 (cancel endpoint); the contract is OPS-059 + UI-012, and UI-094 additionally mis-states it (see DISAGREE). `downloads:process` must be documented as a dead binding.
4. **Status semantics (IN_STAGING):** Part 0 §4.3 implies download completion writes IN_STAGING; in code completion writes `download_status=COMPLETED` and IN_STAGING is an ownership-reconciliation result (`matcher.py:133-205`, `database.py:4359,4391`). DL-027 and DL-081 are correct; Part 0 needs the workflow fix (P0-4).
5. **Manual-pickup series mapping:** Part 0 §4.5 claims the user maps a series; UI-105 + DL Q1 say the action cannot change the pickup/series relation. Parts 0/2/5 all mention the defect, only Part 0 overstates the capability (P0-3).
6. **Status-precedence references:** SER-021 (Calibre reader) and OPS-078 (attribution repair) are cited for the precedence model; the claim is REL-018 + database.py:272-307. No behaviour disagreement between SER/OPS/REL parts — reference-only.
7. **Ownership references:** Part 0 invariant 3 cites SER-025 (synthetic series) and DL-036 (mapping API); correct pair is SER-024 (volume map) and DL-078 (staged ownership). SER and DL text agree with each other; only the references are wrong.
8. **Downloads section count:** UI-101 says seven; DL-056/Part 3 §7 and the template show eight sections. This is the only UI/DL behavioural number that conflicts (rest are consistent).

---

## 6. Self-check — DL-001…DL-081 as extracted in the registry

- **Extraction:** all **81** DL lines are present in `FEATURE_REGISTRY_v0.9.md:164-244` and are **verbatim** the consensus checklist I authored — no truncation, no dropped clause, ordering preserved (DL-001…DL-081 contiguous, no duplicate).
- **Re-verification this round:** spot-checked 20 of my own lines against code again (DL-003, 005, 007, 016, 018, 019, 021, 023, 025, 030, 037, 042, 047, 054, 056, 058, 061, 067, 078, 081). All remain true.
- **Wording precision I voluntarily flag (no vote change; suggest v1.0 tightening):**
  - **DL-042** "attaches the persisted inventory" — precise, but could add "the persisted copy (no scan on read)"; evidence `main.py:2115-2122`, `downloads_inventory.py:251-263`.
  - **DL-081** "promoted to IN_CALIBRE when Calibre owns it" — the code's proxy is `max(wanted) <= matched book_count`, not explicit owned volumes; suggest "…when the matched Calibre book count covers the highest wanted volume". Evidence `downloads_reconcile.py:62-73,155-166`.
  - **DL-034** "file mapping inputs and fetch/retry/edit/discard actions" — could add "no series mapping control" to align with UI-105/DL Q1; evidence `manual_pickup.html:26-84`.
  - **DL-054** is the correct supporting claim for the UI-094 correction: "it is a foreground request, not a background task" — this is why no `downloads:process` task exists.
- **No lost text, no misstatement requiring a vote change.** My only cross-domain additions are the two missing claims in §4 (DL-082/DL-083).

---

## 7. Global verdict

**GLOBAL VERDICT: DISAGREE** (blocking disagreements must be reworded before v1.0)

**Blockers (registry claims):**
1. **UI-094** — claims task-panel reattachment/busy state works for the header "Process Downloads" button; no `downloads:process` task is ever created, so it works for neither button (`main.py:2076-2099`; `downloads.html:47`; `base.html:1074-1091`).
2. **UI-099** — "edits eight fields"; the modal edits seven fields plus a read-only current-XML block (`downloads.html:635-659`).
3. **UI-101** — "seven sections"; the page renders eight `<h2>` sections plus the config card and pickup banner (`downloads.html:9,120,166,194,336,443,534,566`).

**Non-blocking corrections requested in the same pass:**
4. **OPS-067** — "COMPLETED releases whose files no longer exist are reset" is incomplete: the job keeps a release when a recorded copy is found in downloads/staging or when Calibre covers it, and resets only the remainder (`downloads_reconcile.py:135-183`). Suggest: "Every 300 s, COMPLETED releases whose recorded files are missing are re-pointed at any found copy or promoted to IN_CALIBRE when Calibre covers them, and reset to IDLE only when nothing matches."
5. **Part 0 corrections P0-1…P0-6** (§1 above), notably the non-existent `QUEUED` transition, the qBittorrent category/tags sentence with its wrong REL-031 reference, the Manual-Pickup series-mapping overstatement, the IN_STAGING workflow attribution, and the stale claim-ID references.
6. **Two missing claims** (§4): wanted-set authority (`download_wanted_json`) and qBittorrent category/tags behaviour.

I will vote **AGREE on all 445 claims** as soon as the three UI blockers are reworded as proposed (and the OPS-067/Part 0 corrections are accepted or explicitly deferred to the v1.0 edit list). I disagree on **no** SER claim, **no** REL claim, **no** OPS claim other than OPS-067, and **81/81** of my own DL claims stand.
