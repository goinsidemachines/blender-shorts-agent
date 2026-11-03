import colorsys
import re

import numpy as np
import pytest

from bsa import colour, kit
from bsa.short import Short
from tests.kitfix import DARK, KIT


def test_hex_and_lab():
    assert colour.hex_to_srgb("#F3F2EE") == pytest.approx((0xF3 / 255, 0xF2 / 255, 0xEE / 255))
    assert colour.hex_to_srgb("f3f2ee") == colour.hex_to_srgb("#F3F2EE")
    with pytest.raises(ValueError):
        colour.hex_to_srgb("#12")
    assert colour.srgb_to_lab((1, 1, 1)) == pytest.approx((100, 0, 0), abs=0.05)
    assert colour.srgb_to_lab((0, 0, 0)) == pytest.approx((0, 0, 0), abs=0.05)
    assert colour.srgb_to_linear(colour.linear_to_srgb(np.array([0.2, 0.5, 0.9]))) == pytest.approx([0.2, 0.5, 0.9])
    # the grey a paper brand came out as under AgX defaults is far off; a near miss is not
    assert colour.delta_e("#F3F2EE", "#C5C5C3") > 15
    assert colour.delta_e("#F3F2EE", "#F1F1EC") < 2


def test_dominant_colour_is_the_background():
    px = np.full((200, 100, 3), (243, 242, 238), np.uint8)
    px[50:120, 20:80] = (30, 90, 200)  # a hero covering a third of the frame
    px[0:3] = (0, 0, 0)
    assert colour.dominant_colour(px) == pytest.approx((243 / 255, 242 / 255, 238 / 255), abs=1 / 255)


def test_background_of_the_short_or_the_brand():
    s = Short(slug="t", title="T", length_s=10)
    assert colour.background_hex(s, {}) is None
    assert colour.background_hex(s, {"brand": {"background": "#101010"}}) == "#101010"
    assert colour.background_hex(s, {"brand": {"palette": {"background": "#202020"}}}) == "#202020"
    s.style = {"palette": {"background": "#F3F2EE"}}
    assert colour.background_hex(s, {"brand": {"background": "#101010"}}) == "#F3F2EE"


def test_background_warning():
    paper = np.full((60, 40, 3), (243, 242, 238), np.uint8)
    assert colour.background_warning(paper, "#F3F2EE", "s01") is None
    grey = np.full((60, 40, 3), (197, 197, 195), np.uint8)
    msg = colour.background_warning(grey, "#F3F2EE", "s01")
    assert "s01" in msg and "#C5C5C3" in msg and "#F3F2EE" in msg and "paper_world" in msg


def _world(*node_types, stamp=None, use_nodes=True):
    from types import SimpleNamespace

    nodes = [SimpleNamespace(bl_idname=t) for t in node_types]
    return SimpleNamespace(use_nodes=use_nodes, node_tree=SimpleNamespace(nodes=nodes),
                           get=lambda key, default=None: stamp if key == "bsa_backdrop" else default)  # fmt: skip


def test_only_a_flat_backdrop_gets_the_background_check():
    """A sky, an HDRI or a landscape is not a paper backdrop: the background ΔE check and paper_world advice skip it."""
    flat = ("ShaderNodeOutputWorld", "ShaderNodeBackground")
    assert colour.flat_backdrop(_world(*flat)) is True
    assert colour.flat_backdrop(_world(*flat, "ShaderNodeLightPath", "ShaderNodeMixShader", stamp="paper")) is True
    assert colour.flat_backdrop(_world(use_nodes=False)) is True
    assert colour.flat_backdrop(None) is True  # no world: Blender's plain grey
    assert colour.flat_backdrop(_world(*flat, "ShaderNodeTexSky")) is False
    assert colour.flat_backdrop(_world(*flat, "ShaderNodeTexEnvironment", "ShaderNodeMapping")) is False
    assert colour.flat_backdrop(_world(*flat, "ShaderNodeTexGradient", "ShaderNodeValToRGB")) is False
    assert colour.flat_backdrop(_world(*flat), transparent=True) is False


def test_a_set_world_is_not_a_flat_backdrop():
    """Pegboard, graph paper and sky are environments: a set's world (stamped "set") gets no background check, even
    when it is a plain colour; the studio set's paper_world still does."""
    flat = ("ShaderNodeOutputWorld", "ShaderNodeBackground", "ShaderNodeLightPath", "ShaderNodeMixShader")
    assert colour.flat_backdrop(_world(*flat, stamp="set")) is False
    assert colour.flat_backdrop(_world(*flat, stamp="paper")) is True


def test_set_colours_has_every_key_as_hex():
    c = colour.set_colours(kit.kit_from(KIT))
    assert set(c) == {"wall", "floor", "pattern", "wood", "wood_dark", "ground", "hills_near", "hills_far",
                      "sky_top", "sky_horizon"}  # fmt: skip
    assert all(re.fullmatch(r"#[0-9A-F]{6}", v) for v in c.values())


@pytest.mark.parametrize("brand", [KIT, DARK])
def test_pattern_reads_on_its_wall_in_light_and_dark_kits(brand):
    c = colour.set_colours(kit.kit_from(brand))
    assert 6 <= colour.delta_e(c["pattern"], c["wall"]) <= 25
    assert colour.delta_e(c["floor"], c["wall"]) < colour.delta_e(c["pattern"], c["wall"])


def test_wood_stays_warm_with_a_blue_accent():
    k = kit.kit_from({**KIT, "palette": {**KIT["palette"], "accent": "#2255FF"}})
    h, s, _ = colorsys.rgb_to_hsv(*colour.hex_to_srgb(colour.set_colours(k)["wood"]))
    assert 20 / 360 <= h <= 45 / 360 and s >= 0.35


def test_ground_and_sky_follow_motion_and_heat_or_the_default_code():
    c = colour.set_colours(kit.kit_from(KIT))  # KIT has no motion/heat -> DEFAULT_CODE based
    assert c["sky_top"] == kit.DEFAULT_CODE["heat"]
    assert c["ground"] == colour.mix(kit.DEFAULT_CODE["motion"], "#EAF2F8", 0.55)
    own = kit.kit_from({**KIT, "categories": {"motion": "#AA3300", "heat": "#0099CC"}})
    c = colour.set_colours(own)
    assert c["sky_top"] == "#0099CC" and c["ground"] == colour.mix("#AA3300", "#EAF2F8", 0.55)
    assert c["sky_horizon"] == colour.mix("#0099CC", "#EAF2F8", 0.65)
    dark = colour.set_colours(kit.kit_from({**DARK, "categories": {"heat": "#0099CC"}}))
    assert dark["sky_horizon"] == colour.mix("#0099CC", "#FFFFFF", 0.65)


def test_mix_ends():
    assert colour.mix("#000000", "#FFFFFF", 0) == "#000000" and colour.mix("#000000", "#FFFFFF", 1) == "#FFFFFF"
    assert colour.mix("#000000", "#FFFFFF", 0.5) == "#808080"
