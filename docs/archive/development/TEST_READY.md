# MangaTrax Test Suite Readiness Report

## ⚠️ Correction notice (2026-11-02) — earlier "100% verified" claims were void

Every script-style suite under `tests/` used to be executed directly with
`calibre-debug.exe -e <file>`. That interpreter runs Python with **`optimize=2`**
(`sys.flags.optimize == 2`, `__debug__ is False`), so CPython strips every bare
`assert` statement at compile time. The suites' `"ALL TESTS PASSED (100% VERIFIED)"`
banners and their exit code 0 were therefore **not evidence of anything** — no
assertion ever ran. Earlier figures of that kind in this file and in prior session
reports are **void**.

The script-style suites are now run **only** through the assert-aware runner
`tests/run_tests.py`, which recompiles each suite with `optimize=0` and executes it
with live assertions (see `TEST_INFRA.md` §3.3). The 77-test E2E suite
(`tests/e2e/run_all_e2e.py`) is unittest-based (`self.assert*` method calls), so it
genuinely executed its assertions all along; its 77/77 result stands.

## Executive Summary (current, honest)

| | |
|---|---|
| **Sanctioned entry points** | `tests/run_tests.py` (script suites) **and** `tests/e2e/run_all_e2e.py` (E2E) — nothing else |
| **Script-style suites** | 7 suites, 481 `assert` statements compiled live (`optimize=0`), 495 assertion executions, **7 passed / 0 failed** |
| **E2E suite** | **77 / 77** tests pass (unittest, genuinely executed) |
| **Runtime** | Calibre 64-bit Python 3.14.8 (`calibre-debug.exe -e`) |
| **Hermetic** | No live metadata HTTP: AniList (`_fetch_from_anilist`) / MangaUpdates (`_fetch_from_api`) calls are stubbed in every test that would reach them. Verified green with outbound HTTP blocked (dead proxy). |
| **Failure detection** | Proven: an injected `assert False` in a scratch copy is reported as `FAIL` with file:line, statement and message, exit code 1 — while the same file run directly under `calibre-debug -e` still exits 0 (assert stripped). |

---

## 1. How to Run the Tests

Both entry points run inside Calibre's embedded Python interpreter.

### 1.1 Script-style suites — MUST go through the assert-aware runner

```powershell
# All script-style suites (tests/test_*.py)
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\run_tests.py"

# A single suite
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\run_tests.py" tests\test_mangatrax_workflow.py

# List the discovered suites without running them
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\run_tests.py" --list
```

Do **not** run a script-style suite directly (`calibre-debug -e tests\test_*.py`):
under that interpreter the bare `assert` statements are compiled away and the suite
can print "ALL PASSED" and exit 0 without checking anything. `tests/run_tests.py`
prints the interpreter's `sys.flags.optimize`/`__debug__` values on every invocation
so the environment is never ambiguous.

### 1.2 E2E suite (unittest-based, safe to run directly)

```powershell
# All 4 tiers (77 tests)
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\run_all_e2e.py"

# Single tier
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\test_tier1_feature_coverage.py"
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\test_tier2_boundary_corner.py"
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\test_tier3_cross_feature.py"
& "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\test_tier4_real_world.py"
```

---

## 2. Test Execution Results (2026-11-02, branch `audit/m1-m5`)

### 2.1 Script-style suites via `tests/run_tests.py` (assertions live)

```text
==============================================================================
MangaTrax assert-aware test runner
==============================================================================
  interpreter      : Python 3.14.8
  sys.flags.optimize: 2  (calibre-debug -e)
  __debug__        : False
  native asserts   : DISABLED -> every suite below is recompiled with optimize=0
  suites           : 7
  executed-assert counting: sys.monitoring tool id 4
==============================================================================

[1/7] PASS  tests\test_complete_series_and_dual_titles.py  (2.90s)  assert statements live: 64, executions: 73
[2/7] PASS  tests\test_m1_adversarial_challenger.py        (1.12s)  assert statements live: 0, executions: 0
[3/7] PASS  tests\test_m1_defect_fixes.py                  (7.63s)  assert statements live: 0, executions: 0
[4/7] PASS  tests\test_mangatrax_workflow.py               (1.09s)  assert statements live: 306, executions: 310
[5/7] PASS  tests\test_performance_and_lazy_loading.py     (0.29s)  assert statements live: 58, executions: 59
[6/7] PASS  tests\test_releases_ignore_and_backfill_resume.py (0.44s) assert statements live: 53, executions: 53
[7/7] PASS  tests\test_status_precedence.py                (1.33s)  assert statements live: 0, executions: 0

  suites run      : 7
  suites passed   : 7
  suites failed   : 0
  assert statements compiled live (optimize=0): 481
  assert executions observed (sys.monitoring): 495
RESULT: PASS -- every suite ran with assertions compiled live (optimize=0).
```

