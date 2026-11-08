# UI-Enhancement Design Validation — Round 1 (expert-ui)

**Reviewer:** EXPERT-5 — aspects **D (visual consistency & accessibility)** and **E (workflow continuity & feedback)**; UI design authority for Part 5 / G-01…G-50.
**Task:** task-25 · **Date:** 2026-11-08
**Inputs:** `docs/UI_ENHANCEMENT_SPEC.md` v0.1 (282 lines, read in full), Part 5 of `docs/APP_DESIGN_SPEC.md` (interaction inventory, §4.11 test constraints, §8 gap audit lines 5178–5235), final UI claims UI-001…UI-137, and the live templates/JS (`base.html`, `downloads.html`, page templates, partials) plus the pinned test suites. Read-only: this review is the only file written.

**Verdict legend:** ACCEPT = implementable and adequate for aspect D/E; AMEND = direction correct but
requirement text/contract must change before implementation; REJECT = cannot meet the goal as written.
**Result: DESIGN VERDICT: DISAGREE** — 8 blockers (§8). All are wording/contract amendments; the design
direction (shell + palette + mobile bar + responsive cards + page continuity) is feasible and aligns with
the gap audit. Top amendments: B1 header-button count contradiction, B2 test-contract list, B3 More-sheet
vs palette/quick-action collision, B4 overlay stacking numbers, B8 missing gap dispositions (G-26/38/39/50).

---

## 1. Requirement verdict table

### 1.1 Global shell (UX-001…UX-012)

