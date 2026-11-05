import math

import bpy
import numpy as np
from mathutils import Vector

from bsa.blender import bake, camera, extremes, motion
from bsa.layout import H, W, caption_band, target_area


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
    area = target_area()
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
    area = target_area(margin=40)
    x0, y0, x1, y1 = _projected(sc, cam, [tower, ball], frames)
    assert x0 >= area.x - 1 and x1 <= area.right + 1 and y0 >= area.y - 1 and y1 <= area.bottom + 1, (x0, y0, x1, y1)
    assert abs((x1 - x0) - area.w) < 2 or abs((y1 - y0) - area.h) < 2


def test_frame_objects_with_an_orthographic_camera_and_no_band():
    sc, cam = _scene("ORTHO")
    cam.rotation_euler = (math.radians(90), 0, 0)
    plate = _box("Plate", (2.0, 0.1, 1.0), (0.5, 0, 0.5))
    camera.frame_objects(cam, [plate], avoid=None, margin=0)
    s = target_area(avoid=None, margin=0)
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


def _aim(cam, pos, target):
    cam.location = Vector(pos)
    cam.rotation_euler = (Vector(target) - Vector(pos)).to_track_quat("-Z", "Y").to_euler()


def _ground_and_wheel():
    sc, cam = _scene()
    _box("Ground", (400.0, 400.0, 6.9), (0, 0, -2.65))  # a closed bank, its top at z=0.8
    wheel = _box("Wheel", (35.0, 12.0, 35.0), (0, 0, 12))
    return sc, cam, wheel


def test_framing_warns_when_the_camera_ends_up_under_the_ground_if_asked_not_to_lift():
    """Aimed up at a tall wheel from low down, centring the wheel above the caption band slides the camera down along
    its own up axis, under the ground. With above_ground=False (the old behaviour) the result only says so."""
    import warnings

    _sc, cam, wheel = _ground_and_wheel()
    _aim(cam, (16, 58, 4), (0, 0, 8))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = camera.frame_objects(cam, [wheel], above_ground=False)
    assert cam.location.z < 0.8, tuple(cam.location)
    under = [w for w in out["warnings"] if "under" in w and "'Ground'" in w]
    assert under and caught, out["warnings"]
    _aim(cam, (16, 58, 30), (0, 0, 12))  # from above the wheel's centre: centring keeps it above the bank
    out = camera.frame_objects(cam, [wheel])
    assert cam.location.z > 0.8 and not [w for w in out["warnings"] if "under" in w], (tuple(cam.location), out)
    assert out["lifted"] is None
    out = camera.frame_box(cam, (-17.5, -6, -5.5), (17.5, 6, 29.5))
    assert not [w for w in out["warnings"] if "under" in w], out


def test_framing_lifts_the_camera_out_of_the_ground():
    """By default a camera the fit would put under the ground is lifted above it and turned (pitched) so the subject
    is still framed in the band: never a shot from under the terrain."""
    sc, cam, wheel = _ground_and_wheel()
    for start, target in (((16, 58, 4), (0, 0, 8)), ((16, 58, -20), (0, 0, 8)), ((5, 40, 1.5), (0, 0, 20))):
        _aim(cam, start, target)
        out = camera.frame_objects(cam, [wheel])
        assert cam.location.z > 0.8, (start, tuple(cam.location))
        assert out["lifted"] and out["lifted"]["ground"] == "Ground", out
        assert not [w for w in out["warnings"] if "under" in w], out["warnings"]
        area = target_area()
        x0, y0, x1, y1 = _projected(sc, cam, [wheel])
        assert x0 >= area.x - 2 and x1 <= area.right + 2 and y0 >= area.y - 2 and y1 <= area.bottom + 2, (start, x0, y0,
                                                                                                            x1, y1)  # fmt: skip
        assert abs((x1 - x0) - area.w) < 3 or abs((y1 - y0) - area.h) < 3, (start, x0, y0, x1, y1)
        assert abs((y0 + y1) / 2 - (area.y + area.h / 2)) < 3, (start, y0, y1)
    first = dict(out)
    _aim(cam, (5, 40, 1.5), (0, 0, 20))
    out = camera.frame_box(cam, (-17.5, -6, -5.5), (17.5, 6, 29.5))
    assert out["lifted"] and cam.location.z > 0.8
    assert np.allclose(out["location"], first["location"], atol=0.05), (out["location"], first["location"])


