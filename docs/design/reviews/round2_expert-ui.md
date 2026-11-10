# Consensus Round 2 — expert-ui re-vote (v0.10, 457 claims)

**Reviewer:** EXPERT-5 (UI author; round-1 reviewer of Part 0 / Part 3 DL / Part 4 OPS)
**Task:** task-15 · **Date:** 2026-11-08
**Inputs read:** `CORRECTIONS_ROUND1.md`, all five `docs/design/reviews/round1_*.md`, `FEATURE_REGISTRY_v0.10.md` (all 493 lines / 457 claims), `APP_DESIGN_SPEC_v0.10.md` Part 0 (lines 35–580), Parts 3 and 4 (inventories, §3 prose where corrected, and §9 checklists), and the v0.9 registry for a mechanical diff.
**Method:** re-verified every round-1 blocker against the current code with `file:line`; verified all 12 new claims; mechanically diffed the UI block against v0.9; machine-compared all 457 registry lines against the merged spec's consensus checklists (normalized text); spot-checked 15 foreign rewordings. Read-only: no spec, code or test file was modified; this review is the only file written.

## Vote summary

| Section | Claims | Disagree | Result |
|---|---|---|---|
| REL | 44 | 0 | No disagreements in this section. |
| SER | 94 | 0 | No disagreements in this section. |
| DL | 84 | 0 | No disagreements in this section. |
| OPS | 98 | 0 | No disagreements in this section. |
| UI | 137 | 0 | No disagreements in this section. |
| Part 0 | — | 0 behavioural | **AGREE** (4 residual cross-reference typos + prose-drift list recorded as non-blocking editorial; no behavioural statement disputed) |
| **Total** | **457** | **0** | **0 blocking disagreements** |

**Integrity checks (mechanical).**
* Registry: 457 claims, IDs contiguous by section (REL 44, SER 94, DL 84, OPS 98, UI 137), no duplicates
  or gaps (scripted).
* Registry ↔ merged spec: all **457** claim texts are byte-identical to the corresponding checklist lines
  in `APP_DESIGN_SPEC_v0.10.md` (457 parsed, 0 missing, 0 mismatched). No claim was silently reworded
  during the merge.
* UI extraction: v0.9→v0.10 UI block went 136→137 lines: exactly the 8 agreed rewordings changed
  (UI-011, UI-020, UI-090, UI-094, UI-099, UI-101, UI-103, UI-128) plus the new UI-137; all other 128
  lines are byte-identical and in the same order (no truncation, no renumbering).

---

## 1. Round-1 blockers — all resolved

