# UI-Enhancement Validation — Round 3 (final), expert-platform (aspects A = global shell & navigation, D = visual consistency & accessibility)

**Input:** `docs/UI_ENHANCEMENT_SPEC.md` v0.3 (438 lines, UX-001…UX-031), my round-2 review (blockers N1–N3 + non-blocking notes), and the current templates/tests for cross-checking. Read-only; no spec/code edits.
**Version integrity:** v0.3 folds every round-2 item; the requirement set is unchanged at 31 (UX-001…UX-031); the test matrix (§4), acceptance (§5), deferral list (§7) and ledger (§8) remain internally consistent. (Uncommitted implementation work exists in `backend/app/templates/base.html` / `tests/test_nav_structure.py`; I validated the spec text, and the shell already reflects several of these rules — noted where useful.)

## 1. Round-2 blockers → confirmation

| Blocker | v0.3 location | What changed | Verdict |
|---|---|---|---|
| **N1 — overlay ladder put Tasks above the sheet/palette; dialog inert scope incomplete; toast behaviour under a modal undefined** | UX-007:95 + rules :97-100; UX-013:159-162 | Tasks is now **z=55, explicitly below every backdrop (60/80)** and **hidden + inert whenever any dialog is open** (`body.mt-modal-open`); the helper inerts/hides `#global-tasks`, `#mobile-bottom-bar`, `#offline-banner` and disables the **interactive controls** inside `#toast-container`/`#toast-errors` while **keeping the live-region containers in the accessibility tree**, restoring everything on close; trigger/opener `aria-expanded` stays synced. §4:369 adds a new-suite assertion for `body.mt-modal-open #global-tasks`. | **RESOLVED** |
| **N2 — Discovery tabs had `role="tab"` without the rest of the pattern** | UX-025:282-285 (also UX-017:204-205) | Full ARIA tab pattern: `role="tablist"` container, per-tab `role="tab" aria-selected aria-controls="tab-content-…" id="tab-btn-…"`, panels `role="tabpanel" aria-labelledby`, roving `tabindex` with ArrowLeft/ArrowRight/Home/End, plus the `#discovery`/`#blacklist` hash restore. | **RESOLVED** |
| **N3 — active tab conveyed by colour only; pickup dot had no accessible state** | UX-005:66-68; UX-004:58-61 | Active tab = `aria-current="page"` **plus a non-colour cue** (700 weight + 2 px top indicator) + brand accent; the pickup dot is `aria-hidden="true"` and the Downloads tab's accessible name carries the count (`aria-label="Downloads, N manual pickups waiting"`), with the menu badges `aria-hidden` and the same information on their link; 0 exposes no badge. | **RESOLVED** |

No new blocking issues: the v0.3 edits are internally consistent (55 < 60/80; live regions kept for announcements while actions are disabled; tab pattern complete; state indicators have non-colour/accessible equivalents). Everything else in my round-1/2 scope (7-button header + full test matrix, ARIA menu keys, dialog helper, viewport-fit/safe-area/dvh/reflow, UX-015 contrast pairs, UX-014 live regions, UX-018 pinned literals, byte-checked test catalogue, 10-page reachability) remains as previously verified.

## 2. Round-2 non-blocking notes — folded vs remaining

**Folded:** the nine pre-existing partial icon-only buttons are now explicitly named and required to gain `aria-label`+`title` (UX-022:260-263, also UX-017:202-204); G-27 is now correctly marked **PARTIAL** with the migrated/excluded modal lists (§8:432); the pinned-literal governance pointer now says **§4** (non-negotiable 3:16); the no-light-theme/no-new-accent/no-new-font constraint is restored (non-negotiable 5:20-22); `aria-expanded` sync is part of UX-013; `test_release_download_state`/`test_background_tasks` rows and the new-suite assertion list were extended (§4:367-369); G-43/G-45/G-47 dispositions were corrected in the ledger.

**Remaining (non-blocking, editorial — fix during implementation, listed so nothing is lost):**
1. Three stale section pointers: UX-002:39 says “document that in **§7**” (the matrix is **§4**); UX-013:165 says “listed in **§8**” (the deferred-modal list is **§7**); UX-030:343 says “listed in **§7**” (the change is tracked in **§4**). Non-negotiable 3 already points at §4.
2. UX-018 catalogue could add the remaining test-pinned literals that are protected only implicitly by §4 rows: `id="settings-section-nav"` + `sticky top-20`, `aria-label="Open navigation menu"` / `closeMobileMenu()`, `/api/tasks?active=0` / `tasks-panel` / `tasks-active-count` (`#tasks-toast` is now covered in §4:368), `btn-check-feeds`/`icon-feed-check`, `btn-sync-calibre`/`icon-calibre-sync`, `btn-tag-staging`/`icon-staging-tag`, `nav-link-loading`, `link.closest('nav')`.
3. Non-negotiables are mis-numbered: two items are “5.” (visual language and implementation scope, :20 and :23); and “Phase 1 = UX-001…UX-031 **plus UX-019**” is redundant (UX-019 already in range). Renumber to 5/6 and drop the “plus”.
4. §5:389 still says “Expert validation **round 2** … reviewing **v0.2**” — should read round 3 / v0.3 (stale after this sign-off).
5. §4 has two separate `test_background_tasks.py` rows (:361 and :368) — merge them (they are compatible).
6. New-suite coverage: §4:369 and §5:376-379 assert the modal/tab-related behaviours but do not explicitly assert the new **tablist/roving-keys/`role="tabpanel"`** contract; add it so the N2 pattern cannot silently regress.
7. UX-022 table→cards AT note: keep the “verify table semantics when `display` changes (avoid `display:flex` on `table/tr`; keep `role=table/row/cell` or confirm browser behaviour)” implementation note — this was a risk note, not a design defect.
8. UX-014 wording: “`showToast(message, type)` keeps its existing two-argument signature and gains an optional third” — reword to “backward-compatible: existing two-argument call sites unchanged; optional third options object (`{retry, persist}`)”.

## 3. Design verdict

All three round-2 blockers (N1, N2, N3) are resolved against the code facts they depend on, the version introduces no new blocking issue, and the remaining items are editorial/coverage notes for the implementation phase.

**DESIGN VERDICT: AGREE**

Remaining blockers: **0** (8 non-blocking editorial notes above).
