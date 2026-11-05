"""Framing: place a camera so the objects fill the band between the top cards and the caption band.

`frame_objects` keeps the camera's angle (or first turns it to look at the objects, `aim=True`), then moves it along
its view axis (an orthographic camera changes its ortho_scale instead) and, with `center=True`, sideways and up in its
own plane, until the objects' bounding boxes, at every frame given, just fit the band from `bsa.layout.target_area`:
by default the free space between the badge row at the top and the caption band, `margin` px inside; or explicit
screen rows (`px_top`, `px_bottom`). Pixels are in the 1080x1920 layout frame, top-left origin, projected with the
camera's real lens and sensor fit for that frame. `frame_box` does the same for a box given by its corners (in the
world, or in one object's own coordinates): a part of one long mesh, with no helper object. `move` keys a smooth
orbit or dolly between two framed poses, framing a pose every MOVE_EVERY frames so the subject stays in the band.
`follow` keys a camera that tracks (stays put and turns) or rides (moves with) a moving part, smoothed, in the band.

The camera never ends up under the ground (`above_ground=True`, the default): centring slides it along its own up
axis, so a camera aimed up from low down sinks; when the first surface straight above it faces up, or there is a
surface above and none below that acts as ground for the subject (flat, broad, under it), it is lifted just above
that ground's top and pitched so the subject is still framed. Open water (objects tagged "surface") and the framed
objects themselves are never ground, and with no ground at all (a subject floating in empty space) nothing is done.
When that cannot work (the subject wholly under the ground, or still under it after a few lifts) FramingError names
the shot, the camera and the ground.

An answer that cannot be right comes with a warning (`FramingWarning`, and in the result's "warnings"): the camera
sent more than FAR_FACTOR times the framed box's distance or size away (the objects were not where it looked: a
parent moved or turned them), the box ending up outside the view, behind the clip start or past the clip end, the
camera under the ground (only with above_ground=False), or the fit not settling. The warnings are also kept on the
scene (`framing_notes`), so the scene checks list them as "framing_call" findings on the review pages
(`framing_findings`) and the preview chain's build step names them. A box with no size (no geometry) is refused with
ValueError.

The objects are measured as the scene they are in places them (keys, parents, drivers), also in a scene that is not
the window's (script mode builds each scene in turn). Without `scene=`, the camera's scene that holds every object is
used (a camera can be in several scenes). Objects the scene does not have are refused with ValueError: their own
unevaluated place (often the origin) would give a wrong camera with no warning.
"""

from __future__ import annotations

import json
import math
import warnings

import bpy
import numpy as np
from mathutils import Matrix, Vector

from bsa import smoothing, viewer
from bsa.blender.bake import is_overlay, is_surface
from bsa.findings import FRAMING_PROP, framing_notes
from bsa.image_checks import Finding
from bsa.layout import H, Rect, W, caption_band, target_area

_DEFAULT = object()
ITERATIONS = 60
TOL_PX = 0.5
FAR_FACTOR = 5.0  # the camera moved more than this times max(box size, its new distance to the box): absurd
MIN_SIZE = 1e-6  # a box smaller than this (metres) each way has no size to frame
GROUND_CLEARANCE = 0.03  # a lifted camera sits this times the box's size above the ground's top (at least 5 cm)
GROUND_TRIES = 4  # lifts before a camera still under the ground is an error
NOTES_PROP = FRAMING_PROP  # the scene's custom property keeping its framing warnings (JSON)


class FramingWarning(UserWarning):
    """frame_objects / frame_box gave an answer that cannot be what was meant (see the module docstring)."""


class FramingError(RuntimeError):
    """frame_objects / frame_box cannot keep the camera above the ground with the subject framed."""


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
    """(pixel coordinates of world points for a camera-to-world matrix, the camera's focal length in px), in
    the 1080x1920 frame."""
    depsgraph = _depsgraph(scene)
    # The short's 1080x1920 frame with square pixels, whatever the scene's render resolution says now: the layout's
    # pixels are that frame's, and the camera's lens and sensor fit are read for it.
    proj = np.array(cam.calc_matrix_camera(depsgraph, x=W, y=H, scale_x=1, scale_y=1))

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


def _area(safe_rect, avoid, margin, top, px_top, px_bottom, px_left, px_right) -> Rect:
    """The band to frame in (bsa.layout.target_area), from frame_objects' options."""
    extra = {} if top is _DEFAULT else {"top": top}
    return target_area(safe_rect, caption_band() if avoid is _DEFAULT else avoid, margin, px_top=px_top,
                       px_bottom=px_bottom, px_left=px_left, px_right=px_right, **extra)  # fmt: skip


