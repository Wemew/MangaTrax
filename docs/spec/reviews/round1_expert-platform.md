# Round 1 consensus review — expert-platform (OPS author)

**Reviewer:** expert-platform (author of Part 4, OPS-001…OPS-094).
**Assigned foreign parts:** Part 0 (verdict), Part 1 — Releases/Feeds/Discovery/Prioritized (REL), Part 5 — UI/Frontend (UI).
**Registry obligation:** vote on all 445 lines by exception (per-section counts in §1).
**Method:** read the full registry, Part 0, Part 1 and Part 5 of `APP_DESIGN_SPEC_v0.9.md` (plus Part 4 to keep my own domain consistent), then verified claims against the code at `file:line` in commit `86a7bd8`. I did not run the app or the test suite (§0.3 of Part 0 forbids nothing here, but read-only verification was sufficient for every claim I checked).

## 0. Integrity checks (mechanical, before content)

| Check | Result |
|---|---|
| Registry completeness | **445 claims**, IDs unique and contiguous per section: REL 42, SER 92, DL 81, OPS 94, UI 136 — no gaps, no duplicates (scripted check over `FEATURE_REGISTRY_v0.9.md`). |
| Registry vs spec drift | The consensus checklists in `APP_DESIGN_SPEC_v0.9.md` (§9 of every part) are **textually identical** to the corresponding registry lines for all **445/445** claims (0 differences). No lost or reworded claims. |
| My own extraction | The 94 OPS lines in the registry are **byte-identical** to the §9 checklist of `04_platform_ops.md` (0 differences) — nothing truncated or paraphrased. |
| Route count | `main.py` declares **125** `@app.*` routes = 10 HTML + 3 auth + `/health` + **111** `/api/*`. Part 0 §1.2 and Part 5 §2 are consistent with this. |
| Formatting nit | Part 1/Part 3 checklists are bulleted (`- REL-001 —`), Parts 2/4/5 are not. Cosmetic only; no action needed for consensus, but the merged v1.0 should normalise. |

## 1. Vote summary (by exception)

Every claim not named in §5 is **AGREE**.

| Section | Claims | AGREE | DISAGREE | Nature of the disagreements |
|---|---|---|---|---|
| REL | 42 | 41 | 1 (REL-025) | wording precision (see §5) |
| SER | 92 | 91 | 1 (SER-090) | one clause is factually false (cancel UI) |
| DL | 81 | **81 — no disagreements in this section** | 0 | — |
| OPS | 94 | 93 | 1 (OPS-011, **self-flagged**) | overbroad in `basic` mode; corrected wording supplied in §8 |
| UI | 136 | 133 | 3 (UI-011, UI-020, UI-128) | one false clause, one mode-conditional statement, one overbroad gap claim |

## 2. Part 0 verdict — AGREE WITH CORRECTIONS

**Statements I verified true.** Architecture/process model (§2.1–2.3), “one process, many threads”, the 10-page list (§1.2), 125 routes, 13 tables + no FKs + WAL + per-call connections (§2.4), the data-model table ownership (§3.1), the status-precedence summary and “user actions bypass precedence” (§3.2, §4.3, verified `database.py:272-307, 243-249`), the first-run/track/acquire/backfill/maintenance/auth flows (§4.1–4.9), the cross-cutting rules (§5), and the known-limitations index (§7 — its R-1/R-2/OPS R-6 match my Part 4 R-1/R-2/R-6). The “no BookOrbit integration” statement (§1.4) matches a repository-wide grep.

**Corrections required (mechanical, but Part 0 is under vote):**

