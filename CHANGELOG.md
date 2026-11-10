# Changelog

All notable changes to MangaTrax are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning.

## [Unreleased]

### Changed
- **Documentation consolidated:** final design/registry/consensus/UI documents now live at the top
  of `docs/` with an index; modular specification parts moved to `docs/design/parts/`, review
  evidence to `docs/design/reviews/` and `docs/ui/reviews/`.
- **Historical material archived:** `PROJECT.md`, `FEATURE_RECOMMENDATIONS.md`, `TEST_INFRA.md`,
  `TEST_READY.md` and `TORBOX_INTEGRATION_PLAN.md` moved to `docs/archive/development/`; v0.9–v0.11
  spec drafts to `docs/archive/spec-drafts/`; an immutable v1.0 snapshot is kept in
  `docs/archive/v1.0/`.
- **Removed duplicates:** the duplicated `docs/app_design_spec_v1.0/` tree and
  `docs/ui/UI_ENHANCEMENT_SPEC_v1.0.md` copy (the signed tree is preserved in git tag `v1.1.0`).
- **`.gitignore` hardened** for local test/sandbox artifacts (`.qa_tmp/`, `.tmp-test/`,
  `.dsh-tmp/`, `calibre-*/`, live logs, temp files, OS/editor files); development scratch
  directories and Python caches are cleaned from the working tree.

## [1.1.0] — 2026-11-08 — First UI update

### Added
- **Command palette** (`Ctrl/⌘+K`, `Alt+K` fallback) for all 10 pages, quick actions (Sync Calibre,
  Poll Feeds, Process Downloads, Start Backfill, Manual Pickup) and unified series/release search
  with deep links into Series and Releases.
- **Mobile bottom tab bar** (Overview, Series, Downloads, Discover, More) with 44 px targets,
  safe-area insets and a navigation-only bottom-sheet menu; every page remains reachable.
- **Dashboard quick actions** plus Calibre-setup and held-Manual-Pickup banners.
- **Downloads section navigator** with hash/scroll-spy and a safe, visibility-aware qBittorrent
  refresh that updates existing rows only.
- **Responsive card tables** for Series, Releases (flat), Prioritized, Downloads queue/inventory/
  failed/staging and Feeds; mobile filter sheets and preserved filter/sort/query state (Releases,
  Prioritized, Discovery pagination).
- **Series server-side search** with debounce, cancellation and URL state; off-screen auto-load
  gating.
- **Settings**: unsaved-changes bar with Stay/Discard and leave warning, mobile chip nav and
  collapsible cards, Advanced-anchor hiding when debug actions are off, and write-only qBittorrent
  password (blank = unchanged, no longer echoed into HTML).
- **Manual Pickup**: non-destructive "changed — refresh" flow with discard, stored-series prefill,
  saving/saved indicators and revert.
- **Logs**: event-type and actor filters combined with the existing search.
- **Accessibility**: skip link, shared dialog helper (focus trap, inert background, scroll lock,
  Escape/focus return), status/alert live regions, escaped toasts with optional Retry, 44 px matrix,
  non-colour active-tab cues, reduced-motion support and quantified AA contrast tokens.
- New test suite `tests/test_ui_enhancements.py` covering the shell, dashboard, page contracts and
  preserved pinned literals.

### Changed
- Header now has 7 controls: 4 grouped nav triggers (Library, Discover, Downloads, System), the auth
  user menu, mobile menu and command palette. **Downloads** is a dropdown containing Downloads and
  Manual Pickup (the former 6th flat Manual Pickup link was removed).
- "More" opens the mobile sheet as a dialog; Tasks, dialogs and the settings save bar are mutually
  exclusive so they cannot overlap.
- `tests/test_nav_structure.py` updated intentionally for the new header/nav contract.

### Fixed
- Mobile overlay collisions between toasts, Tasks, sticky Load more and the settings save bar.
- Filters/search/sort being reset when paging or switching views.
- Toast messages rendering feed-derived text as HTML (now text-only) and missing live-region
  announcements.
- Dashboard downloads losing the page/scroll position; the button now updates to the real state.
- Series auto-loading every page even when the user was nowhere near the bottom.
- Manual Pickup auto-refresh destroying unsaved volume edits.
- qBittorrent queue showing stale progress (safe in-place refresh; new rows appear on next full
  refresh).

### Documentation
- `docs/APP_DESIGN_SPEC.md` v1.0 — complete application design specification with
  `docs/FEATURE_REGISTRY.md` (457 unanimously agreed claims) and archived copy under
  `docs/archive/v1.0/`.
- `docs/UI_ENHANCEMENT_SPEC.md` v1.0 and `docs/ui/DESIGN_VALIDATION_LOG.md` — 5/5 expert design
  sign-off after three validation rounds, plus QA passes 1–3.
- `docs/UI_ENHANCEMENT_SUMMARY.md` — user-facing summary of this release.

### Verification
- Script suites: 86/86 pass (4,051 live assertions).
- E2E: 77/77 pass.
- No backend routes, schemas or database columns changed.

## [1.0.0]
- Initial tracked release baseline (release monitoring, Calibre integration, qBittorrent/Torbox
  pipeline, staging/tagging, settings/auth/ops).
