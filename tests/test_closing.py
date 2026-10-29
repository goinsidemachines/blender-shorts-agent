import copy
import wave

import numpy as np
import pytest

from bsa import closing, settings
from bsa.audio.mix import mix
from bsa.layout import H, W
from bsa.short import Line, Short, Shot, validate
from bsa.timeline import build_timeline

CARD = {"image": "C:/me/card.png", "seconds": 2.5, "voice_line": "Play with it at example dot com."}


def _short(**kw):
    return Short(slug="t", title="T", length_s=4, shots=[Shot("a", "A", "p", "s", min_s=1.0)],
                 lines=[Line("l1", "Hello there.", "a")], **kw)  # fmt: skip


def test_nothing_set_means_no_card_and_the_end_card_as_before():
    s = copy.deepcopy(settings.DEFAULTS)
    assert closing.resolve(s) is None
    s["handle"] = "me"  # a handle alone keeps the overlay end card
    assert closing.resolve(s) is None and settings.has_end_card(s)


def test_image_and_voice_line_from_settings():
    s = {"handle": "me", "website": "example.com", "closing": CARD}
    card = closing.resolve(s)
    assert card == {"image": "C:/me/card.png", "website": "example.com", "handle": "me", "seconds": 2.5,
                    "voice_line": "Play with it at example dot com."}  # fmt: skip
    assert not settings.has_end_card(s)  # the card says it once: no overlay end card
    assert settings.end_card(s) == {"handle": None, "text": None, "second": None}


def test_website_alone_makes_a_brand_card():
    card = closing.resolve({"website": "example.com", "handle": "@me"})
    assert card["image"] is None and closing.card_rows(card) == [
        ("More at", "lead"), ("example.com", "pill"), ("@me", "handle")]  # fmt: skip


def test_seconds_are_clamped():
    assert closing.clamp_seconds(0.2) == closing.MIN_S and closing.clamp_seconds(60) == closing.MAX_S
    assert closing.clamp_seconds("x") == closing.SECONDS
    assert closing.resolve({"website": "a.com", "closing": {"seconds": None}})["seconds"] == closing.SECONDS


def test_a_short_switches_it_off_or_overrides_it():
    s = {"website": "example.com", "closing": CARD}
    assert closing.resolve(s, _short(closing=False)) is None
    own = closing.resolve(s, _short(closing={"image": None, "voice_line": "Bye.", "seconds": 3}))
    assert own["image"] is None and own["voice_line"] == "Bye." and own["seconds"] == 3


def test_short_closing_is_validated():
    assert validate(_short(closing=False)) == [] and validate(_short(closing={"seconds": 2})) == []
    assert any("closing" in e for e in validate(_short(closing="yes")))
    assert any("unknown keys" in e for e in validate(_short(closing={"colour": "red"})))
    assert any("between" in e for e in validate(_short(closing={"seconds": 9})))
    bad = _short()
    bad.lines.append(Line(closing.LINE_ID, "x", "a"))
    assert any("kept for the closing line" in e for e in validate(bad))


def test_the_closing_line_is_narrated_with_the_story():
    short = _short()
    voiced = closing.with_voice_line(short, closing.resolve({"website": "a.com", "closing": CARD}))
    assert [ln.id for ln in voiced.lines] == ["l1", closing.LINE_ID] and len(short.lines) == 1
    assert closing.with_voice_line(short, closing.resolve({"website": "a.com"})) is short


def test_plan_holds_long_enough_for_the_line():
    cfg = closing.resolve({"website": "a.com", "closing": CARD})
    assert closing.plan(cfg, 101, 30, None) == {"f0": 101, "f1": 175, "n_frames": 75, "line_f0": None}
    long = closing.plan(cfg, 101, 30, 3.0)  # 0.2 + 3.0 + 0.5 s
    assert long["n_frames"] == 111 and long["line_f0"] == 107


def test_a_card_held_longer_for_its_line_says_so():
    """closing.seconds is a minimum: a longer voice line holds the card longer, and the result says so."""
    cfg = closing.resolve({"website": "a.com", "closing": CARD})
    end = closing.plan(cfg, 101, 30, 3.8)  # 0.2 + 3.8 + 0.5 s
    held = closing.hold(cfg, end, 30)
    assert held["seconds"] == 4.5 and held["set_seconds"] == 2.5
    assert held["note"] == ("closing card held 4.5 s so the voice line fits (its take is 3.8 s; closing.seconds is "
                            "2.5)")  # fmt: skip
    plain = closing.hold(cfg, closing.plan(cfg, 101, 30, 1.0), 30)
    assert plain == {"seconds": 2.5, "set_seconds": 2.5}


def test_the_review_page_says_how_long_the_card_holds(tmp_path):
    from bsa.review.pages import _closing_html

    card = {**CARD, "png": str(tmp_path / "closing.png"), "warnings": []}
    assert "at least 2.5 s" in _closing_html(card, tmp_path / "final.html")
    note = "closing card held 4.5 s so the voice line fits (its take is 3.8 s; closing.seconds is 2.5)"
    html = _closing_html({**card, "held_s": 4.5, "hold_note": note}, tmp_path / "final.html")
    assert "held 4.5 s" in html and note in html and "at least" not in html