* **P0-1 — wrong cross-reference IDs.** Nearly all are off by domain; corrected targets are in the registry and code:
  * §1.1 “no user roles … (OPS-009/R-9)” → OPS-009 is env seeding; use **OPS-023** (users helpers have no consumer) or just R-9.
  * §1.5 “No backup/restore, schema version or VACUUM (OPS-073/R-8)” → OPS-073 is the initial-sync worker action; use **OPS-094**.
  * §3.2 inv. 1 “(SER-019, SER-020)” → SER-019 is the match modal, SER-020 nav highlight; use **SER-045** (identity ladder) and **SER-067** (display title never matches).
  * §3.2 inv. 2 “(SER-021/OPS-078)” → SER-021 is the read-only Calibre URI, OPS-078 attribution audit; use **REL-018** (+ `database.py:272-307`).
  * §3.2 inv. 3 “(SER-025, DL-036)” → SER-025 synthetic ids, DL-036 pickup mapping; use **SER-024** (bundle-expanded volume map) and **DL-078** (staged volumes count as owned).
  * §3.2 inv. 4 “(DL-033)” → DL-033 is derived-state mapping; the wanted-set contract is **DL-037**.
  * §3.2 inv. 5 “(REL-030, OPS-079)” → parser/a false-series audit; use **REL-034** and **OPS-091**.
  * §3.2 inv. 6 “(SER-018, R-4)” → SER-018 is Series Load-More; there is no OPS claim for “name joins, not ids” — cite **SER §4.2** instead of an ID.
  * §3.2 inv. 7 “(OPS-058)” → OPS-058 is the cancel endpoint; the task-kind contract lives in **Part 4 §6.1** (no single OPS ID).
  * §4.2 step 3 “(SER-025/026, OPS-078)” → use **SER-024** / **REL-018**.
  * §4.2 step 10 “(OPS-062…OPS-066)” → those are scheduler jobs; webhooks are **OPS-085** and **OPS-086**.
  * §4.4 “qbit category/tags hard-coded … (REL-031)” → REL-031 is collected-batch inference; use **DL-003** and **REL-021**.
* **P0-2 — §2.6 cites `CURRENT_TASK`.** No such symbol exists anywhere in `backend/app` (grep: 0 hits). Replace with `background_tasks`/`TaskReporter`.
* **P0-3 — §6 route-group arithmetic.** The table lists 30+25+40+24+1 = **120** JSON endpoints, while §1.2 (correctly) says **111** `/api/*`. Reconcile by assigning the 9 contested routes (backfill/settings/calibre boundary) or by saying “~120 by owner, 111 distinct paths”.
* **P0-4 — §1.2 “~50 configuration keys”** is loose: the writable surface is **45** keys (`SettingsUpdateRequest`) plus ~10 internal markers. Suggest “45 writable settings keys (+ internal markers)”.

**Part 0 verdict:** architecture, data-model and workflow statements are accurate; the above are reference/quantity defects. **AGREE subject to P0-1…P0-3.**

## 3. Part 1 (REL) verdict — AGREE WITH TWO CORRECTIONS

**Spot checks that passed** (30 foreign claims were checked overall; REL examples):

| Claim | Verified evidence |
|---|---|
| REL-001 server-rendered lists empty, rows lazy-loaded | `main.py:1195-1207` passes `releases: []`, `grouped_series: []`; `releases.html` fetches first batch on `DOMContentLoaded` |
| REL-004 8 series / 50 rows, 10-per-card cap, Show-all expander | `main.py:1301` (`_SERIES_CARD_RELEASE_CAP = 10`), `main.py:1369-1375`; `releases.html:335` (`seriesLimit = 8`), `releases.html:409` (`flatLimit = 50`) |
| REL-009 case-insensitive set membership, highest priority wins | `database.py:203-218` (`lower(jg.value) = lower(pg.name)`), `database.py:173-186` |
| REL-010 no `total`/`search`/`sort`, `has_more = len == limit` | `main.py:1503-1521` |
| REL-013/014 discovery pagination drops tag filters and sort | `discovery.html:239-267` (hrefs carry only `page`, `page_size`, `q`) |
| REL-018 IN_CALIBRE terminal, veto never lifted by automation | `database.py:272-307` (special cases at 299-304) |
| REL-020 route exists, zero template callers | `main.py:4074-4079`; grep over `templates/` finds no caller |
| REL-027 per-feed interval never used | `grep check_interval_minutes` → only schema/write/display + global `feed_check_interval_minutes` (`scheduler.py:5190`) |
| REL-041 / REL-042 no consumers | grep over `templates/` for `releases/grouped` and `/api/search` → 0 callers |

**Corrections:**

