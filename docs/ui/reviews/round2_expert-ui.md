# UI-Enhancement Design Validation — Round 2, expert-ui (aspects D + E)

**Reviewer:** EXPERT-5 — visual consistency & accessibility (D), workflow continuity & feedback (E); UI design authority (Part 5 / G-01…G-50).
**Task:** task-30 · **Date:** 2026-11-08
**Input:** `docs/UI_ENHANCEMENT_SPEC.md` v0.2 (387 lines, read in full), my round-1 review
(`docs/ui/reviews/round1_expert-ui.md`), final `docs/APP_DESIGN_SPEC.md` Part 5 (gap audit + §4.11 test
catalogue), and the templates/tests the v0.2 requirements touch (re-grepped for every pinned literal).
Read-only: this review is the only file written. **Note:** a Phase-1 implementation edit is landing in the working tree concurrently (ase.html, 	ests/test_nav_structure.py are modified); line references to ase.html are to the pre-implementation baseline reviewed here, and the verdict below is on the v0.2 specification, not on that in-progress code.

**Result: DESIGN VERDICT: DISAGREE** — 8 round-1 blockers: **4 RESOLVED** (B1, B3, B5, B6),
**4 REMAINING** (B2, B4, B7, B8). Remaining blockers: **4** (§2). The v0.2 revision is a large,
well-targeted improvement; every remaining item is a contract/consistency fix, not a design reversal.
Fixes are concrete and small; I expect AGREE in round 3.

---

## 1. Per-blocker status

| # | Round-1 blocker | v0.2 evidence | Status |
|---|---|---|---|
| **B1** | Header-button count contradiction | UX-002 now states **7** (Library, Discover, Downloads, System, user, hamburger, palette) and explicitly says the source test counts the auth-conditional user button; §4 nav row says 5→7 and lists the Downloads trigger/child-map changes. Current baseline confirmed: 5 header `<button>` in `base.html` (129/149/177/199/212), header-segment test at `tests/test_nav_structure.py:184–200`. | **RESOLVED** |
| **B2** | §7/§4 test-contract list incomplete/incorrect | Much improved: Downloads `<a>`→`<button>` (:80), child map (:98–127), header 5→7, aria-controls, sheet/palette/new-suite rows. But still misses/errs on four pinned contracts (see **R1**). | **REMAINING** |
| **B3** | More sheet vs quick-action/test collision | UX-006 is explicitly **navigation-only** (no quick actions/search/"Poll Feeds"), ids + `_mobile_menu` markers stay valid, 10 routes/headings/≥10 `min-h-[44px]` preserved; palette markup placed outside `<header>` and after `#toast-container` (test-segment safe). §4 keeps mobile-menu assertions. Confirmed `test_nav_structure.py:283/284` (`>Actions<`, `Poll Feeds` absent) stays satisfiable. | **RESOLVED** |
| **B4** | Overlay stacking underspecified | UX-007 now has a numeric ladder, CSS vars (`--mt-header-h`, `--mt-bottom-bar-h`), viewport-fit, toast/load-more/save-bar rules, safe-area application. One internal ordering contradiction remains (Tasks z-95 above dialogs/palette z-90 and outside UX-013 `inert`) — see **R2**. | **REMAINING (partial)** |
| **B5** | Dangling/un-numbered requirements | UX-013 dialog, UX-014 toast/live regions, UX-015 contrast tokens, UX-016 safe-area/reflow/motion, UX-017 44 px/aria matrix, UX-018 pinned literals, UX-019 offline banner are now numbered with concrete APIs/tokens. `UX-016` no longer dangling. | **RESOLVED** |
| **B6** | Unsaved guard + qBit password | UX-030 defines dirty lifecycle, clear-only-on-success, `beforeunload`, in-app Stay/Discard, four intentional-reload suppressions, Discard reset, blank-render + omit-blank qBit password, and the deliberate blank=unchanged semantics change. Feasible against `settings.html:1158/1489/1518/1534`. Minor traceability correction only (C2/C6). | **RESOLVED** |
| **B7** | Card conversion feasibility/accessibility contract | Series (UX-022, 7 cells) and releases-flat (UX-023) are properly specified as CSS-only with `data-label`, thead visually hidden with `scope="col"`, `.mt-table-empty` empty-state rule, 44 px actions. **Downloads tables and their JS builders are still not covered**, and table-semantics protection is under-specified — see **R3**. | **REMAINING (partial)** |
| **B8** | Gap disposition missing | §8 now has all **50** rows (G-01…G-50, verified contiguous/unique) with FIX/PARTIAL/DEFER; §7 lists 11 deferred items with reasons. Remaining: three PARTIAL rows have no "(with what)", G-45 is mislabelled, and acceptance criteria still don't verify several D/E requirements — see **R4**. | **REMAINING (partial)** |

