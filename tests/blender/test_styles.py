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
    """The median sRGB (0..1) of the `_patch` (5x5 by default)."""
    return np.median(_patch(scene, obj_name, engine, side, percent, half), axis=0)


def _patch(scene, obj_name: str, engine: str, side: str = "lit", percent: int = 10, half: int = 2) -> np.ndarray:
    """The pixels of a (2 half + 1) square patch rendered at `percent` at the projected centre of the probe object's
    face whose normal is most square to the Key (or Sun) light (`side="dark"`: most turned away from it), among the
    faces that face the camera. With no light (blueprint) the face most square to the camera."""
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
    return _patch_at(scene, best[1], engine, f"{obj_name}_{side}", percent, half)


def _patch_at(scene, point, engine: str, tag: str, percent: int = 10, half: int = 2) -> np.ndarray:
    """The sRGB (0..1) pixels, (2 half + 1)^2 x 3, of a square patch round the world `point`, rendered at `percent`
    (only a border round the patch is rendered, so a big percentage stays quick). Freestyle is off for the probe: the
    patch is a face's middle, away from its lines, and a border with no strokes in it makes Freestyle complain."""
    from bpy_extras.object_utils import world_to_camera_view

    uv = world_to_camera_view(scene, scene.camera, Vector(point))
    r = scene.render
    w, h = r.resolution_x * percent // 100, r.resolution_y * percent // 100
    pad_x, pad_y = (half + 8) / w, (half + 8) / h
    r.use_border, r.use_crop_to_border = True, False
    lines, r.use_freestyle = r.use_freestyle, False
    r.border_min_x, r.border_max_x = max(uv.x - pad_x, 0.0), min(uv.x + pad_x, 1.0)
    r.border_min_y, r.border_max_y = max(uv.y - pad_y, 0.0), min(uv.y + pad_y, 1.0)
    png = OUT / f"{scene.get('bsa_style')}_{engine}_{tag}.png"
    try:
        render_still(scene, scene.frame_current, png, preset=PRESET, percent=percent, engine=engine, file_format="PNG")
    finally:
        r.use_border, r.use_freestyle = False, lines
    img = read_png(png)[..., :3].astype(np.float64) / 255.0
    h, w = img.shape[:2]
    x, y = round(uv.x * w - 0.5), round((1.0 - uv.y) * h - 0.5)
    return img[max(y - half, 0) : y + half + 1, max(x - half, 0) : x + half + 1].reshape(-1, 3)


LIT_STYLES = ("realistic", "toy", "clay", "toon", "blueprint", "paper", "pencil")
KEYED = ("blueprint", "pencil")  # the styles that colour only key parts: the probe is the key part


def _lit_check(room: bool, names=LIT_STYLES) -> None:
    """The probe (the crate, the demo's key part) on its lit face: blueprint and pencil show key parts in colour."""
    for style in names:
        got = {}
        for engine in ("eevee", "cycles"):
            sc, kit, built, _ = _build(engine, style, room=room)
            got[engine] = _probe(sc, built["probe"], engine)
            want = looks.lit_expect(style, SPARK, kit, key=style in KEYED)
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
    """toy -> paper -> pencil -> toon -> blueprint -> realistic ends as realistic applied once: Freestyle, bounces,
    lights, materials, line styles."""
    _build("eevee", "realistic")
    once = _state(bpy.context.scene)
    sc, kit, _built, _ = _build("eevee", "toy")
    for name in ("paper", "pencil", "toon", "blueprint", "realistic"):
        styles.reapply(sc, kit, name)
    after = _state(sc)
    for key in once:
        assert after[key] == once[key], (key, after[key], once[key])


def _world_verts(sc) -> dict:
    """Every mesh's evaluated vertex positions in world space (modifiers applied)."""
    deps = bpy.context.evaluated_depsgraph_get()
    deps.update()
    out = {}
    for obj in sc.objects:
        if obj.type == "MESH":
            ev = obj.evaluated_get(deps)
            co = np.empty(len(ev.data.vertices) * 3)
            ev.data.vertices.foreach_get("co", co)
            m = np.array(ev.matrix_world)
            out[obj.name] = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    return out