| My round-1 blocker | v0.10 wording (registry line) | Code re-check | Verdict |
|---|---|---|---|
| **OPS-061** scheduler shielding | "shields the whole tick in a single try/except: a job exception is logged and the remainder of that tick's jobs are skipped, while the loop itself continues" (reg:315) | scheduler.py:5185–5277: one `try` at 5187, one `except` at 5274; loop continues at 5277. | RESOLVED |
| **OPS-067** stale reconcile | "reconciled: re-pointed at a found copy, promoted to IN_CALIBRE when Calibre owns the wanted volumes, or otherwise reset to IDLE with DOWNLOAD_FILES_MISSING" (reg:321) | downloads_reconcile.py:108–195: kept/re-pointed 143–153, IN_CALIBRE 155–165, staging re-point 167–178, reset only 180–183. | RESOLVED (one precision note in §4) |
| **OPS-087** schema transaction | "created … idempotently on one connection with a single final commit; … not one atomic transaction" (reg:341) | database.py:405–997: one connection, final commit 995, plus a conditional intermediate commit at 936 on the first-run LN backfill; schema DDL is not atomic (early DDL autocommits). | RESOLVED; two wording nits recorded in §4 (non-blocking) |
| **UI-011** task-panel Cancel | "Cancel branch exists but never renders because /api/tasks never emits a cancellable field" (reg:366) | base.html:1039 renders on `task.active && task.cancellable`; background_tasks.py:51–76 serialize_task emits no `cancellable`; only `tags:refresh` is cancellable (main.py:1560). | RESOLVED |
| **UI-099** tag-modal field count | "edits seven fields (staged filename, Series, Volume, Title, Writer, Publisher, Genre)" (reg:454) | downloads.html:635,639,643,647,651,655,659 = seven `tag-field-*` inputs; current-XML block 631 is read-only. | RESOLVED |
| **UI-101** Downloads page sections | "eight card sections … plus the downloader-config card, a conditional pickup banner and modals" (reg:456) | downloads.html `<h2>` count = 8 (lines 9,120,166,194,336,443,534,566); config card 57–115; banner 21–26; modals 2064–2105. | RESOLVED |
| **Part 0: basic-mode session cookie** | §4.9 (spec:470–472): "no session cookie is set … 401 with a Basic challenge" | main.py:1064–1067 (no cookie in basic), 1115–1124 (cookie only in the OIDC callback); auth.py:1051–1063 (challenge). | RESOLVED |
| **Part 0: route partition** | §6 (spec:519–527): 30 + 24 + 42 + 14 + 1 = 111 JSON, 125 total | Arithmetic checks out; consistent with §1.2 and Part 5 map. | RESOLVED |
| **Part 0: QUEUED lifecycle** | §4.2 step 7 (spec:375–376): IDLE→DOWNLOADING→COMPLETED/FAILED (+parked states) | No writer of `download_status='QUEUED'` exists; only readers (main.py:1343; database.py:1190; downloads_reconcile.py:30). | RESOLVED |
| **Part 0: NEW / blacklist / lifecycle / exemptions / 45 keys / TaskReporter** | §3.2 inv. 2/8, §4.2 step 4, §4.3, §4.9, §1.2, §2.6 (spec:310–331, 364–366, 389–402, 470–476, 133) | Verified against database.py:253–307, scheduler.py:3573–3603 and 4088–4093, auth.py:946–947, schemas.py:645–923 count = 45 keys, main.py:114–168. | RESOLVED |

---

## 2. New claims (12) verified against code

| Claim | Verified evidence |
|---|---|
| **REL-043** series_releases re-derivation | main.py:1471–1501 (`get_releases_grouped_by_series(status=status, search=series_key)` — no prioritized/sort params; case-insensitive key/name match 1481–1486; slice 1490); database.py:4026 cache path is bypassed when `search` is set. |
| **REL-044** prioritized releases API | main.py:4130–4133; database.py:4616–4668 (default `limit=500`, no `total` field; post-sort by priority at 4667). |
| **SER-093** ownership union | calibre/matcher.py:11–16, 127–131 ("Calibre-owned ∪ staged-owned"), 149–165 (IN_STAGING branch); staged_ownership.py:24 (`CACHE_TTL_SECONDS=60`), 54–152. |
| **SER-094** false-series audit vs repair target | false_series_matches.py:49–73 (`suggested_series` = fixed Calibre match when found) vs 100–136 (repair re-keys to title-parsed `parsed`). |
| **DL-082** wanted-set authority | scheduler.py:1029–1053 (download_wanted_json → stored missing → fresh matcher); database.py download_wanted_json column. |
| **DL-083** qBittorrent category/tags | main.py:3795–3796 (literal `manga`/`mangatrax`), 606 (`category` filter for status list), 3556–3557 (settings persisted); scheduler.py:3745–3749 (literal at auto-download). |
| **DL-084** dead queue API | main.py:3578–3583; no `/api/downloader/queue` reference in any template (grep of `backend/app/templates`); Downloads page uses `/api/downloads/status`. |
| **OPS-095** test-webhook | main.py:4281–4289; notifier/webhook.py:253–335 (real `_post_json` posts, timeout 10 s at 285/311, per-channel `results` + overall `status`, masked-free messages). |
| **OPS-096** sync button has no task binding | main.py:1909–1930 (`_recorded_task` creates the persisted task and waits); series.html:25 button has no `data-task-kind`; reattach only matches `data-task-kind` (base.html:1074–1099). |
| **OPS-097** QUEUED never written | Repo-wide: no `update_download_status(..., "QUEUED")`/`update_downloader_state(..., "QUEUED")`; readers only main.py:1343, database.py:1190–1191, downloads_reconcile.py:29–33; qBittorrent writes DOWNLOADING (database.py:2655–2686); Torbox queueing = `queued:<n>` handle (downloader/base.py:48–68) + `torbox_progress_json.phase`. |
| **OPS-098** reset group seeds | database.py:637–646 (fresh DB: danke-Empire, 1R0N, Lu自主规制, a-sloth) vs 4700–4709 (reset: danke-Empire, LuCirAn, 1stKind, TenmaLand, Stick). |
| **UI-137** task-kind contract | downloads.html:47 declares `downloads:process`; main.py:2076–2099 creates no persisted task; downloads.html:358 duplicate has no binding; base.html:1074–1091 matches `task.kind`. |

