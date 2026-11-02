# Project: MangaTrax Enhancement, Security Remediation & Codebase Simplification

## Architecture
MangaTrax is an automated manga tracker, downloader, and Calibre library integration service built on FastAPI, Jinja2, SQLite (WAL mode), BeautifulSoup4, and feedparser.

### Module Boundaries
- `backend/app/main.py`: FastAPI web server, 9 HTML view templates, 34 REST/AJAX endpoints, middleware, startup lifecycle.
- `backend/app/database.py`: SQLite persistence layer (`MangaDatabase`), 8 tables, WAL mode, transaction management, grouped release caching.
- `backend/app/scheduler.py`: Background worker thread managing periodic tasks (Calibre scanning, feed polling, selective download filtering, staging CBZ tagging, backfill).
- `backend/app/parser.py`: Title parsing, volume/chapter extraction, bundle range detection, candidate title normalization.
- `backend/app/matcher.py`: Release-to-series matching engine, prioritized group weighting, title similarity scoring.
- `backend/app/calibre/scanner.py`: Zero-lock Calibre `metadata.db` reader, comic volume mapping, dual-title resolution.
- `backend/app/tagger/cbz_tagger.py`: Non-destructive ComicInfo.xml metadata injector for `.cbz` files.
- `backend/app/providers/`: Feed providers (`NyaaMangaProvider`, `RSSFeedProvider`, `HtmlScraperProvider`).
- `backend/app/notifier/webhook.py`: Outbound notification dispatcher (Discord, generic webhooks).
- `backend/app/security.py`: (New) Centralized URL validation, SSRF defense, scheme filtering, security headers.
- `backend/app/schemas.py`: (New) Pydantic v2 validation models for all API request payloads.
- `tests/`: Automated integration test suites executed via Calibre's embedded Python runtime (`calibre-debug.exe -e`).

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Series Tracking & Calibre Sync | Zero-lock read of Calibre metadata.db, volume discovery, dual-title mapping | M1 | Survey 1 |
| 2 | Feed Polling & Release Ingestion | Periodic RSS/Nyaa/HTML feed checks, release title parsing, auto-download | M1 | Survey 1 |
| 3 | Selective Volume Downloading | qBittorrent Web API client, selective file prioritization for missing volumes | M1 | Survey 1 |
| 4 | ComicInfo.xml Staging Tagging | Metadata injection into CBZ archives without recompression | M1 | Survey 1 |
| 5 | Discovery & Blacklist Management | Discovered series queue, blacklisting, group prioritization | M1 | Survey 1 |
| 6 | Release Download by ID Bug Fix | Fix `api_release_download` 404 defect for releases > 1000 or ignored | M1 | Survey 1 |
| 7 | Release Status Upsert Bug Fix | Fix `db.upsert_release()` omitting `status` column in SQL UPDATE | M1 | Survey 1 |
| 8 | Auto-Download Transition Bug Fix | Fix `scheduler.py` gating auto-download solely on `is_new` | M1 | Survey 1 |
| 9 | Grouped Cache Invalidation | Add cache invalidation calls to `track_discovered_series`, `clear_discovered_series`, `clear_all_descriptions`, `normalize_series_titles_to_english` | M1 | Survey 1 |
| 10 | Unbounded Parser Loop Guard | Clamp loop bounds in `parser.py:extract_manga_bundle_and_volumes` to prevent algorithmic complexity DoS | M1 | Survey 1 |
| 11 | Calibre Sync Startup Deduplication | Prevent dual concurrent Calibre sync during server lifespan startup | M1 | Survey 1 |
| 12 | Crawl Delay Rate-Limit Fix | Fix `nyaa.py:252` delay calculation when `start_page >= max_pages` | M1 | Survey 3 |
| 13 | HTML Scraper Robustness | Guard against missing `httpx` or network errors in HTML scraper | M1 | Survey 3 |
| 14 | Brainstorming Recommendations Report | Produce comprehensive `FEATURE_RECOMMENDATIONS.md` documenting candidate features and priority matrix | M2 | Survey 1 & 2 |
| 15 | Live Download Progress API (P1) | New endpoint `/api/downloads/status` returning active torrent states, speeds, and progress | M2 | Survey 1 |
| 16 | Interactive ComicInfo Preview & Tagging (P2) | New endpoints `/api/tagger/preview` and `/api/tagger/manual-tag` for inspecting and tagging CBZ files | M2 | Survey 1 |
| 17 | Unified Search & Dual-Title Alias Resolution (P3) | Enhanced search endpoint combining series, releases, and alternate title aliases | M2 | Survey 1 |
| 18 | Centralized SSRF & Outbound URL Guard | Centralized `validate_public_url` blocking private IPs (RFC 1918, RFC 3927 metadata 169.254.169.254, loopback), enforcing HTTP/HTTPS, preventing DNS rebinding/redirect SSRF | M3 | Survey 2 |
| 19 | Pydantic Request Validation Models | Define Pydantic v2 schemas for all 16 POST endpoints in `schemas.py` | M3 | Survey 2 |
| 20 | Stored XSS Remediation | Enforce URL scheme validation (`http://`, `https://`) in `website_url` and Jinja2 `safe_url` filter | M3 | Survey 2 |
| 21 | Calibre Path Traversal & Probing Defense | Contain and validate paths in `/api/calibre/test` and settings `staging_dir`/`calibre_dir` | M3 | Survey 2 |
| 22 | Database Query Hardening | Replace dynamic string formatting in `LIMIT` with parameterized queries, escape `LIKE` wildcards | M3 | Survey 2 |
| 23 | External Network Request Timeouts | Enforce strict timeouts and safe exception shields across feed and metadata scrapers | M3 | Survey 2 |
| 24 | Security Headers & CSRF Verification | Add modern HTTP security headers and basic CSRF origin verification on state-changing endpoints | M3 | Survey 2 |
| 25 | Dependency Pruning | Prune dead `apscheduler` and clean up `backend/requirements.txt` | M4 | Survey 1 & 3 |
| 26 | Title Splitting Regex Deduplication | Centralize copy-pasted title splitting regex across 8 files into `parser.py:split_series_candidate_parts()` | M4 | Survey 3 |
| 27 | Dead Code Elimination | Remove duplicate alias `get_all_volumes_per_series` in `scanner.py` and unused helper routines | M4 | Survey 3 |
| 28 | Read-Query Side-Effect Refactoring | Refactor `get_releases_grouped_by_series` to decouple read queries from inline write mutations | M4 | Survey 3 |
| 29 | Opaque-Box E2E Test Suite (Tiers 1-4) | Comprehensive independent test suite covering features, boundaries, combinations, and realistic scenarios | E2E Track | ORIGINAL_REQUEST |
| 30 | 100% E2E Verification & Adversarial Hardening (Tier 5) | Final verification passing all E2E tests, adversarial coverage hardening, and clean forensic audit | M5 | ORIGINAL_REQUEST |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Testing Track | Build test infra, create Tiers 1-4 test suite, publish TEST_READY.md | none | IN_PROGRESS |
| M1 | Functional Defect Remediation & Verification | Fix identified functional bugs (features 6-13), verify all existing tests pass | none | IN_PROGRESS |
| M2 | Feature Brainstorming & High-Priority Additions | Generate FEATURE_RECOMMENDATIONS.md, implement P1-P3 additions (features 14-17) | M1 | PLANNED |
| M3 | Security Assessment & Remediation | Centralized SSRF guard, Pydantic schemas, XSS fix, path probing defense, SQL hardening, headers (features 18-24) | M1 | PLANNED |
| M4 | Codebase Simplification & Dependency Pruning | Prune requirements.txt, deduplicate title splitting regex, eliminate dead code (features 25-28) | M1, M2, M3 | PLANNED |
| M5 | Final E2E Test Suite & Adversarial Hardening | Pass 100% of E2E tests (Tiers 1-4), adversarial test hardening (Tier 5), clean forensic audit | E2E, M4 | PLANNED |

