# Consensus Round 2 — expert-series re-vote (457 claims)

**Reviewer:** expert-series (author of Part 2 / SER-001…SER-094)
**Assigned foreign parts (round 2):** Part 0 (overview), Part 1 (REL), Part 4 (OPS)
**Inputs read:** `docs/design/CORRECTIONS_ROUND1.md`; all five `docs/design/reviews/round1_*.md` (556 review lines); `docs/archive/spec-drafts/FEATURE_REGISTRY_v0.10.md` in full (457 lines / 5 sections); `docs/archive/spec-drafts/APP_DESIGN_SPEC_v0.10.md` Part 0 (lines 35-583), Part 1 (584-1302), Part 4 (3085-3930), plus the v0.9→v0.10 diffs of Parts 1/4 and the registry SER block.
**Method:** every round-1 blocker was re-checked against the implementation at commit `86a7bd8`; each of the 12 new claims was verified against code at `file:line`; 5 foreign reworded claims were independently re-verified to ensure the edits introduced no new errors; my own 94 SER lines were diffed against `02_series_library.md` §8 (94/94 identical, 8 agreed rewordings, 2 new). No spec or code file was edited.
**Registry integrity:** REL 44 · SER 94 · DL 84 · OPS 98 · UI 137 = **457**, IDs contiguous, no duplicates (scripted).

---

## 1. Per-section vote

Every claim not named below is **AGREE**. Counts are claims in that section.

| Section | Claims | AGREE | DISAGREE (blocking) | Remaining non-blocking corrections |
|---|---|---|---|---|
| Part 0 (overview) | — (synthesis) | **AGREE** with editorial corrections | 0 | 4 stale claim-ID labels (P0-2.1…P0-2.4 below); no behavioural statement wrong |
| REL | 44 | **44** | 0 | none |
| SER | 94 | **94** | 0 | SER-034 wording precision (own domain; no behavioural error) |
| DL | 84 | **84** | 0 | none |
| OPS | 98 | **98** | 0 | OPS-096 wording precision |
| UI | 137 | **137** | 0 | none |
| **Total** | **457** | **457 AGREE** | **0** | 6 editorial items, none blocking |

No claim requires a DISAGREE vote. There are zero blocking disagreements.

---

## 2. Round-1 blocker closure (my task-7 review)

### 2.1 Resolved — verified

| Round-1 blocker | Resolution | Evidence (v0.10 + code) |
|---|---|---|
| Part 0 route partition 120 ≠ 111 | Fixed: §6 now 30 REL + 24 SER + 42 DL + 14 OPS + 1 debug = 111 JSON; totals 125 | `APP_DESIGN_SPEC_v0.10.md:519-527`; independently re-measured with script over 125 `@app.*` decorators: 10 HTML + 3 auth + `/health` + 111 JSON = 125 |
| Part 0 IN_CALIBRE "terminal" | Fixed: equal-rank tie caveat added in Part 0 §3.2/§4.3 and REL-018; matches code exactly | `APP_DESIGN_SPEC_v0.10.md:310-315,401-402`; `backend/app/database.py:253-261,299-307`; registry REL-018 |
| Part 0 "never write NEW" | Fixed: §3.2 inv 8 + §5 rule 3 now say NEW is a real stored status and only the user/API status endpoint excludes it | `APP_DESIGN_SPEC_v0.10.md:329-331,488-489`; `backend/app/main.py:3890-3895`; `backend/app/calibre/matcher.py:209-214` |
| Auth exemption list missing /static, /favicon.ico, /healthz | Fixed in Part 0 §2.2/§4.9 and OPS-011 | `APP_DESIGN_SPEC_v0.10.md:214-225,476`; `backend/app/auth.py:946-947` |
| Wanted-set invariant had no claim | Added DL-082 and referenced from Part 0 §3.2 inv 4 | registry DL-082; `backend/app/scheduler.py:1029-1053`; write paths `main.py:3801`; `scheduler.py:1540,2427,3760,4655` |
| REL-018 / REL-024 / REL-025 / REL-028 / REL-038 / REL-039 | All reworded as requested; re-verified against code | REL-018: `database.py:253-261,299-307`; REL-024: `providers/rss.py:48-52`, `providers/html_scraper.py:58-62`, `providers/base.py:7-15`; REL-025: `providers/nyaa.py:26-38,146-246,320-338`; REL-028: `providers/rss.py:69-77`, `providers/nyaa.py:358-365`, `scheduler.py:3482-3543`; REL-038: non-invalidating writers `database.py:1307-1337,2154-2158,2191-2231,3260-3275`; REL-039: `providers/registry.py:40-41`, `nyaa_source.py:96-113` |
| OPS-019 / OPS-030 / OPS-079 / OPS-087 / OPS-090 / OPS-091 | All reworded; re-verified | OPS-019: `auth.py:961,1080-1100,1150`; OPS-030: `database.py:4670-4722` (backfill keys `4712-4713`); OPS-079: `false_series_matches.py:85-151`; OPS-087: `database.py:405-997`; OPS-090: `database.py:728-780,782-837,918-938,1105-1162` (39 defaults + 2 = 41; 43 after markers); OPS-091: `database.py:1005-1103,1105-1164` |
| UI-011 Cancel never renders | Fixed; now consistent with OPS-059 | registry UI-011; `backend/app/templates/base.html:1039`; `backend/app/background_tasks.py:51-76` |

