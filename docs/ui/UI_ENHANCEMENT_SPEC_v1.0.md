# MangaTrax UI Enhancement Specification — v1.0 (FINAL, approved)

**Status:** APPROVED — 5/5 expert design sign-off after three validation rounds (see
`docs/ui/DESIGN_VALIDATION_LOG.md` and `docs/ui/reviews/`). Implemented in Phase 1 and verified by
QA pass 3 + the full test suite (86/86 suites) and E2E (77/77).
**Inputs:** final application spec (`docs/APP_DESIGN_SPEC.md` v1.0), Part 5 usability audit
(G-01…G-50), test-contract catalogue, and the round-1 UI reviews.
**Goal:** make every user-facing feature easy to reach and use on phones and desktops, in one shared
visual language, without new dependencies and without breaking the application's tested behaviour.

**Non-negotiables (amended per review)**
1. **Reachability:** every one of the **10 HTML pages** is ≤2 interactions from any page on both
   device classes. Every **user action** is ≤2 interactions or explicitly page-local (documented in
   the page matrix). The 111 JSON routes are not navigation targets; the 14 non-UI routes are listed
   in §6 with their disposition.
2. **No new runtime dependencies or build step.** Tailwind Play CDN + Lucide CDN only. New styles are
   **plain CSS** in `base.html` `<style>` (the Play CDN does not compile `@apply`).
3. **Pinned test literals are preserved unless explicitly listed in §4.** UX-018 is the catalogue and
   §4 is the authoritative change matrix; every change must keep the 85 script suites green.
4. **Accessibility:** WCAG 2.1 AA for changed surfaces, with the quantified token/contrast table in
   UX-015, ≥44 px mobile targets, keyboard operability, dialog focus management and live regions.
5. **Visual language:** no light theme, no new accent colours, no new fonts. New chrome (bottom bar,
   pickup dot, palette, chips, cards) reuses the existing brand/rose + slate/cyan/amber/emerald tokens
   from `base.html`'s Tailwind config; UX-015 remains the single source of colour.
6. **Implementation scope:** Phase 1 (this change) = UX-001…UX-031 plus UX-019. Deferred items are listed in §8
   with reasons and must not be silently dropped.

---

## 1. Global requirements

### UX-001 — Skip link and main landmark (ACCEPTED round 1; unchanged)
Visually hidden `Skip to main content` link as first body element → `<main id="main-content"
tabindex="-1">`; visible on keyboard focus.

### UX-002 — Command-palette trigger in the header
One extra header control: icon-only on <sm, icon + `K` hint on ≥sm; `aria-label="Open command
palette"`, `title`, `aria-haspopup="dialog"`, `aria-expanded="false"`, `id="btn-command-palette"`.
**Header `<button>` count after this and UX-003 = 7** (Library, Discover, Downloads, System, user,
hamburger, palette). The source-level nav test counts the auth-conditional user button; document that
in §7. Initial focus of the palette goes to its search input.

### UX-003 — Downloads becomes a dropdown; Manual Pickup promoted (not a 6th flat link)
Desktop nav remains exactly five `data-nav-item` items in order: Overview, Library, Discover,
Downloads, System. Overview stays an `<a>`; Library/Discover/Downloads/System are
`<button data-dropdown-trigger>` inside `[data-dropdown]` wrappers with `aria-haspopup="true"`,
`aria-expanded`, `aria-controls="nav-menu-<name>"`, `role="menu"` + `role="menuitem"` children.
Downloads menu (in order): **Downloads** (`/downloads`), **Manual Pickup**
(`/downloads/manual-pickup`, with `#nav-manual-pickup-count` badge). Parent trigger highlighted when
`active_page == 'downloads'` **or** `request.url.path.startswith('/downloads/manual-pickup')`; each
child highlights on its own exact state (Manual Pickup uses the path check). The old flat Manual
Pickup anchor in the desktop nav is removed. Dropdowns gain the ARIA menu keyboard pattern without
changing roles: Enter/Space opens and focuses the first item, ArrowDown/ArrowUp cycle, Home/End jump,
Escape closes and returns focus to the trigger, Tab closes.

### UX-004 — Manual Pickup badge (live, not dead)
Badges (`#nav-manual-pickup-count` desktop menu item, `#mobile-manual-pickup-count` mobile-menu row,
`#tab-manual-pickup-count` mobile Downloads tab dot): count of active pickups from
`GET /api/torbox/manual-pickups` (`active_count`/`pickups` length). Render only when >0, hide on
failure. The dot is `aria-hidden="true"`; the Downloads tab's accessible name carries the state
(`aria-label="Downloads, N manual pickups waiting"`) and the menu badges are `aria-hidden` with the
same information exposed on their link. A count of 0 exposes no badge. Refresh once on load, on `visibilitychange`→visible, and after pickup actions
(`MangaTasks` finished / local action). No polling loop.

