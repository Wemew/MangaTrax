# MangaTrax Test Infrastructure Specification

## 1. Overview and Purpose

MangaTrax is an automated manga tracker, downloader, and Calibre library integration service. To guarantee high reliability across its multifaceted workflows without requiring live torrent daemons, external tracker connections, or interactive Calibre GUI processes, MangaTrax employs an **opaque-box End-to-End (E2E) testing framework**.

The test infrastructure is located in `tests/e2e/` and fulfills the following objectives:
1. **Hermetic & Self-Contained**: Operates with zero external network connectivity, zero open network port bindings, and zero background daemon dependencies.
2. **Calibre Runtime Native**: Runs directly within Calibre's embedded 64-bit Python 3.14.8 interpreter via `calibre-debug.exe -e`, matching the actual target deployment environment.
3. **Four-Tiered Coverage**: Organizes test cases systematically into Feature Coverage (Tier 1), Boundary & Corner Cases (Tier 2), Cross-Feature Combinations (Tier 3), and Real-World Scenarios (Tier 4).
4. **Ephemeral Isolation**: Uses temporary SQLite databases in WAL mode and temporary directory sandboxes for every test execution context to eliminate state leakage between tests.

---

## 2. Directory Layout

The E2E test infrastructure follows this directory layout:

```text
MangaTrax/
├── tests/
│   ├── run_tests.py                         # SANCTIONED runner for script-style suites (optimize=0)
│   ├── mock_calibre_manga.py                # Shared mock Calibre metadata.db generator
│   ├── test_mangatrax_workflow.py           # Legacy integration suite (script-style, bare asserts)
│   ├── test_complete_series_and_dual_titles.py
│   ├── test_performance_and_lazy_loading.py
│   ├── test_releases_ignore_and_backfill_resume.py
│   ├── test_m1_defect_fixes.py              # M1 suites (raise-based _check() helpers)
│   ├── test_m1_adversarial_challenger.py
│   ├── test_status_precedence.py            # Status precedence model suite (_check() helpers)
│   └── e2e/
│       ├── __init__.py                      # Package indicator
│       ├── harness.py                       # Core test harness, shims, context manager, mock client
│       ├── run_all_e2e.py                   # Master test runner across all 4 tiers
│       ├── test_tier1_feature_coverage.py   # Tier 1: 41 feature tests across 8 modules
│       ├── test_tier2_boundary_corner.py    # Tier 2: 25 boundary & edge condition tests
│       ├── test_tier3_cross_feature.py      # Tier 3: 7 pairwise cross-feature interaction flows
│       └── test_tier4_real_world.py         # Tier 4: 4 deep real-world end-to-end scenarios
├── TEST_INFRA.md                            # Test infrastructure architecture and reference (this document)
└── TEST_READY.md                            # Test execution readiness report, coverage matrix, and test inventory
```

---

## 3. Runtime Environment & Execution Model

### 3.1 Calibre Python Runtime
Calibre provides a self-contained Python 3.14.8 64-bit environment including SQLite3, `zipfile`, `urllib`, `xml.etree.ElementTree`, and core standard library modules.

To execute tests using this interpreter:
```powershell
# Script-style suites (MANDATORY: use the assert-aware runner, see 3.3)
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\run_tests.py"

# E2E suite (unittest-based, genuinely executes its own assertions)
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\run_all_e2e.py"
```

Individual test modules can be executed directly with:
```powershell
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\test_tier1_feature_coverage.py"
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\test_tier2_boundary_corner.py"
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\test_tier3_cross_feature.py"
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\test_tier4_real_world.py"
```

### 3.2 Solving Windows AsyncIO Event Loop Bottlenecks
On Windows, Calibre's embedded runtime can encounter thread or socket deadlocks if standard `asyncio.run()` is invoked against asynchronous FastAPI route handlers.

