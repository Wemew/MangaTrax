# Consensus Round 2 — re-vote by expert-releases (REL author)

**Reviewer:** expert-releases · **Task:** task-11 · **Round:** 2 (v0.10, 457 claims).
**Inputs read:** `CORRECTIONS_ROUND1.md`; all five `reviews/round1_*.md`; `FEATURE_REGISTRY_v0.10.md` (457/457 lines); `APP_DESIGN_SPEC_v0.10.md` Part 0, Parts 2 and 3 (and my own Part 1); `backend/app/**`, `backend/app/templates/**` for verification.
**Method:** every claim voted by exception; my own 44 REL lines diffed against v0.9 and against merged Part 1; all 12 new claims and all 12 of my round-1 blockers re-verified against code. No spec/code file was edited by this review — only this file.

**Counts:** claims reviewed **457/457** (REL 44, SER 94, DL 84, OPS 98, UI 137) · **blocking disagreements: 0** · non-blocking editorial notes: 4 · Part 0: accepted (behaviour statements correct; 3 residual citation typos listed for the editorial pass).

---

## 1. Per-section vote table

| Section | Claims | AGREE | DISAGREE (blocking) | Basis / verification |
|---|---|---|---|---|
| REL (own) | 44 | 44 | 0 | Registry lines diffed against merged Part 1: 44/44 identical, 0 truncation/regression. v0.9→v0.10 diff shows exactly the 6 agreed rewordings (REL-018, -024, -025, -028, -038, -039) + the 2 new claims (REL-043, -044); all 6 rewordings re-verified against code (see §2). |
| SER | 94 | 94 | 0 | Round-1 DISAGREEs SER-018/SER-032 reworded correctly; SER-034/004/019/022 rewordings match code; new SER-093/SER-094 verified; 12 further SER claims re-checked in code this round (`scanner.py`, `database.py`, `scheduler.py`, `series.html`, `test_ui_polish.py`). |
| DL | 84 | 84 | 0 | Round-1 DISAGREEs DL-017/DL-018/DL-021/DL-041 reworded correctly; new DL-082/083/084 verified against `scheduler.py`, `downloads_reconcile.py`, `main.py` grep, templates grep. No new errors introduced by the rewordings I checked (DL-034/042/081). |
| OPS | 98 | 98 | 0 | Spot-checked the round-1 reworded claims (OPS-011/019/030/061/067/087/090/091) and all four new OPS-095…098 against `auth.py`, `main.py`, `notifier/webhook.py`, `database.py`, `background_tasks.py`; all accurate. One non-blocking precision note on OPS-067 wording (§5). |
| UI | 137 | 137 | 0 | Spot-checked the changed claims UI-011/020/090/094/101/103/128 and the new UI-137 against `base.html`, `downloads.html`, `manual_pickup.html`, `auth.py`; all accurate (incl. `data-task-kind="downloads:process"` being declared but never emitted). |
| **Part 0** | — | AGREE | 0 | Behaviour statements, arithmetics and corrected invariants all verified (see §3). Three residual cross-reference typos (non-blocking, §4). |

---

## 2. Round-1 blockers — closure check (all 12)

