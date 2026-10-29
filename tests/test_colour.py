import numpy as np
import pytest

from bsa import colour
from bsa.short import Short


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