---

## 3. Foreign spot-checks after reword (15; requirement was 5)

| Claim | Check | Result |
|---|---|---|
| REL-018 | database.py:299–307: incoming IN_CALIBRE wins; IN_CALIBRE→IN_STAGING blocked; equal-rank `>=` at 305 lets incoming REQUESTED replace stored IN_CALIBRE. | correct |
| REL-025 | providers/nyaa.py:27–28, 190–208, 320–338: 404 or parsed zero-row page ends; empty body raises/retries; Retry-After capped 30 s. | correct |
| REL-039 | providers/registry.py:20–22, 40–41: per-feed `config.base_url` wins over injected setting. | correct |
| REL-024 | providers/rss.py:50–52, html_scraper.py:60–62: SSRF-rejected URL → `[]`. | correct |
| SER-032 | parser.py:1891, 1896 (`if score <= best_score: continue`): strictly above threshold. | correct |
| SER-090 | No working cancel control (OPS-059/base.html:1039). | correct |
| DL-017 | scheduler.py:1503 `if len(active) >= max(1, max_concurrent)`. | correct |
| DL-021 | torbox.py:1275–1291: attempts (1,2,3), sleeps 1 s then 2 s; 4 s entry unreachable. | correct |
| DL-041 | scheduler.py:1651 `count_active_manual_pickups() >= max_items`. | correct |
| DL-042 | main.py:2114–2122 attaches persisted inventory (`load_inventory`); no scan on read. | correct |
| DL-081 | downloads_reconcile.py:62–72: promotion proxy = `max(volumes) <= book_count`. | correct |
| OPS-011 | auth.py:1043–1077: basic = 401 challenge for HTML+API; oidc = API 401 then 302 with `?next=`. | correct |
| OPS-019 | auth.py:1150 `record_identity(identity, force=True)` in the OIDC callback. | correct |
| OPS-030 | database.py:4712–4713 resets `backfill_status`/`backfill_progress`; other tables untouched per 4677–4681. | correct |
| OPS-079 | main.py:2519–2548 + false_series_matches.py:100–136: confirm + ≤500 ids, repair re-parses selected ids with no audit re-validation. | correct |
| OPS-090 | database.py:728–780 = 39 defaults + filter_non_manga/media_filter_applied = 41; JPEGXL + LN markers = 43; 4 groups; 1 seeded feed. | correct |
| OPS-091 | database.py:1005–1103 (media gate incl. `filter_non_manga`) vs 1105–1164 (JPEGXL-only pass gated by `jpegxl_filter_applied`). | correct |

---

## 4. Non-blocking editorial corrections (no claim re-vote required)

These do **not** change any accepted claim's meaning; they should be applied in the v1.0 editorial merge.
I record them so the signed document does not carry stale prose or wrong pointers.

**(a) Part 0 residual claim-ID typos (4).** The status note says every cross-reference was fixed; four are
still wrong pointers (the sentences themselves are behaviourally correct):

1. §3.2 inv. 2, spec:313–315 — "(REL-018, OPS-078)": OPS-078 is the series-attribution audit; the
   precedence statement is REL-018 + Part 4 §4.4 (or SER-086 for vetoes). Replace OPS-078.
2. §3.2 inv. 6, spec:325 — "(SER-093, Part 2 Q4, OPS-089)": SER-093 is ownership union, not name-joins;
   cite OPS-089 (no FKs) + Part 4 §4.1, keep Part 2 Q4.
3. §4.2 step 2, spec:359 — "(SER-019/020, SER-032)": SER-019/020 are the match modal and nav highlight;
   the auto-link/identity claim is SER-032/SER-037/SER-045–047.
4. §4.9 basic bullet, spec:472 — "(OPS-011, OPS-013)": OPS-013 is the OIDC `/auth/login` PKCE claim; the
   Basic challenge is OPS-012. Replace with OPS-011/OPS-012.

