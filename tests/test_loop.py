"""Loop mode: resolution and validation, no end/closing card, the loop math and the looped sound bed."""

import numpy as np
import pytest

from bsa import closing, presets, settings
from bsa.audio import dsp
from bsa.audio.mix import LOOP_XFADE_S, mix
from bsa.audio.sfx import SR
from bsa.blender import loop
from bsa.short import Line, Sfx, Short, Shot, load_short, save_short, validate
from bsa.timeline import Span, Timeline


def _short(**kw):
    return Short(slug="t", title="T", length_s=10, lines=[Line("l01", "One.", "s01")],
                 shots=[Shot("s01", "Hook", "pic", "Scene", min_s=1.0)], **kw)  # fmt: skip


# ---- resolution and validation


def test_loop_is_off_by_default_and_resolves_last_wins():
    assert settings.DEFAULTS["loop"] is False
    assert presets.loop_resolved({}, _short()) == (False, "default")
    assert presets.loop_resolved({"loop": True}, _short()) == (True, "settings")
    assert presets.loop_resolved({"style_preset": "fast-loop"}, _short()) == (True, "preset:fast-loop")
    assert presets.loop_resolved({"loop": True, "style_preset": "fast-loop"}, _short(loop=False)) == (False, "short")
    own = {"presets": {"calm": {"extend": "fast-loop", "loop": False}}, "style_preset": "calm"}
    assert presets.loop_resolved(own, _short()) == (False, "preset:calm")
    assert presets.loop_on({}, _short(preset="fast-loop"))
    assert presets.loop_on({}, {"loop": True})  # a short.json dict works too


def test_loop_is_validated():
    assert "loop" in presets.GROUPS and presets.SHIPPED["fast-loop"]["loop"] is True
    assert presets.preset_problems("p", {"loop": "yes"})
    assert presets.preset_problems("p", {"loop": True}) == []
    assert presets.problems({"loop": 1}, _short())
    assert any("loop" in p for p in presets.problems({}, _short(loop="no")))
    assert any("loop must be" in e for e in validate(_short(loop=1)))
    assert not any("loop" in e for e in validate(_short(loop=True)))
    with pytest.raises(ValueError):
        settings.check_value("loop", "yes")
    assert settings.check_value("loop", True) is True


def test_loop_round_trips_in_short_json(tmp_path):
    p = tmp_path / "short.json"
    save_short(_short(), p)
    assert "loop" not in p.read_text(encoding="utf-8")
    save_short(_short(loop=True), p)
    assert load_short(p).loop is True


def test_preset_save_keeps_loop():
    assert presets.from_short({}, _short(loop=True))["loop"] is True
    assert "loop" not in presets.from_short({}, _short())


# ---- no end card, no closing card


def test_loop_removes_end_card_closing_card_and_the_hold():
    with_card = {"handle": "me", "website": "example.com", "closing": {"image": "c.png"}}
    with_handle = {"handle": "me"}
    assert closing.active(with_card) and settings.has_end_card(with_handle)
    for src in (with_card, with_handle):
        looping = _short(loop=True)
        assert not closing.active(src, looping) and closing.resolve(src, looping) is None
        assert not settings.has_end_card(src, looping)
        assert settings.end_card(src, looping) == {"handle": None, "text": None, "second": None}
    assert not settings.has_end_card(with_handle, _short(loop=True, end_card="Bye"))
    assert not settings.has_end_card({**with_handle, "style_preset": "fast-loop"}, _short())
    assert settings.has_end_card({**with_handle, "style_preset": "fast-loop"}, _short(loop=False))


# ---- the helpers' math


def test_loop_phase():
    assert loop.loop_phase(60, 60) == 0 and loop.loop_phase(1, 60) == -59


def test_whole_turns():
    assert loop.whole_turns(0.021, 60) * 60 == pytest.approx(1.0)
    assert loop.whole_turns(-0.04, 60) * 60 == pytest.approx(-2.0)
    assert loop.whole_turns(0.5, 10) == 0.5
    with pytest.raises(ValueError):
        loop.whole_turns(0.1, 0)


def test_hermite_endpoints_and_slopes():
    v0, d0, v1, d1 = 2.0, 3.0, -1.0, 0.5
    assert loop.hermite_join(v0, d0, v1, d1, 0.0) == pytest.approx(v0)
    assert loop.hermite_join(v0, d0, v1, d1, 1.0) == pytest.approx(v1)
    h = 1e-6
    assert (loop.hermite_join(v0, d0, v1, d1, h) - v0) / h == pytest.approx(d0, abs=1e-4)
    assert (v1 - loop.hermite_join(v0, d0, v1, d1, 1 - h)) / h == pytest.approx(d1, abs=1e-4)
    assert loop.hermite_join(0.0, 0.0, 1.0, 0.0, 0.5) == pytest.approx(0.5)
    assert loop.hermite_join(np.zeros(2), 0, np.ones(2), 0, 0.5) == pytest.approx([0.5, 0.5])


