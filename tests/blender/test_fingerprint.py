"""Inside Blender: per-shot fingerprints change exactly when something that shot renders changes."""

import tempfile
import time
from pathlib import Path

import bpy

from bsa.blender.fingerprint import fingerprints
from bsa.timeline import Span

SPANS = {"A": [Span("s01", 1, 30)], "B": [Span("s02", 31, 60)]}


def _material(name, color):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*color, 1.0)
    return mat


def _camera(sc, name, f0, f1):
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    sc.collection.objects.link(cam)
    cam.rotation_euler = (1.5708, 0, 0)
    cam.location = (0, -8, 0)
    cam.keyframe_insert("location", frame=f0)
    cam.location = (0, -6, 0)
    cam.keyframe_insert("location", frame=f1)
    sc.camera = cam
    return cam


def _two_scenes() -> Path:
    """Scene A (shot s01) and scene B (shot s02): each its own camera, cube and material; a sphere in both shares
    one material. Saved to a temporary .blend, whose path is returned."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    a = bpy.context.scene
    a.name = "A"
    b = bpy.data.scenes.new("B")
    shared = _material("Shared", (0.2, 0.5, 0.8))
    sphere_mesh = bpy.data.meshes.new("Sphere")
    import bmesh

    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=0.5)
    bm.to_mesh(sphere_mesh)
    bm.free()
    sphere_mesh.materials.append(shared)
    for sc, f0, f1 in ((a, 1, 30), (b, 31, 60)):
        sc.world = bpy.data.worlds.new(f"World {sc.name}")
        cube_mesh = bpy.data.meshes.new(f"Cube {sc.name}")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(cube_mesh)
        bm.free()
        cube_mesh.materials.append(_material(f"Only {sc.name}", (0.8, 0.3, 0.1)))
        cube = bpy.data.objects.new(f"Cube {sc.name}", cube_mesh)
        sc.collection.objects.link(cube)
        cube.modifiers.new("Bevel", "BEVEL").width = 0.05
        sphere = bpy.data.objects.new(f"Sphere {sc.name}", sphere_mesh)
        sphere.location = (1.5, 0, 0)
        sc.collection.objects.link(sphere)
        light = bpy.data.objects.new(f"Sun {sc.name}", bpy.data.lights.new(f"Sun {sc.name}", "SUN"))
        sc.collection.objects.link(light)
        _camera(sc, f"Cam {sc.name}", f0, f1)
    path = Path(tempfile.mkdtemp()) / "two.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    return path


def _fp():
    return fingerprints([(bpy.data.scenes[name], spans) for name, spans in SPANS.items()])[0]


def _reload(path):
    bpy.ops.wm.open_mainfile(filepath=str(path))


def test_save_and_reload_without_a_change_keeps_every_fingerprint():
    path = _two_scenes()
    before = _fp()
    assert set(before) == {"s01", "s02"} and before["s01"] != before["s02"]
    _reload(path)
    assert _fp() == before
    bpy.data.scenes["A"].frame_set(17)  # saved at another frame: nothing rendered changed
    bpy.ops.wm.save_mainfile()
    _reload(path)
    assert _fp() == before
    assert _fp() == before  # and computing it twice in one session


def test_a_camera_change_in_shot_2_changes_only_shot_2():
    path = _two_scenes()
    before = _fp()
    _reload(path)
    cam = bpy.data.objects["Cam B"]
    cam.location = (0.5, -6, 0)
    cam.keyframe_insert("location", frame=60)
    bpy.ops.wm.save_mainfile()
    _reload(path)
    after = _fp()
    assert after["s01"] == before["s01"] and after["s02"] != before["s02"]
    bpy.data.cameras["Cam B"].lens = 35.0  # the lens too
    assert _fp()["s02"] != after["s02"]


def _parts():
    out: dict = {}
    fps = fingerprints([(bpy.data.scenes[name], spans) for name, spans in SPANS.items()], out)[0]
    assert fps == _fp()  # recording the parts never changes the fingerprint
    return out


def test_parts_say_which_kind_of_data_changed():
    """A lens change shows in the Camera part only; a moved key in the animation part only."""
    _two_scenes()
    before = _parts()
    assert {"code", "drivers", "animation", "data:Camera", "data:Object", "data:Material"} <= set(before["s02"])
    bpy.data.cameras["Cam B"].lens = 35.0
    after = _parts()
    assert sorted(k for k in after["s02"] if after["s02"][k] != before["s02"][k]) == ["data:Camera"]
    assert after["s01"] == before["s01"]
    cam = bpy.data.objects["Cam B"]
    cam.location = (0.5, -6, 0)
    cam.keyframe_insert("location", frame=60)
    moved = _parts()
    assert sorted(k for k in moved["s02"] if moved["s02"][k] != after["s02"][k]) == ["animation"]


def test_a_tag_changes_the_tags_part_but_not_the_fingerprint():
    """A contact tag changes what the checks find, not what renders: the "tags" part changes, the fingerprint (and so
    the frames) does not."""
    from bsa.blender import bake

    _two_scenes()
    fps, before = _fp(), _parts()
    bake.tag(bpy.data.objects["Cube B"], "contact:Sphere B")
    after = _parts()
    assert _fp() == fps
    assert after["s02"]["tags"] != before["s02"]["tags"] and after["s01"] == before["s01"]


def test_a_material_only_shot_1_uses_changes_only_shot_1():
    _two_scenes()
    before = _fp()
    bsdf = bpy.data.materials["Only A"].node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.11
    after = _fp()
    assert after["s01"] != before["s01"] and after["s02"] == before["s02"]


def test_a_shared_material_changes_both_shots():
    _two_scenes()
    before = _fp()
    tree = bpy.data.materials["Shared"].node_tree
    tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.9, 0.9, 0.1, 1.0)
    after = _fp()
    assert after["s01"] != before["s01"] and after["s02"] != before["s02"]
    tree.links.remove(tree.nodes["Material Output"].inputs["Surface"].links[0])  # a link
    assert _fp()["s01"] != after["s01"]


def test_geometry_modifiers_lights_world_render_settings_and_images_count():
    _two_scenes()
    base = _fp()["s01"]
    changes = []

    def changed(what):
        now = _fp()["s01"]
        changes.append((what, now != base))
        return now

    mesh = bpy.data.meshes["Cube A"]
    mesh.vertices[0].co.x += 0.01
    base = changed("a vertex")
    bpy.data.objects["Cube A"].modifiers["Bevel"].segments = 3
    base = changed("a modifier setting")
    bpy.data.lights["Sun A"].energy = 7.0
    base = changed("a light")
    bpy.data.worlds["World A"].color = (0.1, 0.2, 0.3)
    base = changed("the world")
    bpy.data.scenes["A"].render.resolution_x = 720
    base = changed("render settings")
    bpy.data.scenes["A"].view_settings.look = "AgX - High Contrast"
    base = changed("colour management")
    img = bpy.data.images.new("Tex", 8, 8)
    img.filepath = str(Path(tempfile.mkdtemp()) / "tex.png")
    img.save()
    tex = bpy.data.materials["Only A"].node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    base = changed("an image node")
    Path(img.filepath).write_bytes(Path(img.filepath).read_bytes() + b"\0")  # the file on disk changed
    base = changed("the image file")
    bpy.data.objects["Cube A"].hide_render = True
    base = changed("render visibility")
    text = bpy.data.curves.new("Label", "FONT")
    text.body = "hi"
    bpy.data.scenes["A"].collection.objects.link(bpy.data.objects.new("Label", text))
    base = changed("a new text object")
    text.body = "ho"
    base = changed("the text")
    assert all(ok for _, ok in changes), [w for w, ok in changes if not ok]
    assert _fp()["s02"] == _fp()["s02"]


def test_a_key_inside_one_shot_of_a_shared_scene_changes_only_that_shot():
    """Two shots in one scene: a key moved inside shot 2 (linear keys at the cut) leaves shot 1 alone."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    cam = _camera(sc, "Cam", 1, 20)
    cam.location = (0, -5, 0)
    cam.keyframe_insert("location", frame=40)
    from bsa.blender.bake import _fcurves

    for fc in _fcurves(cam.animation_data.action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    bpy.ops.mesh.primitive_cube_add()
    spans = [Span("s01", 1, 20), Span("s02", 21, 40)]
    before = fingerprints([(sc, spans)])[0]
    for fc in _fcurves(cam.animation_data.action):
        if fc.data_path == "location" and fc.array_index == 1:
            fc.keyframe_points[-1].co.y = -4.0  # the key at frame 40
            fc.update()
    after = fingerprints([(sc, spans)])[0]
    assert after["s01"] == before["s01"] and after["s02"] != before["s02"]
    obj = bpy.context.object
    obj.keyframe_insert("hide_render", frame=1)
    obj.hide_render = True
    obj.keyframe_insert("hide_render", frame=30)  # hidden from frame 30 on: shot 2 only
    later = fingerprints([(sc, spans)])[0]
    assert later["s02"] != after["s02"]


def test_animated_material_values_are_sampled_per_frame():
    _two_scenes()
    before = _fp()
    tree = bpy.data.materials["Only B"].node_tree
    socket = tree.nodes["Principled BSDF"].inputs["Roughness"]
    socket.default_value = 0.2
    socket.keyframe_insert("default_value", frame=31)
    socket.default_value = 0.9
    socket.keyframe_insert("default_value", frame=60)
    after = _fp()
    assert after["s02"] != before["s02"] and after["s01"] == before["s01"]
    socket.default_value = 0.3  # the current (animated) value is not what counts: the keys are
    assert _fp() == after


def test_fingerprints_are_quick():
    path = _two_scenes()
    _reload(path)
    import bmesh

    for i in range(40):  # a busier scene: 40 more animated objects with modifiers and materials
        mesh = bpy.data.meshes.new(f"M{i}")
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.3)
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.append(_material(f"Mat{i}", (i / 40, 0.5, 0.5)))
        obj = bpy.data.objects.new(f"O{i}", mesh)
        obj.modifiers.new("Sub", "SUBSURF")
        bpy.data.scenes["A"].collection.objects.link(obj)
        obj.location = (i % 8, i // 8, 0)
        obj.keyframe_insert("location", frame=1)
        obj.location.z = 1
        obj.keyframe_insert("location", frame=30)
    global SPANS
    saved = SPANS
    SPANS = {"A": [Span("s01", 1, 150), Span("s01b", 151, 300)], "B": [Span("s02", 31, 60)]}
    try:
        t0 = time.perf_counter()
        _fp()
        took = time.perf_counter() - t0
    finally:
        SPANS = saved
    print(f"fingerprints of 330 frames, 50 objects: {took:.2f} s")
    assert took < 20


def test_a_mesh_stored_in_another_order_is_the_same_mesh():
    """A rebuild can store the same faces and edges in another order (bmesh ops do): it renders the same."""
    import hashlib

    from bsa.blender.fingerprint import _mesh_digest

    verts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1)]
    faces = [(0, 1, 2, 3), (0, 1, 5, 4), (1, 2, 5)]

    def digest(fs):
        me = bpy.data.meshes.new("M")
        me.from_pydata(verts, [], fs)
        me.update()
        h = hashlib.sha256()
        _mesh_digest(me, h)
        return h.hexdigest()

    one = digest(faces)
    assert digest([faces[2], faces[0], faces[1]]) == one  # faces reordered
    assert digest([(2, 3, 0, 1), (5, 4, 0, 1), (5, 1, 2)]) == one  # each face starts elsewhere, same winding
    assert digest([(3, 2, 1, 0), (0, 1, 5, 4), (1, 2, 5)]) != one  # a face flipped: renders differently
