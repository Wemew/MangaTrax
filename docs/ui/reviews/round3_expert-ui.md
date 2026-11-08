# UI-Enhancement Design Validation — Round 3 (final), expert-ui (aspects D + E)

**Reviewer:** EXPERT-5 — visual consistency & accessibility (D), workflow continuity & feedback (E); UI design authority (Part 5 / G-01…G-50).
**Task:** task-35 · **Date:** 2026-11-08
**Input:** `docs/UI_ENHANCEMENT_SPEC.md` v0.3 (438 lines, read in full), my round-1/round-2 reviews, and the pinned test suites (re-checked for every literal/suite named in §4). Read-only: this review is the only file written.
**Scope note:** a Phase-1 implementation edit is landing concurrently in `base.html`/partials/tests; this verdict is on the v0.3 **specification**, with two implementation-sync notes at the end.

**Result: DESIGN VERDICT: AGREE — 0 blockers, 8 non-blocking notes.** All four round-2 blockers are
resolved in the requirement text: R1 (complete test matrix) and R2 (overlay ladder) fully; R3 (downloads
card mode) in scope — the contract is stated, with implementation-site enumeration left as a note; R4
(ledger/acceptance) fully for PARTIAL descriptions + manual checklist, with one ledger status wording
note. Non-blocking corrections are documentation-level and can travel with the implementation commit.

## 1. Round-2 blocker verification

| Blocker | v0.3 evidence | Status |
|---|---|---|
| **R1 — §4/UX-018 pinned-literal matrix** | §4 now has a `test_release_download_state.py` row with `setTimeout(poll, 1500)`, `updateReleaseStateDom`, `updateAllCardSummaries`, no-reload-in-`downloadRelease` (line 367); the performance row correctly splits **series** `series-row-deferred`/`loadMoreSeriesRows` from **prioritized** `series-card-deferred` (362); `test_releases_loadmore_availability.py` added (366); the bogus `test_empty_states` is removed and replaced by the correct `test_ui_polish.py`/Part 5 owner (366); a second `test_background_tasks.py` row protects `#tasks-toast` + its `aria-live` region and forbids UX-014 from removing/duplicating the task-finished announcement (368); UX-008 now pins the release-state timers (110–112); UX-018/§4 keep `setTimeout(pollTask, 1500)` (base MangaTasks) distinct from the release-state literal. All referenced suites exist (`test_post_date_sorting.py`, `test_series_refresh.py`, `test_releases_loadmore_availability.py`, `test_release_download_state.py`, `test_background_tasks.py`). | **RESOLVED** |
| **R2 — Tasks above dialogs / save bar** | UX-007 now puts Tasks at **z-55**, below every backdrop (60/80) and explicitly **hidden + inert whenever any dialog is open** (`body.mt-modal-open`), with a written rule that it can never sit above a dialog scrim or collide with the settings save bar (45); UX-013 adds `#global-tasks`, `#mobile-bottom-bar` and `#offline-banner` to the `inert` set and disables toast action controls while keeping the live regions announcing; the new suite asserts `body.mt-modal-open #global-tasks`. Implementation-sync note N1 below. | **RESOLVED** |
| **R3 — Downloads card mode + semantics** | UX-022 now states the card-mode/empty-state contract explicitly for Series, **Releases flat, Prioritized flat and Downloads tables** (`.mt-table-empty`, data-label, 44 px actions, thead kept with `scope="col"`), and UX-022's closing sentence extends the same contract to Downloads tables (264); UX-027 covers the qBit value-only refresh safely. Remaining detail (builder enumeration + explicit ARIA label suppression) is implementation guidance, not a missing requirement — see N2. | **RESOLVED (scope) — N2** |
| **R4 — ledger/acceptance verifiability** | §8 PARTIAL rows now describe the limitation: G-15, G-16, G-23, G-27, G-29, G-32, G-36, G-39, G-47, G-50 all have "(with what)"; G-27 is PARTIAL with its migrated-dialog list; G-43 is now **DEFER** with a §7 row; all 50 rows remain present/unique (scripted check) and every High gap has FIX/PARTIAL/DEFER. Acceptance #1 now lists the new-suite assertions (including offline banner, Tasks modal-open rule, release-state guard, qBit value-only refresh) and #4 adds a source-based manual checklist (10-page reachability, fixed-layer overlap at 360 px, dialog focus trap/return, 320 px reflow, reduced motion, 200 % zoom). | **RESOLVED — N3** |

## 2. Editorial items requested by the lead

