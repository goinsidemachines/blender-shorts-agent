"""The loop check, in Blender: does the last frame of a looping short run into the first? It reads the active camera
(world matrix, focal length, focus distance), every animated object (world location, rotation, scale), the animated
shape keys and the pose bones of animated armatures at four frames, and hands them to bsa.blender.loop.compare, which
predicts the frame after the last from the last two and compares it with the first. Findings are warnings (a loop
that jumps is worth fixing, never a reason to refuse a render)."""

from __future__ import annotations

import numpy as np

from bsa.blender import loop
from bsa.blender.checks import _animated, _depsgraph, _render_meshes, _view_layer
from bsa.image_checks import Finding


def _is_animated(obj) -> bool:
    """The object, or any parent of it, moves by itself (keys, drivers, NLA strips, constraints) or has animated shape
    keys."""
    p = obj
    while p is not None:
        if _animated(p) or _key_animated(getattr(getattr(p, "data", None), "shape_keys", None)):
            return True
        p = p.parent
    return False


def _key_animated(key) -> bool:
    ad = key.animation_data if key is not None else None
    return ad is not None and (ad.action is not None or len(ad.drivers) > 0 or len(ad.nla_tracks) > 0)


def _diagonal(scene, view_layer, dg) -> float:
    """The size of the scene: the diagonal of the box round every rendered mesh at the current frame (1 m without
    meshes)."""
    pts = []
    for obj in _render_meshes(scene, view_layer):
        ev = obj.evaluated_get(dg)
        m = np.array(ev.matrix_world)
        for corner in ev.bound_box:
            pts.append((m @ np.array([*corner, 1.0]))[:3])
    if not pts:
        return 1.0
    pts = np.array(pts)
    return float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))) or 1.0


def _sample(scene, dg, objs: list) -> dict:
    """{(object, channel): (kind, value)} at the scene's current frame."""
    out: dict = {}
    cam = scene.camera
    if cam is not None:
        ev = cam.evaluated_get(dg)
        for name, v in loop.channels_of(np.array(ev.matrix_world)).items():
            if not name.endswith("scale"):
                out[("camera", name)] = v
        out[("camera", "focal length")] = ("lens", float(cam.data.lens))
        dof = cam.data.dof
        if dof.use_dof and dof.focus_object is None:
            out[("camera", "focus distance")] = ("focus", float(dof.focus_distance))
    for obj in objs:
        ev = obj.evaluated_get(dg)
        mw = np.array(ev.matrix_world)
        for name, v in loop.channels_of(mw).items():
            out[(obj.name, name)] = v
        keys = getattr(obj.data, "shape_keys", None) if obj.type in ("MESH", "CURVE", "LATTICE") else None
        if keys is not None and _key_animated(keys):
            live = getattr(getattr(ev, "data", None), "shape_keys", None) or keys
            for block in list(live.key_blocks)[1:]:
                out[(obj.name, f"shape key {block.name!r}")] = ("shape", float(block.value))
        if obj.type == "ARMATURE" and ev.pose is not None:
            for bone in ev.pose.bones:
                for name, v in loop.channels_of(mw @ np.array(bone.matrix), f"bone {bone.name!r} ").items():
                    out[(obj.name, name)] = v
    return out


def loop_findings(scene, first: int, last: int) -> list[Finding]:
    """Warnings for what would jump or change speed where frame `last` runs into frame `first`. [] for a short too
    short to measure. scene.frame_current is put back."""
    if last - first + 1 < loop.MIN_FRAMES:
        return []
    original = scene.frame_current
    try:
        view_layer = _view_layer(scene)
        dg = _depsgraph(scene, view_layer)
        objs = [o for o in scene.objects if o.type != "CAMERA" and _is_animated(o)]
        samples: dict[int, dict] = {}
        diagonal = 1.0
        for frame in (first, first + 1, last - 1, last):
            scene.frame_set(frame)
            dg.update()
            if frame == first:
                diagonal = _diagonal(scene, view_layer, dg)
            samples[frame] = _sample(scene, dg, objs)
    finally:
        scene.frame_set(original)
    rows = loop.compare(samples, last, loop.tolerances(diagonal), first=first)
    shown, more = loop.cap(rows)
    found = [Finding("loop", "warning", None if r["obj"] == "camera" else r["obj"], last, loop.describe(r, last, first))
             for r in shown]  # fmt: skip
    if more:
        found.append(Finding("loop", "warning", None, last, f"loop: {more} more channel(s) do not meet; fix the ones "
                             "listed first"))  # fmt: skip
    return found