`assert statements live: 0` for `test_m1_adversarial_challenger.py`,
`test_m1_defect_fixes.py` and `test_status_precedence.py` is expected and honest:
those suites were written with explicit `_check(cond, msg)` helpers that raise
(instead of bare `assert`), precisely because the old optimize=2 interpreter strip
was known when they were authored. Any failing check still raises and aborts the
suite, so the runner reports them FAIL.

### 2.2 E2E suite via `tests/e2e/run_all_e2e.py` (unittest, genuine)

```text
Tier Name                                                | Pass  | Fail  | Time (s)
------------------------------------------------------------------------------
Tier 1: Feature Coverage (Endpoints, Tracking, Feeds,    | 41    | 0     | 10.58
Tier 2: Boundary & Corner Cases (Empty Inputs, Extreme   | 25    | 0     | 0.84
Tier 3: Cross-Feature Combinations (Pairwise Interacti   | 7     | 0     | 0.83
Tier 4: Real-World Scenarios (Series Lifecycle, Dual-T   | 4     | 0     | 1.69
------------------------------------------------------------------------------
TOTAL                                                    | 77    | 0     | 13.94
==============================================================================
 [PASSED] ALL 4 TIERS EXECUTED SUCCESSFULLY WITH 100% PASS RATE!
==============================================================================
```

---

## 3. Comprehensive Test Inventory (77 Tests)

### Tier 1: Feature Coverage (`tests/e2e/test_tier1_feature_coverage.py`) — 41 Tests

#### Feature 1: Endpoints & Routing (7 tests)
1. `test_01_stats_endpoint_returns_json`: Verifies `/api/stats` returns accurate series, releases, and queue counts.
2. `test_02_series_rows_html`: Verifies `/api/series/rows` renders HTML table fragment for tracked manga series.
3. `test_03_releases_grouped_json`: Verifies `/api/releases/grouped` returns grouped release structure by series.
4. `test_04_feeds_endpoint`: Verifies `/api/feeds` GET returns feed list and POST adds a new feed.
5. `test_05_discovery_endpoint`: Verifies `/api/discovery` returns candidate manga titles with pagination and volume counts.
6. `test_06_blacklist_endpoint`: Verifies `/api/blacklist` returns currently blacklisted series patterns.
7. `test_07_prioritized_groups_endpoint`: Verifies `/api/prioritized_groups` returns seeded release group priorities.

#### Feature 2: Series Tracking & Status Updates (5 tests)
8. `test_08_series_addition_and_retrieval`: Adds a new series and asserts persistence in SQLite.
9. `test_09_series_status_update_ignored_monitored`: Tests toggling series monitoring status (`MONITORED` vs `IGNORED`).
10. `test_10_series_manual_volume_override`: Sets and queries manual volume offsets.
11. `test_11_release_status_update_requested`: Updates a release to `REQUESTED` and verifies state.
12. `test_12_release_ignore_and_revert`: Updates release status to `IGNORED` and resets back to `NEW`.

#### Feature 3: Feed Management & Polling (5 tests)
13. `test_13_add_custom_feed`: Adds an RSS feed with a custom polling interval.
14. `test_14_update_feed_settings`: Updates feed URL, name, and auto-download flag.
15. `test_15_delete_feed`: Deletes an existing feed and verifies removal.
16. `test_16_ingest_rss_feed_releases`: Ingests synthetic RSS XML into releases table.
17. `test_17_nyaa_provider_scraping`: Parses synthetic Nyaa HTML markup and extracts magnet hashes and torrent links.

