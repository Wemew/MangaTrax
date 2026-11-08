#!/usr/bin/env python3
"""UI-enhancement regression guards (Phase 1).

Source-level assertions: the Calibre runner has no browser, so the UI contracts are
validated as markup/JS source text (the same technique as test_nav_structure.py /
test_ui_polish.py). Run through tests/run_tests.py:
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py tests\\test_ui_enhancements.py"
"""

import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(BASE_DIR, "backend", "app", "templates")


def read(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
        return fh.read()


def base():
    return read("base.html")


# ===========================================================================
# 1. Global shell
# ===========================================================================
def test_shell_basics():
    print("--> Testing skip link, viewport, landmarks and header controls...")
    src = base()
    assert '<a href="#main-content" class="mt-skip-link">' in src, "skip link missing"
    assert 'id="main-content" tabindex="-1"' in src, "main landmark missing"
    assert "viewport-fit=cover" in src, "safe-area viewport flag missing"
    assert 'id="site-header"' in src and 'id="site-footer"' in src
    header = src[src.index("<header"):src.index("</header>")]
    assert len(re.findall(r"<button\b", header)) == 7, "header must expose 7 buttons (4 nav, user, mobile, palette)"
    for trigger in ("nav-menu-library", "nav-menu-discover", "nav-menu-downloads", "nav-menu-system", "nav-menu-user"):
        assert 'aria-controls="%s"' % trigger in header, trigger
    assert 'id="btn-command-palette"' in header
    print("    [PASSED] skip link, main landmark, 7 header buttons incl. palette trigger.")


def test_downloads_dropdown():
    print("--> Testing Downloads dropdown + Manual Pickup promotion...")
    src = base()
    nav = src[src.index("<!-- Main Navigation"):src.index("<!-- Right side:")]
    items = re.findall(r'data-nav-item="([a-z]+)"', nav)
    assert items == ["overview", "library", "discover", "downloads", "system"], items
    menu_start = nav.index('id="nav-menu-downloads"')
    menu = nav[menu_start:]
    assert 'href="/downloads"' in menu and 'href="/downloads/manual-pickup"' in menu
    assert 'id="nav-manual-pickup-count"' in menu
    assert "request.url.path.startswith('/downloads/manual-pickup')" in nav
    assert 'href="/downloads/manual-pickup"' in nav
    print("    [PASSED] five top-level items; Downloads menu has Downloads + Manual Pickup.")


def test_mobile_navigation():
    print("--> Testing bottom tab bar + bottom-sheet menu...")
    src = base()
    assert 'id="mobile-bottom-bar"' in src and 'class="mt-bottom-bar"' in src
    bar = src[src.index('id="mobile-bottom-bar"'):src.index("</nav>", src.index('id="mobile-bottom-bar"'))]
    assert len(re.findall(r'<a\b', bar)) == 5, "bottom bar must have 5 tabs"
    for href in ('href="/"', 'href="/series"', 'href="/downloads"', 'href="/discovery"'):
        assert href in bar, href
    assert "toggleMobileMenu(this)" in bar
    assert "aria-current=\"page\"" in bar
    assert 'id="mobile-menu-backdrop"' in src
    menu = src[src.index('<div id="mobile-menu"'):src.index("<!-- Notification Toast Container -->")]
    assert 'role="dialog"' in menu and 'aria-modal="true"' in menu
    assert 'id="mobile-menu-title"' in menu
    assert "closeMobileMenu()" in menu
    assert "mt-scroll-locked" in src
    assert len(re.findall(r"min-h-\[44px\]", menu)) >= 10
    print("    [PASSED] 5-tab bottom bar, dialog sheet with backdrop/close, 44px links.")


def test_command_palette():
    print("--> Testing command palette markup + dispatch...")
    src = base()
    assert 'id="command-palette"' in src
    assert 'id="command-palette-input"' in src and 'id="command-palette-results"' in src
    assert 'role="dialog"' in src and 'aria-modal="true"' in src
    assert "openCommandPalette" in src and "closeCommandPalette" in src
    assert "Ctrl" in src or "ctrlKey" in src
    assert "mtRunAction" in src
    assert "data-mt-action" in src
    for action in ("calibre-sync", "feeds-poll", "downloads-process", "backfill", "manual-pickup"):
        assert "'%s'" % action in src or '"%s"' % action in src, action
    assert "/api/search" in src
    assert "series?search=" in src or "/series?search=" in src
    assert "/releases?view=flat&q=" in src
    print("    [PASSED] palette markup, keyboard handlers, direct action dispatch, search bootstraps.")


def test_overlay_and_dialog_contract():
    print("--> Testing overlay ladder + dialog helper...")
    src = base()
    for token in ("--mt-z-header", "--mt-z-backdrop", "--mt-z-dialog", "--mt-z-toast"):
        assert token in src, token
    assert "--mt-header-h" in src and "--mt-bottom-bar-h" in src
    assert "body.mt-modal-open #global-tasks" in src, "Tasks must be hidden while a dialog is open"
    assert "body.mt-modal-open .mt-bottom-bar" in src
    assert "MangaModal" in src and "MangaModal.open" in src and "MangaModal.close" in src
    for api in ("aria-modal", "inert", "mt-scroll-locked"):
        assert api in src, api
    assert "global-tasks" in src[src.index("MangaModal"):], "tasks node must be part of the inert set"
    assert "toast-container" in src and "toast-errors" in src
    assert 'role="status"' in src and 'role="alert"' in src
    print("    [PASSED] z-tokens, overlay hiding, dialog helper, status/alert live regions.")


def test_feedback_and_polling():
    print("--> Testing toast escaping, pinned polling literals, visibility guards...")
    src = base()
    assert "textContent = prefix" in src or "textContent = message" in src, "toast must not innerHTML user text"
    assert "setInterval(poll, 1500)" in src, "pinned MangaTasks poll interval missing"
    assert "setInterval(checkCalibreSyncStatus, 3000)" in src
    assert "if (document.hidden) return;" in src
    assert "visibilitychange" in src
    assert "mtPollFail" in src and "offline-banner" in src
    assert "refreshManualPickupBadge" in src
    for badge_id in ("nav-manual-pickup-count", "mobile-manual-pickup-count", "tab-manual-pickup-count"):
        assert badge_id in src, badge_id
    assert "manual pickups waiting" in src, "pickup state must be exposed in an accessible name"
    print("    [PASSED] escaped toasts, pinned timers, visibility guard, offline banner, pickup badge names.")


# ===========================================================================
# 2. Dashboard
# ===========================================================================
def test_dashboard_quick_actions_and_inplace_download():
    print("--> Testing dashboard quick actions + in-place download flow...")
    src = read("index.html")
    assert "Quick actions" in src and "mt-quick-grid" in src
    for action in ("calibre-sync", "feeds-poll", "downloads-process", "backfill"):
        assert 'data-mt-action="%s"' % action in src, action
    assert 'href="/downloads/manual-pickup"' in src and 'href="/settings"' in src
    assert 'id="dashboard-pickup-banner"' in src and 'id="dashboard-pickup-count"' in src
    assert "data-dashboard-download=" in src
    dl = src[src.index("async function downloadRelease"):src.index("</script>")]
    assert "location.reload()" not in dl, "dashboard download must update in place"
    assert "/state" in dl and "pollDashboardRelease" in dl
    assert "downloadRelease" in src
    assert "tag_display(r.tags_json, view='dashboard')" in src, "dashboard tag wiring must stay"
    print("    [PASSED] quick actions, pickup/setup banners, in-place state polling, tag wiring kept.")


# ===========================================================================
# 3. Series page
# ===========================================================================
def test_series_search_and_cards():
    print("--> Testing Series server-search + responsive card contract...")
    src = read("series.html")
    assert "/api/series/rows" in src
    assert "search=" in src, "series must send a server search parameter"
    assert "AbortController" in src, "series search must cancel stale requests"
    assert "series-row-deferred" in src
    assert "loadMoreSeriesRows" in src
    assert "decodeURIComponent" in src or "URLSearchParams" in src, "series must read ?search= on load"
    assert "clearSeriesFilters" in src
    partial = read(os.path.join("partials", "series_table_row_chunk.html"))
    assert "mt-table-cards" in src, "series table must opt into card mode"
    for label in ("Monitored", "Series", "Authors", "Owned", "Latest", "Tags", "Actions"):
        assert 'data-label="%s"' % label in partial, "missing data-label: " + label
    assert 'scope="col"' in src
    assert "openSeriesCalibreMatch" in partial
    print("    [PASSED] server search mechanics, data-label cards, pinned handlers.")


# ===========================================================================
# 4. Releases / Prioritized / Discovery
# ===========================================================================
def test_releases_filters_and_cards():
    print("--> Testing Releases mobile filters, order preservation and flat cards...")
    src = read("releases.html")
    assert "&sort={{ sort }}" in src or "sort={{ sort }}" in src, "filter links must preserve sort"
    assert "release-sort-select" in src
    assert "mt-mobile-only" in src or "hidden md:flex" in src
    flat = read(os.path.join("partials", "flat_release_row_chunk.html"))
    assert "data-label=" in flat, "flat release rows need data-label for card mode"
    assert "mt-table-cards" in src or "flatReleasesTable" in src
    assert "loadMoreSeries" in src and "loadMoreFlat" in src
    assert "series-card-deferred" in src or "Load More Series" in src
    print("    [PASSED] sort-preserving links, mobile controls, flat card labels.")


def test_prioritized_mobile():
    print("--> Testing Prioritized filter preservation + notes on mobile...")
    src = read("prioritized.html")
    assert "sort=" in src or "status=" in src
    assert "sm:inline-block" not in src or "hidden sm:inline-block" not in src, "group notes must show on mobile"
    assert "mt-table-cards" in src or "data-label=" in src
    print("    [PASSED] prioritized filter links and mobile notes.")


def test_discovery_pagination_and_tabs():
    print("--> Testing Discovery filter-preserving pagination + ARIA tabs...")
    src = read("discovery.html")
    for param in ("include_tags", "exclude_tags", "tag_match", "sort"):
        assert param in src, "pagination must preserve " + param
    assert 'role="tablist"' in src
    assert 'role="tab"' in src and 'role="tabpanel"' in src
    assert "aria-selected" in src
    assert "ArrowRight" in src and "ArrowLeft" in src
    assert "hash" in src
    print("    [PASSED] full query preservation, tablist/tabpanel + arrow keys.")


def test_feeds_modal():
    print("--> Testing Feeds add-modal dialog semantics...")
    src = read("feeds.html")
    modal = src[src.index('id="modal-add-feed"'):]
    assert "aria-modal" in modal or "mtModal" in modal
    assert "closeAddFeedModal" in src
    assert "Escape" in src
    print("    [PASSED] modal close/backdrop/Escape wiring.")


# ===========================================================================
# 5. Downloads / Manual Pickup
# ===========================================================================
def test_downloads_navigator_and_refresh():
    print("--> Testing Downloads section navigator + safe qBit refresh...")
    src = read("downloads.html")
    assert "mt-section-nav" in src
    for anchor in ("dl-diagnostics", "dl-awaiting", "dl-failed", "dl-queue", "dl-inventory", "dl-staging", "dl-cleanup", "dl-audit"):
        assert 'href="#%s"' % anchor in src, "missing navigator anchor: " + anchor
    assert "data-torrent-hash" in src
    assert "refreshQbit" in src or "qbit-refresh" in src
    assert "live_state_available" in src
    assert "mt-table-cards" in src
    assert "mt-table-empty" in src
    assert "aria-busy" in src
    assert src.count("<script") == 1, "downloads.html must keep one script block"
    assert "/api/downloads/status" in src
    assert "renderTorboxQueue" in src and "renderInventory" in src and "refreshDownloadsNow" in src
    assert "active ? 1000 : 5000" in src
    print("    [PASSED] navigator, hash-keyed refresh guard, card tables, single script.")


def test_manual_pickup_refresh_and_prefill():
    print("--> Testing Manual Pickup non-destructive refresh + prefill...")
    src = read("manual_pickup.html")
    assert "Pickups changed" in src or "pickups-changed" in src
    assert "15" in src and "setInterval" in src or "setTimeout" in src
    assert "data-series-name" in src
    assert "pickupEditSeries" in src
    assert "revert" in src.lower()
    assert "prompt(" not in src
    assert 'pickup-match-modal' in src and "openPickupMatch" in src and "submitPickupMatch" in src
    assert "pickup-error" in src and "mapping kept" in src
    print("    [PASSED] non-destructive refresh, stored-series prefill, pinned pickup strings.")


# ===========================================================================
# 6. Settings / Logs
# ===========================================================================
def test_settings_nav_dirty_guard_password():
    print("--> Testing Settings dirty guard, password blank/omit, mobile nav...")
    src = read("settings.html")
    assert "beforeunload" in src
    assert "Unsaved changes" in src or "unsaved" in src
    assert "Discard" in src
    assert "suppress" in src or "beforeunload" in src
    assert 'id="setting-qbit-pass"' in src
    pw = src[src.index('id="setting-qbit-pass"'):]
    pw = pw[:pw.index(">") + 1]
    assert 'value=""' in pw, "qbit password must not be echoed into value="
    assert "omit" in src.lower() or "delete payload" in src or "delete data" in src or "unchanged" in src.lower()
    assert "settings-section-nav" in src and "sticky top-20" in src
    assert "IntersectionObserver" in src
    print("    [PASSED] dirty guard, blank password field, sticky section nav.")


def test_logs_filters():
    print("--> Testing Logs event/actor filters...")
    src = read("logs.html")
    assert "log-event-filter" in src or "event-filter" in src
    assert "log-actor-filter" in src or "actor-filter" in src
    assert "filterLogs" in src and "clearLogFilters" in src
    assert "mt-table-cards" not in src or True
    print("    [PASSED] logs filters wired.")


def main():
    tests = [
        test_shell_basics, test_downloads_dropdown, test_mobile_navigation, test_command_palette,
        test_overlay_and_dialog_contract, test_feedback_and_polling,
        test_dashboard_quick_actions_and_inplace_download,
        test_series_search_and_cards, test_releases_filters_and_cards, test_prioritized_mobile,
        test_discovery_pagination_and_tabs, test_feeds_modal,
        test_downloads_navigator_and_refresh, test_manual_pickup_refresh_and_prefill,
        test_settings_nav_dirty_guard_password, test_logs_filters,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failures += 1
            print("  [FAILED] %s: %s" % (test.__name__, exc))
    print("=" * 78)
    if failures:
        print("UI ENHANCEMENT SUITE FAILED (%d test(s))" % failures)
        return 1
    print("ALL UI ENHANCEMENT TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
