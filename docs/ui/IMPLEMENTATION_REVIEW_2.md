# Phase-1 UI Implementation Review 2 (final QA) — expert-ui

**Reviewer:** EXPERT-5 (aspects D: visual consistency & accessibility, E: workflow continuity & feedback)
**Task:** task-39 · **Date:** 2026-11-08
**Spec:** `docs/UI_ENHANCEMENT_SPEC.md` v0.3 · **Tree:** all 10 page templates + partials + new
`tests/test_ui_enhancements.py` landed (15 modified files, 2,277 insertions). Read-only: only this file written.

**FINAL VERDICT: FAIL (conditional sign-off).** All five pass-1 blocker/high findings **H1–H5 are FIXED**
and no functional blocker remains; 9 of 15 medium findings are fixed. Six acceptance-level spec deltas
remain open (all small; 2 medium accessibility/feedback, plus manual-checklist items) and the full-suite
run cannot be certified in this sandbox. **No user-facing workflow is broken** — I expect PASS after a
short follow-up patch. Blocker count: **0 functional; 6 acceptance-level open.**

---

## 1. Pass-1 blocker verification (H1–H5)

| # | Finding | Status | Evidence (current tree) |
|---|---|---|---|
| H1 | More tab opened then instantly closed | **FIXED** | `base.html:743` now exempts `[data-mt-sheet-opener]` from the outside-click close; `base.html:702/716` mirror `aria-expanded` on the More opener; More anchor still calls `toggleMobileMenu(this)` (`:583`). |
| H2 | Image lightbox not migrated / z-50 | **FIXED** | `#image-modal { z-index: 90 }` (`base.html:170`); `openImageModal`/`closeImageModal` now use `MangaModal.open/close` (`:866–884`); `MangaModal.open` sets `role="dialog"` + `aria-modal` when absent (`:1408–1409`), so the lightbox gets dialog semantics + focus trap/inert/scroll-lock. |
| H3 | Series auto-chain drains off-screen | **FIXED** | `series.html:118` `seriesRowsNearViewport`, observer sets it (`:165–169`), chain gated at `:255` (`if (seriesRowsNearViewport) setTimeout(...)`); search mode still disconnects. |
| H4 | Dashboard in-place download gaps | **FIXED** | Label is a real `<span>` (`index.html:241–244`, targeted at `:297`); terminal states replace the button with a chip (`:329–337`); timeout/HTTP error produce a Retry-label + error toast (`:339–361`); hidden tab pauses fetches but keeps the deadline (`:321–323`); dashboard reuses `window.refreshManualPickupBadge()` instead of a second fetch (`:363–364`). |
| H5 | Pickup badge double fetch / missing names | **FIXED** | One function, one `DOMContentLoaded` (`base.html:1670–1712`); returns the count; sets `aria-label` on the Downloads tab, desktop menu link and mobile-menu link, badges `aria-hidden` (`:1679–1692`); dashboard reuses it (`index.html:363–364`). |

## 2. Medium findings from pass 1 — re-checked

| # | Status | Evidence / residual |
|---|---|---|
| M1 poll health on non-OK responses | **OPEN (partial)** | `base.html:1303–1316`: `mtPollOk()` now runs only inside `if (res.ok)` (good), but a non-OK HTTP response does **not** call `mtPollFail` (no `else`), so a persistent 500 never raises the offline banner (UX-019 "two consecutive failures"). `mtPollFail` now accepts/ignores 401/403 (`:1702`) — good. Fix: add `else if (window.mtPollFail) mtPollFail(res.status);` |
| M2 reduced-motion keeps spinners | **FIXED** | `base.html:236–239`: `.animate-spin { animation: spin .9s linear infinite !important; }` while other animations/transitions are disabled. |
| M3 contrast tokens | **OPEN (partial)** | `slate-850` is now defined (`base.html:28`), so those hover/gradient classes work. But UX-015's "replace slate-500 with slate-400 on dark cards" is not applied: new chrome still uses `#64748b` (`.mt-palette-group`/`.mt-kbd`, `base.html:182/185`) and 148 `text-slate-500` occurrences remain across templates (e.g. `index.html:50`). AA ratio for #64748b on #161e2e is ≈3.5:1 → fails the spec's 4.5:1 micro-label rule. |
| M4 44 px header controls | **FIXED** | Palette trigger and hamburger are `w-11 h-11 md:w-8 md:h-8` (44 px mobile) — `base.html:356/374`; user trigger is desktop-only (`hidden md:block`). |
| M5 Alt+K fallback | **FIXED** | `base.html:1630` toggles on `(ctrlKey||metaKey)&&K` **or** `altKey&&K`. |
| M6 toast Retry / action name | **OPEN** | `showToast(message, type)` still has no options/retry parameter and no failed-action label (`base.html:620–654`); UX-014's `{retry, persist}` / `showErrorToast` and "error includes failed action name" are unimplemented. Escaping, live regions, persistence and dismiss are correct. |
| M7 dropdown keyboard | **FIXED (minor residual)** | `base.html:1719–1744` adds Home/End and Tab-close; Enter/Space opens only when closed (pressing Enter on an open trigger is a no-op — acceptable, spec requires open not toggle). |
| M8 More `aria-expanded` | **FIXED** | `base.html:702/716`. |
| M9 series non-search inline retry | **OPEN** | `series.html:257–261`: normal batch catch still only `console.error` + reveals the old Load More button; no UX-009 "Couldn't load series." inline Retry (the search path has one). |
| M10 palette search error state | **OPEN (low)** | `base.html:1617–1625`: on HTTP/network failure it re-renders the command list with no error row/Retry; the user cannot distinguish "no results" from "search unavailable". |
| M11 media-scoped sheet | **FIXED** | `base.html:163` wraps the `!important` rule in `@media (max-width:767px)`. |
| M12 single bottom spacing | **FIXED** | `base.html:142` body padding only; `<main>` no longer carries `pb-24` (`:529`). |
| M13 backdrop `aria-hidden` | **FIXED** | `base.html:385`. |
| M14 toast-error cleanup | **FIXED** | `base.html:645` re-adds `hidden` when the error container empties. |
| M15 card-mode table AT semantics | **OPEN (low)** | `base.html:197–230` still converts via `display:block` without explicit `role=table/row/cell` or `aria-hidden` on `td::before` labels; needs the manual AT check from the acceptance checklist. |