def test_framing_refuses_a_subject_under_the_ground():
    """A subject wholly under the ground cannot be framed from above it: a clear error naming the shot, the camera
    and the ground."""
    _sc, cam = _scene()
    _box("Ground", (400.0, 400.0, 6.9), (0, 0, -2.65))
    buried = _box("Buried", (2.0, 2.0, 2.0), (0, 0, -3))
    _aim(cam, (0, -12, -3), (0, 0, -3))
    try:
        camera.frame_objects(cam, [buried], shot="s02")
    except camera.FramingError as exc:
        text = str(exc)
        assert "s02" in text and "'Cam'" in text and "'Ground'" in text and "'Buried'" in text, text
    else:
        raise AssertionError("a camera was framed on a subject under the ground")


def test_framing_under_open_water_is_not_under_the_ground():
    """A camera under a water plane tagged "surface" (an underwater shot of a sunken wreck) is not under the ground:
    open water is not ground, so no lift and no FramingError."""
    _sc, cam = _scene()
    bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, 5))
    water = bpy.context.object
    water.name = "Water"
    water["bsa_tags"] = "surface"
    wreck = _box("Wreck", (6.0, 2.0, 2.0), (0, 0, -3))
    _aim(cam, (0, -14, -2), (0, 0, -3))
    out = camera.frame_objects(cam, [wreck], shot="s01")
    assert out["lifted"] is None and cam.location.z < 5, (out, tuple(cam.location))


def test_framing_a_floating_subject_with_no_floor_is_fine():
    """A subject floating in empty space, framed from below it: there is no ground at all, and the subject itself
    is not ground, so no lift and no FramingError."""
    _sc, cam = _scene()
    sat = _box("Satellite", (8.0, 3.0, 3.0), (0, 0, 10))
    _aim(cam, (0, -20, 0), (0, 0, 10))
    out = camera.frame_objects(cam, [sat], shot="s01")
    assert out["lifted"] is None, out
    _aim(cam, (0.5, -0.5, 0), (0, 0, 10))  # right under the subject, looking up at it
    out = camera.frame_objects(cam, [sat], center=False, shot="s01")
    assert out["lifted"] is None, out
    out = camera.frame_box(cam, (-1, -1, -1), (1, 1, 1), of=sat, center=False, shot="s01")
    assert out["lifted"] is None, out


def test_frame_objects_puts_the_subject_between_explicit_rows():
    """px_top / px_bottom: the subject's top and bottom land on those screen rows (a tall subject: the height
    limits), centred across; the 1080x1920 frame is used whatever the scene's resolution says."""
    sc, cam = _scene()
    sc.render.resolution_x, sc.render.resolution_y = 1920, 1080  # Blender's default: not the short's frame
    cam.location, cam.rotation_euler = (0, -20, 3), (math.radians(90), 0, 0)
    tower = _box("Tower", (1.0, 1.0, 6.0), (0, 0, 3))
    out = camera.frame_objects(cam, [tower], px_top=500, px_bottom=1100)
    assert out["area"][1] == 500 and out["area"][3] == 1100, out
    sc.render.resolution_x, sc.render.resolution_y = W, H
    x0, y0, x1, y1 = _projected(sc, cam, [tower])
    assert abs(y0 - 500) < 2 and abs(y1 - 1100) < 2, (y0, y1)
    assert abs((x0 + x1) / 2 - out["area"][0] / 2 - out["area"][2] / 2) < 2, (x0, x1, out["area"])


