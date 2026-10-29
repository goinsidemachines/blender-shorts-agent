"""Where a shot's keyed motion actually changes: frame-check reports a stuck frame only there."""

from __future__ import annotations

import numpy as np

from bsa.blender.bake import is_hero
from bsa.blender.checks import _depsgraph, _view_layer

MOVE_TOL = 1e-6  # a matrix entry changing by less than this (metres, or unitless rotation terms) is not motion


def moving_frames(scene, f0: int, f1: int, shot_id: str | None = None) -> set[int]:
    """The frames f in (f0, f1] at which a hero of the shot (or, when the shot has none, any rendered object) or the
    scene camera is placed differently than at f - 1, from the evaluated world matrices (keys, parents, constraints
    and drivers all count). scene.frame_current is restored afterwards."""
    objs = [o for o in scene.objects if is_hero(o, shot_id)]
    if not objs:
        objs = [o for o in scene.objects if o.type not in ("CAMERA", "LIGHT") and not o.hide_render]
    if scene.camera is not None:
        objs.append(scene.camera)
    original = scene.frame_current
    depsgraph = _depsgraph(scene, _view_layer(scene))
    out: set[int] = set()
    before = None
    try:
        for frame in range(f0, f1 + 1):
            scene.frame_set(frame)
            depsgraph.update()
            now = np.array([np.array(o.evaluated_get(depsgraph).matrix_world) for o in objs], dtype=np.float64)
            if before is not None and before.shape == now.shape and np.abs(now - before).max(initial=0.0) > MOVE_TOL:
                out.add(frame)
            before = now
    finally:
        scene.frame_set(original)
    return out