### UX-005 — Mobile bottom tab bar
Fixed bottom bar `md:hidden`, 5 equal tabs, ≥44 px, safe-area bottom inset:
**Overview `/`**, **Series `/series`**, **Downloads `/downloads`**, **Discover `/discovery`**,
**More** (opens the sheet). Active tab uses `aria-current="page"`, the brand accent **and a
non-colour cue** (700 font weight + the 2 px top indicator); Downloads
stays active for `/downloads/manual-pickup`; Series/Discover stay active for their query variants.
`main` gets bottom padding on mobile so content is never covered. The bar sits below content but
above page backgrounds (z-scale UX-007).

### UX-006 — Mobile menu becomes a navigation-only bottom sheet
Reuse `#mobile-menu` as a bottom sheet: `position:fixed; inset-inline:0; bottom:0;
max-height:80dvh; overflow:auto;` plus backdrop `#mobile-menu-backdrop`; header row with title and an
icon-only close button (`aria-label`+`title`); `role="dialog" aria-modal="true"`
`aria-labelledby="mobile-menu-title"`; body scroll lock; focus trap; Escape closes; focus returns to
the opener (hamburger **or** More tab; the opener is stored when opened). It contains the same 10
destination links in the same three groups and the auth block, each ≥44 px (`min-h-[44px]`), and
**no quick actions / no search / no "Poll Feeds" text** (navigation-only, resolving the test
collision). Ids/anchors and the `_mobile_menu` source markers stay valid.

### UX-007 — Overlay ladder (numeric, unambiguous)
`base.html` defines `--mt-header-h:4rem; --mt-bottom-bar-h:calc(4rem + env(safe-area-inset-bottom));`
and this z-index ladder:
| Layer | z | Position |
|---|---|---|
| sticky settings section nav | 30 | top under header |
| header, bottom tab bar, desktop sticky load-more | 40 | fixed/sticky |
| settings sticky save bar | 45 | bottom (above tab bar on mobile) |
| global progress bar | 50 | top |
| sheet backdrop, generic modal backdrop | 60 | full screen |
| mobile menu sheet | 70 | bottom |
| command-palette backdrop | 80 | full screen |
| palette, lightbox, tag/match/feed modals | 90 | centered |
| Tasks button/panel | 55 | bottom-right, offset above bottom bar on mobile; **below every backdrop (60/80)**; **hidden + inert whenever any dialog is open** (`body.mt-modal-open`) **or whenever the settings save bar is visible** (`body.mt-save-open`, set by Settings while dirty), so it can never sit above a dialog scrim or overlap the save bar (45); the guarantee comes from mutual exclusion, not z-order |
| toasts | 100 | top-center under header on mobile; bottom-right above Tasks on desktop |
While any dialog is open, the helper also makes the bottom bar, `#offline-banner`, `#global-tasks` and the
**interactive controls** inside `#toast-container`/`#toast-errors` inert/disabled; the live-region
containers themselves stay in the accessibility tree so announcements still work, and all controls are
restored on close. `body.mt-save-open` hides/inerts `#global-tasks` exactly like `body.mt-modal-open`; the Settings
save bar adds/removes the class when it appears/disappears. Rules: toasts must never sit under Tasks; the sticky "Load more" is **in-flow on mobile** (not fixed)
and fixed above Tasks on desktop; the settings save bar sits directly above the tab bar on mobile and
never overlaps toasts; the image lightbox is covered by UX-013; safe-area env() applies to every
bottom layer. `meta viewport` gains `viewport-fit=cover`.

### UX-008 — Visibility-aware polling (pinned literals preserved)
Keep the literal `setInterval(poll, 1500)` in `MangaTasks` and `setInterval(checkCalibreSyncStatus,
3000)`; guard inside the callbacks: when `document.hidden`, return without fetching. On
`visibilitychange`→visible, fetch immediately. Page timers that can be cheaply guarded
(feed-check status, backfill status, Downloads Torbox refresh, Manual Pickup refresh) do the same.
No `location.reload()` may be added inside `MangaTasks`. Page-level release-state timers
(`setTimeout(poll, 1500)` in the release download flow) are skipped while the document is hidden and
resume on visibility; their pinned strings stay unchanged.

### UX-009 — Loading, busy and retry state contract
Lazily loaded regions expose `aria-busy="true"` while loading and clear it afterwards. A failed list
fetch renders an inline state (same container): "Couldn't load <thing>." + a Retry button that
re-runs the same fetch; it must never leave a permanent skeleton and must not rely on
`console.error`. Skeleton styling is shared (`.mt-skeleton`). Applies to Series rows,
Releases/Prioritized batches, Downloads queue/inventory/failed/staging sections.