### 2.2 Partially resolved — non-blocking editorial leftovers

Part 0 cross-reference regeneration is complete except four labels (all in Part 0 prose; no registry claim cites them backwards, and no behavioural statement is affected):

| ID | Location | Wrong citation | Correct citation | Evidence |
|---|---|---|---|---|
| P0-2.1 | §3.2 inv 2 | `(REL-018, OPS-078)` | `(REL-018)` (status precedence); OPS-078 is the series-attribution audit | `APP_DESIGN_SPEC_v0.10.md:314-315`; registry OPS-078 vs REL-018 |
| P0-2.2 | §3.2 inv 6 | `(SER-093, Part 2 Q4, OPS-089)` | `(Part 2 Q4, OPS-089)` (name joins / no FK); SER-093 is ownership union | `APP_DESIGN_SPEC_v0.10.md:324-325`; `backend/app/database.py:377-403` (no FK); registry SER-093 |
| P0-2.3 | §4.2 step 2 | `(SER-019/020, SER-032)` | `(SER-032)` for auto-link, optionally `SER-019` for the manual merge modal; SER-020 is the nav highlight claim | `APP_DESIGN_SPEC_v0.10.md:357-359`; registry SER-020/SER-032 |
| P0-2.4 | §4.9 basic-mode paragraph | `(OPS-011, OPS-013)` | `(OPS-011, OPS-012)`; OPS-013 is the OIDC PKCE login flow | `APP_DESIGN_SPEC_v0.10.md:470-472`; `backend/app/auth.py:533-558,1051-1063` |

These are editorial citation fixes for the v1.0 editing pass. I do **not** treat them as blocking: every substantive Part 0 statement now matches the code, and no registry claim is affected.

---

## 3. New claims (12) — checked against code

| Claim | Verdict | Evidence |
|---|---|---|
| REL-043 `/api/releases/series_releases` re-derives the group with a search (cache bypass), case-insensitive match, slices, ignores prioritized_only/sort | AGREE | `backend/app/main.py:1471-1501`; cache bypass when `search` truthy at `database.py:4024-4031`; prioritized "Show all" inconsistency confirmed (`main.py:1479`) |
| REL-044 `/api/prioritized/releases` returns up to 500 rows, no pagination/total, priority post-sort | AGREE | `main.py:4130-4133`; `database.py:4616-4668` (LIMIT 500, no total, `rows.sort(priority)` at 4667) |
| SER-093 ownership = Calibre ∪ staged, bundle-expanded per book, 60 s cached staged map | AGREE | `backend/app/calibre/matcher.py:127-131`; `backend/app/staged_ownership.py:24,116-129`; `backend/app/calibre/scanner.py:127-147,190-243` |
| SER-094 audit suggests the fixed Calibre match while repair re-keys to the title-parsed series | AGREE | `backend/app/false_series_matches.py:52-67` (audit target) vs `:99-127` (repair target); Part 2 Q19 |
| DL-082 `download_wanted_json` preferred over missing list over matcher; correction paths write it | AGREE | `scheduler.py:1029-1053`; manual pickup `scheduler.py:1765-1775`; submit writes `main.py:3801`, `scheduler.py:1540,2427,3760,4655`; column update `database.py:2637` |
| DL-083 release-submit paths hard-code `manga`/`mangatrax`; `qbit_category` only filters the status list; `qbit_tags` stored unused | AGREE | literals `main.py:3795-3796`; `scheduler.py:2420-2421,3748-3749,4647-4648`; category read `main.py:606`; grep: no `qbit_tags` read |
| DL-084 `/api/downloader/queue` dead API surface | AGREE | `main.py:3578`; grep across `templates/**`: no consumer |
| OPS-095 `test-webhook` real outbound posts (10 s), per-channel results + overall status | AGREE | `main.py:4281-4289`; `notifier/webhook.py:253-335` (10.0 s at `:285,311`) |
| OPS-096 Calibre sync persisted task, synchronous, no `data-task-kind` binding | AGREE (wording precision below) | `main.py:1909-1930`; `series.html:25` (no binding); local disable only `base.html:516-539` |
| OPS-097 `QUEUED` tolerated legacy value never written; slot queueing via `downloader_id='queued:<n>'`/progress phase | AGREE | grep: no `QUEUED` writers; readers `main.py:1343`, `database.py:1190-1191`, `downloads_reconcile.py:30`; encode/parse `downloader/base.py:51-65` |
| OPS-098 reset seeds 5 groups differing from fresh-DB 4 | AGREE | fresh `database.py:640-646`; reset `database.py:4702-4709` (danke-Empire, LuCirAn, 1stKind, TenmaLand, Stick) |
| UI-137 `downloads:process` declared but never emitted; duplicate Tag & move now has no binding | AGREE | `templates/downloads.html:47` (`data-task-kind="downloads:process"`), `:358` (Tag & move now, no binding); `/api/staging/tag` foreground handler `main.py:2076-2103` |

