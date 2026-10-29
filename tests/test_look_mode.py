"""A short's own look (`short.json` "look": "fresh") turns the brand off for that short, without touching settings."""

import json
import shutil
from pathlib import Path

import numpy as np

from bsa import colour
from bsa.review.pages import render_story
from bsa.settings import brand_for, look_mode
from bsa.short import Short, load_short, validate

SAMPLE = Path(__file__).parent / "e2e" / "pendulum" / "source" / "short.json"
BRAND = {"brand": {"mode": "brand", "background": "#101010", "caption": {"color": "#FFD000"}}}


def _short(**kw):
    return Short(slug="t", title="T", length_s=10, **kw)


def test_brand_for_a_short_follows_its_look():
    plain, fresh = _short(), _short(look="fresh")
    assert brand_for(BRAND, plain) == BRAND["brand"] and look_mode(BRAND, plain) == "brand"
    assert brand_for(BRAND, fresh) == {} and look_mode(BRAND, fresh) == "fresh"
    assert brand_for(BRAND) == BRAND["brand"]
    assert look_mode({"brand": {"mode": "fresh"}}, plain) == "fresh"
    assert look_mode({"brand": {"mode": None}}, plain) is None
    assert look_mode({"brand": {"mode": "fresh"}}, _short(look="brand")) == "brand"


def test_look_must_be_fresh_or_brand():
    s = load_short(SAMPLE)
    s.look = "fresh"
    assert not [e for e in validate(s) if "look" in e]
    s.look = "neon"
    assert [e for e in validate(s) if "look must be" in e]


def test_a_fresh_look_short_ignores_the_brand_background():
    s = _short(look="fresh")
    assert colour.background_hex(s, BRAND) is None
    assert colour.background_source(s, BRAND) is None
    s.style = {"palette": {"background": "#F3F2EE"}}
    assert colour.background_source(s, BRAND) == ("#F3F2EE", "the short's background")
    assert colour.background_source(_short(), BRAND) == ("#101010", "the brand background")


def test_background_warning_names_whose_background():
    grey = np.full((60, 40, 3), (197, 197, 195), np.uint8)
    msg = colour.background_warning(grey, "#F3F2EE", "s01", whose="the short's background")
    assert "the short's background #F3F2EE" in msg and "brand" not in msg
    assert "the brand background #F3F2EE" in colour.background_warning(grey, "#F3F2EE", "s01")


def test_story_page_says_which_look_the_short_follows(tmp_path):
    short = load_short(SAMPLE)
    out = tmp_path / "review" / "story.html"
    out.parent.mkdir()
    assert "the brand is off for this short" in render_story(short, out, look="fresh").read_text(encoding="utf-8")
    html = render_story(short, out, look="brand").read_text(encoding="utf-8")
    assert "the brand look from your settings" in html
    html = render_story(short, out).read_text(encoding="utf-8")
    assert "brand is off" not in html and "brand look from" not in html


def test_review_story_with_a_fresh_short_under_brand_settings(tmp_path):
    from tests.test_cli import ok, run

    home = tmp_path / "home"
    home.mkdir()
    ok(run(home, "settings", "set", "workspace", tmp_path / "ws"))
    ok(run(home, "settings", "set", "brand.mode", "brand"))
    sdir = Path(ok(run(home, "new-short", "Pendulum"))["short"])
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    shutil.copyfile(SAMPLE, sdir / "source" / "short.json")
    (sdir / "source" / "styles.json").write_text(json.dumps([{"name": "Dusk", "description": "warm"}]), "utf-8")
    html = Path(ok(run(home, "review", "story", "pendulum"))["page"]).read_text(encoding="utf-8")
    assert "Dusk" not in html and "the brand look from your settings" in html  # the brand: no look options
    data["look"] = "fresh"
    (sdir / "source" / "short.json").write_text(json.dumps(data), encoding="utf-8")
    html = Path(ok(run(home, "review", "story", "pendulum"))["page"]).read_text(encoding="utf-8")
    assert "Dusk" in html and "the brand is off for this short" in html


def test_brand_mode_fresh_turns_the_brand_off_too():
    s = {"brand": {"mode": "fresh", "exposure": 0.5, "look": "Punchy", "background": "#101010"}}
    assert brand_for(s, _short()) == {}
    assert brand_for(s, _short(look="brand")) == s["brand"]
    assert colour.background_source(_short(), s) is None  # never "the brand background" in a fresh short


def test_stills_skip_the_background_check_on_a_sky_or_landscape(tmp_path, monkeypatch):
    """The paper_world advice is for a flat backdrop: a still whose scene has a sky or an HDRI is not checked."""
    import bsa_blender

    monkeypatch.setenv("BSA_HOME", str(tmp_path))
    s = _short(look="fresh", style={"palette": {"background": "#AFC6D3"}})
    missing = tmp_path / "never-read.png"  # reading it would fail: it is skipped before that
    assert bsa_blender._background_warnings(s, [("s01", missing, False)]) == []