---

## Interface Contracts

### `backend/app/security.py` ↔ Outbound Request Callers
```python
def validate_public_url(url: str, allow_redirects: bool = False) -> str:
    """
    Validates that a URL is a legitimate public HTTP/HTTPS URL.
    Raises ValueError / HTTPException if:
    - Scheme is not http or https
    - Hostname resolves to loopback (127.0.0.0/8), private network (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16),
      link-local / cloud metadata (169.254.169.254), or unspecified addresses.
    Returns normalized safe URL string.
    """

def is_safe_href(url: str) -> bool:
    """Returns True if URL scheme is strictly http: or https:; False for javascript:, data:, file:, etc."""
```

### `backend/app/schemas.py` ↔ `backend/app/main.py`
```python
class FeedCreateRequest(BaseModel):
    name: str
    url: str
    feed_type: str = "nyaa"
    interval: int = Field(default=60, ge=5, le=1440)
    auto_download: bool = False

class ReleaseStatusUpdateRequest(BaseModel):
    status: Literal["REQUESTED", "IN_CALIBRE", "IGNORED", "BLACKLISTED", "DISCOVERY"]

class SeriesLinkUpdateRequest(BaseModel):
    website_url: str

class SettingsUpdateRequest(BaseModel):
    # Validated configuration dictionary with strict key whitelisting and type checking
```

