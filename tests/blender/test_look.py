import math
import tempfile
from pathlib import Path

import bpy
import numpy as np

from bsa import colour
from bsa.blender import look, pixels
from bsa.blender.scene import setup_scene
from bsa.short import Short

PAPER = "#F3F2EE"


def _scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    setup_scene(sc, Short(slug="t", title="T", length_s=1), "cycles", "still")
    sc.cycles.samples = 4
    sc.cycles.use_denoising = False
    sc.render.resolution_percentage = 5
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = cam
    return sc


def _render_background(sc) -> np.ndarray:
    out = Path(tempfile.mkdtemp()) / "bg.png"
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True, scene=sc.name)
    return pixels.read_png(out)


def test_a_plain_world_renders_grey_under_agx():
    """The problem: a world of the paper colour at strength 1 comes out visibly darker on screen."""
    sc = _scene()
    world = bpy.data.worlds.new("plain")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (*colour.srgb_to_linear(colour.hex_to_srgb(PAPER)), 1.0)
    sc.world = world
    assert colour.delta_e(PAPER, colour.dominant_colour(_render_background(sc))) > 5


def _plane(name, y, size, mat):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, y, 0), rotation=(math.radians(90), 0, 0))
    obj = bpy.context.object
    obj.name = name
    look.assign(obj, mat)
    return obj


def _alpha(mat) -> float:
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Alpha"].default_value


def test_toy_water_is_tinted_and_see_through_in_eevee():
    """Toy water: blended, alpha 0.4 (0.7 for a stream), a clear coat, no refraction; a red card behind a blue
    water plane still shows through it, tinted, in EEVEE."""
    water = look.toy_water("Water", "#3366CC")
    stream = look.toy_water("Stream", (0.2, 0.4, 0.8), stream=True)
    assert water.surface_render_method == "BLENDED" and stream.surface_render_method == "BLENDED"
    bsdf = next(n for n in water.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert bsdf.inputs["Coat Weight"].default_value == 1.0 and bsdf.inputs["Transmission Weight"].default_value == 0
    assert math.isclose(_alpha(water), look.WATER_ALPHA, abs_tol=1e-6)
    assert math.isclose(_alpha(stream), look.STREAM_ALPHA, abs_tol=1e-6)
    assert math.isclose(_alpha(look.toy_water("W2", "#FFFFFF", alpha=0.2)), 0.2, abs_tol=1e-6)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    setup_scene(sc, Short(slug="t", title="T", length_s=1), "eevee", "still")
    sc.render.resolution_percentage = 5
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = cam
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.rotation_euler = (math.radians(90), 0, 0)  # shines along +y, onto the planes' camera side
    sc.collection.objects.link(sun)
    _plane("Card", 10, 40, look.material("Card", (1.0, 0.0, 0.0), kind="emissive"))

    def centre():
        img = _render_background(sc)
        h, w = img.shape[:2]
        return img[h // 2 - 2 : h // 2 + 2, w // 2 - 2 : w // 2 + 2, :3].reshape(-1, 3).mean(axis=0)

    bare = centre()
    _plane("Water", 4, 20, look.toy_water("Water", "#3366CC"))
    seen = centre()
    assert seen[0] > 0.35 * bare[0], (bare, seen)  # the red card shows through
    assert np.abs(seen - bare).max() > 0.05 and seen[2] > bare[2], (bare, seen)  # tinted blue by the water


def test_paper_world_renders_the_brand_colour():
    sc = _scene()
    out = look.paper_world(sc, PAPER, exposure=0.0)
    assert out["delta_e"] < 1.5, out
    got = colour.dominant_colour(_render_background(sc))
    assert colour.delta_e(PAPER, got) < 2.0, (colour.srgb_to_hex(got), out)
    assert colour.background_warning(_render_background(sc), PAPER, "s01") is None
    assert sc.view_settings.view_transform == "AgX"
    assert colour.flat_backdrop(sc.world) and sc.world["bsa_backdrop"] == "paper"
    sky = bpy.data.worlds.new("sky")
    sky.use_nodes = True
    sky.node_tree.nodes.new("ShaderNodeTexSky")
    assert not colour.flat_backdrop(sky)  # a sky is not a backdrop: the stills skip its background check


def test_paper_world_with_a_look_and_exposure():
    sc = _scene()
    out = look.paper_world(sc, "#2B6CB0", exposure=0.3, look="Punchy")
    assert sc.view_settings.look.endswith("Punchy") and math.isclose(sc.view_settings.exposure, 0.3, abs_tol=1e-6)
    assert out["delta_e"] < 2.0, out
    assert colour.delta_e("#2B6CB0", colour.dominant_colour(_render_background(sc))) < 2.5


def test_brand_view_and_bad_look():
    sc = _scene()
    assert look.brand_view(sc, {"brand": {"exposure": 0.5, "look": "Punchy"}}) == {
        "exposure": 0.5, "look": sc.view_settings.look}
    assert look.brand_view(sc, {"brand": {}}) == {}
    from bsa.short import Short

    fresh = Short(slug="t", title="T", length_s=10, look="fresh")  # its own look: the brand's view stays off
    assert look.brand_view(sc, {"brand": {"exposure": 0.5, "look": "Punchy"}}, short=fresh) == {}
    try:
        look.set_look(sc, "No Such Look")
    except ValueError as exc:
        assert "Punchy" in str(exc)
    else:
        raise AssertionError("an unknown look was accepted")


def test_paper_world_reaches_a_dark_colour_under_punchy():
    """Punchy crushes dark reds to 0: the solve must not stop on that flat region (it used to land on #002031)."""
    sc = _scene()
    out = look.paper_world(sc, "#0E1A2B", look="Punchy")
    assert out["delta_e"] < 2.0, out
    assert "warning" not in out
    assert colour.delta_e("#0E1A2B", colour.dominant_colour(_render_background(sc))) < 2.5


def test_paper_world_says_when_the_view_cant_reach_the_colour():
    sc = _scene()
    out = look.paper_world(sc, "#FF0000", exposure=-3.0)
    assert out["delta_e"] > 2.0, out
    assert "can't reach" in out["warning"] and "#FF0000" in out["warning"] and out["srgb"] in out["warning"]


def test_a_fresh_short_keeps_the_view_even_when_build_py_omits_the_short():
    """setup_scene stamps the short's look on the scene: brand_view and paper_world without short= still leave the
    brand's exposure and look off for a fresh short."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    setup_scene(sc, Short(slug="t", title="T", length_s=1, look="fresh"), "cycles", "still")
    brand = {"brand": {"mode": "brand", "exposure": 0.5, "look": "Punchy"}}
    assert look.brand_view(sc, brand) == {}
    assert sc.view_settings.exposure == 0.0 and not sc.view_settings.look.endswith("Punchy")
    plain = bpy.data.scenes.new("plain")
    setup_scene(plain, Short(slug="t", title="T", length_s=1), "cycles", "still")
    assert look.brand_view(plain, brand)["exposure"] == 0.5
