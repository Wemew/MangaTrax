# MangaTrax Feature Recommendations (M2 — Brainstorm Additions)

> Companion to `PROJECT.md` feature 14 (M2 milestone deliverable).
> Grounded in the code as of branch `audit/m1-m5` (M1 remediation, M3 security
> remediation, P3a naming split, P4 chapter guard all landed). Every proposal below
> names the modules a change would actually touch, so the effort/risk estimates are
> engineering estimates against *this* codebase, not generic guesses.

## How this list was produced

1. Read `PROJECT.md` (module boundaries + feature inventory) and the real modules:
   `main.py` (routes/UI), `database.py` (schema + alias/re-key repair),
   `scheduler.py` (feed poll → matcher → tagger pipeline), `tagger/cbz_tagger.py`,
   `downloader/qbit.py`, `providers/*`, `matcher.py`, `parser.py`, `schemas.py`,
   `security.py`, `auth.py`.
2. Kept only ideas where the existing code *already almost does the work* — the
   prefix "an operator would hit this within a week of real use" — and dropped
   speculative platform work (e.g. "add a mobile app") that has no codebase anchor.
3. Ranked by (user value × confidence) ÷ (effort × risk). The top five are the M2
   implementations; everything below them is deliberately deferred with a reason.

### The single most important finding

The P3a fix (split stable matching key from AniList display title) fixed **future**
tagging, but CBZ files already written to disk keep the wrong `<Series>` forever.
That is real, silent, user-visible data corruption: Calibre matches a book to a
series by the `<Series>` value, so a file tagged `Delicious in Dungeon` while the
library calls the series `Dungeon Meshi` creates a second, wrong series on import —
or silently never matches. Nothing in MangaTrax detected it. That is why the CBZ
audit/repair ranks above every new "feature" in this report.

## Ranked summary

| # | Proposal | User value | Effort | Risk | Decision |
|---|----------|-----------|--------|------|----------|
| 1 | CBZ `<Series>` audit + explicit repair (data repair for the old rename bug) | **Critical** — repairs already-broken files | M | Low (read-only audit; per-file explicit write) | **Implemented (M2)** |
| 2 | Tagger preview + manual tagging (PROJECT.md 16) | High — hand-tag volume 1 of a new series | S/M | Medium (filesystem writes) → contained by scan-only filenames + path guards | **Implemented (M2)** |
| 3 | Live download progress API + non-blocking degrade (PROJECT.md 15) | High — the page is unusable when qBit hangs | S | Low | **Implemented (M2)** |
| 4 | Unified search with AniList alias resolution (PROJECT.md 17) | High — query either spelling, find the series | S | Low (read-only) | **Implemented (M2)** |
| 5 | Expose `discard_unmarked_chapters` in the settings path | Medium — the behaviour is currently env-var-only | XS | Low | **Implemented (M2)** |
| 6 | Provider health + per-feed backoff dashboard | Medium | S/M | Low | Deferred — see §6 |
| 7 | Durable notification outbox + retry | Medium | M/L | Medium | Deferred — see §7 |
| 8 | Duplicate-release identity (infohash) across feeds | Medium | M | Medium | Deferred — see §8 |
| 9 | Staging → Calibre import automation | High | L | High | Deferred — see §9 |
| 10 | Cover-cache negative caching / LRU | Low | XS | Low | Deferred — see §10 |
| 11 | Chapter-level tracking | Medium | L | High | Deferred — see §11 |
| 12 | Multi-library / multi-root Calibre | Low (single-library homes) | L | Medium | Deferred — see §12 |
| 13 | Bearer-token API auth for scripts/mobile | Medium | M | Medium | Deferred — see §13 |
| 14 | Reading-progress sync (Kavita/Komga/BookOrbit) | Medium | L | Medium | Deferred — see §14 |
| 15 | Prometheus metrics endpoint | Low | S | Low | Deferred — see §15 |

