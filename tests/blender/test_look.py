import math
import sys
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


# --- kit materials, kit lights, ink lines and engine matching (round 4b) -------------------------------------------

SPARK = "#D9483B"  # tests/kitfix.KIT's "spark" category


def _tiny(engine: str):
    """An empty scene on `engine` (the test's own choice), 108x192, a camera looking +Y at the origin."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    setup_scene(sc, Short(slug="t", title="T", length_s=1), engine, "still")
    sc.render.resolution_percentage = 10
    sc.cycles.samples = 32
    sc.cycles.use_denoising = False
    sc.cycles.device = "CPU"
    sc.eevee.taa_render_samples = 8
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.location = (0, -6, 0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = cam
    return sc


def _white_world(sc, strength=1.0):
    world = bpy.data.worlds.new("white")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (1, 1, 1, 1)
    bg.inputs["Strength"].default_value = strength
    sc.world = world
    return world


def test_kit_material_is_stamped_and_renders_its_colour():
    sc = _tiny("cycles")
    sc.view_settings.view_transform = "Standard"
    _white_world(sc, 0.0)
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sc.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(90 - 45), 0, 0)  # 45 degrees off the face: its highlight misses the camera
    sun.data.energy = math.pi / math.cos(math.radians(45))  # a diffuse face lit so shows exactly its own colour
    sun.data.angle = 0.0
    mat = look.kit_material("Toy spark", "toy", SPARK.lower())
    assert look.stamp(mat) == {"kind": "toy", "colour": SPARK, "pattern": None}
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert math.isclose(bsdf.inputs["Roughness"].default_value, 0.28, abs_tol=1e-6)
    assert math.isclose(bsdf.inputs["Coat Weight"].default_value, 0.6, abs_tol=1e-6)
    assert math.isclose(bsdf.inputs["Coat Roughness"].default_value, 0.08, abs_tol=1e-6)
    assert not any(n.type == "EMISSION" for n in mat.node_tree.nodes)
    assert bsdf.inputs["Emission Strength"].default_value == 0.0  # lit, never glowing
    _plane("Card", 0, 20, mat)
    got = colour.dominant_colour(_render_background(sc))
    assert colour.delta_e(SPARK, got) < 5, colour.srgb_to_hex(got)
    from bsa.blender.scene import eevee_id

    sc.render.engine = eevee_id()  # the same card in EEVEE (the test picks its engines)
    got = colour.dominant_colour(_render_background(sc))
    assert colour.delta_e(SPARK, got) < 5, ("eevee", colour.srgb_to_hex(got))
    for kind in look.KIT_KINDS:
        for pattern in look.PATTERNS:
            m = look.kit_material(f"m {kind} {pattern}", kind, "#3B8ED9", pattern)
            assert look.stamp(m) == {"kind": kind, "colour": "#3B8ED9", "pattern": pattern}
            base = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Base Color"]
            assert base.is_linked == (pattern is not None), (kind, pattern)  # the pattern drives the colour
    assert look.stamp(look.material("plain", (1, 0, 0))) is None
    for bad in (("x", "chrome", SPARK, None), ("x", "toy", SPARK, "stripes")):
        try:
            look.kit_material(*bad)
        except ValueError as exc:
            assert ("toy_water" in str(exc)) if bad[1] == "chrome" else ("wood" in str(exc)), exc
        else:
            raise AssertionError(f"{bad} was accepted")


def test_kit_lights_stamp_both_engines_and_replace_the_old_rig():
    sc = _tiny("eevee")
    rig = look.kit_lights(sc, (0, 0, 0), 1.0, 2.0, cycles_scale=0.8)
    assert rig["kind"] == "area" and len(rig["lights"]) == 3
    for obj in rig["lights"]:
        stamp = obj["bsa_energy"]
        assert math.isclose(stamp["cycles"], stamp["eevee"] * 0.8, rel_tol=1e-6)
        assert math.isclose(obj.data.energy, stamp["eevee"], rel_tol=1e-6)  # the scene is on EEVEE
    names = {f"{sc.name} {n}" for n in ("Key", "Fill", "Rim")}
    assert names <= {o.name for o in sc.objects}
    key_dir = rig["lights"][0].location.normalized()
    one = look.kit_lights(sc, (0, 0, 0), 1.0, 4.0, single_sun=True, sun_angle_deg=0.5, cycles_scale=1.5)
    suns = [o for o in sc.objects if o.type == "LIGHT"]
    assert [o.name for o in suns] == [f"{sc.name} Sun"] and one["lights"] == suns and one["kind"] == "sun"
    assert not any(n in bpy.data.objects for n in names)
    sun = suns[0]
    assert sun.data.type == "SUN" and math.isclose(math.degrees(sun.data.angle), 0.5, abs_tol=1e-4)
    assert sun["bsa_energy"]["eevee"] == 4.0 and math.isclose(sun["bsa_energy"]["cycles"], 6.0, rel_tol=1e-6)
    assert (sun.location.normalized() - key_dir).length < 1e-4  # from the key's direction
    big = look.kit_lights(sc, (0, 0, 0), 1.0, 2.0, sun=True, sun_angle_deg=3.0)
    assert big["kind"] == "sun" and len(big["lights"]) == 3 and f"{sc.name} Sun" not in bpy.data.objects
    assert all(math.isclose(math.degrees(o.data.angle), 3.0, abs_tol=1e-4) for o in big["lights"])
    assert look.lights(sc, (0, 0, 0), 1.0, kind="sun")["kind"] == "sun"


def _vertex_counts(sc) -> dict:
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    return {o.name: len(o.evaluated_get(dg).data.vertices) for o in sc.objects if o.type == "MESH"}


def test_ink_lines_add_no_geometry_and_exclude_set_surfaces():
    from bsa import looks
    from bsa.blender import bake

    sc = _tiny("eevee")
    bpy.ops.mesh.primitive_cube_add(size=1)
    part = bpy.context.object
    bpy.ops.mesh.primitive_plane_add(size=10)
    floor = bpy.context.object
    bake.tag(floor, "set:floor")
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.2, location=(1, 0, 0))
    glow = bpy.context.object
    bake.tag(glow, "lines:off")
    bpy.ops.mesh.primitive_cone_add(location=(-1, 0, 0))
    arrow = bpy.context.object
    bake.tag(arrow, "overlay")
    bpy.ops.mesh.primitive_cylinder_add(location=(0, 1, 0))
    extra = bpy.context.object
    before = _vertex_counts(sc)
    ls = look.ink_lines(sc, "#1B2A3A", 4.0, exclude=[extra])
    assert _vertex_counts(sc) == before  # Freestyle: lines drawn at render, no shells
    assert sc.render.use_freestyle and sc.render.line_thickness_mode == "RELATIVE"
    assert ls.name == "bsa ink" and ls.select_crease and ls.select_silhouette and ls.select_border
    assert ls.select_external_contour and ls.visibility == "VISIBLE"
    assert math.isclose(ls.linestyle.thickness, looks.freestyle_thickness(4.0), rel_tol=1e-6)
    no = bpy.data.collections[f"{sc.name} no lines"]
    assert ls.select_by_collection and ls.collection == no and ls.collection_negation == "EXCLUSIVE"
    assert {o.name for o in no.objects} == {floor.name, glow.name, arrow.name, extra.name}
    assert part.name not in no.objects
    fs = sc.view_layers[0].freestyle_settings
    assert math.isclose(math.degrees(fs.crease_angle), 140, abs_tol=1e-3)
    look.ink_lines(sc, "#1B2A3A", 3.0, exclude=[extra])
    assert [s.name for s in fs.linesets if s.show_render] == ["bsa ink"]
    assert len([c for c in bpy.data.collections if c.name.startswith(f"{sc.name} no lines")]) == 1
    look.ink_lines(sc, "#1B2A3A", 0)
    assert not sc.render.use_freestyle and "bsa ink" not in [s.name for s in fs.linesets]


def _stamped_light(sc, eevee=10.0, cycles=7.0):
    obj = bpy.data.objects.new("Stamped", bpy.data.lights.new("Stamped", "SUN"))
    sc.collection.objects.link(obj)
    obj["bsa_energy"] = {"eevee": eevee, "cycles": cycles}
    obj.data.energy = 1.0
    return obj


def test_match_engine_reads_the_engine_and_never_writes_it():
    from bsa.blender.scene import eevee_id

    sc = _tiny("cycles")
    stamped = _stamped_light(sc)
    plain = bpy.data.objects.new("Plain", bpy.data.lights.new("Plain", "POINT"))
    sc.collection.objects.link(plain)
    plain.data.energy = 5.0
    world = _white_world(sc, 0.3)
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    world["bsa_strength"] = {bg.name: {"eevee": 0.6, "cycles": 0.2}}
    sc["bsa_style_bounces"] = 0
    sc.cycles.max_bounces = 12
    assert look.match_engine(sc) == "cycles"
    assert sc.render.engine == "CYCLES" and stamped.data.energy == 7.0 and plain.data.energy == 5.0
    assert math.isclose(bg.inputs["Strength"].default_value, 0.2, abs_tol=1e-6) and sc.cycles.max_bounces == 0
    assert sc["bsa_style_engine"] == "cycles"
    sc.render.engine = eevee_id()  # the user switches the engine by hand
    assert look.match_engine(sc) == "eevee"
    assert sc.render.engine == eevee_id() and stamped.data.energy == 10.0 and plain.data.energy == 5.0
    assert math.isclose(bg.inputs["Strength"].default_value, 0.6, abs_tol=1e-6)
    assert sc["bsa_style_engine"] == "eevee"
    bare = _tiny("cycles")  # no kit: nothing is stamped and nothing changes
    bare.cycles.max_bounces = 12
    assert look.match_engine(bare) == "cycles" and bare.cycles.max_bounces == 12 and "bsa_style_engine" not in bare


def _record_energy(light):
    seen = []

    def pre(scene, *_):
        seen.append((scene.render.engine, light.data.energy))

    bpy.app.handlers.render_pre.append(pre)
    return seen, pre


def test_render_still_matches_the_engine_it_renders_with():
    from bsa.blender.stills import render_still

    sc = _tiny("eevee")
    light = _stamped_light(sc)
    look.match_engine(sc)
    assert light.data.energy == 10.0
    seen, pre = _record_energy(light)
    try:
        render_still(sc, 1, Path(tempfile.mkdtemp()) / "s.jpg", percent=5, engine="cycles")
    finally:
        bpy.app.handlers.render_pre.remove(pre)
    assert seen == [("CYCLES", 7.0)], seen
    assert sc.render.engine != "CYCLES" and light.data.energy == 10.0  # restored: the scene's own engine again


def test_every_render_entry_point_matches_the_engine():
    from bsa.blender.render import estimate_seconds, render_range

    sc = _tiny("eevee")
    light = _stamped_light(sc)
    seen, pre = _record_energy(light)
    try:
        render_range(sc, 1, 1, Path(tempfile.mkdtemp()), "preview", percent=5, engine="cycles")
        estimate_seconds(sc, 1, "cycles", 10, percent=5)
    finally:
        bpy.app.handlers.render_pre.remove(pre)
    assert seen == [("CYCLES", 7.0), ("CYCLES", 7.0)], seen
    assert light.data.energy == 10.0


def test_paper_world_stamps_its_colour():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kitfix import KIT

    paper = KIT["palette"]["background"]
    sc = _scene()
    look.paper_world(sc, paper.lower(), light=0.7, exposure=0.0)
    assert dict(sc.world["bsa_paper"]) == {"hex": paper.upper(), "light": 0.7}


def _freestyle_snapshot(sc) -> dict:
    layers = {
        layer.name: (layer.use_freestyle, layer.freestyle_settings.mode, round(layer.freestyle_settings.crease_angle, 6),
                     [(ls.name, ls.show_render) for ls in layer.freestyle_settings.linesets])
        for layer in sc.view_layers
    }  # fmt: skip
    return {"scene": (sc.render.use_freestyle, sc.render.line_thickness_mode), "layers": layers,
            "keys": sorted(sc.keys()), "collections": sorted(c.name for c in bpy.data.collections),
            "linestyles": sorted(s.name for s in bpy.data.linestyles)}  # fmt: skip


def test_ink_lines_off_puts_the_users_freestyle_back_exactly():
    """A user's own Freestyle (on, with their lineset) survives ink on, ink again, ink off; ink off on a scene that
    never had the ink does nothing at all."""
    sc = _tiny("eevee")
    bpy.ops.mesh.primitive_cube_add(size=1)
    layer = sc.view_layers[0]
    sc.render.use_freestyle = True
    mine = layer.freestyle_settings.linesets.new("My lines")
    mine.show_render = True
    before = _freestyle_snapshot(sc)
    look.ink_lines(sc, "#1B2A3A", 0)  # never on: nothing changes
    assert _freestyle_snapshot(sc) == before
    look.ink_lines(sc, "#1B2A3A", 3.0)
    assert not mine.show_render and sc.render.line_thickness_mode == "RELATIVE"
    look.ink_lines(sc, "#7A4CE0", 4.0)  # a second style with lines (toon then pencil)
    look.ink_lines(sc, "#1B2A3A", 0)  # then one without (realistic)
    assert _freestyle_snapshot(sc) == before
    plain = _tiny("eevee")  # Freestyle off to start with: back off, no trace
    before = _freestyle_snapshot(plain)
    look.ink_lines(plain, "#1B2A3A", 3.0)
    look.ink_lines(plain, "#1B2A3A", 0)
    assert _freestyle_snapshot(plain) == before


def test_a_stamp_error_never_hides_the_renders_own_error():
    from bsa.blender.scene import preserved_render_state

    sc = _tiny("eevee")
    light = _stamped_light(sc)
    light["bsa_energy"] = {"cycles": 7.0}  # broken: no eevee value, match_engine raises
    try:
        with preserved_render_state(sc):
            raise RuntimeError("the render failed")
    except RuntimeError as exc:
        assert "the render failed" in str(exc)
    else:
        raise AssertionError("the render's error was lost")
    try:
        with preserved_render_state(sc):
            pass
    except KeyError:
        pass  # no render error to hide: the stamp problem shows
    else:
        raise AssertionError("a broken stamp passed silently")
