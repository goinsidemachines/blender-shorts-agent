"""The motion_at_frame_1 target, in Blender: is something already moving between the first frame and the next? It
compares the active camera's and every rendered mesh's world matrix (an animated parent included) and the animated shape
keys' values at the two frames. Reported as a warning by the scene checks when the target is set (bsa.targets); never
blocks."""

from __future__ import annotations

import numpy as np

from bsa.blender.checks import _depsgraph, _render_meshes, _view_layer
from bsa.blender.loopcheck import _is_animated, _key_animated

EPS = 1e-5  # the largest change of a world-matrix entry (or a shape-key value) that still counts as standing still


def _snapshot(scene, dg, meshes) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    cam = scene.camera
    if cam is not None:
        out["camera"] = np.array(cam.evaluated_get(dg).matrix_world).ravel()
    for obj in meshes:
        ev = obj.evaluated_get(dg)
        out[obj.name] = np.array(ev.matrix_world).ravel()
        keys = getattr(obj.data, "shape_keys", None)
        if keys is not None and _key_animated(keys):
            out[f"{obj.name} shape keys"] = np.array([b.value for b in keys.key_blocks])
    return out


def moving_at_start(scene, first: int) -> list[str]:
    """The names of what moves between frame `first` and `first + 1` ([] = a still start). scene.frame_current is put
    back."""
    original = scene.frame_current
    try:
        view_layer = _view_layer(scene)
        dg = _depsgraph(scene, view_layer)
        meshes = [o for o in _render_meshes(scene, view_layer) if _is_animated(o)]
        snaps = []
        for frame in (first, first + 1):
            scene.frame_set(frame)
            dg.update()
            snaps.append(_snapshot(scene, dg, meshes))
    finally:
        scene.frame_set(original)
    a, b = snaps
    return sorted(k for k in a if k in b and a[k].shape == b[k].shape and float(np.abs(a[k] - b[k]).max()) > EPS)
