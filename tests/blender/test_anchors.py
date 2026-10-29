import itertools
import math

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from bsa.blender import anchors, bake
from bsa.layout import H, W


def _scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = W, H
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.location = (0, -10, 0)
    cam.rotation_euler = (math.pi / 2, 0, 0)  # looking along +Y
    sc.camera = cam
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
    bpy.context.object.name = "Ball"
    return sc, cam, bpy.data.objects["Ball"]


def test_projects_the_top_of_a_known_object():
    sc, cam, _ = _scene()
    out = anchors.project(sc, ["Ball"], 1, 1)
    x, y = out["Ball"]["points"][0]
    assert out["Ball"]["f0"] == 1
    assert abs(x - W / 2) < 0.5  # centred
    top = world_to_camera_view(sc, cam, Vector((0.5, -0.5, 0.5)))  # the nearest top edge is highest on screen
    assert abs(y - (1 - top.y) * H) < 0.5 and y < H / 2


def test_follows_a_moving_object_per_frame():
    sc, _, ball = _scene()
    bake.keyframe_series(ball, "location", 0, [0.2 * i for i in range(10)])
    sc.frame_set(1)
    pts = anchors.project(sc, ["Ball"], 1, 10)["Ball"]["points"]
    xs = [p[0] for p in pts]
    assert len(pts) == 10 and all(b > a for a, b in itertools.pairwise(xs)), xs
    assert sc.frame_current == 1


def test_missing_object_and_behind_the_camera():
    sc, _, ball = _scene()
    try:
        anchors.project(sc, ["Nope"], 1, 1)
    except KeyError as exc:
        assert "Nope" in str(exc)
    else:
        raise AssertionError("a missing object was not reported")
    ball.location = (0, -20, 0)  # behind the camera
    assert anchors.project(sc, ["Ball"], 1, 1)["Ball"]["points"] == [None]


def test_follows_a_camera_switched_by_markers():
    sc, cam, _ = _scene()
    side = bpy.data.objects.new("Side", bpy.data.cameras.new("Side"))
    sc.collection.objects.link(side)
    side.location = (4, 0, 1.5)  # closer and higher than the front camera: the cube's top sits elsewhere on screen
    side.rotation_euler = (math.pi / 2, 0, math.pi / 2)  # looking along -X at the cube
    m1 = sc.timeline_markers.new("cam1", frame=1)
    m1.camera = cam
    m2 = sc.timeline_markers.new("cam2", frame=3)
    m2.camera = side
    sc.frame_set(1)
    pts = anchors.project(sc, ["Ball"], 1, 4)["Ball"]["points"]
    sc.frame_set(4)
    corners = [Vector((x, y, z)) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
    top = max(world_to_camera_view(sc, side, c).y for c in corners)  # highest corner as the side camera sees it
    assert abs(pts[3][1] - (1 - top) * H) < 0.5, pts
    assert abs(pts[3][1] - pts[0][1]) > 20  # really through the other camera
    assert abs(pts[0][0] - W / 2) < 0.5 and abs(pts[3][0] - W / 2) < 0.5


def test_hero_boxes_of_the_shots_heroes():
    sc, cam, ball = _scene()
    bake.tag(ball, "hero:s02")
    bpy.ops.mesh.primitive_cube_add(size=1, location=(2, 0, 0))
    bake.tag(bpy.context.object, "hero")
    bpy.context.object.name = "Other"
    assert anchors.heroes(sc, "s02") == ["Ball", "Other"] and anchors.heroes(sc, "s01") == ["Other"]
    out = anchors.hero_boxes(sc, ["Ball"], 3, 4)
    assert out["f0"] == 3 and len(out["boxes"]["Ball"]) == 2
    x0, y0, x1, y1 = out["boxes"]["Ball"][0]
    corners = [world_to_camera_view(sc, cam, Vector((x, y, z))) for x in (-0.5, 0.5) for y in (-0.5, 0.5)
               for z in (-0.5, 0.5)]  # fmt: skip
    assert abs(x0 - min(c.x for c in corners) * W) < 0.5 and abs(x1 - max(c.x for c in corners) * W) < 0.5
    assert abs(y0 - (1 - max(c.y for c in corners)) * H) < 0.5 and abs(y1 - (1 - min(c.y for c in corners)) * H) < 0.5


def test_scenery_boxes_are_the_other_meshes_on_screen():
    """Every rendered mesh that is no hero, on screen and smaller than a backdrop: what the end card keeps off after
    the heroes (a cradle's top rails)."""
    sc, cam, ball = _scene()
    bake.tag(ball, "hero")
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 2))
    rail = bpy.context.object
    rail.name, rail.scale = "Rail", (4, 0.1, 0.1)
    bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -1))
    bpy.context.object.name = "Floor"  # fills the frame: a backdrop, not something to keep off
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -20, 0))
    bpy.context.object.name = "Behind"  # behind the camera
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 3))
    bpy.context.object.name = "Hidden"
    bpy.context.object.hide_render = True
    out = anchors.scenery_boxes(sc, 2, 3)
    assert out["f0"] == 2 and len(out["boxes"]) == 2
    boxes = out["boxes"][0]
    assert len(boxes) == 1, boxes  # only the rail
    x0, y0, x1, y1 = boxes[0]
    top = world_to_camera_view(sc, cam, Vector((0, -0.05, 2.05)))
    assert x0 < W / 2 < x1 and abs(y0 - (1 - top.y) * H) < 2 and y1 - y0 < 100, boxes
