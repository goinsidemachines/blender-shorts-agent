import dataclasses
import math

import pytest

from bsa import colour, kit, looks
from tests.kitfix import KIT


def test_every_style_has_a_row_and_line_styles_draw_lines():
    assert set(looks.TABLE) == set(kit.STYLES)
    assert {s for s, t in looks.TABLE.items() if t.line_px > 0} == set(looks.LINE_STYLES)
    assert {s for s, t in looks.TABLE.items() if t.view == "Standard"} == set(looks.STANDARD)


def test_toon_sun_energy_is_the_sampler_formula():
    a = min(0.874 * math.pi / 2, math.pi / 2)
    assert looks.toon_sun_energy(0.874, 0.0, 1.0) == pytest.approx(2 * math.pi * (1 - math.cos(a)))


def test_cycles_energy_trims_lit_styles_and_calibrates_toon():
    assert looks.cycles_energy("clay", 10.0, 1.0) == pytest.approx(8.5)
    assert looks.cycles_energy("toon", 4.0, 1.0) == pytest.approx(looks.toon_sun_energy(0.874, 0.0, 1.0))
    assert looks.cycles_energy("pencil", 3.0, 1.0) == pytest.approx(looks.toon_sun_energy(0.95, 0.0, 0.915))


def test_line_px_rules():
    k = kit.kit_from({**KIT, "outline_px": 0})
    assert looks.line_px("toon", k) == 4.0 and looks.line_px("toy", k) == 0
    assert looks.line_px("toy", dataclasses.replace(k, outline_3d=True)) == 2.0
    assert looks.line_px("pencil", dataclasses.replace(k, outline_px=6)) == 6
    assert looks.line_px("toy", dataclasses.replace(k, outline_px=6)) == 0
    assert looks.freestyle_thickness(4.0) == pytest.approx(1.0)


def test_backdrops_and_part_colours_for_the_made_up_kit():
    k = kit.kit_from(KIT)
    assert looks.backdrop("toy", k) == KIT["palette"]["background"].upper()
    assert colour.srgb_to_lab(colour.hex_to_srgb(looks.blueprint_paper(k)))[0] < 40  # a dark paper blue
    assert looks.part_colour("blueprint", "#123456", k, key=True) == k.palette["accent"]
    assert colour.delta_e(looks.warm("#3B8ED9"), "#3B8ED9") > 3  # clay is warmed


def test_shadow_of_is_darker_and_keeps_its_hue_family():
    assert colour.srgb_to_lab(colour.hex_to_srgb(looks.shadow_of("#D9483B", "#1B2A3A")))[0] < \
        colour.srgb_to_lab(colour.hex_to_srgb("#D9483B"))[0]


def test_lit_expect_and_tolerances():
    k = kit.kit_from(KIT)
    assert looks.lit_expect("toy", "#3B8ED9", k, key=False) == "#3B8ED9"
    assert looks.lit_expect("blueprint", "#123456", k, key=True) != k.palette["accent"]
    assert set(looks.LIT_DELTA_E) == set(looks.ENGINE_DELTA_E) == set(kit.STYLES)
    assert looks.lit_limit("toy", "cycles") == 17.3 and looks.lit_limit("clay", "eevee") == 12
    assert looks.lit_limit("toy", "eevee", room=True) == 16.8 and looks.engine_limit("realistic", room=True) == 14.9


def _lin_mix_to_white(hex_, t):
    c = colour.srgb_to_linear(colour.hex_to_srgb(hex_))
    return colour.srgb_to_hex(colour.linear_to_srgb(c + (1 - c) * t))


def test_part_colours_mix_in_linear_light_like_the_sampler():
    k = kit.kit_from(KIT)
    assert looks.part_colour("paper", "#3B8ED9", k, key=False) == _lin_mix_to_white("#3B8ED9", 0.12)
    assert looks.part_colour("pencil", "#123456", k, key=True) == _lin_mix_to_white(k.palette["accent"], 0.25)
    assert looks.part_colour("pencil", "#123456", k, key=False) == _lin_mix_to_white("#F5F2EA", 0.5)
    assert looks.part_colour("blueprint", "#123456", k, key=False) == _lin_mix_to_white(looks.blueprint_paper(k), 0.75)
    assert looks.part_colour("paper", "#000000", k, key=False) == "#616161"  # 0.12 linear is sRGB 97
