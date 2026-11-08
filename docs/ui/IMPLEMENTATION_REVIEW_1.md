# Phase-1 UI Implementation Review 1 — expert-ui (read-only QA)

**Reviewer:** EXPERT-5 (aspects D: visual consistency & accessibility, E: workflow continuity & feedback)
**Task:** task-38 · **Date:** 2026-11-08
**Spec:** `docs/UI_ENHANCEMENT_SPEC.md` v0.3 (current working copy; line refs below are to `backend/app/templates/*`)
**Scope of this pass (landed files only):** `base.html`, `index.html`, `series.html`,
`partials/series_table_row_chunk.html`; `downloads.html` / `manual_pickup.html` / `settings.html` are
mid-edit and were **not** reviewed beyond the test outcome. Read-only: no code/spec edits.

**VERDICT FOR LANDED SCOPE: FAIL** — 1 functional blocker, 3 high-severity spec/accessibility gaps, 9
medium findings (details below). Pinned-contract suites that can run here are green; the failures in
`test_ui_enhancements.py` are all page tests for workers still in progress, and three suites are blocked by
a sandbox temp-dir restriction (not by the implementation).

---

## 1. Tests run

Command: `calibre-debug.exe -e tests\run_tests.py <7 suites>` (repo root, read-only).

| Suite | Result | Detail |
|---|---|---|
| `test_ui_polish.py` | **PASS** | 56 live asserts / 94 executions — focus-visible selector, `min-h-[44px]`, `aria-expanded`, empty states, settings nav intact. |
| `test_nav_structure.py` | **PASS** | 75 / 165 — 5 top-level items, Downloads dropdown children, **7 header buttons**, mobile-menu 10 routes, no actions group. |
| `test_template_markup.py` | **PASS** | 11 / 16 — no duplicate attributes in any landed template/partial; downloads single-script/modal-order still holds. |
| `test_ui_enhancements.py` | **FAIL (expected for pending pages)** | 124 live / 114 exec. PASSED: shell/skip link/7 buttons, Downloads dropdown, bottom bar+sheet markup, palette, overlay/dialog helper/z-tokens, toasts/live regions/pinned timers/offline banner/badge names, dashboard quick actions+in-place flow, series server-search+cards. FAILED (pages not yet landed): `test_releases_filters_and_cards`, `test_prioritized_mobile`, `test_discovery_pagination_and_tabs`, `test_feeds_modal`, `test_downloads_navigator_and_refresh` (missing `dl-diagnostics`), `test_manual_pickup_refresh_and_prefill`, `test_settings_nav_dirty_guard_password`, `test_logs_filters`. |
| `test_series_refresh.py` | **BLOCKED (environment)** | 0/8 executed: `PermissionError [WinError 5]` at `tests/e2e/harness.py:27` (`tempfile.mkdtemp` under the sandboxed `%TEMP%`). |
| `test_tracked_series_calibre_link.py` | **BLOCKED (environment)** | 0/25 executed — same harness `mkdtemp` PermissionError. |
| `test_release_download_state.py` | **BLOCKED (environment)** | 0/18 executed — same harness `mkdtemp` PermissionError. |

**Test-coverage gap:** the suites are source-string assertions. They passed while the functional
`More`-tab defect below (Finding H1) is present; there is no DOM/browser automation in this repo
(`tests/e2e/harness.py:164–176` returns placeholder HTML). Behavioural QA must remain manual until a
DOM test exists.

**Environment note:** an attempted workaround created `MangaTrax/.qa_tmp` (restricted ACL, cannot be
listed/deleted under the sandbox; untracked, git only warns about it). Please remove it outside the
sandbox. The three blocked suites should be run by the Lead/CI outside this sandbox.

---

## 2. Findings (landed files)

Severity: **BLOCKER** (breaks a shell workflow), **HIGH** (spec contract/accessibility breach),
**MEDIUM** (partial spec deviation / UX defect), **LOW** (polish).