#### Feature 4: Calibre Library Synchronization (5 tests)
18. `test_18_calibre_scanner_reads_metadata`: Connects to mock Calibre `metadata.db` and extracts book records.
19. `test_19_calibre_sync_updates_series_volume_counts`: Runs `sync_calibre_series` and updates acquired volumes.
20. `test_20_calibre_scanner_detects_custom_columns`: Reads custom integer and float volume columns.
21. `test_21_calibre_scanner_dual_title_resolution`: Resolves English and Romaji alternate titles.
22. `test_22_calibre_sync_resolves_pending_requests`: Auto-resolves `REQUESTED` releases when books appear in Calibre.

#### Feature 5: ComicInfo.xml Tagging & Metadata Injection (5 tests)
23. `test_23_tag_cbz_with_comicinfo`: Injects structured `ComicInfo.xml` into a `.cbz` archive.
24. `test_24_tag_cbz_preserves_images`: Verifies archive image contents and checksums remain untouched.
25. `test_25_read_existing_comicinfo`: Reads and parses existing `ComicInfo.xml` from an archive.
26. `test_26_tag_cbz_updates_existing_metadata`: Overwrites older `ComicInfo.xml` without corrupting archive.
27. `test_27_staging_scan_processes_pending_cbz`: Automates staged CBZ processing and moves files to Calibre intake.

#### Feature 6: qBittorrent Selective Downloading (5 tests)
28. `test_28_qbit_client_login`: Emulates qBittorrent Web API authentication session.
29. `test_29_qbit_add_torrent_magnet`: Dispatches magnet URI with custom save path and category.
30. `test_30_qbit_fetch_torrent_files`: Queries torrent file manifest and file indices.
31. `test_31_qbit_set_file_priorities`: Sets wanted file priorities to selective missing volumes and ignores duplicates.
32. `test_32_qbit_connection_failure_handled`: Gracefully handles qBittorrent daemon offline conditions.

#### Feature 7: Series Discovery & Candidate Management (5 tests)
33. `test_33_feed_ingestion_populates_discovery`: Unrecognized titles in feeds are routed to discovery queue.
34. `test_34_discovery_aggregates_volume_counts`: Computes highest observed volume for discovered series.
35. `test_35_promote_discovered_series_to_monitored`: Promotes candidate to tracked series and initiates monitoring.
36. `test_36_clear_discovered_series`: Clears discovery table and invalidates cache.
37. `test_37_discovery_pagination_and_sorting`: Tests discovery queue sorting by volume count and date.

#### Feature 8: Blacklist Management & Filtering (4 tests)
38. `test_38_add_series_to_blacklist`: Adds series pattern to blacklist table.
39. `test_39_blacklisted_release_suppressed`: Feeds matching blacklisted titles are dropped or marked `BLACKLISTED`.
40. `test_40_remove_series_from_blacklist`: Removes series from blacklist and restores monitoring eligibility.
41. `test_41_blacklisted_series_rejected_from_discovery`: Verifies discovery queue discards blacklisted candidate titles.

---

### Tier 2: Boundary & Corner Cases (`tests/e2e/test_tier2_boundary_corner.py`) — 25 Tests

#### Category 1: Empty and Missing Inputs (5 tests)
42. `test_01_empty_series_title_rejected`: Submitting empty string or whitespace-only title raises 400.
43. `test_02_empty_feed_url_rejected`: Adding feed with blank URL raises validation error.
44. `test_03_missing_release_id_returns_404`: Querying non-existent release ID returns 404.
45. `test_04_empty_cbz_archive_handled`: Tagging a 0-byte or corrupted CBZ archive raises clean error without crash.
46. `test_05_empty_feed_response_handled`: Empty RSS feed body handled safely without XML parser exception.

#### Category 2: Extreme Volume Numbers & Ranges (5 tests)
47. `test_06_volume_zero_parsing`: Extracts Volume 0 (prologues/one-shots) correctly.
48. `test_07_volume_thousand_plus`: Correctly parses extreme volumes (`Vol. 1042`).
49. `test_08_fractional_point_five_volumes`: Correctly extracts fractional volumes (`Vol. 7.5`, `Vol. 12.5`).
50. `test_09_massive_multi_volume_bundle`: Parses bundles spanning large ranges (`Vol. 01-100`) without looping timeout.
51. `test_10_negative_volume_in_title_handled`: Negative or ambiguous numbers (e.g. `Chapter -01`) safely normalized.

