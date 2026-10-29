"""Framing: place a camera so the objects fill the safe area and stay above the caption band.

`frame_objects` keeps the camera's angle (or first turns it to look at the objects, `aim=True`), then moves it along
its view axis (an orthographic camera changes its ortho_scale instead) and, with `center=True`, sideways and up in its
own plane, until the objects' bounding boxes, at every frame given, just fit the area from `bsa.layout.frame_area`:
the safe area above the caption band, `margin` px inside. Pixels are in the 1080x1920 layout frame, top-left origin.
`frame_box` does the same for a box given by its corners (in the world, or in one object's own coordinates): a part
of one long mesh, with no helper object.

An answer that cannot be right comes with a warning (`FramingWarning`, and in the result's "warnings"): the camera
sent more than FAR_FACTOR times the framed box's distance or size away (the objects were not where it looked: a
parent moved or turned them), the box ending up outside the view, behind the clip start or past the clip end, the
camera ending up under the ground or inside a solid (the first surface straight above it faces up, or nothing is
below it: centring slides the camera along its own up axis, so a camera aimed up from low down sinks), or the fit
not settling. A box with no size (no geometry) is refused with ValueError.

The objects are measured as the scene they are in places them (keys, parents, drivers), also in a scene that is not
the window's (script mode builds each scene in turn). Without `scene=`, the camera's scene that holds every object is
used (a camera can be in several scenes). Objects the scene does not have are refused with ValueError: their own
unevaluated place (often the origin) would give a wrong camera with no warning.
"""

from __future__ import annotations

import warnings

import bpy
import numpy as np
from mathutils import Matrix, Vector

from bsa.blender.bake import is_overlay
from bsa.layout import H, Rect, W, caption_band, frame_area

_DEFAULT = object()
ITERATIONS = 60
TOL_PX = 0.5
FAR_FACTOR = 5.0  # the camera moved more than this times max(box size, its new distance to the box): absurd
MIN_SIZE = 1e-6  # a box smaller than this (metres) each way has no size to frame


class FramingWarning(UserWarning):
    """frame_objects / frame_box gave an answer that cannot be what was meant (see the module docstring)."""


def _depsgraph(scene):
    """The evaluated depsgraph of `scene` (its first view layer when it is not the context scene)."""
    if bpy.context.scene == scene:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    else:
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            depsgraph = bpy.context.evaluated_depsgraph_get()
    if depsgraph.scene != scene:
        raise RuntimeError(f"frame_objects: asked for scene {scene.name!r}, Blender evaluated "
                           f"{depsgraph.scene.name!r}: pass scene= explicitly")  # fmt: skip
    return depsgraph


def _scene_of(cam, objs, scene):
    """`scene`, else the first of the camera's scenes that holds every object (else its first, or the context's)."""
    if scene is not None:
        return scene
    scenes = list(cam.users_scene) or [bpy.context.scene]
    return next((sc for sc in scenes if all(sc.objects.get(o.name) == o for o in objs)), scenes[0])


def _not_evaluated(objs, depsgraph) -> list[str]:
    """Names of the objects `depsgraph` does not evaluate (not in its scene or view layer)."""
    return [o.name for o in objs if not o.evaluated_get(depsgraph).is_evaluated]