The MangaTrax test harness overcomes this by providing a synchronous coroutine stepping engine:
- `run_coroutine_sync(coro)`: Iterates coroutine and generator frames synchronously.
- Transparent shims for `asyncio.to_thread`: Runs synchronous worker functions directly on the current thread without spinning up background loop dispatchers.
This guarantees 100% deterministic, ultra-fast test execution (~14 seconds for 77 comprehensive tests).

### 3.3 Assert-Aware Runner for Script-Style Suites (`tests/run_tests.py`)

**The problem (why earlier "100% verified" claims were void).**
`calibre-debug.exe -e <file>` executes Python with `optimize=2`
(`sys.flags.optimize == 2`, `__debug__ is False`). CPython removes every bare
`assert` statement at compile time when optimize >= 1. The script-style suites in
`tests/` are `if __name__ == "__main__":` scripts whose verification value lives
entirely in bare `assert`s. Run directly, they printed
`ALL TESTS PASSED (100% VERIFIED)` and exited 0 **without executing a single
assertion**. Every historical result produced that way is void.

**The fix.** `tests/run_tests.py` is the only sanctioned entry point for those suites:
* reads each suite's source and recompiles it with
  `compile(src, path, "exec", optimize=0)`;
* `exec`s that code object in a fresh module namespace with `__name__ = "__main__"`,
  `__file__` set, and `tests/` + repo root on `sys.path`, so the suite's own runner
  block executes exactly as if scripted while its assertions stay live;
* supports one suite (`tests\run_tests.py tests\test_mangatrax_workflow.py`) or every
  `tests/test_*.py` suite by default, plus `--list`;
* reports honest per-suite counts: **assert statements live** (static AST count of
  compiled-in `assert`s) and **assertion executions observed** (`sys.monitoring`
  local `LINE` events, installed only on the suite's own code objects — backend code
  is not traced, so timing assertions are not distorted);
* prints `sys.flags.optimize` / `__debug__` on every run so the environment is never
  ambiguous;
* on failure prints the failing file:line, the source statement, the assertion
  message and the full traceback; **no exception is swallowed**;
* exits non-zero if any suite fails. A suite calling `sys.exit(non-zero)` is also
  treated as a failure, so a suite cannot fake success by exiting.

**Proof the runner detects failures.** A scratch copy of a passing suite plus an
injected `assert False, "FAILURE-DEMO: ..."`:

| command | result |
|---|---|
| `calibre-debug.exe -e tests\_scratch_false_assert_demo.py` (direct) | **exit 0** — the false assert is compiled away, prints "ALL ... TESTS PASSED!" |
| `calibre-debug.exe -e tests\run_tests.py tests\_scratch_false_assert_demo.py` | **exit 1** — `failed at : tests\_scratch_false_assert_demo.py:377`, `statement : assert False, 'FAILURE-DEMO: ...'`, `message : FAILURE-DEMO: ...` |

The scratch file was deleted afterwards.

**Suites that use `_check()` instead of `assert`.** `test_m1_adversarial_challenger.py`,
`test_m1_defect_fixes.py` and `test_status_precedence.py` raise explicitly via
`_check(cond, msg)` helpers (they were authored while the optimize=2 strip was known).
The runner honestly reports `assert statements live: 0` for them and still marks them
FAIL on any raised exception.

---

## 4. Test Harness Architecture (`tests/e2e/harness.py`)

The harness provides four major subsystems:

### 4.1 In-Memory Web Framework Compatibility Shims
Enables MangaTrax's `backend/app/main.py` routing, request dispatch, and response formatting to run seamlessly under standalone Python without third-party ASGI web servers:
- **`FastAPI` / `APIRouter`**: Captures route definitions (`@app.get`, `@app.post`, `@app.delete`), parses path templates (e.g. `/api/series/{series_id}/edit`), and stores handler metadata.
- **`Request`**: Emulates HTTP requests, providing `.headers`, `.query_params`, `.json()`, `.body()`, and client IP metadata.
- **`Responses`**: Emulates `JSONResponse`, `HTMLResponse`, `FileResponse`, and `RedirectResponse`.
- **`HTTPException`**: Accurately reproduces HTTP status codes and detail payloads.
- **`Jinja2Templates`**: In-memory template rendering engine for HTML view endpoints.