---

## 4. Foreign spot-checks after correction (5 required; 7 done)

| Claim | Verdict | Verification |
|---|---|---|
| OPS-011 (reworded) | AGREE | `auth.py:946-947` exemptions; `:1007-1019` CSRF gate; `:1021-1077` oidc redirect/JSON 401 vs basic 401 challenge; `:533-558` basic parse |
| OPS-067 (reworded) | AGREE | `downloads_reconcile.py:108-195`: re-points at found copy (`:140-152`), promotes to IN_CALIBRE when book count covers wanted (`:155-165`), otherwise resets to IDLE with `DOWNLOAD_FILES_MISSING` (`:180-183,92-105`); 300 s cadence `scheduler.py:5221-5232` |
| OPS-087 (reworded) | AGREE | `database.py:405-997` (13 tables, idempotent, single final commit at `:995`); Python 3.11 sqlite3 legacy implicit DDL commits — claim now correctly says "not one atomic transaction" |
| REL-025 (reworded) | AGREE | `providers/nyaa.py:26-38,187-246` (retries incl. empty body, 404 genuine end), `:320-338` (zero-row page also end), `:274-276` min page spacing |
| DL-042 (reworded) | AGREE | `main.py:2104-2123` attaches persisted inventory via `downloads_inventory.load_inventory`, no scan on read |
| OPS-061 (reworded) | AGREE | `scheduler.py:5185-5277`: one outer `try` around the whole tick's job list; exception at `:5274-5275` skips the remainder of that tick and the loop continues |
| OPS-090 (reworded) | AGREE | 39-entry defaults dict `database.py:728-780`; two new keys from INSERT OR IGNORE `:829-837`; startup markers `:918-938,1105-1162` → 41/43 |

---

## 5. My own SER lines — self-check

* Registry SER block is **94/94 identical** to `docs/design/parts/02_series_library.md` §8 (programmatic diff; 0 lost/truncated lines). The 8 agreed rewordings (SER-004/018/019/022/032/034/037/090) are present verbatim, and SER-093/094 are added. No regression, no truncation.
* Re-verified the changed SER claims: SER-004 (`index.html:142,152`; min(total,10)), SER-018 (`series.html:63-67,108-169,171-192`: 60 ms chain, observer, hidden button revealed only on error), SER-019 (`series.html:353-435`; no `source`), SER-022 (`scanner.py:43-80`), SER-032 (`database.py:2064`; `parser.py:1893-1903` strictly-greater test), SER-037 (grammar only), SER-090 (`base.html:1016-1092`; `background_tasks.py:51-76`), SER-093/SER-094 (see §3). All accurate.
* One non-blocking precision suggestion: **SER-034** says "rotating cursor over the newest 5000 active releases"; the code loads up to 5000 releases via `get_releases(limit=5000)` (status-tier/recency ordered, excluding BLACKLISTED/IGNORED) and only then filters to the four active statuses (`backend/app/scheduler.py:3279-3287`; ordering `database.py:2896-2903`). Suggested v1.0 wording: "…with a rotating cursor over up to 5000 releases (status-tier/recency ordered, then filtered to active statuses), and persists only rows whose missing volumes or status changed." Not a behavioural error: the bounded 5000-row rotating reconcile claim is correct.

**Non-blocking precision suggestion (foreign):** **OPS-096** "so the button cannot be disabled or reattached across reloads" is slightly stronger than the code: the click handler does disable the button locally for the request (`base.html:516-539`); what is missing is task-panel disable/reattachment across reloads. Suggested: "…no `data-task-kind` binding, so the task panel can never disable it and it cannot be reattached across reloads (the click handler disables it only for the duration of the request)."

---

## 6. Global verdict

**GLOBAL VERDICT: AGREE**

Zero blocking disagreements remain: all 457 claims in `FEATURE_REGISTRY_v0.10.md` are accepted (REL 44/44, SER 94/94, DL 84/84, OPS 98/98, UI 137/137), and Part 0 is accepted with the four citation-label corrections in §2.2 (editorial, no behavioural change). The six non-blocking items in §2.2 and §5 are wording/reference fixes for the v1.0 editing pass only — they do not require another consensus round.