Effort: XS < 1h, S = 1 session, M = a few sessions, L = multi-day.
Risk includes regressions to the stable-key naming contract, auth surface, and the
embedded-interpreter test contract (all suites run inside Calibre's Python).

---

## 1. CBZ `<Series>` audit + explicit repair — IMPLEMENTED

**Problem.** `series_tracking.name` is the stable matching key; ComicInfo tagging
now uses it (P3a). Files tagged by the old build inherited AniList display titles.
`tagger/cbz_tagger.py` had no reader for `ComicInfo.xml` and no way to rewrite an
entry (it only appends), so there was no path to detect or fix them.

**What shipped.**

- `GET /api/tagger/audit` — read-only. Scans `staging_dir`, parses each CBZ's
  `ComicInfo.xml`, and returns per file: `file`, `current_series`,
  `suggested_series`, `status`, `series_match`, `changes`, `safe_to_repair`,
  `reason`, plus a summary (`scanned`, per-status counts, `files_with_changes`).
- `POST /api/tagger/repair-series` — `{"files": [<staged names>], "repair_writer":
  true, "repair_publisher": true}`. There is **no** "all files" mode: only the
  archives named in `files` are ever opened for writing (max 200/request).

**How safe-to-repair is distinguished from ambiguous** (the important design bit):

- **`ok`** — `<Series>` equals a tracked/Calibre name exactly (or a file whose
  Series is right but Writer/Publisher are provably wrong is upgraded to
  `repairable` and only those elements are rewritten).
- **`repairable`** — the correct name is provable, never guessed:
  - `series_match = "normalized"`: matches a tracked/Calibre name up to
    case/punctuation, and tracking/Calibre spelling *is* the matching key; or
  - `series_match = "anilist_alias"`: the value is an **authoritative** AniList
    canonical/romaji alias (`manga_metadata_cache.source == 'anilist'`, non-empty
    canonical title) claimed by **exactly one** tracked/Calibre series. That is
    the exact fingerprint of the old rename bug. The evidence source is the same
    alias index that `database.repair_rekeyed_series_names()` uses, so the two
    repairs cannot disagree.
- **`ambiguous`** — the alias is claimed by ≥ 2 tracked series. Refused: two
  distinct series genuinely can share an AniList alias, so no winner is picked.
- **`unknown_series`** — no tracked/Calibre name and no authoritative alias. No
  name can be derived, so it is reported for manual tagging rather than guessed.
- **`missing_series` / `untagged` / `invalid_comicinfo` / `unreadable`** — the
  Series value cannot be read reliably; the supported path is the explicit manual
  tagger (`POST /api/tagger/manual-tag`), not an automated rewrite.

**Write path.** ZIP has no in-place entry update, so the container is rebuilt in a
temp file beside the archive and atomically swapped in. Every non-ComicInfo entry
is copied with its original `ZipInfo` (name, timestamp, CRC, `compress_type`), so
image payloads are never decoded/resized/re-encoded and `STORED` entries stay
byte-identical; duplicate ComicInfo entries left by old appends are collapsed to
exactly one, which makes repair idempotent. The audit itself opens archives `"r"`
only — a test asserts every staged file's mtime + SHA-256 is unchanged by it.

**Auth/audit.** The route sits behind the normal auth middleware (state-changing
POST ⇒ CSRF origin check too). Each rewrite writes a `CBZ_SERIES_REPAIRED` activity
row with the authenticated actor (asserted in the suite with actor `alice`).

**UI.** The downloads page gains a "ComicInfo `<Series>` Audit & Repair" table:
audit button, per-row selection (only `safe_to_repair` rows are selectable), and a
"Repair Selected (n)" action. Nothing is written without ticking rows.

**Value/effort/risk.** Critical value (fixes files on disk); M effort (reader,
rebuilder, classification, endpoints, UI, tests); low risk (read-only audit, no
bulk mode, ambiguity refused, images untouched).

---

## 2. Tagger preview + manual tagging — IMPLEMENTED (PROJECT.md 16)

**Problem.** `tagger/cbz_tagger.py` could only auto-tag from Calibre templates.
A brand-new series has no Calibre row yet, so its volume 1 could not be tagged at
all — the user had to unzip and edit XML by hand.

**What shipped.**

- `GET /api/tagger/preview?filename=…` — returns the CBZ's current ComicInfo
  fields (or `has_comicinfo: false`), plus filename-parsed series/volume hints.
  Malformed XML returns a valid `invalid_comicinfo` payload rather than a 500.
- `POST /api/tagger/manual-tag` — validated by `schemas.ManualTagRequest`
  (`extra="forbid"`): `filename`, required `series`, optional `volume`, `title`,
  `writer`, `publisher`, `genre`. Only supplied fields are written; an omitted
  field is preserved. A corrupt `ComicInfo.xml` is replaced by the explicit tag
  (`replaced_invalid_comicinfo: true`), which is the documented recovery path.

**Security (non-negotiable, all tested).** The client never supplies a path. The
`filename` is a **lookup key into the staging scan** (`cbz_tagger.scan_staging_cbz_files`)
— it is never joined into a filesystem path. Before any open: schema rejects NUL
bytes, `..` segments, absolute/drive-rooted names and >512 chars; the resolver
restricts to one exact scan match (bare basename or staging-relative path; multiple
matches ⇒ 409), requires the `.cbz` suffix, rejects `os.path.islink()` archives,
confirms the real path with `security.ensure_path_within_roots(..., [staging_dir])`
(catching symlink escapes) and that the file still exists. Both endpoints respect
the auth middleware. Each manual write is logged as `MANUAL_TAG_CBZ` with the actor.

**Value/effort/risk.** High value (unblocks new-series workflow); S/M effort;
medium risk because it writes files — contained by the scan-only filename model,
path containment and explicit user submission.

---

## 3. Live download progress API + non-blocking degrade — IMPLEMENTED (15)

**Problem.** `QBitClient.get_torrents()` hard-coded a 10s socket timeout and both
the dashboard and `/downloads` page called it synchronously inside the request.
With qBittorrent enabled but unreachable (container down, wrong LAN IP, firewall)
a page render could block for the full timeout — an entire page waiting on a
secondary service.

**What shipped.**

- `GET /api/downloads/status?timeout=1.5` — always HTTP 200 with a valid payload:
  `status` ∈ `success | disabled | degraded`, `degraded`, `downloader`
  (`enabled/connected/version/message`), `torrents[]` (hash, name, state,
  progress, `progress_percent`, sizes, `download_speed`, `upload_speed`,
  `eta_seconds`, ratio, seeds/peers, `is_active`), `active[]`, `count`, `totals`
  (aggregate speed/progress/overall ETA). The timeout is clamped to
  `[0.2, 5.0]` seconds and passed to every downloader call.
- `QBitClient.get_torrents(..., timeout=10.0)` gained an explicit timeout (login
  is capped at 3s); the status builder runs in `asyncio.to_thread`, so a hung
  qBittorrent blocks a worker thread only, never the event loop.
- The dashboard (1.0s) and `/downloads` (1.2s) views now use the same builder;
  the template's qBittorrent-native keys are preserved in the normalized snapshot
  so the existing page markup keeps working.

A test starts a local TCP listener that accepts the handshake and never answers
HTTP; the status endpoint and `/downloads` both complete with `degraded` payloads
inside the short budget (asserted by elapsed time).

**Value/effort/risk.** High value (removes a real page-hang class); S effort; low
risk (additive endpoint + timeout parameter, E2E unaffected).

---

## 4. Unified search with alias resolution — IMPLEMENTED (17)

**Problem.** The UI/endpoints searched `series_tracking.name` (and releases by
title) with `LIKE`, but the stable key is intentionally *not* the title users
remember. A user searching "Delicious in Dungeon" found nothing because the stable
key is `Dungeon Meshi`; searching the romaji spelling had the same problem.

**What shipped.** `GET /api/search?q=…&limit=…` returns `series[]` and
`releases[]`:

- Series matching resolves, in order: the stable key (`match: stable_name`), then
  authoritative AniList canonical/romaji aliases (`match: anilist_alias`, with
  `matched_alias` and the cosmetic `display_title`).
- Every hit returns the **stable key** (`stable_name`) plus `display_title`, honouring
  the naming contract instead of replacing it.
- Release matching reuses the LIKE-escaped `database.search_releases()`
  (`security.escape_like`, `ESCAPE '\'`), so `%`/`_` in a query are literal.
- Alias resolution uses only authoritative cached metadata
  (`manga_metadata_cache.source == 'anilist'`), so it stays correct offline and
  never invents a translation.
- Blank query ⇒ HTTP 400; `limit` clamped 1–50.

**Value/effort/risk.** High value, S effort, low risk (read-only; no writes; no
network). The main limitation — deliberately accepted — is that alias resolution
is only as fresh as `manga_metadata_cache` (see §6/§7 ideas).

---

## 5. Expose `discard_unmarked_chapters` in the settings path — IMPLEMENTED

The P4 chapter guard reads `db.get_setting("discard_unmarked_chapters", "true")`,
seeded from `MANGATRAX_DISCARD_UNMARKED_CHAPTERS`, but `schemas.SettingsUpdateRequest`
uses `extra="forbid"` and had no such field — so `POST /api/settings` rejected it
and the behaviour was env-var-only despite being documented as a setting.

**What shipped.** `discard_unmarked_chapters` added to `schemas.SETTINGS_KEYS` and
`SettingsUpdateRequest` (`Optional[str]`, max 20 ⇒ accepts `"true"/"false"`, the
convention of the other boolean settings), plus a checkbox in the settings page
(default rendered on because the database seeds `"true"`). Tests assert default
true → set false (DB + scheduler helper both see it) → set true, and that unknown
keys are still rejected.

---

## 6. Provider health + per-feed backoff — DEFERRED

`database.update_feed_status()` already stores `last_status`/`last_error` per feed,
so a health table is mostly rendering. **Why not now:** the scheduler's fixed
per-feed interval (`feeds.interval`) is the current reliability mechanism; adding
exponential backoff changes polling semantics and needs a persisted backoff column
plus operator-visible state to avoid "why did polling stop?" confusion. Value is
operational, not user-facing. Touch points: `scheduler.check_all_feeds`,
`database.feeds`, `feeds.html`.

## 7. Durable notification outbox + retry — DEFERRED

`NotificationService` dispatches Discord/generic webhooks best-effort; failures are
`logger.debug`-only, so a flaky webhook silently loses events. **Why not now:** a
correct outbox needs a new table, a worker loop with backoff, dedupe keys and a
retention policy — schema-level work with migration concerns for a feature whose
failure is currently benign (the UI still shows the event in `/logs`).

## 8. Duplicate-release identity by infohash — DEFERRED

`api_releases_flat_rows` dedupes by `guid`/title/volume signature *at render time*
only; the same torrent can be stored twice if two feeds publish it under different
GUIDs. **Why not now:** changing the release identity key touches
`database.upsert_release` (guid is `UNIQUE NOT NULL`), selective-download tracking
and the grouped cache; a migration would be needed. Medium value, medium risk, and
the view-level dedupe already masks most of the pain.

## 9. Staging → Calibre import automation — DEFERRED

Tag staging files, then have MangaTrax call `calibredb add` / write metadata into
the Calibre library automatically. **Why not now:** this writes into the user's
actual library. The codebase deliberately reads Calibre zero-lock
(`CalibreMangaScanner`, `get_library_summary` mtime cache) and never writes it;
automating writes needs an explicit locking/queue design, duplicate policy,
rollback and a clear "who owns the library" decision from the user. High value but
high blast radius — do not do it without a design sign-off.

## 10. Cover-cache negative caching / LRU — DEFERRED

`CoverCache.get_or_download` re-attempts failed URLs on every render. A short
negative cache would cut wasted requests. **Why not now:** XS effort but almost no
user-visible value; it belongs in a general cache-hygiene pass (with the
`/api/covers` fallback SVG path), not M2.

## 11. Chapter-level tracking — DEFERRED

The P4 guard *discards* marker-less chapter releases using library context; real
chapter tracking would store chapters per series and mark them owned. **Why not
now:** the entire naming/grouping/ComicInfo pipeline is volume-centric
(`releases.volume_start`, `volumes_json`, `missing_volumes`, `Volume` in
ComicInfo); chapters would ripple through matcher, grouped views, selective
download and Calibre sync. High risk, high effort, needs a product decision.

## 12. Multi-library / multi-root Calibre — DEFERRED

`calibre_path` is a single setting used by the scanner, matcher, sync and settings
path guards. **Why not now:** single-library homes are the documented deployment;
multi-root touches every one of those modules and the `MANGATRAX_PATH_ROOTS`
containment story. Low marginal value for the current single-instance use case.

## 13. Bearer-token API auth for scripts/mobile — DEFERRED

Auth modes are `none | basic | oidc` with browser sessions; a script must use Basic
(if enabled) or a full OIDC browser flow. **Why not now:** needs a token store
(table + rotation/revocation), a new header path in `auth.AuthManager.check_request`
and a documented exposure story for every new API surface — security work that
deserves its own milestone rather than piggy-backing on feature work.

## 14. Reading-progress sync (Kavita/Komga/BookOrbit) — DEFERRED

Would let MangaTrax mark volumes read based on the reader app. **Why not now:**
external integrations without a stable contract; each target has different auth and
API shapes, and the M2 scope was already the CBZ repair + tagger/downloads/search.
Needs its own provider module and a decision about which reader is canonical.

## 15. Prometheus metrics — DEFERRED

`/health` returns auth/config health only. **Why not now:** a single-user
deployment with `/logs` and `/api/backfill/status` already answers "is it working";
metrics need auth exposure decisions and careful cardinality (series names must not
become labels). S effort, but operational value only.

---

## Verification for this milestone

- New suite: `tests/test_m2_features.py` — run through the sanctioned
  assert-aware runner only
  (`calibre-debug.exe -e "tests\run_tests.py"`), because `calibre-debug -e`
  itself compiles with `optimize=2` and would strip every bare `assert`.
- Honest-runner result after this milestone: **14 suites, 14 passed, 0 failed,
  992 assert statements compiled live, 1125 executions**. The baseline was
  13 suites / 817 live asserts / 0 failures, so the new suite adds 175 live
  asserts and nothing regressed.
- E2E suite unchanged: `tests/e2e/run_all_e2e.py` → **77/77** passed.
- The suite covers: audit classification (ok/repairable/ambiguous/unknown/
  untagged/invalid) and its read-only guarantee; metadata-repair planning;
  preview security (traversal, absolute, drive-rooted, non-scan, symlink);
  manual-tag single-entry + image-preservation + idempotence + validation
  rejections; explicit repair selection, refusal of ambiguous/unknown, actor
  logging, idempotence, image preservation; downloads status disabled/degraded/
  success payloads and a hung-downloader timing test; unified search alias
  resolution + LIKE escaping + limit; and the exposed settings boolean.
- Additionally smoke-tested against the production stack (real FastAPI + Pydantic
  v2 + Jinja2, not the embedded-interpreter shims): all new endpoints return the
  documented payloads/status codes and `/`, `/downloads`, `/settings` render.

## Recommended next step after M2

Do **not** immediately build §9 (Calibre import automation) or §11 (chapter
tracking). First let the CBZ audit run against the real staging directory and
report how many files are `ambiguous` vs `repairable` vs `unknown_series` — that
measurement decides whether the alias evidence (authoritative AniList cache) is
dense enough, or whether the next investment should be metadata-cache freshness
(§6/§7 adjacent) instead of more automation.