* **REL-025 (DISAGREE, wording — non-blocking).** “treats only 404/empty as end-of-results” conflates two different things: a **parsed page with zero torrent rows** ends the crawl, but an **empty response body** is a transient failure that is retried and then raises `ProviderFetchError`. Corrected: “The Nyaa crawler paginates `?f=0&c=3_1&s=id&o=desc&p=N`, retries transient failures (including empty bodies) with backoff/Retry-After, spaces page fetches by at least 1 s, treats only an HTTP 404 or a parsed page with zero torrent rows as a genuine end-of-results, and records an explicit stop reason.” Evidence: `backend/app/providers/nyaa.py:27-28, 190-208, 320-335`.
* **Part 1 prose defect (must fix):** §4.1 lists `download_speed` among the `releases` columns read for derived state. **No such column exists** (`grep download_speed backend/app/database.py` → 0 hits); `download_speed` is only a transient dict key built from torrent/progress data (`main.py:505-527, 1333`). Remove it from the column list (or state it is derived, not persisted).

Everything else in Part 1 — including the precedence matrix, the qbit category/tags hard-code note (§5.1, §8 Q10) and the open-question list — matches the implementation.

## 4. Part 5 (UI) verdict — AGREE WITH THREE CORRECTIONS

**Spot checks that passed** (selected; 30 foreign claims checked in total):

| Claim | Verified evidence |
|---|---|
| UI-004 exactly five `data-nav-item`s in order | `base.html:122,128,148,165,176` |
| UI-005/UI-027 Manual Pickup flat link, no active state, badge never populated | `base.html:169-173`; grep for `nav-manual-pickup-count` → only the span |
| UI-006 Escape closes dropdowns and returns focus | `base.html:501-514` (`trigger.focus()` at 512) |
| UI-008 mobile menu 10 links, `min-h-[44px]` | `base.html:223-259` (10 anchors) |
| UI-010 toasts bottom-right, 4 s, `innerHTML`, no live region | `base.html:276, 402-424` |
| UI-013 only menus + lightbox handle Escape | `base.html:502, 642` |
| UI-024 `slate-850` undefined in the Tailwind config | `base.html:12-31` (no slate-850); used in 7 templates |
| UI-026 `{% block scripts %}` never overridden | only `base.html:1146` |
| UI-049 Load More disabled from first paint + sticky duplicate | `releases.html:125, 167, 179-185` |
| UI-057/UI-064 exact empty-state copy | `releases.html:362`, `prioritized.html:325` |
| UI-058 deferred classes have no producer | JS branches exist (`releases.html:504,521`), no partial emits the class |
| UI-088 Torbox-only queue polling (1 s active / 5 s idle) | `downloads.html:1158` timer, `1209-1210` `{% if engine == 'torbox' %}` gate |
| UI-094 only the header button carries the task binding | `downloads.html:47` vs `359` (duplicate has none) |
| UI-113 scroll-spy + `aria-current`, dead Advanced anchor | `settings.html:1558-1566`; nav anchor unconditional, card `{% if debug_actions_enabled %}` |
| UI-126 qBittorrent password in HTML on Settings **and** Downloads; Torbox masked | `settings.html:119`; `downloads.html:101`; `main.py:1879, 4248-4251` |
| UI-127 add-group fields inside the giant form | `settings.html:54-855` form spans the Groups card at `445-493` |
| UI-136 no styled 404/500 | no `@app.exception_handler` in `main.py`; default FastAPI JSON |

**Corrections (see §5): UI-011, UI-020, UI-128.** Otherwise Part 5 is the strongest of the two foreign parts: its route map (125 routes, 14 non-`/api`), its test-constraint inventory and its mobile/a11y/gap audit all match the code I inspected.

## 5. DISAGREE table