def _take(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        t = np.arange(int(seconds * 48000)) / 48000
        w.writeframes((np.sin(2 * np.pi * 200 * t) * 8000).astype("<i2").tobytes())


def test_mix_adds_the_closing_line_after_the_story_and_no_bed(tmp_path):
    short = _short()
    _take(tmp_path / "l1.wav", 1.0)
    _take(tmp_path / f"{closing.LINE_ID}.wav", 1.0)
    tl = build_timeline(short, {"l1": 1.0})
    cfg = closing.resolve({"website": "a.com", "closing": CARD})
    end = closing.plan(cfg, tl.n_frames + 1, tl.fps, closing.take_seconds(tmp_path))
    out, _ = mix(tl, short, tmp_path, extra_bed=np.full(48000 * 20, 0.2),
                 closing=(end["n_frames"], closing.take(tmp_path), end["line_f0"]))  # fmt: skip
    assert len(out) == round((tl.n_frames + end["n_frames"]) / tl.fps * 48000)
    story = round(tl.n_frames / tl.fps * 48000)
    line = round((end["line_f0"] - 1) / tl.fps * 48000)
    assert np.abs(out[story:line]).max() == 0  # no bed over the card
    assert np.abs(out[line : line + 48000]).max() > 0.1  # the closing line
    assert np.abs(out[line + 48000 + 10 :]).max() == 0


def test_zone_warnings_find_text_where_youtube_covers_it():
    clean = np.full((H, W, 3), 230, np.uint8)
    clean[700:760, 300:700] = 20  # text in the middle
    assert closing.zone_warnings(clean) == []
    low = clean.copy()
    for x in range(100, 900, 12):  # text-like stripes in the bottom 18%
        low[1700:1760, x : x + 4] = 20
    assert any("bottom" in w for w in closing.zone_warnings(low))
    right = clean.copy()
    for y in range(400, 1200, 12):
        right[y : y + 4, 980:1060] = 20
    assert any("right" in w for w in closing.zone_warnings(right))


def test_aspect_warning():
    assert closing.aspect_warning(1080, 1920) is None and closing.aspect_warning(2160, 3840) is None
    assert "not 9:16" in closing.aspect_warning(1920, 1080)


def test_hard_rules_read_the_closing_card():
    from bsa import rules

    s = {"website": "example.com", "closing": CARD,
         "rules": [{"id": "r1", "text": "No text on screen except captions.",
                    "kind": "no_text_on_screen_except_captions"}]}  # fmt: skip
    short = _short(rules_check={"r1": "captions only"})
    _, warnings = rules.problems(short, s)
    assert any("closing card shows text" in w for w in warnings)
    assert not any("closing card" in w for w in rules.problems(_short(closing=False, rules_check={"r1": "x"}), s)[1])


def test_setup_asks_about_the_closing_card_once():
    s = copy.deepcopy(settings.DEFAULTS)
    assert "closing" in settings.missing(s)
    assert "closing" not in settings.missing({**s, "closing_asked": True})
    assert "closing" not in settings.missing({**s, "website": "a.com"})
    assert "ends on a.com" in settings.summary_line({**s, "website": "a.com"})


@pytest.mark.blender
def test_closing_card_in_blender(blender_run):
    from pathlib import Path

    result = blender_run(Path(__file__).resolve().parent / "blender" / "test_closing_card.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_brand_card_from_the_website_alone", "test_user_image_fills_the_frame",
                 "test_edit_ends_on_the_closing_card"):  # fmt: skip
        assert f"PASS {name}" in result.stdout


def test_the_edit_reports_and_records_a_longer_hold(tmp_path, monkeypatch):
    """BLENDER edit's closing plan carries the hold note and writes it to stills/closing.json for the final page."""
    import json
    from types import SimpleNamespace

    import bsa_blender
    from bsa.narrate import run

    cfg = closing.resolve({"website": "a.com", "closing": CARD})
    png = tmp_path / "stills" / closing.CARD_FILE
    png.parent.mkdir()
    (png.parent / closing.INFO_FILE).write_text(json.dumps({**cfg, "warnings": []}), encoding="utf-8")
    monkeypatch.setattr(bsa_blender, "_closing_card", lambda sdir, short: (cfg, png, []))
    monkeypatch.setattr(run, "takes_durations", lambda sdir, short: {})
    (tmp_path / "audio" / "vo").mkdir(parents=True)
    _take(tmp_path / "audio" / "vo" / f"{closing.LINE_ID}.wav", 3.8)
    end, warnings = bsa_blender._closing_plan(tmp_path, _short(), SimpleNamespace(n_frames=100, fps=30),
                                              tmp_path / "audio" / "mix.wav")  # fmt: skip
    assert not warnings and end["hold"]["seconds"] == 4.5 and "so the voice line fits" in end["hold"]["note"]
    info = json.loads((png.parent / closing.INFO_FILE).read_text(encoding="utf-8"))
    assert info["held_s"] == 4.5 and info["hold_note"] == end["hold"]["note"]
