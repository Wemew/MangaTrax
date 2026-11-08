# UI-Design validation — Round 3 (final sign-off), expert-downloads (aspects B + E)

**Reviewer:** expert-downloads. **Object:** `docs/UI_ENHANCEMENT_SPEC.md` v0.3e (446 lines) + the shipped implementation of my files (`backend/app/templates/downloads.html`, `backend/app/templates/manual_pickup.html`) and the Lead-owned shell (`base.html`, read-only for me).
**Inputs:** v0.3e §1 (UX-007/008/013/019), §3.7 (UX-027), §3.8 (UX-028), §4 test matrix, §5 acceptance, §8 gap ledger; my round-2 review (`round2_expert-downloads.md`); current templates and the `test_ui_enhancements.py` downloads/pickup assertions. No code/spec edits; this file is the only write.

## 1. Round-2 blocker and residuals — confirmation

| Round-2 item (mine) | v0.3e / implementation | Verdict |
|---|---|---|
| **N1** Tasks widget above dialogs and outside inert scope | UX-007: Tasks moved to **z=55**, below backdrops (60/80); CSS `body.mt-modal-open #global-tasks, body.mt-save-open #global-tasks {visibility:hidden;opacity:0;pointer-events:none}` (`base.html:194-195`); `MangaModal.open` adds `mt-modal-open` and inerts `#main-content`, header, footer, **`#global-tasks`, `#mobile-bottom-bar`, `#offline-banner`** (`base.html:1412-1443`). Settings adds/removes `body.mt-save-open` while dirty (`settings.html:1594-1623`). Mutual exclusion, not z-order — exactly the guarantee requested. | **RESOLVED** |
| Dialog inert scope / live regions | Toast action buttons disabled via pointer-events (`base.html:152`) while `#toast-container`/`#toast-errors` stay in the a11y tree with `role=status/alert` (`base.html:450-451`); all controls restored on close. | **RESOLVED** |
| **N2** G-29 ledger over-claim | UX-027 now states "G-29 recorded as PARTIAL: existing rows update live, new qBit torrent only on next full refresh" (§3.7) and §8 ledger matches (`G-29 PARTIAL`). | **RESOLVED** |
| **N3** reload-path count | UX-030 now says "three intentional reload paths plus one redirect". | RESOLVED |
| **N4** navigator anchor ids | UX-027 names all 8 ids (`#dl-diagnostics … #dl-audit`) and requires awaiting/cleanup/audit wrappers; implementation has them. | RESOLVED |
| **N5** dirty-refresh discard path | UX-028 adds "Discard my edits and refresh" to the changed bar (explicit, no silent loss). | RESOLVED (design) — implementation follow-up below |
| **N7** toast Retry mechanism | UX-014 now specifies optional third `{retry: fn, persist: true}` or `showErrorToast`, keeping `showToast`'s signature. | RESOLVED |
| **N8** deferred modal set | §7 names `#manual-match-modal`, `#staging-series-match-modal`, `#pickup-match-modal` explicitly as deferred with reason. | RESOLVED |

## 2. Round-3 design verification (B/E + N1)

| Item | Design position | Verdict |
|---|---|---|
| Overlay ladder (UX-007) | Numeric ladder incl. Tasks 55 below all scrims, save bar 45, offline banner 36, toasts 100; in-flow mobile Load-more; safe-area + `viewport-fit=cover`; mutual-exclusion rules instead of relying on z-order. | **ACCEPT** — stale "Tasks above modals" contradiction is gone |
| Dialog inert scope (UX-013) | Helper inerts page chrome, **bottom bar, offline banner and tasks**, disables toast actions, keeps live regions; Escape only topmost; focus return. | **ACCEPT** |
| Offline banner (UX-019) | Single `#offline-banner` role=status polite, z=36 above mobile bottom bar (fixed) / in-flow desktop; predicate = offline OR two consecutive failures; aborted/auth-401 excluded; resets on success. | **ACCEPT** (implementation predicate nuance below) |
| Downloads navigator (UX-027) | Page-ordered chips, 8 named anchors, scroll-spy `aria-current="true"`, hash persisted, mobile horizontal scroll, 44 px. | **ACCEPT** — matches shipped markup |
| Diagnostics collapse | Mobile-only default-collapsed toggle with `aria-expanded`/`aria-controls`, desktop always expanded. | **ACCEPT** |
| qBit safe refresh (UX-027) | 10 s visibility-aware; value/text cells only on rows matched by `data-torrent-hash`; no add/remove/reorder; no control replacement; engine/live/degraded guard; stale chip + Retry; `aria-busy`; Torbox literals/timers untouched; duplicate qBit ids fixed. | **ACCEPT** — shipped implementation matches, except the id prefix wording (follow-up 1) |
| qBit row id format | Torbox `id="torbox-row-<release_id>"`, qBit `id="qbit-row-<hash>"`, both keep `data-release-id`/`data-torrent-hash`. | **ACCEPT (design)** — shipped markup uses `torbox-row-<hash>` for qBit; see follow-up 1 |
| Card-mode + five JS builders | `mt-table-cards` + `data-label` in `renderTorboxQueue` (via `_torboxRowHtml`), `renderInventory`, `renderAudit`, `renderStagingDuplicates`, `renderFailedDocuments` and server cells; `mt-table-empty` colspan rows; 44 px actions; `scope="col"` headers kept. | **ACCEPT (design)** — data-labels shipped in all five builders; two implementation gaps in follow-ups 2-3 |
| Manual Pickup (UX-028) | 15 s visible-only poll; never reload while focused/dirty/modal/task; "Pickups changed — Refresh" + "Discard my edits and refresh"; badge refresh; `data-series-name` prefill + post-apply copy; G-36 mitigated; saving/saved + revert + 44 px inputs; pinned strings kept. | **ACCEPT (design)** — shipped behaviour matches except the explicit discard button (follow-up 4) |
| Pickup accessible names | Volume inputs, per-file volume inputs and checkboxes carry `aria-label`; buttons are text-labelled; match modal close/search/new-title labelled. | **ACCEPT** |
| Acceptance checklist (§5) | Includes static z/offset contract, `body.mt-save-open #global-tasks` mutual exclusion, 320 px reflow, reduced-motion, live regions, plus new-suite assertions for qBit value-only refresh. | **ACCEPT** — see follow-up 5 for the 320 px "contract present" wording |

