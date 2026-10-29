import re
from pathlib import Path

import pytest

from bsa.image_checks import Finding
from bsa.review.pages import open_page, render_final, render_story, render_storyboard
from bsa.short import Asset, Fact, Line, load_short
from bsa.timeline import build_timeline

SAMPLE = Path(__file__).parent / "e2e" / "pendulum" / "source" / "short.json"


@pytest.fixture
def short():
    return load_short(SAMPLE)


@pytest.fixture
def review(tmp_path):
    (tmp_path / "review").mkdir()
    (tmp_path / "stills").mkdir()
    return tmp_path


def test_story_page_has_facts_and_sources(short, review):
    out = render_story(short, review / "review" / "story.html")
    html = out.read_text(encoding="utf-8")
    for fact in short.facts:
        assert fact.claim.replace("'", "&#x27;") in html
        assert f'<a href="{fact.source}"' in html
    for ln in short.lines:
        assert ln.text in html
    assert "<title>Story — Why a pendulum keeps perfect time</title>" in html


def test_story_page_style_options_and_assets(short, review):
    short.assets = [Asset("a1", "Brass <bob>", "model", "https://polyhaven.com/a/bob", "CC0", bytes=2_500_000)]
    opts = [{"name": "Dusk", "description": "warm", "thumb": "stills/style-1.jpg"}]
    html = render_story(short, review / "review" / "story.html", opts).read_text(encoding="utf-8")
    assert 'src="../stills/style-1.jpg"' in html
    assert "Dusk" in html and "CC0" in html and "2.5 MB" in html
    assert "Brass &lt;bob&gt;" in html


def test_story_page_lists_pending_downloads(short, review):
    short.assets = [Asset("done1", "Old floor", "textures", "Poly Haven", "CC0", bytes=1_000_000)]
    pending = [Asset("p1", "Brushed <steel>", "textures", "Poly Haven", "CC0", bytes=3_400_000)]
    html = render_story(short, review / "review" / "story.html", None, pending).read_text(encoding="utf-8")
    assert "Brushed &lt;steel&gt;" in html and "3.4 MB" in html
    assert "after your yes" in html
    assert "Old floor" in html  # downloaded assets still credited
    assert "Nothing to download" not in html


def test_story_page_shows_spoken_and_silent_seconds_per_shot(short, review):
    from bsa.timeline import pacing

    short.shots[1].min_s = 30
    rows = pacing(short, build_timeline(short, None))
    html = render_story(short, review / "review" / "story.html", pacing=rows).read_text(encoding="utf-8")
    assert len(re.findall(r'<div class="pace( slow)?">', html)) == len(short.shots)
    assert html.count('<div class="pace slow">') == 1  # only the long hold is flagged
    s02 = next(r for r in rows if r["shot"] == "s02")
    assert f"{s02['spoken_s']:.1f} s spoken" in html and f"{s02['silent_s']:.1f} s silent" in html
    assert f"try about {s02['suggest_s']:.1f} s" in html
    plain = render_story(short, review / "review" / "story2.html").read_text(encoding="utf-8")
    assert '<div class="pace' not in plain


def test_storyboard_has_every_shot_in_order(short, review):
    tl = build_timeline(short, None)
    html = render_storyboard(short, tl, review / "stills", review / "review" / "storyboard.html").read_text(
        encoding="utf-8"
    )
    srcs = re.findall(r'<img[^>]*class="still"[^>]*src="([^"]+)"', html)
    assert srcs == [f"../stills/{s.id}.jpg" for s in short.shots]
    pos = 0
    for shot in short.shots:
        pos = html.index(f"../stills/{shot.id}.jpg", pos)
        for ln in (ln for ln in short.lines if ln.shot == shot.id):
            pos = html.index(ln.text.replace("'", "&#x27;"), pos)
    assert "<title>Storyboard — Why a pendulum keeps perfect time</title>" in html
    assert 'href="#shot-s02"' in html