| ID | Requirement (summary) | Verdict | Concrete amendment / reason (aspect D/E) |
|---|---|---|---|
| UX-001 | Skip link → `<main id="main-content" tabindex="-1">` | **ACCEPT** | Add `.sr-only` utility + `:focus` reveal (rose ring on dark must be ≥3:1 against header/accent backgrounds); link must be the first focusable element; `main` needs the existing bottom padding from UX-005 and `scroll-margin-top: 5rem` so focus isn't hidden under the sticky header; add a source test (first `body` child, exact href `#main-content`). |
| UX-002 | Header command trigger, 6 header buttons | **AMEND (B1)** | Count is wrong once UX-003 lands: header currently has 5 `<button>` (3 nav triggers + user + hamburger, base.html:129/149/177/199/212); UX-003 makes **4** nav triggers (Library, Discover, Downloads, System), then + user + hamburger + command = **7**, not 6. Define `aria-haspopup="dialog" aria-controls="command-palette"`, icon-only mobile form needs `aria-label`+`title` (test_ui_polish icon rule), and the shortcut must not rely solely on Ctrl+K (browser-reserved on Windows/Linux Chrome/Firefox: Ctrl+K focuses the omnibox/search bar). Add a documented fallback (e.g. `Ctrl+Shift+K`/`⌘K`, header button always) and manual browser check. |
| UX-003 | Downloads▾ dropdown, Manual Pickup promoted | **AMEND (B1, B2)** | Keep 5 `data-nav-item` order (Overview, Library, Discover, Downloads, System) ✓. But test navigation: `tests/test_nav_structure.py:80` asserts `<a href="/downloads" data-nav-item="downloads"` — must become a `<button data-dropdown-trigger>`; `test_dropdown_contract` expected-children map must add `downloads → [/downloads, /downloads/manual-pickup]`; `ROUTES`/`test_all_routes_reachable` should include `/downloads/manual-pickup` (currently 9 pages only). §7 says "remove the 6th flat Manual Pickup link assertion" — **no such assertion exists**; correct the §7 row. Active state: manual-pickup page keeps `active_page="downloads"` (frontend-only path check in template is fine, no backend change), so the *trigger* must highlight on `active_page in ['downloads']` **and** `request.url.path.startswith('/downloads/manual-pickup')`; the Manual Pickup child gets its own `aria-current="page"` (missing from spec — add). |
| UX-004 | Live Manual Pickup badge (one fetch/page load) | **AMEND** | Endpoint verified: `GET /api/torbox/manual-pickups` returns `active_count` (main.py:1663–1675), no backend change needed. Define: fetch once after `DOMContentLoaded`, `sessionStorage` cache 60 s to avoid a request on every page navigation, hide on 0/failure (as written), `aria-label="N held torrents"` on the badge, and refresh after pickup fetch/retry/discard actions (custom event or `MangaTasks.watch` completion) so the badge is not stale. Put the badge in **both** the Downloads dropdown child and the mobile menu/tab item (spec says nav item + mobile Downloads tab only). |
| UX-005 | Mobile bottom tab bar (5 tabs) | **AMEND (B3, B4)** | Add `viewport-fit=cover` to the viewport meta (base.html:5 currently `width=device-width, initial-scale=1`) or `env(safe-area-inset-bottom)` does nothing; define `--mt-bottom-bar-h` CSS var and use it for `main`/`footer` padding; tabs need `aria-current="page"` and an active state for "More" when the current route is not one of the 4 destinations; `md:hidden`; z-index/offset must come from the UX-007 table; ≥44 px verified (non-negotiable #5). Tab bar must not overlap the settings save bar (settings.html:860) or sticky Load More (releases.html:179) — those are fixed bottom-right today; the spec must assign their new offsets. |
| UX-006 | Mobile menu as bottom sheet (dialog semantics) | **AMEND (B3, B4, B5)** | Keep `#mobile-menu` markup/id and its 10 `min-h-[44px]` links (tests pin the segment and count, `test_nav_structure.py:283–286`); add `role="dialog" aria-modal="true" aria-labelledby`, a labelled close button, backdrop, focus trap/initial focus/return (via shared modal helper, B5), body scroll lock, and layer it above the header/tab bar. **Do not put quick actions inside `#mobile-menu`**: the test asserts `"Poll Feeds" not in mobile` (line 284) and `">Actions<" not in mobile`; §2's matrix ("More sheet (links)") conflicts with that and with the palette's quick actions (§B3). Keep `md:hidden`. |
| UX-007 | Collision-free floating layers | **AMEND (B4)** | Current stacking is unmanaged and overlapping: header `z-40 sticky` (base.html:101), page-progress `z-50` (:98), toasts `z-50` (:276), tasks `z-50` (:390), sticky Load More `z-40` (releases.html:179; prioritized.html:210), settings save bar `z-40` (settings.html:860), image lightbox (`z-50`). Spec must publish one numeric scale (suggested: header 30, bottom bar 35, sticky in-flow 30, tasks 40, toasts 60, sheet/palette 50, modal 50, lightbox 55, progress 60) plus offsets (bottom bar height var, toasts top-centre mobile `top-20` under header, tasks above bar) and declare ownership (§6). Also: sticky Load More should move in-flow on mobile instead of floating (gap G-01/G-17) and the settings save bar must sit above the bottom bar. No tests pin z-indexes, so this is safe to change. |
| UX-008 | Visibility-aware polling | **AMEND (B2, B10)** | Direction right; implementation contract must preserve two pinned literals: `setInterval(poll, 1500)` must remain in base.html (tests/test_background_tasks.py:161–163) and `setTimeout(poll, 1500)`/no-reload inside `downloadRelease` must remain (tests/test_release_download_state.py:84–108), or those tests must be updated in the same change (they are **not** in §7). Recommended contract: keep the interval literals and early-return inside `poll()` when `document.hidden`; on `visibilitychange` → visible, poll immediately; apply the same guard to downloads live status (downloads.html:1143–1159) and release-state timers. Define single-live-region policy: moving finished tasks to `showToast` collides with the existing `tasks-toast` polite region (double announcement) — remove or downgrade one. |
| UX-009 | Shared skeleton / empty / retry states | **AMEND (B2)** | Keep test-pinned copy/classes: releases retry label `p.error ? 'Retry loading'` (releases.html, loadmore suite:62), empty-state strings and `clear*Filters` functions (test_ui_polish:138–162), `updateLoadMoreUi()` count ≥8. New skeleton must be `aria-busy`+status text; retry ≥44 px. Clarify that Downloads "lazily loaded sections" are the JS-rendered inventory/failures/duplicates/audit bodies (downloads.html:1000–1060, 1266–1330, 1452–1520), not server-rendered sections. |
| UX-010 | Command palette | **AMEND (B3, B5, B9)** | Dialog semantics/focus trap/return via shared helper; verify `/api/search` (q required, max 200, limit 1–50; returns `{series, releases, counts}`, main.py:3498–3514, search.py:72–180) — spec must consume those keys. Define Enter routing precisely: **series hit → `/series?q=<stable_name>`**, **release hit → `/releases?q=<title>`**; both pages must read the param on load, set search and trigger their search path (frontend-only; `releases_view`/`series_view` ignore extra params — no backend change). Quick actions: use a `data-action` registry, not inline `onclick="trigger…()"` (tests forbid extra `triggerFeedCheck` call sites: test_nav_structure:234–240 and expect the only one in feeds.html) and define the handoff contract (B9). Add `aria-activedescendant`/listbox semantics or roving tabindex, Esc close, 44 px option rows. |
| UX-011 | Dashboard quick actions | **AMEND (B3, B9)** | Reuse the palette action registry (one implementation); keep "Poll Feeds"/Sync/etc. as `data-action` buttons to avoid the pinned single-call-site tests (test_poll_feeds_single_trigger scans all templates). "Start Backfill" should call the already-global `openBackfillModal()` **in place** (base.html:756) — no navigation/handoff needed; manual-pickup card should consume the new badge/count contract; add loading/skeleton for the count; keep in-place download state chip. Ensure no action is duplicated between palette and dashboard with different behaviour (single registry + shared test). |
| UX-012 | Consistent page-header contract | **AMEND** | Add a per-page mapping table (10 pages) with h1 text/icon/primary action so implementation and tests agree; keep the "no page action in global header" rule; preserve test-pinned content order inside page headers — e.g. Poll Feeds must stay before Add Feed in feeds.html (test_nav_structure:227–232) and Settings save button inside the form (test_ui_polish:69–76). Define one `<h1>` per page and secondary dialog headings as `<h2>`/`aria-labelledby` (current dialogs use `h3` with no `h2`, an existing a11y defect, G-a11y new). Resolve the 44/36 px rule into one control-size matrix (B5). |

### 1.2 Page-by-page plan (§3) and system sections (§4–§7)

| Section | Verdict | Concrete amendment (D/E focus) |
|---|---|---|
| §3.1 Dashboard | **AMEND (minor)** | Quick actions must use the shared registry (UX-011); reference the count-fetch skeleton/error handling; Manual Pickup banner must not duplicate the new nav badge. |
| §3.2 Series | **AMEND** | Stop the 60 ms auto-chain when the container is off-screen is good (G-11) but must keep the test-pinned strings `series-row-deferred`, `loadMoreSeriesRows`, `IntersectionObserver`, `Load More Series` (tests/test_performance_and_lazy_loading.py:110–126) even if the branch is now dead — or update that suite deliberately. Server-side search must preserve the current debounce/empty-state copy (`clearSeriesFilters`) and URL contract for the palette (`?q=`). Card conversion must keep the exact "Not linked — Match to Calibre series" text (test_tracked_series_calibre_link:146) and `tag_display(s.tags_json, view='series')` (test_tag_display_gaps:65). 44 px applies to the match/backfill/blacklist controls in the card. |
| §3.3 Releases | **AMEND** | Preserve load-more contract exactly (disabled-from-paint buttons, `<span>` labels, `updateLoadMoreUi()` ≥8, `{{ initial_total if current_view != 'flat' else 0 }}`, forbidden `class="hidden px-5 py-2.5`, no `location.reload` in `downloadRelease`) — tests/test_releases_loadmore_availability.py:32–94, test_release_download_state:84–108. Status pills must preserve `view` **and `sort`**, and (G-14) either retain the client search term across pill clicks via URL/sessionStorage or explicitly defer; `Filters` toggle needs `aria-expanded/controls`; the sticky compact bar must not collide with toasts/tasks (UX-007). |
| §3.4 Prioritized | **AMEND** | `hidden sm:inline-block` notes fix fine (G-19); the flat API ignores `search`/`sort` (G-20) — since backend is out of scope, state explicitly "deferred: prioritized flat view stays unsearchable server-side; client filter only". Preserve `clearPrioritizedFilters` copy (test_ui_polish:145–151). |
| §3.5 Discovery | **AMEND** | Building self-links from existing request params needs no backend change (discovery_view already passes `include_tags`, `exclude_tags`, `tag_match`, `tag_sort`: main.py:1615–1618) — state this. Tabs: `role=tablist/tab/tabpanel`, `aria-selected`, hash restore, focus on activation. Tag picker: arrow/Escape/Enter, `aria-expanded` reset, outside-click close; keep `/api/tags` autocomplete. Mobile sticky filter bar must not overflow at 360 px. |
| §3.6 Feeds | **AMEND (B8)** | Card layout + modal close are good, but G-26 (no edit UI; HTML-scraper type unconfigurable) is a **High** gap and is not addressed or deferred. Add an explicit deferral with reason (needs a backend update endpoint/config UI) or a minimal requirement. Keep Poll Feeds→Add Feed order test (test_nav_structure:227–232) and the two pinned button styles. |
| §3.7 Downloads | **AMEND (B4, B7)** | Section navigator needs `sticky` offset under the header and `aria-current`; must not stack with the bottom bar. Diagnostics collapse must keep `auto-download-summary/checklist/decisions` ids. qBittorrent queue refresh: `GET /api/downloads/status` supports it (main.py:2104–2123) — fine, but 10 s polling must be visibility-gated (UX-008) and must not regress the pinned template guards `(t.eta or 0)` etc. (test_downloads_page_views:161–175). Table→card: see B7 feasibility. Tag modal must use the shared modal helper; keep the single `<script>` + modal-after-script constraint (test_template_markup:95–106). Process Downloads: add `data-task-kind` **only if** a persisted task kind is actually emitted — it is not today (Part 5 UI-137), so either emit `downloads:process` or label it foreground; do not create a dead binding. |
| §3.8 Manual Pickup | **AMEND** | Prefill `pickup.series_name` is available in the view context (manual_pickup.html already renders `pickup.series_name`, main.py:1622–1660) — feasible. 15 s refresh while visible must not poll while the fetch task modal is open and must respect UX-008. Volume save indicator + inline error good; keep `pickup-error`, "mapping kept", no `prompt(` and task-kind hooks (test_manual_pickup_polish, test_torbox_manual_pickup). |
| §3.9 Logs | **AMEND (B8)** | Filter selects are good, but G-38's core (only 150 rows, no history) remains: either explicit deferral (server has no pagination endpoint; backend out of scope) or a frontend "show older" via a new endpoint would violate scope #4. Keep exact empty-state copy/`clearLogFilters`, actor chip markup (test_audit_actor:271), and add confirm to Prune now. |
| §3.10 Settings | **AMEND (B6)** | Unsaved guard: dirty set on input/change, cleared **only after successful save**; suppress `beforeunload` for the 4 intentional reload paths (settings.html:1158 prune, 1518 add group, 1534 delete group, 1489 reset redirect `/`) and for Save itself; discard restores loaded values then clears dirty; beforeunload only while dirty. Save bar must sit above the bottom bar (UX-007). Collapsible cards must keep the 9 anchors in exact order/sticky `top-20` (test_ui_polish:39–62). qBittorrent password: make the fix firm (B6), not "if feasible". |
| §3.11 Feedback & consistency | **AMEND (B5, B2)** | These are currently unnumbered prose bullets and one dangling `UX-016`. Convert to numbered requirements (UX-013…): toast API with escaping (build icon element + `textContent` for message; keep 4 types/4 s), single polite live region, error text includes action name via call-site map, retry actions; modal helper API (open/close, dialog vs sheet variant, focus trap/return, Esc, scroll lock, aria-labelledby, stacked-dialog guard, background aria-hidden) and a migration list of the 9 existing modals with test-pinned ids; preserve `test_template_markup` constraints. |
| §4 Visual system | **AMEND (B5)** | `slate-850` fix ✓; shared classes ✓ but add implementation constraints: where the style block lives (base.html `<style>`), cascade vs Tailwind CDN (use `!important`-free specificity or config), and no new colours beyond brand/slate/status set. Add mandatory contrast tokens (see M-4: `text-slate-500` on `#0b0f19` is ≈3.7:1 and fails AA at 10–12 px; use slate-400/300 for readable copy), minimum text sizes, numbered reduced-motion requirement (explicit list: shimmer, pulse, bounce, hover scale, animate-spin rings — keep loading spinners), focus ring that stays visible on rose/cyan active backgrounds, and an explicit 44 px matrix (B5). Typography "body ≥13 px mobile" needs a concrete mechanism (Tailwind config `fontSize` or scoped CSS override); a root-font-size media query would scale spacing too and must not be chosen silently. |
| §5 Out of scope | **AMEND (B8)** | Every deferral must name the gap IDs and reason (G-26 feed edit, G-38 log history, G-39 per-section save, G-49 offline/CDN, G-50 onboarding) or acceptance #4 is unverifiable. |
| §6 Acceptance | **AMEND** | Add acceptance checks for: contrast token audit, reduced-motion static check, skip-link focus, `aria-current` on desktop nav/mobile menu/tab bar, toast escaping call-site coverage, visibility-poll test, unsaved-guard test, data-label pattern present in server partials **and** JS builders, 44 px control matrix, fixed-overlay non-overlap (manual bounding-box check on 360 px and 1280 px), and a G-disposition table check. Change "every one of the 125 routes" (M-1) to "all 10 HTML pages". |
| §7 Intentional test updates | **AMEND (B2, B3)** | Incomplete and one statement is factually wrong (§3 below). |

---

## 2. Gap-audit coverage (Critical/High only)

| Gap | UX/§ coverage | Status |
|---|---|---|
| G-01 overlays collide | UX-007 | **Partial** — needs numeric z-scale/offsets (B4) |
| G-02 Manual Pickup nav/badge | UX-003/004 | Covered |
| G-03 no global action entry points | UX-010/011 + §2 matrix | Covered (B3/B9 contracts) |
| G-06 toast HTML injection | §3.11 + dangling UX-016 | **Partial** — unnumbered/missing contract (B5) |
| G-07 silent failures | UX-009 | Covered (keep pinned copy) |
| G-11 series auto-drain | §3.2 | Covered (test strings) |
| G-12 series search false negatives | §3.2 server search | Covered (URL contract missing) |
| G-13 hidden "not linked" action | §3.2 cards | Covered |
| G-14 filters reset | §3.3 | **Partial** — sort/view only; search retention unspecified (G-14) |
| G-21 pagination loses tags | §3.5 | Covered |
| G-22 tag-picker keyboard | §3.5 | Covered |
| G-26 no feed edit / scraper config | — | **Not covered, not deferred** (B8) |
| G-28 downloads monolith | §3.7 | Covered (sticky offsets) |
| G-29 qBit no refresh | §3.7 | Covered |
| G-34 cramped tables | §3.2/3.3/3.7 + §4 | **Partial** — feasibility/AT amendment (B7) |
| G-38 logs history/pagination | §3.9 filters only | **Partial** — needs explicit deferral (B8) |
| G-39 settings all-or-nothing save | §3.10 guard only | **Partial** — per-section save deferred? state it (B8) |
| G-41 password echoed in HTML | §3.10 conditional | **Partial** — make firm (B6) |
| G-49 offline/CDN | §5 | Deferred ✓ (record reason) |
| G-50 onboarding | — | **Not covered, not deferred** (B8) |

Medium gaps covered by the plan: G-05/UX-008, G-09/UX-011, G-10/§3.1, G-17/UX-007, G-19/§3.4,
G-24/§3.5, G-27/§3.6, G-30/§3.7, G-32/§3.7, G-33/§3.7, G-35/§3.8, G-36/§3.8, G-37/§3.8,
G-48/UX-009. Not addressed (acceptable only if listed as deferred): G-04, G-08, G-15/16, G-18,
G-20 (backend), G-23, G-25, G-31 confirm idioms, G-40 Advanced dead anchor, G-42, G-43, G-44,
G-45, G-46, G-47.

---

## 3. §7 test-contract review (what must change and what §7 misses)

Verified baseline: **85** `tests/test_*.py` suites exist (spec says 85 ✓) and `tests/run_tests.py`
auto-discovers `test_*.py` by glob, so adding `tests/test_ui_enhancements.py` needs no runner edit ✓.
§7's four rows are directionally right but incomplete and partly incorrect.

| Suite | Required update | §7 status |
|---|---|---|
| `test_nav_structure.py` | **Header buttons 5→7** (not 6): line 190 `assert len(buttons) == 5`; line 80 Downloads `<a href="/downloads" data-nav-item>` → `<button …data-dropdown-trigger>`; `test_dropdown_contract` expected_children add `downloads`; `test_dropdown_js` setAttribute count ≥4 still ok; `test_all_routes_reachable`/ROUTES add `/downloads/manual-pickup` (and decide desktop reachability assertion); `test_active_page_values` add manual-pickup path highlight; `test_mobile_menu` must stay valid. | **Incomplete/incorrect** — says 5→6 and describes a non-existent "6th flat link assertion". |
| `test_nav_structure.py` (conflict) | `assert "Poll Feeds" not in mobile` (line 284) and `assert ">Actions<" not in mobile` (line 283) forbid quick actions inside `#mobile-menu`. §2's "More sheet (links)" matrix conflicts; either keep More sheet navigation-only (recommended) or update this test deliberately. | **Missing** |
| `test_nav_structure.py` (call sites) | `test_poll_feeds_single_trigger` (234–240) forbids `onclick="triggerFeedCheck()"` in base/other pages and asserts one call site in feeds.html; palette/dashboard/Mobile quick actions must use `data-action`, never inline trigger calls. Also `test_action_buttons` (147–178) pins header/portal constraints. | **Missing** |
| `test_ui_polish.py` | Keep the exact focus-visible selector string (line 193) — §4 says "extend", so either preserve that literal and add new selectors, or update this test; icon-only rule (171–200) now covers the command button/tab-bar/sheet/palette controls; `min-h-[44px]`/`aria-expanded` counts still pass; settings 9-anchor order/`sticky top-20`/`form="settings-form"` must survive collapsible cards (39–81); empty-state copy/`clear*` functions (138–162) must survive §3 rewrites. | **Missing** (only "additive" is stated). |
| `test_background_tasks.py` | `setInterval(poll, 1500)` literal (162) and "no `location.reload` in MangaTasks" (165) — the visibility-aware poll must keep the literal (early-return inside poll) or §7 must list the update. | **Missing** |
| `test_release_download_state.py` | `setTimeout(poll, 1500)`, `updateReleaseStateDom`, no-reload-in-`downloadRelease` literals (84–108) must survive page-level visibility gating. | **Missing** |
| `test_downloads_page_views.py` | `(t.eta or 0)` / `(t.progress or 0)` / `(t.size or 0)` guard strings (161–175) must remain in downloads.html even after card markup changes; spec's "only wrappers/data attributes" is not enough for this greppy suite. | **Missing** |
| `test_releases_loadmore_availability.py` / `test_releases_pagination.py` | Exact pinned strings: `id="btn-load-more-series" disabled`, forbidden `class="hidden px-5 py-2.5`, `updateLoadMoreUi()` ≥8, `isLoadingSeries = false;\n        updateLoadMoreUi();`, `{{ initial_total if current_view != 'flat' else 0 }}`, "never block the Load More control", no auto-chain on releases. Mobile filter/sticky-bar rewrite must preserve them. | **Missing** |
| `test_performance_and_lazy_loading.py` | Requires base `page-progress-bar`/`ProgressBar`/`nav-link-loading`; releases "Load More Series", no `setupInfiniteScroll`; series `series-row-deferred`/`loadMoreSeriesRows`; the new series search/paging must keep these strings (or update deliberately). | **Missing** |
| `test_template_markup.py` | New JS-built rows (`data-label` in `<tr>`/`<td>` strings) must not duplicate attributes; downloads must keep exactly one `<script>` and modals after it; modal-helper migration must not move markup into the script block. | **Missing** |
| `test_ui_polish` empty-state / `test_tracked_series_calibre_link` / `test_tag_display_gaps` / `test_security_remediation` (`|safe_url`) | Page/series/row rewrites must preserve those exact strings/filters; cite Part 5 §4.11's full constraint catalogue as the authoritative preservation list. | **Missing** |
| All page-view suites | §7's row is too vague ("keep pinned ids/functions; only wrappers/data attributes added"): list the suites and note that CSS-only table→card conversion is the chosen mechanism precisely because it preserves DOM/ids (B7). | **Incomplete** |
| `tests/test_ui_enhancements.py` (new) | Must include static checks for: skip link; header button count 7 + command aria; Downloads dropdown children/active; bottom bar `md:hidden`/44 px/`aria-current`/safe-area var; sheet dialog attrs/close; palette dialog + shortcut + result keys; `data-label` present in server partials **and** JS builders; discovery link params; URL `?q=` consumption on series/releases; toast escaping (`textContent`, no message `innerHTML`); `document.hidden` guard; `beforeunload` guard + reload suppression; reduced-motion media query; `slate-850` gone/defined; `aria-current`; contrast token choices; no duplicate attributes (inherited). | **Under-specified** (add list) |

Manual checks that cannot be automated (harness Jinja shim returns placeholder HTML, tests/e2e/harness.py:164–176; there is no browser automation in this repo): 360 px and 1280 px overlay non-overlap, focus trap/Escape/return, sheet + keyboard behaviour, toast live-region announcement, card layout at 360 px, safe-area on iOS.

---

## 4. Missing requirements (must be added before implementation)

* **M-1** Non-negotiable #1 says "every one of the 125 routes" — impossible for JSON/auth routes; scope it to the **10 HTML pages** + sign-out.
* **M-2** Gap-disposition table: G-01…G-50 → `fixed by UX-xxx / deferred (reason)`; acceptance #4 otherwise unverifiable. Required for G-26, G-38, G-39, G-50, G-49 (H gaps) and for the Medium gaps listed in §2.
* **M-3** First-run onboarding (G-50, H): dashboard empty-state banner when `calibre_status.available` is false with links to Settings › Calibre + Feeds + Downloader; no backend change needed (context exists, index.html:20–30).
* **M-4** Contrast/token accessibility: replace `text-slate-500` micro-copy on `#0b0f19` (≈3.7:1, fails AA below 18.66 px bold/24 px) with slate-400/300 for readable text; document minimum sizes (≥12 px desktop, ≥13 px mobile for body; 10–11 px only for non-essential metadata with 4.5:1 or a size bump); check new bottom bar/toast translucent backgrounds.
* **M-5** Numbered accessibility requirements: `aria-current` on all nav/tab systems, dialog heading association (`aria-labelledby`; fix existing `h3`-without-`h2` modals), `aria-busy` skeletons, live-region policy (one polite region; task completion not double-announced), reduced-motion (numbered), keyboard for palette/sheet/tag picker, 44 px control matrix.
* **M-6** Modal-helper requirement text (API, variants, focus trap/return, Esc, scroll lock, stacked dialogs, background `aria-hidden`, migration list with pinned ids) — currently one prose bullet.
* **M-7** Toast requirement text: escaping via textContent/icon element, single polite region, action-name mapping, error retry action, no `innerHTML` of server/user text, escape copy; keep 4 s and four types.
* **M-8** Command-palette search/handoff contracts: result-key mapping, Enter URL contract (`?q=` on /series and /releases with page-side consumption), `data-action` registry, sessionStorage one-shot handoff protocol (key, consume-on-DOMContentLoaded before page JS, delete before dispatch, bfcache/pageshow guard, 3 s timeout fallback toast then plain navigation), actions already in base (Backfill) run in place, shortcut fallback.
* **M-9** Unsaved-guard contract (dirty lifecycle, clear-on-success, suppression list, discard semantics) and firm qBittorrent-password fix (render blank with placeholder, omit blank from payload; note deliberate semantics change blank=unchanged, matching Torbox mask; no backend change).
* **M-10** Card-conversion contract: CSS-only over existing table DOM; `data-label` in server partials **and** JS builders (downloads: `_torboxRowHtml` :935, `renderInventory` :1000, `renderFailedDocuments` :1707, `renderAudit` :1266, `renderStagingDuplicates` :1452 — plus server-rendered initial rows); preserve table semantics with explicit ARIA roles or visually-hidden headers; labels hidden from AT; 44 px actions in cards.
* **M-11** Scroll/context restoration for reload-after-action pages (`history.scrollRestoration` or sessionStorage restore) and a persisted "action result" toast across the reload/handoff — currently neither exists (G-47/G-18).
* **M-12** Sticky offsets: define `--mt-header-h`, `--mt-bottom-bar-h`; update settings save bar (settings.html:860), releases/prioritized sticky Load More (releases.html:179; prioritized.html:210), downloads section navigator, toasts/tasks positions; `scroll-mt` for in-page anchors.
* **M-13** Mobile safe-area meta (`viewport-fit=cover`) and iOS `env()` handling; tab-bar/content padding; landscape check.
* **M-14** Baseline preservation list: reference Part 5 §4.11 (29 constraint groups) as mandatory reading for every aspect; no aspect may remove a pinned string without the corresponding test update.
* **M-15** `base.html` single-writer/integration order across aspects (A shell, B mobile, D tokens/a11y, E feedback) to avoid merge collisions (all touch the same `<style>`/script blocks).
* **M-16** Activity/page-level retry semantics: failures must show an inline retry, not only a toast (G-07), and retry actions must be keyboard reachable.
* **M-17** Discovery "Filters" summary must be announced (`aria-expanded`, active-filter count) and preserve filter state when toggling hide/show.
* **M-18** Logs/settings/server-limited surfaces need explicit "deferred" statements rather than silence (G-38 history, G-39 per-section save, G-20 prioritized server sort/search).

---

## 5. Overlaps and collisions between aspects A (shell), B (mobile) and D/E

`base.html` is touched by **every** aspect — highest collision risk. Proposed ownership (add to spec §6):

| Surface | Owner | D/E acceptance |
|---|---|---|
| z-index tokens + overlay offsets (UX-007) | **A** owns values, writes them once as CSS vars | D verifies contrast/focus/AT order; B supplies `--mt-bottom-bar-h`; E verifies toast/task ordering |
| Header command trigger (UX-002) + palette (UX-010) | **A** markup/JS | D: dialog role/trap/labels/keyboard; E: search routing + handoff contract |
| Nav dropdowns (UX-003) + badge (UX-004) | **A** | D: `aria-current`/44 px; E: badge fetch lifecycle/refresh |
| Bottom bar (UX-005) + More sheet (UX-006) | **B** markup/behaviour | D: dialog semantics/focus/safe-area/aria-current; A: content padding + z layer; E: navigation feedback |
| Skip link / page header contract (UX-001/012) | **A** (shell) + page owners apply | D defines focus/style and per-page h1/heading rules; E defines primary-action placement |
| Visibility polling (UX-008) | **E** defines contract; **A** implements tasks poll; page aspects implement page polls | §7 test literals |
| Skeleton/retry (UX-009) | **E** policy + D classes; page aspects markup | keep pinned copy |
| Dashboard quick actions (UX-011) | page owner + **A** registry | E: single registry/handoff; D: 44 px/labels |
| Toast + modal helper (§3.11) | **A** markup/helper; **D** a11y acceptance; **E** content/feedback | one live-region policy |
| Card conversion (§4/§3.2/3.3/3.7) | **D** CSS pattern; **B** applies mobile; page aspects add attributes incl. JS builders | table semantics/AT check |
| Reduced motion / contrast / typography (§4) | **D** writes base `<style>` additions | A must not reorder/override; tests static |

Collision hot-spots: (1) UX-002 vs UX-003 header-button count (B1); (2) UX-005/006/007 stacking vs existing fixed overlays (B4); (3) UX-010/011 handoff vs test single-call-site rules (B9); (4) More sheet vs test "Poll Feeds not in mobile" (B3); (5) §4 base `<style>` edits by D and A (M-15); (6) tasks `tasks-toast` live region vs new toast live region (UX-008/§3.11); (7) mobile bottom bar vs settings save bar/sticky Load More/toasts (UX-007).

---

## 6. Feasibility answers for the lead's specific questions

* **Overlay stacking (UX-007):** feasible but **underspecified** — must publish numeric z-scale and offsets and place the existing `z-40/z-50` elements into it (header/base:101, progress/base:98, toasts/base:276, tasks/base:390, load-more/releases:179, save-bar/settings:860, lightbox/base:279–288). Also add `viewport-fit=cover` for safe-area. Blocking amendment B4.
* **Modal helper semantics:** feasible (all modals are plain divs with `onclick` handlers; converting them is a base-JS utility + per-template attribute wiring) but **must not move markup** (test_template_markup requires downloads' modals after its single `<script>`), must keep pinned ids/functions, and needs an explicit API/behaviour list (M-6/B5).
* **data-label card conversion:** **yes, feasible.** Server-rendered tables can convert with CSS only (`thead` visually hidden at <md, `tr/td` block, `td::before{content:attr(data-label)}`) and JS-rendered tables simply need `data-label` added in their template strings: downloads `_torboxRowHtml` (downloads.html:935), `renderInventory` (:1000), `renderAudit` (:1266), `renderStagingDuplicates` (:1452), `renderFailedDocuments` (:1707); releases/prioritized/series rows are server-partial HTML so attributes live in partials. Caveat: changing `display` strips implicit table semantics — add explicit `role="table/row/cell"` or visually hidden headers, and mark the `::before` labels `aria-hidden` (B7).
* **Toast escaping:** feasible but must define the helper (build icon node + `textContent` message; never `innerHTML` with message) and audit ~100 call sites that embed feed/user data (e.g. base.html:832, 911; releases.html:554); also one live-region policy (B5/M-7).
* **Unsaved-changes guard:** feasible with `beforeunload`; the risk is self-inflicted reloads on the settings page (prune :1158, add group :1518, delete group :1534, reset → `window.location.href='/'` :1489) which would throw the prompt — the requirement must include a suppression flag and clear-dirty-on-successful-save (B6).
* **Command-palette quick-action handoff:** feasible because all five actions already live in `base.html` (`triggerCalibreSync` :516, `triggerFeedCheck` :541, `triggerStagingTag` :594, `openBackfillModal` :756, `globalStopBackfill` :647) — so Backfill/Stop can run **in place**; navigation is only needed for page-local actions. Contract must be nailed down (one-shot key consumption, bfcache guard, timeout fallback, data-action registry, no inline trigger strings) (B9).

---

## 7. Risks

1. `base.html` is a single shared file for A/B/D/E — high merge-collision and review risk (M-15).
2. Test suites are greppy string contracts; most UI work will fail tests unless the exact literals are preserved — the §7 list is incomplete (B2).
3. Ctrl+K is browser-reserved on Windows/Linux Chrome/Firefox; relying on it alone risks a broken "power" affordance (amend UX-002/B9).
4. Table→card CSS + AT semantics: silent accessibility regression if roles/headers are not preserved (B7).
5. Toast escaping touches ~100 call sites; partial coverage leaves XSS paths (G-06).
6. Unsaved guard can wrongly block intentional settings reloads (B6).
7. Palette handoff can silently lose an action (target page error/timeout) — fallback toast/navigation required (B9).
8. Bottom bar + safe-area + iOS keyboard + fixed overlays can still overlap despite z-scale (manual device checks needed).
9. qBittorrent password fix changes blank-submit semantics (clear → unchanged); document as intentional.
10. Font-size/contrast changes can alter layout density widely; scope them to changed surfaces and verify no table overflow regressions.
11. All new behaviour is only statically testable (Jinja shim, no browser automation) — the acceptance plan needs explicit manual device checks; otherwise "85 suites pass" does not prove the UX.
12. 44 px dense-row actions expand table/cards and may increase scroll; card mode is the mitigation, but only if applied consistently (B7).

---

## 8. Blocking amendments (must be applied to UI_ENHANCEMENT_SPEC before DESIGN VERDICT can be AGREE)

1. **B1 — Header-button count contradiction.** UX-002 says 6 and §7 says "5 → 6"; with UX-003 the header has **4** nav triggers + user + hamburger + command = **7** raw `<button>`s. Update both places (or make UX-002 count conditional/auth-aware), and state that the source-level test counts the raw (auth-conditional) user button.
2. **B2 — §7 test-contract list incomplete/incorrect.** Add the rows in §3 (§ specific literals and suites), correct the "6th flat Manual Pickup assertion" claim, add the visibility/timer literals, focus-visible selector, downloads guard strings, load-more/perf strings, empty-state copy, and require the full Part 5 §4.11 preservation catalogue.
3. **B3 — More sheet vs quick-action collision.** Decide and write: More sheet is **navigation-only** (recommended) because `test_nav_structure.py:283/284` forbids "Poll Feeds"/">Actions<" inside the mobile menu; mobile quick actions live in the palette/dashboard instead, or the tests are intentionally updated (specify exactly). Resolve §2's matrix wording accordingly.
4. **B4 — Overlay stacking underspecified.** Add the numeric z-index scale, offsets, `viewport-fit=cover`, `--mt-header-h`/`--mt-bottom-bar-h`, and map every existing fixed overlay (header, progress, toasts, tasks, load-more, save bar, lightbox, modals) to it; assign ownership (A owns values, B/D/E verify) and require the manual 360/1280 px overlap check.
5. **B5 — Dangling/un-numbered requirements.** UX-016 is referenced but does not exist; §3.11 and the UX-cross-cutting rules (toast escaping/aria, modal helper, loading/retry, reduced motion, contrast, aria-current, 44 px matrix) must become numbered requirements with concrete APIs/acceptance. Remove or assign UX-016.
6. **B6 — Unsaved guard + qbit password.** Define dirty lifecycle (set on input/change; clear only after successful save), suppression for the four intentional settings reloads (:1158/:1489/:1518/:1534), discard semantics, and make the qBittorrent-password fix firm (blank render + omit blank from payload; note the intentional blank=unchanged semantics change).
7. **B7 — Card-conversion feasibility/accessibility contract.** Require CSS-only over existing table DOM, `data-label` in server partials **and** the five JS builders listed in §6, explicit table ARIA roles/visually hidden headers (labels `aria-hidden`), 44 px actions in card mode, and no removal of test-pinned ids/guards.
8. **B8 — Gap disposition missing.** Acceptance #4 cannot be verified without a G-01…G-50 table; add it and specifically resolve the unaddressed High gaps: G-26 (feed edit; defer with reason or add), G-38 (log history; defer), G-39 (per-section settings save; defer), G-50 (onboarding; add M-3), G-49 (defer ✓), G-14 search retention (fix or defer). Also add missing requirements M-1…M-18 (esp. M-4/M-5 a11y, M-8/M-9 contracts, M-11 scroll continuity, M-14 preservation list).

**DESIGN VERDICT: DISAGREE** (blockers B1–B8). After the amendments above are applied, the direction is
acceptable and I expect to sign off in round 2; no requirement is rejected outright.
