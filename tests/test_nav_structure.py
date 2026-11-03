#!/usr/bin/env python3
"""task-15 suite: top-nav declutter (9 flat links -> 5 grouped items + overflow).

Source-level template assertions (no HTTP needed): the honest runner renders nothing,
so we validate the markup contract that the browser/E2E exercises.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import os
import re
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
BASE_TEMPLATE = os.path.join(BACKEND_DIR, "app", "templates", "base.html")

ROUTES = {
    "overview": "/",
    "series": "/series",
    "releases": "/releases",
    "prioritized": "/prioritized",
    "discovery": "/discovery",
    "feeds": "/feeds",
    "downloads": "/downloads",
    "logs": "/logs",
    "settings": "/settings",
}

ACTIVE_PAGES = [
    "dashboard", "series", "releases", "prioritized",
    "discovery", "feeds", "downloads", "logs", "settings",
]


def _source():
    with open(BASE_TEMPLATE, encoding="utf-8") as handle:
        return handle.read()


def _segment(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _desktop_nav(source):
    return _segment(
        source,
        "<!-- Main Navigation (5 grouped top-level items; task-15) -->",
        "<!-- Right side:",
    )


def _nav_item(nav, name):
    marker = 'data-nav-item="%s"' % name
    start = nav.index(marker)
    following = nav.find('data-nav-item="', start + len(marker))
    end = following if following != -1 else len(nav)
    return nav[start:end]


def _mobile_menu(source):
    return _segment(source, '<div id="mobile-menu"', "<!-- Notification Toast Container -->")


# ===========================================================================
# 1. Exactly 5 top-level items, in the required order
# ===========================================================================
def test_five_top_level_items():
    print("--> Testing desktop nav has exactly 5 grouped top-level items...")
    nav = _desktop_nav(_source())
    items = re.findall(r'data-nav-item="([a-z]+)"', nav)
    assert items == ["overview", "library", "discover", "downloads", "system"], items

    # Overview + Downloads are direct links; Library/Discover/System are dropdowns.
    assert '<a href="/" data-nav-item="overview"' in nav, "Overview link missing"
    assert '<a href="/downloads" data-nav-item="downloads"' in nav, "Downloads link missing"
    for name in ("library", "discover", "system"):
        item = _nav_item(nav, name)
        assert "<button" in item, (name, item)
        assert "data-dropdown-trigger" in item, (name, item)
    # Old flat top-level links are gone.
    for name in ("series", "releases", "prioritized", "discovery", "feeds", "logs", "settings"):
        assert 'data-nav-item="%s"' % name not in nav, "old flat link still top-level: %s" % name
    print("    [PASSED] 5 items; the 7 former flat links are dropdown children now.")


# ===========================================================================
# 2. Dropdown triggers are accessible buttons with the right children
# ===========================================================================
def test_dropdown_contract():
    print("--> Testing dropdown trigger/menu contract...")
    source = _source()
    nav = _desktop_nav(source)
    expected_children = {
        "library": ["/series", "/releases", "/prioritized"],
        "discover": ["/discovery", "/feeds"],
        "system": ["/logs", "/settings"],
    }
    for name, hrefs in expected_children.items():
        item = _nav_item(nav, name)
        assert 'data-dropdown-trigger' in item, name
        assert 'aria-haspopup="true"' in item, name
        assert 'aria-expanded="false"' in item, name
        assert 'aria-controls="nav-menu-%s"' % name in item, name
        menu_id = 'id="nav-menu-%s"' % name
        assert menu_id in item, (name, item)
        mi = item.index(menu_id)
        menu = item[mi:]
        for href in hrefs:
            assert 'href="%s"' % href in menu, (name, href, menu)
        # Children are real links (role=menuitem) inside a role=menu panel.
        assert 'role="menu"' in menu, name
        assert 'role="menuitem"' in menu, name
        # Child links carry nav-tab so the existing instant-load spinner applies.
        assert "nav-tab" in menu, name

    # Parent items highlight when any child page is active.
    assert "active_page in ['series', 'releases', 'prioritized']" in nav
    assert "active_page in ['discovery', 'feeds']" in nav
    assert "active_page in ['logs', 'settings']" in nav
    # Amber treatment for Prioritized is preserved.
    assert "text-amber-400" in _nav_item(nav, "library")
    print("    [PASSED] every dropdown: button trigger + aria + correct child links.")


# ===========================================================================
# 3. Every one of the 9 routes remains reachable (desktop + mobile)
# ===========================================================================
def test_all_routes_reachable():
    print("--> Testing all 9 destinations remain reachable...")
    source = _source()
    desktop = _desktop_nav(source)
    mobile = _mobile_menu(source)
    for label, href in ROUTES.items():
        assert 'href="%s"' % href in desktop, ("desktop", label, href)
        assert 'href="%s"' % href in mobile, ("mobile", label, href)
    print("    [PASSED] all 9 routes present in desktop and mobile navigation.")


# ===========================================================================
# 4. No page-action buttons in the global header (they live on owning pages)
# ===========================================================================
def test_action_buttons():
    print("--> Testing the header has no action buttons (page-owned actions)...")
    source = _source()
    templates_dir = os.path.dirname(BASE_TEMPLATE)
    header = _segment(source, "<!-- Right side:", "</header>")

    # No page-action call sites in the header, and no overflow menu markup at all.
    for call in ('onclick="triggerCalibreSync()"', 'onclick="triggerStagingTag()"',
                 'onclick="triggerFeedCheck()"'):
        assert call not in header, call
    assert "nav-menu-actions" not in source, "overflow menu markup still in base.html"
    assert "more-horizontal" not in source, "overflow menu trigger still in base.html"
    assert 'aria-label="More maintenance actions"' not in source

    # Sync Calibre now lives on the Series page (with the ids the spinner uses).
    with open(os.path.join(templates_dir, "series.html"), encoding="utf-8") as handle:
        series = handle.read()
    assert series.count('onclick="triggerCalibreSync()"') == 1, series.count('onclick="triggerCalibreSync()"')
    assert 'id="btn-sync-calibre"' in series
    assert 'id="icon-calibre-sync"' in series

    # Tag CBZ lives on the Downloads page (with the ids the spinner uses).
    with open(os.path.join(templates_dir, "downloads.html"), encoding="utf-8") as handle:
        downloads = handle.read()
    assert downloads.count('onclick="triggerStagingTag()"') == 1, downloads.count('onclick="triggerStagingTag()"')
    assert 'id="btn-tag-staging"' in downloads
    assert 'id="icon-staging-tag"' in downloads

    # Shared JS definitions stay in base.html (the pages call them).
    assert "async function triggerCalibreSync()" in source
    assert "async function triggerStagingTag()" in source
    print("    [PASSED] header has no actions; Sync on Series, Tag on Downloads, JS shared.")


# ===========================================================================
# 4a. Header buttons are only nav/user/mobile triggers
# ===========================================================================
def test_header_button_audit():
    print("--> Testing header buttons are nav/menu triggers only...")
    source = _source()
    header = _segment(source, "<header", "</header>")
    buttons = re.findall(r"<button\b", header)
    # 3 nav dropdown triggers + user menu trigger + mobile menu trigger.
    assert len(buttons) == 5, f"unexpected header buttons: {len(buttons)}"
    for trigger in ("nav-menu-library", "nav-menu-discover", "nav-menu-system", "nav-menu-user"):
        assert 'aria-controls="%s"' % trigger in header, trigger
    assert 'id="btn-mobile-menu"' in header
    # The mobile menu must not list action buttons either.
    mobile = _mobile_menu(source)
    for call in ('onclick="triggerCalibreSync()"', 'onclick="triggerStagingTag()"',
                 'onclick="triggerFeedCheck()"'):
        assert call not in mobile, call
    assert ">Actions<" not in mobile, "mobile menu still has the Actions group"
    print("    [PASSED] header = 5 triggers (3 nav, user, mobile); mobile menu has no actions.")


# ===========================================================================
# 4b. Poll Feeds exists exactly once, on the Feeds page
# ===========================================================================
def test_poll_feeds_single_trigger():
    print("--> Testing Poll Feeds has exactly one call site (feeds.html)...")
    templates_dir = os.path.dirname(BASE_TEMPLATE)
    call_sites = []
    definition_sites = []
    for name in sorted(os.listdir(templates_dir)):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(templates_dir, name), encoding="utf-8") as handle:
            text = handle.read()
        call_sites.extend((name, line) for line in text.splitlines() if 'onclick="triggerFeedCheck()"' in line)
        definition_sites.extend((name, line) for line in text.splitlines() if "async function triggerFeedCheck()" in line)

    assert [name for name, _line in call_sites] == ["feeds.html"], call_sites
    assert [name for name, _line in definition_sites] == ["base.html"], definition_sites

    with open(os.path.join(templates_dir, "feeds.html"), encoding="utf-8") as handle:
        feeds = handle.read()
    assert 'id="btn-check-feeds"' in feeds
    assert 'id="icon-feed-check"' in feeds
    assert "openAddFeedModal()" in feeds
    # Poll Feeds is visually secondary to Add Feed (Add Feed stays the primary).
    poll_button = feeds[feeds.index('id="btn-check-feeds"'):feeds.index("openAddFeedModal()")]
    assert "bg-slate-800" in poll_button and "bg-brand-600" not in poll_button, poll_button
    # Old header/mobile/discovery triggers are gone.  The shared JS definition may
    # still reference the ids of the Feeds-page button; only markup call sites matter.
    with open(BASE_TEMPLATE, encoding="utf-8") as handle:
        base = handle.read()
    assert 'onclick="triggerFeedCheck()"' not in base, "Poll Feeds call site still in base.html"
    assert "<button" not in base[max(0, base.find("btn-check-feeds") - 160):base.find("btn-check-feeds")], (
        "Poll Feeds button markup still in base.html"
    )
    with open(os.path.join(templates_dir, "discovery.html"), encoding="utf-8") as handle:
        discovery = handle.read()
    assert "triggerFeedCheck" not in discovery, "Discovery still has a Poll Feeds trigger"
    print("    [PASSED] one call site (feeds.html); function definition stays in base.html.")


# ===========================================================================
# 5. Auth collapses into one user menu
# ===========================================================================
def test_user_menu():
    print("--> Testing auth user menu...")
    source = _source()
    header = _segment(source, "<!-- Right side:", "</header>")
    user_menu = _segment(header, 'id="nav-menu-user"', "</div>")
    assert "auth_actor()" in header, "actor name must be displayed"
    assert 'href="/auth/logout"' in user_menu, "Sign out must live in the user dropdown"
    assert "Sign out" in user_menu
    assert 'aria-haspopup="true"' in header[:header.index('id="nav-menu-user"')]
    # The old separate chip + sign-out link are gone from the header bar itself.
    standalone = header[:header.index('id="nav-menu-user"')]
    assert 'href="/auth/logout"' not in standalone, "standalone Sign out link still in header"
    print("    [PASSED] actor + Sign out collapsed into one user dropdown.")


# ===========================================================================
# 6. Mobile menu mirrors the grouping and has 44px tap targets
# ===========================================================================
def test_mobile_menu():
    print("--> Testing mobile hamburger menu grouping...")
    source = _source()
    mobile = _mobile_menu(source)
    hamburger = _segment(source, 'id="btn-mobile-menu"', "</button>")
    assert 'aria-haspopup="true"' in hamburger
    assert 'aria-expanded="false"' in hamburger
    assert 'aria-controls="mobile-menu"' in hamburger
    assert 'aria-label="Open navigation menu"' in hamburger
    assert 'onclick="toggleMobileMenu()"' in hamburger

    for heading in ("Library", "Discover", "System"):
        assert ">%s<" % heading in mobile, heading
    for label, href in ROUTES.items():
        assert 'href="%s"' % href in mobile, ("mobile route", label)
    # No page-action controls in the mobile menu (they live on the owning pages).
    for call in ('onclick="triggerCalibreSync()"', 'onclick="triggerStagingTag()"', 'onclick="triggerFeedCheck()"'):
        assert call not in mobile, call
    assert ">Actions<" not in mobile, "mobile menu still has the Actions group"
    assert "Poll Feeds" not in mobile, "Poll Feeds must not be in the mobile menu"
    # Tap targets: every mobile nav link/action is at least 44px tall.
    assert len(re.findall(r"min-h-\[44px\]", mobile)) >= 10, "tap targets too small/missing"
    print("    [PASSED] hamburger + grouped mobile menu + 44px tap targets.")


# ===========================================================================
# 7. Dropdown JS behaviour (click, outside click, Escape, focus return)
# ===========================================================================
def test_dropdown_js():
    print("--> Testing dropdown JS behaviour source...")
    source = _source()
    assert "function toggleDropdown(trigger)" in source
    assert "function closeAllDropdowns(" in source
    assert "function toggleMobileMenu()" in source
    assert "function closeMobileMenu()" in source
    # Outside-click closes; Escape closes and returns focus to the trigger.
    assert "event.target.closest('[data-dropdown-menu]')" in source
    assert "event.key !== 'Escape'" in source
    assert "trigger.focus()" in source
    # aria-expanded is kept in sync by both open paths.
    assert source.count("setAttribute('aria-expanded'") >= 4
    # Existing instant-load spinner still targets nav links (nav + nav-tab classes).
    assert "nav-link-loading" in source
    assert "link.closest('nav')" in source
    print("    [PASSED] click/outside/Escape/focus behaviour and loading spinner preserved.")


# ===========================================================================
# 8. Every active_page value is still handled
# ===========================================================================
def test_active_page_values():
    print("--> Testing every active_page value still drives an active state...")
    source = _source()
    for value in ACTIVE_PAGES:
        assert "active_page == '%s'" % value in source, value
    # Parent mapping covers all children (no page loses its highlight).
    parents = {
        "Library": ["series", "releases", "prioritized"],
        "Discover": ["discovery", "feeds"],
        "System": ["logs", "settings"],
    }
    for parent, children in parents.items():
        assert "active_page in [%s]" % ", ".join("'%s'" % child for child in children) in source, parent
    print("    [PASSED] dashboard/series/releases/prioritized/discovery/feeds/downloads/logs/settings.")


def main():
    print("=" * 78)
    print("Running top-nav structure suite (task-15)")
    print("=" * 78)
    tests = [
        test_five_top_level_items,
        test_dropdown_contract,
        test_all_routes_reachable,
        test_action_buttons,
        test_header_button_audit,
        test_poll_feeds_single_trigger,
        test_user_menu,
        test_mobile_menu,
        test_dropdown_js,
        test_active_page_values,
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
        print(f"NAV SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL NAV TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
