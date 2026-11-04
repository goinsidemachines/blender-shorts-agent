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
        fingerprints([(bpy.data.scenes[name], spans) for name, spans in SPANS.items()], objects={})  # as the CLI does
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



def _keyed_scene():
    """Two cubes A and B (each its own material) and a camera, one shot of 30 frames."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    import bmesh

    for name, x in (("A", -1.5), ("B", 1.5)):
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.append(_material(f"Mat {name}", (0.8, 0.3, 0.1)))
        obj = bpy.data.objects.new(name, mesh)
        obj.location = (x, 0, 0)
        sc.collection.objects.link(obj)
    _camera(sc, "Cam", 1, 30)
    return sc


def _keys(sc):
    spans = [Span("s01", 1, 30)]
    objects: dict = {}
    fps = fingerprints([(sc, list(spans))], objects=objects)[0]
    assert fps == fingerprints([(sc, list(spans))])[0]
    return objects


def _changed(before, after, shot="s01"):
    b, a = before[shot], after[shot]
    out = sorted(n for n in set(b["objects"]) | set(a["objects"]) if b["objects"].get(n) != a["objects"].get(n))
    return out + (["rest"] if b["rest"] != a["rest"] else [])


def test_object_keys_change_only_for_the_object_that_changed():
    sc = _keyed_scene()
    base = _keys(sc)
    assert set(base["s01"]["objects"]) == {"A", "B"} and base["s01"]["rest"]
    assert all(len(k) == 16 for k in base["s01"]["objects"].values())
    a = bpy.data.objects["A"]
    a.keyframe_insert("location", frame=1)
    a.location.z = 1.0
    a.keyframe_insert("location", frame=5)
    moved = _keys(sc)
    assert _changed(base, moved) == ["A"]
    bpy.data.materials["Mat B"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0, 1, 0, 1)
    coloured = _keys(sc)
    assert _changed(moved, coloured) == ["B"]
    cam = bpy.data.objects["Cam"]
    cam.location = (0.5, -6, 0)
    cam.keyframe_insert("location", frame=30)
    panned = _keys(sc)
    assert _changed(coloured, panned) == ["rest"]
    a["bsa_tags"] = "fast"
    tagged = _keys(sc)
    assert _changed(panned, tagged) == ["A"]


def test_a_parent_change_changes_the_childs_key():
    sc = _keyed_scene()
    empty = bpy.data.objects.new("E", None)
    sc.collection.objects.link(empty)
    c = bpy.data.objects.new("C", bpy.data.objects["A"].data.copy())
    sc.collection.objects.link(c)
    c.parent = empty
    base = _keys(sc)
    empty.keyframe_insert("location", frame=1)
    empty.location.x = 2.0
    empty.keyframe_insert("location", frame=10)
    after = _keys(sc)
    assert _changed(base, after) == ["C", "rest"]


def test_object_keys_do_not_change_the_fingerprints():
    _two_scenes()
    groups = [(bpy.data.scenes[name], spans) for name, spans in SPANS.items()]
    objects: dict = {}
    assert fingerprints(groups) == fingerprints(groups, objects=objects)
    assert set(objects) == {"s01", "s02"}
    assert set(objects["s02"]["objects"]) == {"Cube B", "Sphere B"}
    sc = bpy.context.scene  # one scene, two shots, a hide_render key: the same with and without the keys
    spans = [Span("s01", 1, 15), Span("s01b", 16, 30)]
    obj = bpy.data.objects["Cube A"]
    obj.keyframe_insert("hide_render", frame=1)
    obj.hide_render = True
    obj.keyframe_insert("hide_render", frame=20)
    assert fingerprints([(sc, spans)]) == fingerprints([(sc, spans)], objects={})



def _move(obj, frame=10):
    obj.keyframe_insert("location", frame=1)
    obj.location.z += 1.0
    obj.keyframe_insert("location", frame=frame)


def test_a_boolean_cutters_change_changes_the_cut_objects_key():
    """A live Boolean on A cuts with B: moving B or editing B's mesh changes A's evaluated mesh, so A's key; B (it
    depends on nothing) keeps its key when only A changes."""
    sc = _keyed_scene()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    a.modifiers.new("Cut", "BOOLEAN").object = b
    base = _keys(sc)
    _move(b)
    moved = _keys(sc)
    assert _changed(base, moved) == ["A", "B"]
    b.data.vertices[0].co.x += 0.2
    edited = _keys(sc)
    assert _changed(moved, edited) == ["A", "B"]
    bpy.data.materials["Mat A"].node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.13
    assert _changed(edited, _keys(sc)) == ["A"]


class _Nodes:
    """A geometry nodes modifier on `obj` joining its geometry with an Object Info input's, plus a float and a bool
    input; .set(name, value) sets one of the modifier's inputs ("Target", "Amount", "Flag")."""

    def __init__(self, obj):
        tree = bpy.data.node_groups.new("Join", "GeometryNodeTree")
        tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        self.ids = {name: tree.interface.new_socket(name, in_out="INPUT", socket_type=kind).identifier
                    for name, kind in (("Target", "NodeSocketObject"), ("Amount", "NodeSocketFloat"),
                                       ("Flag", "NodeSocketBool"))}  # fmt: skip
        tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        nodes = tree.nodes
        group_in, group_out = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
        info, join = nodes.new("GeometryNodeObjectInfo"), nodes.new("GeometryNodeJoinGeometry")
        tree.links.new(group_in.outputs["Target"], info.inputs["Object"])
        tree.links.new(group_in.outputs["Geometry"], join.inputs[0])
        tree.links.new(info.outputs["Geometry"], join.inputs[0])
        tree.links.new(join.outputs[0], group_out.inputs[0])
        self.mod = obj.modifiers.new("Join", "NODES")
        self.mod.node_group = tree

    def item(self, name):
        inputs = getattr(getattr(self.mod, "properties", None), "inputs", None)
        return None if inputs is None else getattr(inputs, self.ids[name])

    def set(self, name, value):
        item = self.item(name)
        if item is not None:  # Blender 5
            item.value = value
        else:
            self.mod[self.ids[name]] = value


