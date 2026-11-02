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
│   ├── e2e/
│   │   ├── __init__.py                      # Package indicator
│   │   ├── harness.py                       # Core test harness, shims, context manager, mock client
│   │   ├── run_all_e2e.py                   # Master test runner across all 4 tiers
│   │   ├── test_tier1_feature_coverage.py   # Tier 1: 41 feature tests across 8 modules
│   │   ├── test_tier2_boundary_corner.py    # Tier 2: 25 boundary & edge condition tests
│   │   ├── test_tier3_cross_feature.py      # Tier 3: 7 pairwise cross-feature interaction flows
│   │   └── test_tier4_real_world.py         # Tier 4: 4 deep real-world end-to-end scenarios
├── TEST_INFRA.md                            # Test infrastructure architecture and reference (this document)
└── TEST_READY.md                            # Test execution readiness report, coverage matrix, and test inventory
```

---

## 3. Runtime Environment & Execution Model

### 3.1 Calibre Python Runtime
Calibre provides a self-contained Python 3.14.8 64-bit environment including SQLite3, `zipfile`, `urllib`, `xml.etree.ElementTree`, and core standard library modules.

To execute tests using this interpreter:
```powershell
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
This guarantees 100% deterministic, ultra-fast test execution (~7 seconds for 77 comprehensive tests).

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
