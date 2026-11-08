# MangaTrax UI Enhancement — Phase 1 Summary

**Status:** complete and verified. **Design:** `UI_ENHANCEMENT_SPEC.md` v1.0 (5/5 expert sign-off).
**Process:** `docs/ui/DESIGN_VALIDATION_LOG.md` (3 design rounds, 3 QA passes).
**Tests:** full script suite **86/86 pass** (4,051 live assertions); E2E **77/77 pass**; new
`tests/test_ui_enhancements.py` 16 subtests / 124 assertions.

## What changed for the user

### Both desktop and mobile
* **Command palette** (`Ctrl/⌘+K`, Alt+K fallback) for all 10 pages, quick actions (Sync Calibre,
  Poll Feeds, Process Downloads, Start Backfill, Manual Pickup) and series/release search with
  deep links.
* **One-page-per-glance navigation:** header keeps 5 grouped items; Downloads now owns Manual
  Pickup; the mobile bar adds the five primary destinations plus a "More" sheet containing every
  page and sign-out.
* **Dashboard quick actions** and attention banners (Calibre not configured, held Manual Pickups).
* **In-place feedback:** dashboard download updates to its real state instead of reloading; lists
  show loading/empty/error+Retry states; an offline/degraded banner appears when connectivity fails.

### Mobile-specific
* Fixed bottom tab bar with 44 px targets and safe-area insets; bottom-sheet menu with focus trap.
* Data tables convert to stacked cards (Series, Releases flat view, Prioritized, Downloads queue/
  inventory/failed/staging, Feeds) with labels and 44 px actions.
* Filters collapse behind a "Filters" control (Releases/Prioritized/Discovery); Discovery's tag
  filters/sort now survive pagination; full ARIA tab pattern.
* Settings gets a mobile chip nav, collapsible cards, an unsaved-changes bar with Stay/Discard and a
  leave warning; the qBittorrent password is no longer rendered into the page and is left unchanged
  when blank.
* Downloads gets an eight-section navigator (hash/scroll-spy) and a safe qBittorrent auto-refresh
  (value-only, hash-keyed, never mutates controls/rows). Manual Pickup refreshes non-destructively,
  prefills the stored series, and shows saving/saved/revert per volume row.
* Logs gain event-type/actor filters combined with search.

### Accessibility & robustness
* Skip link; dialog helper with `role=dialog`, `aria-modal`, background `inert`, focus trap, scroll
  lock, Escape ordering and focus return; live regions (`status`/`alert`); escaped toast text with
  optional Retry; non-colour active-tab cue; pickup counts in accessible names; 44 px matrix;
  reduced-motion support; quantified AA contrast tokens and a defined `slate-850`.
* Visibility-aware polling, no Tasks/dialog/save-bar overlap, no CDN or dependency changes.

## Files changed (templates only)

`base.html`, `index.html`, `series.html`, `releases.html`, `prioritized.html`, `discovery.html`,
`feeds.html`, `downloads.html`, `manual_pickup.html`, `logs.html`, `settings.html`,
`partials/series_table_row_chunk.html`, `partials/flat_release_row_chunk.html`,
`partials/series_card_chunk.html`; `tests/test_nav_structure.py` updated intentionally;
`tests/test_ui_enhancements.py` added. No backend route/schema/DB changes.

## Deferred (documented in spec §7)

Error pages (G-08), feed editing (G-26), unified confirm idiom (G-31), log history (G-38),
per-section settings save (G-39), maintenance regroup (G-42), result-surface unification (G-43),
field-level validation (G-44), undo (G-46), full modal URL state (G-47), offline vendoring (G-49),
full onboarding wizard, untouched modal migrations.

## Notes

* The sandbox required a local-only tempfile shim to run suites (`scratch/run_tests_sandboxfix.py`);
  no production/test source was changed for it. `.qa_tmp/` and `.tmp-test/` are restricted-ACL test
  artifacts that the confined session cannot remove; delete them from a normal shell.
* Everything is uncommitted on the working tree (changes are template/test/docs only).
