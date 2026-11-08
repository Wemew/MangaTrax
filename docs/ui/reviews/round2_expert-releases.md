# UI-Enhancement Design Validation — Round 2 — expert-releases (aspects A + C)

**Reviewer:** expert-releases · **Task:** task-26 · **Design:** `docs/UI_ENHANCEMENT_SPEC.md` v0.2 (387 lines; UX-001…UX-031 + §2 accessibility, §3 page requirements, §4 test matrix, §5 acceptance, §6 route disposition, §7 deferrals, §8 gap ledger).
**Inputs read:** v0.2 in full; my round-1 review (`docs/ui/reviews/round1_expert-releases.md`, asks M1–M13/A1–A11); the round-1 reviews of the other four experts; `docs/APP_DESIGN_SPEC.md` Part 5; the affected templates/tests; `git status`/`git diff` of the working tree.
**Working-tree note:** the tree currently contains a **partial reference implementation** (`M backend/app/templates/base.html`, `M tests/test_nav_structure.py`; +613/−45 lines). This review validates the **design**; it used those edits only as feasibility evidence. If they are part of the v0.2 change they must be reviewed in the implementation round against UX-018/§4, not landed as part of design sign-off (no edits were made by this reviewer).

---

## 1. Round-1 blockers — resolution (M1–M13)

| M | Ask (round 1) | v0.2 status | Evidence / remaining action |
|---|---|---|---|
| M1 | Replace impossible "125 routes ≤2 taps"; add API disposition; add 10-page matrix + verification | **RESOLVED (core)** — non-negotiable #1 now scopes to the 10 HTML pages, excludes the 111 JSON routes, requires actions ≤2 or page-local; §6 lists the non-UI route disposition; `/api/search` wired by palette, 6 surfaces deferred. Residual: non-negotiable #1 still says "documented in **the page matrix**" but no such matrix exists in v0.2, and no acceptance/test item verifies it. | v0.2:10-13, 346-352; residual in N3 below |
| M2 | Dispatcher contract for quick actions; no sessionStorage hand-off; no card/action duplication | **RESOLVED** — UX-010 dispatches directly through the global functions (`triggerCalibreSync/triggerFeedCheck/triggerStagingTag/openBackfillModal`), Manual Pickup navigates; UX-018 forbids new literal `onclick` action call sites and routes palette/dashboard through `data-mt-action`. Residual minor: running-state/busy rules for actions already in flight are not specified (the underlying functions are null-safe and self-guard, so no duplicate-submit risk). | v0.2:110-126, 186-188; baseline globals `base.html:516/541/594/756/797` |
| M3 | Palette search + `/series?search=` / `/releases?q=` bootstraps; result/ARIA contract | **RESOLVED** — UX-010 defines debounce/AbortController, `/api/search`, target URLs, listbox/option semantics, keyboard, empty/error states; UX-021 (Series `?search=` + server mode) and UX-023 (`?q=` + one search run) define the bootstraps. | v0.2:110-126, 218-226, 237-245 |
| M4 | Gap-disposition ledger G-01…G-50 | **RESOLVED** — §8 ledger with FIX/PARTIAL/DEFER per gap + §7 deferral reasons + acceptance #4. | v0.2:334-344, 354-369, 371-387 |
| M5 | Feeds config fields frontend-only + edit/replace decision | **REMAINING (non-blocking)** — UX-026 defers *all* of G-26 and §7's reason says "No update endpoint; backend feature". Editing is genuinely backend, but **create-time `config` fields are frontend-only** (API already accepts `config`; the modal sends only `{url, feed_type}` at `feeds.html:197`). | Fix wording to split G-26: "config-at-create: deferred for scope (feasible now); edit: needs backend"; or add selector/max-pages fields to the create modal. |
| M6 | Continuity: in-place for global actions; preserve filter/scroll across reloads; undo or defer | **PARTIAL (documented)** — in-place priority ✓ (UX-020/021/023/027/028), G-18 PARTIAL and G-46 DEFER with reasons. Cheap filter/scroll restoration for the remaining reload actions is not adopted (G-18 PARTIAL does not mention losing filters/scroll). | v0.2:374-386; optional improvement, not blocking |
| M7 | First-run onboarding beyond Calibre-only banner | **PARTIAL (documented)** — UX-011 adds a Calibre setup banner; G-50 PARTIAL and §7 defers the full wizard. Broader conditions (no feeds/downloader untested) not covered. | v0.2:128-134, 368, 387; explicit deferral is acceptable |
| M8 | Palette Settings section deep links / admin-ops discoverability | **REMAINING (non-blocking)** — palette navigation lists "the 10 pages" only; the nine Settings anchors (`settings.html:23-47`) are not palette results (only the setup banner links `#section-calibre`). | Add a "Settings" palette group (9 anchors + Maintenance/Advanced). |
| M9 | Releases filter links preserve search, not only sort; truthful counts | **RESOLVED** — UX-023 preserves sort + "existing parameters", `?q=` bootstrap, G-15 PARTIAL documents loaded-row counts; recommend spelling out `q` in "existing parameters". | v0.2:237-245, 377 |
| M10 | Prioritized sort control or explicit deferral | **PARTIAL (documented)** — G-20 PARTIAL ("no server sort for prioritized flat view"); no sort control added; cards endpoint does support `sort` if wanted later. | v0.2:247-250, 379; acceptable documented partial |
| M11 | Discovery pending-tag state on sort/navigation | **RESOLVED** — pagination keeps all params; "Changing sort keeps pending tag chips (apply reads current picker state)". | v0.2:252-258 |
| M12 | Complete test-contract policy/matrix | **PARTIAL (non-blocking)** — §4 matrix is comprehensive and UX-018 pins literals, but it still omits two suites I flagged as affected: `tests/test_releases_loadmore_availability.py` (exact Load-More class strings/ids/counters/`Retry loading`) and `tests/test_post_date_sorting.py` (`release-sort-select`, `changeReleaseSort`, `sort=${…}`, `url.searchParams.set('sort'`). Also N2 below. | Add rows; no behavioural change to design. |
| M13 | Keyboard-shortcut discoverability (palette footer/help) | **REMAINING (minor)** — keys documented in UX-010, no footer/help entry. | Optional one-line palette footer; not blocking. |