## 3. Implementation follow-ups for QA pass 2 (not design blockers)

These are code-vs-v0.3e deviations in my two files / the shared helper; the design itself is accepted. They are test-invisible today, so they need a QA fix + assertion rather than a design change:

1. **qBit row id prefix.** `downloads.html:273` renders `{% if engine == 'qbit' %}id="torbox-row-{{ t.hash }}"{% else %}id="torbox-row-{{ t.release_id }}"{% endif %}` but UX-027 specifies `id="qbit-row-<hash>"` for qBit rows. Fix: use `id="qbit-row-{{ t.hash }}"` in the qBit branch (Torbox rows unchanged) and add a source assertion in `test_ui_enhancements.py`.
2. **Failed-documents table is not card-converted.** `downloads.html:212` is `<table class="w-full text-xs">` with no `mt-table-cards` class, although `renderFailedDocuments` already emits `data-label`s; on phones the failed-docs rows stay a table. Fix: add `mt-table-cards` (matching UX-027's five-builder card-mode contract).
3. **Missing `scope="col"` headers.** Downloads tables have no `scope="col"` (and neither do the series partials); UX-027 requires "keep `scope='col'` headers" in card mode. Fix: add `scope="col"` to the `<th>`s of the converted tables (queue/inventory/failed/staging/dup/audit) and assert it in the new suite.
4. **Manual Pickup discard button.** UX-028's changed bar must offer "Discard my edits and refresh"; the shipped bar (`manual_pickup.html:26-33`) has Refresh + Dismiss, and `pickupReloadNow` (`:366-372`) falls back to `confirm('Discard unsaved volume changes and refresh?')`. Fix: add the explicit discard action to the bar (keep the confirm as a second line of defence) so the dirty path needs no browser dialog.
5. **Page-poll failure predicate (UX-019).** UX-019 says aborted requests and auth 401/403 redirects do not count, and only `/api/tasks` failures feed the global counter (page polls may use their own twice-failed rule). My page polls call `window.mtPollFail()` on any `!res.ok` (including 401/403) in `refreshDownloadsNow`, `refreshQbitQueueNow` and `pollPickupChanges`; base's own tasks poll only calls it on fetch exceptions (`base.html:1305-1316`). Fix: in page polls, call `mtPollFail` only for network errors / 5xx (or ignore 401/403 entirely) and keep `mtPollOk` on success, so an auth redirect cannot raise "Connection lost".
6. **320 px reflow "contract present" (acceptance §5.2).** No explicit 320 px rule/assertion exists; the behaviour comes from card mode (≤768 px) plus wrap utilities. QA should either add a documented 320 px assertion in the new suite or reword the acceptance line to "card mode + wrap at ≤768 px covers 320 px".
7. **Helper-set dialog semantics (base, UX-013).** Shipped `MangaModal.open` does not set `role="dialog"`/`aria-modal="true"` itself; converted modals carry it in markup (my `#tag-modal` does). Base owner should set them defensively for future migrations.

## 4. Final verdict

All round-2 blockers and non-blocking items in my areas are resolved by v0.3e; the round-3 overlay/inert/offline/qBit/card-mode/pickup changes are coherent, implementable in the existing inline-JS model, and consistent with the pinned test contracts (the full suite passes 86/86). The seven follow-ups above are implementation details in my files/shared helper for QA pass 2 — none requires a design change and none re-opens aspects B/E.

**DESIGN VERDICT: AGREE** — expert-downloads signs off v0.3e (5/5 design sign-off from my side). Follow-ups 1-7 are handed to QA pass 2.