def test_paper_moves_no_surface():
    """Paper's crumple is in the shader: it adds no Subdivision, Triangulate or Displace, and with its chamfer (the
    style bevel every bevelled style has) left out every part's evaluated vertices are exactly where they were
    unstyled, so overlap and floating findings can't change. The chamfer itself only cuts corners: no part grows."""
    sc, kit, built, _ = _build("eevee", None)
    before = _world_verts(sc)
    styles.apply(sc, kit, style="paper", **{k: built[k] for k in ("target", "size", "key")})
    added = {m.type for o in sc.objects for m in o.modifiers}
    assert added <= {"BEVEL"}, added
    bevelled = [o for o in sc.objects if o.modifiers.get(styles.BEVEL)]
    assert bevelled and all(o.modifiers[styles.BEVEL].segments == 1 for o in bevelled)
    with_chamfer = _world_verts(sc)
    for obj in bevelled:
        lo, hi = before[obj.name].min(axis=0), before[obj.name].max(axis=0)
        got = with_chamfer[obj.name]
        assert (got >= lo - 1e-5).all() and (got <= hi + 1e-5).all(), obj.name
        obj.modifiers[styles.BEVEL].show_viewport = False
    after = _world_verts(sc)
    assert after.keys() == before.keys()
    for name, co in before.items():
        assert after[name].shape == co.shape and np.array_equal(after[name], co), name


def test_paper_facets_and_pencil_hatching_read_object_space():
    sc, kit, built, _ = _build("eevee", "paper")
    crate = bpy.data.materials["Crate"]
    voronoi = next(n for n in crate.node_tree.nodes if n.type == "TEX_VORONOI")
    assert voronoi.inputs["Vector"].links[0].from_socket.name == "Generated"  # each part its own 0..1 box
    assert abs(voronoi.inputs["Randomness"].default_value - 1.0) < 1e-6
    assert voronoi.inputs["Scale"].default_value == styles.PAPER_CELLS == 4.0  # 4 cells across every part
    assert _outputs(crate) == ["ALL"]
    styles.reapply(sc, kit, "pencil")
    assert _outputs(crate) == ["CYCLES", "EEVEE"]
    waves = [n for n in crate.node_tree.nodes if n.type == "TEX_WAVE"]
    assert len(waves) == 2  # one hatch per engine's branch
    for wave in waves:
        mapping = wave.inputs["Vector"].links[0].from_node
        assert mapping.type == "MAPPING"
        assert mapping.inputs["Vector"].links[0].from_socket.name == "Object"
        assert abs(math.degrees(mapping.inputs["Rotation"].default_value[2]) - 40) < 1e-4
        assert abs(wave.inputs["Scale"].default_value - 60 / built["size"]) < 1e-4


def test_pencil_hatching_is_fixed_to_the_object():
    """The crate's shadowed (hatched) side, rendered, then again after a move; the patch round the face's centre
    must match (mean ΔE <= 2). Two moves: the crate 0.3 m sideways with the camera following (a hatch in world space
    slides across the face), and the frame panned 9 px by lens shift, the crate moving on screen (a hatch in screen
    space stays put while the face moves under it). Each move is checked to catch its wrong hatch: rewired to world
    position, then to window coordinates, the same move changes the patch far more (over 5 times, and over ΔE 1)."""
    from mathutils import Matrix

    sc, _kit, built, _ = _build("eevee", "pencil")
    crate = bpy.data.objects[built["probe"]]
    crate.rotation_euler.z = math.radians(-37)  # its shadowed side broad on screen, as in the toon shadow test
    shift = Matrix.Translation((0.3, 0.0, 0.0))
    pan = 9 / sc.render.resolution_x

    def carry(sign):
        for obj in (crate, sc.camera):
            obj.matrix_world = (shift if sign > 0 else shift.inverted()) @ obj.matrix_world

    def lens(sign):
        sc.camera.data.shift_x += sign * pan

    def mean_de(move, tag):
        first = _patch(sc, crate.name, "eevee", side="dark", percent=100, half=8)
        move(1)
        second = _patch(sc, crate.name, "eevee", side="dark", percent=100, half=8)
        move(-1)
        de = float(np.mean([colour.delta_e(x, y) for x, y in zip(first, second)]))
        print(f"HATCH {tag} dE {de:.2f}")
        return de, first

    def rewire(output):
        mat = crate.material_slots[0].material
        nt = mat.node_tree
        for mapping in [n for n in nt.nodes if n.type == "MAPPING"]:
            src = nt.nodes.new("ShaderNodeNewGeometry") if output == "Position" else nt.nodes.new("ShaderNodeTexCoord")
            nt.links.new(src.outputs[output], mapping.inputs["Vector"])

    carried, patch = mean_de(carry, "object space, carried")
    spread = float(np.std(patch.mean(axis=1)))
    print(f"HATCH spread {spread:.4f}")
    assert spread > 0.01, spread  # the patch is hatched: lines, not a flat grey
    assert carried <= 2, carried
    panned, _ = mean_de(lens, "object space, panned")
    assert panned <= 2, panned
    rewire("Position")  # the wrong hatches, each caught by its move: far over what the object-space hatch moved
    wrong = mean_de(carry, "world space, carried")[0]
    assert wrong > max(1.0, 5 * carried), (wrong, carried)
    rewire("Window")
    wrong = mean_de(lens, "window space, panned")[0]
    assert wrong > max(1.0, 5 * panned), (wrong, panned)


