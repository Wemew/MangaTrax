# Consensus Round 3 — final sign-off, expert-ui (v0.11)

**Reviewer:** EXPERT-5 (UI author; round-1 reviewer of Part 0 / Part 3 DL / Part 4 OPS)
**Task:** task-20 · **Date:** 2026-11-08
**Inputs verified:** `APP_DESIGN_SPEC_v0.11.md`, `FEATURE_REGISTRY_v0.11.md`, plus a full textual diff against
v0.10. Read-only: no spec, code or test file was modified; only this review was written.

## Scope of v0.11 (mechanical diff, v0.10 → v0.11)

Spec: 18 insertions / 19 deletions, 17 hunks — title/registry references (5 lines), the 4 Part-0 pointer
fixes, REL-043, SER-034, Part 4 §3.1 qbit rows (2 lines), Part 4 §4.4 QUEUED sentence, OPS-067, OPS-087,
OPS-096, and Part 5 §5.5 auth prose. Registry: 6 hunks — title + exactly REL-043 (line 66), SER-034 (104),
OPS-067 (321), OPS-087 (341), OPS-096 (350). No other claim or prose was touched.

## Confirmation table

| # | Edit | Verified content | Evidence | Result |
|---|---|---|---|---|
| 1 | **REL-043** corrected consequence | New text: only caller is the "Show all" handler on `/releases`; the same button rendered on `/prioritized` has no handler defined on that page and is inert. `showAllSeriesReleases` is defined only in `releases.html:291`, while the shared partial (`series_card_chunk.html:282`) is also rendered by `prioritized.html`, which defines no such function; clicking there is a no-op (console `ReferenceError`). | releases.html:291; series_card_chunk.html:282; prioritized.html includes the partial, no definition | CONFIRMED |
| 2 | **OPS-087** corrected commit facts | New text: first run commits twice — after the conditional one-time LN backfill (`database.py:936`, only when the marker is absent) and a final commit at `:995`; later runs commit once; schema work is not one atomic transaction. Code matches: only explicit commits in `init_db` are 936 (inside `if not marker:`) and 995. | database.py:920–936, 995 | CONFIRMED |
| 3 | **SER-034** precision | New text: rotating cursor over up to 5000 releases in status-tier/recency order, then filtered to active statuses. Code: `get_releases(limit=5000)` (default order = status CASE tier, `updated_at DESC`, `id DESC`), then filtered to `REQUESTED/DISCOVERY/IN_CALIBRE/NEW`, rotating window. | scheduler.py:3278–3301; database.py:2893–2903 | CONFIRMED |
| 4 | **OPS-067** precision | Now "promoted to IN_CALIBRE when the matched Calibre book count covers the highest wanted volume". Code proxy: `max(volumes) <= book_count`. | downloads_reconcile.py:62–72, 160 | CONFIRMED |
| 5 | **OPS-096** precision | Added parenthetical restating the missing `data-task-kind`. Series Sync button has no `data-task-kind` (series.html:25) while `_recorded_task` persists the task (main.py:1909–1930); re-attachment matches only `data-task-kind` (base.html:1074–1099). | series.html:25; main.py:1909–1930; base.html:1074–1099 | CONFIRMED (parenthetical is redundant but harmless; claim semantics unchanged) |
| 6 | **Part 0 pointer fixes (4)** | §3.2.2 → `(REL-018, SER-086)`; §3.2.6 → `(Part 4 §4.1, OPS-089, Part 2 Q4)`; §4.2 step 2 → `(SER-032, SER-037, SER-045…SER-047)`; §4.9 basic bullet → `(OPS-011, OPS-012)`. All targets now state the cited behaviour (SER-086 vetoes, SER-032/037/045–047 auto-link/identity, OPS-012 Basic challenge). | spec:313–315, 324–325, 358–359, 471–472; registry lines for those IDs | CONFIRMED |
| 7 | **Part 5 §5.5 auth prose** | Now the mode matrix: oidc = HTML redirect to login + `/api/*` 401 JSON; basic = every unauthenticated request 401 Basic challenge; none = open. Matches auth.py:1043–1077 and UI-020. | spec:4717; auth.py:1043–1077 | CONFIRMED |
| 8 | **Part 4 §4.4 QUEUED** | Now: values observed = IDLE, DOWNLOADING, PENDING_DEDUPE, COMPLETED, FAILED; QUEUED tolerated on read but never written (OPS-097). Matches repo-wide absence of a QUEUED writer. | spec:3600; OPS-097; main.py:1343 read-only | CONFIRMED |
| 9 | **Part 4 §3.1 qbit rows** | `qbit_category` = filter for the live queue listing only; release adds hard-code `manga`; `qbit_tags` = stored but unused (DL-083). Matches main.py:3795–3796, 606, 3556–3557; scheduler.py:3745–3749. | spec:3223; code as cited | CONFIRMED |

## No UI claim changed

* Registry UI block: 137 claims in v0.10 and 137 in v0.11; mechanical `Compare-Object` shows **0 changed
  lines** (no reword, no renumber, no truncation).
* Registry ↔ merged spec: all **457** claim texts are byte-identical to the v0.11 §9/§10 checklists
  (457 parsed on both sides, 0 missing, 0 mismatched). Section counts remain REL 44 / SER 94 / DL 84 /
  OPS 98 / UI 137 = 457.
* All 137 UI claims therefore stand as accepted in round 2 (including the eight agreed rewordings
  UI-011/020/090/094/099/101/103/128 and the new UI-137).

## Residual non-blocking editorial note (does not affect claims or this vote)

The Part 3 and Part 4 §2 feature-inventory "user-facing behaviour" cells listed in my round-2 review §4(b)
were not re-synced in v0.11 (the v0.11 edit list did not include them): e.g. Part 3 DL-017 "above", DL-021
"1/2/4 s", DL-034, DL-041 "Above", DL-042; Part 4 OPS-011 (unconditional 302), OPS-061, OPS-067
(reset-only), OPS-087 ("one transaction"), OPS-090 ("45+"), OPS-091, OPS-019. Their §9 checklist lines and
the registry are correct (verified above); this is supporting prose drift only. Recommend applying the
mechanical §2 re-sync in the v1.0 archival copy — no claim re-vote or third round required.

## Sign-off

All round-2 blockers are resolved as edited; every v0.11 edit is behaviourally accurate against the code;
the UI claims are unchanged; the registry and the merged specification are textually consistent on all 457
claims. No blocking disagreement remains.

GLOBAL VERDICT: AGREE