---

## 2. Remaining blocking issues

### R1 — §4/UX-018 pinned-literal matrix is still incomplete and has two wrong attributions *(from B2)*

Acceptance #1 and non-negotiable #3 make this matrix the "authority"; as written an implementer can
keep the spec and still fail four suites:

1. **`tests/test_release_download_state.py` is absent from §4 and its literals are missing from UX-018.**
   It pins `"/api/releases/${releaseId}/state"`, **`"setTimeout(poll, 1500)"`** (`releases.html:646,650`;
   `prioritized.html:587,591`), `updateReleaseStateDom`, `updateAllCardSummaries`, `addEventListener('click'`,
   `Retry`, `release-card-summary`, and "no `location.reload()` inside `downloadRelease`" (test:99–107).
   UX-018's `setTimeout(pollTask, 1500)` is the **base MangaTasks** literal (`base.html:1343/1351`) and
   does not replace the release-state one. Add a §4 row + the release-state literals to UX-018.
2. **§4 misattributes the performance literals.** The row says "keep `series-card-deferred`/`Load More Series`,
   `loadMoreSeries`" under `test_performance_and_lazy_loading.py`, but that suite requires
   **series: `series-row-deferred` + `loadMoreSeriesRows`** (test:124–125) and **prioritized:
   `series-card-deferred` + `loadMoreSeries`** (test:118–119). Split the row accordingly (or the
   series changes will silently break `test_lazy_loading_template_partials`).
3. **`test_releases_loadmore_availability.py` is not listed.** It pins `id="btn-load-more-series" disabled`,
   `id="btn-load-more-flat" disabled`, the forbidden `class="hidden px-5 py-2.5`, `p.error ? 'Retry loading'`,
   `{{ initial_total if current_view != 'flat' else 0 }}`, `updateLoadMoreUi()` count ≥8, and
   `isLoadingSeries = false;\n        updateLoadMoreUi();` / flat equivalent (test:42–93). §4's
   `test_releases_pagination.py` row does not cover these; add the row (UX-023 keeps the contract but
   will rewrite the surrounding markup).
4. **`test_empty_states` does not exist.** §4 lists "`… test_empty_states` strings"; empty-state copy and
   `clear*Filters` are pinned in **`test_ui_polish.py:138–162`**. Replace the name (and note UX-018
   already lists the copy — good).
5. **`test_background_tasks.py` row only protects two needles.** The suite also requires `global-tasks`,
   `tasks-panel`, `tasks-active-count`, `MangaTasks`, `bindButton`, `watch`, `/api/tasks?active=0`,
   **`aria-live`**, **`tasks-toast`** (`test:161–163`). UX-014 removes the *second* live region; if the
   `#tasks-toast` element or base `aria-live` string is deleted, the test fails. State explicitly:
   keep the element/needles (e.g. `#tasks-toast` becomes a plain status element) or list this suite as
   intentionally updated.

**Amendment:** add the two suite rows, split the performance row, replace `test_empty_states` with
`test_ui_polish.py`, add the release-state literals to UX-018, and state the `tasks-toast`/`aria-live`
decision in the `test_background_tasks.py` row.

### R2 — UX-007 ladder contradicts UX-013: Tasks floats above dialogs *(from B4)*

