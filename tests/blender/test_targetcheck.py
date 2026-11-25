import math
import os
import tempfile

import bpy

from bsa.blender import targetcheck

N = 24
os.environ["BSA_HOME"] = tempfile.mkdtemp()


def _scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, N
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.location = (0, -12, 1.5)
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = cam
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
    bpy.context.object.name = "Cube"
    return sc


def _key(obj, path, index, fn, frames=range(1, N + 1)):
    for f in frames:
        getattr(obj, path)[index] = fn(f)
        obj.keyframe_insert(path, index=index, frame=f)


def test_a_static_scene_is_not_moving():
    sc = _scene()
    print("static scene moving:", targetcheck.moving_at_start(sc, 1))
    assert targetcheck.moving_at_start(sc, 1) == []


def test_a_moving_cube_is_found_by_name():
    sc = _scene()
    _key(bpy.data.objects["Cube"], "location", 0, lambda f: 0.1 * f)
    moving = targetcheck.moving_at_start(sc, 1)
    print("moving cube:", moving)
    assert moving == ["Cube"]
    assert sc.frame_current == 1  # put back


def test_a_moving_camera_is_found():
    sc = _scene()
    _key(bpy.data.objects["Cam"], "location", 0, lambda f: 0.05 * f)
    assert targetcheck.moving_at_start(sc, 1) == ["camera"]


def test_a_cube_that_starts_moving_later_is_still_at_frame_1():
    sc = _scene()
    cube = bpy.data.objects["Cube"]
    _key(cube, "location", 0, lambda f: 0.0 if f <= 6 else 0.1 * (f - 6))
    assert targetcheck.moving_at_start(sc, 1) == []
    assert targetcheck.moving_at_start(sc, 6) == ["Cube"]  # a window starting at frame 6 does move


def test_a_parent_that_moves_carries_its_mesh():
    sc = _scene()
    empty = bpy.data.objects.new("Rig", None)
    sc.collection.objects.link(empty)
    cube = bpy.data.objects["Cube"]
    cube.parent = empty
    _key(empty, "location", 1, lambda f: 0.1 * f)
    assert targetcheck.moving_at_start(sc, 1) == ["Cube"]


def test_a_hidden_moving_cube_is_not_something_visible():
    sc = _scene()
    cube = bpy.data.objects["Cube"]
    _key(cube, "location", 0, lambda f: 0.1 * f)
    cube.hide_render = True
    assert targetcheck.moving_at_start(sc, 1) == []


def test_an_animated_shape_key_counts():
    sc = _scene()
    cube = bpy.data.objects["Cube"]
    cube.shape_key_add(name="Basis")
    key = cube.shape_key_add(name="Squash")
    for f in range(1, N + 1):
        key.value = f / (N + 1)
        key.keyframe_insert("value", frame=f)
    assert targetcheck.moving_at_start(sc, 1) == ["Cube shape keys"]