### UX-010 — Command palette (direct dispatch, no hand-off)
`#command-palette` markup lives **outside `<header>` and after `#toast-container`** (test-segment
safe), `role="dialog" aria-modal="true"`, labelled input, results `role="listbox"`, options
`role="option"` with `aria-selected`.
* Navigation commands: the 10 pages.
* Quick actions (dispatched directly through the already-global, null-safe functions):
  Sync Calibre → `triggerCalibreSync()`; Poll Feeds → `triggerFeedCheck()`; Process Downloads →
  `triggerStagingTag()`; Start Backfill → `openBackfillModal()`; Manual Pickup → navigate
  `/downloads/manual-pickup`.
* Search: 200 ms debounce + `AbortController`, calls `/api/search?q=`; series hit activates
  `/series?search=<stable_name>`; release hit activates `/releases?view=flat&q=<title>`; empty state
  and error state are rendered in the palette.
* Keyboard: ArrowUp/Down move, Enter activates, Escape closes, Home/End jump; Tab is trapped; focus
  returns to the header trigger. Ctrl/⌘+K toggles with an Alt+K fallback (some browsers reserve
Ctrl+K); `/` focuses the page search when not typing.
* Page bootstraps (required for activation): Series reads `?search=` on load, sets the input and
  enters server-search mode (UX-021); Releases reads `?q=`, sets the input and runs
  `handleReleaseSearch()` once (UX-023).

### UX-011 — Dashboard quick actions + pickups/setup banners
`/` gains a Quick actions card (6 buttons: Sync Calibre, Poll Feeds, Process Downloads, Start
Backfill, Manual Pickup + count, Settings) using the same dispatcher as UX-010 (no new routes).
A Manual Pickup banner appears when the active-pickup count >0 (fetched from
`/api/torbox/manual-pickups`). A setup banner appears when `calibre_status.available` is false:
"Calibre library not configured — open Settings" linking `/settings#section-calibre`. The dashboard
Download button switches to an in-place flow (UX-020); the existing queue/tag markup stays intact.

### UX-012 — Page header contract
Icon + title + one-line description; primary action right-aligned on ≥sm and full-width below on
mobile; secondary actions icon+label ≥36 px desktop / ≥44 px mobile. No page actions in the global
header (existing rule).

---

## 2. Cross-cutting accessibility & feedback

### UX-013 — Shared dialog helper (sheet, palette, lightbox, tag/feeds modals)
`MangaModal.open(element, opener, {labelledBy})` / `.close(element)`:
set `role="dialog"`, `aria-modal="true"`, remember the opener, set `inert` on
`#main-content`, header, footer, `#global-tasks`, `#mobile-bottom-bar` and `#offline-banner` while
open (CSS also hides the Tasks widget/bottom bar via `body.mt-modal-open`), and `#global-tasks` is
hidden while the settings save bar is visible (`body.mt-save-open`), disable toast action
buttons without removing the live regions, and keep the trigger/opener `aria-expanded` in sync. lock body scroll, move focus to the first focusable
element (or the dialog), trap Tab/Shift+Tab, close on Escape only for the topmost dialog, and restore
focus to the opener. Apply to the mobile sheet, command palette, image lightbox, Downloads tag modal
and Feeds add-feed modal. Existing other modals are migrated only when touched; listed in §7.

### UX-014 — Toast/live-region contract (escaping fixed)
`#toast-container` is `role="status" aria-live="polite"` for success/info (auto-dismiss 4 s);
`#toast-errors` is `role="alert" aria-live="assertive"` for failures (persist until dismissed, include
a Retry action for fetch failures). All toast text is inserted with `textContent`/nodes, never
`innerHTML`; `showToast(message, type)` keeps its public signature and existing copy. Events
announced: action failed (alert), task finished (status), sync/tag/backfill completion (status).
No double announcement: `MangaTasks` keeps the existing `#tasks-toast` status announcement and
must not also route the same event through `showToast` a second time.
Mechanism for retry: `showToast(message, type, options)` keeps its existing two-argument
signature and gains an optional third `{retry: fn, persist: true}` (or an exported
`showErrorToast`); existing call sites stay unchanged.