The ladder puts **Tasks at z-95**, above **palette/lightbox/modals at z-90** and above the **settings
save bar at z-45**. `#global-tasks` (`base.html:390`) is outside `#main-content`, `<header>` and
`<footer>`, so UX-013's `inert` list does not cover it: the Tasks button/panel stays clickable and visually
on top of an open palette/lightbox/sheet, and on `/settings` it can overlap the sticky save bar (both
bottom-right). That violates the ladder's own "collision-free" rule and dialog modality.

**Amendment:** put Tasks **below** the backdrop layer (e.g. z-55, between progress 50 and backdrop 60), or
add "UX-013 also sets `inert`/`hidden` on `#global-tasks` while any dialog is open"; and add a rule for
`/settings` (hide/offset Tasks while the save bar is visible). Add a static assertion for the ladder
ordering (Tasks < backdrop) and a manual overlap check.

### R3 — Downloads tables' card conversion + table semantics are missing *(from B7)*

UX-022 converts only `partials/series_table_row_chunk.html`; UX-023 converts only the flat-release partial.
The **Downloads page tables** (queue server rows `downloads.html:232–330`, inventory `:394+`, staging
`:603`, failed docs `:162+`, duplicates, audit) are not specified as card-converted anywhere, yet UX-022's
empty-state rule explicitly names them and **§8 marks G-34 FIX "UX-022/027/030 card mode"** — UX-027/030
contain no card-conversion requirement. The new-suite assertion list also says only "data-label on
series/release partials" (§4 line 330). Round 1 B7 required `data-label` in **server partials and the five
JS builders** plus explicit table ARIA; only half is in v0.2.

**Amendment:** add a UX-027 clause (and a §4 new-suite assertion): CSS-only card mode below 768 px for all
downloads tables, with `data-label` on every cell in server markup **and** in `_torboxRowHtml` (:935),
`renderInventory` (:1000), `renderFailedDocuments` (:1707), `renderAudit` (:1266),
`renderStagingDuplicates` (:1452); `.mt-table-empty` for colspan empty states; keep pinned ids/guards.
Second, make the semantics explicit: keep native table roles (or add `role="table/row/columnheader/cell"`),
keep `scope="col"` headers, and mark the `td::before` labels `aria-hidden` so AT hears one label, not two;
state that this applies to series/releases too. Fix G-34's ledger text to match.

### R4 — Ledger and acceptance criteria are not yet verifiable for several D/E requirements *(from B8)*

1. **Ledger incomplete as specified:** acceptance #4 requires every PARTIAL to say "with what", but
   **G-16, G-23, G-43** are bare `PARTIAL` in §8; **G-45 is marked FIX** although the gap (sortable
   headers, column chooser, row selection, sticky header; horizontal-scroll actions) is only partially
   mitigated by card mode — mark PARTIAL with what remains (or implement). G-34's FIX also depends on R3.
2. **Acceptance criteria lack verifiable checks for:** UX-013 dialog helper (focus trap, Escape topmost,
   focus return, `inert`), UX-016 reduced-motion, 200 % zoom and `env()` use, UX-017 44 px/aria matrix,
   UX-019 offline banner, UX-020 in-place download poll, UX-025 tab semantics/hash, UX-027 qBit-refresh
   safety rules (no row add/remove/reorder/control replacement, no Torbox fallback rows,
   stale→Retry), UX-028 auto-refresh guard (dirty/focus/modal/task), UX-030 Advanced-anchor visibility and
   anchor→expand behaviour for collapsed sections, UX-031 copy/pending-state rule. Some are in the
   new-suite list, but acceptance #1–#4 do not require them and several are not in the suite list at all.
3. **Add to acceptance:** either extend `tests/test_ui_enhancements.py`'s assertion list (§4 line 330)
   with the static-checkable items above, and add an explicit short **manual acceptance checklist**
   (360 px/1280 px overlap, 320 px reflow, 200 % zoom, iOS safe-area, dialog keyboard/focus, reduced
   motion, 44 px matrix, offline banner) — otherwise "verifiable" is not true for D/E.

---

## 3. Acceptance-criteria verifiability (as requested)

