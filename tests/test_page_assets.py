"""The review page is one self-contained HTML file: no outside resources, no markup built from strings."""

import re

from bsa.review import page
from bsa.review.page import TEMPLATES


def test_page_is_one_file_with_no_external_resources():
    full = page.render("server")
    html = re.sub(r'<a href="https://[^"]*" target="_blank" rel="noopener noreferrer">', "<a>", full)  # links only
    assert full.count('target="_blank"') == 4 and html.count("<a>") == 4  # the footer's links, nothing loaded
    assert "<script src" not in html and "<link" not in html and "http://" not in html.replace("http://www.w3.org", "")
    assert "https://" not in html and "@import" not in html
    assert 'window.BSA_MODE = "server"' in html and "window.BSA_SNAPSHOT =" not in html


def test_page_inlines_every_part():
    html = page.render("server")
    for part in ("page.css", "qr.js", "stages.js", "page.js"):
        body = (TEMPLATES / "page" / part).read_text("utf-8").strip()
        assert body[:200] in html, part
    assert "@@" not in html  # every placeholder was filled


def test_scripts_never_use_innerHTML():
    for f in ("page.js", "stages.js", "qr.js"):
        text = (TEMPLATES / "page" / f).read_text("utf-8")
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert bad not in text, (f, bad)


def test_static_mode_embeds_escaped_snapshot():
    html = page.render("static", {"x": "</script><b>"})
    assert html.count("</script>") == html.count("<script>") and "<\\/script>" in html
    assert 'window.BSA_MODE = "static"' in html


def test_title_is_escaped():
    html = page.render("static", {"short": {"title": "<b>Levers & you</b>"}})
    assert "<title>&lt;b&gt;Levers &amp; you&lt;/b&gt;</title>" in html


def test_unknown_mode_is_refused():
    try:
        page.render("other")
    except ValueError:
        return
    raise AssertionError("an unknown mode must be refused")