def test_html_escaped(short, review):
    evil = "<script>"
    short.lines.append(Line("l99", evil, "s03"))
    short.title = evil
    short.facts[0].claim = evil
    short.shots[0].title = evil
    short.shots[1].labels = [evil]
    short.post = {"title": evil, "description": evil, "hashtags": [evil]}
    short.assets = [Asset("a1", evil, "model", evil, evil, bytes=1)]
    tl = build_timeline(short, None)
    findings = [Finding(evil, "error", evil, 1, evil)]
    mp4 = review / "out" / "a.mp4"
    for page in (
        render_story(short, review / "review" / "story.html"),
        render_storyboard(short, tl, review / "stills", review / "review" / "sb.html"),
        render_final(short, findings, mp4, review / "review" / "final.html"),
    ):
        html = page.read_text(encoding="utf-8")
        assert "&lt;script&gt;" in html
        assert "<script>" not in re.sub(r"<script>.*?</script>", "", html, flags=re.DOTALL)  # ignore our own inline JS


def test_unsafe_fact_sources_are_not_links(short, review):
    short.facts[0].source = "javascript:alert(1)"
    short.facts.append(Fact("f2", "Claim two", "Wikipedia"))
    short.facts.append(Fact("f3", "Claim three", "data:text/html,<b>x</b>"))
    html = render_story(short, review / "review" / "story.html").read_text(encoding="utf-8")
    assert "href=\"javascript" not in html and "href=\"data:" not in html
    assert "javascript:alert(1)" in html and "Wikipedia" in html
    assert "<a href=" not in html


def test_untimed_shot_is_still_shown(short, review):
    tl = build_timeline(short, None)
    tl.shots = [s for s in tl.shots if s.id != "s02"]
    html = render_storyboard(short, tl, review / "stills", review / "review" / "sb.html").read_text(encoding="utf-8")
    assert "../stills/s02.jpg" in html and "not timed yet" in html
    assert html.index("../stills/s01.jpg") < html.index("../stills/s02.jpg") < html.index("../stills/s03.jpg")


def test_no_side_tab_borders():
    from pathlib import Path as P

    from bsa.review import pages

    for f in (P(pages.__file__).parent / "templates").iterdir():
        assert "border-left:" not in f.read_text(encoding="utf-8")


def test_offline(short, review):
    tl = build_timeline(short, None)
    mp4 = review / "out" / "x.mp4"
    pages = [
        render_story(short, review / "review" / "story.html"),
        render_storyboard(short, tl, review / "stills", review / "review" / "storyboard.html"),
        render_final(short, [], mp4, review / "review" / "final.html"),
    ]
    sources = {f.source for f in short.facts}
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for url in re.findall(r"https?://[^\s\"'<>]+", text):
            assert url in sources
        assert "goinsidemachines" not in text.lower()
        assert 'src="//' not in text and "@import" not in text


def test_final_page_lists_errors_first(short, review):
    findings = [
        Finding("fireflies", "warning", "Sphere", 12, "warn-message"),
        Finding("black_frame", "error", None, 40, "err-message"),
    ]
    mp4 = review / "out" / "pendulum.mp4"
    mp4.parent.mkdir()
    mp4.write_bytes(b"\0" * 1_500_000)
    html = render_final(short, findings, mp4, review / "review" / "final.html").read_text(encoding="utf-8")
    assert html.index("err-message") < html.index("warn-message")
    assert 'src="../out/pendulum.mp4"' in html
    assert "1.5 MB" in html
    assert short.post["title"] in html and "#physics" in html
    assert html.count('data-copy=') >= 3
    assert "<title>Final video — Why a pendulum keeps perfect time</title>" in html


def test_final_page_no_findings(short, review):
    html = render_final(short, [], review / "out" / "a.mp4", review / "review" / "final.html").read_text(
        encoding="utf-8"
    )
    assert "All checks passed" in html


def test_open_page(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr("webbrowser.open", lambda u: seen.append(u))
    p = tmp_path / "x.html"
    p.write_text("x")
    open_page(p)
    assert seen == [p.as_uri()]


def test_final_page_without_checks_says_not_run(short, review):
    out = review / "review" / "final.html"
    html = render_final(short, [], review / "out" / "a.mp4", out, checks_run=False).read_text(encoding="utf-8")
    assert "All checks passed" not in html and "checks not run" in html.lower()