**Blockers remaining: 0.** RESOLVED 6/13 (M1 core, M2, M3, M4, M9, M11); PARTIAL/documented 4/13 (M6, M7, M10, M12); REMAINING but non-blocking 3/13 (M5, M8, M13).

---

## 2. Lead-listed global resolutions — verification

| Item | Status | Evidence |
|---|---|---|
| Header 7 buttons; Downloads dropdown; only Overview an anchor | **RESOLVED & feasible** | UX-002/003 define 7 raw header buttons (4 nav triggers incl. Downloads, user, hamburger, palette) and Overview as the only top-level anchor; §4 updates `test_nav_structure.py:80/:191` (7 buttons) and `aria-controls` list; the in-progress reference markup already implements the Downloads dropdown/`nav-menu-downloads` and the test is updated to expect 7 (`test_nav_structure.py:191`). |
| Complete test matrix | **RESOLVED with 2 omissions** | §4 covers nav/ui-polish/background-tasks/perf/template/downloads/manual-pickup/releases-pagination + new suite; missing `test_releases_loadmore_availability.py` and `test_post_date_sorting.py` (M12). |
| Palette direct dispatch + Series/Releases query bootstraps | **RESOLVED** | UX-010 direct calls, no hand-off; UX-021 `?search=`, UX-023 `?q=`, each with one-shot bootstrap and `history.replaceState`. Feasible in Jinja (`request.query_params`) + inline JS without route changes. |
| Reachability reworded to 10 pages, JSON routes excluded | **RESOLVED (wording) / residual** | Non-negotiable #1 and §6 correct; the referenced "page matrix" is missing (N3). |
| More sheet is navigation-only | **RESOLVED** | UX-006: same 10 links/3 groups/auth block, ≥44 px, no actions/search/Poll Feeds; resolves the `test_nav_structure.py:156/198` and `:206-241` collisions. |
| Numeric overlay ladder + safe areas | **RESOLVED** | UX-007 z-table (30/40/45/50/60/70/80/90/95/100) + CSS vars + `viewport-fit=cover`; UX-016 safe-area/dvh/reduced-motion. One unassigned layer: the new offline banner (N5). |
| Quantified contrast table | **RESOLVED** | UX-015 pairs are plausible AA: `#e2e8f0` on `#0b0f19` ≈14.8:1 (≥12), `#94a3b8` on `#161e2e` ≈6.5:1 (≥6), slate-500-on-dark ≈3.5:1 correctly rejected (≥4.5 required), white on `brand-600` ≈4.7:1 (≥4.5), white on `brand-500` ≈3.7:1 correctly banned; `slate-850` defined. |
| Dialog/live-region/pinned-literal requirements | **RESOLVED with 1 pinned-literal conflict** | UX-013/014/018 complete; see N4 for `#tasks-toast` pinned by `test_background_tasks.py:163`. |

---

## 3. New requirements UX-019 and UX-022

| Requirement | Verdict | Notes / amendments |
|---|---|---|
| **UX-019 — Offline/degraded banner** | **ACCEPT with clarification** | Feasible frontend-only, high value (G-49-adjacent, complements UX-009). Define the failure predicate: count only network errors/5xx/timeouts, never 401/403/429, require `navigator.onLine !== false` to be ignored as a sole trigger; require two **consecutive cycles of the same poll** (not two different polls in one cycle); show once after first successful load only; hide on `online` or next success. Add its position/z to the UX-007 ladder (the in-progress implementation uses a sticky banner at top `z-35`, currently unlisted). |
| **UX-022 — Card-mode empty states** | **ACCEPT with scope fix** | Correct rule and feasible (templates own the empty rows; `.mt-table-empty` + suppressed `data-label` pseudo-elements). Scope list is incomplete: Feeds (UX-026 converts to cards; `colspan="8"` empty row at `feeds.html` empty state), Prioritized flat (UX-024; `colspan="6"` rows in `prioritized.html`) and JS-injected empty rows (Downloads `tbody.innerHTML = '<tr><td colspan=…>` strings, e.g. `downloads.html` queue/staging/audit) are not named. Also CAUTION: use a table-level selector (`table.mt-table-cards tbody tr > td[colspan]`) rather than adding a class to every row, so JS-generated empty rows are covered without touching test-pinned JS strings. Verify `data-label` on all 7 Series cells and all flat-release cells; the monitor toggle needs `aria-label` added (currently `title` only, `partials/series_table_row_chunk.html:4`). |

