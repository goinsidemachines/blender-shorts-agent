"""Where the heroes' large flat faces are seen in a frame, for the shading-gradient frame check
(bsa.image_checks.shading_findings): rays from the shot camera through a grid of pixels, grouped by the hero and the
plane they hit first."""

from __future__ import annotations

import numpy as np
from mathutils import Vector

from bsa.blender.bake import is_hero
from bsa.blender.checks import _depsgraph, _view_layer

GRID_COLUMNS = 60  # rays across the frame (and as many rows as keep them square)
LARGE_SHARE = 0.02  # a face seen in at least this share of the rays is large
PLANE_COS = 0.9994  # hits whose normals are within about 2 degrees ...
PLANE_TOL = 0.01  # ... and whose planes are within this share of the object's size are one flat face
FACING = 0.2  # a face seen more edge-on than this (cosine to the view ray) is left out


def flat_faces(scene, frame: int, size: tuple[int, int], shot_id: str | None = None) -> list[tuple[str, list, list]]:
    """[(hero name, pixel rows, pixel columns)] for each large flat face of a hero of `shot_id` seen at `frame`,
    in an image of `size` (width, height) pixels, top row first. Only the pixels where the face is seen first (not
    hidden behind anything) count. scene.frame_current is restored."""
    cam = scene.camera
    if cam is None:
        return []
    original = scene.frame_current
    try:
        scene.frame_set(frame)
        depsgraph = _depsgraph(scene, _view_layer(scene))
        depsgraph.update()
        cam = scene.camera.evaluated_get(depsgraph)
        heroes = {o.name for o in scene.objects if is_hero(o, shot_id) and o.type == "MESH" and not o.hide_render}
        if not heroes:
            return []
        return _faces(scene, depsgraph, cam, heroes, size)
    finally:
        scene.frame_set(original)


def _faces(scene, depsgraph, cam, heroes: set[str], size: tuple[int, int]) -> list[tuple[str, list, list]]:
    w, h = size
    step = max(2, w // GRID_COLUMNS)
    m = cam.matrix_world
    tr, _, bl, tl = (Vector(c) for c in cam.data.view_frame(scene=scene))
    ortho = cam.data.type == "ORTHO"
    eye = m.translation
    forward = (m.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
    groups: dict[str, list[dict]] = {}
    rays = 0
    for row in range(step // 2, h, step):
        v = row / h
        for col in range(step // 2, w, step):
            u = col / w
            p = m @ (tl + (tr - tl) * u + (bl - tl) * v)
            origin, direction = (p, forward) if ortho else (eye, (p - eye).normalized())
            rays += 1
            hit, loc, normal, _, obj, _ = scene.ray_cast(depsgraph, origin, direction)
            name = getattr(obj, "original", obj).name if hit and obj is not None else None
            if name not in heroes or -normal.dot(direction) < FACING:
                continue
            tol = PLANE_TOL * max(max(obj.dimensions), 1e-3)
            offset = normal.dot(loc)
            for g in groups.setdefault(name, []):
                if g["n"].dot(normal) > PLANE_COS and abs(g["d"] - offset) < tol:
                    break
            else:
                g = {"n": normal.copy(), "d": offset, "rows": [], "cols": []}
                groups[name].append(g)
            g["rows"].append(row)
            g["cols"].append(col)
    least = max(1, LARGE_SHARE * rays)
    return [(name, g["rows"], g["cols"]) for name, gs in groups.items() for g in gs if len(g["rows"]) >= least]


def frame_shading(scene, frames: list, shot_id: str | None, load) -> list:
    """Shading findings (bsa.image_checks.shading_findings) for rendered frames [(frame number, png path)] of one
    shot, `load` decoding a PNG to pixels."""
    from bsa.image_checks import luminance, shading_findings

    out = []
    for frame, png in frames:
        pixels = load(png)
        faces = flat_faces(scene, frame, (pixels.shape[1], pixels.shape[0]), shot_id)
        if faces:
            out += shading_findings(luminance(pixels), [(n, np.array(r), np.array(c)) for n, r, c in faces], frame)
    return out


