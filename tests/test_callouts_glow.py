from bsa.blender import callouts as glow

CUE = {"f_on": 20, "f_off": 60, "culprit": False, "shot": "s01"}


def _keys(cues, last=None, fps=24.0, strength=6.0):
    return dict(glow.strength_keys(cues, strength, fps, last))


def test_ramps_hold_and_fade():
    keys = _keys([CUE])
    assert keys[20] == 0 and keys[26] == 6.0 and keys[60] == 6.0 and keys[70] == 0
    assert 19 not in keys or keys[19] == 0


def test_culprit_pulses_between_half_and_full():
    keys = _keys([{**CUE, "culprit": True}])
    held = [v for f, v in keys.items() if 26 <= f <= 60]
    assert min(held) == 3.0 and max(held) == 6.0
    assert keys[70] == 0 or keys[70] < 6.0


def test_clipped_to_the_shot_end():
    keys = _keys([{**CUE, "f_off": 80}], last={"s01": 80})
    assert max(keys) == 81 and keys[81] == 0 and keys[80] == 6.0  # nothing past the shot, 0 on the next frame
    short = _keys([{**CUE, "f_off": 75}], last={"s01": 80})
    assert short[80] == 3.0 and short[81] == 0  # half way through the fade-out at the shot's last frame


def test_two_cues_on_one_object_take_the_larger():
    keys = _keys([CUE, {**CUE, "f_on": 40, "f_off": 90}])
    assert keys[60] == 6.0 and keys[90] == 6.0 and keys[100] == 0


def test_glow_settings_defaults_and_brand(bsa_home):
    from bsa.short import Short

    short = Short(slug="t", title="T", length_s=10, lines=[], shots=[])
    strength, colour = glow.glow_settings(short, {})
    assert strength == glow.DEFAULT_GLOW and colour.startswith("#") and len(colour) == 7
    brand = {"brand": {"callout": {"color": "#00FF00", "glow": 12}}}
    assert glow.glow_settings(short, brand) == (12.0, "#00FF00")