---

## 4. New non-blocking findings (errata/coverage; no design change)

1. **N1 — Dangling scope range.** Non-negotiable #5 says "Phase 1 = UX-001…UX-037" but v0.2 defines only UX-001…UX-031. Fix to `UX-001…UX-031` (or add UX-032…037 if intended). v0.2:20.
2. **N2 — §7/§4 pointer drift (5 places).** Non-negotiable #3 says the pinned-literal catalogue is "§7" (deferred table); the catalogue is §4. Same wrong pointer at UX-018 ("unless §7 says otherwise"), UX-002 ("document that in §7"), UX-022 ("partials in §7"), UX-030 ("listed in §7"). Fix all to §4. v0.2:16-17, 36, 188, 235, 307.
3. **N3 — "The page matrix" does not exist.** Non-negotiable #1 requires actions to be documented in "the page matrix"; add a §2 ten-page matrix (desktop path / mobile path / primary action) and one assertion for it in the new suite; otherwise reachability is normative but unverified. My round-1 per-page matrix can be reused verbatim.
4. **N4 — `#tasks-toast` pinned literal vs UX-014.** UX-014 says "MangaTasks uses the toast path only, not a second live region", but `tests/test_background_tasks.py:163` pins `tasks-toast` + `aria-live` as required strings. Either (a) keep `#tasks-toast` and announce task completion there (single live region), or (b) add a §4 row updating that test. Currently neither is stated.
5. **N5 — Offline banner layer + predicate.** UX-019 is not in the UX-007 ladder; add `offline banner` row (in-flow sticky under header, z 35, full-width max-w) and the failure predicate from §3 above. v0.2:76-94, 203-208.
6. **N6 — Multi-location badge id.** UX-003 names the badge `#nav-manual-pickup-count` while UX-004 renders badges in three places (desktop item, mobile-menu row, tab dot). Duplicate ids would break `getElementById`; specify a shared class (`.mt-pickup-badge`, `.mt-tab-badge`) + distinct ids (the in-progress reference already uses `nav-/mobile-/tab-manual-pickup-count` — design should say so). v0.2:43-56.
7. **N7 — Acceptance #5 says "at least 4/5 experts sign off"** while the project signed the app spec 5/5; decide 5/5 (or record the dissent process) so the design sign-off matches the established consensus rule. v0.2:343-344.
8. **N8 — Settings password feasibility wording.** UX-030's "blank = unchanged (omit the key)" is frontend-only and works because `POST /api/settings` uses `model_dump(exclude_unset=True, exclude_none=True)` (`main.py:4223`) and `/api/downloader/configure` only sets password when not `None` (`main.py:3561-3563`) — state this explicitly (no backend change) and keep the deliberate semantic change in the §4 row (currently points at §7, see N2).

---

## 5. Risks to watch at implementation (unchanged by design verdict)

* Keep the two source-test segments intact: `test_nav_structure._desktop_nav` (`:49-54`) and `_mobile_menu` (`:65-66`, ends at `<!-- Notification Toast Container -->`); palette and bottom bar must stay after that marker (the in-progress reference already places the palette after the toast container — verify on every future edit).
* Load-More/`sticky-load-more` ids, `updateLoadMoreUi()` calls, `Retry loading`, `initial_total`, `release-sort-select`/`changeReleaseSort` and URL-param expressions are pinned; mobile wrappers must not change them (M12 rows to add).
* `test_template_markup` requires no duplicate attributes per tag and exactly one `<script>` in `downloads.html` with modals after it; the card/navigator work must comply.
* Tailwind Play CDN JIT on injected markup is brittle: new components' classes should live in `base.html <style>` (the reference diff already does this with `.mt-*` CSS) and `lucide.createIcons()` re-run after injection.
* `inert` on header/main/footer needs a fallback for older browsers (focus trap already required); keep `aria-hidden` as backup.

---

## 6. Verdict

v0.2 resolves the round-1 **blocking** direction of every one of my amendments: the impossible 125-route promise is replaced with a 10-page/action reachability rule and a route-disposition section; palette actions are direct-dispatch with pinned-literal preservation; search bootstraps are specified on both target pages; the overlay ladder, contrast targets, dialog/live-region and gap-disposition requirements are all present and feasible in Jinja + inline JS with no new dependencies. The residual items above are wording/coverage corrections (M5, M8, M13, M12, N1–N8), not design blockers; none prevents implementing Phase 1 or the amended acceptance criteria.

**Blocking issues remaining: 0.**

**DESIGN VERDICT: AGREE**
