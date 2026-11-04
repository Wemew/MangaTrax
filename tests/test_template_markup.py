#!/usr/bin/env python3
"""Template markup guards: no duplicated attributes inside one HTML tag.

Why: a duplicated ``class`` attribute is silently ignored after the first one by
browsers, which is how the Releases download button lost all its styling (the
download-state work added a second ``class`` while injecting ``download-release-btn``).
Jinja2 templates cannot be rendered by the Calibre test runner, so these are source
assertions over every template/partial.
"""

import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(BASE_DIR, "backend", "app", "templates")

TAG_RE = re.compile(r"<(button|a|div|span|input|select|option|label|form|textarea)\b[^>]*>", re.DOTALL)
CLASS_ATTR_RE = re.compile(r"\bclass\s*=\s*[\"']", re.IGNORECASE)


def _template_paths():
    paths = []
    for root, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            if name.endswith(".html"):
                paths.append(os.path.join(root, name))
    return paths


def find_duplicate_attribute_tags(source):
    """Return (tag_text, attribute) for tags carrying the same attribute twice."""
    offenders = []
    for match in TAG_RE.finditer(source):
        tag = match.group(0)
        for attr in ("class", "id", "data-release-id", "data-task-kind", "onclick", "title"):
            hits = len(re.findall(r"\b" + re.escape(attr) + r"\s*=", tag, re.IGNORECASE))
            if hits > 1:
                offenders.append((tag.strip().replace("\n", " ")[:180], attr))
    return offenders


def test_no_duplicate_attributes():
    problems = []
    for path in _template_paths():
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        for tag, attr in find_duplicate_attribute_tags(source):
            problems.append(f"{os.path.basename(path)}: duplicate {attr}= in <{tag}>")
    assert not problems, "duplicate attributes (first value wins, breaking style/behaviour):\n" + "\n".join(problems)
    print("    [PASSED] every template tag carries each attribute at most once.")


def test_download_buttons_are_styled():
    card = os.path.join(TEMPLATES, "partials", "series_card_chunk.html")
    flat = os.path.join(TEMPLATES, "partials", "flat_release_row_chunk.html")
    for path in (card, flat):
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        buttons = [m.group(0) for m in TAG_RE.finditer(source) if "download-release-btn" in m.group(0)]
        assert buttons, f"{os.path.basename(path)}: download button not found"
        for button in buttons:
            classes = re.search(r'class="([^"]*)"', button)
            assert classes, f"{os.path.basename(path)}: download button missing class attribute"
            value = classes.group(1)
            assert "download-release-btn" in value, value
            assert "bg-brand-600" in value, f"download button lost its background style: {value}"
            assert "rounded" in value and ("px-" in value or "p-" in value), f"download button lost its button chrome: {value}"
        # The label lives between the tags, not inside the opening tag we matched.
        if os.path.basename(path) == "series_card_chunk.html":
            assert "<span>Download</span>" in source, "download label missing inside the card button"
    print("    [PASSED] card + flat download buttons carry their full styling classes.")


def test_no_html_comments_inside_scripts():
    """HTML markup accidentally placed inside <script> is a JS syntax error.

    That is exactly how the whole Downloads page JS (Clear failed / Remove / Retry /
    polling / manual match) silently died: the manual-series-match modal was inserted
    before the script's closing tag, so the browser failed to parse the script and every
    handler was undefined.  An HTML ``<!--`` comment at the start of a line inside a
    script block is a reliable, string-literal-safe signal of that mistake.
    """
    problems = []
    for path in _template_paths():
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        for block in re.findall(r"<script[^>]*>(.*?)</script>", source, re.DOTALL | re.IGNORECASE):
            for lineno, line in enumerate(block.splitlines(), 1):
                if line.strip().startswith("<!--"):
                    problems.append(f"{os.path.basename(path)}: HTML comment inside <script> (block line {lineno})")
    assert not problems, "HTML markup inside <script> breaks the whole page JS:\n" + "\n".join(problems)
    print("    [PASSED] no HTML markup/comments are nested inside <script> blocks.")


def test_manual_match_modal_outside_script():
    path = os.path.join(TEMPLATES, "downloads.html")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    script_end = source.find("</script>")
    modal = source.find('id="manual-match-modal"')
    assert script_end >= 0, "downloads.html has no closing </script>"
    assert modal > script_end, (
        "manual-match modal must live outside the <script> block (it currently breaks page JS)"
    )
    assert source.count("<script") == 1, "downloads.html should keep a single script block"
    print("    [PASSED] downloads.html: modal markup sits after the script block.")


def run():
    test_no_duplicate_attributes()
    test_download_buttons_are_styled()
    test_no_html_comments_inside_scripts()
    test_manual_match_modal_outside_script()
    print("ALL TEMPLATE MARKUP TESTS PASSED")


if __name__ == "__main__":
    run()