def frame_objects(cam, objs, frames=None, safe_rect: Rect | None = None, avoid=_DEFAULT, margin: float = 24,
                  aim: bool = False, center: bool = True, scene=None, key: int | None = None, top=_DEFAULT,
                  px_top: float | None = None, px_bottom: float | None = None, px_left: float | None = None,
                  px_right: float | None = None, shift: bool = False, above_ground: bool = True,
                  shot: str | None = None) -> dict:  # fmt: skip
    """Move `cam` so the bounding boxes of `objs` (at each of `frames`, default the current frame) fill the band to
    frame heroes in (`bsa.layout.target_area`: by default the free space between the top cards and the caption band,
    24 px inside) as tightly as fits: the box touches the band on its limiting sides and stays inside it, centred.

    - `avoid`: the band to keep clear (default `bsa.layout.caption_band()`, rows about 1165-1395); None for none.
    - `top`: the top cards to keep clear (default `bsa.layout.top_cards_band()`, the badge row, rows about 221-297;
      `top_cards_band(labels=2)` for a badge and two stacked labels; None for none).
    - `px_top` / `px_bottom`: explicit screen rows (pixels in the 1080x1920 frame, from the top) for the band's top
      and bottom instead: a tall subject's top and bottom land on them (either alone: the other is the default's).
      `px_left` / `px_right`: explicit columns too, for a whole target box per shot (rows 820-1250 under a title
      card, say).
    - `shift`: centre with the lens shift (cam.data.shift_x / shift_y) instead of moving or turning the camera: it
      stays where the distance fit puts it, on its own view axis, so it never slides down into the ground.
    - `aim`: first turn the camera to look at the boxes' centre (world Z up); else its angle is kept.
    - `center`: slide the camera in its own plane so the boxes are centred in the band; else only the distance (or
      ortho scale) changes and the boxes stay where they project now, scaled about the frame centre.
    - `above_ground` (default True): a camera the fit would put under the ground (or inside the solid under it) is
      lifted just above the ground's top and pitched so the subject is still framed in the band. A subject wholly
      under the ground, or ground it cannot get above, raises FramingError naming the shot, camera and ground.
      False: the old behaviour, only a warning.
    - `shot`: the shot id, for messages and for the framing findings the checks list (see framing_findings).
    - `key`: a frame to key the framed position at (location, rotation and, for an orthographic camera,
      ortho_scale). Without it nothing is keyed.

    The camera's real lens and sensor fit are used for the short's 1080x1920 frame, whatever the scene's render
    resolution is while building.

    Safe on an animated camera: it starts from the pose the camera has now (an unkeyed change included), measuring
    the objects at other `frames` neither moves the camera nor changes the scene's frame (it comes back to the frame
    it was on), and existing keys are left alone. The result is a plain transform, though: on a camera with keys,
    the next frame change snaps it back to them, so pass `key=FRAME` (or key it yourself) right after. To frame
    several positions on one camera, call it once per position with its own `key`: no need to clear the animation.

    Sets cam.matrix_world (and ortho_scale for an orthographic camera) and returns {"location", "box" (x0, y0, x1, y1
    px of everything framed), "area" (x0, y0, x1, y1 px), "distance" (along the view axis to the boxes' centre),
    "shift" ((shift_x, shift_y) of the lens), "lifted" (None, or {"ground", "by"} when it was lifted out of the ground), "warnings" (why the answer cannot be
    right, each also a FramingWarning and kept on the scene for the checks; empty when it is fine)}. A box with no
    size (objects with no geometry) raises ValueError."""
    objs = list(objs)
    scene = _scene_of(cam, objs, scene)
    area = _area(safe_rect, avoid, margin, top, px_top, px_bottom, px_left, px_right)
    pose = _pose(cam)  # the pose now: frame changes while measuring would snap a keyed camera back to its keys
    pts = _corners(objs, scene, list(frames) if frames else [None])
    _set_pose(cam, pose)
    what = ", ".join(repr(o.name) for o in objs[:4]) + (f" and {len(objs) - 4} more" if len(objs) > 4 else "")
    return _frame(cam, pts, scene, what, area, aim, center, key, above_ground, shot, shift, objs)


def frame_box(cam, lo, hi, of=None, frames=None, safe_rect: Rect | None = None, avoid=_DEFAULT, margin: float = 24,
              aim: bool = False, center: bool = True, scene=None, key: int | None = None, top=_DEFAULT,
              px_top: float | None = None, px_bottom: float | None = None, px_left: float | None = None,
              px_right: float | None = None, shift: bool = False, above_ground: bool = True,
              shot: str | None = None) -> dict:  # fmt: skip
    """`frame_objects` for the box with corners `lo` and `hi`: in the world, or with `of` (an object) in that object's
    own coordinates, following it at each of `frames` (keys, parents, constraints). Frames a part of one long mesh
    (the last metre of a rail, one end of a beam) without a helper object. Same options and result."""
    scene = _scene_of(cam, [of] if of is not None else [], scene)
    area = _area(safe_rect, avoid, margin, top, px_top, px_bottom, px_left, px_right)
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
    return _frame(cam, pts, scene, what, area, aim, center, key, above_ground, shot, shift,
                  [of] if of is not None else [])


MOVE_EVERY = 2  # camera.move keys a framed pose every this many frames


