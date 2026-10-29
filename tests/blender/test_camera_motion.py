import math

import bpy
import numpy as np
from mathutils import Vector

from bsa.blender import bake, camera, extremes, motion
from bsa.layout import H, W, caption_band, frame_area


def _scene(cam_type="PERSP"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 30
    sc.render.resolution_x, sc.render.resolution_y = W, H
    data = bpy.data.cameras.new("Cam")
    data.type = cam_type
    cam = bpy.data.objects.new("Cam", data)
    sc.collection.objects.link(cam)
    cam.location = (1.0, -3.0, 2.0)  # too close and off to one side
    cam.rotation_euler = (math.radians(80), 0, math.radians(5))
    sc.camera = cam
    return sc, cam


def _box(name, size, location):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    return obj


def _projected(sc, cam, objs, frames=(None,)):
    from bpy_extras.object_utils import world_to_camera_view

    xs, ys = [], []
    for frame in frames:
        if frame is not None:
            sc.frame_set(frame)
        bpy.context.view_layer.update()
        for obj in objs:
            for c in obj.bound_box:
                p = world_to_camera_view(sc, cam, obj.matrix_world @ Vector(c))
                xs.append(p.x * W)
                ys.append((1 - p.y) * H)
    return min(xs), min(ys), max(xs), max(ys)


def test_frame_objects_fills_the_safe_area_above_the_captions():
    sc, cam = _scene()
    lever = _box("Lever", (5.0, 0.3, 0.4), (0, 0, 1))  # 5 m wide: the width limits in portrait
    out = camera.frame_objects(cam, [lever])
    area = frame_area()
    x0, y0, x1, y1 = _projected(sc, cam, [lever])
    assert x0 >= area.x - 1 and x1 <= area.right + 1 and y0 >= area.y - 1 and y1 <= area.bottom + 1, (x0, y0, x1, y1)
    assert abs((x1 - x0) - area.w) < 2, (x1 - x0, area.w)  # fills the width
    assert y1 < caption_band().y  # clear of the captions
    assert abs((x0 + x1) / 2 - (area.x + area.w / 2)) < 2 and abs((y0 + y1) / 2 - (area.y + area.h / 2)) < 2
    assert np.allclose(out["box"], (x0, y0, x1, y1), atol=1)
    assert math.isclose(cam.rotation_euler[0], math.radians(80), abs_tol=1e-6)  # the angle is kept


def test_frame_objects_over_the_frames_of_a_motion_and_aim():
    sc, cam = _scene()
    tower = _box("Tower", (0.6, 0.6, 3.0), (0, 0, 1.5))  # tall: the height limits
    ball = _box("Ball", (0.3, 0.3, 0.3), (0, 0, 3.2))
    bake.keyframe_series(ball, "location", 0, [2.0 * math.sin(i / 5) for i in range(30)])
    frames = list(range(1, 31))
    camera.frame_objects(cam, [tower, ball], frames=frames, aim=True, margin=40)
    area = frame_area(margin=40)
    x0, y0, x1, y1 = _projected(sc, cam, [tower, ball], frames)
    assert x0 >= area.x - 1 and x1 <= area.right + 1 and y0 >= area.y - 1 and y1 <= area.bottom + 1, (x0, y0, x1, y1)
    assert abs((x1 - x0) - area.w) < 2 or abs((y1 - y0) - area.h) < 2


def test_frame_objects_with_an_orthographic_camera_and_no_band():
    sc, cam = _scene("ORTHO")
    cam.rotation_euler = (math.radians(90), 0, 0)
    plate = _box("Plate", (2.0, 0.1, 1.0), (0.5, 0, 0.5))
    camera.frame_objects(cam, [plate], avoid=None, margin=0)
    s = frame_area(avoid=None, margin=0)
    x0, y0, x1, y1 = _projected(sc, cam, [plate])
    assert abs((x1 - x0) - s.w) < 2 and y0 >= s.y - 1 and y1 <= s.bottom + 1


def test_moving_frames_follow_keyed_heroes_and_the_camera():
    sc, cam = _scene()
    hero = _box("Hero", (1, 1, 1), (0, 0, 0))
    bake.tag(hero, "hero")
    _box("Scenery", (1, 1, 1), (3, 0, 0))
    xs = [0.0] * 5 + [0.1 * i for i in range(10)] + [0.9] * 15  # moves from frame 6 to 15, then rests
    bake.keyframe_series(hero, "location", 0, xs)
    assert motion.moving_frames(sc, 1, 30, "s01") == set(range(7, 16))
    bake.keyframe_series(cam, "location", 2, [2.0 + 0.01 * i for i in range(30)])  # a slow camera move
    assert motion.moving_frames(sc, 1, 30, "s01") == set(range(2, 31))
    assert sc.frame_current == 1


def test_frame_objects_on_an_animated_camera_keeps_its_pose_and_keys():
    """An animated camera: framing starts from the pose it has now (an unkeyed change too), sampling other frames
    does not snap it to its keys, the scene keeps its frame, and key=FRAME keys each framed position, so several
    positions are framed on one camera without clearing its animation."""
    sc, cam = _scene()
    left = _box("Left", (0.5, 0.5, 0.5), (-2, 0, 1))
    right = _box("Right", (0.5, 0.5, 0.5), (2, 0, 1))
    cam.location = (0.0, -9.0, 1.0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    for f in (1, 30):
        cam.keyframe_insert("location", frame=f)
        cam.keyframe_insert("rotation_euler", frame=f)
    sc.frame_set(12)
    cam.rotation_euler = (math.radians(90), 0, math.radians(20))  # unkeyed: the angle to frame with
    out = camera.frame_objects(cam, [left], frames=[1, 12, 30])
    assert sc.frame_current == 12
    assert math.isclose(cam.rotation_euler[2], math.radians(20), abs_tol=1e-6)  # not snapped back to the keys
    assert np.allclose(cam.location, out["location"], atol=1e-6)
    first = camera.frame_objects(cam, [left], key=10)
    second = camera.frame_objects(cam, [right], key=20)
    keyed = {kp.co[0] for fc in extremes.fcurves(cam) if fc.data_path == "location"
             for kp in fc.keyframe_points}  # fmt: skip
    assert keyed == {1.0, 10.0, 20.0, 30.0}
    sc.frame_set(10)
    assert np.allclose(cam.matrix_world.translation, first["location"], atol=1e-5)
    sc.frame_set(20)
    assert np.allclose(cam.matrix_world.translation, second["location"], atol=1e-5)
    sc.frame_set(1)
    assert np.allclose(cam.location, (0.0, -9.0, 1.0), atol=1e-6)  # the existing keys are untouched


def test_frame_objects_refuses_a_box_with_no_size():
    sc, cam = _scene()
    empty = bpy.data.objects.new("NoGeometry", bpy.data.meshes.new("NoGeometry"))
    sc.collection.objects.link(empty)
    try:
        camera.frame_objects(cam, [empty])
    except ValueError as exc:
        assert "NoGeometry" in str(exc) and "no size" in str(exc), exc
    else:
        raise AssertionError("a box with no size was framed")


def test_frame_objects_warns_when_the_answer_is_absurd():
    """Parts far from where the camera looks (their parent turned them elsewhere): the camera is sent far off. The
    result says so, and so does a Python warning."""
    import warnings

    _sc, cam = _scene()
    lever = _box("Lever", (5.0, 0.3, 0.4), (0, 0, 1))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = camera.frame_objects(cam, [lever])
    assert out["warnings"] == [] and not caught, (out["warnings"], caught)
    cam.location, cam.rotation_euler = (1.0, -3.0, 2.0), (math.radians(80), 0, math.radians(5))
    nut = _box("Nut", (0.02, 0.02, 0.02), (60, 25, -10))  # 2 cm, far off to the side and behind
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = camera.frame_objects(cam, [nut])
    assert out["warnings"] and any("Nut" in w for w in out["warnings"]), out
    assert any("moved" in w and "m" in w for w in out["warnings"]), out["warnings"]
    assert caught and issubclass(caught[0].category, camera.FramingWarning), caught


def _second_scene():
    """Scene "Tank" (the window's) and scene "Wheel", built after it as script mode does: a cube parented to an empty
    at z=12 with a driven gondola beside it, and a camera linked to both scenes (Tank first)."""
    import bmesh

    sc, cam = _scene()
    sc.name = "Tank"
    wheel = bpy.data.scenes.new("Wheel")
    wheel.render.resolution_x, wheel.render.resolution_y = W, H
    rig = bpy.data.objects.new("Rig", None)
    wheel.collection.objects.link(rig)
    rig.location = (0, 0, 12)
    me = bpy.data.meshes.new("Arm")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(me)
    bm.free()
    arm = bpy.data.objects.new("Arm", me)
    wheel.collection.objects.link(arm)
    arm.parent = rig
    gondola = bpy.data.objects.new("Gondola", me)
    wheel.collection.objects.link(gondola)
    gondola.parent, gondola.location = rig, (4, 0, 0)
    drv = gondola.driver_add("rotation_euler", 1).driver
    var = drv.variables.new()
    var.targets[0].id, var.targets[0].data_path = rig, "rotation_euler[1]"
    drv.expression = "-var"
    wheel.collection.objects.link(cam)
    cam.location, cam.rotation_euler = (2, -30, 12), (math.radians(90), 0, 0)
    return sc, wheel, cam, [arm, gondola]


def test_frame_objects_measures_parented_objects_in_a_scene_that_is_not_the_windows():
    """Script mode builds each scene in turn: the objects are measured where the scene they are in places them (the
    parent lifts them to z=12), never at their own unevaluated place, whatever scene the window shows."""
    tank, wheel, cam, objs = _second_scene()
    for frames in (None, [1, 10]):
        pts = camera._corners(objs, wheel, frames or [None])
        assert abs(pts[:, 2].min() - 11) < 1e-3 and abs(pts[:, 2].max() - 13) < 1e-3, (frames, pts[:, 2])
    camera.frame_objects(cam, objs)  # the camera is in both scenes: the one holding the objects is used
    got = np.array(cam.location)
    cam.location, cam.rotation_euler = (2, -30, 12), (math.radians(90), 0, 0)
    camera.frame_box(cam, (-1, -1, 11), (5, 1, 13), scene=wheel)  # the same box, given in the world
    assert np.allclose(got, cam.location, atol=1e-3), (got, tuple(cam.location))
    try:
        camera.frame_objects(cam, objs, scene=tank)
    except ValueError as e:
        assert "'Arm'" in str(e) and "'Tank'" in str(e), e
    else:
        raise AssertionError("objects that scene 'Tank' does not have were measured anyway")


def test_framing_warns_when_the_camera_ends_up_under_the_ground():
    """Aimed up at a tall wheel from low down, centring the wheel above the caption band slides the camera down along
    its own up axis, under the ground: the box is in view, seen from under the terrain. The result says so."""
    import warnings

    from mathutils import Vector

    _sc, cam = _scene()
    _box("Ground", (400.0, 400.0, 6.9), (0, 0, -2.65))  # a closed bank, its top at z=0.8
    wheel = _box("Wheel", (35.0, 12.0, 35.0), (0, 0, 12))

    def aim(pos, target):
        cam.location = Vector(pos)
        cam.rotation_euler = (Vector(target) - Vector(pos)).to_track_quat("-Z", "Y").to_euler()

    aim((16, 58, 4), (0, 0, 8))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = camera.frame_objects(cam, [wheel])
    assert cam.location.z < 0.8, tuple(cam.location)
    under = [w for w in out["warnings"] if "under" in w and "'Ground'" in w]
    assert under and caught, out["warnings"]
    aim((16, 58, 30), (0, 0, 12))  # from above the wheel's centre: centring keeps it above the bank
    out = camera.frame_objects(cam, [wheel])
    assert cam.location.z > 0.8 and not [w for w in out["warnings"] if "under" in w], (tuple(cam.location), out)
    out = camera.frame_box(cam, (-17.5, -6, -5.5), (17.5, 6, 29.5))
    assert not [w for w in out["warnings"] if "under" in w], out


def test_frame_box_frames_a_region_of_one_long_mesh():
    """frame_box: a part of one long mesh (in its own coordinates, following it) without a helper object."""
    sc, cam = _scene()
    rail = _box("Rail", (10.0, 0.2, 0.2), (0, 0, 1))  # 10 m long; frame only its last metre
    rail.rotation_euler = (0, 0, math.radians(30))
    bpy.context.view_layer.update()
    out = camera.frame_box(cam, (0.4, -0.5, -0.5), (0.5, 0.5, 0.5), of=rail)  # local: x 0.4..0.5 of the unit cube
    m = rail.matrix_world
    pts = [m @ Vector((x, y, z)) for x in (0.4, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
    from bpy_extras.object_utils import world_to_camera_view

    bpy.context.view_layer.update()
    proj = [world_to_camera_view(sc, cam, p) for p in pts]
    x0, x1 = min(p.x for p in proj) * W, max(p.x for p in proj) * W
    y0, y1 = min((1 - p.y) * H for p in proj), max((1 - p.y) * H for p in proj)
    area = frame_area()
    assert np.allclose(out["box"], (x0, y0, x1, y1), atol=1), (out["box"], (x0, y0, x1, y1))
    assert x0 >= area.x - 1 and x1 <= area.right + 1 and y0 >= area.y - 1 and y1 <= area.bottom + 1
    assert max(x1 - x0 - area.w, y1 - y0 - area.h) > -2  # fills the area one way
    world = camera.frame_box(cam, tuple(min(p[i] for p in pts) for i in range(3)),
                             tuple(max(p[i] for p in pts) for i in range(3)))  # world coordinates  # fmt: skip
    assert world["warnings"] == []