def _cast_shadow_point(sc):
    """A point on the wall well inside the stand plate's cast shadow (10 cm round it shadowed too) that the camera
    sees: points near the plate's right edge carried along the sun's rays onto the wall, the first that qualifies."""
    sun = bpy.data.objects[f"{sc.name} Sun"]
    to_sun = (sun.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    wall_y = bpy.data.objects["Wall"].matrix_world.translation.y - 0.001
    deps = bpy.context.evaluated_depsgraph_get()
    cam = sc.camera.matrix_world.translation

    def shadowed(point):
        return sc.ray_cast(deps, point + to_sun * 0.01, to_sun)[0]

    def seen(point):
        hit, loc, *_ = sc.ray_cast(deps, cam, (point - cam).normalized())
        return hit and (loc - point).length < 0.01

    for x in (1.4, 1.3, 1.2, 1.1, 1.0):
        for z in (1.6, 1.9, 1.3, 2.2):
            on_plate = Vector((x, 1.72, z))
            point = on_plate - to_sun * ((on_plate.y - wall_y) / to_sun.y)
            near = [point + Vector((dx, 0.0, dz)) for dx in (-0.1, 0.0, 0.1) for dz in (-0.1, 0.0, 0.1)]
            if all(shadowed(p) and seen(p) for p in near):
                return point
    raise AssertionError("no point on the wall is in the plate's shadow and seen by the camera")


def test_pencil_cycles_shadow_is_hatched_or_noted():
    """In Cycles the wall in the stand plate's cast shadow is hatched as in EEVEE (more spread than the sampler's
    plain grey shadow), or the fallback to the plain shadow is recorded."""
    assert styles.PENCIL_CYCLES_SHADOW in ("hatched", "plain")
    if styles.PENCIL_CYCLES_SHADOW == "plain":
        return
    spread = {}
    for mode in ("plain", "hatched"):
        styles.PENCIL_CYCLES_SHADOW = mode
        try:
            sc, *_ = _build("cycles", "pencil")
        finally:
            styles.PENCIL_CYCLES_SHADOW = "hatched"
        patch = _patch_at(sc, _cast_shadow_point(sc), "cycles", f"shadow_{mode}", percent=100, half=8)
        spread[mode] = float(np.var(patch.mean(axis=1)))
    print(f"SHADOW pencil cycles variance {spread}")
    assert spread["hatched"] > 20 * spread["plain"] and spread["hatched"] > 1e-4, spread


def test_pencil_lines_are_sketchy_and_reapplied_once():
    sc, kit, built, _ = _build("eevee", "pencil")
    assert _linesets(sc) == [["bsa ink", "bsa pencil"]]
    fs = sc.view_layers[0].freestyle_settings
    assert not fs.linesets["bsa ink"].show_render and fs.linesets["bsa pencil"].show_render
    style = fs.linesets["bsa pencil"].linestyle
    assert style.chaining == "SKETCHY" and style.rounds == 3 and abs(style.alpha - 0.85) < 1e-6
    want = colour.srgb_to_linear(colour.hex_to_srgb("#34343A"))
    assert all(abs(a - b) < 1e-4 for a, b in zip(style.color, want))
    assert [m.type for m in style.thickness_modifiers] == ["ALONG_STROKE"]
    noise, stretch = style.geometry_modifiers
    assert (noise.type, noise.frequency, round(noise.amplitude, 4)) == ("PERLIN_NOISE_1D", 8, 1.6)
    assert (stretch.type, stretch.backbone_length) == ("BACKBONE_STRETCHER", 6)
    once = _counts(sc)
    styles.apply(sc, kit, **{k: built[k] for k in ("target", "size", "key")})
    assert _counts(sc) == once
    style = sc.view_layers[0].freestyle_settings.linesets["bsa pencil"].linestyle  # made again, as blueprint's is
    assert len(style.geometry_modifiers) == 2 and len(style.thickness_modifiers) == 1
    assert once["lights"] == 1 and bpy.data.objects.get(f"{sc.name} Sun") is not None
    styles.reapply(sc, kit, "toon")
    assert _linesets(sc) == [["bsa ink"]] and sc.view_layers[0].freestyle_settings.linesets["bsa ink"].show_render
    assert "bsa pencil" not in bpy.data.linestyles
    _no_dot_names()
