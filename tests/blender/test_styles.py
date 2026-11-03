import math
import os
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kitfix import KIT

from bsa import colour, looks
from bsa.blender import demo, look, styles
from bsa.blender import scene as scene_mod
from bsa.blender.pixels import read_png
from bsa.blender.scene import setup_scene
from bsa.blender.stills import render_still
from bsa.kit import kit_from
from bsa.short import Short

scene_mod.use_gpu = lambda: "NONE"  # Cycles on the CPU, as the plan pins tests: the same noise on every machine
PRESET = "bsa test tiny"
scene_mod.PRESETS[PRESET] = {"percent": 10, "cycles": {"samples": 32, "denoise": False}, "eevee": {"samples": 8}}
OUT = Path(os.environ.get("BSA_STYLE_TEST_OUT") or tempfile.mkdtemp())
SPARK = "#D9483B"


def _kit(style: str):
    return kit_from({**KIT, "style": style})


def _build(engine: str, style: str | None = "toy", room: bool = True):
    """A fresh scene with the demo machine in its room (the floor and wall shorts are lit with), styled with `style`
    (None: built, not styled)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    setup_scene(sc, Short(slug="t", title="T", length_s=1), engine, "still")
    kit = _kit(style or "toy")
    built = demo.lever(sc, kit, room=room)
    args = {k: built[k] for k in ("target", "size", "key")}
    result = styles.apply(sc, kit, **args) if style else None
    return sc, kit, built, result


def _probe(scene, obj_name: str, engine: str, side: str = "lit", percent: int = 10, half: int = 2) -> np.ndarray:
    """The median sRGB (0..1) of a (2 half + 1) square patch (5x5) rendered at `percent` at the projected centre of the probe object's face whose normal is most
    square to the Key (or Sun) light (`side="dark"`: most turned away from it), among the faces that face the camera.
    With no light (blueprint) the face most square to the camera."""
    from bpy_extras.object_utils import world_to_camera_view

    scene.view_layers[0].update()
    obj = bpy.data.objects[obj_name]
    light = bpy.data.objects.get(f"{scene.name} Key") or bpy.data.objects.get(f"{scene.name} Sun")
    cam = scene.camera
    mw = obj.matrix_world
    rot = mw.to_3x3().inverted().transposed()
    best = None
    for poly in obj.data.polygons:
        centre = mw @ poly.center
        normal = (rot @ poly.normal).normalized()
        to_cam = (cam.matrix_world.translation - centre).normalized()
        if normal.dot(to_cam) <= 0.05:
            continue
        if light is None:
            toward = to_cam
        else:
            toward = (light.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()  # a light shines down -Z
        lit = normal.dot(toward) * (1 if side == "lit" else -1)
        if best is None or lit > best[0]:
            best = (lit, centre)
    png = OUT / f"{scene.get('bsa_style')}_{engine}_{obj_name}_{side}.png"
    render_still(scene, scene.frame_current, png, preset=PRESET, percent=percent, engine=engine, file_format="PNG")
    img = read_png(png)[..., :3].astype(np.float64) / 255.0
    h, w = img.shape[:2]
    uv = world_to_camera_view(scene, cam, best[1])
    x, y = round(uv.x * w - 0.5), round((1.0 - uv.y) * h - 0.5)
    patch = img[max(y - half, 0) : y + half + 1, max(x - half, 0) : x + half + 1].reshape(-1, 3)
    return np.median(patch, axis=0)


def _lit_check(room: bool) -> None:
    """The probe (the crate, the demo's key part) on its lit face: blueprint shows key parts in the accent colour."""
    for style in ("realistic", "toy", "clay", "toon", "blueprint"):
        got = {}
        for engine in ("eevee", "cycles"):
            sc, kit, built, _ = _build(engine, style, room=room)
            got[engine] = _probe(sc, built["probe"], engine)
            want = looks.lit_expect(style, SPARK, kit, key=style == "blueprint")
            de = colour.delta_e(want, got[engine])
            limit = looks.lit_limit(style, engine, room)
            print(f"LIT room={room} {style} {engine} {colour.srgb_to_hex(got[engine])} want {want} dE {de:.2f}")
            assert de <= limit, (room, style, engine, de, limit)
        between = colour.delta_e(got["eevee"], got["cycles"])
        limit = looks.engine_limit(style, room)
        print(f"ENGINES room={room} {style} dE {between:.2f}")
        assert between <= limit, (room, style, between, limit)


def test_lit_colour_matches_the_kit_in_both_engines():
    _lit_check(room=True)


def test_lit_colour_matches_without_a_floor_too():
    _lit_check(room=False)


def _principled_of(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def test_parts_take_the_style_whatever_their_kind_and_sets_stay_matte():
    """A matte-kind part (the gears) is glossy toy plastic in toy and bumped clay in clay; a material only set
    objects use (the room's floor and wall) is a matte Principled in every style."""
    for style in ("toy", "clay", "realistic"):
        _build("eevee", style)
        gear = bpy.data.materials["GearBig"]
        kinds = {n.bl_idname for n in gear.node_tree.nodes}
        bsdf = _principled_of(gear)
        if style == "toy":
            assert (
                "ShaderNodeLayerWeight" in kinds
                and abs(bsdf.inputs["Coat Weight"].default_value - look.TOY_COAT) < 1e-6
            )
            assert abs(bsdf.inputs["Roughness"].default_value - look.TOY_ROUGHNESS) < 1e-6
        if style == "clay":
            assert "ShaderNodeBump" in kinds and abs(bsdf.inputs["Roughness"].default_value - 0.88) < 1e-6
        for name in ("Floor", "Wall"):
            floor = bpy.data.materials[name]
            assert {n.bl_idname for n in floor.node_tree.nodes} == {
                "ShaderNodeOutputMaterial",
                "ShaderNodeBsdfPrincipled",
            }, (style, name)
            assert abs(_principled_of(floor).inputs["Roughness"].default_value - look.MATTE_ROUGHNESS) < 1e-6
            assert not bpy.data.objects[name].modifiers


def test_a_key_copy_goes_on_the_key_object_only():
    sc, kit, _built, _ = _build("eevee", None)
    crate = bpy.data.objects["Crate"]
    twin = bpy.data.objects.new("Twin", crate.data)  # a linked duplicate: same mesh, same material slot
    sc.collection.objects.link(twin)
    twin.location = (2.5, 0, 0.4)
    styles.apply(sc, kit, target="Lever", size=3.6, key=["Crate"])
    assert crate.material_slots[0].material.name == "Crate key"
    assert twin.material_slots[0].material.name == "Crate"


def _counts(sc) -> dict:
    return {
        "objects": len(bpy.data.objects),
        "scene objects": len(sc.objects),
        "lights": len(bpy.data.lights),
        "materials": len(bpy.data.materials),
        "worlds": len(bpy.data.worlds),
        "modifiers": sum(len(o.modifiers) for o in sc.objects),
        "style bevels": sum(1 for o in sc.objects for m in o.modifiers if m.name == "bsa style bevel"),
        "linesets": sum(len(v.freestyle_settings.linesets) for v in sc.view_layers),
        "linestyles": len(bpy.data.linestyles),
        "collections": len(bpy.data.collections),
        "nodes": {m.name: len(m.node_tree.nodes) for m in bpy.data.materials if m.node_tree},
    }


def _no_dot_names():
    for coll in (bpy.data.objects, bpy.data.materials, bpy.data.lights, bpy.data.worlds, bpy.data.meshes,
                 bpy.data.linestyles, bpy.data.collections):  # fmt: skip
        bad = [x.name for x in coll if x.name[-4:-3] == "." and x.name[-3:].isdigit()]
        assert not bad, bad


def test_without_a_kit_styles_change_nothing():
    sc, *_ = _build("eevee", None)
    before = _counts(sc)
    assert styles.apply(sc, None) == {"style": None}
    assert sc.view_settings.view_transform == "AgX"
    assert _counts(sc) == before
    assert "bsa_style" not in sc and "bsa_style_args" not in sc


def test_apply_twice_is_the_same_as_once():
    sc, kit, built, first = _build("eevee", "toy")
    once = _counts(sc)
    again = styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
    assert _counts(sc) == once
    assert once["style bevels"] > 0 and once["lights"] == len(looks.RIGS["toy"])
    assert again["lights"] == first["lights"] and again["restyled"] == first["restyled"]
    _no_dot_names()


def _tree(mat) -> tuple:
    def value(sock):
        v = getattr(sock, "default_value", None)
        if v is None:
            return None
        try:
            return tuple(round(float(x), 5) for x in v)
        except TypeError:
            return round(float(v), 5) if isinstance(v, (int, float)) else str(v)

    nodes = sorted((n.bl_idname, tuple((s.identifier, value(s)) for s in n.inputs if not s.is_linked))
                   for n in mat.node_tree.nodes)  # fmt: skip
    links = sorted((lk.from_node.bl_idname, lk.from_socket.identifier, lk.to_node.bl_idname, lk.to_socket.identifier)
                   for lk in mat.node_tree.links)  # fmt: skip
    return tuple(nodes), tuple(links), getattr(mat, "surface_render_method", None)


def _stamped_trees() -> dict:
    return {m.name: _tree(m) for m in bpy.data.materials if look.stamp(m) is not None}


def test_reapply_switches_style_and_back():
    sc, kit, _built, _ = _build("eevee", "toy")
    trees, counts = _stamped_trees(), _counts(sc)
    assert sc.view_settings.view_transform == "Standard"
    clay = styles.reapply(sc, kit, "clay")
    assert clay["style"] == "clay" and sc.view_settings.view_transform == "AgX"
    assert _stamped_trees() != trees
    styles.reapply(sc, kit, "toy")
    assert sc.view_settings.view_transform == "Standard"
    assert _stamped_trees() == trees
    assert _counts(sc) == counts
    realistic = styles.reapply(sc, kit, "realistic")  # toy -> clay -> realistic: no stray lights, worlds, materials
    after = _counts(sc)
    assert realistic["style"] == "realistic"
    rigs = len(looks.RIGS["realistic"]) - len(looks.RIGS["toy"])  # realistic has its own rig: no stray lights
    assert after["lights"] == counts["lights"] + rigs and after["objects"] == counts["objects"] + rigs
    assert sum(1 for o in sc.objects if o.type == "LIGHT") == len(looks.RIGS["realistic"])
    for key in ("materials", "worlds", "style bevels", "linesets", "collections"):
        assert after[key] == counts[key], key
    _no_dot_names()
    try:
        styles.reapply(bpy.data.scenes.new("never"), kit)
    except ValueError as exc:
        assert "never styled" in str(exc)
    else:
        raise AssertionError("reapply on an unstyled scene passed")


def test_materials_not_from_kit_material_are_kept_and_named():
    sc, kit, built, _ = _build("eevee", None)
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(2.5, 0, 0.25))
    mine = look.material("Mine", (0.2, 0.3, 0.4))
    look.assign(bpy.context.object, mine)
    before = _tree(mine)
    result = styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
    assert _tree(mine) == before
    assert result["kept"] == ["Mine"]
    named = [w for w in result["warnings"] if "Mine" in w]
    assert len(named) == 1 and "kit_material" in named[0] and "toy" in named[0]
    assert not any(m.name == "bsa style bevel" for m in bpy.context.object.modifiers)


def test_match_engine_follows_the_render_engine_not_the_build():
    sc, kit, built, _ = _build("eevee", "toy")
    before = sc.render.engine
    got = _probe(sc, built["probe"], "cycles")
    assert sc.render.engine == before
    de = colour.delta_e(looks.lit_expect("toy", SPARK, kit, key=False), got)
    print(f"BUILT EEVEE RENDERED CYCLES dE {de:.2f}")
    assert de <= looks.lit_limit("toy", "cycles", room=True)


def test_engine_is_never_changed_by_apply():
    for engine in ("eevee", "cycles"):
        sc, kit, _built, _ = _build(engine, None)
        before = sc.render.engine
        styles.apply(sc, kit, target="Lever", size=3.6, key=["Crate"])
        assert sc.render.engine == before
        styles.reapply(sc, kit, "clay")
        assert sc.render.engine == before


def test_toon_shadow_side_is_the_fixed_dark_step():
    """A face turned away from the sun shows the part's fixed dark step (looks.shadow_of) in both engines. The crate
    is turned 37 degrees so its shadowed side faces the camera broadly (as built it is a sliver on screen)."""
    for engine in ("eevee", "cycles"):
        sc, kit, built, _ = _build(engine, "toon")
        bpy.data.objects[built["probe"]].rotation_euler.z = math.radians(-37)
        got = _probe(sc, built["probe"], engine, side="dark", percent=20)
        want = looks.shadow_of(SPARK, kit.palette["ink"])
        de = colour.delta_e(want, got)
        print(f"SHADOW toon {engine} {colour.srgb_to_hex(got)} want {want} dE {de:.2f}")
        assert de <= 4, (engine, de)


def _outputs(mat) -> list[str]:
    return sorted(n.target for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL")


def test_toon_has_a_material_output_per_engine_and_blueprint_one():
    _build("eevee", "toon")
    for name in ("Crate", "Lever", "Floor", "Wall", "Ink"):
        assert _outputs(bpy.data.materials[name]) == ["CYCLES", "EEVEE"], name
    _build("eevee", "blueprint")
    for mat in (m for m in bpy.data.materials if look.stamp(m) is not None):
        assert _outputs(mat) == ["ALL"], mat.name
    assert bpy.data.materials["Lever"].surface_render_method == "BLENDED"


def _linesets(sc) -> list[list[str]]:
    """The style linesets ("bsa ...") on each view layer (a factory view layer has its own "LineSet" too)."""
    return [sorted(ls.name for ls in v.freestyle_settings.linesets if ls.name.startswith("bsa "))
            for v in sc.view_layers]  # fmt: skip


def test_line_styles_reapplied_keep_one_lineset():
    sc, kit, _built, _ = _build("eevee", "toon")
    assert _linesets(sc) == [["bsa ink"]]
    styles_once = sorted(s.name for s in bpy.data.linestyles)
    no_lines = bpy.data.collections[f"{sc.name} no lines"]
    users = no_lines.users
    assert not sc.view_layers[0].freestyle_settings.linesets["bsa ink"].select_contour
    styles.reapply(sc, kit, "blueprint")
    assert _linesets(sc) == [["bsa hidden", "bsa ink"]]
    fs = sc.view_layers[0].freestyle_settings
    hidden = fs.linesets["bsa hidden"]
    assert hidden.visibility == "HIDDEN" and hidden.linestyle.use_dashed_line
    assert (hidden.linestyle.dash1, hidden.linestyle.gap1) == (5, 4)
    assert abs(hidden.linestyle.alpha - 0.45) < 1e-6
    assert abs(hidden.linestyle.thickness - fs.linesets["bsa ink"].linestyle.thickness / 2) < 1e-6
    assert fs.linesets["bsa ink"].select_contour
    styles.reapply(sc, kit, "toon")
    assert _linesets(sc) == [["bsa ink"]]
    assert not sc.view_layers[0].freestyle_settings.linesets["bsa ink"].select_contour
    assert sorted(s.name for s in bpy.data.linestyles) == styles_once
    assert no_lines.users == users  # removing "bsa hidden" left the shared "no lines" collection its users
    _no_dot_names()


def _verts(sc) -> int:
    deps = bpy.context.evaluated_depsgraph_get()
    deps.update()
    return sum(len(o.evaluated_get(deps).data.vertices) for o in sc.objects if o.type == "MESH")


def test_lines_add_no_geometry():
    sc, kit, built, _ = _build("eevee", None)
    before = _verts(sc)
    styles.apply(sc, kit, style="toon", **{k: built[k] for k in ("target", "size", "key")})
    assert sc.render.use_freestyle
    assert _verts(sc) == before


def test_blueprint_needs_no_lights():
    sc, kit, _built, _ = _build("eevee", "toon")
    assert bpy.data.objects.get(f"{sc.name} Sun") is not None
    result = styles.reapply(sc, kit, "blueprint")
    assert result["lights"] == []
    for name in ("Key", "Fill", "Rim", "Top", "Sun"):
        assert bpy.data.objects.get(f"{sc.name} {name}") is None, name
    assert not any(o.type == "LIGHT" for o in sc.objects) and len(bpy.data.lights) == 0


def test_blueprint_without_a_key_warns():
    sc, kit, built, _ = _build("eevee", None)
    result = styles.apply(sc, kit, target=built["target"], size=built["size"], style="blueprint")
    assert any("pass key=[...] to styles.apply" in w for w in result["warnings"]), result["warnings"]
    keyed = styles.apply(sc, kit, style="blueprint", **{k: built[k] for k in ("target", "size", "key")})
    assert not any("key=" in w for w in keyed["warnings"])


def _state(sc) -> dict:
    layer = sc.view_layers[0]
    fs = layer.freestyle_settings
    return {
        **_counts(sc),
        "trees": _stamped_trees(),
        "freestyle": (sc.render.use_freestyle, sc.render.line_thickness_mode, layer.use_freestyle, fs.mode,
                      round(fs.crease_angle, 5), _linesets(sc)),
        "bounces": sc.cycles.max_bounces,
        "props": sorted(k for k in sc.keys() if k.startswith("bsa_")),  # noqa: SIM118 - an ID, not a dict
        "view": (sc.view_settings.view_transform, sc.view_settings.look),
        "lights": sorted(o.name for o in sc.objects if o.type == "LIGHT"),
        "smooth": {o.name: [p.use_smooth for p in o.data.polygons] for o in sc.objects if o.type == "MESH"},
    }  # fmt: skip


def test_style_switches_leave_no_trace():
    """toy -> toon -> blueprint -> realistic ends as realistic applied once: Freestyle, bounces, lights, materials."""
    _build("eevee", "realistic")
    once = _state(bpy.context.scene)
    sc, kit, _built, _ = _build("eevee", "toy")
    for name in ("toon", "blueprint", "realistic"):
        styles.reapply(sc, kit, name)
    after = _state(sc)
    for key in once:
        assert after[key] == once[key], (key, after[key], once[key])