**(b) Domain-part §2 inventory cells not re-synced to the corrected §9 claims** (the §9 checklists and the
registry are correct; only the "user-facing behavior" summaries of the same IDs are stale). Verified in my
two assigned parts: Part 3 §2 — DL-017 ("above"), DL-021 ("1/2/4 s"), DL-034 (no "no series control"),
DL-041 ("Above"), DL-042 (no "persisted copy"), DL-081 ("confirmed in Calibre" rather than the book-count
proxy). Part 4 §2 — OPS-011 (HTML 302 unconditionally), OPS-019 (no force exception), OPS-030 (no backfill
state reset), OPS-061 (no whole-tick shield), OPS-067 (reset-only), OPS-079 (old "only rows the fixed
matcher would no longer map"), OPS-087 ("one transaction"), OPS-090 ("45+ default keys"), OPS-091 (old
wording). Part 4 §4.4 (spec:3601) still lists QUEUED among "Values observed", contradicting OPS-097 —
reword to "tolerated on read; never written". (The editorial pass should re-sync every part's §2 cells from
the v0.10 §9/registry, including Parts 1/2 which I did not re-read this round.)

**(c) OPS-067 vs DL-081 precision.** OPS-067 says "promoted to IN_CALIBRE when Calibre owns the wanted
volumes"; the code's proxy is the matched Calibre **book count** covering the highest wanted volume
(downloads_reconcile.py:62–72), exactly as DL-081 states. Suggest aligning OPS-067 to DL-081's wording.

**(d) OPS-087 mechanism nit.** The operative conclusion ("not one atomic transaction") is correct, but
"single final commit" omits the conditional intermediate commit during the first-run LN backfill
(database.py:936), and on Python 3.6+ (image ships 3.11) `sqlite3` does not implicitly commit before DDL —
early DDL simply runs in autocommit before a transaction is opened. Suggested wording: "one connection, a
final commit at :995 plus a conditional first-run commit at :936; early schema DDL autocommits, so the
schema work is not one atomic transaction."

**(e) OPS-096 phrasing (optional).** "the button cannot be disabled or reattached across reloads" reads as
never disableable; the click handler does disable it while the synchronous request runs (base.html:516–539).
Qualify as "cannot be disabled/reattached across reloads (no data-task-kind)".

---

## 5. Self-check — UI-001…UI-137 as extracted

* Mechanical diff v0.9→v0.10 UI block: 136 → 137 claims; exactly 8 rewordings (UI-011, UI-020, UI-090,
  UI-094, UI-099, UI-101, UI-103, UI-128) + the new UI-137; other 128 lines byte-identical, order intact,
  no truncation/regression. All 8 rewordings re-verified against the templates/JS:
  UI-011 (base.html:1039; background_tasks.py:51–76), UI-020 (auth.py:1043–1077), UI-090
  (downloads.html:872–880 → `refreshDownloadsNow`), UI-094 (downloads.html:47/358; main.py:2076–2099),
  UI-099 (seven `tag-field-*` inputs), UI-101 (eight `<h2>` + config card + banner), UI-103
  (scheduler.py:1803–1840: whole-torrent zip only when no file list), UI-128 (releases.html:230/238;
  series.html:162–165). UI-137 verified (§2).
* No lost text or misstatement remains in my section; I accept all 137 UI claims as written.

---

## 6. Global verdict

**GLOBAL VERDICT: AGREE**

All 457 registry claims are accepted as written (REL 44 / SER 94 / DL 84 / OPS 98 / UI 137 — 0
disagreements), the merged spec's §9 checklists are byte-identical to the registry, every round-1 blocker
of mine is resolved (OPS-061, OPS-067, OPS-087, UI-011, UI-099, UI-101, and all Part 0 behavioural fixes
including basic-auth cookies, route partition, citations, QUEUED and scheduler shielding), and all 12 new
claims are accurate against the code.

No blocking disagreements. The items in §4 are non-blocking editorial fixes (4 Part-0 pointer typos,
stale §2 inventory cells / §4.4 QUEUED sentence, two wording precision nits) and can be applied in the
v1.0 editorial pass without reopening the claim vote. The UI-enhancement design phase can start from the
Part 5 gap audit (G-01…G-50) once the Lead records this sign-off.
