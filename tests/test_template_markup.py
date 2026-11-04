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


def run():
    test_no_duplicate_attributes()
    test_download_buttons_are_styled()
    print("ALL TEMPLATE MARKUP TESTS PASSED")


if __name__ == "__main__":
    run()