### `backend/app/parser.py` ↔ Callers
```python
def split_series_candidate_parts(title: str) -> List[str]:
    """Centralized candidate title splitting across delimiters (|, /, –, —, -, //, ;)."""
```

---

## Code Layout
- `backend/app/`: Primary application source files
  - `main.py`: FastAPI routes and application lifecycle
  - `database.py`: SQLite DAL and query execution
  - `scheduler.py`: Background synchronization threads
  - `parser.py`: Title and volume extraction logic
  - `matcher.py`: Release matching algorithms
  - `security.py`: Outbound URL validation, SSRF defense, security utilities
  - `schemas.py`: Pydantic request models
  - `calibre/`: Calibre integration modules
  - `providers/`: Feed reader implementations
  - `tagger/`: ComicInfo.xml tagging routines
  - `templates/`: Jinja2 UI templates
- `tests/`: Automated test suite executed via Calibre's Python interpreter
  - `test_mangatrax_workflow.py`
  - `test_complete_series_and_dual_titles.py`
  - `test_performance_and_lazy_loading.py`
  - `test_releases_ignore_and_backfill_resume.py`
  - `test_e2e_endpoints.py`: (New) FastAPI endpoint test suite
  - `test_security_remediation.py`: (New) SSRF, XSS, input validation test suite
  - `test_high_priority_additions.py`: (New) New feature additions test suite

---

## Outcome (final)

| Milestone | Result |
|---|---|
| E2E Track | ✅ 77 opaque-box tests, Tiers 1-4, hermetic/offline |
| M1 Functional Defect Remediation | ✅ after the verification gate initially returned **FAIL** (see below) |
| M2 Feature Recommendations & Additions | ✅ `FEATURE_RECOMMENDATIONS.md` + CBZ `<Series>` audit/repair, tagger preview/manual-tag, downloads status API, unified alias search, `discard_unmarked_chapters` toggle |
| M3 Security & Auth | ✅ 3-mode auth gate (`none`/`basic`/`oidc` via PocketID), user audit trail (`activity_logs.actor` + `users` + JIT), SSRF guard, strict schemas, path containment, XSS/LIKE/LIMIT hardening |
| M4 Simplification & Dependency Pruning | ✅ `requirements.txt` 10 → 7, canonical title splitter (13 duplicates removed), dead code eliminated, GETs side-effect free |
| M5 Final Acceptance | ✅ 14 suites / 1017 live assertions / 0 failures; E2E 77/77 |

### Critical correction to earlier verification claims
`calibre-debug.exe -e` runs Python with `optimize=2`, so every bare `assert` in the script-style suites was
**stripped at compile time**. All prior "100% verified / ALL TESTS PASSED" figures for those suites were
therefore not evidence. [`tests/run_tests.py`](tests/run_tests.py) recompiles each suite with `optimize=0`
and is now the **only** sanctioned entry point for script suites; it prints the interpreter's optimize level
and counts both compiled asserts and actual executions. The E2E suite is `unittest`-based and was always genuine.

### Defects found only because verification became honest
- **Status clobbering (data loss):** feed re-polls overwrote deliberate `REQUESTED`/`IN_CALIBRE`/`BLACKLISTED`
  statuses, silently emptying the wanted list. Fixed with an explicit `resolve_status()` precedence model.
- **AniList rename vs. matching:** series were re-keyed to AniList English titles, breaking Calibre matching and
  corrupting `<Series>` in already-tagged CBZ files. Fixed by splitting the stable matching key from the display title.
- **Marker-less chapters** (`Series 180`) ingested as junk releases; now discarded via a context-guarded check.
- **Cover cache silently dead** without `httpx` (`covers.py` returned `None`); a real stdlib downloader with
  per-redirect-hop SSRF validation replaced it.

### Final dependency set (7)
`fastapi`, `uvicorn[standard]`, `jinja2`, `beautifulsoup4`, `feedparser`, `pydantic`, `authlib`.
Removed: `httpx` (→ stdlib `urllib`), `apscheduler` (unused), `python-multipart` (unused).
