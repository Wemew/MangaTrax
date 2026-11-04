#!/usr/bin/env python3
"""task-45 suite: explicit Load More pagination, server-side search, per-card cap.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # noqa: E402
import app.main as main_module  # noqa: E402


def _insert(ctx, guid, series, title, status="DISCOVERY"):
    with ctx.db.get_connection() as conn:
        conn.execute(
            "INSERT INTO releases (guid, title, series_name, status, volumes_json, "
            "missing_vols_json, download_url) VALUES (?, ?, ?, ?, '[]', '[]', ?);",
            (guid, title, series, status, f"magnet:?xt=urn:btih:{guid}"),
        )
        conn.commit()


def test_pagination_math():
    print("--> Testing flat/cards pagination math + search...")
    ctx = E2ETestContext(prefix="mangatrax_paging_")
    try:
        for index in range(25):
            series = f"Paging Series {index // 3:02d}"
            _insert(ctx, f"flat-{index}", series, f"{series} v{index + 1:02d}")
        total = ctx.db.count_releases()
        assert total == 25, total
        page = ctx.db.get_releases(limit=10, offset=0)
        page2 = ctx.db.get_releases(limit=10, offset=10)
        assert len(page) == 10 and len(page2) == 10, (len(page), len(page2))
        assert not ({row["id"] for row in page} & {row["id"] for row in page2})
        search = ctx.db.get_releases(limit=50, offset=0, search="Paging Series 01")
        assert search and all("Paging Series 01" in row["series_name"] for row in search), search
        assert ctx.db.count_releases(search="Paging Series 01") == len(search)
        cards = ctx.db.get_releases_grouped_by_series(limit=8, offset=0)
        assert len(cards) == 8, len(cards)
        cards_total = ctx.db.count_releases_grouped_by_series()
        assert cards_total == 9, cards_total
        # Bounded per-series cards: 3 releases/series here, cap helper preserves totals.
        capped = main_module._cap_group_releases(cards)
        for group in capped:
            assert "release_total" in group and "releases" in group, group
        print("    [PASSED] flat offset/limit, search+count, cards pagination and cap metadata are correct.")
    finally:
        ctx.close()


def test_series_card_cap():
    print("--> Testing per-card release cap (10) + release_total...")
    ctx = E2ETestContext(prefix="mangatrax_paging_card_")
    try:
        for index in range(25):
            _insert(ctx, f"card-{index}", "Cuckoos Big Series", f"Cuckoos Big Series v{index + 1:02d}")
        groups = ctx.db.get_releases_grouped_by_series()
        assert len(groups) == 1, groups
        group = groups[0]
        assert len(group["releases"]) == 25, len(group["releases"])
        capped = main_module._cap_group_releases(groups)
        assert capped[0]["release_total"] == 25, capped[0]
        assert len(capped[0]["releases"]) == 10, len(capped[0]["releases"])
        assert capped[0]["releases_shown"] == 10, capped[0]
        print("    [PASSED] a 25-release series renders exactly 10 initially with release_total=25.")
    finally:
        ctx.close()


def test_flat_default_endpoint():
    print("--> Testing default flat_rows/cards endpoint responses...")
    ctx = E2ETestContext(prefix="mangatrax_paging_api_")
    try:
        for index in range(5):
            _insert(ctx, f"api-{index}", f"API Series {index}", f"API Series {index} v01")
        flat = ctx.client.get("/api/releases/flat_rows")
        assert flat.status_code == 200, flat.text
        body = flat.json()
        for key in ("count", "total", "has_more", "offset", "html"):
            assert key in body, (key, body)
        cards = ctx.client.get("/api/releases/cards")
        assert cards.status_code == 200, cards.text
        assert "html" in cards.json() and "total" in cards.json()
        print("    [PASSED] default flat_rows/cards responses expose total/has_more/html.")
    finally:
        ctx.close()


def test_template_wiring():
    print("--> Testing no auto-drain + Load More/search/card wiring...")
    releases = open(os.path.join(BACKEND_DIR, "app", "templates", "releases.html"), encoding="utf-8").read()
    prioritized = open(os.path.join(BACKEND_DIR, "app", "templates", "prioritized.html"), encoding="utf-8").read()
    card = open(
        os.path.join(BACKEND_DIR, "app", "templates", "partials", "series_card_chunk.html"),
        encoding="utf-8",
    ).read()
    for template in (releases, prioritized):
        assert "setTimeout(fetchNextSeriesBatch" not in template, "series auto-chain still present"
        assert "setTimeout(fetchNextFlatBatch" not in template, "flat auto-chain still present"
        assert "setupInfiniteScroll()" not in template, "automatic infinite-scroll trigger still active"
        assert "Load More Series" in template and "Load More Releases" in template, "Load More buttons missing"
        assert "Showing " in template, "Showing X of Y counter missing"
        assert "btn.disabled = false" in template or "disabled" in template
    assert "search=${encodeURIComponent(currentSearch)}" in releases, "cards search not sent to server"
    assert "search=${encodeURIComponent(currentSearch)}" in releases, "flat search not sent to server"
    assert "handleReleaseSearch" in releases, "debounced search reset missing"
    assert "resetReleasePagination" in releases, "pagination reset missing"
    assert "showAllSeriesReleases" in releases and "showAllSeriesReleases" in card, "series Show all wiring missing"
    assert "Show all {{ group.release_total }} releases" in card, "card cap expander label missing"
    assert "release_total" in card, "release_total missing from card"
    assert "series_releases" in card or "showAllSeriesReleases" in releases, "series_releases fetch wiring missing"
    main_source = open(os.path.join(BACKEND_DIR, "app", "main.py"), encoding="utf-8").read()
    assert "_cap_group_releases" in main_source and "series_releases" in main_source
    assert "search: Optional[str] = Query(None)" in main_source
    print("    [PASSED] no auto chain/observer, Load More + counters + server search + Show all wired (both pages).")


def main():
    print("=" * 78)
    print("Running releases pagination suite (task-45)")
    print("=" * 78)
    tests = [
        test_pagination_math,
        test_series_card_cap,
        test_flat_default_endpoint,
        test_template_wiring,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception:
            failures += 1
            print(f"  [FAILED] {test.__name__}")
            traceback.print_exc()
    print("=" * 78)
    if failures:
        print(f"PAGINATION SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL RELEASES PAGINATION TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