def test_frame_objects_fits_an_explicit_box_with_the_lens_shift():
    """An explicit target box (rows and columns) and shift=True: the camera keeps its angle and only moves along its
    view axis; the lens shift centres the subject in the box (and is keyed with key=)."""
    sc, cam = _scene()
    cam.location, cam.rotation_euler = (0, -20, 3), (math.radians(90), 0, 0)
    wide = _box("Wide", (6.0, 1.0, 1.0), (1.5, 0, 2.0))  # off the camera's axis: only the shift can centre it
    out = camera.frame_objects(cam, [wide], px_top=820, px_bottom=1250, px_left=110, px_right=970, shift=True, key=7)
    assert out["area"] == (110, 820, 970, 1250), out
    assert math.isclose(cam.rotation_euler[0], math.radians(90), abs_tol=1e-6) and cam.rotation_euler[2] == 0
    assert abs(cam.location.x) < 1e-6 and abs(cam.location.z - 3) < 1e-6, tuple(cam.location)  # along its axis only
    assert out["shift"] != (0.0, 0.0) and out["warnings"] == [], out
    x0, y0, x1, y1 = _projected(sc, cam, [wide])
    assert x0 >= 109 and x1 <= 971 and y0 >= 819 and y1 <= 1251, (x0, y0, x1, y1)
    assert abs((x1 - x0) - 860) < 2, (x0, x1)  # the width limits
    assert abs((y0 + y1) / 2 - 1035) < 2 and abs((x0 + x1) / 2 - 540) < 2, (x0, y0, x1, y1)
    keyed = {fc.data_path for fc in extremes.fcurves(cam.data)}
    assert {"shift_x", "shift_y"} <= keyed, keyed


def test_framing_warnings_are_kept_on_the_scene_as_findings():
    """Each framing's warnings are kept on the scene (per camera and key), so the checks list them as "framing_call"
    findings for the review pages; framing that camera and key again without a problem clears them."""
    import warnings

    sc, cam = _scene()
    nut = _box("Nut", (0.02, 0.02, 0.02), (60, 25, -10))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = camera.frame_objects(cam, [nut], key=5, shot="s01")
    assert out["warnings"]
    found = camera.framing_findings(sc, "s01", 1, 30)
    assert found and all(f.check == "framing_call" and f.severity == "warning" and f.obj == "Cam" for f in found), found
    assert found[0].frame == 5 and "Nut" in found[0].message
    assert camera.framing_findings(sc, "s02", 40, 60) == []  # another shot
    _aim(cam, (0, -8, 1), (0, 0, 1))
    lever = _box("Lever", (5.0, 0.3, 0.4), (0, 0, 1))
    assert camera.frame_objects(cam, [lever], key=5, shot="s01")["warnings"] == []
    assert camera.framing_findings(sc, "s01", 1, 30) == []


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
    area = target_area()
    assert np.allclose(out["box"], (x0, y0, x1, y1), atol=1), (out["box"], (x0, y0, x1, y1))
    assert x0 >= area.x - 1 and x1 <= area.right + 1 and y0 >= area.y - 1 and y1 <= area.bottom + 1
    assert max(x1 - x0 - area.w, y1 - y0 - area.h) > -2  # fills the area one way
    world = camera.frame_box(cam, tuple(min(p[i] for p in pts) for i in range(3)),
                             tuple(max(p[i] for p in pts) for i in range(3)))  # world coordinates  # fmt: skip
    assert world["warnings"] == []


def _orbit_scene():
    sc, cam = _scene()
    sc.frame_end = 101
    _box("Floor", (20.0, 20.0, 0.1), (0, 0, -0.05))
    lock = _box("Lock", (1.2, 0.4, 0.6), (0, 0, 0.3))
    cam.location = (3.0, -3.0, 1.5)
    camera.frame_objects(cam, [lock], aim=True, key=1)
    return sc, cam, lock


def test_move_orbits_with_the_subject_in_the_band_the_whole_way():
    """A 90 degree orbit framed every 2 frames: the subject stays in the band at every frame (keys and in between),
    the distance changes smoothly, the Euler keys never flip, and stray keys inside the move are replaced."""
    sc, cam, lock = _orbit_scene()
    cam.location = (9.0, 9.0, 9.0)
    cam.keyframe_insert("location", frame=50)  # a stray key inside the move: removed
    sc.frame_set(1)
    out = camera.move(cam, [lock], 1, 101, orbit=90, shot="s02")
    assert out["keys"][:3] == [1, 3, 5] and out["keys"][-1] == 101 and out["warnings"] == [], out
    turned = (out["end"][0] - out["start"][0] + 180) % 360 - 180
    assert abs(turned - 90) < 6, out
    area = target_area()
    for f in range(1, 102):
        x0, y0, x1, y1 = _projected(sc, cam, [lock], [f])
        assert x0 >= area.x - 6 and x1 <= area.right + 6 and y0 >= area.y - 6 and y1 <= area.bottom + 6, (f, x0, y0, x1, y1)
        assert max(x1 - x0 - area.w, y1 - y0 - area.h) > -40, (f, x0, y0, x1, y1)  # still fills the band
    d = np.array(out["distances"])
    assert np.abs(np.diff(d, 2)).max() < 0.02 * d.mean(), d
    zs = []
    for f in range(1, 102):
        sc.frame_set(f)
        zs.append(cam.rotation_euler[2])
    assert np.abs(np.diff(zs)).max() < math.radians(5), np.degrees(np.diff(zs)).max()
    keyed = {kp.co[0] for fc in extremes.fcurves(cam) if fc.data_path == "location" for kp in fc.keyframe_points}
    assert 50.0 not in keyed and keyed == set(map(float, out["keys"]))