| Criterion | Verifiable now? | Note |
|---|---|---|
| #1 all suites + new suite pass; no literal removed without §4 row | **Partially** | Runner/auto-discovery verified (85 `test_*.py`, `run_tests.py` globs `test_*.py`, so the new suite needs no registration). But R1's omissions mean a spec-compliant implementation can still fail; fix R1 first. |
| #2 static layout contract / z-scale/offsets / 320 px reflow | **Partially** | z-var block and CSS presence are static-checkable; **fixed-layer overlap is only a "computed CSS review"** with no checklist. Add a manual checklist item (360 px/1280 px bounding boxes) and a ladder-order assertion (R2). |
| #3 contrast pairs + focus ring | **Yes** | UX-015 ratios check out numerically (body ≈15:1, secondary #94a3b8/#161e2e ≈6.5:1, white/brand-600 ≈4.7:1, white/brand-500 ≈3.7:1 correctly declared text-forbidden). Static calculation is feasible. |
| #4 gap ledger complete with reasons | **No** | G-16/G-23/G-43 lack descriptions; G-45 mislabelled; G-34 depends on R3 (see R4). |
| #5 round-2 sign-offs | Procedural | Fine. |
| — | Missing D/E checks | UX-013/016/017/019/020/025/027/028/030 as above (R4). |

---

## 4. New non-blocking corrections (fix in the same editorial pass)

* **C1** Non-negotiable #5 says "Phase 1 = UX-001…**UX-037**" but only **UX-001…UX-031** are defined —
  fix the range (or add UX-032…037). Also non-negotiable **#3** says the preservation catalogue is "§7",
  and **#5** says deferred items are "§8"; the catalogue is **§4** and deferrals are **§7**. UX-013 says
  "listed in §8" (should be §7); UX-030 says the password semantics change "is listed in §7" but no such
  row exists — add it (e.g. a §4 "deliberate behaviour changes" row or a §7 note).
* **C2** UX-030 "the four intentional `location.reload()` paths": settings has three explicit
  `location.reload()` (1158 prune, 1518 add group, 1534 delete group) plus the reset redirect
  (`href='/'` :1489) and the new Discard reload. Enumerate the exact paths so the guard-suppression list
  cannot miss the new Discard/other paths.
* **C3** UX-004 says the mobile Downloads tab shows "a small dot" while the same sentence says "count";
  define one (dot with `aria-label="N held torrents"` is fine) and whether the desktop menu item shows
  the number.
* **C4** UX-008's guard list omits the **release-state timers** (`setTimeout(poll, 1500)` after a
  download, up to 30 s) — include them (or state why they are exempt) so a hidden tab stops the poll.
* **C5** UX-014 keeps `showToast(message,type)` but `#toast-errors` needs a Retry/dismiss callback; define
  the optional third argument/return without breaking ~100 existing call sites, and cap repeated
  identical errors (otherwise every transient failure becomes a persistent assertive alert).
* **C6** UX-010 keeps only Ctrl/⌘+K; Ctrl+K is browser-reserved on Windows/Linux Chrome/Firefox for the
  address-bar search. Keep the header button as the universal route and document the fallback
  (e.g. Ctrl+Shift+K) + manual browser check; do not let the palette's only power shortcut be
  non-functional in a major browser.
* **C7** UX-030 mobile section cards collapse, but the 9 anchor chips jump to hidden sections: require
  "anchor click expands the target card before scrolling" and make scroll-spy map collapsed cards to
  their header; otherwise the G-40 nav fix introduces a new dead interaction.
* **C8** §8 G-43 is PARTIAL without description and is absent from §7 while the task brief expected a
  deferral; align the two lists (G-08 is in §7; G-43 is not).
* **C9** UX-005 `--mt-bottom-bar-h: calc(4rem + env(...))`: verify a 5-tab bar with icon+label fits 64 px
  at 320 px width and with 200 % text zoom (otherwise the var should be content-driven, not 4rem).
* **C10** UX-027 says "Fix duplicate `id="torbox-row-*"` for qBittorrent rows" — confirmed real: server
  rows use `id="torbox-row-{{ t.release_id }}"` (`downloads.html:233`) and qBit rows have no
  `release_id` (`_torrent_status_view` exposes `hash`, `main.py:507–532`), so qBit rows currently render
  a duplicated empty id. State the new key (`id="qbit-row-<hash>"`, keep `data-release-id`) explicitly.
