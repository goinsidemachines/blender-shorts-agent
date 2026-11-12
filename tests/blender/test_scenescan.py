import os
import tempfile
from pathlib import Path

import bpy

from bsa import heavy
from bsa.blender import scenescan

os.environ["BSA_HOME"] = tempfile.mkdtemp()


def _fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def _cube(name, location=(0, 0, 0), size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _boolean(wall, cutter):
    mod = wall.modifiers.new("Cut", "BOOLEAN")
    mod.object = cutter
    cutter.hide_render = True


def _kinds(found):
    return [f["kind"] for f in found]


def test_boolean_on_an_animated_object_is_warned():
    _fresh()
    wall, cutter = _cube("Wall", size=2.0), _cube("Cutter", (0, -1, 0), 0.5)
    _boolean(wall, cutter)
    wall.keyframe_insert("location", frame=1)
    wall.location.x = 1
    wall.keyframe_insert("location", frame=10)
    found = [f for f in scenescan.scan(bpy.data) if f["kind"] == "boolean"]
    assert len(found) == 1 and "Wall" in found[0]["message"] and "animated object" in found[0]["message"], found


def test_boolean_on_an_animated_parent_is_warned():
    _fresh()
    spin, wall, cutter = _cube("Spin"), _cube("Wall", size=2.0), _cube("Cutter", (0, -1, 0), 0.5)
    wall.parent = spin
    _boolean(wall, cutter)
    spin.rotation_euler = (0, 0, 0)
    spin.keyframe_insert("rotation_euler", frame=1)
    spin.rotation_euler = (0, 0, 3)
    spin.keyframe_insert("rotation_euler", frame=10)
    found = [f for f in scenescan.scan(bpy.data) if f["kind"] == "boolean"]
    assert len(found) == 1 and "animated parent (Spin)" in found[0]["message"], found


def test_boolean_on_an_animated_operand_is_warned():
    _fresh()
    wall, cutter = _cube("Wall", size=2.0), _cube("Cutter", (0, -1, 0), 0.5)
    _boolean(wall, cutter)
    cutter.keyframe_insert("location", frame=1)
    cutter.location.x = 1
    cutter.keyframe_insert("location", frame=10)
    found = [f for f in scenescan.scan(bpy.data) if f["kind"] == "boolean"]
    assert len(found) == 1 and "animated operand (Cutter)" in found[0]["message"], found


def test_boolean_on_a_static_object_is_not_warned():
    _fresh()
    wall, cutter = _cube("Wall", size=2.0), _cube("Cutter", (0, -1, 0), 0.5)
    _boolean(wall, cutter)
    assert "boolean" not in _kinds(scenescan.scan(bpy.data))


def _trees(n, shared):
    mesh = bpy.data.objects["Seed"].data
    sc = bpy.context.scene
    for i in range(n):
        obj = bpy.data.objects.new(f"Tree.{i:03d}", mesh if shared else mesh.copy())
        obj.location = (i, 0, 0)
        sc.collection.objects.link(obj)


def test_many_copied_meshes_are_warned():
    _fresh()
    _cube("Seed")
    _trees(heavy.COPY_LIMIT, shared=False)
    found = [f for f in scenescan.scan(bpy.data) if f["kind"] == "copies"]
    assert len(found) == 1 and "shared_mesh" in found[0]["message"], found


def test_objects_sharing_one_mesh_are_not_warned():
    _fresh()
    _cube("Seed")
    _trees(heavy.COPY_LIMIT * 2, shared=True)
    assert "copies" not in _kinds(scenescan.scan(bpy.data))


def test_different_shapes_are_not_copies():
    _fresh()
    for i in range(heavy.COPY_LIMIT + 5):
        _cube(f"Box{i}", size=1.0 + i * 0.1)
    assert "copies" not in _kinds(scenescan.scan(bpy.data))


def test_big_blend_is_warned():
    _fresh()
    path = Path(tempfile.mkdtemp()) / "short.blend"
    with open(path, "wb") as f:
        f.truncate((heavy.BIG_BLEND_MB + 1) * 1024 * 1024)  # sparse: no disk used
    found = [f for f in scenescan.scan(bpy.data, path) if f["kind"] == "size"]
    assert len(found) == 1 and "many copied meshes" in found[0]["message"], found
    path.write_bytes(b"small")
    assert "size" not in _kinds(scenescan.scan(bpy.data, path))


def test_freestyle_over_a_long_range_is_info():
    sc = _fresh()
    sc.render.use_freestyle = True
    sc.frame_start, sc.frame_end = 1, heavy.FREESTYLE_LONG_FRAMES + 100
    found = [f for f in scenescan.scan(bpy.data) if f["kind"] == "freestyle"]
    assert len(found) == 1 and found[0]["severity"] == "info" and "chunk_frames" in found[0]["message"], found
    sc.frame_end = 50
    assert "freestyle" not in _kinds(scenescan.scan(bpy.data))
