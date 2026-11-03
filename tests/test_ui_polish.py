#!/usr/bin/env python3
"""task-16 suite: UI polish (settings nav, sync banner, page-owned actions,
empty states, a11y). Source-level template assertions (no HTTP needed).

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import html as html_module
import os
import re
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(BASE_DIR, "backend", "app", "templates")
BASE = os.path.join(TEMPLATES, "base.html")
SETTINGS = os.path.join(TEMPLATES, "settings.html")


def _read(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as handle:
        return handle.read()


def _base():
    with open(BASE, encoding="utf-8") as handle:
        return handle.read()


def _settings():
    with open(SETTINGS, encoding="utf-8") as handle:
        return handle.read()


# ===========================================================================
# A. Settings page section navigation + sticky save
# ===========================================================================
def test_settings_section_nav():
    print("--> Testing settings section nav + sticky save bar...")
    source = _settings()
    expected = [
        ("Calibre", "section-calibre"),
        ("Downloader", "section-downloader"),
        ("Staging", "section-staging"),
        ("Schedules", "section-schedules"),
        ("Webhooks", "section-webhooks"),
        ("Groups", "section-groups"),
        ("Maintenance", "section-maintenance"),
        ("Advanced", "section-advanced"),
    ]
    assert 'id="settings-section-nav"' in source, "sticky section nav missing"
    assert "sticky top-20" in source, "section nav is not sticky"
    for label, anchor in expected:
        assert 'href="#%s"' % anchor in source, (label, anchor)
        assert 'id="%s"' % anchor in source, (label, anchor)
        assert "data-settings-nav=\"%s\"" % anchor in source, (label, anchor)
        assert "> %s" % label in source or ">%s" % label in source, (label, anchor)
    # Exactly the 8 anchors, no strays.
    anchors = re.findall(r'data-settings-nav="([^"]+)"', source)
    assert anchors == [anchor for _label, anchor in expected], anchors

    # Scroll-spy + smooth anchor navigation are plain JS (no dependency).
    assert "IntersectionObserver" in source
    assert "setActive(" in source
    assert "aria-current" in source

    # The save button is still inside the form; the sticky bar references the form.
    form = source[source.index('<form id="settings-form"'):source.index("</form>")]
    assert 'type="submit"' in form, "save button left the form"
    assert 'id="settings-save-bar"' in source
    save_bar = source[source.index('id="settings-save-bar"'):]
    save_bar = save_bar[:save_bar.index("</div>", save_bar.index("</button>"))]
    assert 'form="settings-form"' in save_bar, "sticky save button is not associated with the form"
    assert "form.addEventListener('input'" in source and "form.addEventListener('change'" in source
    # Navigation only: form field ids/names unchanged (spot-check the core fields).
    for field_id in ("setting-calibre-path", "setting-staging-dir", "setting-qbit-url",
                     "setting-discord-url", "settings-new-group-name"):
        assert 'id="%s"' % field_id in source, field_id
    print("    [PASSED] 8 anchors↔card ids, scroll-spy JS, sticky save bar tied to the form.")


# ===========================================================================
# B. Background Calibre sync banner
# ===========================================================================
def test_calibre_sync_banner():
    print("--> Testing background Calibre sync banner...")
    source = _base()
    assert 'id="calibre-sync-banner"' in source, "banner markup missing"
    assert "Indexing your Calibre library in the background" in source
    assert "lists refresh automatically" in source
    # Polls the status endpoint and polls only while running.
    assert "/api/calibre/status" in source
    assert "initial_sync_done" in source
    assert "in_progress" in source
    assert "setInterval(checkCalibreSyncStatus, 3000)" in source
    # Dismissible + refresh on completion.
    assert "function dismissCalibreSyncBanner()" in source
    assert "location.reload()" in source
    # Never rendered on auth routes (server-side guard + JS guard).
    assert "request.url.path.startswith('/auth/')" in source
    assert "window.location.pathname.startsWith('/auth/')" in source
    print("    [PASSED] slim banner, status polling only while running, refresh + auth guard.")


# ===========================================================================
# C. Page-owned actions: no action buttons in the global header
# ===========================================================================
def test_page_owned_actions():
    print("--> Testing page-owned actions (no header action buttons)...")
    base = _base()
    header = base[base.index("<!-- Right side:"):base.index("</header>")]
    for call in ('onclick="triggerCalibreSync()"', 'onclick="triggerStagingTag()"',
                 'onclick="triggerFeedCheck()"'):
        assert call not in header, call
    assert "nav-menu-actions" not in base, "overflow menu markup still present"
    assert "more-horizontal" not in base

    series = _read("series.html")
    assert series.count('onclick="triggerCalibreSync()"') == 1, "Sync Calibre must be on the Series page"
    assert 'id="icon-calibre-sync"' in series
    downloads = _read("downloads.html")
    assert downloads.count('onclick="triggerStagingTag()"') == 1, "Tag CBZ must be on the Downloads page"
    assert 'id="icon-staging-tag"' in downloads
    feeds = _read("feeds.html")
    assert feeds.count('onclick="triggerFeedCheck()"') == 1, "Poll Feeds must be on the Feeds page"
    assert 'id="btn-check-feeds"' in feeds
    # No dangling hide-on-owning-page guard for the removed header items.
    assert "active_page != 'downloads'" not in base
    assert "triggerFeedCheck()" not in _read("discovery.html")
    print("    [PASSED] Sync→Series, Tag→Downloads, Poll→Feeds; no header actions/overflow.")


# ===========================================================================
# D. Empty states + visible filter resets
# ===========================================================================
def test_empty_states():
    print("--> Testing friendly empty states and clear-filter actions...")
    releases = _read("releases.html")
    assert "No manga series found matching current filters." in releases
    assert "Clear filters" in releases and "clearReleaseFilters" in releases

    prioritized = _read("prioritized.html")
    assert "No prioritized manga releases found matching current filters." in prioritized
    assert "Clear filters" in prioritized and "clearPrioritizedFilters" in prioritized

    series = _read("series.html")
    assert 'id="series-filter-empty"' in series
    assert "No manga series match your search" in series
    assert "clearSeriesFilters" in series
    assert "No manga series found in Calibre library." in series  # pre-existing no-data state kept

    feeds = _read("feeds.html")
    assert "No feeds configured yet" in feeds
    assert 'onclick="openAddFeedModal()"' in feeds

    logs = _read("logs.html")
    assert "No activity logs recorded yet" in logs
    assert 'id="logs-filter-empty"' in logs
    assert "clearLogFilters" in logs
    print("    [PASSED] all five pages have icon empty states; filtered lists offer Clear.")


# ===========================================================================
# E. Accessibility & consistency
# ===========================================================================
def test_accessibility():
    print("--> Testing a11y pass (icon-only buttons, focus rings, tap targets)...")
    base = _base()
    # Every icon-only button must have BOTH aria-label and title.
    button_pattern = re.compile(r"<button\b[^>]*>.*?</button>", re.S)
    offenders = []
    for name in sorted(os.listdir(TEMPLATES)):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(TEMPLATES, name), encoding="utf-8") as handle:
            source = handle.read()
        for match in button_pattern.finditer(source):
            block = match.group(0)
            tag_end = block.index(">") + 1
            tag = block[:tag_end]
            inner = block[tag_end:-len("</button>")]
            text = html_module.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
            if text:
                continue
            if 'aria-label="' not in tag or 'title="' not in tag:
                line = source[:match.start()].count("\n") + 1
                offenders.append(f"{name}:{line}")
    assert not offenders, "icon-only buttons missing aria-label/title: %s" % offenders

    # Consistent focus-visible styling for all interactive controls.
    assert ":where(a, button, input, select, textarea, summary):focus-visible" in base
    # Mobile tap targets >= 44px.
    assert 'min-h-[44px]' in base
    # Dropdown/mobile triggers are real buttons with aria-expanded.
    assert base.count('aria-expanded="false"') >= 4
    assert 'aria-label="Open navigation menu"' in base
    assert "closeMobileMenu()" in base
    print("    [PASSED] labelled icon buttons, focus rings, 44px mobile targets, aria triggers.")


def main():
    print("=" * 78)
    print("Running UI polish suite (task-16)")
    print("=" * 78)
    tests = [
        test_settings_section_nav,
        test_calibre_sync_banner,
        test_page_owned_actions,
        test_empty_states,
        test_accessibility,
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
        print(f"UI POLISH SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL UI POLISH TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