#### Category 3: Special Characters & Unicode Sanitization (5 tests)
52. `test_11_japanese_kanji_kana_titles`: Full CJK titles (`葬送のフリーレン`, `SPY×FAMILY`) preserved accurately.
53. `test_12_quotes_and_accents_in_title`: Accents and special symbols (`Kaguya-sama: Love Is War!`, `Pokémon`) handled safely.
54. `test_13_xss_meta_characters_in_title`: Input containing `<script>alert(1)</script>` sanitized in outputs.
55. `test_14_sql_injection_probe_in_series_search`: Input `' OR 1=1 --` safely handled via parameterized queries.
56. `test_15_windows_reserved_characters_in_filename`: Colons, slashes, asterisks sanitized for Windows filesystem paths.

#### Category 4: Malformed Feeds & Corrupt XML/CBZ (5 tests)
57. `test_16_truncated_rss_xml`: Truncated XML stream caught safely without daemon crash.
58. `test_17_malformed_comicinfo_xml_healed`: Malformed `ComicInfo.xml` inside CBZ safely replaced or overwritten.
59. `test_18_html_feed_missing_table`: Ingestion safely handles HTML missing expected selector tables.
60. `test_19_cbz_missing_zip_central_directory`: Corrupted ZIP central directory reported cleanly.
61. `test_20_network_timeout_simulated`: Network timeouts in feed fetch handled cleanly with retry backoff.

#### Category 5: Zero, Negative, and Boundary Interval Limits (5 tests)
62. `test_21_feed_interval_zero_rejected`: Interval = 0 rejected with 400 validation error.
63. `test_22_feed_interval_negative_rejected`: Interval = -60 rejected with 400 validation error.
64. `test_23_feed_interval_minimum_boundary`: Minimum boundary interval (5 minutes) accepted.
65. `test_24_feed_interval_maximum_boundary`: Maximum boundary interval (1440 minutes / 24 hours) accepted.
66. `test_25_feed_interval_excessive_rejected`: Interval = 100,000 minutes clamped or rejected.

---

### Tier 3: Cross-Feature Combinations (`tests/e2e/test_tier3_cross_feature.py`) — 7 Tests

67. `test_flow_01_calibre_sync_to_missing_detection_to_autoresolve`: Scans Calibre library, identifies missing Volume 3, registers request, and auto-resolves when Volume 3 appears.
68. `test_flow_02_nyaa_feed_to_title_parsing_to_series_association`: Polling Nyaa HTML -> parser extracts volume -> release associated with monitored series.
69. `test_flow_03_release_autodownload_to_qbit_selective_priority`: Auto-download triggered -> qBittorrent client skips existing volumes and selects missing volumes.
70. `test_flow_04_staged_cbz_to_comicinfo_tagging_to_calibre_detection`: New CBZ placed in staging -> tagged with metadata -> scanned by Calibre sync.
71. `test_flow_05_discovered_series_to_promotion_to_feed_matching`: Candidate series accumulated in discovery -> promoted to monitored -> future feed releases auto-matched.
72. `test_flow_06_blacklist_addition_to_feed_release_suppression`: Adding series to blacklist immediately suppresses matched releases from ingestion and download.
73. `test_flow_07_custom_group_priority_release_preference`: Prioritizing a release group scores its releases higher than generic groups for the same volume.

---

### Tier 4: Real-World Scenarios (`tests/e2e/test_tier4_real_world.py`) — 4 Tests

74. `test_scenario_01_complete_series_acquisition_lifecycle`:
    - Full end-to-end lifecycle for *Dungeon Meshi*:
    - Library starts with Volumes 1-6.
    - Missing Volumes 7-14 identified and marked `REQUESTED`.
    - Batch releases ingested from Nyaa feed.
    - Multi-volume bundle (`Vol. 07-14`) downloaded selectively.
    - Staged CBZ files tagged with ComicInfo.xml metadata.
    - Calibre scanner runs, auto-resolving all pending requests and completing the series.
75. `test_scenario_02_dual_title_manga_release_storm`:
    - Tests high-frequency dual-title releases under alternate naming (*Kimetsu no Yaiba* vs. *Demon Slayer*).
    - Releases arrive in mixed order with varied formatting (`[Group] Demon Slayer - Kimetsu no Yaiba Vol 01-05`, `Kimetsu no Yaiba / Demon Slayer Vol. 06-10`).
    - Verifies alias resolution assigns all releases to the canonical series without creating duplicate records.