| Round-1 blocker | v0.10 status | Verification |
|---|---|---|
| B1 REL-018 overbroad `resolve_status` + equal-rank `IN_CALIBRE` | **Resolved** | Registry line 41 now scopes the automation rule, adds the equal-rank `REQUESTED` caveat and the explicit user/support carve-out; matches `database.py:253-261,299-307` (`resolve_status(IN_CALIBRE, REQUESTED) → REQUESTED`). Part 1 §3.7/§3.21 and Part 0 inv2/rule 2 now consistent. |
| B2 SER-018 loading mechanism | **Resolved** | Registry line 88 now states the 60 ms self-scheduling chain, observer redundancy and error-only Load More; matches `series.html:108-169,171-192,236-246`. |
| B3 SER-032 "≥ 0.90" | **Resolved** | Registry line 102 now "strictly above 0.90"; matches `parser.py:1829,1890-1903` (`best_score = threshold`, `if score <= best_score: continue`). |
| B4 DL-017 "above max_concurrent" | **Resolved** | Registry line 184 now "at or above"; matches `scheduler.py:1499-1510` (`len(active) >= max_concurrent`). |
| B5 DL-018 zip/unknown-size escape | **Resolved** | Registry line 185 now says zip also when the estimate is unknown, with streaming cap; matches `scheduler.py:2199-2216` and `torbox.py:1320-1338`. |
| B6 DL-021 1/2/4 s backoff | **Resolved** | Registry line 188 now "3 attempts … sleeping 1 s then 2 s"; matches `torbox.py:1275-1290` (4 s map entry unreachable because retry requires `attempt < 3`). |
| B7 DL-041 "above max_items" | **Resolved** | Registry line 208 now "at or above"; matches `scheduler.py:1651-1657`. |
| B8 Part 0 "never write NEW" | **Resolved** | Rule 2 now respects `resolve_status`; invariant 8 and §5 rule 3 state `NEW` is a real stored status; no "never write a status of NEW" string remains in v0.10 (grep). Matches `matcher.py:196-214`, `scheduler.py:3579-3603`, `database.py:2559`. |
| B9 Part 0 "drops blacklisted releases" | **Resolved** | §4.2 step 4 now says blacklisted-series releases are **stored** with status `BLACKLISTED` and duplicates upsert by guid; matches `scheduler.py:3573-3577,3638`; `database.py:2461-2571`. |
| B10 Part 0 Basic-auth session cookie | **Resolved** | §2.1 diagram and §4.9 now state Basic mode has no session cookie; OIDC-only session; matches `auth.py:992-997,1056-1058`. |
| B11 Part 0 lifecycle diagram / precedence wording | **Resolved** | §4.3 now: download completes sets `download_status=COMPLETED`, `IN_STAGING` comes from staging/ownership reconcile, `IGNORED → unignore → REQUESTED`, equal-rank caveat; automated precedence scoped, user/support carve-out. Matches `main.py:1314-1361`; `matcher.py:153-172`; `main.py:4088-4093`; `database.py:2573-2594`. |
| B12 Part 0 cross-references / route partition / registry filename | **Resolved except 3 citation typos** | Route arithmetic verified: 10 HTML + 3 auth + 1 health + 30 REL + 24 SER + 42 DL + 14 OPS + 1 debug = 125 routes / 111 JSON; REL 30 and DL 42 match the exact route-prefix counts in `main.py`. Registry filename references all use `FEATURE_REGISTRY_v0.10.md`. Three residual claim-ID typos remain, listed as non-blocking (§4). |

---

## 3. New claims (12) — verified against code