| Item | v0.3 check | Result |
|---|---|---|
| Phase range / dangling UX-032…037 | Phase 1 = UX-001…UX-031 (+ redundant "plus UX-019"); no undefined IDs remain | Fixed (minor N4) |
| Pointers (§4 vs §7 vs §8) | Non-negotiable 3 now correctly names §4 as the matrix; but five pointers remain off by one section: NN5 line 23 "Deferred … §8" → §7; UX-002 line 39 "document in §7" → §4; UX-013 line 165 "listed in §8" → §7; UX-018 line 209 "unless §7 says otherwise" → §4; UX-030 line 343 "listed in §7" (password semantics row still absent — add a §4 "deliberate behaviour change" row or a §7 row) | **Partially fixed — N4** |
| qBit duplicate row id | UX-027 line 309–310 says "fix duplicate `id="torbox-row-*"` … by using the hash key (keep `data-release-id`)" — correct intent, but the new id format is not stated | Fixed in substance — N5 (state `id="qbit-row-<hash>"`) |
| Reload paths / unsaved guard | UX-030 line 338–340 enumerates three `location.reload()` paths + the reset redirect and suppresses the guard; two labels are imprecise (`:1158` is the log-prune reload, not "save"; add/delete group are not "debug/maintenance") and the new Discard reload is not in the list | **Partially fixed — N6** |
| Badge semantics | UX-004: dot is `aria-hidden="true"`, accessible name carries the count ("Downloads, N manual pickups waiting"), menu badges `aria-hidden` | Fixed |
| Discovery tab pattern | UX-025 has the full ARIA tab pattern (`tablist`/`tab`/`aria-selected`/`aria-controls`/`tabpanel`/`aria-labelledby`/roving tabindex/Arrow/Home/End + `#discovery`/`#blacklist` restore) | Fixed |
| Visual-language constraint restored | Non-negotiable 5 (visual language) reuses existing brand/slate/cyan/amber/emerald tokens, no light theme/new fonts, UX-015 single source of colour | Fixed (numbering N4) |
| qBit blank/omit frontend-only | UX-030 renders `value=""` + "(unchanged)" placeholder and omits blank key; §5 guardrail confirms Phase 1 adds no backend routes/schemas/DB changes; semantics change declared | Fixed |
| Ctrl+K fallback | UX-010 adds the **Alt+K** fallback alongside Ctrl/⌘+K | Fixed |

## 3. Non-blocking notes (apply during implementation / final editorial pass)

* **N1 (implementation sync):** the in-progress `base.html` currently defines `--mt-z-tasks: 95` (the v0.2
  value); v0.3 requires **55** and the `body.mt-modal-open` hide/inert rule — update the implementation to
  match before the new-suite z-scale/modal assertions are written.
* **N2 (R3 detail):** add one sentence naming the five JS row builders that must emit `data-label`
  (`_torboxRowHtml`, `renderInventory`, `renderFailedDocuments`, `renderAudit`,
  `renderStagingDuplicates`) and the card-mode table-semantics rule (native table roles preserved or
  explicit `role="table/row/columnheader/cell"`, `td::before` label pseudo-elements `aria-hidden`), and
  extend the new-suite assertion beyond "series/release partials" to the downloads builders. The
  requirement contract ("same card-mode/empty-state contract applies … Downloads tables") already covers
  the behaviour, hence non-blocking.
* **N3 (ledger wording):** G-45 is still `FIX (mobile cards + 44 px; desktop table layout unchanged)`;
  because sortable headers/column-chooser/row-selection/sticky headers are not delivered, mark it
  **PARTIAL (mobile card mode/44 px only; desktop table interactions unchanged)** to satisfy acceptance
  #4's FIX/PARTIAL definitions. G-34 may keep FIX once N2's implementation is in the same change.
* **N4 (documentation):** fix the five section pointers above, the duplicated "5." numbering
  (Visual language / Implementation scope), the redundant "+ UX-019", and acceptance #5 "reviewing v0.2"
  → v0.3/round 3.
* **N5 (qBit id):** state the exact replacement id (`id="qbit-row-<hash>"`, keep `data-release-id`) and
  note the current duplication (`downloads.html:233` renders `id="torbox-row-<release_id>"`, while qBit
  rows have no `release_id`; `_torrent_status_view` exposes `hash`, `main.py:507–532`).
* **N6 (reload paths):** correct the labels for `settings.html:1158` (log prune) / `:1518`/`:1534`
  (prioritized-group add/delete) and add the new Discard-reload path to the suppression list.
* **N7 (verification):** the manual checklist covers the D/E items well; add "44 px matrix spot-check
  (row actions, close buttons, chips)" and "aria-current present on header/mobile-menu/bottom-bar active
  item" to the source-based checklist, since both are cheap to verify but not in the new-suite list.
* **N8 (minor):** `test_background_tasks.py` now appears twice in §4 (lines 361 and 368); merge the two
  rows for clarity (both requirements are valid).

## 4. Confirmation of D/E-relevant resolutions

* Pinned-literals safety net now covers the suites that the UI work will actually touch; the release-state
  visibility guard is explicitly additive to `setTimeout(poll, 1500)` (UX-008 + §4).
* Dialog contract is complete for the migrated set: role/aria-modal, opener memory, `inert` scope
  (main/header/footer/tasks/bottom bar/offline banner), scroll lock, focus trap/return, topmost-Escape,
  toast-control disabling without killing live regions; deferral of the other modals is explicit (§7 last row).
* Toast contract: escaped text, separate polite/assertive regions, retry callback without breaking the
  two-argument public signature, no duplicate task-finished announcement (`#tasks-toast` protected).
* Contrast/reduced-motion/safe-area/44 px/reflow/aria requirements are quantified and acceptance-linked;
  manual checklist covers what the Jinja-shim tests cannot observe (no browser automation in this repo).
* Overlay ladder, bottom bar/sheet, save bar, toasts, offline banner (z=36) and Tasks now have a single
  numeric owner; palette/sheet/dialog ordering is unambiguous.
* Gap ledger is complete (50/50), High gaps are FIX/PARTIAL/DEFER with reasons, deferred work is listed in
  §7 (including G-43 now), and Phase-1 guardrail (no backend routes/schemas/DB changes) is explicit.

## 5. Final verdict

No blocking disagreements remain. R1 and R2 are fully resolved; R3 is resolved at requirement level with
implementation-site enumeration recorded as N2; R4 is resolved in substance with one ledger wording fix
(N3). All eight remaining notes are documentation/implementation-sync items and do not change the design,
the visual language, or any accepted test contract.

**DESIGN VERDICT: AGREE**