def _corners(objs, scene, frames, box=None) -> np.ndarray:
    """World-space bounding-box corners of every object, at each frame (evaluated: keys, parents, modifiers); with
    `box` ((lo, hi) in the objects' own coordinates), that box's corners instead of their bounding boxes."""
    depsgraph = _depsgraph(scene)
    original = scene.frame_current
    pts = []
    local = None
    if box is not None:
        lo, hi = np.asarray(box[0], dtype=np.float64), np.asarray(box[1], dtype=np.float64)
        local = np.array([(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    missing = _not_evaluated(objs, depsgraph)
    if missing:
        raise ValueError(f"frame_objects: scene {scene.name!r} does not have {', '.join(repr(n) for n in missing)} "
                         "(or hides it in its view layer), so where it is is not known: pass scene= the scene it is "
                         "in, or frame a box with frame_box")  # fmt: skip
    try:
        for frame in frames:
            if frame is not None:
                scene.frame_set(frame)
                depsgraph.update()
            for obj in objs:
                ev = obj.evaluated_get(depsgraph)
                m = np.array(ev.matrix_world, dtype=np.float64)
                corners = local if local is not None else np.array([tuple(c) for c in ev.bound_box], dtype=np.float64)
                pts.append(corners @ m[:3, :3].T + m[:3, 3])
    finally:
        scene.frame_set(original)
    if not pts:
        raise ValueError("frame_objects needs at least one object")
    return np.concatenate(pts)


def _projector(cam, scene):
    """(pixel coordinates of world points for a camera-to-world matrix, the camera's focal length in px)."""
    r = scene.render
    depsgraph = _depsgraph(scene)
    proj = np.array(cam.calc_matrix_camera(depsgraph, x=r.resolution_x, y=r.resolution_y,
                                           scale_x=r.pixel_aspect_x, scale_y=r.pixel_aspect_y))  # fmt: skip

    def project(cam_to_world: np.ndarray, pts: np.ndarray):
        view = np.linalg.inv(cam_to_world)
        local = pts @ view[:3, :3].T + view[:3, 3]
        clip = np.c_[local, np.ones(len(local))] @ proj.T
        w = np.where(np.abs(clip[:, 3]) < 1e-12, 1e-12, clip[:, 3])
        x = (clip[:, 0] / w + 1) / 2 * W
        y = (1 - (clip[:, 1] / w + 1) / 2) * H
        return x, y, -local[:, 2]

    return project, proj[1, 1] * H / 2  # px per unit at depth 1 (vertical)


def _look_at(m: np.ndarray, target: np.ndarray) -> np.ndarray:
    """m turned (about its own position, keeping world Z up) to look at `target`."""
    loc = m[:3, 3]
    direction = Vector(target - loc)
    if direction.length < 1e-9:
        return m
    rot = direction.to_track_quat("-Z", "Y").to_matrix()
    out = np.array(Matrix.Translation(Vector(loc)) @ rot.to_4x4(), dtype=np.float64)
    scale = np.linalg.norm(m[:3, :3], axis=0)
    out[:3, :3] *= scale
    return out


_POSE = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale", "delta_location",
         "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")


def _pose(cam) -> dict:
    """The camera's own transform values (and ortho scale) as they are now, keyed or not."""
    return {**{k: tuple(getattr(cam, k)) for k in _POSE}, "ortho_scale": cam.data.ortho_scale}


def _set_pose(cam, pose: dict) -> None:
    for k in _POSE:
        setattr(cam, k, pose[k])
    cam.data.ortho_scale = pose["ortho_scale"]


def frame_objects(cam, objs, frames=None, safe_rect: Rect | None = None, avoid=_DEFAULT, margin: float = 24,
                  aim: bool = False, center: bool = True, scene=None, key: int | None = None) -> dict:  # fmt: skip
    """Move `cam` so the bounding boxes of `objs` (at each of `frames`, default the current frame) fill the area to
    frame heroes in (`bsa.layout.frame_area(safe_rect, avoid, margin)`: by default the safe area above the caption
    band, 24 px inside) as tightly as fits: the box touches the area on its limiting sides and stays inside it.

    - `avoid`: the band to keep clear (default `bsa.layout.caption_band()`, rows about 1165-1395); None for none.
    - `aim`: first turn the camera to look at the boxes' centre (world Z up); else its angle is kept.
    - `center`: slide the camera in its own plane so the boxes are centred in the area; else only the distance (or
      ortho scale) changes and the boxes stay where they project now, scaled about the frame centre.

    - `key`: a frame to key the framed position at (location, rotation and, for an orthographic camera,
      ortho_scale). Without it nothing is keyed.

    Safe on an animated camera: it starts from the pose the camera has now (an unkeyed change included), measuring
    the objects at other `frames` neither moves the camera nor changes the scene's frame (it comes back to the frame
    it was on), and existing keys are left alone. The result is a plain transform, though: on a camera with keys,
    the next frame change snaps it back to them, so pass `key=FRAME` (or key it yourself) right after. To frame
    several positions on one camera, call it once per position with its own `key`: no need to clear the animation.

    Sets cam.matrix_world (and ortho_scale for an orthographic camera) and returns {"location", "box" (x0, y0, x1, y1
    px of everything framed), "area" (x0, y0, x1, y1 px), "distance" (along the view axis to the boxes' centre),
    "warnings" (why the answer cannot be right, each also a FramingWarning; empty when it is fine)}. A box with no
    size (objects with no geometry) raises ValueError."""
    objs = list(objs)
    scene = _scene_of(cam, objs, scene)
    pose = _pose(cam)  # the pose now: frame changes while measuring would snap a keyed camera back to its keys
    pts = _corners(objs, scene, list(frames) if frames else [None])
    _set_pose(cam, pose)
    what = ", ".join(repr(o.name) for o in objs[:4]) + (f" and {len(objs) - 4} more" if len(objs) > 4 else "")
    return _frame(cam, pts, scene, what, safe_rect, avoid, margin, aim, center, key)


def frame_box(cam, lo, hi, of=None, frames=None, safe_rect: Rect | None = None, avoid=_DEFAULT, margin: float = 24,
              aim: bool = False, center: bool = True, scene=None, key: int | None = None) -> dict:  # fmt: skip
    """`frame_objects` for the box with corners `lo` and `hi`: in the world, or with `of` (an object) in that object's
    own coordinates, following it at each of `frames` (keys, parents, constraints). Frames a part of one long mesh
    (the last metre of a rail, one end of a beam) without a helper object. Same options and result."""
    scene = _scene_of(cam, [of] if of is not None else [], scene)
    box = (tuple(lo), tuple(hi))
    if of is None:
        lo_, hi_ = np.asarray(box[0], dtype=np.float64), np.asarray(box[1], dtype=np.float64)
        pts = np.array([(x, y, z) for x in (lo_[0], hi_[0]) for y in (lo_[1], hi_[1]) for z in (lo_[2], hi_[2])])
        what = f"the box {box[0]} to {box[1]}"
    else:
        pose = _pose(cam)
        pts = _corners([of], scene, list(frames) if frames else [None], box)
        _set_pose(cam, pose)
        what = f"the box {box[0]} to {box[1]} of {of.name!r}"
    return _frame(cam, pts, scene, what, safe_rect, avoid, margin, aim, center, key)


def _fmt(v) -> str:
    return "(" + ", ".join(f"{float(x):.2f}" for x in v) + ")"


def _frame(cam, pts, scene, what, safe_rect, avoid, margin, aim, center, key) -> dict:
    """Move the camera so the world points `pts` fill the framing area (see frame_objects)."""
    size = float(np.ptp(pts, axis=0).max()) if len(pts) else 0.0
    if not np.all(np.isfinite(pts)) or size < MIN_SIZE:
        raise ValueError(f"frame_objects: {what} has no size to frame (no geometry, or every point in one place): "
                         "frame objects with geometry, or a box with frame_box")  # fmt: skip
    area = frame_area(safe_rect, caption_band() if avoid is _DEFAULT else avoid, margin)
    _depsgraph(scene).update()  # matrix_world from the restored pose (no frame change: keys are not read again)
    project, focal = _projector(cam, scene)
    ortho = cam.data.type == "ORTHO"
    m = np.array(cam.matrix_world, dtype=np.float64)
    start = m[:3, 3].copy()
    settled = False
    if aim:
        m = _look_at(m, (pts.min(0) + pts.max(0)) / 2)
    rot = m[:3, :3] / np.linalg.norm(m[:3, :3], axis=0)
    right, up, back = rot[:, 0], rot[:, 1], rot[:, 2]
    want = np.array([area.x + area.w / 2, area.y + area.h / 2])
    for _ in range(ITERATIONS):
        local_depth = (pts - m[:3, 3]) @ -back
        if not ortho and local_depth.min() < 0.05 * max(np.ptp(pts, axis=0).max(), 1e-3):  # behind or too close
            m[:3, 3] += back * (np.ptp(pts, axis=0).max() + 0.1 - local_depth.min())
            continue
        x, y, depth = project(m, pts)
        box = np.array([x.min(), y.min(), x.max(), y.max()])
        size = box[2:] - box[:2]
        s = max(size[0] / area.w, size[1] / area.h)
        mid = (box[:2] + box[2:]) / 2
        off = (mid - want) if center else np.zeros(2)
        if abs(s - 1) * max(area.w, area.h) < TOL_PX and np.abs(off).max() < TOL_PX:
            settled = True
            break
        d = float(np.mean(depth))
        per_px = 1 / focal if ortho else d / focal  # world units per pixel at the boxes' depth
        m[:3, 3] += right * off[0] * per_px - up * off[1] * per_px  # pixel y grows downwards
        if ortho:
            cam.data.ortho_scale *= s
            project, focal = _projector(cam, scene)
        else:
            m[:3, 3] += back * d * (s - 1)
    cam.matrix_world = Matrix(m.tolist())
    if key is not None:
        cam.keyframe_insert("location", frame=key)
        cam.keyframe_insert(cam.rotation_mode == "QUATERNION" and "rotation_quaternion" or (
            "rotation_axis_angle" if cam.rotation_mode == "AXIS_ANGLE" else "rotation_euler"), frame=key)  # fmt: skip
        if ortho:
            cam.data.keyframe_insert("ortho_scale", frame=key)
    x, y, depth = project(m, pts)
    box = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
    found = _absurd(cam, what, pts, start, m[:3, 3], box, depth, settled, ortho)
    below = _under(scene, m[:3, 3], what)
    if below:
        found.insert(len(found) - (not settled), below)
    for w in found:
        warnings.warn(w, FramingWarning, stacklevel=3)
    return {
        "location": tuple(float(v) for v in m[:3, 3]),
        "box": box,
        "area": (area.x, area.y, area.right, area.bottom),
        "distance": float(np.mean(depth)),
        "warnings": found,
    }


def _first_hit(scene, depsgraph, origin: Vector, direction: Vector):
    """(location, normal, object) of the first surface along the ray that is not an overlay, or None."""
    for _ in range(32):
        hit, loc, normal, _index, obj, _m = scene.ray_cast(depsgraph, origin, direction)
        if not hit:
            return None
        if obj is None or not is_overlay(obj.original):
            return loc, normal, obj
        origin = loc + direction * max(1e-4, 1e-6 * abs(loc.z))  # a glow or guide line: look through it
    return None


def _under(scene, pos: np.ndarray, what: str) -> str | None:
    """Why a camera at `pos` is under the ground or inside a solid, or None: the first surface straight above it
    faces up (it is seen from below), or there is a surface above it and none below (it is under all the scenery; a
    camera under a bridge still has the ground or water below it)."""
    depsgraph = _depsgraph(scene)
    origin = Vector(pos.tolist())
    above = _first_hit(scene, depsgraph, origin, Vector((0.0, 0.0, 1.0)))
    if above is None:
        return None
    loc, normal, obj = above
    if normal.z <= 0 and _first_hit(scene, depsgraph, origin, Vector((0.0, 0.0, -1.0))) is not None:
        return None  # a ceiling or the underside of a bridge, with ground below
    name = obj.original.name if obj is not None else "a surface"
    return (f"frame_objects: the camera ends up under {name!r} (at {_fmt(pos)}, {loc.z - pos[2]:.2f} m below it): it "
            f"sees {what} from under the ground or from inside a solid. Start the camera higher than the box's "
            "centre (centring slides it along its own up axis), or use center=False")  # fmt: skip


def _absurd(cam, what, pts, start, end, box, depth, settled, ortho) -> list[str]:
    """Why the framed answer cannot be what was meant (see the module docstring), or []."""
    out = []
    centre = (pts.min(0) + pts.max(0)) / 2
    size = float(np.ptp(pts, axis=0).max())
    moved = float(np.linalg.norm(end - start))
    near = max(size, float(np.linalg.norm(centre - end)))
    if moved > FAR_FACTOR * near:
        out.append(f"frame_objects moved the camera {moved:.2f} m (from {_fmt(start)} to {_fmt(end)}) to frame {what}, "
                   f"more than {FAR_FACTOR:g} times the box's size or distance ({near:.2f} m): the box (centre "
                   f"{_fmt(centre)}, {size:.3f} m across) is not where the camera looked. A parent may have moved or "
                   "turned the objects; check where they are (or frame a region with frame_box), use aim=True to "
                   "turn the camera to them, and look at the still")  # fmt: skip
    if box[0] < -1 or box[1] < -1 or box[2] > W + 1 or box[3] > H + 1:
        out.append(f"frame_objects: {what} ends up outside the view (box {tuple(round(v) for v in box)} px in "
                   f"{W}x{H}): look at the still")  # fmt: skip
    data = cam.data
    if not ortho and float(depth.min()) < data.clip_start:
        out.append(f"frame_objects: part of {what} is nearer than the camera's clip start ({data.clip_start:g} m): "
                   "it is cut off or not rendered")  # fmt: skip
    if float(depth.max()) > data.clip_end:
        out.append(f"frame_objects: part of {what} is past the camera's clip end ({data.clip_end:g} m, the box "
                   f"reaches {float(depth.max()):.1f} m): it is not rendered. Raise clip_end")  # fmt: skip
    if not settled:
        out.append(f"frame_objects: the fit for {what} did not settle in {ITERATIONS} steps: look at the still")
    return out
