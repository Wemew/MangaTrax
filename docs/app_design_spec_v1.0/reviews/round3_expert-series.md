# Consensus Round 3 — final sign-off by expert-series (v0.11)

**Reviewer:** expert-series (Part 2 / SER author) · **Task:** task-17
**Scope:** final confirmation of v0.11 = v0.10 + exactly (a) REL-043 consequence, (b) OPS-087 commit facts, (c) non-blocking editorial fixes (Part 0 pointers, Part 5 auth prose, SER-034 cursor wording, Part 4 qbit/download-status rows).
**Inputs read:** `docs/spec/FEATURE_REGISTRY_v0.11.md` (493 lines / 457 claims), `docs/spec/APP_DESIGN_SPEC_v0.11.md` Part 0 (lines 35-582) + the v0.10→v0.11 diffs of the merged spec, and the amended claim blocks in Parts 1/2/4/5.
**No spec or code file was edited; only this review was written.**

## 1. Change-scope verification

Mechanical diff of v0.10 → v0.11 shows exactly the announced edits and nothing else:

| Artefact | Changed lines | Result |
|---|---|---|
| `FEATURE_REGISTRY_v0.11.md` | 12 diff lines (title, REL-043, SER-034, OPS-067, OPS-087, OPS-096) | 457 claims, REL 44 · SER 94 · DL 84 · OPS 98 · UI 137, IDs contiguous, no duplicates |
| `APP_DESIGN_SPEC_v0.11.md` | 37 diff lines: version/companion references, Part 0 pointers, REL-043 + SER-034 + OPS-067 + OPS-087 + OPS-096 checklist rows, Part 4 qbit settings rows + download-status row, Part 5 CSRF/auth prose | all within (a)-(c); no unrelated content drift |

SER block check: exactly **SER-034** changed in the SER section (94/94 claims still present); no truncation or regression.

## 2. Confirmation table

| # | Item to verify | Verdict | Evidence |
|---|---|---|---|
| 1 | **REL-043** corrected consequence: the endpoint's only caller is the `/releases` "Show all" handler; the same button on `/prioritized` has no handler and is inert | **AGREE** | `backend/app/templates/releases.html:291` (`function showAllSeriesReleases`) and `:301` (fetch `/api/releases/series_releases`); `backend/app/templates/partials/series_card_chunk.html:282` (shared button); `backend/app/templates/prioritized.html:128` includes the partial but defines no `showAllSeriesReleases` (grep: 0 matches), so the click handler is undefined; endpoint behaviour `backend/app/main.py:1471-1501` (search re-derive, case-insensitive match, slice, no prioritized-only/sort) |
| 2 | **OPS-087** corrected commit facts: first run commits twice (conditional LN backfill commit at :936 + final at :995), later runs once | **AGREE** | `backend/app/database.py:920-938` — LN backfill only when `ln_release_backfill_done` marker is absent, `conn.commit()` at `:936`; final `conn.commit()` at `:995`; grep inside `init_db` (lines 405-997) finds exactly those two `conn.commit()` calls, so later runs commit only at `:995`; schema is therefore not one atomic transaction |
| 3 | **SER-034** new wording: rotating cursor over up to 5000 releases in status-tier/recency order, then filtered to active statuses | **AGREE** | `backend/app/scheduler.py:3279` (`db.get_releases(limit=5000)`), `:3283-3287` (filters `REQUESTED/DISCOVERY/IN_CALIBRE/NEW`), `:3292-3301` (batch window + rotating cursor); ordering `backend/app/database.py:2896-2903` (status tier then `updated_at DESC`) |
| 4 | **Part 0 pointers** (my round-2 P0-2.1…P0-2.4) | **AGREE** | inv 2 now `(REL-018, SER-086)` (`APP_DESIGN_SPEC_v0.11.md:314`); inv 6 now `(Part 4 §4.1, OPS-089, Part 2 Q4)` (`:324`; Part 4 §4.1 states "No FOREIGN KEY constraints… referential integrity is application-level" at `:3562-3582`); §4.2 step 2 now `(SER-032, SER-037, SER-045…SER-047)` (`:358`); §4.9 now `(OPS-011, OPS-012)` (`:471`). All four previously wrong labels are gone and the replacements match the referenced claims |
| 5 | **OPS-067** amended wording (book count covers highest wanted volume) | **AGREE** | `backend/app/downloads_reconcile.py:62-72` (`max(volumes) <= book_count`), promotion branch `:155-165`, re-point branch `:140-152`, reset branch `:180-183`; 300 s cadence `scheduler.py:5221-5232` |
| 6 | **OPS-096** amended wording | **AGREE (non-blocking editorial note)** | `backend/app/main.py:1909-1930` (synchronous, persisted task); `backend/app/templates/series.html:25` (no `data-task-kind`); `backend/app/templates/base.html:516-539` (local click-handler disable). The parenthetical "(it has no data-task-kind binding)" duplicates the clause but is not factually wrong; no behavioural correction is needed for sign-off |
| 7 | Part 4 qbit settings rows and download-status row (editorial fixes) | **AGREE** | qbit literals `backend/app/main.py:3795-3796`, `scheduler.py:2420-2421,3748-3749,4647-4648`; `qbit_category` only read for status filter `main.py:606`; no `qbit_tags` read (grep); `QUEUED` never written (grep: only readers `main.py:1343`, `database.py:1190-1191`, `downloads_reconcile.py:30`) |
| 8 | Part 5 CSRF/auth prose fixed | **AGREE** | `backend/app/auth.py:1007-1019,1021-1077,1051-1063` (oidc redirect/JSON 401, basic 401 challenge, none open) |
| 9 | Full SER self-check | **AGREE** | SER 94/94 present; only SER-034 reworded; the other 93 lines unchanged from v0.10 and already verified in rounds 1-2 (no truncation) |
| 10 | Registry totals / integrity | **AGREE** | 457 claims = 44+94+84+98+137; contiguous IDs; 0 duplicates (scripted) |

## 3. Remaining blockers

**None.** All round-1 and round-2 findings are incorporated; the v0.11 amendments are factually correct against commit `86a7bd8`. The only retained editorial nit is OPS-096's redundant parenthetical (row 6), which does not affect its correctness or the specification's consensus.

**GLOBAL VERDICT: AGREE**