| Claim | Why wrong / incomplete | Corrected one-sentence claim | Evidence |
|---|---|---|---|
| UI-011 | The panel never renders a Cancel control: `serialize_task` has no `cancellable` field, so `task.cancellable` is always `undefined` and the button branch is dead. | “A floating Tasks panel polls `/api/tasks?active=0&limit=20` every 1.5 s on every page and renders up to 12 tasks with progress, ETA and error; its Cancel branch exists but never renders because `/api/tasks` never emits a `cancellable` field.” | `base.html:1039`; `background_tasks.py:51-76`; `main.py:1555-1561` |
| SER-090 | Same defect: “supports cancelling cancellable tasks” is not observable — the only cancellable kind (`tags:refresh`) has no UI control. | “The global Tasks panel polls `/api/tasks`, shows persisted progress/message/ETA/error for series-domain jobs, and disables matching action buttons while a task of the same kind/scope runs; it exposes no working cancel control.” | `base.html:1039`; `background_tasks.py:51-76, 176-187` |
| UI-020 | Only true in `oidc` mode. In `basic` mode an unauthenticated request (HTML **or** API) gets 401 + `WWW-Authenticate: Basic`, never a redirect; in `none` mode nothing is gated. | “In `oidc` mode unauthenticated HTML requests are redirected to `/auth/login` and `/api/*` requests receive 401 JSON; in `basic` mode both receive a 401 Basic challenge; the header hides auth UI in `none` mode.” | `auth.py:1051-1063` (basic challenge) vs `auth.py:1065-1077` (oidc redirect/API 401); `main.py:249-264` |
| UI-128 | Overbroad: Releases has a retry affordance and Series has a fallback button; the claim also contradicts UI-049 (“retry-on-error”) in the same part. | “Most list-fetch failures are only logged to `console.error`, leaving a skeleton or stale list; Releases turns its Load More button into ‘Retry loading’ and Series reveals the hidden Load More button, so those two pages do have a retry path.” | `releases.html:230, 238`; `series.html:162-165`; `releases.html:397-402` (the cited catch) |
| OPS-011 (self) | Mode-conditional: in `basic` mode HTML requests get 401, not 302. | “The auth middleware allows `/auth/*`, `/static/*`, `/favicon.ico`, `/health` and `/healthz`; in `oidc` mode unauthenticated HTML gets 302 to `/auth/login?next=…` while `/api/*` gets JSON 401, and in `basic` mode every unauthenticated request gets 401 + Basic challenge.” | `auth.py:1043-1077`; `test_auth_modes.py:297-346` |
| REL-025 | “404/empty” conflates end-of-results with a retried empty body. | (wording in §3) | `providers/nyaa.py:27-28, 190-208, 320-335` |

## 6. Missing / duplicated features

**Missing (should exist for the redesign phase):**
1. **Auth response matrix.** No claim states the per-mode unauthenticated response (oidc redirect vs basic challenge vs none-open). The information is scattered in OPS-011 (overbroad) and UI-020 (overbroad). Either amend both (preferred, see §5) or add one claim, e.g. `OPS-095 — In basic mode every unauthenticated request (HTML and API alike) receives 401 with a Basic WWW-Authenticate challenge; the 302 /auth/login redirect exists only in oidc mode.`
2. **Why cancellation is invisible.** OPS-059 states the outcome but not the mechanism; the task-panel contract claim should cite that `/api/tasks` (`serialize_task`) omits `cancellable` and that cancel is limited to in-process flags (lost on restart). Amend OPS-059 wording rather than add an ID.
3. **No claim for the number of settings keys/routes** — only Part 0 prose. Optional; not needed for consensus.

**Duplicated / overlapping (keep, but cross-reference so two owners do not diverge):**
1. `OPS-050` and `OPS-069` both describe the activity prune (feature vs job schedule) — both true; add “(see OPS-069)” in OPS-050 or vice versa.
2. The task panel is described in `SER-090`, `UI-011` and `UI-012` — three owners for one component, which is exactly how the Cancel error survived drafting. Recommend: UI-011/UI-012 own the panel contract; SER-090 keeps only “buttons disabled by kind/scope”.
3. `UI-005` and `UI-027` overlap (nav link vs its badge) — harmless, but UI-027 is a subset; keep both, cross-reference.

No IDs should be merged or deleted; no features are missing from the registry other than the auth-matrix wording above.

## 7. Cross-domain inconsistencies