def test_move_pushes_in_to_a_part():
    """A dolly with `to`: the framed box slides from the whole lock to its plug, which fills the band at the end."""
    sc, cam, lock = _orbit_scene()
    plug = _box("Plug", (0.2, 0.2, 0.2), (0.3, -0.25, 0.3))
    out = camera.move(cam, [lock], 1, 61, to=[plug])
    area = target_area()
    x0, y0, x1, y1 = _projected(sc, cam, [plug], [61])
    assert abs((x1 - x0) - area.w) < 4 or abs((y1 - y0) - area.h) < 4, (x0, y0, x1, y1)
    d = np.array(out["distances"])
    assert d[-1] < 0.5 * d[0] and np.all(np.diff(d) < 1e-6), d  # it moves in all the way, never back out


def _keyed_box(name, size, keys, axis):
    """A box keyed along one axis: `keys` (frame, value) pairs."""
    obj = _box(name, size, (0.0, 0.0, size[2] / 2))
    for f, v in keys:
        obj.location[axis] = v
        obj.keyframe_insert("location", index=axis, frame=f)
    return obj


def _in_band(sc, cam, objs, frames, tol=1.0):
    area = target_area()
    for f in frames:
        x0, y0, x1, y1 = _projected(sc, cam, objs, [f])
        assert x0 >= area.x - tol and x1 <= area.right + tol and y0 >= area.y - tol and y1 <= area.bottom + tol, (
            f, (x0, y0, x1, y1), area)


def test_follow_tracks_a_moving_part_from_a_fixed_place():
    """Track: the camera stays where it is (backing off only along its view axis when the part would leave the band)
    and turns to keep a sliding part in the band."""
    sc, cam = _scene()
    sc.frame_end = 60
    slider = _keyed_box("Slider", (0.4, 0.4, 0.4), [(1, 0.0), (60, 4.0)], 0)
    cam.location = (2.0, -6.0, 1.5)
    sc.frame_set(1)
    camera.frame_objects(cam, [slider], aim=True, key=1)
    start = np.array(cam.matrix_world.translation)
    out = camera.follow(cam, slider, 1, 60, shot="s03")
    assert out["keys"][:3] == [1, 3, 5] and out["keys"][-1] == 60, out["keys"]
    assert len(out["distances"]) == len(out["keys"]) and out["warnings"] == [], out
    for f in out["keys"]:
        sc.frame_set(f)
        moved = np.array(cam.matrix_world.translation) - start
        if np.linalg.norm(moved) > 1e-4:
            back = np.array(cam.matrix_world.col[2][:3])
            back /= np.linalg.norm(back)
            along = moved / np.linalg.norm(moved)
            assert along @ back > 0.995, (f, moved, back)  # only backed off, along its view axis
    _in_band(sc, cam, [slider], (1, 30, 60))
    sc.frame_set(1)
    first = cam.matrix_world.col[2].to_3d()
    sc.frame_set(60)
    assert first.angle(cam.matrix_world.col[2].to_3d()) > math.radians(10)  # it turned to follow the part