* **C11** v0.1's non-negotiable "no backend behaviour/routes change" was dropped in v0.2; §3 heading still
  implies it. Re-add as a non-negotiable (or state that template-only changes are allowed and no route/DB
  change is permitted), so page requirements cannot drift into backend work.
* **C12** UX-013's migration list says "mobile sheet, palette, lightbox, downloads tag modal, feeds
  add-feed modal"; other modals stay un-migrated (documented as deferral) — ensure the deferral is
  visible in §7 (currently only the last row mentions tag/other migrations) so the a11y debt is explicit.

---

## 5. Confirmed resolutions of round-1 contract concerns (spot checks)

* UX-002/UX-003 header: 7 raw header buttons (4 nav triggers + user + hamburger + palette) matches the
  header-segment count logic; `data-nav-item` order stays 5; Downloads children order/Manual Pickup
  active path work with `active_page="downloads"` + `request.url.path` (no backend change).
* UX-004: endpoint `GET /api/torbox/manual-pickups` really returns `active_count` (`main.py:1663–1675`);
  badge refresh on visible/after actions is feasible; no polling loop.
* UX-006: navigation-only sheet resolves the `>Actions<`/`Poll Feeds` mobile-menu assertions.
* UX-008: keeping `setInterval(poll, 1500)` + in-callback `document.hidden` guard is feasible and
  preserves the background-tasks suite. (Also requires the R1 `tasks-toast` clarification.)
* UX-010: direct dispatch is feasible — all five actions are already global in `base.html`
  (`triggerCalibreSync:516`, `triggerFeedCheck:541`, `triggerStagingTag:594`, `openBackfillModal:756`,
  `globalStopBackfill:647`); `/api/search` contract verified (`q` max 200, limit 1–50, returns
  `{series, releases, counts}`, `main.py:3498–3514`, `search.py:72–180`). `?search=` / `?q=` bootstraps
  are frontend-only (views ignore extra params).
* UX-015/016/017: token ratios are realistic and the safe-area/motion/44 px/aria matrix is implementable;
  `viewport-fit=cover` is genuinely absent from `base.html:5` today.
* UX-027 qBit refresh: `_torrent_status_view` exposes `hash` (`main.py:508`) and qBit rows are
  server-rendered, so `data-torrent-hash` + "update existing cells only" is feasible; the explicit
  "never add Torbox fallback rows when degraded" is important because `_build_downloader_status` would
  otherwise surface Torbox DB rows (`main.py:596–603`).

---

## 6. Final verdict

All B1/B3/B5/B6 round-1 blockers are resolved; B2/B4/B7/B8 are resolved in direction but still carry the
four contract-level gaps above. No requirement is rejected; the design remains feasible and aligned with
the gap audit.

**DESIGN VERDICT: DISAGREE**

Blockers (must be applied to v0.3 before sign-off):
1. **R1** — §4/UX-018 pinned-literal matrix: add `test_release_download_state.py` row + release-state
   literals; split the performance row (`series-row-deferred`/`loadMoreSeriesRows` vs
   `series-card-deferred`/`loadMoreSeries`); add `test_releases_loadmore_availability.py`;
   replace non-existent `test_empty_states` with `test_ui_polish.py`; protect
   `tasks-toast`/`aria-live` in the background-tasks row.
2. **R2** — UX-007 ladder: place Tasks below the backdrop/modal layer (or inert/hide it while a dialog is
   open) and resolve Tasks-vs-save-bar on `/settings`.
3. **R3** — downloads tables card mode: `data-label` in server markup **and** the five JS builders,
   `.mt-table-empty`, explicit table-role/`aria-hidden` label semantics; update G-34.
4. **R4** — ledger/acceptance: describe G-16/G-23/G-43 PARTIALs, mark G-45 PARTIAL (or implement), and
   add verifiable acceptance checks (new-suite assertions + manual checklist) for UX-013/016/017/019/
   020/025/027/028/030.

Non-blocking corrections C1–C12 are recorded in §4 and can ride with the same edit.