def _rz(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])


def _samples(fn, n, first=1):
    return {f: fn(f) for f in (first, first + 1, n - 1, n)}


def test_compare_a_whole_turn_loop_passes_and_a_partial_one_fails():
    n, tol = 60, loop.tolerances(4.0)

    def spin(turn_deg):
        return lambda f: {("Cube", "rotation"): ("rot", _rz((f - 1) * turn_deg / n))}

    assert loop.compare(_samples(spin(360), n), n, tol) == []  # a whole turn: frame n+1 is frame 1
    assert loop.compare(_samples(spin(720), n), n, tol) == []
    bad = loop.compare(_samples(spin(350), n), n, tol)
    assert [(r["obj"], r["channel"], r["what"]) for r in bad] == [("Cube", "rotation", "jump")]
    assert "Cube" in loop.describe(bad[0], n)


def test_compare_positions_scale_lens_and_shape_keys():
    n, tol = 60, loop.tolerances(10.0)  # 5 cm
    line = {("Box", "position"): lambda f: ("pos", np.array([0.1 * f, 0.0, 0.0]))}
    assert loop.compare(_samples(lambda f: {k: v(f) for k, v in line.items()}, n), n, tol)[0]["what"] == "jump"

    def sine(f):
        return {("Box", "position"): ("pos", np.array([np.sin(2 * np.pi * (f - 1) / n), 0, 0]))}

    assert loop.compare(_samples(sine, n), n, tol) == []
    rows = loop.compare(_samples(lambda f: {("camera", "focal length"): ("lens", 35.0 + 0.1 * f)}, n), n, tol)
    assert rows[0]["channel"] == "focal length"
    assert loop.compare(_samples(lambda f: {("Face", "shape key 'Smile'"): ("shape", f / n)}, n), n, tol)
    assert loop.compare(_samples(lambda f: {("Box", "scale"): ("scale", np.ones(3))}, n), n, tol) == []


def test_compare_a_change_of_speed_at_the_loop_point_is_a_kink():
    n, tol = 60, loop.tolerances(10.0)  # 5 cm
    # frame n+1 predicted = 0.0 + (0.0 - 0.0) = 0.0 = frame 1: no jump, but it stands still at the end and moves at
    # the start (1 m per frame): the speed changes at the loop point
    xs = {1: 0.0, 2: 1.0, n - 1: 0.0, n: 0.0}

    def pos(f):
        return {("Box", "position"): ("pos", np.array([xs[f], 0.0, 0.0]))}

    rows = loop.compare(_samples(pos, n), n, tol)
    assert [r["what"] for r in rows] == ["kink"]


def test_compare_skips_clips_too_short_to_measure():
    assert loop.compare({1: {}, 2: {}, 3: {}}, 3, loop.tolerances(1.0)) == []


# ---- the sound bed


def _timeline(n_frames=120):
    return Timeline(30, n_frames, [Span("s01", 1, n_frames)], [Span("l1", 31, 60)], [])


def test_circular_xfade_keeps_length_and_is_silent_at_the_wrap():
    rng = np.random.default_rng(1)
    x = rng.standard_normal((SR, 2)) * 0.1 + 0.3
    k = round(LOOP_XFADE_S * SR)
    y = dsp.circular_xfade(x, k)
    assert y.shape == x.shape
    assert np.allclose(y[-1], 0.0, atol=1e-9)  # ends silent and starts silent: no click where it wraps
    assert np.allclose(y[0], 0.0, atol=1e-9)
    assert np.array_equal(y[k:-k], x[k:-k])
    assert dsp.circular_xfade(np.ones(3), 100).shape == (3,)


def test_loop_mix_has_the_same_length_and_a_continuous_wrap():
    short = Short(slug="t", title="T", length_s=4, shots=[Shot("s01", "Hook", "pic", "Scene")],
                  sfx=[Sfx("whoosh", "s01", 0.0, 4.0, -20)])  # fmt: skip
    tl = _timeline(120)
    plain, _ = mix(tl, short, None)
    looped, _ = mix(tl, short, None, loop=True)
    assert looped.shape == plain.shape
    assert np.abs(looped[0]).max() < 1e-3 and np.abs(looped[-1]).max() < 1e-3
    step = np.abs(np.diff(looped, axis=0)).max()
    assert np.abs(looped[0] - looped[-1]).max() <= step + 1e-9