## 3. New pages — spec compliance spot-check (all landed)

| Page | Verified (source + `test_ui_enhancements`) | Notes |
|---|---|---|
| `releases.html` | View switch/status pills preserve `sort` **and** `?q=` (`:34–73`); mobile scrollable pill row + Filters `aria-expanded` (`:12–14, 85, 493–498`); flat table `mt-table-cards` (`:150`) with data-label cells in `partials/flat_release_row_chunk.html:3–173`; `?q=` bootstrap (`:202, 500–514`); pinned load-more strings/counters kept (`:136, 190–193, 232–253`). | OK |
| `prioritized.html` | Same filter/order preservation; `mt-table-cards` flat view (`:182`); notes field visible on mobile (`:93`); managed group form; PIN copy kept. | OK |
| `discovery.html` | Pagination/filters preserved via `tag_query_suffix` (`:7, 258–287`); full ARIA tab pattern + hash restore (`:35–49, 309, 432–443`); tag picker combobox keyboard (`:75, 738–760`) and `aria-expanded`; mobile Filters toggle (`:66, 448–451`); sort change carries pending tag state (`:627–638`). | OK |
| `feeds.html` | Table → cards with data-label (`:37–117`); add-feed dialog uses `window.mtModal.open/close` with backdrop/Close/Escape (`:124–208`); empty-state CTA keeps test pin. | OK (dialog keeps `z-50` class, see N3) |
| `downloads.html` | 8 anchored navigator chips incl. `#dl-inventory` (`:77–85`, anchor at `:373`); sticky scroll-spy `aria-current`; diagnostics mobile toggle; qBit 10 s visibility-gated refresh that updates only `data-torrent-hash` rows and never inserts Torbox fallback rows (`:2155–2199`); JS-rendered tables carry `data-label` (`:922–927, 1007–1014, 1293–1299, 1508–1513, 1762–1769`); card/empty-state CSS; tag modal via `MangaModal` with unsaved-close warning (`:1428–1430, 2269`). | OK — except fake task binding N1 |
| `manual_pickup.html` | 15 s visible-only auto-refresh with dirty/focus/modal/task guard (`:407–415`); stored-series prefill (`data-series-name` `:36`, `pickupEditSeries` `:251–256`); per-input saving/saved indicator + revert; pinned strings/task-kinds kept. | OK |
| `settings.html` | Dirty lifecycle + `beforeunload` + Stay/Discard (`:1594–1686`); `body.mt-save-open` mutual exclusion with Tasks (`:1608, 1623`); save bar z-45 above bottom bar (`:8–18`); all four intentional reloads suppress the guard (`:1205, 1536, 1567, 1584`); blank-omitted qBit password (`:145, 1121`) with explicit semantics change; Advanced anchor hidden when debug off while source keeps the 9-anchor test contract (`:9–10, 73`); collapsing cards + anchor expands target (`:1689–1798`). | OK |
| `logs.html` | Event-type + actor filters AND-combined with text search, Clear, filtered empty state (`:28–48, 100–185`); mobile rows stack without truncating timestamps (`:59–77`). | OK |
| Partials | `series_table_row_chunk.html` 7×`data-label`, labelled icon buttons, 44 px mobile, pinned text/handlers; `flat_release_row_chunk.html` 6×`data-label` incl. `mt-cell-actions`; `series_card_chunk` keeps pinned classes/strings; no duplicate attributes (test passes). | OK |

## 4. New open findings from pass 2 (none functional blocker)

