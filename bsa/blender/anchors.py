"""Where objects are on screen: each `near` label's object projected through the shot's camera, per frame, so the
edit can put the label just above it, the screen boxes of the heroes of the shots with near labels (labels keep off
them) and of the last shot (the end card stays off them), and the last shot's other meshes during the end card
(`scenery_boxes`: the end card keeps off them too, after the heroes).
The result goes to source/anchors.json, stamped with the timeline and build."""

from __future__ import annotations

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from bsa.blender.bake import is_hero
from bsa.layout import H, W


def _depsgraph(scene):
    if bpy.context.scene == scene:
        return bpy.context.evaluated_depsgraph_get()
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        return bpy.context.evaluated_depsgraph_get()


def screen_top(scene, cam, obj, depsgraph) -> list[float] | None:
    """[x, y] in pixels (origin top-left) of the top of the object's evaluated bounding box on screen: x at the box
    centre, y at its highest projected corner. None when the object is wholly behind the camera."""
    ev = obj.evaluated_get(depsgraph)
    m = ev.matrix_world
    corners = [m @ Vector(c) for c in ev.bound_box]
    pts = [world_to_camera_view(scene, cam, c) for c in corners]
    front = [p for p in pts if p.z > 0]
    if not front:
        return None
    centre = world_to_camera_view(scene, cam, sum(corners, Vector()) / len(corners))
    x = centre.x if centre.z > 0 else sum(p.x for p in front) / len(front)
    return [round(x * W, 2), round((1.0 - max(p.y for p in front)) * H, 2)]


def screen_box(scene, cam, obj, depsgraph) -> list[float] | None:
    """[x0, y0, x1, y1] in pixels (origin top-left) around the object's evaluated bounding box on screen (corners in
    front of the camera only), or None when it is wholly behind the camera."""
    ev = obj.evaluated_get(depsgraph)
    pts = [world_to_camera_view(scene, cam, ev.matrix_world @ Vector(c)) for c in ev.bound_box]
    front = [p for p in pts if p.z > 0]
    if not front:
        return None
    xs, ys = [p.x * W for p in front], [(1.0 - p.y) * H for p in front]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


SCENERY_MAX_SHARE = 0.4  # a mesh whose box covers more of the frame than this is a backdrop (floor, wall, sky)


def scenery_boxes(scene, f0: int, f1: int, shot_id: str | None = None) -> dict:
    """{"f0": f0, "boxes": [[[x0, y0, x1, y1], ...] per frame f0..f1]}: the screen boxes (clipped to the frame) of
    every rendered mesh or curve that is no hero of `shot_id` (no hero at all without one) and is visible, on screen
    and covers at most SCENERY_MAX_SHARE of the frame (a floor or backdrop fills the frame: it is not something to
    keep off). scene.frame_current is restored."""
    view_layer = scene.view_layers[0]
    objs = [o for o in scene.objects if o.type in ("MESH", "CURVE") and not o.hide_render
            and not is_hero(o, shot_id) and o.visible_get(view_layer=view_layer)]  # fmt: skip
    out = {"f0": f0, "boxes": []}

    def one(cam, dg):
        boxes = []
        for o in objs:
            b = screen_box(scene, cam, o, dg)
            if b is None:
                continue
            x0, y0, x1, y1 = max(b[0], 0.0), max(b[1], 0.0), min(b[2], float(W)), min(b[3], float(H))
            if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) > SCENERY_MAX_SHARE * W * H:
                continue
            boxes.append([round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)])
        out["boxes"].append(boxes)

    _each_frame(scene, f0, f1, one)
    return out


def heroes(scene, shot_id: str) -> list[str]:
    """Names of the renderable objects in `scene` that are heroes in shot `shot_id`."""
    return sorted(o.name for o in scene.objects if is_hero(o, shot_id) and not o.hide_render)


def hero_boxes(scene, names: list[str], f0: int, f1: int) -> dict:
    """{"f0": f0, "boxes": {name: [[x0, y0, x1, y1] or None per frame f0..f1]}} through the scene's camera at each
    frame. scene.frame_current is restored."""
    out = {"f0": f0, "boxes": {n: [] for n in names}}
    _each_frame(scene, f0, f1, lambda cam, dg: [out["boxes"][n].append(screen_box(scene, cam, scene.objects[n], dg))
                                                for n in names])  # fmt: skip
    return out


def _each_frame(scene, f0: int, f1: int, fn) -> None:
    depsgraph = _depsgraph(scene)
    original = scene.frame_current
    try:
        for frame in range(f0, f1 + 1):
            scene.frame_set(frame)
            depsgraph.update()  # a scene that is not the context scene is not evaluated by frame_set alone
            cam = scene.camera  # markers may switch the camera during a shot
            if cam is None:
                raise ValueError(f"scene {scene.name!r} has no camera at frame {frame}")
            fn(cam.evaluated_get(depsgraph), depsgraph)
    finally:
        scene.frame_set(original)


def project(scene, names: list[str], f0: int, f1: int) -> dict:
    """{object name: {"f0": f0, "points": [[x, y] or None per frame f0..f1]}} through the scene's camera at each frame
    (camera markers may switch it). Missing objects raise KeyError naming them. scene.frame_current is restored."""
    missing = [n for n in names if scene.objects.get(n) is None]
    if missing:
        raise KeyError(", ".join(missing))
    out = {n: {"f0": f0, "points": []} for n in names}
    _each_frame(scene, f0, f1, lambda cam, dg: [out[n]["points"].append(screen_top(scene, cam, scene.objects[n], dg))
                                                for n in names])  # fmt: skip
    return out
