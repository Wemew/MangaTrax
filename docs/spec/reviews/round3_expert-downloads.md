# Consensus Round 3 (final) — expert-downloads sign-off

**Reviewer:** expert-downloads (author of Part 3 / DL-001…DL-084)
**Objects:** `docs/spec/FEATURE_REGISTRY_v0.11.md`, `docs/spec/APP_DESIGN_SPEC_v0.11.md` (vs v0.10/v0.9)
**Method:** diffed v0.10→v0.11 (registry and merged spec), then re-verified every changed claim against the current working tree (`backend/app/**`, `backend/app/templates/**`). No spec/code edits; this file is the only write.

## 1. Registry delta v0.10 → v0.11

Diff is exactly the expected editorial pass: title/counts unchanged (REL 44 · SER 94 · DL 84 · OPS 98 · UI 137 = **457**), **5 claims changed** and nothing else:
`REL-043`, `SER-034`, `OPS-067`, `OPS-087`, `OPS-096`. No claim added, removed, renumbered or truncated.

## 2. My two round-2 blockers — verified fixed

| Blocker | v0.11 wording | Code verification | Result |
|---|---|---|---|
| **REL-043** consequence ("Show all" on /prioritized) | "…its only caller is the 'Show all' handler on `/releases`, while the same button rendered on `/prioritized` has no handler defined on that page and is inert." | Endpoint `main.py:1471-1501` (search re-derive, cache bypass, case-insensitive match, slice, no `prioritized_only`/`sort`); sole handler definition `releases.html:291`; only `series_releases` caller `releases.html:301`; shared card emits the button `partials/series_card_chunk.html:279-286`; `/api/prioritized/cards` renders that same partial `main.py:1406-1424`; `prioritized.html` contains **0** occurrences of `showAllSeriesReleases`/`series_releases` (grep) | **RESOLVED** — claim now matches code exactly |
| **OPS-087** commits | "on a first run it explicitly commits twice — after the conditional one-time LN backfill (`database.py:936`, only when the marker is absent) and a final commit at `:995` — and once on later runs, so the schema work is not one atomic transaction." (implicit-commit parenthetical removed) | `database.py:405-408` (`with conn:` at indent 12 wraps only the cursor assignment at 16; next statement back at 12); `database.py:920-936` (conditional commit inside `if not marker:` at indent 20, inside the one-time LN backfill); `database.py:995` (final commit, indent 12); 13 `CREATE TABLE IF NOT EXISTS` | **RESOLVED** — claim now matches code exactly |

## 3. Other v0.11 claim changes — all accurate

| Claim | Verified |
|---|---|
| **SER-034** "rotating cursor over up to 5000 releases in status-tier/recency order, then filtered to active statuses" | `scheduler.py:3279` (`get_releases(limit=5000)`; default sort = status-tier CASE + `updated_at DESC`, `database.py:2893-2904`), `:3283-3288` (filter to REQUESTED/DISCOVERY/IN_CALIBRE/NEW and non-stable), `:3289-3310` (cursor rotation, batch size env default 2000 min 50) — **accurate** |
| **OPS-067** "promoted to IN_CALIBRE when the matched Calibre book count covers the highest wanted volume" | `downloads_reconcile.py:62-73` (`max(volumes) <= matched.book_count` proxy), promotion at `:155-165`; keep/promote/reset order at `:143-183` — **accurate** |
| **OPS-096** sync button has no `data-task-kind` binding | `series.html:25` (`#btn-sync-calibre`, onclick only), `base.html:516-539`, `main.py:1909-1930` — **accurate**; the appended "(it has no data-task-kind binding)" duplicates the same clause — cosmetic only, no sign-off impact (optional v1.0 copyedit) |

## 4. DL claims — unchanged, no editorial drift

- v0.11 DL block = **84** claims (DL-001…DL-084); content is **byte-identical to v0.10** (scripted per-claim diff: 0 changes) and **identical to my Part 3 checklist** (84 registry vs 84 spec, 0 differences, IDs contiguous).
- The v0.11 spec's Part 3 config table now reads `qbit_category`: "filter for the live queue listing only; release adds hard-code `manga` (DL-083)" and `qbit_tags`: "stored but unused: release adds hard-code `mangatrax` (DL-083)", and the download-status paragraph now states "`QUEUED` is tolerated on read but never written (OPS-097)" — both consistent with DL-083/OPS-097 and with my earlier corrections; no DL claim changed.
- No other Part 3 line was altered by the editorial pass (merged-spec diff shows only the Part 0 reference fixes, auth ref fix, the qbit rows and the status paragraph).

## 5. Round-2 Part 0 residual references — fixed too

Verified in v0.11 Part 0 (diff v0.10→v0.11):
- invariant 2 now cites `(REL-018, SER-086)` (was OPS-078) — `database.py:299-307`;
- invariant 6 now cites `(Part 4 §4.1, OPS-089, Part 2 Q4)` (SER-093 removed) — `database.py:377-403`;
- §4.2 step 2 now cites `(SER-032, SER-037, SER-045…SER-047)` (SER-019/020 removed) — registry IDs;
- basic-mode challenge now cites `OPS-012` (was OPS-013) — `auth.py:949,1060`.
All three round-2 non-blocking items are therefore closed; nothing else in Part 0 changed behaviourally (header note "incorporates every round-1 and round-2 correction" is accurate).

## 6. Final vote

| Section | Claims | Disagreements |
|---|---|---|
| Part 0 | — | 0 |
| REL | 44 | 0 |
| SER | 94 | 0 |
| DL | 84 | 0 |
| OPS | 98 | 0 |
| UI | 137 | 0 |
| **Total** | **457** | **0** |

Both round-2 blockers are resolved exactly as proposed, the 5 v0.11 claim edits are accurate against code, no DL claim text was altered or truncated, the three residual Part 0 references were fixed, and no new error was introduced anywhere in the corpus.

**GLOBAL VERDICT: AGREE**