def test_a_geometry_nodes_object_input_and_a_hook_are_dependencies():
    sc = _keyed_scene()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    gn = _Nodes(a)
    gn.set("Target", b)
    base = _keys(sc)
    _move(b)
    moved = _keys(sc)
    assert _changed(base, moved) == ["A", "B"]
    gn.set("Amount", 0.5)  # a plain input value
    assert _changed(moved, _keys(sc)) == ["A"]

    sc = _keyed_scene()  # a hook on A pulled by B
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    a.modifiers.new("Hook", "HOOK").object = b
    base = _keys(sc)
    _move(b)
    assert _changed(base, _keys(sc)) == ["A", "B"]


def test_a_dependency_cycle_ends_and_its_keys_are_stable():
    sc = _keyed_scene()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    a.modifiers.new("Cut", "BOOLEAN").object = b
    b.modifiers.new("Cut", "BOOLEAN").object = a
    base = _keys(sc)
    assert _keys(sc) == base
    _move(b)
    assert _changed(base, _keys(sc)) == ["A", "B"]



def _golden_scene():
    """_two_scenes plus what the per-frame loop treats specially: a WAVE modifier (geometry sampled per frame), an
    animated material value and a keyed hide_render (channels), a parented object."""
    _two_scenes()
    a = bpy.data.objects["Cube A"]
    a.modifiers.new("Wave", "WAVE")
    socket = bpy.data.materials["Only A"].node_tree.nodes["Principled BSDF"].inputs["Roughness"]
    socket.keyframe_insert("default_value", frame=1)
    socket.default_value = 0.9
    socket.keyframe_insert("default_value", frame=30)
    sphere = bpy.data.objects["Sphere A"]
    sphere.keyframe_insert("hide_render", frame=1)
    sphere.hide_render = True
    sphere.keyframe_insert("hide_render", frame=20)
    sphere.parent = a


# fingerprints of _golden_scene computed by the code before the object keys (6f0dac3), per Blender version, with the
# render presets' code fixed (_FIXED_RENDER_CODE) so an edit to scene.py does not move them
GOLDEN = {"5.2.0 LTS": {"s01": "91029611177095de0537490848f1ff1b", "s02": "60b25b50e883733dd2cc2e765ec06d8b"}}
_FIXED_RENDER_CODE = b"render presets: fixed for the golden test"


