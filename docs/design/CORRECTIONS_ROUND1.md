# Round 1 consensus corrections — applied record (v0.10)

**Date:** 2026-11-08 · **From:** v0.9 (445 claims) · **To:** v0.10 (457 claims)
**Inputs:** all five independent round-1 reviews (`docs/design/reviews/round1_*.md`).
**Rule applied:** every reviewer DISAGREE was either reworded, adopted as a new claim, or (for Part 0)
rewritten. No claim was deleted. Reviewers were shown the full set of corrections in round 2.

## 1. Reworded claims (38)

| Claim | Correction theme | Raised by |
|---|---|---|
| REL-018 | `resolve_status` scope + IN_CALIBRE equal-rank tie | releases, series |
| REL-024 | RSS/HTML SSRF-rejected URLs return `[]` | series |
| REL-025 | end-of-results = 404 or parsed zero-row page; empty body retried | platform, series |
| REL-028 | provider-level chapter/LN filtering is silent | series |
| REL-038 | grouped-cache invalidation is not universal | series |
| REL-039 | Nyaa base URL precedence (per-feed > setting/env > default) | series |
| SER-004 | badge = length of 10-row fetch, not 8 rendered cards | series (self) |
| SER-018 | 60 ms self-scheduling chain; observer redundant; Load More error-only | releases, series (self) |
| SER-019 | match modal sends no `source` (name-ordered) | series (self) |
| SER-022 | library summary fields when available vs false | series (self) |
| SER-032 | fuzzy accepts scores strictly > 0.90 | releases |
| SER-034 | rotating cursor over newest 5000 active rows | releases |
| SER-037 | grammar fix ("merges duplicate trackers") | series (self) |
| SER-090 | task panel has no working cancel control | platform |
| DL-017 | refusal at/above `torbox_max_concurrent` | releases |
| DL-018 | zip also when size estimate unknown; streaming cap | releases |
| DL-021 | 3 attempts, 1 s then 2 s (4 s unreachable) | releases |
| DL-034 | no series-mapping control on Manual Pickup | downloads (self) |
| DL-041 | holds refused at/above cap | releases |
| DL-042 | persisted inventory copy, no scan on read | downloads (self) |
| DL-081 | promotion proxy = matched Calibre book count | downloads (self) |
| OPS-011 | basic mode = 401 Basic challenge; oidc-only redirect | platform (self) |
| OPS-019 | OIDC callback forces JIT upsert | series |
| OPS-030 | reset also clears backfill state keys | series |
| OPS-061 | whole tick shielded, not per job | ui |
| OPS-067 | reconcile keeps/promotes/resets, not "reset" | downloads, ui |
| OPS-079 | false-series repair re-keys selected ids without re-validation | series |
| OPS-087 | two schema transactions / implicit DDL commits | ui, series |
| OPS-090 | 41 seeded keys, 43 after startup markers | series |
| OPS-091 | separate JPEGXL-only pass runs regardless of `filter_non_manga` | series |
| UI-011 | task-panel Cancel never renders | downloads, platform, series, ui (self) |
| UI-020 | oidc redirect vs basic 401 vs none-open | platform |
| UI-090 | Check now refreshes the queue | ui (self) |
| UI-094 | `downloads:process` is never emitted; neither button binds | downloads, ui |
| UI-099 | tag modal edits seven fields | downloads, ui (self) |
| UI-101 | eight card sections + config/pickup/modals | downloads, ui (self) |
| UI-103 | "Fetch whole torrent" fetches unfetched files individually | downloads |
| UI-128 | Releases/Series do have retry affordances | platform |

## 2. New claims (12 → 457 total)

| ID | Behaviour |
|---|---|
| REL-043 | `/api/releases/series_releases` re-derives the group with search, ignores prioritized-only/sort → "Show all" on `/prioritized` can reveal non-prioritized releases |
| REL-044 | `/api/prioritized/releases` returns up to 500 rows, no pagination/total, priority post-sort |
| SER-093 | ownership = Calibre ∪ staged, bundle-expanded, 60 s cached staged map |
| SER-094 | false-series audit target can differ from repair target |
| DL-082 | `download_wanted_json` authority over missing list/matcher |
| DL-083 | qBittorrent release adds hard-code `manga`/`mangatrax`; settings inert on adds |
| DL-084 | `/api/downloader/queue` is a dead API surface |
| OPS-095 | `test-webhook` performs real outbound posts and returns per-channel results |
| OPS-096 | Calibre sync has no `data-task-kind` UI binding |
| OPS-097 | `download_status='QUEUED'` tolerated but never written |
| OPS-098 | reset seeds 5 groups ≠ fresh-DB 4 groups |
| UI-137 | page-declared task kinds must match emitted task kinds; `downloads:process` never emitted |

## 3. Prose / Part 0 fixes

* Part 0 (`00_overview.md`) rewritten: route partition (REL 30 / SER 24 / DL 42 / OPS 14 + debug 1 =
  111 JSON, 125 total), 45 writable settings keys, `current_task` → `background_tasks`/`TaskReporter`,
  auth exemption list, lifecycle diagram (download completion ≠ IN_STAGING; unignore direction),
  blacklisted rows stored not dropped, `NEW` is a real stored status, IN_CALIBRE equal-rank caveat,
  Manual Pickup has no series-mapping control, qBittorrent settings scoping, corrected claim
  references, and Part 5/index numbering.
* Part 1: removed non-existent `download_speed` column; Nyaa 404-or-zero-row stop rule; qBittorrent
  "ignored" statement scoped to release adds (queue filter still reads `qbit_category`).
* Part 2: added Q19 (false-series audit vs repair target).
* Part 3: §5.1 qBittorrent settings rows corrected.

## 4. Not adopted (recorded, no claim change)

* Merging duplicated claims (REL-021↔DL-032/033, REL-035↔DL-079/080, SER-074↔REL-042, etc.) — the
  registry rule forbids deleting claims; duplicates stay but are cross-referenced in Part 0/part texts.
* Renumbering/aliasing duplicated IDs — deferred to an editorial pass after v1.0.
* Any behavioural fix (bug fixes) — out of scope for the specification.
