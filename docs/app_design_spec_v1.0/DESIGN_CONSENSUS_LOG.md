# MangaTrax Design Specification — Consensus Log

**Objective:** all five experts agree on **all features and all workings** of MangaTrax before the
finalized spec is saved and UI work begins.
**Final artefacts:** `docs/APP_DESIGN_SPEC.md` (v1.0, 793 KB) + `docs/FEATURE_REGISTRY.md`
(457 claims) + archive `docs/app_design_spec_v1.0/`.
**Rule:** a round closes only when every expert returns `AGREE` with zero blocking disagreements.

## Round 1 — v0.9 (445 claims, 5 independent reviews)

| Expert | Assignment | Verdict | Blocking items |
|---|---|---|---|
| expert-releases | SER + DL + registry + Part 0 | DISAGREE | 12 blockers (REL-018; SER-018/032; DL-017/018/021/041; Part 0 NEW/lifecycle/auth/blacklist/refs) |
| expert-series | REL + OPS + registry + Part 0 | DISAGREE | 8 blockers (Part 0 refs/route counts; IN_CALIBRE terminal; REL-018/039/038; OPS-079; OPS-019/030/087/090/091; UI-011) |
| expert-downloads | SER + UI + registry + Part 0 | DISAGREE | 3 claim blockers (UI-094/099/101) + OPS-067 + 6 Part 0 corrections |
| expert-platform | REL + UI + registry + Part 0 | DISAGREE | 5 blockers (UI-011/SER-090; UI-020/OPS-011; UI-128; Part 0 refs/routes/CURRENT_TASK; Part 1 `download_speed`) |
| expert-ui | DL + OPS + registry + Part 0 | DISAGREE | 6 claim blockers (OPS-061/067/087; UI-011/099/101) + 5 Part 0 corrections |

**Resolution:** 38 claims reworded, 12 new claims added (445 → 457), Part 0 rewritten, prose defects
fixed. Record: `docs/spec/CORRECTIONS_ROUND1.md`; evidence: `docs/spec/reviews/round1_*.md`. The full
feedback set from every reviewer was distributed to the whole panel for round 2.

## Round 2 — v0.10 (457 claims)

| Expert | Verdict | Remaining blockers |
|---|---|---|
| expert-releases | AGREE | none |
| expert-series | AGREE | none (editorial notes only) |
| expert-downloads | DISAGREE | REL-043 consequence clause; OPS-087 commit facts |
| expert-platform | AGREE | none (editorial notes only) |
| expert-ui | AGREE | none (editorial notes only) |

**Resolution:** both exact corrections proposed by expert-downloads were applied (REL-043, OPS-087),
plus the editors' non-blocking fixes (Part 0 pointers, Part 5 auth prose, OPS-067/096 precision,
SER-034 cursor wording, Part 4 qbit rows / QUEUED sentence). All reviewer feedback was redistributed.

## Round 3 — v0.11 (457 claims, final)

| Expert | Assignment | Verdict | Sign-off file |
|---|---|---|---|
| expert-releases | SER + DL + registry + Part 0 | **AGREE** (457/457) | `reviews/round3_expert-releases.md` |
| expert-series | REL + OPS + registry + Part 0 | **AGREE** (457/457) | `reviews/round3_expert-series.md` |
| expert-downloads | SER + UI + registry + Part 0 | **AGREE** (457/457) | `reviews/round3_expert-downloads.md` |
| expert-platform | REL + UI + registry + Part 0 | **AGREE** (457/457) | `reviews/round3_expert-platform.md` |
| expert-ui | DL + OPS + registry + Part 0 | **AGREE** (457/457) | `reviews/round3_expert-ui.md` |

**Result: 5/5 experts AGREE on all 457 claims and on Part 0. The stage is closed.**

## Final sign-off

We, the five-expert panel, confirm that `docs/APP_DESIGN_SPEC.md` v1.0 and
`docs/FEATURE_REGISTRY.md` v1.0 accurately describe every feature and working of MangaTrax at commit
`86a7bd8`, that all evidence-backed disagreements have been resolved, and that the document is the
baseline for the UI-enhancement phase.

| Expert | Domain | Verdict | Date |
|---|---|---|---|
| expert-releases | Releases, Feeds, Discovery, Prioritized | AGREE | 2026-11-08 |
| expert-series | Series, Calibre, Overview | AGREE | 2026-11-08 |
| expert-downloads | Downloads, Torbox/qBit, Staging, Tagger | AGREE | 2026-11-08 |
| expert-platform | Platform, Settings, Auth, Ops, Persistence | AGREE | 2026-11-08 |
| expert-ui | UI, Frontend, Interaction, Usability | AGREE | 2026-11-08 |

**Non-blocking editorial backlog (does not affect any claim):** a few Part 0 pointer labels, some
domain-part §2 inventory summaries lag their corrected §9 claims, and one cosmetic duplicate clause
in OPS-096. These are recorded in the round-3 reviews for a future copy-edit; the claim set and
registry are final.

**Next stage:** UI-enhancement design validation (`docs/UI_ENHANCEMENT_SPEC.md`), using the same five
experts with overlapping aspects, then implementation and final validation.