def _golden_fingerprints():
    from bsa.blender import fingerprint

    path = Path(tempfile.mkdtemp()) / "scene.py"
    path.write_bytes(_FIXED_RENDER_CODE)
    saved, fingerprint._RENDER_CODE = fingerprint._RENDER_CODE, path
    try:
        groups = [(bpy.data.scenes[name], spans) for name, spans in SPANS.items()]
        got = fingerprints(groups)[0]
        assert fingerprints(groups, objects={})[0] == got
    finally:
        fingerprint._RENDER_CODE = saved
    return got


def _golden_gn_scene():
    """_golden_scene with a geometry nodes modifier on Cube A (scene A only) whose inputs are set."""
    _golden_scene()
    gn = _Nodes(bpy.data.objects["Cube A"])
    gn.set("Amount", 0.5)
    gn.set("Flag", True)
    gn.set("Target", bpy.data.objects["Sun A"])  # not Sphere A: a child of Cube A, read by it, is a cycle


# _golden_gn_scene's fingerprints by the code before (6f0dac3, which did not see the inputs) and now
GOLDEN_GN = {
    "5.2.0 LTS": {"before": {"s01": "1b8d8ecad762908af5cfd5fc3f10324f", "s02": "60b25b50e883733dd2cc2e765ec06d8b"},
                  "now": {"s01": "8a46b0a085558ffb7a53f1525eced76b", "s02": "60b25b50e883733dd2cc2e765ec06d8b"}},
}


def test_fingerprints_are_byte_identical_to_before():
    want = GOLDEN.get(bpy.app.version_string)
    _golden_scene()
    got = _golden_fingerprints()
    if want is None:  # another Blender: its RNA differs, so its fingerprints do too
        print(f"no golden fingerprints for Blender {bpy.app.version_string}: {got}")
        return
    assert got == want



def test_geometry_nodes_inputs_change_the_fingerprint():
    """A float, a bool and an object input of a geometry nodes modifier each change the shot's fingerprint and the
    object's key (Blender 5 keeps them where the generic walk does not look)."""
    sc = _keyed_scene()
    gn = _Nodes(bpy.data.objects["A"])
    spans = [Span("s01", 1, 30)]

    def fp():
        return fingerprints([(sc, spans)])[0]["s01"]

    seen = [fp()]
    keys = [_keys(sc)]
    for name, value in (("Amount", 0.5), ("Flag", True), ("Target", bpy.data.objects["B"])):
        gn.set(name, value)
        seen.append(fp())
        keys.append(_keys(sc))
        assert seen[-1] != seen[-2], name
        assert "A" in _changed(keys[-2], keys[-1]), name
    assert fp() == seen[-1]  # and it is stable


def test_an_animated_geometry_nodes_input_is_sampled_per_frame():
    sc = _keyed_scene()
    gn = _Nodes(bpy.data.objects["A"])
    item = gn.item("Amount")
    if item is None:  # before Blender 5 the inputs are ID properties, keyed through the modifier
        return
    spans = [Span("s01", 1, 15), Span("s02", 16, 30)]
    item.value = 0.1
    item.keyframe_insert("value", frame=1)
    item.value = 0.2
    item.keyframe_insert("value", frame=10)
    before = fingerprints([(sc, spans)])[0]
    item.value = 0.7  # the current (animated) value is not what counts: the keys are
    assert fingerprints([(sc, spans)])[0] == before
    from bsa.blender.bake import _fcurves

    fc = next(f for f in _fcurves(bpy.data.objects["A"].animation_data.action) if "Socket" in f.data_path)
    fc.keyframe_points[-1].co.y = 0.9  # the key at frame 10: shot 1 only (constant after it)
    fc.update()
    after = fingerprints([(sc, spans)])[0]
    assert after["s01"] != before["s01"]


def test_geometry_nodes_inputs_now_count_and_only_where_they_are():
    """A shot whose scene has geometry nodes inputs renders once again after the update (its fingerprint differs from
    6f0dac3's); a shot whose scene has none keeps its fingerprint."""
    want = GOLDEN_GN.get(bpy.app.version_string)
    _golden_gn_scene()
    got = _golden_fingerprints()
    if want is None:
        print(f"no golden geometry nodes fingerprints for Blender {bpy.app.version_string}: {got}")
        return
    assert got == want["now"]
    assert got["s01"] != want["before"]["s01"] and got["s02"] == want["before"]["s02"]