1. **Task kinds / cancellation (OPS ↔ SER ↔ UI).** OPS-059 (“Cancel button never renders”) is correct and is contradicted by SER-090 (“supports cancelling cancellable tasks”) and UI-011 (“…and Cancel”). Part 5 §2.2 also lists the cancel route as owned by `GLOBAL` although the global UI never shows it. Resolution: amend SER-090 + UI-011; keep OPS-059; note the API works for `tags:refresh` only while the process that started it is alive (`background_tasks.py:176-187`).
2. **Auth/CSRF/security statements (OPS ↔ UI).** OPS-011, UI-020 and Part 5 §5.5 all describe “HTML redirects to login, API 401” as universal. Correct in `oidc` mode only. Also worth stating consistently: CSRF is active only when auth is enabled **and** `PUBLIC_URL` is set **and** `CSRF_PROTECT` is on (`auth.py:1007-1019`), which Part 0 R-1 and my OPS-021 already say — keep those and fix the two claims.
3. **Settings keys (REL ↔ OPS).** Part 1 §5.1 is accurate about `filter_non_manga` gating only the one-shot pass (matches OPS-091) and about the hard-coded qbit category/tags. Only wording issue: “`auto_tag` — enables the 5-minute staging tag/route pass (… also runs after manual downloads)” — the manual path runs `process_downloads_to_staging` + `tag_staging_files` regardless of `auto_tag` (`main.py:2084-2085`), so the parenthetical is fine but could note “independent of `auto_tag`”.
4. **Data-model invariants (Part 0 ↔ Parts 1/4).** Part 0 §3.2’s invariant IDs are wrong in six places (P0-1) and Part 1 §4.1 invents a `download_speed` column (see §3). Both must be fixed before the model statements can be called verified.
5. **Thread model (Part 0 ↔ Part 4).** Consistent: Torbox fetch pool is bounded by `torbox_max_concurrent` (`scheduler.py:2475-2484`), matching my Part 4 §3.4.1. No action.

## 8. Self-check of the OPS extraction (OPS-001…OPS-094)

* **Extraction fidelity:** 94/94 registry lines are identical to my spec checklist (scripted diff, 0 differences). No truncated or reworded text.
* **One misstatement found and self-corrected:** **OPS-011** — “unauthenticated HTML requests get 302 … and `/api/*` … JSON 401” is only true in `oidc` mode. Corrected wording in §5. All other 93 OPS claims re-read against the code during this round and stand as written (notably OPS-059, OPS-060, OPS-091, OPS-094, which the other parts depend on).
* **Optional precision note (no amendment needed):** OPS-050 and OPS-069 describe the same schedule from two angles; both are accurate.
* **Cross-check of dependent OPS claims used by other parts:** OPS-004/005 (`/health` probe shape), OPS-020 (actor), OPS-050/051 (prune math), OPS-059/060 (tasks), OPS-087/089 (schema/WAL) — verified again while reviewing Parts 0/1/5.

## 9. Global verdict

**GLOBAL VERDICT: DISAGREE**

Blocking disagreements (each is a factual claim error, not a style preference):

1. **UI-011** — claims the Tasks panel renders Cancel; the button can never render (`background_tasks.py:51-76`, `base.html:1039`). **SER-090** carries the same false clause and must be fixed with it.
2. **UI-020** — auth-response claim is true only in `oidc` mode; `basic` mode returns a 401 Basic challenge for HTML (`auth.py:1051-1063`). Same wording defect, self-flagged, in **OPS-011**; Part 5 §5.5 prose repeats it.
3. **UI-128** — “no retry affordance” is contradicted by the code (`releases.html:230,238`, `series.html:162-165`) and by UI-049 in the same part.
4. **Part 0 reference/quantity defects P0-1…P0-3** — about fifteen wrong claim-ID cross-references, the nonexistent `CURRENT_TASK`, and the 120-vs-111 route-group arithmetic. Mechanical to fix, but Part 0 is under vote.
5. **Part 1 §4.1** — `download_speed` is not a `releases` column; remove it from the column inventory.

Non-blocking correction: **REL-025** wording (“404/empty”) in §5/§3.

Once blockers 1–5 are amended (all wordings are supplied above), I will vote **AGREE** on all 445 claims.
