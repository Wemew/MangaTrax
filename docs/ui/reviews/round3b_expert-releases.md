# UI-Enhancement Design Validation — Round 3b (B1 re-check) — expert-releases

**Reviewer:** expert-releases · **Task:** task-33 (B1 re-check) · **Design:** `docs/UI_ENHANCEMENT_SPEC.md` v0.3e.
**Scope:** verify only my one remaining blocker **B1** (Tasks widget vs settings save bar overlap on mobile) and the associated acceptance/test updates; re-confirm no regression in aspect A/C items.

---

## Confirmation table

| B1 acceptance point | Status | Evidence |
|---|---|---|
| Settings save bar sets `body.mt-save-open` while visible and removes it when clean | **RESOLVED** | UX-030: v0.3e:343-344 "Sticky save bar … while it is visible it adds `body.mt-save-open` (hidden/inert `#global-tasks`) and removes it when clean." |
| While `mt-save-open` is set, `#global-tasks` is hidden/inert/pointer-events-none, same rule as hero dialogs | **RESOLVED** | UX-007 Tasks row (v0.3e:95) now: "…hidden + inert whenever any dialog is open (`body.mt-modal-open`) **or whenever the settings save bar is visible** (`body.mt-save-open`, set by Settings while dirty), so it can never sit above a dialog scrim or overlap the save bar (45); the guarantee comes from mutual exclusion, not z-order." UX-007 rules (v0.3e:100): "`body.mt-save-open` hides/inerts `#global-tasks` exactly like `body.mt-modal-open`." |
| Same muting applies in the shared dialog helper (no regression to dialog behaviour) | **RESOLVED** | UX-013 (v0.3e:163): Tasks is "hidden while the settings save bar is visible (`body.mt-save-open`), disable toast action controls…" — the dialog-open path (`mt-modal-open`) is unchanged, including bottom bar/offline banner inert + toast action disabling + preserved live regions. |
| False "can never collide … (45)" sentence replaced by the mutual-exclusion guarantee | **RESOLVED** | v0.3e:95 explicitly states z=55 is above the save bar (45) and that the no-overlap guarantee comes from mutual exclusion, not z-order (my B1 wording). |
| New-suite assertion added | **RESOLVED** | §4 new-suite row (v0.3e:377) asserts "`body.mt-modal-open #global-tasks` and `body.mt-save-open #global-tasks` …"; acceptance #1 checklist includes it. |
| Acceptance #2 updated to verify the mutual exclusion | **RESOLVED** | Acceptance #2 (v0.3e:390): "…`body.mt-save-open #global-tasks` and `body.mt-modal-open` mutual exclusion; 320 px reflow contract present." |
| No design regression in aspects A/C | **CONFIRMED** | The rest of UX-007 (numeric ladder 30/40/45/50/60/70/80/90/55/100), safe areas, offline banner (z=36, predicate), Discovery ARIA tabs, nine partial icon buttons, corrected test matrix, G-27/G-43 ledger and the acceptance checklist are unchanged from the version I signed off on in round 3 except for the B1 lines above. The non-blocking errata listed in `round3_expert-releases.md` remain editorial and do not affect sign-off. |

**Blocker count: 0.**

**DESIGN VERDICT: AGREE**
