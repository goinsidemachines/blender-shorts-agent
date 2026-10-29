"""The moving parts at the ends of their motion: for each hero of a shot (and the parts parented under it), the frames
where each animated channel is at its minimum and maximum within the shot, rendered at full resolution through the
shot's camera. Parts that slide, swing or hinge against each other show joins, gaps and parts passing through each
other best at maximum stroke or swing, which a mid-shot still or a small contact sheet hides.

A hero moved by something other than its own keys (a keyed parent that is no hero, such as a pivot empty, a
constraint or a driver) is followed in the world: its world position and turn (`<object> world location[i]`,
`world rotation[i]`, with what moves it named) give its extremes too."""

from __future__ import annotations

from pathlib import Path

from bsa.blender.anchors import heroes
from bsa.blender.bake import _fcurves
from bsa.blender.stills import render_still

EPS = 1e-6


def fcurves(obj) -> list:
    """The object's own animation curves (its action slot's only, in Blender 4.4+ slotted actions)."""
    anim = obj.animation_data
    if anim is None or anim.action is None:
        return []
    action = anim.action
    slot = getattr(anim, "action_slot", None)
    if slot is not None:
        try:
            from bpy_extras import anim_utils

            bag = anim_utils.action_get_channelbag_for_slot(action, slot)
            return list(bag.fcurves) if bag is not None else []
        except (ImportError, AttributeError):
            pass
    return _fcurves(action)


def _parts(scene, shot_id: str) -> list:
    """Each hero of the shot and every object parented under it, once, in name order."""
    seen = {}
    for name in heroes(scene, shot_id):
        obj = scene.objects[name]
        for o in [obj, *obj.children_recursive]:
            if o.name in scene.objects:
                seen[o.name] = o
    return [seen[n] for n in sorted(seen)]


def _driven(obj) -> bool:
    """Does the object move by something besides its own action: drivers, NLA strips or constraints?"""
    ad = obj.animation_data
    if ad is not None and (len(ad.drivers) or len(ad.nla_tracks)):
        return True
    return len(obj.constraints) > 0


def _movers(obj) -> list[str]:
    """What moves the object besides its own keys: its animated ancestors (nearest first), its constraints' targets
    and its drivers. Empty when only its own keys (if any) move it."""
    out = []
    p = obj.parent
    while p is not None:
        ad = p.animation_data
        if (ad is not None and (ad.action is not None or len(ad.drivers) or len(ad.nla_tracks))) or p.constraints:
            out.append(p.name)
        p = p.parent
    for con in obj.constraints:
        target = getattr(con, "target", None)
        out.append(f"{con.type.lower()} constraint" + (f" on {target.name}" if target is not None else ""))
    ad = obj.animation_data
    if ad is not None and (len(ad.drivers) or len(ad.nla_tracks)):
        out.append("drivers")
    return out


def _world_channels(scene, objs: list, frames: range) -> dict[str, list[list[float]]]:
    """{object name: [[x, y, z, rx, ry, rz] per frame]}: evaluated world location and rotation (Euler XYZ, kept
    continuous from frame to frame). scene.frame_current is restored afterwards."""
    from bsa.blender.checks import _depsgraph, _view_layer

    original = scene.frame_current
    depsgraph = _depsgraph(scene, _view_layer(scene))
    rows: dict[str, list[list[float]]] = {o.name: [] for o in objs}
    prev: dict[str, object] = {}
    try:
        for frame in frames:
            scene.frame_set(frame)
            depsgraph.update()
            for o in objs:
                m = o.evaluated_get(depsgraph).matrix_world
                rot = m.to_euler("XYZ", prev[o.name]) if o.name in prev else m.to_euler("XYZ")
                prev[o.name] = rot
                rows[o.name].append([*m.translation, *rot])
    finally:
        scene.frame_set(original)
    return rows


def extreme_frames(scene, shot_id: str, f0: int, f1: int) -> dict[int, list[str]]:
    """{frame: ["<object> <data_path>[<index>] min|max", ...]} over frames f0..f1 for every animated channel of the
    shot's heroes and their parts that changes within the shot (the first frame of each extreme). A part moved by
    something other than its own keys (an animated ancestor that is not one of the parts, a constraint, a driver)
    adds its world channels: "<object> world location[i] min|max (moved by Pivot)"."""
    out: dict[int, list[str]] = {}
    frames = range(f0, f1 + 1)

    def add(name: str, values: list[float]) -> None:
        lo, hi = min(values), max(values)
        if hi - lo < EPS:
            return
        out.setdefault(frames[values.index(hi)], []).append(f"{name} max")
        out.setdefault(frames[values.index(lo)], []).append(f"{name} min")

    parts = _parts(scene, shot_id)
    inside = {o.name for o in parts}
    moved = []
    for obj in parts:
        for fc in fcurves(obj):
            add(f"{obj.name} {fc.data_path}[{fc.array_index}]", [fc.evaluate(f) for f in frames])
        movers = [m for m in _movers(obj) if m not in inside]
        if movers and (obj.parent is None or obj.parent.name not in inside or _driven(obj)):
            moved.append((obj, movers))  # a part under a hero that moves it is shown by that hero's channels
    if moved:
        rows = _world_channels(scene, [o for o, _ in moved], frames)
        for obj, movers in moved:
            by = " and ".join(movers)
            for i, what in enumerate(("location[0]", "location[1]", "location[2]", "rotation[0]", "rotation[1]",
                                      "rotation[2]")):  # fmt: skip
                add(f"{obj.name} world {what} (moved by {by})", [r[i] for r in rows[obj.name]])
    return {f: out[f] for f in sorted(out)}


def render_extremes(scene, shot_id: str, f0: int, f1: int, out_dir: Path, engine: str | None = None,
                    percent: int = 100) -> list[dict]:  # fmt: skip
    """A full-resolution JPEG per extreme frame (each frame once) as `<out_dir>/<shot>-f0123.jpg`, through the camera
    the shot uses at that frame. Returns [{"frame", "path", "why": [...]}] in frame order."""
    rows = []
    for frame, why in extreme_frames(scene, shot_id, f0, f1).items():
        path = render_still(scene, frame, Path(out_dir) / f"{shot_id}-f{frame:04d}.jpg", percent=percent, engine=engine)
        rows.append({"frame": frame, "path": str(path), "why": why})
    return rows