| # | Sev | File:line | Finding | Spec ref | Concrete fix |
|---|---|---|---|---|---|
| H1 | **BLOCKER** | `base.html:580` + `base.html:724–741` (esp. 739) | **Bottom-bar "More" opens the sheet and it closes instantly.** The inline `onclick` opens it, then the document-level outside-click handler runs for the same click: target is not inside `#mobile-menu` and is not `#btn-mobile-menu`, so line 739 `closeMobileMenu()` closes it again. `data-mt-sheet-opener="more"` is never consulted. Mobile users cannot reach Releases/Prioritized/Feeds/Logs/Settings from the primary bar. | UX-005/006, nav matrix non-negotiable 1 | Add `event.target.closest('[data-mt-sheet-opener]')` to the exclusion in the document click handler (or `event.stopPropagation()` in the More handler), then verify: tap More → sheet stays open, focus moves to sheet, More `aria-expanded=true`. |
| H2 | **HIGH** | `base.html:474` (`#image-modal` `z-50`), `base.html:861–885` | **Image lightbox was not migrated to `MangaModal`.** It keeps its own `class="z-50"` (spec ladder says lightbox = 90), own `document.body.style.overflow`, own global Escape handler and no focus trap/inert. Since Tasks is now z-55, the Tasks widget paints above the lightbox; the menu backdrop (60) and palette (90) also sit above it. | UX-013 (apply to image lightbox), UX-007 ladder | Call `MangaModal.open(modal, opener)` / `.close(modal)` from `openImageModal`/`closeImageModal`, remove the local overflow + Escape code, give `#image-modal` a CSS z-index 90 (same layer as tag/feed modals), and add the shell test assertion that the lightbox uses `MangaModal`/`mt-modal-open`. |
| H3 | **HIGH** | `series.html:250–253` | **Auto-chain still drains every page off-screen.** `setTimeout(fetchNextSeriesRows, 60)` continues unconditionally whenever `has_more`, regardless of viewport position. The new IntersectionObserver is additive and does not gate the chain. Spec §4 (`test_performance...` row) says "the series chain may stay but must stop off-screen", and ledger G-11 (PARTIAL) claims "no unbounded drain off-screen"; neither is true. | UX-021, §4 performance row, G-11 | Gate the `setTimeout` on "load-more container is within ~300 px of the viewport" (observer-maintained flag) or drop the chain and rely on observer + button; keep `series-row-deferred`/`loadMoreSeriesRows` strings. |
| H4 | **HIGH** | `index.html:297–299`, `318–343` | **Dashboard in-place download is incomplete.** The button markup (`index.html:241`) has no `<span>`, so `label = btn`; the first `textContent='Sending…'` destroys the icon forever. On timeout there is **no error toast and no Retry** (spec UX-020 requires both); a hidden tab exits the loop and re-enables the button as "Download" (duplicate-download risk); terminal success re-enables a still-clickable button labelled "In staging"/"In Calibre"; `if (!res.ok) break` fails silently. | UX-020, UX-008 | Wrap the label in `<span>` and target it; track terminal state and replace the button with a state chip (no re-click); on timeout/hide show error toast with Retry; stop on `document.hidden` but resume on visibility (or keep deadline only while visible); announce the final state. |
| H5 | **HIGH** | `base.html:1701`, `1702–1714`, `1715`; `index.html:346–361` | **Pickup badge: duplicated fetches + missing accessible names.** Two `DOMContentLoaded` listeners call `refreshManualPickupBadge` (original registered at 1701, reassigned wrapper registered at 1715) → **2 GETs/page load**; dashboard adds a third (index). Only the Downloads tab's `aria-label` is updated; the desktop nav and mobile-menu Manual Pickup links never get the count in their accessible names, despite UX-004 requiring it. | UX-004 | Register one listener; make the fetch return the count and let the dashboard reuse it (no second fetch); set `aria-label`/`title` on all three links (`Downloads, N manual pickups waiting`, `Manual Pickup, N waiting`); keep badges `aria-hidden`. |
| M1 | **MEDIUM** | `base.html:1303–1317` (`mtPollOk` at 1307) | **Poll health counts success before checking `res.ok`.** Every HTTP response (including 500) resets `pollFailures`; only thrown network errors call `mtPollFail`. The offline banner therefore never appears for a persistent API 5xx, contradicting UX-019's "two consecutive failures". | UX-019 | Move `mtPollOk()` inside `if (res.ok)`; call `mtPollFail()` when `!res.ok` (and keep the catch path). |
| M2 | **MEDIUM** | `base.html:236–239` | **Reduced motion also disables loading spinners.** The `prefers-reduced-motion` block sets `animation:none` on `.animate-spin`; spec says "disables transitions/animations **except loading spinners**". Users lose the only busy indicator in sync/tag/task flows. | UX-016 | Remove `.animate-spin` from the disable list (and `.mt-skeleton` optional); keep the spinner, or slow it; document the exception. |
| M3 | **MEDIUM** | `base.html:180` (`#64748b` palette group), `index.html:50` (`text-slate-500`), `series.html:78`, `base.html:183`; `index.html:9` (`via-slate-850`), `partials/series_table_row_chunk.html:2` (`hover:bg-slate-850/50`) | **UX-015 contrast/token rules not applied.** New micro labels still use `slate-500` (#64748b) on dark cards (spec: replace with slate-400, ≥4.5:1); `slate-850` is still referenced in landed templates although the Tailwind config does not define it (invalid utility, no hover/gradient stop). | UX-015 | Swap micro-label colours to `text-slate-400`/`#94a3b8`; add `slate: {850:'#1b2537'}` to the Tailwind config (or remove the classes); verify no `text-slate-500` remains in changed surfaces. |
| M4 | **MEDIUM** | `base.html:353` (palette trigger `w-8 h-8`), `base.html:371` (hamburger), and existing user-menu trigger | **Header icon controls are 32×32 on mobile**, below the spec's global 44×44 mobile target (UX-017); the palette trigger also lacks the `K` hint required on ≥sm (UX-002) and its `title` mentions only Ctrl+K. | UX-017, UX-002 | Add `min-w-[44px] min-h-[44px]` on mobile for header icon buttons (or a documented exception with a larger hit area); add a `<span class="hidden sm:inline">K</span>` hint and title "Ctrl/⌘+K (Alt+K fallback)". |
| M5 | **MEDIUM** | `base.html:1631` | **No Alt+K fallback.** The palette toggle listens only to Ctrl/⌘+K; v0.3 UX-010 explicitly requires an Alt+K fallback because Ctrl+K is browser-reserved in some browsers. | UX-010 | Add `event.altKey && event.key.toLowerCase()==='k'` to the toggle condition; keep the header button as universal entry. |
| M6 | **MEDIUM** | `base.html:617–652` | **Toast error contract lacks Retry/action name.** Escaping, separate status/alert regions, persistence and dismiss are implemented, but there is no optional `{retry}`/`showErrorToast` and no failed-action naming required by UX-014; every failure is a bare message. | UX-014 | Extend `showToast(message, type, options)` with `{retry, persist}` (keep two-arg compatibility), render a Retry button in `#toast-errors`, and have fetch-failure call sites pass an action label + retry; add a static test. |
| M7 | **MEDIUM** | `base.html:1717–1742` | **Dropdown keyboard pattern incomplete.** Arrow navigation works, but Home/End do not jump, Tab does not close the menu, and Enter/Space on an already-open trigger does nothing (prevented default, no toggle). UX-003 requires Enter/Space open+first item, Arrow cycle, Home/End jump, Escape close+return, Tab close. | UX-003 | Extend the handler: Home/End → first/last item; Tab → `closeAllDropdowns()` + return focus; Enter/Space when `aria-expanded=true` → close; add a test to the new suite. |
| M8 | **MEDIUM** | `base.html:580` | **"More" tab never updates `aria-expanded`.** The hamburger does; the More tab declares `aria-haspopup="dialog" aria-controls="mobile-menu"` but the open/close state is not mirrored, so AT cannot tell whether the sheet is open. | UX-006/UX-017 | In `toggleMobileMenu`, also update `[data-mt-sheet-opener]` `aria-expanded`; restore on close. |
| M9 | **MEDIUM** | `series.html:254–258` | **Normal (non-search) series batch failure has no UX-009 inline retry.** The catch only reveals the old Load More button and logs to console; no "Couldn't load series." + Retry in the table. The search path does have `showSeriesSearchRetry`. | UX-009 | Use the same inline retry pattern in the normal catch (and clear `aria-busy`); keep the pinned Load More strings. |
| M10 | **MEDIUM** | `base.html:1625` | **Palette search-error state is indistinguishable from "no search results".** The catch just re-renders page/action commands; UX-010 requires an error state. | UX-010 | Render an inline "Search unavailable — Retry" row (with `retryPaletteSearch`) and keep commands visible below it. |
| M11 | **LOW** | `base.html:162` | `#mobile-menu:not(.hidden){display:block!important}` defeats `md:hidden`; if the sheet is open and the viewport is widened to ≥768 px it stays visible on desktop. | UX-006 | Scope the rule to `@media (max-width:767px)` (or toggle a class and let `md:hidden` win). |
| M12 | **LOW** | `base.html:141` + `base.html:main` `pb-24` | Double bottom spacing on mobile: `body{padding-bottom:var(--mt-bottom-bar-h)}` **and** `<main … pb-24 md:pb-8>`. | UX-016 | Keep one mechanism (body padding, with `main` only `md:pb-8`). |
| M13 | **LOW** | `base.html:446–448` | Decorative backdrop `#mobile-menu-backdrop` has no `aria-hidden="true"`; the sheet's badge dot still renders the count glyph where v0.3 describes a dot (accessible name is correct). | UX-006/UX-004 | Add `aria-hidden="true"` to backdrop; keep dot glyph empty or accept as minor. |
| M14 | **LOW** | `base.html:447–448` | `#toast-errors` never re-gains `hidden` when its last toast is dismissed (empty fixed container remains in the layer stack, harmless but untidy). | UX-014 | Re-add `hidden` when `children.length===0` after removal. |
| M15 | **LOW** | `base.html:197–234` | Card mode (`mt-table-cards`) hides `thead` by geometry only; no explicit `role`/`aria-hidden` for the generated `data-label` pseudo content, so some AT/browser combinations may read labels twice or lose table relationships. | UX-022 (N2 in design review) | Add `td::before` is already visual; mark it `aria-hidden` impossible for pseudo-elements → instead add explicit `role="table"/"row"/"cell"` on the card-mode table or keep native semantics and verify with AT; document manual check. |

**Positives confirmed in this slice** (not exhaustive): skip link + `<main id="main-content" tabindex="-1">`;
viewport-fit/safe-area/sheet `dvh`; 7 header buttons and the Downloads dropdown (2 children, active states,
manual-pickup path); 5-tab bottom bar with `aria-current`; sheet dialog/backdrop/close + inert background +
focus trap/return via `MangaModal`; palette markup, keyboard listbox, direct `data-mt-action` dispatch and
search bootstraps; escaped toasts with separate status/alert live regions; pinned `setInterval(poll, 1500)`,
no `location.reload` inside MangaTasks; tasks z-55 + `body.mt-modal-open` hide/inert; series server-search
(debounce/Abort/`?search=`/empty+retry states), card-mode `data-label` on all 7 series cells and 44 px
actions; dashboard quick actions + setup/pickup banners; all pinned-literal suites pass.

---

## 3. Must-fix list before QA pass 2 sign-off

1. **H1** More-tab sheet opens-then-closes (functional blocker; add a behaviour test).
2. **H2** migrate `#image-modal` to `MangaModal` + z-90 (a11y/overlay contract).
3. **H3** stop the series chain off-screen (spec/ledger accuracy).
4. **H4** dashboard download label/icon, timeout/retry, hidden-tab and terminal-state handling.
5. **H5** single pickup-count fetch + accessible names on all three badge links.
6. M1–M9 (offline-banner success check, reduced-motion spinner, contrast/slate tokens, 44 px header,
   Alt+K, toast Retry, dropdown keyboard, More aria-expanded, series inline retry) — these can ride in the
   remaining workers' PR or a follow-up, but M1–M6 are required for the D/E acceptance criteria.
7. Pending workers: `downloads.html`, `manual_pickup.html`, `settings.html`, `releases.html`,
   `prioritized.html`, `discovery.html`, `feeds.html`, `logs.html` + flat/card partials — their 8
   `test_ui_enhancements` tests must go green in pass 2; `downloads` navigator anchors
   (`#dl-diagnostics`…) are the first observed gap.
8. Remove `MangaTrax/.qa_tmp` outside the sandbox; run the three temp-dir-blocked suites in CI.

**PASS/FAIL:** **FAIL** for the currently landed slice (H1 is a user-facing blocker; H2–H5 are spec/accessibility
highs). Re-run this checklist after the remaining workers land for pass 2.