def _smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def _box_corners(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return np.array([(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def _angles(v: np.ndarray) -> tuple[float, float, float]:
    """(azimuth, elevation, length) of a vector: radians, azimuth about world Z from +X, elevation above the XY
    plane."""
    r = float(np.linalg.norm(v))
    if r < 1e-9:
        return 0.0, 0.0, 0.0
    return math.atan2(v[1], v[0]), math.asin(max(-1.0, min(1.0, v[2] / r))), r


def _clear_between(cam, f0: int, f1: int) -> None:
    """Remove the camera's (and its lens data's) transform, ortho and shift keys strictly between f0 and f1."""
    from bsa.blender.extremes import fcurves

    keyed = set(_POSE) | {"ortho_scale", "shift_x", "shift_y"}
    for owner in (cam, cam.data):
        for fc in fcurves(owner):
            if fc.data_path not in keyed:
                continue
            for k in reversed([k for k in fc.keyframe_points if f0 < k.co[0] < f1]):
                fc.keyframe_points.remove(k)


MOVE_SMOOTH = 6.0  # frames: the camera's distance along a move is eased over about this long (a Gaussian's sigma)


def _ease_distances(cam, poses, every: int, above_ground: bool, depth=None, sigma: float | None = None) -> list[float]:
    """Ease the distance of each keyed pose of a move: the fit takes the larger of the width and the height limit, so
    its distance turns a corner where one takes over from the other (a bump in the dolly). The distances along each
    pose's view axis are smoothed (never nearer than fitted, so the subject stays in the band; the first and last
    keys keep their framing) and the location is keyed again. `depth`: the amounts to smooth instead (how far back
    along its view axis each pose is, from any base: camera.follow's backing off), `sigma` in keys (default
    MOVE_SMOOTH / every). Returns the camera's distance to the framed box's centre at each key."""
    out = []
    if cam.data.type != "ORTHO" and len(poses) > 2:
        if depth is None:
            depth = np.array([float((c - m[:3, 3]) @ -(m[:3, 2] / np.linalg.norm(m[:3, 2]))) for _f, m, c in poses])
        depth = np.asarray(depth, dtype=np.float64)
        # mirrored through the end values: the first and last keys stay put
        eased = smoothing.gaussian(depth, MOVE_SMOOTH / max(1, every) if sigma is None else sigma)
        for (f, m, _c), d0, d1 in zip(poses, depth, eased, strict=True):
            back = m[:3, 2] / np.linalg.norm(m[:3, 2])
            step = back * max(0.0, float(d1 - d0))
            if not step.any() or (above_ground and step[2] < 0):
                continue  # already as far as it needs, or easing would sink a camera the fit kept above the ground
            m[:3, 3] += step
            cam.matrix_world = Matrix(m.tolist())
            cam.keyframe_insert("location", frame=f)
    for _f, m, c in poses:
        out.append(float(np.linalg.norm(m[:3, 3] - c)))
    return out


def move(cam, objs, f0: int, f1: int, orbit: float = 0.0, tilt: float = 0.0, to=None, end_at=None,
         every: int = MOVE_EVERY, scene=None, shot: str | None = None, safe_rect: Rect | None = None,
         avoid=_DEFAULT, margin: float = 24, top=_DEFAULT, px_top: float | None = None,
         px_bottom: float | None = None, px_left: float | None = None, px_right: float | None = None,
         shift: bool = False, above_ground: bool = True) -> dict:  # fmt: skip
    """Key a smooth camera move from frame `f0` to `f1` that keeps `objs` framed in the band the whole way: an orbit
    (`orbit` degrees about the world Z axis through the subject, counter-clockwise seen from above; `tilt` degrees up
    or down), a dolly or push-in (`to`: other objects, a part, framed at the end: the framed box slides from `objs`'
    box to theirs), or both. `end_at`: a camera position to end at instead of `orbit` / `tilt` (its direction from the
    subject is used, the shorter way round; the distance still comes from the fit).

    It starts from the camera's pose now (frame the start with frame_objects first, or set its angle), so the move
    carries on from the shot's start without a jump. Every `every` frames (and at `f1`) that pose is turned about the
    subject's centre on a smoothstep (slow out, slow in), carried along with the subject's box at that frame (measured
    where the objects are then: keys, parents) and framed into the band exactly as frame_objects does (the same fit, band options and
    above-ground lift), and keyed. Keying only the two ends of an orbit lets location and rotation interpolate apart,
    so the camera swings off the subject in between; framing each pose densely keeps it in the band, and the
    distance changes smoothly (no bumps between sparse refits). The camera's existing keys strictly between `f0` and
    `f1` are removed first; keys outside stay. Euler rotations are keyed continuous (no 360 degree flips).

    Returns {"keys": the keyed frames, "start" / "end": (azimuth, elevation) in degrees of the camera seen from the
    subject, "distances": the camera's distance to the framed box's centre at each key, "warnings": the framing
    warnings, each once (also issued as FramingWarning)}."""
    objs = list(objs)
    to = list(to) if to else []
    if f1 <= f0:
        raise ValueError(f"camera.move: f1 ({f1}) must come after f0 ({f0})")
    scene = _scene_of(cam, objs + to, scene)
    area = _area(safe_rect, avoid, margin, top, px_top, px_bottom, px_left, px_right)
    every = max(1, int(every))
    frames = list(range(f0, f1 + 1, every))
    if frames[-1] != f1:
        frames.append(f1)
    pose = _pose(cam)  # the pose now: measuring other frames would snap a keyed camera back to its keys
    boxes = []
    for f in frames:  # the framed box at each key: objs', sliding to `to`'s
        a = _corners(objs, scene, [f])
        lo, hi = a.min(0), a.max(0)
        if to:
            b = _corners(to, scene, [f])
            s = _smoothstep((f - f0) / (f1 - f0))
            lo, hi = lo + (b.min(0) - lo) * s, hi + (b.max(0) - hi) * s
        boxes.append((lo, hi))
    _set_pose(cam, pose)
    _depsgraph(scene).update()
    m0 = np.array(cam.matrix_world, dtype=np.float64)
    c0 = (boxes[0][0] + boxes[0][1]) / 2
    az0, el0, r0 = _angles(m0[:3, 3] - c0)
    if r0 < 1e-9:
        raise ValueError(f"camera.move: camera {cam.name!r} sits at the subject's centre: give it a start position")
    if end_at is not None:
        az1, el1, _ = _angles(np.asarray(end_at, dtype=np.float64) - (boxes[-1][0] + boxes[-1][1]) / 2)
        turn, rise = (az1 - az0 + math.pi) % (2 * math.pi) - math.pi, el1 - el0  # the shorter way round
    else:
        turn, rise = math.radians(orbit), math.radians(tilt)
    limit = math.radians(89)
    rise = max(-limit, min(limit, el0 + rise)) - el0
    what = ", ".join(repr(o.name) for o in objs[:4]) + (f" and {len(objs) - 4} more" if len(objs) > 4 else "")
    _clear_between(cam, f0, f1)
    euler = cam.rotation_mode not in ("QUATERNION", "AXIS_ANGLE")
    prev = None
    found: list[str] = []
    poses = []
    for f, (lo, hi) in zip(frames, boxes, strict=True):
        s = _smoothstep((f - f0) / (f1 - f0))
        rot = np.array(Matrix.Rotation(turn * s, 3, "Z"))  # the start pose, turned about the subject ...
        v = rot @ (m0[:3, 3] - c0)
        h = np.cross(v, (0.0, 0.0, 1.0))
        if np.linalg.norm(h) > 1e-9 and rise:  # ... and raised or lowered over it
            rot = np.array(Matrix.Rotation(rise * s, 3, Vector((h / np.linalg.norm(h)).tolist()))) @ rot
        centre = (lo + hi) / 2
        m = np.eye(4)
        m[:3, :3] = rot @ m0[:3, :3]
        m[:3, 3] = centre + rot @ (m0[:3, 3] - c0)
        cam.matrix_world = Matrix(m.tolist())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _frame(cam, _box_corners(lo, hi), scene, what, area, False, True, f, above_ground, shot, shift,
                   objs + to)  # fmt: skip
        found += [str(w.message) for w in caught if issubclass(w.category, FramingWarning)]
        if euler:  # the same turn as the key before, not one 360 degrees away
            e = cam.rotation_euler.copy() if prev is None else cam.matrix_basis.to_euler(cam.rotation_mode, prev)
            cam.rotation_euler = e
            cam.keyframe_insert("rotation_euler", frame=f)
            prev = e.copy()
        elif cam.rotation_mode == "QUATERNION":  # the same sign as the key before: no flick between opposite keys
            q = cam.rotation_quaternion.copy()
            if prev is not None and prev.dot(q) < 0:
                cam.rotation_quaternion = -q
                cam.keyframe_insert("rotation_quaternion", frame=f)
            prev = cam.rotation_quaternion.copy()
        poses.append((f, np.array(cam.matrix_world, dtype=np.float64), centre))
    distances = _ease_distances(cam, poses, every, above_ground)
    _f, m_end, c_end = poses[-1]
    end = _angles(m_end[:3, 3] - c_end)
    found = list(dict.fromkeys(found))
    for w in found:
        warnings.warn(w, FramingWarning, stacklevel=2)
    return {"keys": frames, "start": (math.degrees(az0), math.degrees(el0)),
            "end": (math.degrees(end[0]), math.degrees(end[1])), "distances": distances, "warnings": found}


FOLLOW_TRIES = 30  # camera.follow's steps back along the view axis before a key that still does not fit is warned
TURN_STEPS = 12  # camera.follow's corrections turning the aim point onto the band's centre
TURN_TOL_PX = 0.5  # ... and how near (px) it must end up, else a warning


def _matrices(obj, scene, frames) -> list[np.ndarray]:
    """`obj`'s evaluated world matrix at each frame (keys, parents, constraints); the scene comes back to its frame."""
    depsgraph = _depsgraph(scene)
    original = scene.frame_current
    out = []
    try:
        for f in frames:
            scene.frame_set(f)
            depsgraph.update()
            out.append(np.array(obj.evaluated_get(depsgraph).matrix_world, dtype=np.float64))
    finally:
        scene.frame_set(original)
    return out


def _turn_to(project, focal: float, m: np.ndarray, point: np.ndarray, want: np.ndarray) -> tuple[np.ndarray, bool]:
    """(`m` turned about its own position (world Z up, no roll) so the world `point` projects onto the pixel `want`,
    whether it got there: within TURN_TOL_PX, in front of the camera)."""
    m = _look_at(m, point)
    for step in range(TURN_STEPS + 1):
        x, y, depth = project(m, point[None])
        off = (float(x[0]) - want[0], float(y[0]) - want[1])
        if max(abs(off[0]), abs(off[1])) < 0.05 or step == TURN_STEPS:
            break
        right = m[:3, 0] / np.linalg.norm(m[:3, 0])
        yaw = np.array(Matrix.Rotation(-math.atan2(off[0], focal), 3, "Z"))  # point right of want: turn right
        pitch = np.array(Matrix.Rotation(-math.atan2(off[1], focal), 3, Vector(right.tolist())))  # below: look down
        m = m.copy()
        m[:3, :3] = yaw @ pitch @ m[:3, :3]
    return m, bool(depth[0] > 0 and max(abs(off[0]), abs(off[1])) < TURN_TOL_PX)


def _back_off(project, focal: float, m: np.ndarray, pts: np.ndarray, area: Rect, aim: np.ndarray,
              want: np.ndarray) -> tuple[np.ndarray, bool, bool]:  # fmt: skip
    """(`m` moved back along its view axis, turned to `aim` again each step, just until the world points `pts` fit
    `area`; never nearer, whether they fit, whether the last turn got there)."""
    turned = True
    half = np.array([area.w / 2, area.h / 2])
    span = max(float(np.ptp(pts, axis=0).max()), 1e-3)
    for _ in range(FOLLOW_TRIES):
        back = m[:3, 2] / np.linalg.norm(m[:3, 2])
        local_depth = (pts - m[:3, 3]) @ -back
        if local_depth.min() < 0.05 * span:  # behind or too close
            step = span + 0.1 - float(local_depth.min())
        else:
            x, y, _depth = project(m, pts)
            reach = max(max(want[0] - x.min(), x.max() - want[0]) / half[0],
                        max(want[1] - y.min(), y.max() - want[1]) / half[1])  # fmt: skip
            if reach <= 1 + 1e-4:
                return m, True, turned
            step = max(float((aim - m[:3, 3]) @ -back) * (reach - 1) * 1.01, 1e-4)
        m = m.copy()
        m[:3, 3] += back * step
        m, turned = _turn_to(project, focal, m, aim, want)
    return m, False, turned


def follow(cam, target, f0: int, f1: int, mode: str = "track", offset=None, smooth: float = MOVE_SMOOTH,
           band: bool = True, every: int = MOVE_EVERY, scene=None, shot: str | None = None,
           safe_rect: Rect | None = None, avoid=_DEFAULT, margin: float = 24, top=_DEFAULT,
           above_ground: bool = True) -> dict:  # fmt: skip
    """Key the camera from frame `f0` to `f1` to follow a moving part (`target`: an object, or a list of objects
    framed as their joint box), keeping it in the band (`bsa.layout.target_area`, the same options as frame_objects).

    - `mode="track"`: the camera stays where it is now and turns to keep the part's box centred in the band (a
      gear's tooth going round, a slider crossing the frame).
    - `mode="ride"`: the camera moves with the part, keeping `offset` from it: a vector in the first target's own
      space (default: where the camera is now, in that space at `f0`), so it rides up and down with a piston and
      turns with a wheel. It aims at the box's centre as in track.

    Every `every` frames (and at `f1`) the box is measured where the part is then (keys, parents) and the camera is
    keyed (location and rotation; Euler keys without flips). The aim point (the box's centre) and, riding, the
    position are smoothed with the Gaussian `camera.move` eases its distance with (`smooth` frames, its sigma; 0 for
    none): no jitter from a part that shakes or turns a corner, the first and last keys kept. With `band` (the
    default), a key where the box does not fit the band backs the camera off along its view axis just enough, never
    nearer than the smoothed position, and those backing-off distances are eased too (never nearer than fitted).
    With `above_ground`, a camera under the ground is lifted as frame_objects does, which refits that key into the
    band (centred, the camera moved and turned) even with `band=False`. Every pose is worked out before any key
    changes (an error leaves the camera's keys as they were); then its keys strictly between `f0` and `f1` are
    replaced, and keys outside stay. A perspective camera only (an orthographic one is refused: it has no distance
    to back off along). A key the camera could not turn to aim at is warned about.

    Returns {"keys": the keyed frames, "distances": the camera's distance to the box's centre at each key,
    "warnings": the framing warnings, each once (also issued as FramingWarning and kept on the scene with `shot`
    for the checks)}."""
    objs = list(target) if isinstance(target, (list, tuple)) else [target]
    if mode not in ("track", "ride"):
        raise ValueError(f"camera.follow: mode {mode!r} is not 'track' (the camera stays put and turns) or 'ride' "
                         "(it moves with the part)")  # fmt: skip
    if f1 <= f0:
        raise ValueError(f"camera.follow: f1 ({f1}) must come after f0 ({f0})")
    if not objs:
        raise ValueError("camera.follow needs a target object")
    if cam.data.type == "ORTHO":
        raise ValueError(f"camera.follow: camera {cam.name!r} is orthographic; follow needs a perspective camera "
                         "(frame each pose with frame_objects and key=)")  # fmt: skip
    scene = _scene_of(cam, objs, scene)
    area = _area(safe_rect, avoid, margin, top, None, None, None, None)
    every = max(1, int(every))
    frames = list(range(f0, f1 + 1, every))
    if frames[-1] != f1:
        frames.append(f1)
    what = ", ".join(repr(o.name) for o in objs[:4]) + (f" and {len(objs) - 4} more" if len(objs) > 4 else "")
    pose = _pose(cam)  # the pose now: measuring other frames would snap a keyed camera back to its keys
    boxes = [_corners(objs, scene, [f]) for f in frames]
    lead = _matrices(objs[0], scene, frames) if mode == "ride" else None
    _set_pose(cam, pose)
    _depsgraph(scene).update()
    for pts in boxes:
        if not np.all(np.isfinite(pts)) or float(np.ptp(pts, axis=0).max()) < MIN_SIZE:
            raise ValueError(f"camera.follow: {what} has no size to frame (no geometry, or every point in one "
                             "place): follow objects with geometry")  # fmt: skip
    m0 = np.array(cam.matrix_world, dtype=np.float64)
    sigma = max(0.0, float(smooth)) / every
    aims = smoothing.gaussian(np.array([(b.min(0) + b.max(0)) / 2 for b in boxes]), sigma)
    if mode == "track":
        places = np.repeat(m0[None, :3, 3], len(frames), axis=0)
    else:
        local = np.r_[np.asarray(offset, dtype=np.float64), 1.0] if offset is not None else (
            np.linalg.inv(lead[0]) @ np.r_[m0[:3, 3], 1.0])  # fmt: skip
        places = smoothing.gaussian(np.array([(mw @ local)[:3] for mw in lead]), sigma)
    project, focal = _projector(cam, scene)
    want = np.array([area.x + area.w / 2, area.y + area.h / 2])
    skip = frozenset(o.name for o in objs)
    planned = []  # every pose first: an error part way (FramingError from the lift) leaves the camera's keys alone
    for f, pts, aim, place in zip(frames, boxes, aims, places, strict=True):
        m = m0.copy()
        m[:3, 3] = place
        m, turned = _turn_to(project, focal, m, aim, want)
        settled = True
        if band:
            m, settled, turned_back = _back_off(project, focal, m, pts, area, aim, want)
            turned = turned and turned_back
        if above_ground:
            m, settled, _lifted = _lift(cam, scene, m, pts, area, True, settled, what, shot, False, skip)
        planned.append((f, pts, aim, place, m, settled, turned))
    _clear_between(cam, f0, f1)
    euler = cam.rotation_mode not in ("QUATERNION", "AXIS_ANGLE")
    prev = None
    found: list[str] = []
    poses = []
    backs = []
    for f, pts, aim, place, m, settled, turned in planned:
        back = m[:3, 2] / np.linalg.norm(m[:3, 2])
        backs.append(float((m[:3, 3] - place) @ back))
        cam.matrix_world = Matrix(m.tolist())
        cam.keyframe_insert("location", frame=f)
        if euler:  # the same turn as the key before, not one 360 degrees away
            e = cam.rotation_euler.copy() if prev is None else cam.matrix_basis.to_euler(cam.rotation_mode, prev)
            cam.rotation_euler = e
            cam.keyframe_insert("rotation_euler", frame=f)
            prev = e.copy()
        elif cam.rotation_mode == "QUATERNION":  # the same sign as the key before: no flick between opposite keys
            q = cam.rotation_quaternion.copy()
            if prev is not None and prev.dot(q) < 0:
                cam.rotation_quaternion = -q
            cam.keyframe_insert("rotation_quaternion", frame=f)
            prev = cam.rotation_quaternion.copy()
        else:
            cam.keyframe_insert("rotation_axis_angle", frame=f)
        x, y, depth = project(m, pts)
        box = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
        msgs = _absurd(cam, what, pts, place, m[:3, 3], box, depth, settled, False)
        below = None if above_ground else _under(scene, m[:3, 3], what, pts, skip)
        if below:
            msgs.insert(len(msgs) - (not settled), below)
        if not turned:
            msgs.append(f"camera.follow: the camera could not turn to put {what}'s centre on the band's centre at "
                        f"frame {f} (it aims {_fmt(aim)} from {_fmt(m[:3, 3])}): look at the still")  # fmt: skip
        _note(scene, cam, f, shot, msgs)
        found += msgs
        poses.append((f, m, aim))
    distances = _ease_distances(cam, poses, every, above_ground, depth=backs, sigma=sigma)
    found = list(dict.fromkeys(found))
    for w in found:
        warnings.warn(w, FramingWarning, stacklevel=2)
    return {"keys": frames, "distances": distances, "warnings": found}


def _fmt(v) -> str:
    return "(" + ", ".join(f"{float(x):.2f}" for x in v) + ")"


def _shift_unit(cam) -> float:
    """Pixels of the 1080x1920 frame that a lens shift of 1 moves the picture by (Blender's shift is in units of the
    sensor-fit dimension: the larger one for AUTO)."""
    fit = cam.data.sensor_fit
    return float(W if fit == "HORIZONTAL" else H if fit == "VERTICAL" else max(W, H))


def _fit(cam, scene, m: np.ndarray, pts: np.ndarray, area: Rect, center: bool, floor: float | None = None,
         shift: bool = False):
    """(m moved so the world points `pts` fill `area`, whether the fit settled). With `floor` (a height) the camera
    stays at or above it: the vertical centring turns (pitches) the camera instead of sliding it along its up axis,
    and a dolly that would sink it below the floor moves it level instead. With `shift` the centring is done with
    the lens shift (cam.data.shift_x / shift_y), the camera keeps its angle and only moves along its view axis."""
    m = m.copy()
    ortho = cam.data.type == "ORTHO"
    project, focal = _projector(cam, scene)
    want = np.array([area.x + area.w / 2, area.y + area.h / 2])
    span = max(float(np.ptp(pts, axis=0).max()), 1e-3)
    for _ in range(ITERATIONS):
        rot = m[:3, :3] / np.linalg.norm(m[:3, :3], axis=0)
        right, up, back = rot[:, 0], rot[:, 1], rot[:, 2]
        local_depth = (pts - m[:3, 3]) @ -back
        if not ortho and local_depth.min() < 0.05 * span:  # behind or too close
            m[:3, 3] += back * (span + 0.1 - local_depth.min())
            if floor is not None:
                m[2, 3] = max(m[2, 3], floor)
            continue
        x, y, depth = project(m, pts)
        box = np.array([x.min(), y.min(), x.max(), y.max()])
        size = box[2:] - box[:2]
        s = max(size[0] / area.w, size[1] / area.h)
        mid = (box[:2] + box[2:]) / 2
        off = (mid - want) if center else np.zeros(2)
        if abs(s - 1) * max(area.w, area.h) < TOL_PX and np.abs(off).max() < TOL_PX:
            return m, True
        d = float(np.mean(depth))
        per_px = 1 / focal if ortho else d / focal  # world units per pixel at the boxes' depth
        if shift:
            unit = _shift_unit(cam)  # a positive shift moves the picture left (x) or down (y) on screen
            cam.data.shift_x += off[0] / unit
            cam.data.shift_y -= off[1] / unit
            project, focal = _projector(cam, scene)
        elif floor is None:
            m[:3, 3] += right * off[0] * per_px - up * off[1] * per_px  # pixel y grows downwards
        else:
            m[:3, 3] += right * off[0] * per_px
            turn = -math.atan2(off[1] * per_px, d)  # subject too low on screen: look down
            m[:3, :3] = np.array(Matrix.Rotation(turn, 3, Vector(right.tolist()))) @ m[:3, :3]
        if ortho:
            cam.data.ortho_scale *= s
            project, focal = _projector(cam, scene)
        else:
            step = back * d * (s - 1)
            if floor is not None and m[2, 3] + step[2] < floor:
                flat = np.array([back[0], back[1], 0.0])
                n = float(np.linalg.norm(flat))
                if n > 0.2:  # the same change of distance, moving level
                    step = flat / n * (d * (s - 1) / n)
            m[:3, 3] += step
        if floor is not None:
            m[2, 3] = max(m[2, 3], floor)
    return m, False


def _where(scene, shot) -> str:
    return f"shot {shot}, scene {scene.name!r}" if shot else f"scene {scene.name!r}"


def _frame(cam, pts, scene, what, area, aim, center, key, above_ground=True, shot=None, shift=False,
           framed=()) -> dict:
    """Move the camera so the world points `pts` fill the framing band `area` (see frame_objects). `framed`: the
    objects framed, never taken for the ground."""
    size = float(np.ptp(pts, axis=0).max()) if len(pts) else 0.0
    if not np.all(np.isfinite(pts)) or size < MIN_SIZE:
        raise ValueError(f"frame_objects: {what} has no size to frame (no geometry, or every point in one place): "
                         "frame objects with geometry, or a box with frame_box")  # fmt: skip
    _depsgraph(scene).update()  # matrix_world from the restored pose (no frame change: keys are not read again)
    ortho = cam.data.type == "ORTHO"
    m = np.array(cam.matrix_world, dtype=np.float64)
    start = m[:3, 3].copy()
    if aim:
        m = _look_at(m, (pts.min(0) + pts.max(0)) / 2)
    if shift:
        cam.data.shift_x = cam.data.shift_y = 0.0  # centred from the camera's own axis each time
    m, settled = _fit(cam, scene, m, pts, area, center, shift=shift)
    lifted = None
    skip = frozenset(o.name for o in framed)
    if above_ground:
        m, settled, lifted = _lift(cam, scene, m, pts, area, center, settled, what, shot, shift, skip)
    cam.matrix_world = Matrix(m.tolist())
    if key is not None:
        cam.keyframe_insert("location", frame=key)
        cam.keyframe_insert(cam.rotation_mode == "QUATERNION" and "rotation_quaternion" or (
            "rotation_axis_angle" if cam.rotation_mode == "AXIS_ANGLE" else "rotation_euler"), frame=key)  # fmt: skip
        if ortho:
            cam.data.keyframe_insert("ortho_scale", frame=key)
        if shift:
            cam.data.keyframe_insert("shift_x", frame=key)
            cam.data.keyframe_insert("shift_y", frame=key)
    project, _focal = _projector(cam, scene)
    x, y, depth = project(m, pts)
    box = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
    found = _absurd(cam, what, pts, start, m[:3, 3], box, depth, settled, ortho)
    below = None if above_ground else _under(scene, m[:3, 3], what, pts, skip)
    if below:
        found.insert(len(found) - (not settled), below)
    _note(scene, cam, key, shot, found)
    for w in found:
        warnings.warn(w, FramingWarning, stacklevel=3)
    return {
        "location": tuple(float(v) for v in m[:3, 3]),
        "box": box,
        "area": (area.x, area.y, area.right, area.bottom),
        "distance": float(np.mean(depth)),
        "shift": (float(cam.data.shift_x), float(cam.data.shift_y)),
        "lifted": lifted,
        "warnings": found,
    }


def _lift(cam, scene, m, pts, area, center, settled, what, shot, shift=False, skip=frozenset()):
    """(m, settled, lifted): a camera under the ground lifted just above the ground's top and refitted with its height
    held there (pitching instead of sinking), until no ground is above it. FramingError when the subject is wholly
    under the ground or the camera is still under it after GROUND_TRIES tries. `skip`: names of the framed objects
    (never ground)."""
    lifted = None
    size = float(np.ptp(pts, axis=0).max())
    clearance = max(GROUND_CLEARANCE * size, 2 * cam.data.clip_start, 0.05)
    from_z = float(m[2, 3])
    for attempt in range(GROUND_TRIES + 1):
        ground = _ground(scene, m[:3, 3], pts, skip)
        if ground is None:
            break
        top, name = ground
        if attempt == GROUND_TRIES or float(pts[:, 2].max()) <= top:
            why = (f"{what} is wholly under its top (z {top:.2f}, the subject's highest point {pts[:, 2].max():.2f})"
                   if float(pts[:, 2].max()) <= top else f"after {GROUND_TRIES} lifts it is still under it")  # fmt: skip
            raise FramingError(f"frame_objects ({_where(scene, shot)}): camera {cam.name!r} ends up under the ground "
                               f"{name!r} (at {_fmt(m[:3, 3])}) and cannot be lifted above it with {what} still "
                               f"framed: {why}. Move the subject or the ground, or frame from another angle")  # fmt: skip
        floor = top + clearance
        lifted = {"ground": name}
        m = m.copy()
        m[2, 3] = max(m[2, 3], floor)
        m, settled = _fit(cam, scene, m, pts, area, center, floor=floor, shift=shift)
    if lifted is not None:
        lifted["by"] = float(m[2, 3]) - from_z
    return m, settled, lifted


def _first_hit(scene, depsgraph, origin: Vector, direction: Vector, skip=frozenset()):
    """(location, normal, object) of the first surface along the ray that can be ground, or None: overlays, open
    water (tagged "surface") and the objects named in `skip` (the ones being framed) are looked through."""
    for _ in range(64):
        hit, loc, normal, _index, obj, _m = scene.ray_cast(depsgraph, origin, direction)
        if not hit:
            return None
        src = obj.original if obj is not None else None
        if src is None or not (is_overlay(src) or is_surface(src) or src.name in skip):
            return loc, normal, obj
        origin = loc + direction * max(1e-4, 1e-6 * abs(loc.z))  # a glow, water or the subject: look through it
    return None


def _acts_as_ground(obj, pts: np.ndarray) -> bool:
    """Could the (evaluated) object be ground for the subject with world points `pts`: roughly flat and horizontal,
    at least bsa.viewer.GROUND_MIN_SIDE and the subject's width each way, and under the subject's footprint (the
    shape test of bsa.viewer.is_ground, without its height test: a subject sunk into a bank still stands on it)?"""
    if obj is None:
        return False
    mw = np.array(obj.matrix_world, dtype=np.float64)
    corners = np.array([tuple(c) for c in obj.bound_box], dtype=np.float64)
    world = corners @ mw[:3, :3].T + mw[:3, 3]
    lo, hi, s_lo, s_hi = world.min(0), world.max(0), pts.min(0), pts.max(0)
    side = min(hi[0] - lo[0], hi[1] - lo[1])
    if side < max(viewer.GROUND_MIN_SIDE, s_hi[0] - s_lo[0], s_hi[1] - s_lo[1]):
        return False
    if hi[2] - lo[2] > viewer.GROUND_FLAT * side:
        return False
    return bool(lo[0] <= s_hi[0] and s_lo[0] <= hi[0] and lo[1] <= s_hi[1] and s_lo[1] <= hi[1])


def _ground(scene, pos: np.ndarray, pts: np.ndarray | None = None, skip=frozenset()) -> tuple[float, str] | None:
    """(the height of the ground's top above `pos`, the ground's name) when a camera at `pos` is under the ground or
    inside a solid, else None: the first surface straight above it faces up (it is seen from below), or there is a
    surface above it and none below (it is under all the scenery; a camera under a bridge still has the ground or
    water below it) and that object could be ground for the subject `pts` (_acts_as_ground: a floating object
    over a camera in a floorless scene is not ground). Under all the scenery, the top is the first surface facing up
    going up through it. Open water (tagged "surface") and the objects named in `skip` (the framed ones) are never
    ground: they are looked through."""
    depsgraph = _depsgraph(scene)
    origin = Vector(pos.tolist())
    up = Vector((0.0, 0.0, 1.0))
    above = _first_hit(scene, depsgraph, origin, up, skip)
    if above is None:
        return None
    loc, normal, obj = above
    if normal.z <= 0:
        if _first_hit(scene, depsgraph, origin, Vector((0.0, 0.0, -1.0)), skip) is not None:
            return None  # a ceiling or the underside of a bridge, with ground below
        if pts is not None and not _acts_as_ground(obj, pts):
            return None  # nothing below and nothing ground-like above: no ground at all
    top = loc.z
    if normal.z <= 0:
        probe = loc
        for _ in range(32):
            nxt = _first_hit(scene, depsgraph, probe + up * max(1e-4, 1e-6 * abs(probe.z)), up, skip)
            if nxt is None:
                break
            probe, n, _o = nxt
            top = probe.z
            if n.z > 0:
                break
    return float(top), (obj.original.name if obj is not None else "a surface")


def _under(scene, pos: np.ndarray, what: str, pts: np.ndarray | None = None, skip=frozenset()) -> str | None:
    """Why a camera at `pos` is under the ground or inside a solid (see _ground), or None."""
    ground = _ground(scene, pos, pts, skip)
    if ground is None:
        return None
    top, name = ground
    return (f"frame_objects: the camera ends up under {name!r} (at {_fmt(pos)}, {top - pos[2]:.2f} m below it): it "
            f"sees {what} from under the ground or from inside a solid. Leave above_ground on (it lifts the camera), "
            "start the camera higher than the box's centre, or use center=False")  # fmt: skip


def _note(scene, cam, key, shot, found: list[str]) -> None:
    """Keep this framing's warnings on the scene (replacing the last ones for this camera and key), for the checks."""
    notes = framing_notes(scene)
    kept = [n for n in notes if not (n.get("camera") == cam.name and n.get("key") == key)]
    kept += [{"camera": cam.name, "key": key, "shot": shot, "message": w} for w in found]
    if kept != notes or (kept and NOTES_PROP not in scene):
        scene[NOTES_PROP] = json.dumps(kept)


def framing_findings(scene, shot: str | None = None, f0: int | None = None, f1: int | None = None) -> list[Finding]:
    """The framing warnings kept on `scene` as "framing_call" findings (warnings) for shot `shot` (frames f0..f1): those
    framed for that shot, or with no shot given, those keyed inside f0..f1 or not keyed."""
    out = []
    for n in framing_notes(scene):
        if n.get("shot") is not None and shot is not None:
            if n["shot"] != shot:
                continue
        elif n.get("key") is not None and f0 is not None and f1 is not None and not f0 <= n["key"] <= f1:
            continue
        cam = n.get("camera")
        out.append(Finding("framing_call", "warning", cam, n.get("key"), f"camera {cam!r}: {n.get('message')}"))
    return out


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
