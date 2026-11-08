# Phase-1 UI Implementation Review 3 — final QA check (expert-ui)

**Reviewer:** EXPERT-5 · **Task:** final QA check requested by Lead (no task id supplied; no pending
`task-*` exists in the shared board — work done read-only). **Spec:** `docs/UI_ENHANCEMENT_SPEC.md` v0.3.
**Tree:** final Phase-1 implementation (all 10 pages + partials + new UI suite). Only this file written.

## Confirmation table (Lead's acceptance-level fixes)

| # | Fix claimed | Verified evidence | Status |
|---|---|---|---|
| M1 | Non-OK poll responses call `mtPollFail`; 401/403 ignored | `base.html:1320–1331`: success (`mtPollOk`) only inside `res.ok`; `else if (mtPollFail) mtPollFail(res.status)` (`:1327–1329`); network catch calls it too (`:1331`); `mtPollFail` returns early for 401/403 (`:1734`). | **FIXED** |
| M3 | `slate-500`→`slate-400` across templates; palette group/kbd tokens raised | Palette group/kbd/empty/retry/section-nav/offline colours now `#94a3b8` (`base.html:182,185,186,214,235`); `text-slate-500` count dropped **148 → 12** template-wide; the UI-015 declared colour pairs now resolve to the AA values. Residual: 12 peripheral metadata/icon uses remain in card/row partials (see §3). | **FIXED (token level)** |
| M6 | `showToast` options `{action,retry}`, error prefix + Retry | `base.html:625` signature; error text prefixed with `"<action> failed: "` (`:640`); Retry button appended when `options.retry` is a function (`:653–659`); escaping/live regions/persistence still intact. | **FIXED** |
| M9 | Series normal-batch catch renders inline "Couldn't load series. Retry" | `series.html:257–268`: catch keeps the Load More fallback, appends `#series-load-retry` row with the exact copy and a Retry button that re-runs `fetchNextSeriesRows`; excludes duplicate rows; `finally` resets `isLoadingSeriesRows`. | **FIXED** |
| M10 | Palette error state with Retry | `base.html:1649`: a dedicated "Search unavailable." row (with retry semantics) is rendered on fetch failure instead of silently showing only commands; the aborted-request path is still ignored. | **FIXED** |
| N2 | 44 px min-heights for mobile selects/inputs/palette rows/`.mt-btn` | `base.html:219` `.mt-btn` mobile 44; `:239` `main select, main input[type=text/number], .mt-palette-item, .mt-btn { min-height: 44px; }`; `:237` section-nav chips mobile 44; table card actions already 44 (`:211`); quick actions 44 (`:232`); bottom bar 44 (`:145`). Releases/prioritized filter pills and discovery controls are covered by the `main a`/`main select` rule set (spot-checked classes). | **FIXED** |
| N3 | Migrated page modals pinned to z-90 | `base.html:241–242`: `#different-series-modal, #manual-match-modal, #modal-add-feed, #modal-link, #pickup-match-modal, #series-calibre-match-modal, #staging-series-match-modal, #tag-modal { z-index: 90; }` (plus image modal/palette 90 at `:170–171`). | **FIXED** |
| N1 | `data-task-kind="downloads:process"` kept | Still present at `downloads.html:67`. Lead's rationale accepted: no backend task of that kind exists, so the binding is inert/decorative and a suite pins the attribute; recorded here as a known decorative binding (APP_DESIGN_SPEC UI-137 / UX-027 wording should note the exception in a follow-up doc edit). | **ACCEPTED (documented)** |
| N4 | Source-only test limitation | No browser automation exists (`tests/e2e/harness.py:164–176` returns placeholder HTML); the manual acceptance checklist in UI spec v0.3 §5 remains the behavioural gate. | **STANDS (documented)** |

## Tests (final check)

| Suite | Result |
|---|---|
| `test_ui_enhancements.py` | **PASS** — 124 live asserts / 161 executions; all 16 subtests including the new toast/poll/44 px/dialog assertions. |
| `test_ui_polish.py` | **PASS** — 56 / 94 (focus ring, 44 px sample, aria, empty states, settings nav). |
| `test_template_markup.py` | **PASS** — 11 / 16 (no duplicate attributes; downloads single-script/modal order). |
| `test_series_refresh.py` | **BLOCKED (environment, unchanged)** — 0/8 executions: `PermissionError [WinError 5]` at `tests/e2e/harness.py:27` (`tempfile.mkdtemp` under the sandbox). Same limitation as QA passes 1–2; not a code failure. Lead's full-suite/E2E run outside the sandbox is the authoritative gate. |

## Residual notes (non-blocking, no action required for Phase 1 sign-off)

1. Twelve `text-slate-500` uses remain in changed partials (e.g. `partials/flat_release_row_chunk.html:41,
   57, 67`, `partials/series_card_chunk.html:126, 145, 155, 239`, `partials/series_table_row_chunk.html:17,
   47`): all peripheral metadata/icons at 10–11 px, not the new chrome; UX-015's declared token pairs are
   fixed. Optional follow-up: swap to `text-slate-400` in the v1.0 polish pass.
2. `test_series_refresh` and the full 86-suite + E2E runs need CI/outside-sandbox execution; their
   in-sandbox failure mode is the harness temp-dir restriction only.
3. Housekeeping (unchanged): `.qa_tmp`, my stray `calibre-912y8hv0`, and other workers' `.tmp-test/` dirs
   remain untracked with restricted ACLs; delete outside the sandbox.

## Verdict

All acceptance-level fixes the Lead listed are verified in the final tree; the only 0-execution suite is
environment-blocked (same as the two prior QA passes), and the requested UI/source suites are green.
No functional, accessibility-contract, or feedback blocker remains for Phase-1 UI.

**FINAL QA VERDICT: PASS**
