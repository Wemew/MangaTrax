# Round 3 (final) sign-off — expert-platform (OPS author)

**Inputs:** `FEATURE_REGISTRY_v0.11.md` (457 claims) and `APP_DESIGN_SPEC_v0.11.md` (Part 0 + Parts 1/4/5 spot-verified), diffed against v0.10. Read-only; no spec/code edits.

## 0. Version integrity

| Check | Result |
|---|---|
| Claim count / IDs | **457**, unchanged from v0.10 (REL 44 · SER 94 · DL 84 · OPS 98 · UI 137); no additions, no removals (scripted diff). |
| Registry ↔ merged spec | All **457/457** §9 checklist lines are textually identical to the registry (0 differences). |
| Claim-level changes v0.10→v0.11 | Exactly **5** claims reworded: **REL-043, OPS-067, OPS-087, OPS-096, SER-034** (two more than the two named: OPS-067 and OPS-096 were also adjusted — both verified below). No other claim changed. |
| Editorial changes | Header/round-2 note, Part 0 pointers (2 fixed, 2 partially/incorrectly re-pointed — §3), Part 4 §3.1 qbit rows, Part 4 §4.4 download-status sentence, Part 5 §5.5 auth prose. No other edits detected in the diff. |

## 1. Confirmation table

| Item | v0.11 text | Code evidence | Verdict |
|---|---|---|---|
| **OPS-087** commit facts | “`init_db` … on a first run it explicitly commits twice — after the conditional one-time LN backfill (`database.py:936`, only when the marker is absent) and a final commit at `:995` — and once on later runs, so the schema work is not one atomic transaction.” | `init_db` (`database.py:405-997`) contains exactly two `conn.commit()` calls: `:936` inside `if not marker:` of the LN backfill (`database.py:920-938`) and `:995` (final). A later run skips `:924-936` and commits once. One connection, 13 tables unchanged. | **AGREE — exact.** |
| **REL-043** corrected consequence | “…its only caller is the ‘Show all’ handler on `/releases`, while the same button rendered on `/prioritized` has no handler defined on that page and is inert.” | `/api/releases/series_releases` is fetched only at `releases.html:301`, inside `showAllSeriesReleases()` (`releases.html:291`). The button markup lives in the shared `partials/series_card_chunk.html:279-284` (`onclick="showAllSeriesReleases(this)"`), and `prioritized.html` defines no such function → the button throws/inert there. | **AGREE.** |
| **SER-034** precision | “…rotating cursor over up to 5000 releases in status-tier/recency order, then filtered to active statuses…” | `scheduler.py:3279` `get_releases(limit=5000)`; `database.py:2901-2903` default ORDER BY status tier (`REQUESTED, DISCOVERY, IN_CALIBRE, other`) then `updated_at DESC`; `scheduler.py:3283-3287` filters to REQUESTED/DISCOVERY/IN_CALIBRE/NEW. | **AGREE.** |
| **OPS-067** (extra change) | “…promoted to IN_CALIBRE when the matched Calibre book count covers the highest wanted volume…” | `downloads_reconcile.py:62-72` `_volumes_in_calibre`: `max(volumes) <= book_count` (True when unknown/no count), then `:160-165` promote. | **AGREE.** |
| **OPS-096** (extra change) | Same claim plus a redundant trailing “(it has no data-task-kind binding)”. | `series.html:25` has no `data-task-kind`; `main.py:1909-1930` creates a recorded task. Content unchanged and correct; duplication is cosmetic. | **AGREE** (cosmetic only). |
| Part 4 §3.1 `qbit_category` | “filter for the live queue listing only; release adds hard-code `manga` (DL-083)” | `main.py:606-607` uses the setting only for `get_torrents(category=…)`; `main.py:3792-3796` `add_torrent(..., category="manga", tags="mangatrax")`; setting written at `:3556`. | **AGREE.** |
| Part 4 §3.1 `qbit_tags` | “stored but unused: release adds hard-code `mangatrax` (DL-083)” | Same add site (`:3796`); setting written at `:3557`, never read elsewhere (grep). | **AGREE.** |
| Part 4 §4.4 QUEUED sentence | “Values observed: IDLE (default), DOWNLOADING, PENDING_DEDUPE, COMPLETED, FAILED; QUEUED is tolerated on read but never written by current code (OPS-097).” | No writer sets QUEUED (`database.py:2674` → DOWNLOADING; callers IDLE/FAILED/MANUAL_PICKUP/COMPLETED/PENDING_DEDUPE); QUEUED only in read filters (`database.py:1190`, `main.py:1343`, `downloads_reconcile.py:30`). | **AGREE.** Minor residual: the enumeration omits `MANUAL_PICKUP`, a real written value (`scheduler.py:1675`) — non-blocking prose nit (§3). |
| Part 5 §5.5 auth prose | “in `oidc` mode HTML routes redirect to login and `/api/*` returns 401 JSON; in `basic` mode every unauthenticated request gets a 401 Basic challenge (`none` is open); …” | `auth.py:1043-1077`: exempt/authenticated allow first; `basic` (and oidc break-glass with a presented header) → 401 `BASIC_CHALLENGE` for every path (`:1056-1063`); oidc → `/api/*` 401 JSON (`:1067-1073`) else 302 `/auth/login?next=…` (`:1074-1077`); `none` returns allow earlier (`:1032-1033`). | **AGREE.** |
| Part 0 §3.2 inv.2 pointer | “(REL-018, SER-086)” | Correct claim for the precedence sentence is REL-018; SER-086 covers the veto half, not the equal-rank `IN_CALIBRE→REQUESTED` tie (`database.py:272-307`). | **Partially fixed** — pointer still imprecise; non-blocking (§3). |
| Part 0 §3.2 inv.6 pointer | “(Part 4 §4.1, OPS-089, Part 2 Q4)” | “Name joins, not ids” is not about connection policy; `OPS-089` is the SQLite connection/WAL claim. | **Not fixed** — `OPS-089` residual mispointer; non-blocking (§3). |
| Part 0 §4.2 step 2 pointer | “(SER-032, SER-037, SER-045…SER-047)” | Matches auto-link (`database.py:2020-2085`), link/merge (`:1872-2018`) and identity ladder. | **AGREE — fixed.** |
| Part 0 §4.9 pointer | “(OPS-011, OPS-012)” | OPS-012 is the Basic-challenge claim. | **AGREE — fixed.** |

