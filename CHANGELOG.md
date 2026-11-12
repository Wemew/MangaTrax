# Changelog

All notable changes to MangaTrax are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning.

## [Unreleased]

### Added
- **Per-series management panel** (Series → Manage): view and edit the cosmetic display title,
  pin/clear an exact AniList entry (`anilist_id` + `anilist_locked`), list the series' releases,
  split a registered synonym into its own tracked series (mapping retargeted, optional re-key of
  matching releases) and retarget a synonym to another tracked/Calibre series.
  New routes: `GET /api/series/{id}/management`, `POST /api/series/{id}/identity`,
  `POST /api/series/{id}/anilist-map`, `POST /api/series/{id}/synonyms/spin-out`,
  `POST /api/series/{id}/aliases/{alias_id}/retarget`.
- **Exact AniList pinning:** `parse_anilist_id` accepts an id or an AniList URL, an exact
  `Media(id:)` lookup replaces fuzzy title search for pinned series, and a pinned series always
  resolves through its stored id — automatic enrichment can never overwrite a manual mapping.
  Clearing a mapping also drops its cached metadata row and locks the series.
- **Remember a re-bind mapping:** the per-release “Different series” modal gained
  “Remember this mapping for future releases” (opt-in alias from the old title to the chosen
  series; off by default, so a one-off move stays one-off).
- **Searchable manual series matches** in Settings (250 ms debounce, explicit no-match state).

### Changed
- **Series pickers are debounced and stale-cancelling:** the release re-bind picker waits 500 ms
  and aborts the previous request (the Calibre match picker 250 ms); an identical already-rendered
  query is not refetched.
- **Calibre series snapshot is memoised for 10 s** (single-flight) and invalidated after any
  series-identity mutation, instead of rescanning the whole library per keystroke.
- **Attribution audit is budgeted:** a volume/format-only title suffix skips the parser, identity
  resolution is memoised per parsed spelling, and the audit stops at an 8 s budget, returning
  `truncated` / `elapsed_ms` so the UI can show partial results instead of timing out.
- **Selective qBittorrent filtering uses the persisted wanted set** and records one signature per
  resolved torrent: the 30 s pass retries only unloaded metadata and never re-calls file priorities
  or re-logs the same decision.
- **Themed thin scrollbars** (`custom-scrollbar`) on long internal panes.

### Changed
- **Visual identity — “Sumi Ink & Hanko”:** neutral Sumi Carbon surfaces replace the blue-slate
  palette, Space Grotesk / Plus Jakarta Sans / JetBrains Mono are loaded for display, UI and
  technical data, and a CSS screentone layer adds print texture without image assets.
- **Brand mark:** custom tankōbon-spine SVG logo and inline data-URI favicon replace the placeholder
  Lucide tile; the header badge now shows the release version instead of “Bundles & qBit”.
- **Card de-noising:** Discovery top badges are neutral except volume-1/complete and the prioritized
  VIP star; the MangaUpdates synopsis is a quiet left-rule catalog entry rather than a framed
  box-in-box; the MangaUpdates link is a neutral metadata chip.
- **Tankōbon volume badges** on Release cards: monospace labels with sharp left spine borders —
  muted emerald for owned volumes, high-contrast amber for wanted volumes.
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
