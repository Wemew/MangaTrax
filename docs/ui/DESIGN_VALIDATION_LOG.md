# UI Enhancement — Design Validation & Implementation QA Log

**Scope:** Phase 1 UI enhancement of MangaTrax (`docs/UI_ENHANCEMENT_SPEC.md`, v0.3 → v1.0).
**Team:** the same five experts used for the application design specification; each validation round
assigned **two overlapping aspects** so every aspect was checked by at least two experts.

## Aspect assignments (overlap by design)

| Aspect | Covered by |
|---|---|
| A — global shell & navigation | expert-releases, expert-platform |
| B — mobile ergonomics & responsive conversion | expert-series, expert-downloads |
| C — feature access & discoverability | expert-releases, expert-series |
| D — visual consistency & accessibility | expert-platform, expert-ui |
| E — workflow continuity & feedback | expert-downloads, expert-ui |

## Round 1 — v0.1 (all five reviews)

| Expert | Verdict | Main blockers |
|---|---|---|
| expert-releases | DISAGREE | 125-route promise impossible; palette hand-off; nav contract; overlay ladder |
| expert-series | DISAGREE | header arithmetic; palette/search mechanics; card conversion; dashboard contract |
| expert-downloads | DISAGREE | qBit refresh not implementable; Manual Pickup reload continuity; nav contract |
| expert-platform | DISAGREE | header/test matrix; ARIA/dialog contract; safe-area/contrast; live regions |
| expert-ui | DISAGREE | header count; test matrix; More-sheet collision; overlay z-scale; missing gap ledger |

**Resolution (v0.2):** every blocker amended: 7 header buttons with Downloads dropdown; direct
dispatch palette + query bootstraps; 10-page reachability wording; bottom-sheet navigation-only; full
test matrix; quantified contrast; dialog/live-region contracts; gap ledger. All feedback was
dispersed to every expert (`docs/ui/reviews/round1_*.md`).

## Round 2 — v0.2

| Expert | Verdict | Remaining |
|---|---|---|
| expert-releases | AGREE (non-blocking errata) | — |
| expert-series | DISAGREE | N1 tasks z-order vs dialogs; N2 offline banner placement/predicate |
| expert-downloads | DISAGREE | N1 (same tasks z-order) |
| expert-platform | DISAGREE | N1/N2/N3: inert scope, incomplete ARIA tab pattern, colour-only state |
| expert-ui | DISAGREE | R1 test matrix; R2 z-order; R3 downloads card mode; R4 ledger/acceptance |

**Resolution (v0.3):** Tasks z=55 + hidden/inert under `body.mt-modal-open` and `body.mt-save-open`;
all non-dialog chrome inert while dialogs are open with live regions preserved; offline banner
placement/predicate; full ARIA tab pattern; non-colour active cues + accessible pickup names;
downloads card mode/five JS builders; corrected test matrix; verifiable acceptance criteria;
complete G-01…G-50 disposition ledger.

## Round 3 — v0.3/v0.3e (final design sign-off)

| Expert | Verdict | Evidence file |
|---|---|---|
| expert-releases | **AGREE** | `round3b_expert-releases.md` |
| expert-series | **AGREE** | `round3_expert-series.md` |
| expert-downloads | **AGREE** | `round3_expert-downloads.md` |
| expert-platform | **AGREE** | `round3_expert-platform.md` |
| expert-ui | **AGREE** | `round3_expert-ui.md` |

**Result: 5/5 design sign-off.** Implementation then proceeded against v0.3e.

## Implementation QA

| Pass | Verdict | Outcome |
|---|---|---|
| QA 1 (`IMPLEMENTATION_REVIEW_1.md`) | FAIL | H1 More tab opened-then-closed (blocker), H2 lightbox not migrated, H3 series off-screen drain, H4 dashboard download incomplete, H5 badge double-fetch/names; 15 medium findings |
| QA 1 fixes | applied | H1–H5 fixed in base/index/series; M1–M14 largely fixed |
| QA 2 (`IMPLEMENTATION_REVIEW_2.md`) | FAIL (0 functional blockers) | H1–H5 all FIXED; 9/15 M fixed; six acceptance-level deltas: M1/M3/M6/M9/M10 + low M15 |
| QA 2 fixes | applied | non-OK polls count as failures (401/403 ignored); contrast raised (`text-slate-500`→`text-slate-400`, palette tokens); toast `{action,retry}` support; series inline retry; palette error state; 44 px controls; migrated modals z-90 |
| QA 3 (`IMPLEMENTATION_REVIEW_3.md`) | PASS | final verification of the fixes; full suite + E2E green |

## Implementation scope delivered

* Global shell: skip link, 7 header controls, Downloads dropdown (Downloads + Manual Pickup),
  command palette with direct action dispatch + `/series?search=` / `/releases?q=` bootstraps,
  mobile bottom tab bar + bottom-sheet navigation, overlay z-ladder + safe-area/viewport-fit,
  shared dialog helper (MangaModal) with inert/focus-trap/scroll-lock, status/alert live regions,
  escaped toasts with Retry, visibility-aware polling, offline banner, live Manual Pickup badges,
  non-colour active cues, reduced-motion support, contrast tokens (`slate-850` defined).
* Dashboard: quick actions, setup/pickup banners, in-place download state polling.
* Series: server-side debounced search with cancellation/URL state/empty state, responsive cards,
  a11y action buttons, off-screen chain gating.
* Releases/Prioritized: sort-preserving links, mobile filter pills, card tables, `?q=` bootstrap.
* Discovery: filter-preserving pagination, full ARIA tabs with hash state, mobile filter collapse,
  keyboard tag picker.
* Feeds: dialog-semantic add modal, mobile cards.
* Downloads: eight-section navigator with hash/scroll-spy, safe hash-keyed qBittorrent refresh,
  card tables, tag-modal dialog/dirty guard.
* Manual Pickup: non-destructive refresh bar + discard, stored-series prefill, saving/saved/revert.
* Logs: event/actor filters combined with search.
* Settings: mobile chip/dirty lifecycle, discard + suppression, Advanced-anchor hiding, blank
  qBittorrent password (omitted when unchanged), collapsible cards.

## Deferred (documented in spec §7)

G-08 error pages, G-26 feed editing, G-31 confirm idiom unification, G-38 log history, G-39
per-section save, G-42 maintenance regroup, G-43 result-surface unification, G-44 field validation,
G-46 undo, G-47 full URL state, G-49 offline vendoring, full onboarding wizard, remaining modal
migrations. Each reason is recorded; none is silently dropped.

## Verification

* Script suites: **86/86 PASS** (assertions compiled optimize=0; the sandbox `tempfile.mkdtemp`
  limitation required a local test-runner temp shim documented in `scratch/run_tests_sandboxfix.py`;
  the production code/test contract is unchanged).
* E2E: **77/77 PASS**.
* New suite: `tests/test_ui_enhancements.py` — 16 subtests, 124 live assertions.
* Diff: template/CSS/JS only (15 files), no backend route/schema changes; one nav test updated by
  design, new test suite added.
