#!/usr/bin/env python3
"""Releases-page Load More availability regression (load-more button must exist from
first paint and stay available whenever more pages exist).

Covers:
* the grouped-series container is no longer `display:none` (which used to hide the
  button on any error path),
* both Load More buttons are rendered up front (disabled "Loading..." until the first
  batch resolves) instead of behind a `hidden` class,
* a sticky/floating Load More control exists for grouped and flat views,
* every fetch path calls the single updateLoadMoreUi() helper (start, success,
  empty, error/finally), and cosmetic post-processing cannot block it,
* search/filtering no longer hides the Load More control (paging is server-side),
* the views pass a server-rendered `initial_total` so the control can exist even
  before the first API round-trip,
* the prioritized page has the same treatment.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(BASE_DIR, "backend", "app", "templates")
MAIN = os.path.join(BASE_DIR, "backend", "app", "main.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_releases_page():
    releases = _read(os.path.join(TEMPLATES, "releases.html"))

    # 1. The grouped container must not start hidden.
    assert 'id="load-more-series-container" class="text-center py-6" style="display: none;"' not in releases, (
        "series load-more container must not start display:none"
    )
    assert "load-more-series-container" in releases

    # 2. Buttons exist from first paint (disabled + explicit label span).
    assert 'id="btn-load-more-series" disabled' in releases, "series load-more button must render disabled, not hidden"
    assert 'id="btn-load-more-flat" disabled' in releases, "flat load-more button must render disabled, not hidden"
    assert 'class="hidden px-5 py-2.5' not in releases, "series load-more button must not start with hidden class"
    assert "load-more-series-label" in releases and "load-more-flat-label" in releases
    assert "deferred-series-count" in releases and "deferred-flat-count" in releases

    # 3. Sticky control for long pages.
    assert 'id="sticky-load-more"' in releases, "sticky load-more control missing"
    assert "clickStickyLoadMore" in releases, "sticky control is not wired"
    assert "sticky-load-more-text" in releases

    # 4. Single visibility helper used by every path.
    assert "function updateLoadMoreUi" in releases, "updateLoadMoreUi helper missing"
    assert releases.count("updateLoadMoreUi()") >= 8, (
        f"updateLoadMoreUi must be called on start/success/empty/error paths (found {releases.count('updateLoadMoreUi()')})"
    )
    assert "seriesLoadError = true" in releases and "flatLoadError = true" in releases, (
        "error paths must mark the retry state for the helper"
    )
    assert "seriesLoadError = false" in releases and "flatLoadError = false" in releases
    assert "p.error ? 'Retry loading'" in releases, "helper must switch the button to a retry state"
    assert "updateAllCardSummaries();" in releases
    assert "never block the Load More control" in releases, "cosmetic post-processing must be wrapped"

    # 5. Filtering must not hide the control.
    assert "seriesMore.style.display = 'none'" not in releases, "filterReleases must not hide the series control"
    assert "flatMore.style.display = 'none'" not in releases, "filterReleases must not hide the flat control"

    # 6. Server-rendered total.
    assert "initial_total" in releases, "releases template must consume initial_total"
    assert "{{ initial_total if current_view != 'flat' else 0 }}" in releases

    # 7. Both fetchers keep the helper in their finally block (covers errors/empty).
    assert releases.count("isLoadingSeries = false;\n        updateLoadMoreUi();") >= 1
    assert releases.count("isLoadingFlat = false;\n        updateLoadMoreUi();") >= 1

    print("    [PASSED] releases.html: Load More present from first paint, sticky control, retry/error state, no filter hiding.")


def test_prioritized_page():
    prio = _read(os.path.join(TEMPLATES, "prioritized.html"))
    assert 'id="btn-load-more-series" disabled' in prio
    assert 'id="btn-load-more-flat" disabled' in prio
    assert "function updateLoadMoreUi" in prio
    assert 'id="sticky-load-more"' in prio
    assert "clickStickyLoadMore" in prio
    assert "seriesLoadError = true" in prio and "flatLoadError = true" in prio
    assert "seriesMore.style.display = 'none'" not in prio
    assert "flatMore.style.display = 'none'" not in prio
    assert "initial_total" in prio
    assert prio.count("isLoadingSeries = false;\n        updateLoadMoreUi();") >= 1
    assert prio.count("isLoadingFlat = false;\n        updateLoadMoreUi();") >= 1
    print("    [PASSED] prioritized.html: same availability/retry/sticky behaviour.")


def test_views_pass_initial_total():
    main_src = _read(MAIN)
    for marker in ("async def releases_view", "async def prioritized_view"):
        start = main_src.find(marker)
        assert start >= 0, f"{marker} missing"
        window = main_src[start:start + 1800]
        assert "initial_total" in window, f"{marker} must compute/pass initial_total"
    assert "count_releases(status=status)" in main_src, "flat view must count releases up front"
    assert "count_releases_grouped_by_series(status=status)" in main_src, "grouped view must count series up front"
    print("    [PASSED] releases_view/prioritized_view pass a server-rendered initial_total.")


def run():
    test_releases_page()
    test_prioritized_page()
    test_views_pass_initial_total()
    print("ALL LOAD-MORE AVAILABILITY TESTS PASSED")


if __name__ == "__main__":
    run()