| Claim | Verification | Verdict |
|---|---|---|
| REL-043 | `main.py:1471-1501` re-derives via `get_releases_grouped_by_series(status, search=series_key)` (search bypasses the 5 s cache, `database.py:4525-4540`), matches `series_key`/`series_name` case-insensitively, slices; no `prioritized_only`/`sort` passed; "Show all" is only rendered when `release_total > shown`. | AGREE |
| REL-044 | `main.py:4130-4133` (status-only param) → `database.py:4616-4668` (default `limit=500`, no total field, `rows.sort(key=-prioritized_priority)`); accurate. | AGREE |
| SER-093 | `calibre/matcher.py:127-131` (Calibre owned ∪ staged), `:150-205` decision use; staged map `staged_ownership.py:24,116-137` 60 s cache; `scanner.py:190-243` bundle expansion. | AGREE |
| SER-094 | `false_series_matches.py:55-67` (audit `suggested_series` = fixed Calibre match) vs `:99-126` (repair writes parsed series); Part 2 Q19 added. | AGREE |
| DL-082 | `scheduler.py:1029-1053` precedence (`download_wanted_json` → stored missing → fresh matcher); correction writers `scheduler.py:1757-1776` and submission handle `database.py:2655-2686`; accurate as an invariant. | AGREE |
| DL-083 | grep: `qbit_category` read only at `main.py:606` (live status filter) beyond config persistence (`main.py:3556-3566`); `qbit_tags` stored only; both release-add paths hard-code `manga`/`mangatrax` (`main.py:3792-3797`, `scheduler.py:3744-3750`). | AGREE |
| DL-084 | grep across `backend/app`: `/api/downloader/queue` appears only at its route definition `main.py:3578-3583`; no template/JS caller; Downloads page uses `/api/downloads/status`. | AGREE |
| OPS-095 | `notifier/webhook.py:253-335`: real `_post_json` posts with `timeout=10.0`, per-channel `{success,message}` results and overall `status`; `main.py:4281-4289` is the only settings test-webhook route. | AGREE |
| OPS-096 | `series.html:25` Sync Calibre button has no `data-task-kind`; `main.py:1909-1930` wraps the scan in `_recorded_task`. | AGREE |
| OPS-097 | grep `"QUEUED"`: only read filters (`downloads_reconcile.py:29-31`, `database.py:1189-1191`, `main.py:1343`); no write path; qBittorrent submit → DOWNLOADING; Torbox queue via `downloader_id='queued:<n>'`. | AGREE |
| OPS-098 | `database.py:640-646` (fresh: danke-Empire, 1R0N, Lu自主规制, a-sloth) vs `:4702-4708` (reset: danke-Empire, LuCirAn, 1stKind, TenmaLand, Stick). | AGREE |
| UI-137 | `downloads.html:47` header declares `data-task-kind="downloads:process"`; duplicate "Tag & move now" button at `:359` has no binding; `/api/staging/tag` (`main.py:2076-2099`) is a foreground await creating no persisted task. | AGREE |

---

## 4. Part 0 — verdict and residual (non-blocking) corrections

**Part 0 verdict: AGREE.** All corrected invariants match code: NEW stored (inv8, §5.3); blacklist rows stored (§4.2); Basic auth no cookie (§2.1, §4.9); lifecycle diagram (§4.3) correct including the equal-rank `IN_CALIBRE → REQUESTED` caveat; route partition arithmetic 30/24/42/14+1 = 111 JSON of 125 total verified (REL 30 and DL 42 match exact route prefixes, remaining split by owning surface as the section states); 45 writable settings keys counted in `SettingsUpdateRequest` (`schemas.py:645-719`); auth exemption list (`/auth/*`, `/static/*`, `/favicon.ico`, `/health`, `/healthz`) matches `auth.py:946-947`; `background_tasks`/`TaskReporter` wording correct; registry filename references correct; SER §7 count 19 / DL §7 count 13 match the parts (verified Q-header counts); Part 5/index numbering fixed (§0.3 now says §7).

Three residual claim-ID typos (editorial fixes for the v1.0 copy; they do not change any behaviour statement):

| Location | Problem | Correction | Evidence |
|---|---|---|---|
| Part 0 §3.2 inv2 | Cites `OPS-078` for status precedence; OPS-078 is the series-attribution audit/repair claim | Cite `REL-018` (+ `database.py:272-307`) only | `FEATURE_REGISTRY_v0.10.md:332`; `APP_DESIGN_SPEC_v0.10.md:314-315` |
| Part 0 §3.2 inv6 | Cites `SER-093` (ownership union) for "name joins, not ids; deleting/renaming a Calibre series does not update trackers" | Cite `Part 2 Q4` + `OPS-089` (no FK constraints); drop `SER-093` | `FEATURE_REGISTRY_v0.10.md:163,343`; `APP_DESIGN_SPEC_v0.10.md:324-325` |
| Part 0 §4.2 step 2 | Cites `SER-020` (nav active state) for auto-link/merge of trackers | Cite `SER-032` (auto-link) + `SER-037` (link/merge service); drop `SER-020` | `FEATURE_REGISTRY_v0.10.md:90,102,107`; `APP_DESIGN_SPEC_v0.10.md:358-359` |

---

## 5. Post-correction spot-checks of foreign claims (5+ required)