## 2. Prior-round carry-overs re-checked

* My round-1/2 blockers (UI-011, UI-020, OPS-011, UI-128, SER-090, REL-025, Part 1 `download_speed`, Part 5 §5.5) are all resolved in v0.11 (the §5.5 prose now matches `auth.py:1043-1077`).
* The four Part 0 pointer fixes are 2 correct (§4.2 step 2, §4.9) and 2 imperfect (§3.2 inv.2 replaced with another imprecise pointer; inv.6 retained `OPS-089`). Both statements themselves are true; only the pointers are off.
* No new claim-level error was introduced by the v0.11 edits: all 5 changed claims verified against code above.

## 3. Residual non-blocking editorial notes (no claim affected)

1. `APP_DESIGN_SPEC_v0.11.md:314` — replace `SER-086` with `database.py:272-307` (or drop it); SER-086 does not cover the equal-rank tie.
2. `APP_DESIGN_SPEC_v0.11.md:324` — drop `OPS-089` from the name-joins pointer (keep `Part 4 §4.1`, `Part 2 Q4`; optionally add SER-032/SER-037).
3. `APP_DESIGN_SPEC_v0.11.md:3600` (my Part 4 §4.4) — the download-status enumeration should include `MANUAL_PICKUP` (`scheduler.py:1675`, `downloads_reconcile.py:30`).
4. `OPS-096` — the appended “(it has no data-task-kind binding)” duplicates the preceding clause; cosmetic.

None of these alters a claim's truth and none is a consensus blocker; they are recorded for the post-v1.0 editorial pass.

## 4. Verdict

All 457 claims are accepted as written, the v0.11 corrections are accurate against the code, and the remaining items are pointer/cosmetic prose only.

**GLOBAL VERDICT: AGREE**
