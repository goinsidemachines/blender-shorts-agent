import math
import os
import tempfile

import bpy

from bsa.blender import loopcheck

N = 48
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


def _key(obj, path, index, fn):
    """A key on every frame 1..N with value fn(frame) (collinear keys: a straight run, a smooth curve)."""
    for f in range(1, N + 1):
        getattr(obj, path)[index] = fn(f)
        obj.keyframe_insert(path, index=index, frame=f)


def _found(sc):
    return loopcheck.loop_findings(sc, 1, N)


def test_a_cube_turning_exactly_once_loops():
    sc = _scene()
    _key(bpy.data.objects["Cube"], "rotation_euler", 2, lambda f: (f - 1) * 2 * math.pi / N)
    assert _found(sc) == []


def test_a_cube_turning_twice_and_a_moving_camera_that_returns_loop():
    sc = _scene()
    _key(bpy.data.objects["Cube"], "rotation_euler", 2, lambda f: (f - 1) * 4 * math.pi / N)
    cam = bpy.data.objects["Cam"]
    _key(cam, "location", 0, lambda f: math.sin(2 * math.pi * (f - 1) / N))
    assert _found(sc) == []


def test_a_cube_sliding_without_returning_is_found():
    sc = _scene()
    _key(bpy.data.objects["Cube"], "location", 0, lambda f: 0.05 * f)
    found = _found(sc)
    assert [(f.check, f.severity, f.obj) for f in found] == [("loop", "warning", "Cube")]
    assert "position" in found[0].message and found[0].frame == N


def test_a_partial_turn_is_found():
    sc = _scene()
    _key(bpy.data.objects["Cube"], "rotation_euler", 2, lambda f: (f - 1) * math.radians(300) / N)
    found = _found(sc)
    assert len(found) == 1 and "rotation" in found[0].message


def test_a_camera_move_that_does_not_return_is_found():
    sc = _scene()
    _key(bpy.data.objects["Cam"], "location", 1, lambda f: -12 + 0.1 * f)
    found = _found(sc)
    assert found and all(f.obj is None and "camera" in f.message for f in found)
    assert any("position" in f.message for f in found)


def test_a_zoom_that_does_not_return_is_found():
    sc = _scene()
    cam = bpy.data.objects["Cam"].data
    for f in range(1, N + 1):
        cam.lens = 35 + 0.2 * f
        cam.keyframe_insert("lens", frame=f)
    found = _found(sc)
    assert any("focal length" in f.message for f in found)


def test_a_shape_key_that_does_not_return_is_found():
    sc = _scene()
    cube = bpy.data.objects["Cube"]
    cube.shape_key_add(name="Basis")
    key = cube.shape_key_add(name="Squash")
    for f in range(1, N + 1):
        key.value = f / (N + 1)
        key.keyframe_insert("value", frame=f)
    found = _found(sc)
    assert any("Squash" in f.message for f in found), found


def test_a_still_scene_and_a_frame_count_too_short_to_measure_pass():
    sc = _scene()
    assert _found(sc) == []
    assert loopcheck.loop_findings(sc, 1, 3) == []