| # | Sev | File:line | Finding | Concrete fix |
|---|---|---|---|---|
| N1 | MEDIUM | `downloads.html:67` | The header "Process Downloads" button still declares `data-task-kind="downloads:process"`, but `/api/staging/tag` is foreground and no task of that kind is ever emitted (APP_DESIGN_SPEC UI-137; spec UX-027 says "no fake task binding"). Other task-kinds (`downloads:prune`, `:flatten`, `audit:comicinfo`, `repair:comicinfo`, `downloads:staging-purge`, `downloads:archive-convert`) are real. | Remove `data-task-kind="downloads:process"` (keep the button + id/onclick); or implement the task server-side — not in Phase 1. |
| N2 | MEDIUM | `releases.html:49–73`, `prioritized.html` pill row, `discovery.html:76/80`, `base.html:183` | UX-017 "every interactive control on mobile ≥44×44" is not met for several new controls: status/view filter pills (`px-2.5 py-1` ≈26 px), discovery sort/tag-mode selects (`py-2` ≈32 px), palette option rows (`padding .6rem` ≈34 px) — `min-h-[44px]` appears 0× in releases/prioritized/discovery/feeds (downloads/manual pickup use own 44 px classes). | Add mobile `min-h-[44px]`/padding to pill/select/palette-option styles (or a shared `.mt-tap` utility), then spot-check row by row in the manual 44 px matrix. |
| N3 | LOW | `feeds.html:124`, `downloads.html:2269/2330` | Migrated page dialogs keep `class="... z-50"`; the ladder says modal/palette layer 90. Practical stacking is safe today (body.mt-modal-open hides Tasks and backdrops don't co-open), but the numeric contract is inconsistent. | Add scoped CSS `#modal-add-feed, #tag-modal, #manual-match-modal, #staging-series-match-modal { z-index: 90; }` (or move to a `mt-dialog` class). |
| N4 | LOW | test suite gap | `tests/test_ui_enhancements.py` is source-string only (the repo has no browser automation); it passed while pass-1 H1 (More-tab instant close) was live. The More/overlay/dialog behaviours rely on manual checks. | Add (or keep permanently in the acceptance checklist) a short DOM/manual script: More opens+stays open+focused, lightbox traps focus, palette Escape returns focus, undo of modal inert, mobile tab bar/layer non-overlap at 360 px. |
| N5 | note | full-suite gate | The full `tests/run_tests.py` run cannot be certified in this sandbox: 86 suites, 7 pass, 79 fail — every sampled failure is the harness's `tempfile.mkdtemp` `PermissionError [WinError 5]` at `tests/e2e/harness.py:27` (275 occurrences; suites requiring E2E/temp DBs fail before assertions). Runnable source suites all pass (see §5). | Run the full suite in CI/outside the sandbox (and the E2E runner) before merge; then close this gate. |

Housekeeping: the sandbox-blocked test attempts left `MangaTrax/.qa_tmp` and `MangaTrax/calibre-912y8hv0`
(untracked, restricted ACL, cannot be removed from inside the sandbox); `.tmp-test/` dirs likewise belong
to other workers. Please delete all four outside the sandbox.

## 5. Tests run

| Suite | Result |
|---|---|
| `test_ui_enhancements.py` | **PASS** — 124 live asserts / 161 executions; all 16 feature subtests pass (shell, Downloads dropdown, mobile nav, palette, overlay/dialog, feedback/polling, dashboard in-place, series search/cards, releases filters/cards, prioritized mobile, discovery tabs/pagination, feeds modal, downloads navigator/qBit, manual pickup refresh/prefill, settings guard/password, logs filters). |
| `test_ui_polish.py` | PASS — 56 / 94 (focus ring, 44 px sample, aria-expanded, empty states, settings nav/save-bar intact). |
| `test_nav_structure.py` | PASS — 75 / 165 (5 top-level items, Downloads dropdown 2 children, **7 header buttons**, mobile menu, no actions). |
| `test_template_markup.py` | PASS — 11 / 16 (no duplicate attributes, download-button chrome, one downloads `<script>`, modals after script). |
| `test_releases_loadmore_availability.py` | PASS — 38 / 40 (disabled-from-paint, sticky control, updateLoadMoreUi counts, initial_total interpolation). |
| `test_part_volume_parsing.py` / `test_typing_imports.py` | PASS — 12/28, 3/3. |
| `tests/run_tests.py` (all 86) | **INCONCLUSIVE — environment**: 7 pass / 79 fail; sampled failures are the sandbox `mkdtemp` PermissionError (no UI-source failure demonstrated). Re-run in CI. E2E suite also blocked by the same harness restriction. |

## 6. Verdict

- **H1–H5:** all **FIXED**; no functional blocker remains.
- **M-findings:** 9/15 fixed, 1 partial (M3), 5 open (M1, M6, M9, M10, M15) — all medium/low.
- **New pages:** all 10 pages/partials implement their v0.3 page requirements; two new medium gaps (N1 fake task-kind, N2 44 px matrix) and two lows (N3 dialog z, N4 test coverage).

**FINAL VERDICT: FAIL (conditional sign-off)** — Phase 1 is functionally sound and the shell/workflow
redesign works, but v0.3 acceptance is not fully met: UX-014 (toast Retry/action name), UX-009 (series
inline retry), UX-015 (slate-500 contrast), UX-027 ("no fake task binding") and UX-017 (44 px matrix)
remain open, and the full-suite gate must run in CI. These are small, localized edits; on a follow-up
patch plus a green CI full-suite run I will sign **PASS**.