def test_follow_rides_with_a_part_keeping_its_offset():
    """Ride: the camera keeps its start offset from a piston going up and down (within the smoothing's lag) and
    keeps it in the band."""
    sc, cam = _scene()
    sc.frame_end = 120
    piston = _keyed_box("Piston", (0.3, 0.3, 0.6), [(1, 0.3), (60, 0.8), (120, 0.3)], 2)
    cam.location = (1.5, -4.0, 1.2)
    sc.frame_set(1)
    camera.frame_objects(cam, [piston], aim=True, key=1, margin=150)  # room to spare: no backing off
    offset = cam.matrix_world.translation - piston.matrix_world.translation
    out = camera.follow(cam, [piston], 1, 120, mode="ride")
    assert out["warnings"] == [], out
    for f in (1, 20, 40):
        sc.frame_set(f)
        now = cam.matrix_world.translation - piston.matrix_world.translation
        assert (now - offset).length < 0.02, (f, now, offset)
    sc.frame_set(1)
    low = cam.matrix_world.translation.z
    sc.frame_set(60)
    assert cam.matrix_world.translation.z > low + 0.4, (low, cam.matrix_world.translation.z)  # it rode up
    _in_band(sc, cam, [piston], (1, 20, 40, 60, 120))


def test_follow_refuses_an_unknown_mode():
    _sc, cam = _scene()
    wheel = _box("Wheel", (0.5, 0.5, 0.5), (0, 0, 0.25))
    try:
        camera.follow(cam, wheel, 1, 30, mode="orbit")
    except ValueError as e:
        assert "track" in str(e) and "ride" in str(e), e
    else:
        raise AssertionError("an unknown mode was accepted")
    try:
        camera.follow(cam, wheel, 30, 30)
    except ValueError as e:
        assert "f1" in str(e), e
    else:
        raise AssertionError("f1 <= f0 was accepted")


def _fast_slide(name, size, y=0.0):
    """A box sliding 6 m along X from frame 11 to 60, faster and faster (x = 6 t^2), keyed every frame."""
    obj = _box(name, size, (0.0, y, size[2] / 2))
    for f in range(11, 61):
        obj.location.x = 6.0 * ((f - 11) / 49) ** 2
        obj.keyframe_insert("location", index=0, frame=f)
    return obj


def _location_keys(cam):
    return {kp.co[0]: tuple(fc.evaluate(kp.co[0]) for fc in extremes.fcurves(cam) if fc.data_path == "location")
            for fc in extremes.fcurves(cam) if fc.data_path == "location" for kp in fc.keyframe_points}


def test_follow_keeps_keys_outside_and_a_fast_part_in_the_band_in_both_modes():
    """Keys before f0 and after f1 stay as they were and a stale key inside is replaced; a part sliding faster and
    faster stays in the band at every key, tracked (a list of two parts) and ridden; band=False does not move a
    tracking camera."""
    for mode in ("track", "ride"):
        sc, cam = _scene()
        sc.frame_end = 80
        parts = [_fast_slide("Slider", (0.4, 0.4, 0.4))]
        if mode == "track":
            parts.append(_fast_slide("Rail", (0.6, 0.2, 0.2), y=0.4))
        cam.location = (3.0, -7.0, 2.0) if mode == "track" else (1.0, -3.0, 1.0)
        sc.frame_set(11)
        camera.frame_objects(cam, parts, aim=True, key=11)
        for f, where in ((1, (9.0, -9.0, 3.0)), (30, (0.0, 0.0, 9.0)), (70, (-9.0, -9.0, 3.0))):
            cam.location = where
            cam.keyframe_insert("location", frame=f)
            cam.keyframe_insert("rotation_euler", frame=f)
        before = _location_keys(cam)
        sc.frame_set(11)
        out = camera.follow(cam, parts if mode == "track" else parts[0], 11, 60, mode=mode)
        after = _location_keys(cam)
        assert after[1.0] == before[1.0] and after[70.0] == before[70.0], (mode, before, after)
        assert 30.0 not in after and set(after) == {1.0, 70.0} | set(map(float, out["keys"])), (mode, sorted(after))
        assert out["warnings"] == [], (mode, out["warnings"])
        _in_band(sc, cam, parts, out["keys"])
    sc, cam = _scene()
    slider = _fast_slide("Slider", (0.4, 0.4, 0.4))
    cam.location = (3.0, -7.0, 2.0)
    sc.frame_set(11)
    camera.frame_objects(cam, [slider], aim=True, key=11)
    start = np.array(cam.matrix_world.translation)
    out = camera.follow(cam, slider, 11, 60, band=False)
    for f in out["keys"]:
        sc.frame_set(f)
        assert np.linalg.norm(np.array(cam.matrix_world.translation) - start) < 1e-5, f  # turned, never moved