### 4.2 Ephemeral Sandbox (`E2ETestContext`)
A Python context manager that provisions an isolated testing environment:
1. **Isolated Data Directory**: Sets `os.environ["DATA_DIR"]` to a unique temporary folder (`tempfile.mkdtemp()`).
2. **Fresh WAL SQLite Database**: Instantiates `MangaDatabase` at `temp_dir/mangatrax_e2e.db`, creating all 8 tables (`series`, `releases`, `feeds`, `discovery`, `blacklist`, `prioritized_groups`, `settings`, `schema_version`).
3. **Mock Calibre Library Generator**: Generates a valid Calibre SQLite `metadata.db` file with custom column tables (`custom_column_1`, etc.) and pre-populated manga books.
4. **Mock Staging Area**: Creates a staging directory with synthesized `.cbz` files containing valid ZIP structures and `ComicInfo.xml` entries.
5. **Mock External Services**:
   - `MockQBitClient`: Tracks authentication, torrent additions (magnets, URLs), file inventory queries, and selective volume priorities without network calls.
   - `MockDownloader`: Intercepts download commands and tracks requested volumes.
6. **Automatic Teardown**: Closes database connection pools, resets global application singletons, and recursively deletes all temporary directories upon context exit.

### 4.3 Route Dispatcher (`E2EClient`)
Simulates HTTP client interaction with `main.py`:
- `client.get(path, params=None)`
- `client.post(path, json=None, data=None)`
- `client.delete(path)`

Matches parameterized route paths (`/api/releases/{release_id}/download`), extracts path arguments, binds query/json bodies, executes endpoint coroutines synchronously via `run_coroutine_sync`, and returns an `E2EResponse` object containing `.status_code`, `.json()`, and `.text`.

### 4.4 Synthetic Feed & Payload Generators
- `generate_rss_xml(items)`: Creates valid RSS 2.0 XML feeds with custom titles, enclosures, and publication dates.
- `generate_nyaa_rss(items)`: Creates Nyaa-compatible RSS XML with standard BitTorrent namespace tags (`nyaa:infoHash`, `nyaa:seeders`, `nyaa:size`).
- `generate_nyaa_html(items)`: Generates valid HTML documents matching Nyaa's table markup (`table.torrent-list tbody tr`) to thoroughly exercise `NyaaMangaProvider` scraping routines.
- `create_test_cbz(path, comic_info_xml=None, image_files=None)`: Constructs valid CBZ comic archives with embedded XML metadata and image frames.

---

## 5. Test Suite Tier Taxonomy

```
+----------------------------------------------------------------------------+
|                          MangaTrax E2E Test Suite                          |
+----------------------------------------------------------------------------+
| Tier 1: Feature Coverage (41 tests)                                        |
|   - Endpoints, Series Tracking, Feeds, Calibre Sync, Tagging, qBit,        |
|     Discovery, Blacklist Management                                        |
+----------------------------------------------------------------------------+
| Tier 2: Boundary & Corner Cases (25 tests)                                 |
|   - Empty inputs, extreme volume numbers, Unicode/special characters,      |
|     malformed XML/corrupted CBZ, invalid polling intervals                 |
+----------------------------------------------------------------------------+
| Tier 3: Cross-Feature Combinations (7 tests)                               |
|   - Multi-component interaction pipelines: Calibre -> Feeds -> Tagging ->   |
|     qBittorrent Selective Downloader -> Discovered Series Promotion        |
+----------------------------------------------------------------------------+
| Tier 4: Real-World Scenarios (4 tests)                                     |
|   - Full manga series acquisition lifecycle (Dungeon Meshi Vol. 01-14)     |
|   - Dual-title release storms (Kimetsu no Yaiba / Demon Slayer)            |
|   - 250-release WAL concurrency stress                                     |
|   - Interrupted backfill network resumption                                |
+----------------------------------------------------------------------------+
```

---

## 6. Guidelines for Writing New Tests