| Claim | Checked against | Result |
|---|---|---|
| SER-018 (reworded) | `series.html:108-169` (60 ms self-chain at 160; error reveal at 164-165), `:236-246` | Accurate |
| SER-032 (reworded) | `parser.py:1829,1890-1903`; `database.py:2064` | Accurate |
| DL-017 (reworded) | `scheduler.py:1499-1510` (`>=`) | Accurate |
| DL-021 (reworded) | `torbox.py:1275-1290` | Accurate |
| DL-041 (reworded) | `scheduler.py:1651-1657` | Accurate |
| SER-004 (reworded) | `main.py:1144`; `database.py:3059-3060`; `index.html:142,152` (`len` of 10-row fetch) | Accurate |
| DL-042 (reworded) | `main.py:2104-2123,2115-2122` (persisted inventory, no scan on read) | Accurate |
| DL-081 (reworded) | `downloads_reconcile.py:62-72` (`_volumes_in_calibre`: `max(volumes) <= book_count`) | Accurate (book-count proxy correctly stated) |
| OPS-067 (reworded) | `downloads_reconcile.py:160-165` + `:62-72` | **Non-blocking precision note:** "promoted to IN_CALIBRE when Calibre owns the wanted volumes" is looser than the code (`book_count >= max(wanted volume)`); DL-081 states the proxy exactly. Suggest aligning OPS-067's wording with DL-081 ("…when the matched Calibre series' book count covers the highest wanted volume"). |
| UI-103 (reworded) | `scheduler.py` whole-torrent condition (`whole_torrent = not descriptors and not selected_indexes and not files`) | Accurate |

---

## 6. Self-check — my REL-001…REL-044 extraction

* **Identity/regression check passed:** merged Part 1 REL checklist == registry v0.10 REL lines, 44/44 identical (0 diffs after trim). The v0.9 → v0.10 diff is exactly the agreed set — 6 reworded (REL-018, -024, -025, -028, -038, -039) and 2 new (REL-043, -044); no other REL line changed, nothing truncated or renumbered.
* **Rewordings re-verified:** REL-018 (registry line 41 vs `database.py:253-261,299-307`), REL-024 (`rss.py:48-52`, `html_scraper.py:58-62`), REL-025 (`nyaa.py:190-208,320-338` — empty body retried, 404 or zero-row page ends), REL-028 (provider filters silent at `rss.py:69-77`; scheduler audits), REL-038 (all download/status writers do call `_invalidate_grouped_cache()` — 11 functions at `database.py:2653,2686,2703,2716,2728,2764,2793,2806,2817,2841,2856`; tag/release-detail/series-link/cover writers do not), REL-039 (`registry.py:17-47`, `nyaa_source.py:96-113`). All correct.
* **REL-043/044** verified as new claims (§3). No self-corrections requested beyond the already-applied REL-018 wording.

---

## 7. Non-blocking editorial notes / deferred items (do not block consensus)

1. **OPS-067 wording** — align with DL-081's precise book-count proxy (see §5).
2. **Part 0 residual claim-ID typos** — OPS-078 (inv2), SER-093 (inv6), SER-020 (§4.2 step2) per §4.
3. **Duplicated claims remain by design** (REL-021↔DL-032/033, REL-015↔SER-054, REL-016↔SER-086, REL-035↔DL-079/080, REL-036/037↔SER-056…065/OPS-074/075, REL-038↔SER-083/OPS-082, REL-042↔SER-074, REL-021 state↔DL-033, UI-094↔UI-137). Per `CORRECTIONS_ROUND1.md` §4 these are intentionally retained (no claim may be deleted) and cross-referenced; no action required for v1.0 except the deferred editorial aliasing pass.
4. **No behavioural code-vs-spec disagreements remain** in the parts I reviewed; open questions in Part 0 §7 correctly point at the SEC/DL/REL issue lists (REL Q6, SER Q2/Q19, DL Q1/Q8, OPS R-6 all exist in v0.10).

---

**GLOBAL VERDICT: AGREE**
