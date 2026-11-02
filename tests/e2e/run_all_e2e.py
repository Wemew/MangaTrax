r"""
Master E2E Test Suite Runner for MangaTrax.
Executes all 4 tiers of opaque-box automated test suites:
  - Tier 1: Feature Coverage (41 tests)
  - Tier 2: Boundary & Corner Cases (25 tests)
  - Tier 3: Cross-Feature Combinations (7 tests)
  - Tier 4: Real-World Scenarios (4 tests)

Run via Calibre's Python interpreter:
  & "C:\Program Files\Calibre2\calibre-debug.exe" -e "tests\e2e\run_all_e2e.py"
"""

import os
import sys
import time
import unittest

E2E_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.dirname(E2E_DIR)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from e2e.test_tier1_feature_coverage import TestTier1FeatureCoverage
from e2e.test_tier2_boundary_corner import TestTier2BoundaryCorner
from e2e.test_tier3_cross_feature import TestTier3CrossFeature
from e2e.test_tier4_real_world import TestTier4RealWorldScenarios


def run_all_tiers():
    print("=" * 78)
    print("           MangaTrax Comprehensive E2E Opaque-Box Test Suite")
    print("               Executing Tiers 1 through 4 via Calibre Python")
    print("=" * 78)
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"Python Runtime: {sys.version}")
    print("=" * 78)

    tiers = [
        ("Tier 1: Feature Coverage (Endpoints, Tracking, Feeds, Calibre, Tagging, qBit, Discovery, Blacklist)", TestTier1FeatureCoverage),
        ("Tier 2: Boundary & Corner Cases (Empty Inputs, Extreme Vols, Unicode, Malformed XML, Intervals)", TestTier2BoundaryCorner),
        ("Tier 3: Cross-Feature Combinations (Pairwise Interaction Flows)", TestTier3CrossFeature),
        ("Tier 4: Real-World Scenarios (Series Lifecycle, Dual-Titles, Concurrency, Resumption)", TestTier4RealWorldScenarios),
    ]

    total_run = 0
    total_failures = 0
    total_errors = 0
    results_summary = []
    overall_start = time.time()

    for tier_name, test_class in tiers:
        print(f"\n--> Running {tier_name}...")
        tier_start = time.time()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_class)
        runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
        res = runner.run(suite)
        tier_elapsed = time.time() - tier_start

        count = res.testsRun
        fails = len(res.failures)
        errs = len(res.errors)
        passed = count - (fails + errs)

        total_run += count
        total_failures += fails
        total_errors += errs

        results_summary.append({
            "name": tier_name,
            "total": count,
            "passed": passed,
            "failed": fails,
            "errors": errs,
            "time": tier_elapsed,
            "success": res.wasSuccessful()
        })

    overall_elapsed = time.time() - overall_start

    print("\n" + "=" * 78)
    print("                           E2E TEST EXECUTION SUMMARY")
    print("=" * 78)
    print(f"{'Tier Name':<56} | {'Pass':<5} | {'Fail':<5} | {'Time (s)':<8}")
    print("-" * 78)

    all_passed = True
    for item in results_summary:
        if not item["success"]:
            all_passed = False
        short_name = item["name"][:54]
        print(f"{short_name:<56} | {item['passed']:<5} | {item['failed'] + item['errors']:<5} | {item['time']:<8.2f}")

    print("-" * 78)
    print(f"{'TOTAL':<56} | {total_run - (total_failures + total_errors):<5} | {total_failures + total_errors:<5} | {overall_elapsed:<8.2f}")
    print("=" * 78)

    if all_passed:
        print(" [PASSED] ALL 4 TIERS EXECUTED SUCCESSFULLY WITH 100% PASS RATE!")
        print("=" * 78)
        return 0
    else:
        print(" [FAILED] FAILURES OR ERRORS DETECTED DURING TEST EXECUTION.")
        print("=" * 78)
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tiers())