When adding new tests to the MangaTrax E2E suite, adhere to these principles:

1. **Use `E2ETestContext`**:
   ```python
   from harness import E2ETestContext

   with E2ETestContext() as ctx:
       # Fresh isolated DB, staging, and calibre paths are ready
       resp = ctx.client.get("/api/stats")
       assert resp.status_code == 200
   ```
2. **Never Touch Live Resources**: Do not open network sockets, connect to external URLs, or read the user's live Calibre directory. Always supply mock paths or feeds via `ctx`.
3. **Progressive Testability**: Keep tests self-contained and verifiable with only the current codebase. Avoid testing implementation details; test behaviors and observable outputs.
4. **Assert Deterministic State**: Account for baseline database seed values (e.g. MangaTrax initializes with 4 default prioritized groups and 1 default feed).
5. **Script-Style Suites**: If a new file is a `if __name__ == "__main__":` script, it must be run through `tests/run_tests.py` (never `calibre-debug -e <file>` directly), or written with an explicit `_check(cond, msg)` helper that raises instead of bare `assert`.
6. **No Live Metadata HTTP**: stub the current metadata boundary and seed expected metadata locally:
   ```python
   from unittest.mock import patch
   from app.metadata.anilist import AniListMetadataService
   from app.metadata.mangaupdates import MangaUpdatesService

   with patch.object(AniListMetadataService, "_fetch_from_anilist", return_value=None), \
        patch.object(MangaUpdatesService, "_fetch_from_api", return_value=None):
       ...
   db.cache_manga_metadata("series", {..., "total_volumes": 24, "source": "anilist"})
   ```
   A cached metadata entry is only trusted by `resolve_manga_series()` when it carries
   `total_volumes`; without that field the code falls through to a live AniList query
   (this made `test_romaji_to_english_titles_conversion` pass only on networked
   machines — a false-confidence failure mode that has been fixed).
7. **Assert the Real Contract**: e.g. volume values returned by
   `auto_tag_staging_cbz_files()` are display strings (`"4"`, `"1-5"`), not floats,
   and `db.get_setting()` returns TEXT (coerce before numeric comparison).

---

## 7. Hermetic / Offline Contract

The suite must produce identical results with or without network access. This is
part of the definition of "passing", not an optional nicety.

- **E2E suite**: `tests/e2e/harness.py` replaces
  `AniListMetadataService._fetch_from_anilist` with an offline stub at import time
  (`_offline_anilist_fetch`), and every external service (qBittorrent, Calibre,
  feeds) is a mock. No sockets are opened, no torrent daemon or user Calibre
  library is touched.
- **Script suites**: every test that would reach live metadata HTTP stubs it at the
  current boundary:
  - AniList GraphQL → `patch.object(AniListMetadataService, "_fetch_from_anilist", return_value=None)`
    (`test_dual_titles_detection_during_backfill`, `test_dual_title_delimiter_preservation`).
  - MangaUpdates REST → `patch.object(MangaUpdatesService, "_fetch_from_api", return_value=None)`
    (`test_external_metadata_volume_resolution`).
  - Metadata a test expects to resolve is seeded into `manga_metadata_cache`
    (including `total_volumes`), never fetched.
- **Verification evidence**: the full script-suite run was executed with
  `HTTP_PROXY`/`HTTPS_PROXY` pointed at a dead proxy (`http://127.0.0.1:9`), so every
  outbound HTTP request fails, and still passed 7/7 suites. A networked run before
  these fixes spent 50+ seconds in AniList 429 cooldowns and one assertion
  (`test_romaji_to_english_titles_conversion`, `tests/test_mangatrax_workflow.py`)
  passed only because live AniList answered — that is exactly the false-confidence
  mode this section exists to prevent.
- Do not add tests that depend on live network, a live qBittorrent/Calibre instance,
  or tight wall-clock timing (generous upper bounds only).
- **Sanctioned commands**:
  ```powershell
  & "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\run_tests.py"      # script suites, assert-live
  & "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\run_all_e2e.py" # 77 unittest E2E tests
  ```