76. `test_scenario_03_high_volume_concurrent_ingestion_and_db_stress`:
    - Simulates 250 release items ingested across multiple feeds simultaneously into SQLite WAL mode.
    - Verifies zero database locking errors (`sqlite3.OperationalError: database is locked`), accurate transaction commits, and cache consistency.
77. `test_scenario_04_broken_network_and_backfill_resumption`:
    - Simulates intermittent network drops during historical feed backfill.
    - Verifies backfill state checkpointing, error logging, and seamless resumption from the last recorded timestamp without duplicate downloads.

---

## 4. Test Matrix & Requirements Traceability

| Requirement | Test Coverage | Status |
|-------------|---------------|--------|
| **R1. Feature Review & Verification** | Tier 1 (41 tests) across 8 core functional modules | **VERIFIED (41/41)** |
| **R1. Boundary & Corner Cases** | Tier 2 (25 tests) across 5 edge condition categories | **VERIFIED (25/25)** |
| **R1. Cross-Feature Pipelines** | Tier 3 (7 tests) across pairwise subsystem interactions | **VERIFIED (7/7)** |
| **R1. Realistic Workflows & Stress** | Tier 4 (4 tests) covering full lifecycles, dual titles, and WAL stress | **VERIFIED (4/4)** |
| **Script-style suites (M1 + legacy)** | 7 suites / 481 `assert` statements compiled live / 495 assertion executions | **VERIFIED (7/7 suites, 0 failures)** |
| **Assertions actually execute** | `tests/run_tests.py` recompiles every suite with `optimize=0`; injected-false-assert demo proves failures are caught (exit 1) | **VERIFIED** |
| **Calibre Runtime Integration** | Execution under Calibre Python 3.14.8 (`calibre-debug.exe -e`) | **VERIFIED** |
| **Hermetic Isolation** | Zero live metadata HTTP (AniList/MangaUpdates stubbed in tests that would reach them), zero live torrent/Calibre resources, ephemeral SQLite WAL sandboxing. Green with outbound HTTP blocked. | **VERIFIED** |

---

## 5. Architectural Findings & Notes for Implementation Agents

During the development and verification of this opaque-box E2E suite, several architectural behaviors were observed and codified:
1. **Database Default Seeds**: MangaTrax's `MangaDatabase` automatically provisions 4 default prioritized groups (`danke-Empire`, `1R0N`, `Lu自主规制`, `a-sloth`) and 1 default feed (`Manga Releases (RSS)`). Any tests inspecting group or feed counts must account for this baseline.
2. **CBZ Tagging Interface**: `inject_comicinfo` expects a Python dictionary payload (`Dict[str, Any]`), not raw XML strings. Keys include `series_name`, `volume`, `authors`, `publisher`, `tags`.
3. **Nyaa Provider Implementation**: `NyaaMangaProvider` scrapes HTML table markup (`table.torrent-list tbody tr`) using BeautifulSoup, capturing `torrent_url`, `magnet_url`, `seeders`, and `post_size`.
4. **qBittorrent Priority Signature**: Selective volume downloading lives in
   `QBitClient.apply_selective_volume_download()`, which lists files via
   `get_torrent_files()` and deselects owned volumes via
   `set_file_priorities(hash, [indices], priority=0)`. The older
   `filter_torrent_bundle_files()` helper no longer exists — tests must mock at the
   current boundary.
5. **Never run a script-style suite directly.** `calibre-debug.exe -e` uses
   `optimize=2`; bare `assert` statements are stripped at compile time and the suite
   will happily print "ALL TESTS PASSED" while checking nothing. Use
   `tests/run_tests.py`, or write new suites with explicit `_check(cond, msg)` raises
   (as `test_m1_*` and `test_status_precedence.py` do).
6. **Volume values are display strings in the tagger result.** Since commit d1ee8eb,
   `auto_tag_staging_cbz_files()` returns `{"volume": "4"}` (or `"1-5"` for bundle
   ranges) — a display string that is also written into `ComicInfo.xml`, **not** the
   pre-d1ee8eb float `4.0`. Assertions must match the string contract.
7. **Settings are strings.** `MangaDatabase.get_setting()` returns TEXT from
   `app_settings`; numeric settings such as `backfill_last_page` must be coerced
   (`int(...)`) before comparing to ints.