### UX-015 — Quantified colour/focus tokens (AA)
Required token pairs (measured values; implementer must keep or improve them):
| Pair | Minimum |
|---|---|
| body text `#e2e8f0` on `#0b0f19` | ≥12:1 |
| secondary text `#94a3b8` on `#161e2e` | ≥6:1 |
| micro labels: replace `slate-500` (#64748b) with slate-400 on dark cards | ≥4.5:1 |
| white on `brand-600` (#e11d48) | ≥4.5:1 |
| white on `brand-500` (#f43f5e) — **must not be used for text**; use `brand-600` or dark text | ≥4.5:1 |
| focus ring: solid `#f43f5e` 2 px, 2 px offset; on rose surfaces add a 1 px `#fff` outer ring | 3:1 non-text |
`slate-850` is defined (`#1b2537`) or removed. Acceptance includes a static contrast calculation for
the changed pairs.

### UX-016 — Safe areas, reflow, motion
`meta viewport` gains `viewport-fit=cover`; every bottom-fixed layer uses
`env(safe-area-inset-bottom)`; sheets use `dvh`; content reflows without horizontal scrolling at
320 px width and at 200 % zoom (tables become cards per UX-022/024/030). `prefers-reduced-motion:
reduce` disables transitions/animations except loading spinners. The five-tab bottom bar fits at
320 px without horizontal scrolling (5 equal columns, labels ≥9 px or icon-only at the narrowest
width); 200 % zoom must not clip any tab.

### UX-017 — Global 44 px / aria matrix
Every interactive control on mobile is ≥44×44 px, including row action buttons, table actions,
checkboxes, chips that act as buttons, and close buttons. Every icon-only control has `aria-label`
and `title`; this explicitly includes the Series row edit-link and blacklist icon buttons, row
checkboxes, navigator refresh, sheet/palette close and task cancel. Toggles expose `aria-expanded`; current nav/page is `aria-current="page"`; tab-like
controls expose `role="tab"`/`aria-selected` where they switch panels.

### UX-018 — Pinned-literal preservation (no silent test rewrites)
No new literal `onclick="triggerFeedCheck()"`, `onclick="triggerCalibreSync()"` or `onclick="triggerStagingTag()"` call sites may be added outside their owning pages (Feeds/Series/Downloads); the palette and dashboard dispatch through JS `data-mt-action` attributes instead. No new `prompt(` call sites anywhere.
These literals/contracts must remain unless §7 says otherwise: `setInterval(poll, 1500)`,
`setInterval(checkCalibreSyncStatus, 3000)`, `setTimeout(pollTask, 1500)`; no `location.reload()`
inside the `MangaTasks` IIFE; downloads `(t.eta or 0)`-style guards and `renderTorboxQueue`,
`renderInventory`, `refreshDownloadsNow`, `active ? 1000 : 5000`, `_sparklineSvg`, `speed_history`,
`torbox-updated`, `torbox-reconnecting`, `torbox-queue-body`, `inventory-body`; exactly one `<script`
block in downloads.html with all modals after it; `manual_pickup` strings (`pickup-match-modal`,
`openPickupMatch`, `submitPickupMatch`, `pickup-error`, "mapping kept", `pickupSaveMapping`,
`pickupSaveVolumes`, task-kind strings, no `prompt(`); releases `setTimeout(fetchNextSeriesBatch`
absent and no auto-chain; series `series-row-deferred`, `loadMoreSeriesRows`, empty-state copy,
`openSeriesCalibreMatch`, "Not linked — Match to Calibre series"; focus-visible selector; `min-h-[44px]`;
`aria-expanded="false"` count ≥4; empty-state copy on Releases/Prioritized/Series/Feeds/Logs and
`clear*Filters` names; `safe_url` on dynamic hrefs; `tag_display` signatures.

---

### UX-019 — Offline / degraded banner
A single `#offline-banner` (role="status", `aria-live="polite"`) appears when
`navigator.onLine === false` or after **two consecutive non-aborted failures** of the global
`/api/tasks` poll (only `/api/tasks` failures increment this counter; page-specific polls may show
the banner through their own twice-failed rule). It is in-flow on
desktop and fixed directly above the mobile bottom bar (z=36) so it never overlaps the mobile
toast band; it disappears on `online` or the next successful poll, and the counter resets on any
success. Aborted requests and auth redirects/401s do not count. The banner sits after the
setup/pickup banners in DOM order. This is additive to UX-009's per-region retry states.

## 3. Page matrix and requirements (each implementable without new backend routes)

### UX-020 — Dashboard (`/`) in-place download + banners
Quick actions/banners per UX-011. Download action: POST `/api/releases/{id}/download`, then poll
`/api/releases/{id}/state` every 1.5 s for up to 30 s and update the card's button label/state text
in place (no `location.reload()`); on failure or timeout show an error toast with Retry and leave the
button usable. Keep existing dashboard stats/queue/tag rendering unchanged.

### UX-021 — Series (`/series`): server-side search, state and race handling
Debounce 300 ms; `AbortController` cancels the previous request; a search request uses
`/api/series/rows?offset=0&limit=50&search=<q>`; while search mode is active: stop the 60 ms
self-chain, disconnect the observer, hide the Load More button, and render the server's rows + count;
empty result shows the existing `series-filter-empty` copy (with `clearSeriesFilters`); Clear reloads
the normal first page (or refetches offset 0) and restores the chain. Search text is reflected in
`?search=` via `history.replaceState` (debounced typing does not create history entries); on load
the page reads `?search=` and enters search mode. Palette activation deep-links through the same
bootstrap. Existing row filtering remains available for non-search interaction, but the previous
"no-JS fallback" wording is dropped (the page is JS-driven).
Server-side empty state must not claim "no matches" while a request is pending.

### UX-022 — Series stacked cards + action accessibility
**Card-mode empty states.** Any table converted to cards must render its `colspan` empty-state row as a centered block (`.mt-table-empty`) with the label pseudo-elements suppressed; no stray cells or empty-card borders. Applies to the Series, Releases flat, Downloads queue/inventory/failed/staging tables.
CSS-only conversion of `partials/series_table_row_chunk.html` below 768 px using `data-label` on all
7 cells; `<thead>` hidden visually but kept with `scope="col"` for AT; card shows name/cover/publisher,
authors, owned, latest volume, tags, actions. Actions are ≥44 px in card mode; the
"Not linked — Match to Calibre series" control becomes a visible button that keeps its exact text and
`openSeriesCalibreMatch` handler; the monitor toggle gets `aria-label`+`title` (it currently has only
`title`); `safe_url`/`tag_display` remain. The a11y static test is extended to partials in §4 and the
nine pre-existing partial icon buttons it will flag (flat release row download/ignore/unignore/
blacklist, series-card ignore/unignore, series-row monitor/edit-link/blacklist) must gain
`aria-label`+`title` in the same change. The same card-mode/empty-state contract
applies to Releases, Prioritized flat view, and Downloads tables.

### UX-023 — Releases (`/releases`): filter/order state + mobile controls
Status-pill and view links preserve `sort` (and existing parameters) so filtering never resets the
chosen ordering; the seven pills become a horizontally scrollable filter row under a "Filters" toggle
on mobile (`aria-expanded`), with the active pill scrolled into view; search+sort become a sticky
compact bar under the header on mobile. Flat table converts to cards below 768 px with `data-label`
on every cell (server partial `flat_release_row_chunk.html`), actions ≥44 px. Sticky "Load More" is
in-flow on mobile and fixed above Tasks on desktop (UX-007); keep disabled-from-first-paint, retry
label, counters and the no-auto-chain rule. `/` keyboard shortcut focuses the search. `?q=` bootstrap
runs the existing search once (UX-010).

### UX-024 — Prioritized (`/prioritized`)
Same filter/order preservation and mobile pill row as Releases; group notes visible on mobile;
group management stays in flow; amber identity retained; flat-card conversion for the flat view.
Download state chips remain in-place.

### UX-025 — Discovery (`/discovery`)
Tabs use the full ARIA tab pattern: `role="tablist"` container, each tab
`role="tab" aria-selected aria-controls="tab-content-…" id="tab-btn-…"`, panels
`role="tabpanel" aria-labelledby`, roving `tabindex` with ArrowLeft/ArrowRight/Home/End and the
`#discovery`/`#blacklist` hash restore. Pagination links include `q`, `page_size`, `include_tags` (joined), `exclude_tags` (joined),
`tag_match`, `sort` so filters survive page changes and form submits. Mobile: filter/tag panel collapses behind a "Filters" button showing the active filter count;
sort and tag picker remain reachable. Tag picker gains ArrowUp/Down/Enter/Escape and correct
`aria-expanded`. Changing sort keeps pending tag chips (apply reads current picker state).

### UX-026 — Feeds (`/feeds`)
Add-feed modal uses UX-013 (close X, backdrop, Escape, focus trap). Feed cards show name, type, URL,
interval, enabled/auto-download, last status/checked (existing fields), and actions; stack on mobile.
Editing an existing feed is **deferred** (no update endpoint; G-26 in §8).

### UX-027 — Downloads (`/downloads`): section navigator + safe qBittorrent refresh
Sticky in-page navigator with chips in page order: **Diagnostics, Awaiting Series, Failed Documents,
Queue, Inventory, Staging, Staging Cleanup, Audit**; anchor ids `#dl-diagnostics`, `#dl-awaiting`,
`#dl-failed`, `#dl-queue`, `#dl-inventory`, `#dl-staging`, `#dl-cleanup`, `#dl-audit` (wrap the
awaiting/cleanup/audit regions so every chip has a stable target), scroll-margin, active chip via
scroll-spy (`aria-current="true"`), horizontal scroll on mobile, hash persisted.
G-29 is recorded as PARTIAL: existing rows update live, but a brand-new qBit torrent appears only on
the next full page refresh (the safe updater never inserts rows).
Diagnostics collapses by default on mobile.
qBittorrent refresh (G-29): a visibility-aware 10 s timer calls the existing
`/api/downloads/status`; it may only update **text/value cells of rows already present**, matched by
a stable key (`data-torrent-hash` added to queue rows; no row add/remove/reorder, no control
replacement, no Torbox fallback rows when `live_state_available=false`/degraded). On mismatch or
failure show the existing "Updated …" chip as "stale — Retry" and never touch armed confirms or the
tag modal. Existing Torbox refresh timers and literals stay. Fix duplicate `id="torbox-row-*"` for qBittorrent rows by using the hash key: Torbox rows keep
`id="torbox-row-<release_id>"`, qBit rows use `id="qbit-row-<hash>"`, and both keep
`data-release-id`/`data-torrent-hash` where available. Downloads card mode must add `data-label` in
the five JS builders (`renderTorboxQueue`, `renderInventory`, `renderAudit`, `renderStagingDuplicates`,
`renderFailedDocuments`) and in server-rendered cells; keep `scope="col"` headers and
`aria-hidden`/non-announced generated labels. Add `aria-busy` on the queue during
refresh; failure shows the UX-009 retry state.
The two Process/Tag buttons stay (header primary, secondary explains it is the same foreground
action); no fake task binding. Tag modal uses UX-013 and warns on unsaved close.

### UX-028 — Manual Pickup (`/downloads/manual-pickup`)
Auto-refresh: poll `/api/torbox/manual-pickups` every 15 s **only while visible**; never reload the
page while an input is focused/dirty, a modal is open, or a task is running. On change, show a
"Pickups changed — Refresh" button (and update the nav badge); clicking refresh reloads only after
the dirty/focus guard allows it; the bar also offers "Discard my edits and refresh" for the dirty
case (explicit discard, no silent loss). "Edit series" prefills with the pickup's stored `series_name` when
available (add `data-series-name` to the card), else the title; because the endpoint re-keys
releases only, the post-apply copy says "release re-keyed; this pickup still shows its torrent
title" and G-36 is marked **mitigated, not fixed** (§8). Volume inputs are ≥44 px, show
"saving…"/"saved" per input, and offer a "revert to stored mapping" action. Keep all pinned strings.

### UX-029 — Logs (`/logs`)
Add client-side event-type and actor filter controls (populated from rendered badges) alongside the
text search; combining filters uses AND; Clear resets all; filtered empty state updates; mobile rows
stack badge/actor/message/time without truncating the timestamp. History beyond the current 150 rows
is **deferred** (no backend pagination; G-38).

### UX-030 — Settings (`/settings`)
**Downloads config panel.** The in-app downloader configuration panel on `/downloads` uses the same dirty-guard lifecycle for unsaved URL/username/password/enabled changes; its password input also renders blank (unchanged) and omits the field when blank.
Section nav keeps 9 anchors/order and `sticky top-20`; on mobile it becomes a horizontally scrollable
chip row with the active chip in view; **Advanced anchor is hidden when `debug_actions_enabled` is
false** (G-40). Sticky save bar sits above the mobile tab bar (z-45) and shows "Unsaved changes"; while it is
visible it adds `body.mt-save-open` (hidden/inert `#global-tasks`) and removes it when clean.
Dirty lifecycle: set on `input`/`change`; cleared only after a successful save; `beforeunload` warns
while dirty; an in-app confirm offers Stay/Discard; the three intentional reload paths plus one
reset-then-redirect path suppress the guard before reloading (`settings.html:1158` save, `:1518`/
`:1534` debug/maintenance reloads, `:1489` reset redirect). A Discard button resets the form to server state and reloads.
qBittorrent password: render `value=""`, `placeholder="•••••••• (unchanged)"`, and omit the key from
the save payload when blank (blank = unchanged); setting a new value saves it. This changes the
previous "blank clears the password" semantics deliberately and is listed in §4.
Section cards collapse on mobile (first open); all fields remain in the single form; clicking a
section-nav anchor expands the target card if it is collapsed before scrolling.

### UX-031 — Discovery/series/releases empty + error copy consistency
All lazy regions use UX-009 states and the existing pinned empty-state copy; no page claims "no
results" while a fetch is pending; Retry re-runs the exact request.

---

## 4. Test-contract changes (complete matrix)

Preserve everything in UX-018. Intentional edits (must be applied in the same commit):

| Test | Change | Why |
|---|---|---|
| `test_nav_structure.py` | 5 top-level items stay; Downloads becomes `<button data-dropdown-trigger>` (update the `<a ... data-nav-item="downloads">` assertion at :80, the dropdown child map at :98-127 adds `/downloads` + `/downloads/manual-pickup`, parent highlight expression, header button count 5→7, `aria-controls` list adds `nav-menu-downloads`, `data-nav-item` order unchanged, `_desktop_nav`/`_mobile_menu` marker comments unchanged, active-page literals extended with the manual-pickup path branch); dropdown test adds Downloads children; mobile menu assertions stay (10 routes, headings, ≥10 `min-h-[44px]`, no actions); add UX-005/UX-006/UX-010 assertions in the new suite | UX-002/003/005/006 |
| `test_ui_polish.py` | keep all existing assertions; extend icon-only scan to `templates/partials/*.html`; add skip-link, bottom-bar, palette, live-region and safe-area checks in the new suite | UX-001/013/014/017 |
| `test_background_tasks.py` | keep `setInterval(poll, 1500)` literal and no-reload-in-IIFE assertion; visibility guard is inside the callback | UX-008 |
| `test_performance_and_lazy_loading.py` | keep `Load More Series`/`loadMoreSeries` in releases and `setupInfiniteScroll()` absent; **series** keeps `series-row-deferred`/`loadMoreSeriesRows`; **prioritized** keeps `series-card-deferred`; the series chain may stay but must stop off-screen | UX-021/023 |
| `test_template_markup.py` | unchanged; new markup must satisfy it (no duplicate attrs; one downloads script; modals after) | UX-018 |
| `test_downloads_page_views.py`, `test_downloads_status_cache.py` | keep pinned expressions/function names/literals and `location.reload()` count; qBit updater is additive | UX-027 |
| `test_manual_pickup_polish.py` / `test_torbox_manual_pickup.py` | keep pinned strings; auto-refresh/prefill/save indicators additive | UX-028 |
| `test_series_refresh.py`, `test_tracked_series_calibre_link.py`, `test_tag_display_gaps.py`, `test_releases_pagination.py`, `test_releases_loadmore_availability.py`, `test_post_date_sorting.py` | unchanged; row/partial edits keep pinned strings and handlers; empty-state copy is pinned in `test_ui_polish.py` (there is no `test_empty_states` suite) | UX-022/023 |
| `test_release_download_state.py` | keep `setTimeout(poll, 1500)`, `updateReleaseStateDom`, `updateAllCardSummaries` and the no-reload-in-`downloadRelease` contract; visibility guard must not replace these | UX-020 |
| `test_background_tasks.py` | keep `#tasks-toast` and its `aria-live` region; UX-014 must not remove or duplicate the task-finished announcement | UX-014 |
| new `tests/test_ui_enhancements.py` | asserts: skip link; Dashboard in-place download string/state-poll; offline banner layer/semantics; `body.mt-modal-open #global-tasks` and `body.mt-save-open #global-tasks` hidden/inert rules; tablist/roving-keys assertion for Discovery; 5-item nav with Downloads dropdown (2 children); 7 header buttons; bottom tab bar (5 tabs, 44 px); sheet dialog+backdrop+close; palette markup/dispatch/keyboard; `viewport-fit=cover`; z-var block; toast escaping + live regions; data-label on series/release partials; discovery pagination includes tag/sort params; series `?search=` bootstrap; releases `?q=` bootstrap; settings password blank/omit; unsaved guard literals; no pinned-literal drift | all |

**Explicitly NOT changed:** `test_auth_modes`, security, downloads/Torbox backend suites, `test_nav_structure` route reachability (all 10 hrefs remain in desktop+mobile).

## 5. Acceptance criteria

1. All 85 script suites + E2E suite pass; the new suite passes; no pinned literal was removed without a §4 row.
   The new suite explicitly verifies: skip link; 7 header buttons + Downloads dropdown; 5-tab bottom bar/
   sheet; palette dispatch + `/series?search=`/`/releases?q=`; `data-label` cards; discovery pagination
   parameters; series search bootstrap; offline banner; `body.mt-modal-open #global-tasks`; release-state
   visibility guard; settings password blank/omit + unsaved guard; qBit value-only refresh contract.
2. Static layout contract: z-scale and offsets as UX-007; `viewport-fit=cover`; every fixed layer's
   offset resolves without overlap at 360 px (source-level assertions + a computed CSS review), including
   `body.mt-save-open #global-tasks` and `body.mt-modal-open` mutual exclusion; 320 px reflow contract present.
3. Contrast: the UX-015 pairs meet the listed ratios in a static calculation; focus ring present.
4. Gap ledger: every G-01…G-50 row in §8 is marked FIX (implemented), PARTIAL (with what), or DEFER (with reason); no High gap silently dropped.
   A short source-based manual acceptance checklist covers what the suite cannot observe: 10 pages reachable via
   header/mobile nav; no two fixed layers overlap at 360 px per the z/offset table; dialog traps focus and returns it;
   320 px reflow; reduced-motion rule present; 200 % zoom does not clip the bottom bar.

5. Expert validation round 3: at least 4/5 experts sign off after reviewing v0.3; remaining objections
   must be non-blocking or recorded as deferred.

**Implementation guardrail:** Phase 1 adds/changes no backend routes, schemas or DB columns; all
behaviour is template/CSS/JS plus the existing endpoints. Any backend change discovered as necessary
is deferred and recorded in §7.

## 6. Non-UI route disposition

The 125 routes = 10 HTML pages + 3 auth + `/health` + 111 JSON. UI-less JSON surfaces and their
disposition: `/api/search` (wired by the palette), `/api/releases/grouped` (unused; defer removal),
`/api/downloader/queue` (dead; defer removal), link-audit/link-repair/series-aliases GET (defer wiring),
`/api/releases/{id}/status` (defer UI), `/api/backfill/*` (used by the shared modal), debug action
(defer). No API route is a navigation target.

## 7. Deferred (Phase 2) with reasons

| Gap | Deferred item | Reason |
|---|---|---|
| G-08 | Styled 404/403/500 pages | Needs FastAPI exception handlers/backend change |
| G-26 | Feed editing / HTML-scraper config UI | No update endpoint; backend feature |
| G-31 | Single confirm idiom across all destructive actions | Requires refactoring ~20 confirm sites; design standard proposed but not applied everywhere |
| G-38 | Log history/pagination | No backend paging endpoint |
| G-39 | Per-section settings save | Needs backend partial-save semantics; only dirty-guard/discard added |
| G-42 | Maintenance card regroup | Low value vs risk; anchors exist |
| G-43 | Unified test-result surfaces | Requires refactoring ~6 inline result paths + backend error mapping |
| G-44 | Field-level validation messages | Needs schema error mapping; server 400 handling deferred |
| G-46 | Undo for blacklist/ignore/delete | Needs durable undo store |
| G-47 | Full URL state for modals/tabs | Only Discovery tabs/Series search implemented |
| G-49 | Vendored Tailwind/Lucide (offline) | Large build/deploy change |
| G-50 | Guided onboarding | Mitigated by setup banner (UX-011); full wizard deferred |
| — | Modal migrations beyond UX-013 | Deferred: #manual-match-modal, #staging-series-match-modal, #pickup-match-modal and other untouched dialogs keep current behaviour; only the sheet, palette, lightbox, tag modal and Feeds add modal migrate |

## 8. Gap disposition ledger (G-01…G-50)

FIX = implemented in Phase 1; PARTIAL = mitigated, limitation described; DEFER = §7.
G-01 FIX (UX-007), G-02 FIX (UX-003/004), G-03 FIX (UX-010/011/012), G-04 FIX (UX-010/011 backfill),
G-05 FIX (UX-008), G-06 FIX (UX-014), G-07 FIX (UX-009), G-08 DEFER, G-09 FIX (UX-011),
G-10 FIX (UX-020), G-11 PARTIAL (observer/sticky fallback, no unbounded drain off-screen),
G-12 FIX (UX-021), G-13 FIX (UX-022), G-14 FIX (UX-023), G-15 PARTIAL (counts still reflect loaded
rows), G-16 PARTIAL (counts recomputed only on full refresh), G-17 FIX (UX-007/023), G-18 PARTIAL (download stays in place; other actions
reload), G-19 FIX (UX-024), G-20 PARTIAL (no server sort for prioritized flat view), G-21 FIX
(UX-025), G-22 FIX (UX-025), G-23 PARTIAL (typing filters loaded rows; Enter is the server query), G-24 FIX (UX-025), G-25 FIX (UX-025), G-26 DEFER,
G-27 PARTIAL (sheet/palette/lightbox/tag/feeds only; rest deferred §7), G-28 FIX (UX-027), G-29 PARTIAL (UX-027: existing rows update live; new rows on next refresh), G-30 FIX (UX-027 labeling),
G-31 DEFER, G-32 PARTIAL (filter counts updated for staging groups), G-33 FIX (UX-013/027),
G-34 FIX (UX-022/027/030 card mode + 44 px), G-35 FIX (UX-028), G-36 PARTIAL (prefill + copy; still
cannot change pickup series), G-37 FIX (UX-028), G-38 DEFER, G-39 PARTIAL (dirty guard; per-section
save deferred), G-40 FIX (UX-030), G-41 FIX (UX-030), G-42 DEFER, G-43 DEFER, G-44 DEFER,
G-45 PARTIAL (mobile cards + 44 px; desktop table interactions unchanged), G-46 DEFER, G-47 PARTIAL (Series search + Discovery tabs only),
G-48 FIX (UX-009), G-49 DEFER, G-50 PARTIAL (setup banner).
