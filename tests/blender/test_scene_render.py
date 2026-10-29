import tempfile
import time
from pathlib import Path

import bpy

from bsa.blender import bake, render, stills
from bsa.blender import scene as scene_mod
from bsa.short import Short


def _short():
    return Short(slug="t", title="t", length_s=1.0)


def _tiny_scene(percent=5, engine="eevee", preset="still"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add()
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.location = (0, -6, 0)
    cam.rotation_euler = (1.5708, 0, 0)
    sc.camera = cam
    scene_mod.setup_scene(sc, _short(), engine, preset)
    sc.render.resolution_percentage = percent
    return sc


def test_setup_scene_9x16():
    sc = _tiny_scene()
    assert (sc.render.resolution_x, sc.render.resolution_y) == (1080, 1920)
    assert sc.render.fps == 30
    assert sc.view_settings.view_transform == "AgX"
    assert sc.render.image_settings.file_format == "PNG"
    assert sc.render.image_settings.color_depth == "8"
    assert sc.render.image_settings.color_mode == "RGB"
    assert scene_mod.use_gpu() in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI", "NONE")
    assert sc.render.engine == scene_mod.eevee_id()


def test_render_still_jpeg():
    sc = _tiny_scene()
    out = Path(tempfile.mkdtemp()) / "still.jpg"
    assert stills.render_still(sc, 1, out) == out  # "still" preset is 50%
    assert out.exists() and out.stat().st_size > 0
    img = bpy.data.images.load(str(out))
    assert tuple(img.size) == (540, 960)


def test_resume_rerenders_empty_placeholders():
    sc = _tiny_scene()
    out = Path(tempfile.mkdtemp()) / "frames"
    paths = render.render_range(sc, 1, 3, out, "still", percent=5)
    assert [p.name for p in paths] == ["0001.png", "0002.png", "0003.png"]
    assert all(p.stat().st_size > 0 for p in paths)
    m1, m3 = paths[0].stat().st_mtime_ns, paths[2].stat().st_mtime_ns
    time.sleep(0.05)
    paths[1].write_bytes(b"")
    render.render_range(sc, 1, 3, out, "still", percent=5)
    assert paths[1].stat().st_size > 0
    assert paths[0].stat().st_mtime_ns == m1
    assert paths[2].stat().st_mtime_ns == m3


def test_keyframe_series():
    sc = _tiny_scene()
    obj = bpy.data.objects["Cube"]
    bake.keyframe_series(obj, "location", 0, [0, 1, 2])
    sc.frame_set(2)
    assert abs(obj.location.x - 1.0) < 1e-6
    obj2 = bpy.data.objects.new("Other", None)
    sc.collection.objects.link(obj2)
    bake.keyframe_series(obj2, "location", 0, [0, 2, 4], frame0=1, step=2)
    sc.frame_set(2)  # halfway between keys at frames 1 and 3
    assert abs(obj2.location.x - 1.0) < 1e-6
    fcs = bake._fcurves(obj2.animation_data.action)
    assert fcs and all(kp.interpolation == "LINEAR" for fc in fcs for kp in fc.keyframe_points)
    assert [kp.co[0] for kp in fcs[0].keyframe_points] == [1, 3, 5]


def test_tags_merge():
    _tiny_scene()
    obj = bpy.data.objects["Cube"]
    bake.tag(obj, "hero", "contact:floor")
    bake.tag(obj, "hero", "grounded")
    assert bake.tags(obj) == ["hero", "contact:floor", "grounded"]
    assert obj["bsa_tags"] == "hero,contact:floor,grounded"
    assert bake.tags(bpy.data.objects["Cam"]) == []
    try:
        bake.tag(obj, "a,b")
    except ValueError:
        pass
    else:
        raise AssertionError("comma tag accepted")


def test_estimate_restores_scene_state():
    sc = _tiny_scene(engine="eevee", preset="still")
    before = (sc.render.engine, sc.cycles.samples, sc.render.use_motion_blur, sc.render.resolution_percentage)
    render.estimate_seconds(sc, 1, "cycles", 2, percent=5)
    assert (sc.render.engine, sc.cycles.samples, sc.render.use_motion_blur, sc.render.resolution_percentage) == before
    assert sc.render.engine == scene_mod.eevee_id()


def test_render_range_raises_on_failed_render():
    import types

    sc = _tiny_scene()
    real = render.bpy
    fake_render = types.SimpleNamespace(render=lambda **k: {"CANCELLED"})
    render.bpy = types.SimpleNamespace(ops=types.SimpleNamespace(render=fake_render))
    try:
        render.render_range(sc, 1, 2, Path(tempfile.mkdtemp()) / "f", "still")
    except RuntimeError as e:
        assert "0001.png" in str(e) and "0002.png" in str(e)
    else:
        raise AssertionError("no error on failed render")
    finally:
        render.bpy = real


def test_explicit_engine_overrides_scene():
    sc = _tiny_scene(engine="eevee")
    out = Path(tempfile.mkdtemp()) / "f"
    render.render_range(sc, 1, 1, out, "still", percent=5, engine="cycles")
    assert sc.render.engine == scene_mod.eevee_id()  # restored after the Cycles render


def test_estimate_positive():
    sc = _tiny_scene()
    assert render.estimate_seconds(sc, 1, "eevee", 10, percent=5) > 0


def test_presets_and_look_helpers():
    from bsa.blender import look

    sc = _tiny_scene(engine="cycles", preset="final")
    assert sc.cycles.samples == 256 and abs(sc.cycles.adaptive_threshold - 0.01) < 1e-6
    assert sc.render.use_motion_blur and sc.render.resolution_percentage == 5
    scene_mod.apply_preset(sc, "eevee", "final")
    assert sc.eevee.taa_render_samples == 64 and sc.render.use_motion_blur
    cube = bpy.data.objects["Cube"]
    for kind in ("plastic", "metal", "glass", "emissive"):
        look.assign(cube, look.material(f"m_{kind}", (0.8, 0.2, 0.1), kind=kind, bump_scale=0.5))
    assert any(n.bl_idname == "ShaderNodeBump" for n in cube.data.materials[0].node_tree.nodes)
    look.bevel(cube, 0.02)
    look.smooth(cube)


def _shading(obj) -> tuple[float, float]:
    """(largest normal jump in degrees across any edge as rendered, largest tilt of a corner normal on the biggest
    face away from that face's own normal)."""
    import math

    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        corner = {}
        faces_of = {}
        for p in me.polygons:
            for li in p.loop_indices:
                corner[(me.loops[li].vertex_index, p.index)] = me.corner_normals[li].vector.copy()
            for ek in p.edge_keys:
                faces_of.setdefault(ek, []).append(p.index)
        jump = max(
            math.degrees(corner[(v, fs[0])].angle(corner[(v, fs[1])], 0.0))
            for ek, fs in faces_of.items()
            if len(fs) == 2
            for v in ek
        )
        big = max(me.polygons, key=lambda p: p.area)
        tilt = max(math.degrees(me.corner_normals[li].vector.angle(big.normal, 0.0)) for li in big.loop_indices)
        return jump, tilt
    finally:
        ev.to_mesh_clear()


def test_bevel_rounds_smoothly_and_keeps_flat_faces_flat():
    from bsa.blender import look

    for order in ("smooth-then-bevel", "bevel-then-smooth", "bevel-only"):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        if order == "smooth-then-bevel":
            look.smooth(cube, 30)
        look.bevel(cube, 0.05)
        if order == "bevel-then-smooth":
            look.smooth(cube, 30)
        jump, tilt = _shading(cube)
        assert jump < 0.5, (order, jump)  # no crease anywhere: the bevel shades smoothly into the faces
        assert tilt < 0.5, (order, tilt)  # the big flat faces render flat (no gradient from the bevel)


def test_render_still_shows_its_frame_in_a_scene_that_is_not_the_context_scene():
    """The stills command opens the .blend and renders each shot's scene, which is usually not the window's scene."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.name = "Window scene"  # the user's scene, not the factory one a short's scenes replace
    other = bpy.data.scenes.new("Shot scene")
    assert bpy.context.scene != other
    scene_mod.setup_scene(other, _short(), "eevee", "still")
    other.world = bpy.data.worlds.new("dark")
    bpy.ops.mesh.primitive_plane_add(size=2.0)  # lands in the context scene; move it to the shot's scene
    card = bpy.context.object
    bpy.context.scene.collection.objects.unlink(card)
    other.collection.objects.link(card)
    from bsa.blender import look

    look.assign(card, look.material("glow", (1.0, 1.0, 1.0), kind="emissive"))
    card.rotation_euler = (1.5708, 0, 0)  # face the camera
    bake.keyframe_series(card, "location", 0, [-1.5 + 3.0 * i / 9 for i in range(10)])  # left at 1, right at 10
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    other.collection.objects.link(cam)
    cam.location = (0, -6, 0)
    cam.rotation_euler = (1.5708, 0, 0)
    other.camera = cam
    out = Path(tempfile.mkdtemp()) / "f10.jpg"
    stills.render_still(other, 10, out, percent=10)
    img = bpy.data.images.load(str(out))
    w, h = img.size
    px = list(img.pixels)
    lum = [sum(px[(y * w + x) * 4 : (y * w + x) * 4 + 3]) for y in range(h) for x in range(w)]
    left = sum(v for i, v in enumerate(lum) if i % w < w // 2)
    right = sum(v for i, v in enumerate(lum) if i % w >= w // 2)
    assert right > 1.5 * left, (left, right)  # the card is on the right at frame 10, not where frame 1 put it
    assert bpy.context.scene != other  # the window's scene is left as it was


def test_remove_scenes_frees_object_names_for_a_rebuild():
    """A rebuild in an open Blender (MCP mode) must get the same object names back: contact:<name> tags use them."""

    def build():
        sc = bpy.data.scenes.new("Shot scene")
        stand = bpy.data.objects.new("Stand", bpy.data.meshes.new("Stand"))
        sc.collection.objects.link(stand)
        return stand

    bpy.ops.wm.read_factory_settings(use_empty=True)
    keep = bpy.data.objects.new("Keep", None)
    bpy.context.scene.collection.objects.link(keep)
    build()
    scene_mod.remove_scenes(["Shot scene", "Not there"])
    assert "Shot scene" not in bpy.data.scenes
    assert build().name == "Stand"
    assert "Keep" in bpy.data.objects  # objects still used elsewhere stay


def test_remove_scenes_leaves_data_it_did_not_own():
    """In MCP mode the open file is the user's: only what the removed scenes used may go."""
    from bsa.blender import look

    bpy.ops.wm.read_factory_settings(use_empty=True)
    loose_mat = bpy.data.materials.new("User's spare material")  # zero users, not ours
    loose_img = bpy.data.images.new("User's reference", 4, 4)  # zero users, not ours
    shared = look.material("Shared brass", (0.9, 0.6, 0.3))
    kept = bpy.data.scenes.new("Kept")
    other = bpy.data.objects.new("Other", bpy.data.meshes.new("Other"))
    kept.collection.objects.link(other)
    look.assign(other, shared)

    shot = bpy.data.scenes.new("Shot scene")
    stand = bpy.data.objects.new("Stand", bpy.data.meshes.new("Stand mesh"))
    shot.collection.objects.link(stand)
    look.assign(stand, shared)
    own_img = bpy.data.images.new("Shot texture", 4, 4)
    own = look.material("Shot wood", (0.2, 0.1, 0.05))
    own.node_tree.nodes.new("ShaderNodeTexImage").image = own_img
    stand.data.materials.append(own)
    bake.keyframe_series(stand, "location", 0, [0.0, 1.0])
    action = stand.animation_data.action.name

    scene_mod.remove_scenes(["Shot scene"])
    for gone, coll in (("Stand", "objects"), ("Stand mesh", "meshes"), ("Shot wood", "materials"),
                       ("Shot texture", "images"), (action, "actions")):  # fmt: skip
        assert gone not in getattr(bpy.data, coll), (coll, gone)
    assert "User's spare material" in bpy.data.materials and loose_mat.users == 0
    assert "User's reference" in bpy.data.images and loose_img.users == 0
    assert "Shared brass" in bpy.data.materials and "Other" in bpy.data.objects


def _timed_renders(costs):
    """Swap render.bpy / render.time for fakes: each render "takes" the next cost in seconds. Returns (calls, undo)."""
    import types

    clock = {"t": 0.0}
    calls = []
    left = list(costs)

    def fake_render(**kw):
        calls.append(kw)
        clock["t"] += left.pop(0)
        return {"FINISHED"}

    real_bpy, real_time = render.bpy, render.time
    render.bpy = types.SimpleNamespace(app=bpy.app, ops=types.SimpleNamespace(render=types.SimpleNamespace(
        render=fake_render)))  # fmt: skip
    render.time = types.SimpleNamespace(perf_counter=lambda: clock["t"])

    def undo():
        render.bpy, render.time = real_bpy, real_time

    return calls, undo


def test_eevee_estimate_times_a_warm_frame():
    """EEVEE's first render compiles shaders: timing that one cold frame made the estimate about twice the real
    render. The estimate times a second, warm frame and adds the cold start once."""
    sc = _tiny_scene(engine="eevee")
    sc.frame_start, sc.frame_end = 1, 100
    calls, undo = _timed_renders([10.0, 1.0])  # cold 10 s, warm 1 s
    try:
        secs = render.estimate_seconds(sc, 1, "eevee", 100, percent=5)
    finally:
        undo()
    assert len(calls) == 2
    assert abs(secs - (9.0 + 1.0 * 100 * render.MARGIN)) < 1e-6, secs


def test_cycles_estimate_times_one_frame():
    sc = _tiny_scene(engine="cycles")
    calls, undo = _timed_renders([3.0])
    try:
        secs = render.estimate_seconds(sc, 1, "cycles", 10, percent=5)
    finally:
        undo()
    assert len(calls) == 1 and abs(secs - 3.0 * 10 * render.MARGIN) < 1e-6


def test_a_shorts_own_scenes_replace_the_factory_scene():
    """A new scene's camera named "Camera" must not come out "Camera.001" because the factory "Scene" still holds its
    own "Camera": building a short's scenes removes the untouched factory scene and its objects and data."""
    bpy.ops.wm.read_factory_settings()  # "Scene" with Cube, Light and Camera
    scene_mod.remove_scenes(["Pendulum"])  # the first build: nothing of the short's yet
    assert not {"Cube", "Light", "Camera"} & set(bpy.data.objects.keys()), list(bpy.data.objects)
    sc = bpy.data.scenes.new("Pendulum")
    scene_mod.setup_scene(sc, _short(), "eevee", "still")
    assert list(bpy.data.scenes.keys()) == ["Pendulum"], list(bpy.data.scenes)
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    sc.collection.objects.link(cam)
    light = bpy.data.objects.new("Light", bpy.data.lights.new("Light", "POINT"))
    sc.collection.objects.link(light)
    assert (cam.name, cam.data.name, light.name, light.data.name) == ("Camera", "Camera", "Light", "Light")

    bpy.ops.wm.read_factory_settings()  # a scene the user worked in is theirs: kept
    bpy.data.objects.new("Mine", None)
    bpy.context.scene.collection.objects.link(bpy.data.objects["Mine"])
    scene_mod.remove_scenes(["Pendulum"])
    sc = bpy.data.scenes.new("Pendulum")
    scene_mod.setup_scene(sc, _short(), "eevee", "still")
    assert "Scene" in bpy.data.scenes and {"Cube", "Mine"} <= set(bpy.data.objects.keys())
